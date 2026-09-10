//! Compiled port of `scripts/validate_mvp.py`: structural checks over a selected
//! `docs/mvp` tree. It inspects documents and reports; it never runs model
//! evaluations, executes candidate code, or records acceptance.
//!
//! Contract (unchanged from the legacy validator): collected findings produce a
//! `FAIL` report and exit status 2; `PASS` exits 0; malformed or unreadable input
//! is refused with `BLOCKED: <reason>` on stderr, exit status 2 and no report.
use anyhow::{Result, anyhow, bail};
use clap::Subcommand;
use serde::de::{self, Deserializer, MapAccess, SeqAccess, Visitor};
use serde::ser::SerializeMap;
use serde::{Deserialize, Serialize, Serializer};
use sha2::{Digest, Sha256};
use std::collections::{BTreeSet, VecDeque};
use std::ffi::OsString;
use std::fmt;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

const HEADINGS: [&str; 7] = [
    "User goal",
    "Inputs and provenance",
    "Workflow and phase exits",
    "Outputs and standardized templates",
    "Validation and behavioral acceptance",
    "Native creator authoring prompt",
    "Shared authoring requirements",
];
const SCOPE: &str = "Local links, JSON, index/source mappings, required sections, cached research digests, whitespace/fences";
const NOT_CHECKED: [&str; 4] = [
    "Full YAML/schema semantics",
    "semantic provenance",
    "Mermaid rendering",
    "native skill behavior",
];
const SYMLINK_HOP_LIMIT: usize = 64;

#[derive(Subcommand)]
pub enum Action {
    /// Check authored document links, indexes and source hashes; never run model evaluations.
    Mvp {
        /// Selected docs/mvp directory; provider sources are resolved two levels above it.
        #[arg(long)]
        mvp: PathBuf,
        /// Optional full report including files_sha256; parent directories are created.
        #[arg(long)]
        report: Option<PathBuf>,
    },
}

/// Ordered JSON document. Object entries keep document order so findings are
/// reported in the same order as the legacy validator; a repeated key replaces
/// the earlier value in place.
enum Json {
    Null,
    Bool(bool),
    Number(serde_json::Number),
    Str(String),
    List(Vec<Json>),
    Dict(Vec<(String, Json)>),
}

impl<'de> Deserialize<'de> for Json {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct JsonVisitor;
        impl<'de> Visitor<'de> for JsonVisitor {
            type Value = Json;
            fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
                formatter.write_str("any JSON value")
            }
            fn visit_bool<E: de::Error>(self, value: bool) -> Result<Json, E> {
                Ok(Json::Bool(value))
            }
            fn visit_i64<E: de::Error>(self, value: i64) -> Result<Json, E> {
                Ok(Json::Number(value.into()))
            }
            fn visit_u64<E: de::Error>(self, value: u64) -> Result<Json, E> {
                Ok(Json::Number(value.into()))
            }
            fn visit_f64<E: de::Error>(self, value: f64) -> Result<Json, E> {
                serde_json::Number::from_f64(value)
                    .map(Json::Number)
                    .ok_or_else(|| E::custom("non-finite number"))
            }
            fn visit_str<E: de::Error>(self, value: &str) -> Result<Json, E> {
                Ok(Json::Str(value.to_owned()))
            }
            fn visit_string<E: de::Error>(self, value: String) -> Result<Json, E> {
                Ok(Json::Str(value))
            }
            fn visit_unit<E: de::Error>(self) -> Result<Json, E> {
                Ok(Json::Null)
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Json, A::Error> {
                let mut items = Vec::new();
                while let Some(item) = seq.next_element()? {
                    items.push(item);
                }
                Ok(Json::List(items))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Json, A::Error> {
                let mut entries: Vec<(String, Json)> = Vec::new();
                while let Some((key, value)) = map.next_entry::<String, Json>()? {
                    match entries.iter_mut().find(|(existing, _)| *existing == key) {
                        Some(slot) => slot.1 = value,
                        None => entries.push((key, value)),
                    }
                }
                Ok(Json::Dict(entries))
            }
        }
        deserializer.deserialize_any(JsonVisitor)
    }
}

