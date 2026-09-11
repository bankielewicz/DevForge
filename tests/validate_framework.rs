//! Black-box regression tests for `devforge validate framework`.
//!
//! Every fixture is synthetic and Rust-owned; the only real-tree inputs are the
//! companion DevForgeAI checkout used as a compatibility oracle and its two
//! authored plugin hook directories. These cases prove the structural checks
//! ported from `scripts/validate_framework.py`. A structural `PASS` is never
//! native skill behavior, package readiness, qualification or acceptance, and
//! nothing here executes a candidate skill, hook or runtime host.
use serde_json::{Value, json};
use std::fs;
use std::os::unix::fs::symlink;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};

const BIN: &str = env!("CARGO_BIN_EXE_devforge");
const LEGACY: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/scripts/validate_framework.py");
const PYTHON: &str = "/usr/bin/python3";
const CORE: [&str; 4] = [
    "devforge-brainstorm",
    "devforge-project-expert-creator",
    "devforge-develop",
    "devforge-review",
];
const EVENTS: [&str; 4] = ["SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"];
const SELF_SCHEMA: &str = "devforge.skill-validator-self-evals/v1";
static COUNTER: AtomicU64 = AtomicU64::new(0);

fn plugin_dir(provider: &str) -> String {
    format!("providers/{provider}/plugins/devforgeai")
}

fn write_file(path: &Path, bytes: &[u8]) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, bytes).unwrap();
}

/// One synthetic framework root with both provider plugins and the four core skills.
struct Fixture {
    dir: PathBuf,
    root: PathBuf,
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.dir);
    }
}

