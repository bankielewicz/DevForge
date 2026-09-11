//! Compiled port of `scripts/validate_framework.py`: read-only structural
//! validation of a selected DevForgeAI framework checkout.
//!
//! It inspects declarative sources and reports. It never imports, installs or
//! executes a candidate skill, hook, agent or runtime host, never materializes
//! an authored eval fixture, and never records acceptance. The only subprocess
//! it starts is a bounded `python3` syntax inspection of a `.py` artifact, which
//! receives the file bytes on stdin and no filesystem path (see
//! `docs/integration/framework-structure-validation.md`).
//!
//! Contract (unchanged from the legacy validator): a structural `PASS` object on
//! stdout with exit status 0, or `BLOCKED: <reason>` on stderr with exit status
//! 2 and no stdout. A `PASS` is structure only; behavior stays `NOT_EVALUATED`.
use crate::plugin;
use anyhow::{Result, anyhow, bail, ensure};
use serde::Serialize;
use serde_json::{Map, Value};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::ffi::{OsStr, OsString};
use std::fs;
use std::io::Write;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const CORE: [&str; 4] = [
    "devforge-brainstorm",
    "devforge-project-expert-creator",
    "devforge-develop",
    "devforge-review",
];
const PROVIDERS: [&str; 2] = ["claude", "codex"];
const PRUNED: [&str; 3] = [".git", ".poc", "__pycache__"];
const SELF_SCHEMA: &str = "devforge.skill-validator-self-evals/v1";
const SELF_FIELDS: [&str; 12] = [
    "schema_version",
    "skill_under_test",
    "provider",
    "purpose",
    "authoring_status",
    "last_authored_date",
    "execution_status",
    "execution_boundary",
    "fixture_materialization",
    "fixture_sets",
    "common_grading",
    "cases",
];
const BOUNDARY_TEXT: [&str; 6] = [
    "instruction",
    "target_write_policy",
    "required_runtime",
    "output_root",
    "source_export_policy",
    "missing_prerequisite_result",
];
const MATERIALIZATION_TEXT: [&str; 9] = [
    "format",
    "path_rule",
    "operator_record",
    "workers",
    "comparison",
    "control_bundle",
    "synthetic_limit",
    "artifact_case_facts",
    "protected_file_observation",
];
const GRADING_TEXT: [&str; 7] = [
    "schema",
    "pass_rule",
    "fail_rule",
    "unavailable_rule",
    "preserve_targets",
    "no_auto_repair",
    "expected_result_distinction",
];
const OPERATIONS: [&str; 3] = ["files", "replace_files", "append_files"];
const CASE_FIELDS: [&str; 8] = [
    "id",
    "title",
    "tier",
    "status",
    "fixture_set",
    "validator_request",
    "operator_setup",
    "required_observations",
];
const CASE_TEXT: [&str; 6] = [
    "id",
    "title",
    "tier",
    "status",
    "fixture_set",
    "validator_request",
];
/// The interpreter is only asked to parse a Python artifact; it never imports,
/// executes or even names the candidate file.
const PYTHON: &str = "/usr/bin/python3";
const INSPECTION: &str = "import ast,sys; ast.parse(sys.stdin.buffer.read())";
const INSPECTION_DEADLINE: Duration = Duration::from_secs(10);
const SYMLINK_HOP_LIMIT: usize = 64;