impl Json {
    fn kind(&self) -> &'static str {
        match self {
            Json::Null => "null",
            Json::Bool(_) => "boolean",
            Json::Number(_) => "number",
            Json::Str(_) => "string",
            Json::List(_) => "array",
            Json::Dict(_) => "object",
        }
    }

    fn get(&self, key: &str, what: &str) -> Result<&Json> {
        let Json::Dict(entries) = self else {
            bail!("{what} must be an object, found {}", self.kind());
        };
        entries
            .iter()
            .find(|(existing, _)| existing == key)
            .map(|(_, value)| value)
            .ok_or_else(|| anyhow!("missing key '{key}' in {what}"))
    }

    fn list(&self, what: &str) -> Result<&[Json]> {
        match self {
            Json::List(items) => Ok(items),
            other => bail!("{what} must be an array, found {}", other.kind()),
        }
    }

    fn dict(&self, what: &str) -> Result<&[(String, Json)]> {
        match self {
            Json::Dict(entries) => Ok(entries),
            other => bail!("{what} must be an object, found {}", other.kind()),
        }
    }

    fn str(&self, what: &str) -> Result<&str> {
        match self {
            Json::Str(text) => Ok(text),
            other => bail!("{what} must be a string, found {}", other.kind()),
        }
    }

    /// Python truthiness of the decoded value.
    fn truthy(&self) -> bool {
        match self {
            Json::Null => false,
            Json::Bool(value) => *value,
            Json::Number(number) => number.as_f64().is_some_and(|value| value != 0.0),
            Json::Str(text) => !text.is_empty(),
            Json::List(items) => !items.is_empty(),
            Json::Dict(entries) => !entries.is_empty(),
        }
    }

    /// Text used inside findings; strings appear verbatim as in the legacy messages.
    fn display(&self) -> String {
        match self {
            Json::Null => "None".to_owned(),
            Json::Bool(true) => "True".to_owned(),
            Json::Bool(false) => "False".to_owned(),
            Json::Number(number) => number.to_string(),
            Json::Str(text) => text.clone(),
            Json::List(_) | Json::Dict(_) => format!("<{}>", self.kind()),
        }
    }
}

/// Per-file SHA-256 inventory in traversal order.
struct Inventory(Vec<(String, String)>);

impl Serialize for Inventory {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut map = serializer.serialize_map(Some(self.0.len()))?;
        for (path, digest) in &self.0 {
            map.serialize_entry(path, digest)?;
        }
        map.end()
    }
}

#[derive(Serialize)]
struct Report {
    schema_version: u32,
    created_at_utc: String,
    status: &'static str,
    errors: Vec<String>,
    specifications: usize,
    skill_output_templates: usize,
    shared_templates: usize,
    authoring_templates: usize,
    scope: &'static str,
    not_checked: [&'static str; 4],
    native_skill_behavior: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    files_sha256: Option<Inventory>,
}

pub fn main(action: &Action) {
    let Action::Mvp { mvp, report } = action;
    match run(mvp, report.as_deref()) {
        Ok(status) => std::process::exit(status),
        Err(error) => {
            eprintln!("BLOCKED: {error}");
            std::process::exit(2);
        }
    }
}

fn run(mvp: &Path, report_path: Option<&Path>) -> Result<i32> {
    let root = realpath(&std::env::current_dir()?.join(mvp));
    let mut report = validate(&root)?;
    let status = if report.status == "PASS" { 0 } else { 2 };
    if let Some(path) = report_path {
        if let Some(parent) = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            fs::create_dir_all(parent).map_err(|error| io_error(&error, parent))?;
        }
        let mut document = serde_json::to_string_pretty(&report)?;
        document.push('\n');
        fs::write(path, document).map_err(|error| io_error(&error, path))?;
    }
    report.files_sha256 = None;
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(status)
}