impl Fixture {
    fn empty() -> Self {
        let dir = std::env::temp_dir().join(format!(
            "devforge-validate-framework-{}-{}",
            std::process::id(),
            COUNTER.fetch_add(1, Ordering::SeqCst)
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        // Messages carry resolved paths, so the fixture root must be canonical.
        let dir = fs::canonicalize(&dir).unwrap();
        let root = dir.join("framework");
        fs::create_dir_all(&root).unwrap();
        Self { dir, root }
    }

    fn new() -> Self {
        let fixture = Self::empty();
        for provider in ["codex", "claude"] {
            let plugin = plugin_dir(provider);
            for name in CORE {
                fixture.write(
                    &format!("{plugin}/skills/{name}/SKILL.md"),
                    &format!("---\nname: {name}\ndescription: Synthetic test skill.\n---\n"),
                );
            }
            fixture.write(
                &format!("{plugin}/.{provider}-plugin/plugin.json"),
                "{\"name\": \"devforgeai\"}",
            );
        }
        fixture
    }

    fn path(&self, relative: &str) -> PathBuf {
        self.root.join(relative)
    }

    fn write(&self, relative: &str, text: &str) {
        write_file(&self.path(relative), text.as_bytes());
    }

    fn remove(&self, relative: &str) {
        fs::remove_file(self.path(relative)).unwrap();
    }

    fn remove_dir(&self, relative: &str) {
        fs::remove_dir_all(self.path(relative)).unwrap();
    }

    /// A path outside the framework root, used as a symlink target.
    fn outside(&self, name: &str) -> PathBuf {
        self.dir.join(name)
    }

    fn run(&self) -> Output {
        run_rust(&self.root)
    }

    fn legacy(&self) -> Output {
        run_legacy(&self.root)
    }
}

fn run_rust(root: &Path) -> Output {
    Command::new(BIN)
        .args(["validate", "framework", "--framework"])
        .arg(root)
        // A stale hook executable hint must never reach a host: nothing is launched.
        .env(
            "DEVFORGE_DELIVERY_EXECUTABLE",
            "/missing/synthetic-devforge",
        )
        .output()
        .unwrap()
}

fn run_legacy(root: &Path) -> Output {
    assert!(
        Path::new(PYTHON).is_file() && Path::new(LEGACY).is_file(),
        "compatibility baseline unavailable: {PYTHON} and {LEGACY} are required"
    );
    Command::new(PYTHON)
        .arg(LEGACY)
        .arg("--framework")
        .arg(root)
        .env(
            "DEVFORGE_DELIVERY_EXECUTABLE",
            "/missing/synthetic-devforge",
        )
        .output()
        .unwrap()
}

fn text(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
}

fn pass(skills: u64) -> Value {
    json!({"status": "PASS", "skills": skills, "scope": "structure only", "behavior": "NOT_EVALUATED"})
}

fn assert_pass(output: &Output, expected: &Value) {
    assert_eq!(
        output.status.code(),
        Some(0),
        "stderr={:?}",
        text(&output.stderr)
    );
    assert!(
        output.stderr.is_empty(),
        "stderr={:?}",
        text(&output.stderr)
    );
    let report: Value = serde_json::from_slice(&output.stdout).unwrap_or_else(|error| {
        panic!("stdout was not JSON ({error}): {:?}", text(&output.stdout))
    });
    assert_eq!(&report, expected);
}

fn assert_blocked(output: &Output, reason: &str) {
    assert_eq!(
        output.status.code(),
        Some(2),
        "stdout={:?} stderr={:?}",
        text(&output.stdout),
        text(&output.stderr)
    );
    assert!(
        output.stdout.is_empty(),
        "stdout={:?}",
        text(&output.stdout)
    );
    assert_eq!(text(&output.stderr), format!("BLOCKED: {reason}\n"));
}

fn assert_blocked_containing(output: &Output, needle: &str) {
    assert_eq!(
        output.status.code(),
        Some(2),
        "stdout={:?} stderr={:?}",
        text(&output.stdout),
        text(&output.stderr)
    );
    assert!(
        output.stdout.is_empty(),
        "stdout={:?}",
        text(&output.stdout)
    );
    let reason = text(&output.stderr);
    assert!(
        reason.starts_with("BLOCKED: ") && reason.contains(needle),
        "expected a BLOCKED reason containing {needle:?}, found {reason:?}"
    );
}

fn requirement(provider: &str) -> Value {
    json!({
        "schema_version": "devforge.runtime-requirement/v1",
        "runtime": "devforge.delivery",
        "protocol": "devforge.delivery-runtime/v1",
        "provider": provider,
        "completion_mode": "managed-session",
        "required_events": EVENTS,
    })
}

/// The sidecar as the authored packages write it: declaration order, not the
/// alphabetical order a `serde_json::Value` would serialize.
fn requirement_text(provider: &str) -> String {
    format!(
        "{{\n  \"schema_version\": \"devforge.runtime-requirement/v1\",\n  \"runtime\": \"devforge.delivery\",\n  \"protocol\": \"devforge.delivery-runtime/v1\",\n  \"provider\": \"{provider}\",\n  \"completion_mode\": \"managed-session\",\n  \"required_events\": [\n    \"SessionStart\",\n    \"UserPromptSubmit\",\n    \"Stop\",\n    \"SessionEnd\"\n  ]\n}}\n"
    )
}

fn hook_source(provider: &str) -> Value {
    let command = format!(
        "\"${{DEVFORGE_DELIVERY_EXECUTABLE:-devforge}}\" delivery hook --provider {provider}"
    );
    let mut events = serde_json::Map::new();
    for event in EVENTS {
        events.insert(
            event.to_owned(),
            json!([{"hooks": [{"type": "command", "command": command}]}]),
        );
    }
    json!({ "hooks": events })
}

fn write_delivery(fixture: &Fixture, provider: &str) {
    let plugin = plugin_dir(provider);
    fixture.write(
        &format!("{plugin}/hooks/runtime-requirements.json"),
        &requirement_text(provider),
    );
    fixture.write(
        &format!("{plugin}/hooks/hooks.json"),
        &hook_source(provider).to_string(),
    );
}

fn delivery_pass(providers: &[&str]) -> Value {
    let mut report = pass(8);
    let mut declared = serde_json::Map::new();
    for provider in providers {
        declared.insert((*provider).to_owned(), requirement(provider));
    }
    let object = report.as_object_mut().unwrap();
    object.insert("runtime_requirements".to_owned(), Value::Object(declared));
    object.insert("runtime_host".to_owned(), json!("NOT_VERIFIED"));
    report
}

// ---------------------------------------------------------------------------
// Traversal, retained evidence and required structure
// ---------------------------------------------------------------------------

#[test]
fn source_only_passes() {
    assert_pass(&Fixture::new().run(), &pass(8));
}

#[test]
fn retained_entrypoint_keeps_original_name_in_evidence_container() {
    let fixture = Fixture::new();
    let archive = "docs/skill-authoring/history/previous-builder-revision-2026-09-07/SKILL.md";
    let original = "---\nname: skill-builder\ndescription: Preserved original entrypoint.\n---\n";
    fixture.write(archive, original);
    assert_pass(&fixture.run(), &pass(8));
    assert_eq!(fs::read_to_string(fixture.path(archive)).unwrap(), original);
}

#[test]
fn retained_entrypoint_still_requires_valid_name_and_description() {
    let archive = "docs/skill-authoring/history/previous-builder-revision-2026-09-07/SKILL.md";
    for content in [
        "---\nname: INVALID_NAME\ndescription: Original.\n---\n",
        "---\nname: skill-builder\n---\n",
    ] {
        let fixture = Fixture::new();
        fixture.write(archive, content);
        assert_blocked(&fixture.run(), archive);
    }
}

#[test]
fn named_snapshot_entrypoints_preserve_original_metadata() {
    let original = "---\nname: skill-builder\ndescription: Preserved original.\n---\n";
    for container in [
        "history/previous-revision-20260907T010203Z",
        "integration-20260907T145039Z",
    ] {
        for snapshot in [
            "source-before",
            "source-after",
            "installed-before",
            "installed-after",
            "installed-builder-before",
            "installed-builder-before-02",
        ] {
            let fixture = Fixture::new();
            let relative = format!("docs/skill-authoring/{container}/{snapshot}/SKILL.md");
            fixture.write(&relative, original);
            assert_pass(&fixture.run(), &pass(8));
            assert_eq!(
                fs::read_to_string(fixture.path(&relative)).unwrap(),
                original
            );
        }
    }
}

#[test]
fn snapshot_name_exception_is_bounded_and_metadata_still_checked() {
    let original = "---\nname: skill-builder\ndescription: Preserved original.\n---\n";
    let mut cases: Vec<(&str, &str)> = vec![
        (
            "docs/skill-authoring/history/revision-2026-09-07/source-before/SKILL.md",
            "---\nname: INVALID\ndescription: Old.\n---\n",
        ),
        (
            "docs/skill-authoring/history/revision-2026-09-07/source-before/SKILL.md",
            "---\nname: skill-builder\n---\n",
        ),
    ];
    for relative in [
        "docs/skill-authoring/history/revision-2026-09-07/source-before/deeper/SKILL.md",
        "docs/skill-authoring/history/undated/SKILL.md",
        "docs/skill-authoring/history/revision-2026-09-07/source-before-lookalike/SKILL.md",
        "docs/skill-authoring/undated-container/installed-before/SKILL.md",
        "docs/skill-authoring/history-lookalike/revision/source-before/SKILL.md",
        "authored/docs/skill-authoring/history/revision/source-before/SKILL.md",
        "providers/codex/plugins/devforgeai/skills/source-before/SKILL.md",
    ] {
        cases.push((relative, original));
    }
    for (relative, content) in cases {
        let fixture = Fixture::new();
        fixture.write(relative, content);
        assert_blocked(&fixture.run(), relative);
    }
}

#[test]
fn archive_does_not_exempt_deeper_or_lookalike_skill_directories() {
    for relative in [
        "docs/skill-authoring/history/retained/authored/SKILL.md",
        "docs/skill-authoring/history-lookalike/retained/SKILL.md",
        "authored/docs/skill-authoring/history/retained/SKILL.md",
        "providers/codex/plugins/devforgeai/skills/wrong-name/SKILL.md",
    ] {
        let fixture = Fixture::new();
        fixture.write(
            relative,
            "---\nname: skill-builder\ndescription: Synthetic mismatched package.\n---\n",
        );
        assert_blocked(&fixture.run(), relative);
    }
}

#[test]
fn retained_tree_json_and_python_are_still_inspected() {
    for (filename, content) in [("bad.json", "{malformed"), ("bad.py", "def broken(:\n")] {
        let fixture = Fixture::new();
        let relative = format!("docs/skill-authoring/history/retained/references/{filename}");
        fixture.write(&relative, content);
        assert_blocked_containing(&fixture.run(), &relative);
    }
}

#[test]
fn valid_python_and_json_in_retained_evidence_stay_inspectable_and_pass() {
    let fixture = Fixture::new();
    fixture.write(
        "docs/skill-authoring/history/retained/references/fine.py",
        "def retained(argument):\n    return argument\n",
    );
    fixture.write(
        "docs/skill-authoring/history/retained/references/fine.json",
        "{\"retained\": true}",
    );
    assert_pass(&fixture.run(), &pass(8));
}

#[test]
fn frozen_runtime_review_workflows_are_inert_evidence() {
    let fixture = Fixture::new();
    let relative = "docs/skill-authoring/integration-20260907T145039Z/runtime-review-01/frozen-source/.github/workflows/ci.yml";
    let original = "name: Preserved runtime workflow\non: push\n";
    fixture.write(relative, original);
    assert_pass(&fixture.run(), &pass(8));
    assert_eq!(
        fs::read_to_string(fixture.path(relative)).unwrap(),
        original
    );
}

#[test]
fn workflow_ownership_is_still_enforced_outside_frozen_runtime_review() {
    for relative in [
        ".github/workflows/ci.yml",
        "providers/codex/.github/workflows/ci.yml",
        "docs/skill-authoring/undated/runtime-review-01/frozen-source/.github/workflows/ci.yml",
        "docs/skill-authoring/integration-20260907T145039Z/authored/.github/workflows/ci.yml",
        "docs/skill-authoring/integration-20260907T145039Z/runtime-review-lookalike/frozen-source/.github/workflows/ci.yml",
    ] {
        let fixture = Fixture::new();
        fixture.write(relative, "name: Synthetic workflow\n");
        assert_blocked(&fixture.run(), "GitHub workflows belong in DevForge");
    }
}

#[test]
fn retained_entrypoint_symlink_is_rejected() {
    let fixture = Fixture::new();
    let relative = "docs/skill-authoring/history/retained/SKILL.md";
    let target = fixture.outside("original-SKILL.md");
    write_file(
        &target,
        b"---\nname: skill-builder\ndescription: Synthetic original.\n---\n",
    );
    fs::create_dir_all(fixture.path(relative).parent().unwrap()).unwrap();
    symlink(&target, fixture.path(relative)).unwrap();
    assert_blocked(&fixture.run(), &format!("symlink not accepted: {relative}"));
}

#[test]
fn root_runtime_is_pruned_before_enumeration() {
    let fixture = Fixture::new();
    // Any enumeration of the private runtime would refuse both of these.
    let launcher = fixture.path(".devforge-runtime/codex/home/tmp/arg0/synthetic/wrapper");
    fs::create_dir_all(launcher.parent().unwrap()).unwrap();
    symlink(fixture.outside("benign-target"), &launcher).unwrap();
    fixture.write(
        ".devforge-runtime/non-source.json",
        "{malformed runtime JSON",
    );
    fixture.write("authored/nested/valid.json", "{}");
    assert_pass(&fixture.run(), &pass(8));
}

#[test]
fn root_runtime_symlinks_are_rejected() {
    for name in ["target-directory", "target-file", "missing-target"] {
        let fixture = Fixture::new();
        let target = fixture.outside(name);
        match name {
            "target-directory" => fs::create_dir_all(&target).unwrap(),
            "target-file" => write_file(&target, b"synthetic target\n"),
            _ => {}
        }
        symlink(&target, fixture.path(".devforge-runtime")).unwrap();
        assert_blocked(&fixture.run(), "symlink not accepted: .devforge-runtime");
    }
}

#[test]
fn authored_symlinks_are_rejected_including_nested_runtime() {
    for (relative, directory) in [
        ("authored/link", false),
        ("authored/directory-link", true),
        ("authored/.devforge-runtime/link", false),
    ] {
        let fixture = Fixture::new();
        let target = fixture.outside(if directory {
            "target-directory"
        } else {
            "target-file"
        });
        if directory {
            fs::create_dir_all(&target).unwrap();
        } else {
            write_file(&target, b"synthetic target\n");
        }
        fs::create_dir_all(fixture.path(relative).parent().unwrap()).unwrap();
        symlink(&target, fixture.path(relative)).unwrap();
        assert_blocked(&fixture.run(), &format!("symlink not accepted: {relative}"));
    }
}

#[test]
fn authored_json_is_checked_including_nested_runtime() {
    for relative in [
        "authored/invalid.json",
        "authored/.devforge-runtime/invalid.json",
    ] {
        let fixture = Fixture::new();
        fixture.write(relative, "{malformed authored JSON");
        assert_blocked_containing(&fixture.run(), relative);
    }
}

#[test]
fn existing_exclusions_retain_root_and_nested_scope() {
    let fixture = Fixture::new();
    for prefix in ["", "authored/"] {
        for name in [".git", ".poc", "__pycache__"] {
            let ignored = format!("{prefix}{name}");
            fixture.write(
                &format!("{ignored}/invalid.json"),
                "{malformed excluded JSON",
            );
            symlink(
                fixture.outside("missing-target"),
                fixture.path(&format!("{ignored}/link")),
            )
            .unwrap();
        }
    }
    assert_pass(&fixture.run(), &pass(8));
}

#[test]
fn existing_excluded_symlink_entries_remain_excluded() {
    let fixture = Fixture::new();
    for prefix in ["", "authored/"] {
        for name in [".git", ".poc", "__pycache__"] {
            let link = fixture.path(&format!("{prefix}{name}"));
            fs::create_dir_all(link.parent().unwrap()).unwrap();
            symlink(fixture.outside("missing-target"), &link).unwrap();
        }
    }
    assert_pass(&fixture.run(), &pass(8));
}

#[test]
fn required_structure_checks_remain_active() {
    let plugin = plugin_dir("codex");
    let manifest = format!("{plugin}/.codex-plugin/plugin.json");
    let evals = format!("{plugin}/skills/devforge-review/evals/evals.json");

    let fixture = Fixture::new();
    fixture.remove(&format!("{plugin}/skills/devforge-review/SKILL.md"));
    assert_blocked(&fixture.run(), "codex core skill missing");

    let fixture = Fixture::new();
    fixture.write(&manifest, "{\"name\": \"wrong\"}");
    assert_blocked(
        &fixture.run(),
        &fixture.path(&manifest).display().to_string(),
    );

    let fixture = Fixture::new();
    fixture.remove(&manifest);
    assert_blocked(
        &fixture.run(),
        &format!(
            "[Errno 2] No such file or directory: '{}'",
            fixture.path(&manifest).display()
        ),
    );

    let fixture = Fixture::new();
    fixture.write("plugins/devforgeai/retired.txt", "retired");
    assert_blocked(&fixture.run(), "retired shared plugin source still exists");

    let fixture = Fixture::new();
    fixture.write(&evals, "{\"skill_name\": \"wrong\", \"evals\": []}");
    assert_blocked(
        &fixture.run(),
        &format!("wrong eval skill: {}", fixture.path(&evals).display()),
    );

    let fixture = Fixture::new();
    fixture.write(
        &evals,
        "{\"skill_name\": \"devforge-review\", \"evals\": [{\"files\": [\"missing.txt\"]}]}",
    );
    assert_blocked(
        &fixture.run(),
        &format!(
            "missing fixture: {}",
            fixture
                .path(&format!(
                    "{plugin}/skills/devforge-review/evals/missing.txt"
                ))
                .display()
        ),
    );
}

// ---------------------------------------------------------------------------
// Codex agent TOML metadata (no legacy unit test covers this branch)
// ---------------------------------------------------------------------------

#[test]
fn agent_toml_requires_truthy_identity_metadata() {
    let relative = "providers/codex/agents/reviewer.toml";
    let fixture = Fixture::new();
    fixture.write(
        relative,
        "name = \"reviewer\"\ndescription = \"Synthetic\"\ndeveloper_instructions = \"Read only.\"\n",
    );
    assert_pass(&fixture.run(), &pass(8));

    for document in [
        "description = \"Synthetic\"\ndeveloper_instructions = \"Read only.\"\n",
        "name = \"\"\ndescription = \"Synthetic\"\ndeveloper_instructions = \"Read only.\"\n",
        "name = \"reviewer\"\ndescription = \"Synthetic\"\ndeveloper_instructions = []\n",
    ] {
        let fixture = Fixture::new();
        fixture.write(relative, document);
        assert_blocked(&fixture.run(), relative);
    }

    // Outside an `agents` path component only parsing is required.
    let fixture = Fixture::new();
    fixture.write("providers/codex/other/reviewer.toml", "name = \"\"\n");
    assert_pass(&fixture.run(), &pass(8));

    let fixture = Fixture::new();
    fixture.write("providers/codex/other/reviewer.toml", "name = \n");
    assert_blocked_containing(&fixture.run(), "providers/codex/other/reviewer.toml");
}

// ---------------------------------------------------------------------------
// Runtime requirement sidecars and bounded hook sources
// ---------------------------------------------------------------------------

#[test]
fn legacy_result_omits_runtime_claims() {
    let fixture = Fixture::new();
    fixture.write(
        &format!("{}/hooks/hooks.json", plugin_dir("codex")),
        "{\"hooks\": []}",
    );
    assert_pass(&fixture.run(), &pass(8));
}

#[test]
fn valid_sidecars_report_only_declared_providers_without_executing_host() {
    for providers in [vec!["codex"], vec!["claude"], vec!["codex", "claude"]] {
        let fixture = Fixture::new();
        for provider in &providers {
            write_delivery(&fixture, provider);
        }
        assert_pass(&fixture.run(), &delivery_pass(&providers));
    }
}

#[test]
fn invalid_json_and_duplicate_requirement_keys_are_rejected() {
    let valid = requirement("codex").to_string();
    let documents = [
        "{".to_owned(),
        format!(
            "{{\"schema_version\":\"devforge.runtime-requirement/v1\",{}",
            &valid[1..]
        ),
        format!(
            "{},\"protocol\":\"devforge.delivery-runtime/v1\"}}",
            &valid[..valid.len() - 1]
        ),
        valid.replace("\"managed-session\"", "NaN"),
    ];
    for document in documents {
        let fixture = Fixture::new();
        write_delivery(&fixture, "codex");
        fixture.write(
            &format!("{}/hooks/runtime-requirements.json", plugin_dir("codex")),
            &document,
        );
        assert_blocked_containing(&fixture.run(), "");
    }
}

#[test]
fn requirement_must_be_an_object_with_exact_keys() {
    let mut documents = vec![
        json!(null),
        json!([]),
        json!("requirement"),
        json!(1),
        json!(true),
        json!({}),
    ];
    let mut extra = requirement("codex");
    extra["unknown"] = json!("unsupported");
    documents.push(extra);
    for key in requirement("codex").as_object().unwrap().keys() {
        let mut document = requirement("codex");
        document.as_object_mut().unwrap().remove(key);
        documents.push(document);
    }
    for document in documents {
        let fixture = Fixture::new();
        write_delivery(&fixture, "codex");
        fixture.write(
            &format!("{}/hooks/runtime-requirements.json", plugin_dir("codex")),
            &document.to_string(),
        );
        assert_blocked_containing(
            &fixture.run(),
            "unsupported or malformed runtime requirement",
        );
    }
}

#[test]
fn requirement_literals_and_scalar_types_are_strict() {
    let unsupported: [(&str, Value); 5] = [
        ("schema_version", json!("devforge.runtime-requirement/v2")),
        ("runtime", json!("other.runtime")),
        ("protocol", json!("devforge.delivery-runtime/v2")),
        ("provider", json!("unsupported")),
        ("completion_mode", json!("unmanaged-session")),
    ];
    for (key, value) in unsupported {
        for invalid in [
            value.clone(),
            json!(null),
            json!(true),
            json!(1),
            json!([]),
            json!({}),
            json!(""),
        ] {
            let fixture = Fixture::new();
            write_delivery(&fixture, "codex");
            let mut document = requirement("codex");
            document[key] = invalid;
            fixture.write(
                &format!("{}/hooks/runtime-requirements.json", plugin_dir("codex")),
                &document.to_string(),
            );
            assert_blocked_containing(
                &fixture.run(),
                "unsupported or malformed runtime requirement",
            );
        }
    }
}

#[test]
fn requirement_provider_must_match_its_plugin() {
    for (provider, wrong) in [("codex", "claude"), ("claude", "codex")] {
        let fixture = Fixture::new();
        write_delivery(&fixture, provider);
        let mut document = requirement(provider);
        document["provider"] = json!(wrong);
        fixture.write(
            &format!("{}/hooks/runtime-requirements.json", plugin_dir(provider)),
            &document.to_string(),
        );
        assert_blocked_containing(
            &fixture.run(),
            "unsupported or malformed runtime requirement",
        );
    }
}

#[test]
fn required_events_are_exact_ordered_literals() {
    let reversed: Vec<&str> = EVENTS.iter().rev().copied().collect();
    let shortened: Vec<&str> = EVENTS[..3].to_vec();
    let mut with_duplicate: Vec<&str> = EVENTS.to_vec();
    with_duplicate.push("Stop");
    let mut with_extra: Vec<&str> = EVENTS.to_vec();
    with_extra.push("Interrupt");
    let invalid = [
        json!(null),
        json!(true),
        json!(4),
        json!("Stop"),
        json!({}),
        json!([]),
        json!(shortened),
        json!(reversed),
        json!(with_duplicate),
        json!(with_extra),
        json!(["SessionStart", "UserPromptSubmit", "stop", "SessionEnd"]),
        json!(["SessionStart", "UserPromptSubmit", 1, "SessionEnd"]),
    ];
    for events in invalid {
        let fixture = Fixture::new();
        write_delivery(&fixture, "codex");
        let mut document = requirement("codex");
        document["required_events"] = events;
        fixture.write(
            &format!("{}/hooks/runtime-requirements.json", plugin_dir("codex")),
            &document.to_string(),
        );
        assert_blocked_containing(
            &fixture.run(),
            "unsupported or malformed runtime requirement",
        );
    }
}

#[test]
fn runtime_requirement_needs_a_hook_source() {
    let fixture = Fixture::new();
    write_delivery(&fixture, "codex");
    fixture.remove(&format!("{}/hooks/hooks.json", plugin_dir("codex")));
    assert_blocked_containing(&fixture.run(), "missing framework hook component");
}

#[test]
fn hook_source_requires_each_event_once_and_no_extra_events() {
    let mut documents = vec![json!({"hooks": {}}), json!({"hooks": []}), json!({})];
    for event in EVENTS {
        for replacement in [
            None,
            Some(json!([])),
            Some(json!({})),
            Some(json!("command")),
        ] {
            let mut document = hook_source("codex");
            match replacement {
                None => {
                    document["hooks"].as_object_mut().unwrap().remove(event);
                }
                Some(value) => document["hooks"][event] = value,
            }
            documents.push(document);
        }
        let mut doubled = hook_source("codex");
        let group = doubled["hooks"][event][0].clone();
        doubled["hooks"][event].as_array_mut().unwrap().push(group);
        documents.push(doubled);
    }
    let mut extra = hook_source("codex");
    let stop = extra["hooks"]["Stop"].clone();
    extra["hooks"]["Interrupt"] = stop;
    documents.push(extra);
    for document in documents {
        let fixture = Fixture::new();
        write_delivery(&fixture, "codex");
        fixture.write(
            &format!("{}/hooks/hooks.json", plugin_dir("codex")),
            &document.to_string(),
        );
        assert_blocked_containing(&fixture.run(), "");
    }
}

#[test]
fn hook_source_requires_one_correct_command_handler_per_event() {
    let wrong_commands = [
        json!(""),
        json!(null),
        json!(1),
        json!("\"${DEVFORGE_DELIVERY_EXECUTABLE:-devforge}\" delivery hook --provider claude"),
        json!("${DEVFORGE_DELIVERY_EXECUTABLE:-devforge} delivery hook --provider codex"),
        json!("devforge delivery hook --provider codex"),
        json!("\"${DEVFORGE_DELIVERY_EXECUTABLE:-devforge}\" delivery status --provider codex"),
    ];
    for event in EVENTS {
        let valid = hook_source("codex")["hooks"][event][0]["hooks"][0].clone();
        let command = valid["command"].clone();
        let mut handlers = vec![
            json!([]),
            json!([valid.clone(), valid.clone()]),
            json!([{"type": "prompt", "prompt": "continue"}]),
            json!([{"command": command}]),
            json!([{"type": "command"}]),
            json!([null]),
        ];
        for command in &wrong_commands {
            let mut handler = valid.clone();
            handler["command"] = command.clone();
            handlers.push(json!([handler]));
        }
        for invalid in handlers {
            let fixture = Fixture::new();
            write_delivery(&fixture, "codex");
            let mut document = hook_source("codex");
            document["hooks"][event][0]["hooks"] = invalid;
            fixture.write(
                &format!("{}/hooks/hooks.json", plugin_dir("codex")),
                &document.to_string(),
            );
            assert_blocked_containing(&fixture.run(), "");
        }
    }
}

#[test]
fn duplicate_hook_event_keys_are_rejected() {
    let fixture = Fixture::new();
    write_delivery(&fixture, "codex");
    let groups = hook_source("codex")["hooks"]["Stop"].to_string();
    let document = hook_source("codex").to_string();
    fixture.write(
        &format!("{}/hooks/hooks.json", plugin_dir("codex")),
        &format!("{},\"Stop\":{groups}}}}}", &document[..document.len() - 2]),
    );
    assert_blocked_containing(&fixture.run(), "duplicate JSON key");
}

// ---------------------------------------------------------------------------
// Eval declarations
// ---------------------------------------------------------------------------

const EVALS: &str = "providers/codex/plugins/devforgeai/skills/devforge-review/evals/evals.json";

/// Synthetic self-eval declaration; intentionally malformed payloads stay inert strings.
fn document() -> Value {
    let cases: Vec<Value> = ["A", "B", "C"]
        .iter()
        .map(|tier| {
            json!({
                "id": format!("case-{}", tier.to_lowercase()),
                "title": "Synthetic case",
                "tier": tier,
                "status": "NOT_RUN",
                "fixture_set": "variant",
                "validator_request": "Read only.",
                "operator_setup": ["Use the frozen fixture."],
                "required_observations": ["Preserve separate outcomes."],
                "requires_real_control_bundle": *tier == "A",
            })
        })
        .collect();
    json!({
        "schema_version": SELF_SCHEMA,
        "skill_under_test": "devforge-review",
        "provider": "codex",
        "purpose": "Synthetic authored cases, never executed by structural validation.",
        "authoring_status": "AUTHORED_NOT_EXECUTED",
        "last_authored_date": "2025-01-02",
        "execution_status": "NOT_RUN",
        "execution_boundary": {
            "run_now": false, "instruction": "Retain as data.", "target_write_policy": "Read only.",
            "required_runtime": "Separately assigned.", "output_root": "Separately assigned.",
            "source_export_policy": "Source only.", "missing_prerequisite_result": "Report unavailable.",
        },
        "fixture_materialization": {
            "format": "Inline UTF-8.", "path_rule": "Contained relative paths.",
            "operator_record": "Freeze actual inputs.", "workers": "Isolated workers.",
            "comparison": "Separate outcomes.", "control_bundle": "Real evidence if required.",
            "synthetic_limit": "Not native evidence.", "artifact_case_facts": "Synthetic facts.",
            "protected_file_observation": "Read-only source.",
        },
        "common_grading": {
            "schema": "Separate grading.", "pass_rule": "Evidence required.",
            "fail_rule": "Observed contradiction.", "unavailable_rule": "Missing observation.",
            "preserve_targets": "Do not edit.", "no_auto_repair": "Author owns repair.",
            "expected_result_distinction": "Target and evaluator outcomes differ.",
        },
        "fixture_sets": {
            "origin": {"files": {
                "target/SKILL.md": "---\nname: [deliberately malformed\n",
                "target/data.json": "{deliberately malformed JSON",
                "target/never.py": "raise AssertionError('inline code must never execute')\n",
            }, "authority_note": "Synthetic source fixture."},
            "variant": {"base": "origin", "append_files": {"target/SKILL.md": "Opaque appendix.\n"},
                        "replace_files": {"target/data.json": "still not JSON"},
                        "absent_files": ["target/missing.md"]},
        },
        "cases": cases,
    })
}

fn eval_fixture(document: &Value) -> Fixture {
    let fixture = Fixture::new();
    fixture.write(EVALS, &document.to_string());
    fixture
}

fn eval_run(document: &Value) -> (Fixture, Output) {
    let fixture = eval_fixture(document);
    let output = fixture.run();
    (fixture, output)
}

fn assert_eval_blocked(document: &Value, needle: &str) {
    let (_fixture, output) = eval_run(document);
    assert_blocked_containing(&output, needle);
}

fn assert_eval_pass(document: &Value) {
    let (_fixture, output) = eval_run(document);
    assert_pass(&output, &pass(8));
}

fn inventory(root: &Path) -> Vec<(String, Vec<u8>)> {
    let mut files = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(&directory).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                pending.push(path);
            } else {
                files.push((
                    path.strip_prefix(root).unwrap().display().to_string(),
                    fs::read(&path).unwrap(),
                ));
            }
        }
    }
    files.sort();
    files
}