/// The one supported runtime requirement, emitted in the authored key order.
#[derive(Serialize)]
struct Requirement {
    schema_version: &'static str,
    runtime: &'static str,
    protocol: &'static str,
    provider: String,
    completion_mode: &'static str,
    required_events: [&'static str; 4],
}

impl Requirement {
    fn new(provider: &str) -> Self {
        Self {
            schema_version: "devforge.runtime-requirement/v1",
            runtime: "devforge.delivery",
            protocol: "devforge.delivery-runtime/v1",
            provider: provider.to_owned(),
            completion_mode: "managed-session",
            required_events: plugin::REQUIRED_EVENTS,
        }
    }
}

#[derive(Serialize)]
struct Report {
    status: &'static str,
    skills: usize,
    scope: &'static str,
    behavior: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    runtime_requirements: Option<BTreeMap<String, Requirement>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    runtime_host: Option<&'static str>,
}

pub(crate) fn main(framework: &Path) {
    if let Err(error) = run(framework) {
        eprintln!("BLOCKED: {error}");
        std::process::exit(2);
    }
}

fn run(framework: &Path) -> Result<()> {
    let root = realpath(&std::env::current_dir()?.join(framework));
    let report = validate(&root)?;
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}

fn validate(root: &Path) -> Result<Report> {
    let mut skills = 0;
    walk(root, root, &mut skills)?;
    ensure!(
        !root.join("plugins/devforgeai").exists(),
        "retired shared plugin source still exists"
    );
    let mut requirements = BTreeMap::new();
    for provider in PROVIDERS {
        let package = root.join(format!("providers/{provider}/plugins/devforgeai"));
        let manifest = package.join(format!(".{provider}-plugin/plugin.json"));
        let declared: Value = serde_json::from_str(&read_text(&manifest)?)
            .map_err(|error| anyhow!("{}: {error}", manifest.display()))?;
        let name = declared
            .as_object()
            // The legacy script left a non-object manifest as an uncaught
            // TypeError; refuse it with the same exit status as every other
            // malformed input instead.
            .ok_or_else(|| anyhow!("plugin manifest must be an object: {}", manifest.display()))?
            .get("name")
            // Legacy raises KeyError, whose text is the repr of the missing key.
            .ok_or_else(|| anyhow!("'name'"))?;
        ensure!(name == "devforgeai", "{}", manifest.display());
        ensure!(
            CORE.iter().all(|skill| package
                .join("skills")
                .join(skill)
                .join("SKILL.md")
                .is_file()),
            "{provider} core skill missing"
        );
        if plugin::load_requirement(&package, provider)?.is_some() {
            plugin::load_plugin_hooks(&package, provider)?;
            requirements.insert(provider.to_owned(), Requirement::new(provider));
        }
        for cases in eval_declarations(&package) {
            validate_evals(&cases, provider)?;
        }
    }
    let declared = !requirements.is_empty();
    Ok(Report {
        status: "PASS",
        skills,
        scope: "structure only",
        behavior: "NOT_EVALUATED",
        runtime_requirements: declared.then_some(requirements),
        runtime_host: declared.then_some("NOT_VERIFIED"),
    })
}

/// Every source file below `root`, refusing symlinks and pruning the excluded
/// directory names plus the root operational runtime. Entries are visited in
/// component order: a directory's own files first, then its subdirectories.
fn walk(root: &Path, directory: &Path, skills: &mut usize) -> Result<()> {
    let mut entries: Vec<(OsString, PathBuf)> = Vec::new();
    for entry in fs::read_dir(directory).map_err(|error| io_error(&error, directory))? {
        let entry = entry.map_err(|error| io_error(&error, directory))?;
        entries.push((entry.file_name(), entry.path()));
    }
    entries.sort();
    let mut nested = Vec::new();
    for (name, path) in entries {
        if PRUNED.iter().any(|pruned| name == OsStr::new(pruned)) {
            continue;
        }
        let relative = path.strip_prefix(root).unwrap_or(&path).to_path_buf();
        ensure!(
            !fs::symlink_metadata(&path).is_ok_and(|meta| meta.file_type().is_symlink()),
            "symlink not accepted: {}",
            relative.display()
        );
        if path.is_dir() {
            // Only the real root operational directory is outside source scope.
            if relative != Path::new(".devforge-runtime") {
                nested.push(path);
            }
        } else if path.is_file() {
            inspect(&path, &relative, skills)?;
        }
    }
    for directory in nested {
        walk(root, &directory, skills)?;
    }
    Ok(())
}

fn inspect(path: &Path, relative: &Path, skills: &mut usize) -> Result<()> {
    let parts: Vec<&OsStr> = relative.components().map(Component::as_os_str).collect();
    let names = |name: &str| parts.iter().any(|part| *part == OsStr::new(name));
    if names(".github") && names("workflows") && !retained_runtime_workflow(&parts) {
        bail!("GitHub workflows belong in DevForge");
    }
    let extension = path.extension().and_then(OsStr::to_str);
    if extension == Some("json") {
        let text = read_text(path)?;
        python_json(&text, false).map_err(|error| anyhow!("{}: {error}", relative.display()))?;
    }
    if extension == Some("toml") {
        let text = read_text(path)?;
        let data: toml::Table =
            toml::from_str(&text).map_err(|error| anyhow!("{}: {error}", relative.display()))?;
        if names("agents") {
            ensure!(
                ["name", "description", "developer_instructions"]
                    .iter()
                    .all(|key| data.get(*key).is_some_and(toml_truthy)),
                "{}",
                relative.display()
            );
        }
    }
    if extension == Some("py") {
        inspect_python(path, relative)?;
    }
    if path.file_name() == Some(OsStr::new("SKILL.md")) {
        inspect_skill(path, relative, &parts, skills)?;
    }
    Ok(())
}

/// Python truthiness of a decoded TOML value.
fn toml_truthy(value: &toml::Value) -> bool {
    match value {
        toml::Value::String(text) => !text.is_empty(),
        toml::Value::Integer(number) => *number != 0,
        toml::Value::Float(number) => *number != 0.0,
        toml::Value::Boolean(flag) => *flag,
        toml::Value::Datetime(_) => true,
        toml::Value::Array(items) => !items.is_empty(),
        toml::Value::Table(entries) => !entries.is_empty(),
    }
}

fn inspect_skill(path: &Path, relative: &Path, parts: &[&OsStr], skills: &mut usize) -> Result<()> {
    let label = relative.display().to_string();
    let text = read_text(path)?;
    ensure!(text.starts_with("---\n"), "{label}");
    let front = front_matter(&text);
    let name = front.split('\n').find_map(frontmatter_name);
    // A directly retained original entrypoint is documentation inside a dated
    // evidence container, not an installed package directory. Its metadata is
    // still inspected, and every descendant source file with it.
    let archived = retained_entrypoint(parts);
    let parent = path
        .parent()
        .and_then(Path::file_name)
        .and_then(OsStr::to_str);
    ensure!(
        name.is_some_and(|name| archived || Some(name) == parent),
        "{label}"
    );
    ensure!(
        front.split('\n').any(|line| line
            .strip_prefix("description: ")
            .is_some_and(|rest| !rest.is_empty())),
        "{label}"
    );
    if !archived {
        *skills += 1;
    }
    Ok(())
}

/// `text.split("---\n", 2)[1]` for text already known to start with `---\n`.
fn front_matter(text: &str) -> &str {
    let rest = &text["---\n".len()..];
    match rest.find("---\n") {
        Some(end) => &rest[..end],
        None => rest,
    }
}

/// One `^name: ([a-z0-9-]+)$` line under `re.M`, whose boundaries are newlines.
fn frontmatter_name(line: &str) -> Option<&str> {
    let name = line.strip_prefix("name: ")?;
    (!name.is_empty()
        && name
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-'))
    .then_some(name)
}

/// A dated custody container's descendants, without excluding them.
fn container_tail<'a>(parts: &[&'a OsStr]) -> Vec<&'a str> {
    if parts.len() < 4
        || parts[0] != OsStr::new("docs")
        || parts[1] != OsStr::new("skill-authoring")
    {
        return Vec::new();
    }
    let mut tail = Vec::new();
    for part in &parts[2..] {
        match part.to_str() {
            Some(text) => tail.push(text),
            None => return Vec::new(),
        }
    }
    if tail[0] == "history" {
        tail.remove(0);
    }
    if dated_container(tail[0]) {
        tail.split_off(1)
    } else {
        Vec::new()
    }
}