fn validate(root: &Path) -> Result<Report> {
    let mut errors = Vec::new();
    let index: Json = parse_json(
        &read_text(&local(root, "package-index.json")?)?,
        "package-index.json",
    )?;
    let skills = index.get("skills", "package-index.json")?.list("skills")?;
    let mut names = BTreeSet::new();
    for skill in skills {
        names.insert(skill.get("name", "skill entry")?.str("skill name")?);
    }
    if names.len() != 12 || skills.len() != 12 {
        errors.push("expected 12 unique skill specifications".to_owned());
    }
    let mut template_count = 0;
    for skill in skills {
        let name = skill.get("name", "skill entry")?.str("skill name")?;
        if !valid_skill_name(name) {
            bail!("invalid indexed skill name");
        }
        let specification = local(
            root,
            skill
                .get("specification", "skill entry")?
                .str("specification")?,
        )?;
        let text = read_text(&specification)?;
        let basename = specification
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_default();
        for heading in HEADINGS {
            if !text.contains(heading) {
                errors.push(format!("{basename}: missing {heading}"));
            }
        }
        for template in skill.get("templates", "skill entry")?.list("templates")? {
            let path = template
                .get("path", "template entry")?
                .str("template path")?;
            template_count += 1;
            if !is_file(&local(root, path)?) {
                errors.push(format!("missing template: {path}"));
            }
            for consumer in template
                .get("consumers", "template entry")?
                .list("consumers")?
            {
                let known = match consumer {
                    Json::Str(text) => names.contains(text.as_str()),
                    Json::List(_) | Json::Dict(_) => bail!("consumer must be a string"),
                    _ => false,
                };
                if !known {
                    errors.push(format!("unknown consumer: {}", consumer.display()));
                }
            }
        }
        for (provider, entry) in skill
            .get("implementations", "skill entry")?
            .dict("implementations")?
        {
            let source = entry.get("source", "implementation entry")?;
            if !source.truthy() {
                continue;
            }
            let expected = format!("providers/{provider}/plugins/devforgeai/skills/{name}");
            let framework = root.parent().and_then(Path::parent).ok_or_else(|| {
                anyhow!("selected document root has no framework root two levels up")
            })?;
            let matches = matches!(source, Json::Str(text) if *text == expected)
                && is_file(&framework.join(&expected).join("SKILL.md"));
            if !matches {
                errors.push(format!("incorrect provider source: {}", source.display()));
            }
        }
    }
    let shared_templates = index
        .get("shared_templates", "package-index.json")?
        .list("shared_templates")?;
    let authoring_templates = index
        .get("authoring_templates", "package-index.json")?
        .list("authoring_templates")?;
    let indexed = index
        .get("shared_contracts", "package-index.json")?
        .list("shared_contracts")?
        .iter()
        .chain(shared_templates)
        .chain(authoring_templates);
    for relative in indexed {
        let relative = relative.str("indexed document")?;
        if !is_file(&local(root, relative)?) {
            errors.push(format!("missing indexed document: {relative}"));
        }
    }

    let mut files = Vec::new();
    for path in sorted_entries(root)? {
        let relative = path.strip_prefix(root).unwrap_or(&path);
        let relative_text = relative
            .to_str()
            .ok_or_else(|| anyhow!("non-UTF-8 document name under {}", root.display()))?
            .to_owned();
        if is_symlink(&path) {
            errors.push(format!("symlink document: {relative_text}"));
            continue;
        }
        let first = relative.components().next();
        if !is_file(&path)
            || matches!(first, Some(Component::Normal(name)) if name == "validation")
            || relative_text == "validation.json"
        {
            continue;
        }
        let bytes = read_bytes(&path)?;
        files.push((
            relative_text.clone(),
            format!("{:x}", Sha256::digest(&bytes)),
        ));
        let extension = path.extension().and_then(|extension| extension.to_str());
        if extension == Some("json") {
            parse_json::<de::IgnoredAny>(&decode_text(bytes, &path)?, &relative_text)?;
            continue;
        }
        if extension != Some("md") {
            continue;
        }
        let text = decode_text(bytes, &path)?;
        let lines = python_splitlines(&text);
        if lines.iter().filter(|line| line.starts_with("```")).count() % 2 == 1 {
            errors.push(format!("unbalanced code fence: {relative_text}"));
        }
        if lines.iter().any(|line| python_rstrip(line) != *line) {
            errors.push(format!("trailing whitespace: {relative_text}"));
        }
        let parent = path.parent().unwrap_or(root);
        for link in markdown_links(&text) {
            if link.contains("://") || link.starts_with('#') || link.contains("{{") {
                continue;
            }
            let target = link.split_once('#').map_or(link, |(target, _)| target);
            if !target.is_empty() && !parent.join(target).exists() {
                errors.push(format!("broken local link: {relative_text} -> {target}"));
            }
        }
    }

    let research = root.join("research");
    let sources: Json = parse_json(
        &read_text(&research.join("sources.json"))?,
        "research/sources.json",
    )?;
    for source in sources
        .get("sources", "research/sources.json")?
        .list("sources")?
    {
        let entries = source.dict("research source")?;
        if !entries.iter().any(|(key, _)| key == "snapshot") {
            continue;
        }
        let snapshot = local(
            &research,
            source.get("snapshot", "research source")?.str("snapshot")?,
        )?;
        let current = is_file(&snapshot)
            && matches!(
                source.get("sha256", "research source")?,
                Json::Str(expected) if *expected == format!("{:x}", Sha256::digest(read_bytes(&snapshot)?))
            );
        if !current {
            errors.push(format!(
                "research response digest mismatch: {}",
                source.get("id", "research source")?.display()
            ));
        }
    }

    Ok(Report {
        schema_version: 2,
        created_at_utc: utc_isoformat(SystemTime::now()),
        status: if errors.is_empty() { "PASS" } else { "FAIL" },
        errors,
        specifications: skills.len(),
        skill_output_templates: template_count,
        shared_templates: shared_templates.len(),
        authoring_templates: authoring_templates.len(),
        scope: SCOPE,
        not_checked: NOT_CHECKED,
        native_skill_behavior: "NOT_EVALUATED",
        files_sha256: Some(Inventory(files)),
    })
}