#[test]
fn explicit_schema_checks_all_tiers_without_executing_or_materializing_payloads() {
    let fixture = eval_fixture(&document());
    let before = inventory(&fixture.root);
    assert_pass(&fixture.run(), &pass(8));
    assert_eq!(inventory(&fixture.root), before);
    assert!(
        !fixture
            .path(EVALS)
            .parent()
            .unwrap()
            .join("target")
            .exists()
    );
}

#[test]
fn unknown_schema_and_unversioned_self_evals_do_not_fall_back() {
    for schema in [
        json!(null),
        json!("devforge.skill-validator-self-evals/v2"),
        json!("arbitrary/v1"),
    ] {
        let mut invalid = document();
        invalid["schema_version"] = schema;
        assert_eval_blocked(&invalid, "unsupported eval schema");
    }
    let mut invalid = document();
    invalid.as_object_mut().unwrap().remove("schema_version");
    assert_eval_blocked(&invalid, "wrong eval skill");
}

#[test]
fn optional_workspace_refinement_is_typed_declarative_metadata() {
    let mut valid = document();
    valid["workspace_allocation_refinement"] = json!({
        "change_id": "SYNTHETIC-CHANGE",
        "requirement_ids": ["SYNTHETIC-ONE", "SYNTHETIC-TWO"],
        "status": "AUTHORED_NOT_EXECUTED",
        "historical_expectations": "Retain original evidence.",
    });
    assert_eval_pass(&valid);
    for (key, value) in [
        ("change_id", json!("")),
        ("requirement_ids", json!([])),
        ("requirement_ids", json!(["SYNTHETIC-ONE", false])),
        ("requirement_ids", json!(["SYNTHETIC-ONE", "SYNTHETIC-ONE"])),
        ("status", json!(null)),
        ("historical_expectations", json!([])),
    ] {
        let mut invalid = valid.clone();
        invalid["workspace_allocation_refinement"][key] = value;
        assert_eval_blocked(&invalid, "");
    }
    let mut unknown = valid["workspace_allocation_refinement"].clone();
    unknown["unknown"] = json!(true);
    for value in [json!(null), json!([]), json!({}), unknown] {
        let mut invalid = valid.clone();
        invalid["workspace_allocation_refinement"] = value;
        assert_eval_blocked(&invalid, "");
    }
}