/// `[a-z0-9][a-z0-9-]*-(?:\d{4}-\d{2}-\d{2}|\d{8}T\d{6,12}Z)`. Existing evidence
/// uses calendar dates or UTC timestamps with an optional fractional-second
/// suffix; ordinary authored directories get no exemption.
fn dated_container(name: &str) -> bool {
    let bytes = name.as_bytes();
    let digits = |slice: &[u8]| !slice.is_empty() && slice.iter().all(u8::is_ascii_digit);
    let stem = |end: usize| {
        end > 0
            && bytes[end] == b'-'
            && (bytes[0].is_ascii_lowercase() || bytes[0].is_ascii_digit())
            && bytes[..end]
                .iter()
                .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || *byte == b'-')
    };
    if bytes.len() > 11 {
        let separator = bytes.len() - 11;
        let date = &bytes[separator + 1..];
        if digits(&date[..4])
            && date[4] == b'-'
            && digits(&date[5..7])
            && date[7] == b'-'
            && digits(&date[8..10])
            && stem(separator)
        {
            return true;
        }
    }
    if let Some(head) = bytes.strip_suffix(b"Z") {
        let fraction = head
            .iter()
            .rev()
            .take_while(|byte| byte.is_ascii_digit())
            .count();
        if (6..=12).contains(&fraction) && head.len() > fraction {
            let marker = head.len() - fraction - 1;
            if head[marker] == b'T'
                && marker >= 9
                && digits(&head[marker - 8..marker])
                && stem(marker - 9)
            {
                return true;
            }
        }
    }
    false
}

/// Only entrypoints directly retained in the documented snapshot shapes.
fn retained_entrypoint(parts: &[&OsStr]) -> bool {
    let tail = container_tail(parts);
    if tail.last() != Some(&"SKILL.md") {
        return false;
    }
    if tail.len() == 1 {
        return parts.get(2) == Some(&OsStr::new("history"));
    }
    tail.len() == 2 && snapshot_directory(tail[0])
}

/// `(?:source|installed)(?:-[a-z][a-z0-9-]*)?-(?:before|after)(?:-[0-9]+)?`.
fn snapshot_directory(name: &str) -> bool {
    let trimmed = match name.rsplit_once('-') {
        Some((head, index)) if !index.is_empty() && index.bytes().all(|b| b.is_ascii_digit()) => {
            head
        }
        _ => name,
    };
    let Some(head) = trimmed
        .strip_suffix("-before")
        .or_else(|| trimmed.strip_suffix("-after"))
    else {
        return false;
    };
    ["source", "installed"].iter().any(|stem| {
        head == *stem
            || head
                .strip_prefix(stem)
                .and_then(|rest| rest.strip_prefix('-'))
                .is_some_and(|label| {
                    label.starts_with(|c: char| c.is_ascii_lowercase())
                        && label.bytes().all(|byte| {
                            byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-'
                        })
                })
    })
}

fn retained_runtime_workflow(parts: &[&OsStr]) -> bool {
    let tail = container_tail(parts);
    tail.len() == 5
        && tail[0]
            .strip_prefix("runtime-review-")
            .is_some_and(|index| !index.is_empty() && index.bytes().all(|b| b.is_ascii_digit()))
        && tail[1..4] == ["frozen-source", ".github", "workflows"]
}

/// `(<plugin>/skills).glob("*/evals/evals.json")`, in a deterministic order.
fn eval_declarations(package: &Path) -> Vec<PathBuf> {
    let skills = package.join("skills");
    let Ok(entries) = fs::read_dir(&skills) else {
        return Vec::new();
    };
    let mut names: Vec<OsString> = entries.flatten().map(|entry| entry.file_name()).collect();
    names.sort();
    names
        .into_iter()
        .map(|name| skills.join(name).join("evals/evals.json"))
        .filter(|path| path.is_file())
        .collect()
}

fn validate_evals(path: &Path, provider: &str) -> Result<()> {
    let label = path.display().to_string();
    let data = strict_json(path)?;
    let Some(entries) = data.as_object() else {
        bail!("{label}: eval declaration must be an object");
    };
    let skill = path
        .parent()
        .and_then(Path::parent)
        .and_then(Path::file_name)
        .and_then(OsStr::to_str)
        .unwrap_or_default();
    if entries.contains_key("schema_version") {
        ensure!(
            entries["schema_version"] == SELF_SCHEMA,
            "{label}: unsupported eval schema"
        );
        return validate_self_evals(&data, skill, provider, &label);
    }
    ensure!(
        entries.get("skill_name").and_then(Value::as_str) == Some(skill),
        "wrong eval skill: {label}"
    );
    let Some(cases) = entries.get("evals").and_then(Value::as_array) else {
        bail!("{label}: evals must be a list");
    };
    for case in cases {
        let Some(case) = case.as_object() else {
            bail!("{label}: legacy eval case must be an object");
        };
        let files = match case.get("files") {
            None => &[][..],
            Some(Value::Array(files)) => files.as_slice(),
            Some(_) => bail!("{label}: legacy files must be a list"),
        };
        for relative in files {
            eval_text(Some(relative), &format!("{label}: fixture path"))?;
            let relative = relative.as_str().unwrap_or_default();
            let candidate = Path::new(relative);
            ensure!(
                !candidate.is_absolute()
                    && !candidate
                        .components()
                        .any(|part| part == Component::ParentDir),
                "fixture path escapes eval root: {relative}"
            );
            let mut fixture = path.parent().unwrap_or(path).to_path_buf();
            for part in candidate.components() {
                if let Component::Normal(name) = part {
                    fixture.push(name);
                }
            }
            ensure!(fixture.is_file(), "missing fixture: {}", fixture.display());
        }
    }
    Ok(())
}