/// `[a-z0-9]+(?:-[a-z0-9]+)*` over the whole name.
fn valid_skill_name(name: &str) -> bool {
    !name.is_empty()
        && name.split('-').all(|segment| {
            !segment.is_empty()
                && segment
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
        })
}

/// Resolve a document path inside `root` (already resolved). Absolute paths,
/// escapes through `..` or symlinks, and a symlinked final entry are refused.
fn local(root: &Path, relative: &str) -> Result<PathBuf> {
    let path = root.join(relative);
    if Path::new(relative).is_absolute() || !realpath(&path).starts_with(root) {
        bail!("path outside selected document root: {relative}");
    }
    if is_symlink(&path) {
        bail!("symlink document: {relative}");
    }
    Ok(path)
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

fn is_symlink(path: &Path) -> bool {
    fs::symlink_metadata(path).is_ok_and(|metadata| metadata.is_symlink())
}

fn is_file(path: &Path) -> bool {
    fs::metadata(path).is_ok_and(|metadata| metadata.is_file())
}

/// Every entry below `root`, in component-wise path order, without following
/// symlinked directories; symlinks themselves are listed.
fn sorted_entries(root: &Path) -> Result<Vec<PathBuf>> {
    fn walk(
        directory: &Path,
        entries: &mut Vec<(Vec<OsString>, PathBuf)>,
        root: &Path,
    ) -> Result<()> {
        for entry in fs::read_dir(directory).map_err(|error| io_error(&error, directory))? {
            let entry = entry.map_err(|error| io_error(&error, directory))?;
            let path = entry.path();
            let key = path
                .strip_prefix(root)
                .unwrap_or(&path)
                .components()
                .map(|component| component.as_os_str().to_os_string())
                .collect();
            let is_directory = entry.file_type().is_ok_and(|kind| kind.is_dir());
            entries.push((key, path.clone()));
            if is_directory {
                walk(&path, entries, root)?;
            }
        }
        Ok(())
    }
    let mut entries = Vec::new();
    walk(root, &mut entries, root)?;
    entries.sort();
    Ok(entries.into_iter().map(|(_, path)| path).collect())
}

fn read_bytes(path: &Path) -> Result<Vec<u8>> {
    fs::read(path).map_err(|error| io_error(&error, path))
}

/// Text with universal newline translation, matching Python's `read_text()`.
fn read_text(path: &Path) -> Result<String> {
    decode_text(read_bytes(path)?, path)
}

fn decode_text(bytes: Vec<u8>, path: &Path) -> Result<String> {
    let text = String::from_utf8(bytes)
        .map_err(|error| anyhow!("'utf-8' codec can't decode {}: {error}", path.display()))?;
    Ok(text.replace("\r\n", "\n").replace('\r', "\n"))
}

fn parse_json<'a, T: Deserialize<'a>>(text: &'a str, what: &str) -> Result<T> {
    serde_json::from_str(text).map_err(|error| anyhow!("{what}: {error}"))
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

/// Python `str.splitlines()` after newline translation.
fn python_splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    for (offset, character) in text.char_indices() {
        if matches!(
            character,
            '\n' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}'
        ) {
            lines.push(&text[start..offset]);
            start = offset + character.len_utf8();
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// Python `str.rstrip()`: Unicode whitespace plus the ASCII separators 0x1c-0x1f.
fn python_rstrip(line: &str) -> &str {
    line.trim_end_matches(|character: char| {
        character.is_whitespace() || ('\x1c'..='\x1f').contains(&character)
    })
}

/// Targets of `](...)` occurrences, exactly as the legacy `\]\(([^)]+)\)` scan found them.
fn markdown_links(text: &str) -> Vec<&str> {
    let bytes = text.as_bytes();
    let mut links = Vec::new();
    let mut index = 0;
    while index + 1 < bytes.len() {
        if bytes[index] == b']' && bytes[index + 1] == b'(' {
            let start = index + 2;
            if let Some(length) = bytes[start..]
                .iter()
                .position(|&byte| byte == b')')
                .filter(|&length| length > 0)
            {
                links.push(&text[start..start + length]);
                index = start + length + 1;
                continue;
            }
        }
        index += 1;
    }
    links
}

/// Python `datetime.now(timezone.utc).isoformat()`: microseconds only when nonzero.
fn utc_isoformat(now: SystemTime) -> String {
    let elapsed = now.duration_since(UNIX_EPOCH).unwrap_or_default();
    let seconds = elapsed.as_secs();
    let micros = elapsed.subsec_micros();
    let (year, month, day) = civil_from_days((seconds / 86_400) as i64);
    let (hour, minute, second) = (seconds / 3_600 % 24, seconds / 60 % 60, seconds % 60);
    let fraction = if micros == 0 {
        String::new()
    } else {
        format!(".{micros:06}")
    };
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}{fraction}+00:00")
}

/// Proleptic Gregorian date from days since 1970-01-01.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let shifted = days + 719_468;
    let era = shifted.div_euclid(146_097);
    let day_of_era = shifted - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let shifted_month = (5 * day_of_year + 2) / 153;
    let day = (day_of_year - (153 * shifted_month + 2) / 5 + 1) as u32;
    let month = if shifted_month < 10 {
        shifted_month + 3
    } else {
        shifted_month - 9
    } as u32;
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

#[cfg(test)]
mod tests {
    use super::{
        civil_from_days, markdown_links, python_rstrip, python_splitlines, utc_isoformat,
        valid_skill_name,
    };
    use std::time::{Duration, UNIX_EPOCH};

    #[test]
    fn timestamp_matches_python_isoformat_shapes() {
        assert_eq!(utc_isoformat(UNIX_EPOCH), "1970-01-01T00:00:00+00:00");
        let stamp = UNIX_EPOCH + Duration::new(1_788_000_000, 5_000);
        assert_eq!(utc_isoformat(stamp), "2026-08-29T10:40:00.000005+00:00");
        assert_eq!(civil_from_days(20_000), (2024, 10, 4));
        assert_eq!(civil_from_days(-1), (1969, 12, 31));
    }

    #[test]
    fn skill_names_follow_the_legacy_pattern() {
        assert!(valid_skill_name("devforge-brainstorm"));
        assert!(valid_skill_name("a1"));
        for invalid in ["", "-a", "a-", "a--b", "A", "a_b", "a b", "a\n"] {
            assert!(!valid_skill_name(invalid), "{invalid:?}");
        }
    }

    #[test]
    fn markdown_helpers_follow_python_semantics() {
        assert_eq!(markdown_links("[a](x) [b]() [c](y#z) ]("), ["x", "y#z"]);
        assert_eq!(markdown_links("[n](multi\nline)"), ["multi\nline"]);
        assert_eq!(python_splitlines("a\nb\x0cc\n"), ["a", "b", "c"]);
        assert_eq!(python_splitlines("\n"), [""]);
        assert!(python_splitlines("").is_empty());
        assert_eq!(python_rstrip("x \t\u{a0}\x1f"), "x");
    }
}