#[test]
fn optional_case_requirement_ids_are_nonempty_unique_text() {
    let mut valid = document();
    valid["cases"][0]["requirement_ids"] = json!(["SYNTHETIC-ONE", "SYNTHETIC-TWO"]);
    assert_eval_pass(&valid);
    for value in [
        json!(null),
        json!("ONE"),
        json!([]),
        json!([false]),
        json!([""]),
        json!(["ONE", "ONE"]),
    ] {
        let mut invalid = valid.clone();
        invalid["cases"][0]["requirement_ids"] = value;
        assert_eval_blocked(&invalid, "");
    }
}

#[test]
fn case_requirement_references_resolve_when_refinement_is_declared() {
    let mut valid = document();
    valid["workspace_allocation_refinement"] = json!({
        "change_id": "SYNTHETIC-CHANGE", "requirement_ids": ["DECLARED-ONE"],
        "status": "AUTHORED_NOT_EXECUTED", "historical_expectations": "Retain evidence.",
    });
    valid["cases"][0]["requirement_ids"] = json!(["DECLARED-ONE"]);
    assert_eval_pass(&valid);
    valid["cases"][0]["requirement_ids"] = json!(["UNDECLARED-TWO"]);
    assert_eval_blocked(&valid, "unknown case requirement ID");
}