/// Check declarations and path relationships only; inline bytes stay inert.
fn validate_self_evals(data: &Value, skill: &str, provider: &str, label: &str) -> Result<()> {
    let entries = object_fields(
        data,
        &SELF_FIELDS,
        &["workspace_allocation_refinement"],
        label,
    )?;
    ensure!(
        entries["skill_under_test"] == skill,
        "wrong eval skill: {label}"
    );
    ensure!(
        entries["provider"] == provider,
        "wrong eval provider: {label}"
    );
    for key in [
        "purpose",
        "authoring_status",
        "last_authored_date",
        "execution_status",
    ] {
        eval_text(entries.get(key), &format!("{label}: {key}"))?;
    }
    if let Some(refinement) = entries.get("workspace_allocation_refinement") {
        let detail = format!("{label}: workspace_allocation_refinement");
        let fields = object_fields(
            refinement,
            &[
                "change_id",
                "requirement_ids",
                "status",
                "historical_expectations",
            ],
            &[],
            &detail,
        )?;
        for key in ["change_id", "status", "historical_expectations"] {
            eval_text(fields.get(key), &format!("{detail}: {key}"))?;
        }
        eval_text_list(
            fields.get("requirement_ids"),
            &format!("{detail}: requirement_ids"),
        )?;
        ensure!(
            unique(fields["requirement_ids"].as_array().unwrap_or(&Vec::new())),
            "{detail}: duplicate requirement ID"
        );
    }
    for (key, text_fields, extra) in [
        ("execution_boundary", &BOUNDARY_TEXT[..], &["run_now"][..]),
        (
            "fixture_materialization",
            &MATERIALIZATION_TEXT[..],
            &[][..],
        ),
        ("common_grading", &GRADING_TEXT[..], &[][..]),
    ] {
        let detail = format!("{label}: {key}");
        let mut allowed = text_fields.to_vec();
        allowed.extend_from_slice(extra);
        let fields = object_fields(&entries[key], &allowed, &[], &detail)?;
        for field in text_fields {
            eval_text(fields.get(*field), &format!("{detail}.{field}"))?;
        }
    }
    ensure!(
        entries["execution_boundary"]["run_now"].is_boolean(),
        "{label}: run_now must be a boolean declaration"
    );

    let Some(fixtures) = entries["fixture_sets"]
        .as_object()
        .filter(|fixtures| !fixtures.is_empty())
    else {
        bail!("{label}: fixture_sets must be a nonempty object");
    };
    for (name, fixture) in fixtures {
        eval_text_value(name, &format!("{label}: fixture identity"))?;
        let detail = format!("{label}: fixture {name}");
        let mut optional = vec!["base", "absent_files", "authority_note"];
        optional.extend_from_slice(&OPERATIONS);
        let fields = object_fields(fixture, &[], &optional, &detail)?;
        if let Some(base) = fields.get("base") {
            eval_text(Some(base), &format!("{detail}: base"))?;
            ensure!(
                fixtures.contains_key(base.as_str().unwrap_or_default()),
                "{detail}: unknown fixture base"
            );
        }
        if let Some(note) = fields.get("authority_note") {
            eval_text(Some(note), &format!("{detail}: authority_note"))?;
        }
        for operation in OPERATIONS {
            let Some(values) = fields.get(operation) else {
                continue;
            };
            let Some(values) = values.as_object() else {
                bail!("{detail}: {operation} must be an object");
            };
            for (relative, content) in values {
                eval_text_value(relative, &detail)?;
                inline_fixture_path(relative, &detail)?;
                // Decoded JSON text is already UTF-8, so the legacy encode check
                // cannot fail here; a lone surrogate escape is refused earlier.
                ensure!(
                    content.is_string(),
                    "{detail}: inline content must be a string"
                );
            }
        }
        match fields.get("absent_files") {
            None => {}
            Some(Value::Array(absent)) => {
                for relative in absent {
                    eval_text(Some(relative), &detail)?;
                    inline_fixture_path(relative.as_str().unwrap_or_default(), &detail)?;
                }
            }
            Some(_) => bail!("{detail}: absent_files must be a list"),
        }
    }

    // Resolve only path sets, never payloads or filesystem writes. Iteration
    // avoids recursing through candidate-controlled inheritance chains.
    let mut resolved: BTreeMap<&str, BTreeSet<&str>> = BTreeMap::new();
    let mut pending: BTreeSet<&str> = fixtures.keys().map(String::as_str).collect();
    while !pending.is_empty() {
        let ready: Vec<&str> = pending
            .iter()
            .copied()
            .filter(
                |name| match fixtures[*name].get("base").and_then(Value::as_str) {
                    Some(base) => resolved.contains_key(base),
                    None => true,
                },
            )
            .collect();
        ensure!(!ready.is_empty(), "{label}: fixture base cycle");
        for name in ready {
            let fixture = &fixtures[name];
            let mut paths: BTreeSet<&str> = fixture
                .get("base")
                .and_then(Value::as_str)
                .and_then(|base| resolved.get(base))
                .cloned()
                .unwrap_or_default();
            if let Some(files) = fixture.get("files").and_then(Value::as_object) {
                paths.extend(files.keys().map(String::as_str));
            }
            for operation in ["replace_files", "append_files"] {
                if let Some(values) = fixture.get(operation).and_then(Value::as_object) {
                    ensure!(
                        values.keys().all(|key| paths.contains(key.as_str())),
                        "{label}: fixture {name}: unresolved {operation} target"
                    );
                }
            }
            if let Some(absent) = fixture.get("absent_files").and_then(Value::as_array) {
                ensure!(
                    !absent
                        .iter()
                        .any(|item| item.as_str().is_some_and(|text| paths.contains(text))),
                    "{label}: fixture {name}: declared absent file is present"
                );
            }
            resolved.insert(name, paths);
            pending.remove(name);
        }
    }

    let Some(cases) = entries["cases"]
        .as_array()
        .filter(|cases| !cases.is_empty())
    else {
        bail!("{label}: cases must be a nonempty list");
    };
    let mut identities: BTreeSet<&str> = BTreeSet::new();
    for case in cases {
        let fields = object_fields(
            case,
            &CASE_FIELDS,
            &["requires_real_control_bundle", "requirement_ids"],
            &format!("{label}: case"),
        )?;
        for key in CASE_TEXT {
            eval_text(fields.get(key), &format!("{label}: case {key}"))?;
        }
        let id = fields["id"].as_str().unwrap_or_default();
        ensure!(identities.insert(id), "{label}: duplicate case ID {id}");
        ensure!(
            matches!(fields["tier"].as_str(), Some("A" | "B" | "C")),
            "{label}: unsupported case tier"
        );
        ensure!(
            fixtures.contains_key(fields["fixture_set"].as_str().unwrap_or_default()),
            "{label}: unknown case fixture_set"
        );
        for key in ["operator_setup", "required_observations"] {
            eval_text_list(fields.get(key), &format!("{label}: {key}"))?;
        }
        if let Some(flag) = fields.get("requires_real_control_bundle") {
            ensure!(
                flag.is_boolean(),
                "{label}: requires_real_control_bundle must be boolean"
            );
        }
        if let Some(ids) = fields.get("requirement_ids") {
            eval_text_list(Some(ids), &format!("{label}: case requirement_ids"))?;
            let list = ids.as_array().unwrap_or(&Vec::new()).clone();
            ensure!(unique(&list), "{label}: duplicate case requirement ID");
            if let Some(refinement) = entries.get("workspace_allocation_refinement") {
                let declared: BTreeSet<&str> = refinement["requirement_ids"]
                    .as_array()
                    .map(|items| items.iter().filter_map(Value::as_str).collect())
                    .unwrap_or_default();
                ensure!(
                    list.iter()
                        .all(|item| item.as_str().is_some_and(|text| declared.contains(text))),
                    "{label}: unknown case requirement ID"
                );
            }
        }
    }
    Ok(())
}

fn object_fields<'a>(
    value: &'a Value,
    required: &[&str],
    optional: &[&str],
    label: &str,
) -> Result<&'a Map<String, Value>> {
    let Some(entries) = value.as_object() else {
        bail!("{label}: expected an object");
    };
    let complete = required.iter().all(|key| entries.contains_key(*key))
        && entries
            .keys()
            .all(|key| required.contains(&key.as_str()) || optional.contains(&key.as_str()));
    ensure!(complete, "{label}: unsupported fields");
    Ok(entries)
}

fn eval_text(value: Option<&Value>, label: &str) -> Result<()> {
    let present = value
        .and_then(Value::as_str)
        .is_some_and(|text| !python_strip(text).is_empty());
    ensure!(present, "{label}: expected nonempty text");
    Ok(())
}

fn eval_text_value(text: &str, label: &str) -> Result<()> {
    ensure!(
        !python_strip(text).is_empty(),
        "{label}: expected nonempty text"
    );
    Ok(())
}

fn eval_text_list(value: Option<&Value>, label: &str) -> Result<()> {
    let Some(items) = value
        .and_then(Value::as_array)
        .filter(|items| !items.is_empty())
    else {
        bail!("{label}: expected a nonempty list");
    };
    for item in items {
        eval_text(Some(item), label)?;
    }
    Ok(())
}

fn unique(items: &[Value]) -> bool {
    let mut seen = BTreeSet::new();
    items
        .iter()
        .all(|item| item.as_str().is_some_and(|text| seen.insert(text)))
}

fn inline_fixture_path(value: &str, label: &str) -> Result<()> {
    let drive = {
        let mut characters = value.chars();
        matches!(
            (characters.next(), characters.next()),
            (Some(first), Some(':')) if first.is_ascii_alphabetic()
        )
    };
    let safe = !value.starts_with('/')
        && !drive
        && !value.contains('\\')
        && !value.contains('\0')
        && value
            .split('/')
            .all(|part| !matches!(part, "" | "." | ".."));
    ensure!(
        safe,
        "{label}: unsafe inline fixture path {}",
        python_repr(value)
    );
    Ok(())
}

/// Python `str.strip()`: Unicode whitespace plus the ASCII separators 0x1c-0x1f.
fn python_strip(text: &str) -> &str {
    text.trim_matches(|character: char| {
        character.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&character)
    })
}

/// Python `repr()` of a string, as the legacy diagnostic embedded it.
fn python_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut rendered = String::from(quote);
    for character in text.chars() {
        match character {
            '\\' => rendered.push_str("\\\\"),
            '\n' => rendered.push_str("\\n"),
            '\r' => rendered.push_str("\\r"),
            '\t' => rendered.push_str("\\t"),
            _ if character == quote => {
                rendered.push('\\');
                rendered.push(character);
            }
            _ if (character as u32) < 0x20 || character as u32 == 0x7f => {
                rendered.push_str(&format!("\\x{:02x}", character as u32));
            }
            _ => rendered.push(character),
        }
    }
    rendered.push(quote);
    rendered
}

/// `runtime_requirements.read_json`, with the two refusals it names verbatim.
fn strict_json(path: &Path) -> Result<Value> {
    let raw = fs::read(path).map_err(|error| io_error(&error, path))?;
    if let Ok(text) = std::str::from_utf8(&raw) {
        python_json(text, true)?;
    }
    plugin::strict_json(&raw).map_err(|error| anyhow!("{}: {error}", path.display()))
}