#[test]
fn identity_and_provider_must_match_with_other_fields_valid() {
    for (field, value, message) in [
        (
            "skill_under_test",
            json!("different-skill"),
            "wrong eval skill",
        ),
        ("skill_under_test", json!(null), "wrong eval skill"),
        ("provider", json!("claude"), "wrong eval provider"),
        ("provider", json!(true), "wrong eval provider"),
    ] {
        let mut invalid = document();
        invalid[field] = value;
        assert_eval_blocked(&invalid, message);
    }
}

#[test]
fn envelope_and_metadata_shapes_are_required() {
    for invalid in [json!(null), json!([]), json!("evals")] {
        assert_eval_blocked(&invalid, "eval declaration must be an object");
    }
    for key in document().as_object().unwrap().keys() {
        if key == "schema_version" {
            continue;
        }
        let mut invalid = document();
        invalid.as_object_mut().unwrap().remove(key);
        assert_eval_blocked(&invalid, "unsupported fields");
    }
    for (field, value) in [
        ("purpose", json!("")),
        ("execution_status", json!(false)),
        ("execution_boundary", json!([])),
        ("fixture_materialization", json!({})),
        ("common_grading", json!(null)),
    ] {
        let mut invalid = document();
        invalid[field] = value;
        assert_eval_blocked(&invalid, "");
    }
    let mut invalid = document();
    invalid["execution_boundary"]["run_now"] = json!(0);
    assert_eval_blocked(&invalid, "run_now must be a boolean");
}

#[test]
fn cases_must_be_a_nonempty_array_and_ids_unique() {
    for cases in [
        json!(null),
        json!({}),
        json!("cases"),
        json!(true),
        json!([]),
    ] {
        let mut invalid = document();
        invalid["cases"] = cases;
        assert_eval_blocked(&invalid, "cases must be a nonempty list");
    }
    let mut invalid = document();
    let first = invalid["cases"][0]["id"].clone();
    invalid["cases"][1]["id"] = first;
    assert_eval_blocked(&invalid, "duplicate case ID");
}

#[test]
fn case_fields_and_optional_boolean_are_checked() {
    for (field, value) in [
        ("id", json!(" ")),
        ("title", json!(3)),
        ("tier", json!("unknown")),
        ("status", json!(null)),
        ("validator_request", json!([])),
        ("operator_setup", json!("setup")),
        ("operator_setup", json!([])),
        ("required_observations", json!([false])),
        ("requires_real_control_bundle", json!("true")),
    ] {
        let mut invalid = document();
        invalid["cases"][0][field] = value;
        assert_eval_blocked(&invalid, "");
    }
    for replacement in [json!(null), json!({"id": "incomplete"})] {
        let mut invalid = document();
        invalid["cases"][0] = replacement;
        assert_eval_blocked(&invalid, "");
    }
}

#[test]
fn case_fixture_and_base_references_must_resolve() {
    let mut invalid = document();
    invalid["cases"][0]["fixture_set"] = json!("undefined");
    assert_eval_blocked(&invalid, "unknown case fixture_set");
    let mut invalid = document();
    invalid["fixture_sets"]["variant"]["base"] = json!("undefined");
    assert_eval_blocked(&invalid, "unknown fixture base");
}

#[test]
fn self_and_multi_node_fixture_cycles_are_rejected() {
    for base in ["origin", "variant"] {
        let mut invalid = document();
        invalid["fixture_sets"]["origin"]["base"] = json!(base);
        assert_eval_blocked(&invalid, "fixture base cycle");
    }
}

#[test]
fn every_inline_path_operation_rejects_escaping_or_nonportable_paths() {
    let paths = [
        "../escape",
        "/absolute",
        "C:/absolute",
        "C:drive-relative",
        "folder\\escape",
        "folder/../escape",
        "folder//alias",
        "./alias",
        "nul\u{0}name",
    ];
    for operation in ["files", "replace_files", "append_files", "absent_files"] {
        for relative in paths {
            let mut invalid = document();
            invalid["fixture_sets"]["variant"][operation] = if operation == "absent_files" {
                json!([relative])
            } else {
                let mut entries = serde_json::Map::new();
                entries.insert(relative.to_owned(), json!("opaque"));
                Value::Object(entries)
            };
            assert_eval_blocked(&invalid, "unsafe inline fixture path");
        }
    }
}