/// A bounded `python3` parse of one `.py` artifact. The interpreter receives the
/// file bytes on stdin, never a path, and nothing is imported or executed.
fn inspect_python(path: &Path, relative: &Path) -> Result<()> {
    let label = relative.display().to_string();
    let source = fs::read(path).map_err(|error| io_error(&error, path))?;
    let mut child = Command::new(PYTHON)
        .args(["-I", "-B", "-S", "-c", INSPECTION])
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("LANG", "C.UTF-8")
        .spawn()
        .map_err(|error| anyhow!("{label}: Python syntax inspection unavailable: {error}"))?;
    // The deadline covers the write as well as the parse: a hypothetical
    // interpreter that never drained stdin would otherwise block here, past a
    // pipe buffer's worth of source, without the deadline ever starting.
    let started = Instant::now();
    if let Some(mut stdin) = child.stdin.take() {
        // The inspector reads every byte before parsing; a closed pipe means it
        // has already failed, which the exit status reports.
        let _ = stdin.write_all(&source);
    }
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => {}
            Err(error) => {
                bail!("{label}: Python syntax inspection unavailable: {error}");
            }
        }
        if started.elapsed() > INSPECTION_DEADLINE {
            let _ = child.kill();
            let _ = child.wait();
            bail!(
                "{label}: Python syntax inspection exceeded {} seconds",
                INSPECTION_DEADLINE.as_secs()
            );
        }
        std::thread::sleep(Duration::from_millis(5));
    };
    ensure!(status.success(), "{label}: invalid Python syntax");
    Ok(())
}

/// Text with universal newline translation, matching Python's `read_text()`.
fn read_text(path: &Path) -> Result<String> {
    let bytes = fs::read(path).map_err(|error| io_error(&error, path))?;
    let text = String::from_utf8(bytes)
        .map_err(|error| anyhow!("'utf-8' codec can't decode {}: {error}", path.display()))?;
    Ok(text.replace("\r\n", "\n").replace('\r', "\n"))
}

/// Legacy-style OS error text, e.g. `[Errno 2] No such file or directory: '<path>'`.
fn io_error(error: &std::io::Error, path: &Path) -> anyhow::Error {
    let text = error.to_string();
    match error.raw_os_error() {
        Some(code) => {
            let description = text.trim_end_matches(&format!(" (os error {code})"));
            anyhow!("[Errno {code}] {description}: '{}'", path.display())
        }
        None => anyhow!("{text}: '{}'", path.display()),
    }
}

/// Non-strict realpath: symlinks are expanded left to right, `..` is applied
/// after expansion, and a nonexistent tail is appended unchanged.
fn realpath(path: &Path) -> PathBuf {
    let mut resolved = PathBuf::from("/");
    let mut pending: VecDeque<OsString> = normal_components(path);
    let mut hops = 0;
    while let Some(name) = pending.pop_front() {
        if name == ".." {
            resolved.pop();
            continue;
        }
        let candidate = resolved.join(&name);
        match fs::read_link(&candidate) {
            Ok(target) => {
                hops += 1;
                if hops > SYMLINK_HOP_LIMIT {
                    resolved = candidate;
                    resolved.extend(pending);
                    return resolved;
                }
                if target.is_absolute() {
                    resolved = PathBuf::from("/");
                }
                let mut expanded = normal_components(&target);
                expanded.extend(pending);
                pending = expanded;
            }
            Err(_) => resolved = candidate,
        }
    }
    resolved
}