#[test]
fn inline_content_must_be_utf8_text_in_every_map() {
    for operation in ["files", "replace_files", "append_files"] {
        for content in [json!(null), json!(true), json!(7), json!([]), json!({})] {
            let mut invalid = document();
            invalid["fixture_sets"]["variant"][operation] = json!({"target/data.json": content});
            assert_eval_blocked(&invalid, "inline content");
        }
    }
    // A lone surrogate escape is refused; the reason wording is a parity exception.
    let fixture = Fixture::new();
    let mut invalid = document();
    invalid["fixture_sets"]["variant"]["files"] = json!({"target/data.json": "PLACEHOLDER"});
    fixture.write(
        EVALS,
        &invalid
            .to_string()
            .replace("\"PLACEHOLDER\"", "\"\\ud800\""),
    );
    assert_blocked_containing(&fixture.run(), "");
}

#[test]
fn fixture_objects_and_operations_have_declared_shapes() {
    for fixtures in [json!(null), json!([]), json!({})] {
        let mut invalid = document();
        invalid["fixture_sets"] = fixtures;
        assert_eval_blocked(&invalid, "fixture_sets must be a nonempty object");
    }
    for (field, value) in [
        ("base", json!(null)),
        ("files", json!([])),
        ("replace_files", json!("text")),
        ("append_files", json!(null)),
        ("absent_files", json!({})),
        ("authority_note", json!(false)),
        ("unsupported_operation", json!({})),
    ] {
        let mut invalid = document();
        invalid["fixture_sets"]["variant"][field] = value;
        assert_eval_blocked(&invalid, "");
    }
}

#[test]
fn patch_targets_resolve_and_declared_absence_stays_absent() {
    for operation in ["replace_files", "append_files"] {
        let mut invalid = document();
        invalid["fixture_sets"]["variant"][operation] = json!({"target/undefined.txt": "opaque"});
        assert_eval_blocked(&invalid, &format!("unresolved {operation} target"));
    }
    let mut invalid = document();
    invalid["fixture_sets"]["variant"]["absent_files"] = json!(["target/SKILL.md"]);
    assert_eval_blocked(&invalid, "declared absent file is present");
}

#[test]
fn duplicate_keys_and_nonfinite_json_are_rejected() {
    let original = document().to_string();
    let fixture = Fixture::new();
    fixture.write(
        EVALS,
        &format!("{{\"schema_version\":\"{SELF_SCHEMA}\",{}", &original[1..]),
    );
    assert_blocked(&fixture.run(), "duplicate JSON key: schema_version");
    for token in ["NaN", "Infinity", "-Infinity", "1e999"] {
        let fixture = Fixture::new();
        fixture.write(
            EVALS,
            &original.replace(
                "\"requires_real_control_bundle\":true",
                &format!("\"requires_real_control_bundle\":{token}"),
            ),
        );
        assert_blocked(&fixture.run(), &format!("non-finite JSON value: {token}"));
    }
}

#[test]
fn legacy_nonfinite_values_require_strict_json_parsing() {
    // Legacy validation permits the extra field, so a later type check cannot
    // disguise removal of the strict parser's non-finite checks.
    let valid = json!({"skill_name": "devforge-review", "evals": [], "numeric_metadata": 1.5});
    assert_eval_pass(&valid);
    let original = valid.to_string();
    for token in ["NaN", "Infinity", "-Infinity", "1e999"] {
        let fixture = Fixture::new();
        fixture.write(
            EVALS,
            &original.replace(
                "\"numeric_metadata\":1.5",
                &format!("\"numeric_metadata\":{token}"),
            ),
        );
        assert_blocked(&fixture.run(), &format!("non-finite JSON value: {token}"));
    }
}

#[test]
fn legacy_eval_format_keeps_existing_file_checks() {
    let fixture = Fixture::new();
    fixture.write(
        "providers/codex/plugins/devforgeai/skills/devforge-review/evals/fixtures/input.txt",
        "Synthetic fixture bytes.",
    );
    fixture.write(
        EVALS,
        &json!({"skill_name": "devforge-review", "evals": [{"id": 1, "files": ["fixtures/input.txt"]}]})
            .to_string(),
    );
    assert_pass(&fixture.run(), &pass(8));
    for (document, message) in [
        (
            json!({"skill_name": "wrong", "evals": []}),
            "wrong eval skill",
        ),
        (
            json!({"skill_name": "devforge-review", "evals": [{"files": ["../outside"]}]}),
            "escapes eval root",
        ),
        (
            json!({"skill_name": "devforge-review", "evals": [{"files": ["missing.txt"]}]}),
            "missing fixture",
        ),
        (
            json!({"skill_name": "devforge-review", "evals": null}),
            "evals must be a list",
        ),
        (
            json!({"skill_name": "devforge-review", "evals": [null]}),
            "legacy eval case must be an object",
        ),
        (
            json!({"skill_name": "devforge-review", "evals": [{"files": "input.txt"}]}),
            "legacy files must be a list",
        ),
    ] {
        assert_eval_blocked(&document, message);
    }
}

// ---------------------------------------------------------------------------
// Compatibility oracle against the unchanged legacy script
// ---------------------------------------------------------------------------

type Mutation = fn(&Fixture);

/// (name, stderr compared verbatim, mutation). Cases with `false` differ only in
/// diagnostic wording or in the legacy script's uncaught-exception exit status.
const ORACLE: [(&str, bool, Mutation); 18] = [
    ("valid", true, |_| {}),
    ("retained-entrypoint", true, |fixture| {
        fixture.write(
            "docs/skill-authoring/history/previous-builder-revision-2026-09-07/SKILL.md",
            "---\nname: skill-builder\ndescription: Preserved.\n---\n",
        );
    }),
    ("frozen-workflow", true, |fixture| {
        fixture.write(
            "docs/skill-authoring/integration-20260907T145039Z/runtime-review-01/frozen-source/.github/workflows/ci.yml",
            "name: Preserved\non: push\n",
        );
    }),
    ("delivery-sidecars", true, |fixture| {
        write_delivery(fixture, "codex");
        write_delivery(fixture, "claude");
    }),
    ("hook-command-mismatch", true, |fixture| {
        write_delivery(fixture, "codex");
        fixture.write(
            &format!("{}/hooks/hooks.json", plugin_dir("codex")),
            &hook_source("claude").to_string(),
        );
    }),
    ("workflow-ownership", true, |fixture| {
        fixture.write(
            "providers/codex/.github/workflows/ci.yml",
            "name: Synthetic\n",
        );
    }),
    ("symlink", true, |fixture| {
        let target = fixture.outside("target-file");
        write_file(&target, b"synthetic target\n");
        fs::create_dir_all(fixture.path("authored")).unwrap();
        symlink(&target, fixture.path("authored/link")).unwrap();
    }),
    ("skill-name-mismatch", true, |fixture| {
        fixture.write(
            "providers/codex/plugins/devforgeai/skills/wrong-name/SKILL.md",
            "---\nname: skill-builder\ndescription: Synthetic.\n---\n",
        );
    }),
    ("missing-core-skill", true, |fixture| {
        fixture.remove("providers/codex/plugins/devforgeai/skills/devforge-review/SKILL.md");
    }),
    ("wrong-manifest-name", true, |fixture| {
        fixture.write(
            "providers/codex/plugins/devforgeai/.codex-plugin/plugin.json",
            "{\"name\": \"wrong\"}",
        );
    }),
    ("missing-manifest", true, |fixture| {
        fixture.remove("providers/codex/plugins/devforgeai/.codex-plugin/plugin.json");
    }),
    ("retired-plugin", true, |fixture| {
        fixture.write("plugins/devforgeai/retired.txt", "retired");
    }),
    ("eval-wrong-skill", true, |fixture| {
        fixture.write(EVALS, "{\"skill_name\": \"wrong\", \"evals\": []}");
    }),
    ("eval-missing-fixture", true, |fixture| {
        fixture.write(
            EVALS,
            "{\"skill_name\": \"devforge-review\", \"evals\": [{\"files\": [\"missing.txt\"]}]}",
        );
    }),
    ("eval-nonfinite", true, |fixture| {
        fixture.write(
            EVALS,
            "{\"skill_name\": \"devforge-review\", \"evals\": [], \"n\": NaN}",
        );
    }),
    ("eval-self-schema", true, |fixture| {
        fixture.write(EVALS, &document().to_string());
    }),
    ("agent-toml", true, |fixture| {
        fixture.write(
            "providers/codex/agents/reviewer.toml",
            "name = \"reviewer\"\n",
        );
    }),
    // Diagnostic wording (malformed JSON) and the legacy uncaught SyntaxError.
    ("malformed-json", false, |fixture| {
        fixture.write("authored/broken.json", "{malformed");
    }),
];

#[test]
fn matches_the_legacy_validator_on_synthetic_trees() {
    for (name, exact_stderr, mutate) in ORACLE {
        let fixture = Fixture::new();
        mutate(&fixture);
        let legacy = fixture.legacy();
        let rust = fixture.run();
        assert_eq!(
            rust.status.code(),
            legacy.status.code(),
            "{name}: exit status; rust stderr={:?} legacy stderr={:?}",
            text(&rust.stderr),
            text(&legacy.stderr)
        );
        assert_eq!(
            text(&rust.stdout),
            text(&legacy.stdout),
            "{name}: stdout bytes"
        );
        if exact_stderr {
            assert_eq!(text(&rust.stderr), text(&legacy.stderr), "{name}: stderr");
        } else {
            assert!(
                text(&rust.stderr).starts_with("BLOCKED: "),
                "{name}: stderr={:?}",
                text(&rust.stderr)
            );
        }
    }
}

#[test]
fn python_syntax_inspection_refuses_what_the_legacy_parser_refuses() {
    // The legacy script leaves SyntaxError uncaught (exit 1); the compiled
    // command refuses with BLOCKED and exit 2. Both are non-zero refusals.
    let fixture = Fixture::new();
    fixture.write("authored/broken.py", "def broken(:\n");
    let legacy = fixture.legacy();
    let rust = fixture.run();
    assert_ne!(
        legacy.status.code(),
        Some(0),
        "legacy accepted broken syntax"
    );
    assert_blocked_containing(&rust, "authored/broken.py");
}

fn companion_framework() -> Option<PathBuf> {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    ["../DevForgeAI", "../../framework/DevForgeAI"]
        .iter()
        .map(|relative| manifest.join(relative))
        .find(|candidate| candidate.join("providers").is_dir())
        .and_then(|path| fs::canonicalize(path).ok())
}

#[test]
fn refuses_the_real_framework_checkout_like_the_legacy_script() {
    let Some(framework) = companion_framework() else {
        eprintln!("companion framework absent; real-tree oracle NOT_RUN");
        return;
    };
    let legacy = run_legacy(&framework);
    let rust = run_rust(&framework);
    assert_eq!(
        rust.status.code(),
        legacy.status.code(),
        "real tree exit status; rust stderr={:?} legacy stderr={:?}",
        text(&rust.stderr),
        text(&legacy.stderr)
    );
    if legacy.status.code() == Some(0) {
        assert_eq!(text(&rust.stdout), text(&legacy.stdout), "real tree stdout");
        return;
    }
    // Both refuse. The checkout holds several independent defects and neither
    // validator promises which one is reported first, so the comparable facts
    // are the exit status and the single-line refusal shape. A divergence is
    // printed rather than asserted; the byte-level parity evidence is the
    // synthetic oracle, the real hook packages and the repaired-copy runs
    // recorded in docs/integration/framework-structure-validation.md.
    for (who, reason) in [
        ("rust", text(&rust.stderr)),
        ("legacy", text(&legacy.stderr)),
    ] {
        let mut lines = reason.lines();
        let first = lines.next().unwrap_or_default();
        assert!(
            first.starts_with("BLOCKED: ") && first.len() > "BLOCKED: ".len(),
            "{who} refusal was not a single BLOCKED line: {reason:?}"
        );
        assert_eq!(lines.next(), None, "{who} refusal was not one line");
    }
    assert!(rust.stdout.is_empty() && legacy.stdout.is_empty());
    if text(&rust.stderr) != text(&legacy.stderr) {
        eprintln!(
            "real-tree first defect differs (traversal order is unspecified in both)\n  rust:   {}  legacy: {}",
            text(&rust.stderr),
            text(&legacy.stderr)
        );
    }
}

#[test]
fn the_real_authored_hook_packages_validate_byte_for_byte_like_the_legacy_script() {
    let Some(framework) = companion_framework() else {
        eprintln!("companion framework absent; real hook-package oracle NOT_RUN");
        return;
    };
    let fixture = Fixture::new();
    let mut declared = Vec::new();
    for provider in ["claude", "codex"] {
        let source = framework.join(format!("{}/hooks", plugin_dir(provider)));
        if !source.is_dir() {
            continue;
        }
        for name in ["runtime-requirements.json", "hooks.json"] {
            let Ok(bytes) = fs::read(source.join(name)) else {
                continue;
            };
            write_file(
                &fixture.path(&format!("{}/hooks/{name}", plugin_dir(provider))),
                &bytes,
            );
        }
        let manifest = framework.join(format!(
            "{}/.{provider}-plugin/plugin.json",
            plugin_dir(provider)
        ));
        if let Ok(bytes) = fs::read(&manifest) {
            write_file(
                &fixture.path(&format!(
                    "{}/.{provider}-plugin/plugin.json",
                    plugin_dir(provider)
                )),
                &bytes,
            );
        }
        declared.push(provider);
    }
    assert!(
        !declared.is_empty(),
        "no authored hook package was readable"
    );
    let legacy = fixture.legacy();
    let rust = fixture.run();
    assert_eq!(rust.status.code(), legacy.status.code(), "exit status");
    assert_eq!(text(&rust.stdout), text(&legacy.stdout), "stdout bytes");
    assert_eq!(text(&rust.stderr), text(&legacy.stderr), "stderr");
}

#[test]
fn a_missing_framework_root_is_refused_like_the_legacy_script() {
    let fixture = Fixture::empty();
    fixture.remove_dir("");
    let legacy = fixture.legacy();
    let rust = fixture.run();
    assert_eq!(rust.status.code(), legacy.status.code());
    assert_eq!(text(&rust.stderr), text(&legacy.stderr));
}