fn normal_components(path: &Path) -> VecDeque<OsString> {
    path.components()
        .filter_map(|component| match component {
            Component::Normal(name) => Some(name.to_os_string()),
            Component::ParentDir => Some(OsString::from("..")),
            Component::RootDir | Component::CurDir | Component::Prefix(_) => None,
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Python's JSON dialect
// ---------------------------------------------------------------------------

/// Validate one document against Python's `json` grammar, which accepts
/// duplicate object keys and the `NaN`/`Infinity` constants. `strict` adds the
/// duplicate-key and non-finite refusals of `runtime_requirements.strict_json`,
/// with the same diagnostic text and in the same order.
fn python_json(text: &str, strict: bool) -> Result<()> {
    let mut scanner = Scanner {
        text,
        bytes: text.as_bytes(),
        at: 0,
        strict,
    };
    scanner.space();
    scanner.value()?;
    scanner.space();
    if scanner.at != scanner.bytes.len() {
        return Err(scanner.refuse("Extra data"));
    }
    Ok(())
}

struct Scanner<'a> {
    text: &'a str,
    bytes: &'a [u8],
    at: usize,
    strict: bool,
}

impl Scanner<'_> {
    fn refuse(&self, message: &str) -> anyhow::Error {
        self.refuse_at(message, self.at)
    }

    fn refuse_at(&self, message: &str, at: usize) -> anyhow::Error {
        let head = &self.bytes[..at.min(self.bytes.len())];
        let line = 1 + head.iter().filter(|byte| **byte == b'\n').count();
        let column = match head.iter().rposition(|byte| *byte == b'\n') {
            Some(index) => at - index,
            None => at + 1,
        };
        anyhow!("{message}: line {line} column {column} (char {at})")
    }

    fn space(&mut self) {
        while matches!(self.bytes.get(self.at), Some(b' ' | b'\t' | b'\n' | b'\r')) {
            self.at += 1;
        }
    }

    fn literal(&mut self, word: &str) -> Result<()> {
        if self.bytes[self.at..].starts_with(word.as_bytes()) {
            self.at += word.len();
            return Ok(());
        }
        Err(self.refuse("Expecting value"))
    }

    fn nonfinite(&self, token: &str) -> Result<()> {
        ensure!(!self.strict, "non-finite JSON value: {token}");
        Ok(())
    }

    fn value(&mut self) -> Result<()> {
        match self.bytes.get(self.at) {
            Some(b'{') => self.object(),
            Some(b'[') => self.array(),
            Some(b'"') => self.string().map(|_| ()),
            Some(b't') => self.literal("true"),
            Some(b'f') => self.literal("false"),
            Some(b'n') => self.literal("null"),
            Some(b'N') => {
                self.literal("NaN")?;
                self.nonfinite("NaN")
            }
            Some(b'I') => {
                self.literal("Infinity")?;
                self.nonfinite("Infinity")
            }
            Some(b'-') if self.bytes.get(self.at + 1) == Some(&b'I') => {
                self.literal("-Infinity")?;
                self.nonfinite("-Infinity")
            }
            Some(b'-' | b'0'..=b'9') => self.number(),
            _ => Err(self.refuse("Expecting value")),
        }
    }

    fn number(&mut self) -> Result<()> {
        let start = self.at;
        if self.bytes.get(self.at) == Some(&b'-') {
            self.at += 1;
        }
        match self.bytes.get(self.at) {
            Some(b'0') => self.at += 1,
            Some(b'1'..=b'9') => {
                while matches!(self.bytes.get(self.at), Some(b'0'..=b'9')) {
                    self.at += 1;
                }
            }
            _ => return Err(self.refuse_at("Expecting value", start)),
        }
        let mut real = false;
        if self.bytes.get(self.at) == Some(&b'.') {
            self.at += 1;
            let digits = self.at;
            while matches!(self.bytes.get(self.at), Some(b'0'..=b'9')) {
                self.at += 1;
            }
            if self.at == digits {
                return Err(self.refuse_at("Expecting value", start));
            }
            real = true;
        }
        if matches!(self.bytes.get(self.at), Some(b'e' | b'E')) {
            self.at += 1;
            if matches!(self.bytes.get(self.at), Some(b'+' | b'-')) {
                self.at += 1;
            }
            let digits = self.at;
            while matches!(self.bytes.get(self.at), Some(b'0'..=b'9')) {
                self.at += 1;
            }
            if self.at == digits {
                return Err(self.refuse_at("Expecting value", start));
            }
            real = true;
        }
        // Only float tokens reach Python's `parse_float`; integers stay exact.
        if real && self.strict {
            let token = &self.text[start..self.at];
            ensure!(
                token.parse::<f64>().is_ok_and(f64::is_finite),
                "non-finite JSON value: {token}"
            );
        }
        Ok(())
    }

    fn string(&mut self) -> Result<String> {
        let start = self.at;
        self.at += 1;
        let mut decoded = String::new();
        loop {
            let Some(&byte) = self.bytes.get(self.at) else {
                return Err(self.refuse_at("Unterminated string starting at", start));
            };
            match byte {
                b'"' => {
                    self.at += 1;
                    return Ok(decoded);
                }
                b'\\' => {
                    let escape = self.at;
                    self.at += 1;
                    let Some(&kind) = self.bytes.get(self.at) else {
                        return Err(self.refuse_at("Unterminated string starting at", start));
                    };
                    self.at += 1;
                    match kind {
                        b'"' => decoded.push('"'),
                        b'\\' => decoded.push('\\'),
                        b'/' => decoded.push('/'),
                        b'b' => decoded.push('\u{8}'),
                        b'f' => decoded.push('\u{c}'),
                        b'n' => decoded.push('\n'),
                        b'r' => decoded.push('\r'),
                        b't' => decoded.push('\t'),
                        b'u' => decoded.push(self.escaped(escape)?),
                        _ => return Err(self.refuse_at("Invalid \\escape", escape)),
                    }
                }
                0x00..=0x1f => return Err(self.refuse("Invalid control character at")),
                _ => {
                    let character = self.text[self.at..].chars().next().unwrap_or('\u{fffd}');
                    decoded.push(character);
                    self.at += character.len_utf8();
                }
            }
        }
    }

    /// One `\uXXXX` unit, pairing surrogates as Python's decoder does. A lone
    /// surrogate cannot be represented, so it decodes as the replacement
    /// character; it exists only for duplicate-key comparison here, and
    /// `plugin::strict_json` refuses the document regardless.
    fn escaped(&mut self, escape: usize) -> Result<char> {
        let unit = self.hex(escape)?;
        if (0xd800..0xdc00).contains(&unit) && self.bytes.get(self.at..self.at + 2) == Some(b"\\u")
        {
            let saved = self.at;
            self.at += 2;
            let low = self.hex(saved)?;
            if (0xdc00..0xe000).contains(&low) {
                let scalar = 0x10000 + ((unit - 0xd800) << 10) + (low - 0xdc00);
                return Ok(char::from_u32(scalar).unwrap_or('\u{fffd}'));
            }
            self.at = saved;
        }
        Ok(char::from_u32(unit).unwrap_or('\u{fffd}'))
    }

    fn hex(&mut self, escape: usize) -> Result<u32> {
        let value = self
            .bytes
            .get(self.at..self.at + 4)
            .and_then(|slice| std::str::from_utf8(slice).ok())
            .and_then(|digits| u32::from_str_radix(digits, 16).ok());
        let Some(value) = value else {
            return Err(self.refuse_at("Invalid \\uXXXX escape", escape));
        };
        self.at += 4;
        Ok(value)
    }

    fn object(&mut self) -> Result<()> {
        let mut keys: Vec<String> = Vec::new();
        self.at += 1;
        self.space();
        if self.bytes.get(self.at) == Some(&b'}') {
            self.at += 1;
            return Ok(());
        }
        loop {
            self.space();
            if self.bytes.get(self.at) != Some(&b'"') {
                return Err(self.refuse("Expecting property name enclosed in double quotes"));
            }
            keys.push(self.string()?);
            self.space();
            if self.bytes.get(self.at) != Some(&b':') {
                return Err(self.refuse("Expecting ':' delimiter"));
            }
            self.at += 1;
            self.space();
            self.value()?;
            self.space();
            match self.bytes.get(self.at) {
                Some(b',') => self.at += 1,
                Some(b'}') => {
                    self.at += 1;
                    break;
                }
                _ => return Err(self.refuse("Expecting ',' delimiter")),
            }
        }
        // The legacy `object_pairs_hook` runs once the object is complete, so a
        // non-finite value nested inside it is reported first.
        if self.strict {
            let mut seen = BTreeSet::new();
            for key in &keys {
                ensure!(seen.insert(key.as_str()), "duplicate JSON key: {key}");
            }
        }
        Ok(())
    }

    fn array(&mut self) -> Result<()> {
        self.at += 1;
        self.space();
        if self.bytes.get(self.at) == Some(&b']') {
            self.at += 1;
            return Ok(());
        }
        loop {
            self.space();
            self.value()?;
            self.space();
            match self.bytes.get(self.at) {
                Some(b',') => self.at += 1,
                Some(b']') => {
                    self.at += 1;
                    return Ok(());
                }
                _ => return Err(self.refuse("Expecting ',' delimiter")),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{
        dated_container, front_matter, frontmatter_name, python_json, python_repr, python_strip,
        retained_entrypoint, retained_runtime_workflow, snapshot_directory,
    };
    use std::ffi::OsStr;
    use std::path::Path;

    fn parts(relative: &str) -> Vec<&OsStr> {
        Path::new(relative)
            .components()
            .map(|component| component.as_os_str())
            .collect()
    }

    #[test]
    fn dated_containers_follow_the_legacy_pattern() {
        for name in [
            "previous-builder-revision-2026-09-07",
            "revision-2026-09-07",
            "previous-revision-20260907T010203Z",
            "integration-20260907T145039Z",
            "a-20260907T010203123456Z",
        ] {
            assert!(dated_container(name), "{name}");
        }
        for name in [
            "",
            "undated",
            "undated-container",
            "retained",
            "history-lookalike",
            "claude-scaffolding-20260910",
            "2026-09-07",
            "-2026-09-07",
            "A-2026-09-07",
            "a-2026-9-07",
            "a-20260907T01020Z",
            "a-20260907T0102031234567Z",
            "a-20260907010203Z",
        ] {
            assert!(!dated_container(name), "{name}");
        }
    }

    #[test]
    fn snapshot_directories_follow_the_legacy_pattern() {
        for name in [
            "source-before",
            "source-after",
            "installed-before",
            "installed-after",
            "installed-builder-before",
            "installed-builder-before-02",
        ] {
            assert!(snapshot_directory(name), "{name}");
        }
        for name in [
            "source-before-lookalike",
            "source",
            "sourcebefore",
            "source--before",
            "Installed-before",
            "source-Builder-before",
            "source-before-02x",
        ] {
            assert!(!snapshot_directory(name), "{name}");
        }
    }

    #[test]
    fn retained_shapes_are_bounded_to_the_documented_containers() {
        assert!(retained_entrypoint(&parts(
            "docs/skill-authoring/history/previous-builder-revision-2026-09-07/SKILL.md"
        )));
        assert!(retained_entrypoint(&parts(
            "docs/skill-authoring/integration-20260907T145039Z/installed-builder-before-02/SKILL.md"
        )));
        for relative in [
            "docs/skill-authoring/integration-20260907T145039Z/SKILL.md",
            "docs/skill-authoring/history/undated/SKILL.md",
            "docs/skill-authoring/history/revision-2026-09-07/source-before/deeper/SKILL.md",
            "authored/docs/skill-authoring/history/revision-2026-09-07/source-before/SKILL.md",
        ] {
            assert!(!retained_entrypoint(&parts(relative)), "{relative}");
        }
        assert!(retained_runtime_workflow(&parts(
            "docs/skill-authoring/integration-20260907T145039Z/runtime-review-01/frozen-source/.github/workflows/ci.yml"
        )));
        for relative in [
            "docs/skill-authoring/undated/runtime-review-01/frozen-source/.github/workflows/ci.yml",
            "docs/skill-authoring/integration-20260907T145039Z/runtime-review-lookalike/frozen-source/.github/workflows/ci.yml",
            "docs/skill-authoring/integration-20260907T145039Z/runtime-review-01/frozen-source/.github/workflows/nested/ci.yml",
        ] {
            assert!(!retained_runtime_workflow(&parts(relative)), "{relative}");
        }
    }

    #[test]
    fn frontmatter_helpers_follow_python_semantics() {
        assert_eq!(front_matter("---\nname: a\n---\nbody\n"), "name: a\n");
        assert_eq!(front_matter("---\nname: a\n"), "name: a\n");
        assert_eq!(
            frontmatter_name("name: devforge-review"),
            Some("devforge-review")
        );
        for line in ["name: Bad", "name: a b", "name: ", "name:x", " name: a"] {
            assert_eq!(frontmatter_name(line), None, "{line}");
        }
        assert_eq!(python_strip("\u{1f} x \u{a0}"), "x");
        assert_eq!(python_repr("folder\\escape"), "'folder\\\\escape'");
        assert_eq!(python_repr("nul\0name"), "'nul\\x00name'");
    }

    #[test]
    fn the_python_json_dialect_is_lenient_unless_strict_is_requested() {
        for document in [
            "{\"a\": 1, \"a\": 2}",
            "NaN",
            "[1e999, Infinity, -Infinity]",
            "{\"a\": \"\\ud800\"}",
            "999999999999999999999999",
            " [1, {\"b\": [true, false, null]}] ",
        ] {
            python_json(document, false).unwrap_or_else(|error| panic!("{document}: {error}"));
        }
        for document in ["", "{", "[1,]", "{'a': 1}", "1 2", "\"a\nb\"", "01"] {
            assert!(python_json(document, true).is_err(), "{document}");
        }
        assert_eq!(
            python_json("{\"a\": 1, \"a\": 2}", true)
                .unwrap_err()
                .to_string(),
            "duplicate JSON key: a"
        );
        for (document, token) in [
            ("{\"a\": NaN}", "NaN"),
            ("{\"a\": Infinity}", "Infinity"),
            ("{\"a\": -Infinity}", "-Infinity"),
            ("{\"a\": 1e999}", "1e999"),
        ] {
            assert_eq!(
                python_json(document, true).unwrap_err().to_string(),
                format!("non-finite JSON value: {token}"),
                "{document}"
            );
        }
        // A nested value is decoded before the enclosing object completes.
        assert_eq!(
            python_json("{\"a\": 1, \"b\": {\"c\": NaN}, \"a\": 2}", true)
                .unwrap_err()
                .to_string(),
            "non-finite JSON value: NaN"
        );
        // Large integers stay exact in Python and never reach `parse_float`.
        python_json("999999999999999999999999", true).unwrap();
    }
}
