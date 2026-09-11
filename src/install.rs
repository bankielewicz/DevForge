//! Manual-only installation of the promoted Codex expert workflows.
//!
//! Compiled Rust owns evidence validation, the acceptance predicates and every
//! installation write for this path; no Python is consulted. Digests bind the
//! declared evidence, and the running executable must match the owner-selected
//! authority record before any authority-bearing action. The operator still
//! owns acceptance and observation truth: a matching digest proves which bytes
//! were referenced, never that behavior is correct. No candidate code runs.
use crate::delivery::StrictJson;
use anyhow::{Context, Result, anyhow, bail, ensure};
use clap::Subcommand;
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::Read;
use std::os::unix::fs::MetadataExt;
use std::os::unix::process::ExitStatusExt;
use std::path::{Component, Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, LazyLock};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

/// Content identity of every build input, computed by `build.rs`.
pub const SOURCE_SHA256: &str = env!("DEVFORGE_SOURCE_SHA256");
const NAMES: [&str; 2] = [
    "devforge-evaluate-expert",
    "devforge-project-expert-creator",
];
const CREATOR_PHASES: [&str; 5] = [
    "Intake",
    "Selection",
    "Design",
    "Authoring",
    "PreparedTransfer",
];
const TASKS: [&str; 12] = [
    "T01", "T02", "T03", "T04", "T05", "T06", "T07", "T08", "T09", "T10", "T11", "T12",
];
const NATIVE_TASKS: [&str; 4] = ["T05", "T06", "T07", "T08"];
const LOCAL_CHECKS: [(&str, &str); 9] = [
    ("package_integrity", "D"),
    ("installed_resources", "D"),
    ("independent_semantics", "S"),
    ("grounded_creation", "N"),
    ("reuse", "N"),
    ("bounded_enhancement", "N"),
    ("missing_evidence_refusal", "N"),
    ("creator_to_evaluator", "N"),
    ("evaluator_to_creator", "N"),
];
const RECEIVING: [&str; 2] = ["creator_to_evaluator", "evaluator_to_creator"];
const MAX_BYTES: u64 = 32 * 1024 * 1024;
const AUTHORITY_LIMIT: u64 = 1024 * 1024;
const INVENTORY: &str = ".devforge-install.json";
const SKILL_ROOT: &str = ".agents/skills";
const INSTALL_ROOTS: [&str; 3] = [".agents", ".codex", ".claude"];
const AUTHORITY_SCHEMA: &str = "devforge.manual-install-authority/v1";
const LOCAL_BASELINE_V1: &str = "devforge.manual-expert-local-baseline/v1";
/// The `v1` record plus a `test_destination` selected for the actual installation.
const LOCAL_BASELINE_V2: &str = "devforge.manual-expert-local-baseline/v2";
/// The report `probe-runtime` returns and `guard-validator` requires as its evidence.
const PROBE_SCHEMA: &str = "devforge.runtime-probe/v1";
const GUARD_REQUEST_SCHEMA: &str = "devforge.validator-guard-request/v1";
const GUARD_SCHEMA: &str = "devforge.validator-guard/v1";
static EMPTY_OBJECT: LazyLock<Value> = LazyLock::new(|| Value::Object(Map::new()));
static EMPTY_LIST: LazyLock<Value> = LazyLock::new(|| Value::Array(Vec::new()));

#[derive(Subcommand)]
pub enum Action {
    /// Report this executable's digest and embedded source identity for authority pinning.
    Identity,
    /// Install only the promoted Codex expert workflows from owner-selected adoption evidence.
    ManualExperts {
        /// DevForgeAI checkout containing providers/codex/plugins/devforgeai.
        #[arg(long)]
        framework: PathBuf,
        /// Exact owner-selected adoption or local-baseline record.
        #[arg(long)]
        evidence: PathBuf,
        /// Owner-controlled record pinning this executable and its source identity.
        #[arg(long)]
        authority: PathBuf,
    },
    /// Check whether preserved local-adoption evidence is mechanically consumable by
    /// `manual-experts`, through the same readers, without installing or mutating anything.
    CheckLocalEvidence {
        /// DevForgeAI checkout containing providers/codex/plugins/devforgeai.
        #[arg(long)]
        framework: PathBuf,
        /// Preflight packet naming the selected set, packages, allocations and observations.
        #[arg(long)]
        packet: PathBuf,
        /// Owner-controlled record pinning this executable and its source identity.
        #[arg(long)]
        authority: PathBuf,
    },
    /// Probe an explicitly selected runtime executable and validate the delivery
    /// capabilities it reports. Reads and executes only that runtime; writes nothing.
    /// The global --project, when given, binds the report to that installation and
    /// requires this validating executable to be outside it.
    ProbeRuntime {
        /// Absolute, canonical, single-hard-link runtime executable to probe.
        #[arg(long)]
        runtime: PathBuf,
        /// Provider the installed package requires; repeat for each, codex or claude.
        #[arg(long)]
        provider: Vec<String>,
        // The global `--project` optionally names the installation this probe admits;
        // the validating executable must then be outside that project.
    },
    /// Decide, immediately before the installation writes, whether the planned
    /// destinations may proceed against this validating executable: none may name
    /// it, none may already alias its inode, and its own bytes must still be the
    /// ones its probe report bound. Reads one `devforge.validator-guard-request/v1`
    /// object from stdin, requires the global --project, and writes nothing.
    GuardValidator,
}

pub fn run(action: &Action, project: Option<&Path>) -> Result<Value> {
    match action {
        Action::Identity => identity(),
        Action::ProbeRuntime { runtime, provider } => probe_runtime(runtime, provider, project),
        Action::GuardValidator => guard_validator(project.context("--project is required")?),
        Action::ManualExperts {
            framework,
            evidence,
            authority,
        } => manual_experts(
            project.context("--project is required")?,
            framework,
            evidence,
            authority,
        ),
        Action::CheckLocalEvidence {
            framework,
            packet,
            authority,
        } => check_local_evidence(
            project.context("--project is required")?,
            framework,
            packet,
            authority,
        ),
    }
}

// ---- evidence predicates -------------------------------------------------

fn refuse(reason: &str) -> anyhow::Error {
    anyhow!("manual expert evidence: {reason}")
}
fn require(condition: bool, reason: &str) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(refuse(reason))
    }
}
fn malformed() -> anyhow::Error {
    refuse("malformed nested record")
}
fn obj(value: &Value) -> Result<&Map<String, Value>> {
    value.as_object().ok_or_else(malformed)
}
fn list(value: &Value) -> Result<&Vec<Value>> {
    value.as_array().ok_or_else(malformed)
}
/// `dict.get(key)`: the container must be an object; a missing key is null.
fn get<'a>(value: &'a Value, key: &str) -> Result<&'a Value> {
    Ok(obj(value)?.get(key).unwrap_or(&Value::Null))
}
/// `dict.get(key, {})`.
fn sub<'a>(value: &'a Value, key: &str) -> Result<&'a Value> {
    Ok(obj(value)?.get(key).unwrap_or(&EMPTY_OBJECT))
}
/// `dict.get(key, [])`.
fn seq<'a>(value: &'a Value, key: &str) -> Result<&'a Value> {
    Ok(obj(value)?.get(key).unwrap_or(&EMPTY_LIST))
}
/// `value[key]`.
fn index<'a>(value: &'a Value, key: &str) -> Result<&'a Value> {
    obj(value)?.get(key).ok_or_else(malformed)
}
fn is(value: &Value, expected: &str) -> bool {
    value.as_str() == Some(expected)
}
fn exact<'a>(value: &'a Value, fields: &[&str], name: &str) -> Result<&'a Map<String, Value>> {
    value
        .as_object()
        .filter(|map| map.len() == fields.len() && fields.iter().all(|f| map.contains_key(*f)))
        .ok_or_else(|| refuse(&format!("invalid {name}")))
}
fn text<'a>(value: &'a Value, name: &str) -> Result<&'a str> {
    match value.as_str() {
        Some(s) if !s.trim().is_empty() && !s.contains("{{") => Ok(s),
        _ => Err(refuse(&format!("missing {name}"))),
    }
}
fn is_hex64(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn unique(values: &[&Value]) -> bool {
    values
        .iter()
        .enumerate()
        .all(|(i, a)| values[..i].iter().all(|b| a != b))
}
fn set_eq(a: &[&Value], b: &[&Value]) -> bool {
    a.iter().all(|x| b.contains(x)) && b.iter().all(|x| a.contains(x))
}
fn key_set(rows: &Value, key: &str, expected: &[String]) -> Result<bool> {
    let mut actual = Vec::new();
    for row in list(rows)? {
        actual.push(get(row, key)?);
    }
    let expected: Vec<Value> = expected.iter().map(|s| json!(s)).collect();
    Ok(set_eq(&actual, &expected.iter().collect::<Vec<_>>()))
}
fn labels(prefix: &str, count: usize) -> Vec<String> {
    (1..=count).map(|i| format!("{prefix}{i:02}")).collect()
}
fn falsy(value: &Value) -> bool {
    match value {
        Value::Null => true,
        Value::Bool(b) => !b,
        Value::Number(n) => n.as_f64() == Some(0.0),
        Value::String(s) => s.is_empty(),
        Value::Array(a) => a.is_empty(),
        Value::Object(o) => o.is_empty(),
    }
}
fn is_int(value: &Value) -> bool {
    value.as_i64().is_some() || value.as_u64().is_some()
}

fn strict_json(raw: &[u8]) -> std::result::Result<Value, String> {
    serde_json::from_slice::<StrictJson>(raw)
        .map(|StrictJson(value)| value)
        .map_err(|error| {
            let message = error.to_string();
            if message.contains("duplicate JSON key") || message.contains("nonfinite JSON") {
                message
            } else {
                "invalid JSON".to_string()
            }
        })
}

fn decode(raw: &[u8]) -> Result<Value> {
    require(raw.len() as u64 <= MAX_BYTES, "record exceeds size limit")?;
    let value = strict_json(raw).map_err(|reason| refuse(&reason))?;
    require(value.is_object(), "record must be an object")?;
    Ok(value)
}

fn canonical(path: &Path) -> Result<bool> {
    if path
        .components()
        .any(|c| matches!(c, Component::CurDir | Component::ParentDir))
    {
        return Ok(false);
    }
    match fs::canonicalize(path) {
        Ok(real) => Ok(real == path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(true),
        Err(error) => Err(error.into()),
    }
}

fn source_files(root: &Path) -> Result<BTreeMap<String, String>> {
    fn visit(root: &Path, dir: &Path, files: &mut BTreeMap<String, String>) -> Result<()> {
        let Ok(entries) = fs::read_dir(dir) else {
            return Ok(());
        };
        for entry in entries {
            let path = entry?.path();
            let meta = fs::symlink_metadata(&path)?;
            require(
                !meta.file_type().is_symlink(),
                "source identity contains symlink",
            )?;
            if meta.is_dir() {
                visit(root, &path, files)?;
            } else if meta.is_file() {
                let relative = path.strip_prefix(root)?;
                if relative
                    .components()
                    .any(|c| c.as_os_str() == "__pycache__")
                    || relative.extension().is_some_and(|e| e == "pyc")
                {
                    continue;
                }
                let name = relative.to_str().context("non-UTF8 source path")?;
                files.insert(name.to_string(), crate::hash(&fs::read(&path)?));
            }
        }
        Ok(())
    }
    let mut files = BTreeMap::new();
    if fs::symlink_metadata(root).is_ok_and(|m| m.is_dir()) {
        visit(root, root, &mut files)?;
    }
    Ok(files)
}

/// Microseconds since the Unix epoch for a timezone-aware ISO 8601 timestamp.
fn time(value: &Value) -> Result<i128> {
    let invalid = || refuse("invalid timestamp");
    let text = text(value, "timestamp")
        .map_err(|_| invalid())?
        .replace('Z', "+00:00");
    parse_iso(&text).ok_or_else(invalid)
}

fn is_leap(year: i64) -> bool {
    (year % 4 == 0 && year % 100 != 0) || year % 400 == 0
}

/// Days since 1970-01-01 for a valid proleptic Gregorian calendar date.
fn civil_days(year: i64, month: i64, day: i64) -> Option<i64> {
    let leap = is_leap(year);
    let days_in_month = match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if leap => 29,
        2 => 28,
        _ => return None,
    };
    if !(1..=days_in_month).contains(&day) {
        return None;
    }
    let (y, m) = if month <= 2 {
        (year - 1, month + 9)
    } else {
        (year, month - 3)
    };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let doy = (153 * m + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    Some(era * 146_097 + doe - 719_468)
}

/// The timestamp contract the legacy guard accepted through
/// `datetime.fromisoformat` (Python 3.12) with `Z` mapped to `+00:00`:
/// calendar or ISO week dates in extended or basic form (week 53 only in a
/// long ISO year), any single separator character, `HH[:MM[:SS]]` or
/// `HH[MM[SS]]` with an optional fraction of any length after the last
/// component (truncated to microseconds), and a mandatory offset in the same
/// `HH[[:]MM[[:]SS]][.frac]` grammar totalling under 24 hours. Offset minute
/// and second fields above 59 are summed as the legacy parser summed them,
/// and an offset fraction counts only when the whole offset is non-zero
/// (`+00:00:00.5` and `+00:00.5` are UTC), which is also legacy behavior.
fn parse_iso(text: &str) -> Option<i128> {
    fn digits(bytes: &[u8], start: usize, len: usize) -> Option<i64> {
        let slice = bytes.get(start..start + len)?;
        if !slice.iter().all(u8::is_ascii_digit) {
            return None;
        }
        std::str::from_utf8(slice).ok()?.parse().ok()
    }
    /// Consume fraction digits after a `.`/`,`; microseconds, truncated.
    fn fraction(bytes: &[u8], pos: &mut usize, allow_empty: bool) -> Option<i64> {
        let start = *pos;
        while bytes.get(*pos).is_some_and(u8::is_ascii_digit) {
            *pos += 1;
        }
        if *pos == start && !allow_empty {
            return None;
        }
        let mut padded = format!("{:0<6}", std::str::from_utf8(&bytes[start..*pos]).ok()?);
        padded.truncate(6);
        padded.parse().ok()
    }
    let bytes = text.as_bytes();
    let year = digits(bytes, 0, 4)?;
    let mut pos = 4;
    let extended = bytes.get(pos) == Some(&b'-');
    if extended {
        pos += 1;
    }
    let days = if bytes.get(pos) == Some(&b'W') {
        pos += 1;
        let week = digits(bytes, pos, 2)?;
        pos += 2;
        let mut weekday = 1;
        let has_day = if extended {
            bytes.get(pos) == Some(&b'-')
        } else {
            bytes.get(pos).is_some_and(u8::is_ascii_digit)
        };
        if has_day {
            if extended {
                pos += 1;
            }
            weekday = digits(bytes, pos, 1)?;
            pos += 1;
        }
        if !(1..=53).contains(&week) || !(1..=7).contains(&weekday) {
            return None;
        }
        // 1970-01-01 was a Thursday (Monday = 0). Week 53 exists only when
        // January 1 is a Thursday, or a Wednesday in a leap year.
        let jan1_weekday = (civil_days(year, 1, 1)? + 3).rem_euclid(7);
        if week == 53 && !(jan1_weekday == 3 || (jan1_weekday == 2 && is_leap(year))) {
            return None;
        }
        // ISO week 1 contains January 4.
        let jan4 = civil_days(year, 1, 4)?;
        let monday = jan4 - (jan4 + 3).rem_euclid(7);
        monday + (week - 1) * 7 + (weekday - 1)
    } else {
        let month = digits(bytes, pos, 2)?;
        pos += 2;
        if extended {
            if bytes.get(pos) != Some(&b'-') {
                return None;
            }
            pos += 1;
        }
        let day = digits(bytes, pos, 2)?;
        pos += 2;
        civil_days(year, month, day)?
    };
    // Any single separator character precedes the time.
    pos += text.get(pos..)?.chars().next()?.len_utf8();
    let hour = digits(bytes, pos, 2)?;
    pos += 2;
    let mut minute = 0;
    let mut second = 0;
    let mut micro = 0;
    let colon = bytes.get(pos) == Some(&b':');
    if colon || bytes.get(pos).is_some_and(u8::is_ascii_digit) {
        if colon {
            pos += 1;
        }
        minute = digits(bytes, pos, 2)?;
        pos += 2;
        let has_seconds = if colon {
            bytes.get(pos) == Some(&b':')
        } else {
            bytes.get(pos).is_some_and(u8::is_ascii_digit)
        };
        if has_seconds {
            if colon {
                pos += 1;
            }
            second = digits(bytes, pos, 2)?;
            pos += 2;
        }
    }
    // The legacy parser took a fraction after whichever component came last.
    if matches!(bytes.get(pos), Some(b'.' | b',')) {
        pos += 1;
        micro = fraction(bytes, &mut pos, true)?;
    }
    let sign = match bytes.get(pos) {
        Some(b'+') => 1,
        Some(b'-') => -1,
        _ => return None,
    };
    pos += 1;
    // The offset uses the time grammar: HH[[:]MM[[:]SS]] and a fraction after
    // the last component, which here must not be empty.
    let offset_hours = digits(bytes, pos, 2)?;
    pos += 2;
    let mut offset_minutes = 0;
    let mut offset_seconds = 0;
    let mut offset_micro = 0;
    let colon = bytes.get(pos) == Some(&b':');
    if colon || bytes.get(pos).is_some_and(u8::is_ascii_digit) {
        if colon {
            pos += 1;
        }
        offset_minutes = digits(bytes, pos, 2)?;
        pos += 2;
        let has_seconds = if colon {
            bytes.get(pos) == Some(&b':')
        } else {
            bytes.get(pos).is_some_and(u8::is_ascii_digit)
        };
        if has_seconds {
            if colon {
                pos += 1;
            }
            offset_seconds = digits(bytes, pos, 2)?;
            pos += 2;
        }
    }
    if matches!(bytes.get(pos), Some(b'.' | b',')) {
        pos += 1;
        offset_micro = fraction(bytes, &mut pos, false)?;
    }
    if pos != bytes.len() || hour >= 24 || minute >= 60 || second >= 60 {
        return None;
    }
    // The legacy parser applied the fraction only to a non-zero whole offset;
    // an all-zero whole offset is UTC whatever its fraction.
    let whole = offset_hours * 3_600 + offset_minutes * 60 + offset_seconds;
    let offset = if whole == 0 {
        0
    } else {
        i128::from(whole) * 1_000_000 + i128::from(offset_micro)
    };
    if offset >= 86_400 * 1_000_000 {
        return None;
    }
    let local = i128::from(days * 86_400 + hour * 3_600 + minute * 60 + second) * 1_000_000
        + i128::from(micro);
    Some(local - i128::from(sign) * offset)
}

struct Evidence {
    project: PathBuf,
    framework: PathBuf,
    pins: BTreeMap<PathBuf, String>,
    source_trees: BTreeMap<PathBuf, BTreeMap<String, String>>,
}

impl Evidence {
    fn pin(&mut self, reference: &Value, authority: bool) -> Result<Vec<u8>> {
        let reference = exact(reference, &["path", "sha256"], "evidence pin")?;
        let path = PathBuf::from(text(&reference["path"], "pin path")?);
        require(
            path.is_absolute() && canonical(&path)?,
            "noncanonical pin path",
        )?;
        if authority {
            require(
                !INSTALL_ROOTS
                    .iter()
                    .any(|root| path.starts_with(self.project.join(root)))
                    && !path.starts_with(&self.framework),
                "authority evidence must be outside candidate and installation roots",
            )?;
        }
        let expected = reference["sha256"]
            .as_str()
            .filter(|s| is_hex64(s))
            .ok_or_else(|| refuse("invalid digest"))?;
        require(
            fs::metadata(&path).is_ok_and(|m| m.is_file() && m.len() <= MAX_BYTES),
            "missing or oversized evidence",
        )?;
        let raw = fs::read(&path)?;
        require(
            crate::hash(&raw) == expected,
            &format!("missing/stale evidence: {}", path.display()),
        )?;
        require(
            self.pins.get(&path).is_none_or(|sha| sha == expected),
            "conflicting pin",
        )?;
        self.pins.insert(path, expected.to_string());
        Ok(raw)
    }
    fn document(&mut self, reference: &Value, authority: bool) -> Result<Value> {
        decode(&self.pin(reference, authority)?)
    }
    fn walk(&mut self, value: &Value) -> Result<()> {
        match value {
            Value::Object(map) => {
                if map.contains_key("path") && map.contains_key("sha256") {
                    self.pin(
                        &json!({"path": map["path"], "sha256": map["sha256"]}),
                        false,
                    )?;
                } else {
                    for child in map.values() {
                        self.walk(child)?;
                    }
                }
            }
            Value::Array(items) => {
                for child in items {
                    self.walk(child)?;
                }
            }
            _ => {}
        }
        Ok(())
    }
    fn refs(&mut self, values: &Value) -> Result<()> {
        require(
            values.as_array().is_some_and(|v| !v.is_empty()),
            "required obligation has no evidence",
        )?;
        for reference in list(values)? {
            self.pin(reference, false)?;
        }
        Ok(())
    }
    fn recheck(&mut self) -> Result<()> {
        for (root, files) in &self.source_trees {
            require(
                source_files(root)? == *files,
                "source identity changed before install",
            )?;
        }
        for (path, sha) in self.pins.clone() {
            self.pin(&json!({"path": path, "sha256": sha}), false)?;
        }
        Ok(())
    }
}

fn selected_packages(
    planned: &BTreeMap<String, Vec<u8>>,
) -> Result<BTreeMap<String, BTreeMap<String, String>>> {
    let mut result = BTreeMap::new();
    for name in NAMES {
        let prefix = format!("{SKILL_ROOT}/{name}/");
        let files: BTreeMap<String, String> = planned
            .iter()
            .filter(|(path, _)| path.starts_with(&prefix))
            .map(|(path, data)| (path[prefix.len()..].to_string(), crate::hash(data)))
            .collect();
        if !files.is_empty() {
            require(
                files.contains_key("SKILL.md"),
                "recognized package has no entrypoint",
            )?;
            result.insert(name.to_string(), files);
        }
    }
    Ok(result)
}

struct Obligation {
    tier: Option<String>,
    kinds: Value,
}

fn native(tier: Option<&str>) -> bool {
    tier.is_some_and(|t| ["C", "B", "A"].contains(&t))
}

/// Bind the projection to every original case assertion, including excluded ones.
fn catalog_coverage(
    cases_ref: &Value,
    policy: &Value,
    evidence: &mut Evidence,
) -> Result<BTreeMap<String, Obligation>> {
    let refs = seq(policy, "catalog_refs")?;
    require(
        refs.as_array().is_some_and(|r| r.contains(cases_ref)),
        "missing original case catalog",
    )?;
    let refs = list(refs)?;
    let projection = seq(policy, "catalog_assertions")?;
    require(
        projection.as_array().is_some_and(|p| !p.is_empty()),
        "missing catalog assertion projection",
    )?;
    let projection = list(projection)?;
    let mut known = BTreeSet::new();
    let mut obligations = BTreeMap::new();
    for reference in refs {
        let raw = evidence.pin(reference, false)?;
        let path = index(reference, "path")?.as_str().unwrap_or_default();
        if reference != cases_ref && !path.ends_with(".json") {
            continue; // Additional specification anchors are reviewed separately.
        }
        let catalog = decode(&raw)?;
        let key = if obj(&catalog)?.contains_key("cases") {
            "cases"
        } else {
            "evals"
        };
        let cases = get(&catalog, key)?;
        require(
            cases.as_array().is_some_and(|c| !c.is_empty()),
            "unsupported/empty case catalog",
        )?;
        let mut case_ids = BTreeSet::new();
        for (position, case) in list(cases)?.iter().enumerate() {
            require(case.is_object(), "invalid catalog case")?;
            let case_id = text(get(case, "id")?, "case ID")?;
            require(
                case_ids.insert(case_id.to_string()),
                "duplicate catalog case",
            )?;
            let fields: Vec<&str> = [
                "required_observations",
                "expectations",
                "frozen_discriminator",
            ]
            .into_iter()
            .filter(|field| obj(case).is_ok_and(|c| c.contains_key(*field)))
            .collect();
            require(
                fields.len() == 1,
                "case needs one supported assertion list/discriminator",
            )?;
            let field = fields[0];
            let value = index(case, field)?;
            let assertions: Vec<&Value> = if field == "frozen_discriminator" {
                vec![value]
            } else {
                require(
                    value.as_array().is_some_and(|a| !a.is_empty()),
                    "empty case assertions",
                )?;
                list(value)?.iter().collect()
            };
            let tier = get(case, "tier")?.as_str().map(str::to_string);
            for (number, assertion) in assertions.iter().enumerate() {
                text(assertion, "original assertion")?;
                let pointer = if field == "frozen_discriminator" {
                    format!("/{key}/{position}/{field}")
                } else {
                    format!("/{key}/{position}/{field}/{number}")
                };
                let mut matches = Vec::new();
                for row in projection {
                    if get(row, "source_ref")? == reference
                        && is(get(row, "source_pointer")?, &pointer)
                        && is(get(row, "case_id")?, case_id)
                    {
                        matches.push(row);
                    }
                }
                require(
                    !matches.is_empty(),
                    &format!("catalog assertion omitted: {case_id}{pointer}"),
                )?;
                for row in matches {
                    let kinds = seq(row, "evidence_kinds")?;
                    if native(tier.as_deref()) {
                        require(
                            kinds.as_array().is_some_and(|k| k.contains(&json!("N"))),
                            "native catalog evidence kind weakened",
                        )?;
                    }
                    if let Some(id) = get(row, "assertion_id")?.as_str() {
                        obligations.insert(
                            id.to_string(),
                            Obligation {
                                tier: tier.clone(),
                                kinds: kinds.clone(),
                            },
                        );
                        known.insert(id.to_string());
                    }
                }
            }
        }
    }
    let mut ids = Vec::new();
    for row in projection {
        ids.push(get(row, "assertion_id")?);
    }
    require(
        ids.iter()
            .all(|v| v.as_str().is_some_and(|s| !s.is_empty()))
            && unique(&ids),
        "invalid/duplicate catalog projection",
    )?;
    let mut selected = Vec::new();
    for row in list(seq(policy, "assertions")?)? {
        selected.push(get(row, "assertion_id")?);
    }
    require(
        set_eq(&selected, &ids),
        "catalog/selection inventory differs",
    )?;
    // Every additional (for example specification) projection resolves its pinned anchor.
    for row in projection {
        let source_ref = get(row, "source_ref")?;
        require(refs.contains(source_ref), "unknown catalog source")?;
        let id = index(row, "assertion_id")?.as_str().unwrap_or_default();
        if !known.contains(id) {
            let raw = evidence.pin(source_ref, false)?;
            let path = index(source_ref, "path")?.as_str().unwrap_or_default();
            let anchor = text(get(row, "source_pointer")?, "source anchor")?;
            require(
                !path.ends_with(".json")
                    && std::str::from_utf8(&raw).is_ok_and(|t| t.contains(anchor)),
                "unknown catalog assertion pointer",
            )?;
            obligations.insert(
                id.to_string(),
                Obligation {
                    tier: None,
                    kinds: seq(row, "evidence_kinds")?.clone(),
                },
            );
        }
    }
    Ok(obligations)
}

struct Validated {
    summary: Value,
    evidence: Evidence,
}

fn validate(
    evidence_path: &Path,
    planned: &BTreeMap<String, Vec<u8>>,
    project: &Path,
    framework: &Path,
) -> Result<Validated> {
    let packages = selected_packages(planned)?;
    require(!packages.is_empty(), "no promoted Codex package selected")?;
    let mut evidence = Evidence {
        project: project.to_path_buf(),
        framework: framework.to_path_buf(),
        pins: BTreeMap::new(),
        source_trees: BTreeMap::new(),
    };
    let raw = fs::read(evidence_path).with_context(|| {
        format!(
            "manual expert evidence: cannot read {}",
            evidence_path.display()
        )
    })?;
    let root_pin = json!({"path": evidence_path, "sha256": crate::hash(&raw)});
    let record = evidence.document(&root_pin, true)?;
    let schema = get(&record, "schema_version")?;
    if is(schema, LOCAL_BASELINE_V1) || is(schema, LOCAL_BASELINE_V2) {
        return local_baseline(&record, &root_pin, &packages, evidence);
    }
    exact(
        &record,
        &[
            "schema_version",
            "project_root",
            "owner",
            "authorization",
            "packages",
        ],
        "adoption record",
    )?;
    require(
        is(
            &record["schema_version"],
            "devforge.manual-expert-adoption/v1",
        ),
        "unsupported adoption version",
    )?;
    require(
        record["project_root"] == json!(project),
        "wrong installation destination",
    )?;
    let owner = text(&record["owner"], "integration owner")?.to_string();
    evidence.pin(&record["authorization"], true)?;
    require(record["packages"].is_array(), "packages must be an array")?;
    let keys = [
        "name",
        "manifest",
        "specification",
        "plan",
        "cases",
        "creator",
        "results",
        "decision",
        "review",
        "acceptance",
    ];
    let mut indexed: BTreeMap<String, &Value> = BTreeMap::new();
    for package in list(&record["packages"])? {
        exact(package, &keys, "package adoption")?;
        let name = package["name"].as_str();
        require(
            name.is_some_and(|n| !indexed.contains_key(n)),
            "duplicate package",
        )?;
        indexed.insert(name.unwrap_or_default().to_string(), package);
    }
    require(
        indexed.keys().eq(packages.keys()),
        "adoption package selection differs from planned installation",
    )?;
    for (name, package) in &indexed {
        let mut docs: BTreeMap<&str, Value> = BTreeMap::new();
        for key in [
            "manifest",
            "plan",
            "creator",
            "results",
            "decision",
            "review",
            "acceptance",
        ] {
            docs.insert(
                key,
                evidence.document(&package[key], key == "review" || key == "acceptance")?,
            );
        }
        for document in docs.values() {
            evidence.walk(document)?;
        }
        evidence.pin(&package["specification"], false)?;
        evidence.pin(&package["cases"], false)?;
        let manifest = &docs["manifest"];
        exact(
            manifest,
            &["schema_version", "name", "files_sha256"],
            "runtime manifest",
        )?;
        require(
            is(
                &manifest["schema_version"],
                "devforge.expert-runtime-manifest/v1",
            ) && is(&manifest["name"], name)
                && manifest["files_sha256"] == json!(packages[name]),
            "candidate manifest differs from planned bytes",
        )?;
        let creator = &docs["creator"];
        exact(
            creator,
            &[
                "schema_version",
                "author",
                "candidate",
                "specification",
                "phases",
            ],
            "creator completion",
        )?;
        require(
            is(
                &creator["schema_version"],
                "devforge.expert-creator-completion/v1",
            ) && creator["candidate"] == package["manifest"]
                && creator["specification"] == package["specification"],
            "creator identity mismatch",
        )?;
        let author = text(&creator["author"], "candidate author")?;
        require(
            creator["phases"].as_object().is_some_and(|p| {
                p.len() == CREATOR_PHASES.len() && CREATOR_PHASES.iter().all(|k| p.contains_key(*k))
            }),
            "all five creator phases are required",
        )?;
        for phase in obj(&creator["phases"])?.values() {
            exact(phase, &["classification", "evidence"], "creator phase")?;
            require(
                is(&phase["classification"], "Enforced"),
                "creator classification changed",
            )?;
            evidence.refs(&phase["evidence"])?;
        }
        let (plan, results, decision, review) = (
            &docs["plan"],
            &docs["results"],
            &docs["decision"],
            &docs["review"],
        );
        require(
            is(
                get(plan, "schema_version")?,
                "devforge.skill-validation-plan/v2",
            ),
            "unsupported evaluation plan",
        )?;
        let policy = sub(plan, "validation_policy")?;
        let mode = get(policy, "mode")?
            .as_str()
            .unwrap_or_default()
            .to_string();
        require(
            is(get(policy, "version")?, "VPR-2") && (mode == "Routine" || mode == "Full"),
            "unknown validation policy",
        )?;
        require(
            get(sub(policy, "candidate_identity")?, "candidate")? == &package["manifest"],
            "plan candidate mismatch",
        )?;
        require(
            get(review, "candidate_ref")? == &package["manifest"],
            "review candidate mismatch",
        )?;
        for kind in ["specification", "cases"] {
            let expected = json!({"kind": kind, "path": package[kind]["path"], "sha256": package[kind]["sha256"]});
            require(
                seq(plan, "input_refs")?
                    .as_array()
                    .is_some_and(|refs| refs.contains(&expected)),
                &format!("plan input mismatch: {kind}"),
            )?;
        }
        require(
            is(
                get(results, "schema_version")?,
                "devforge.skill-validation-results/v2",
            ) && is(
                get(decision, "schema_version")?,
                "devforge.skill-validation-decision/v2",
            ) && get(results, "plan")? == &package["plan"]
                && get(decision, "plan")? == &package["plan"]
                && get(decision, "results")? == &package["results"],
            "evaluation binding mismatch",
        )?;
        let run_id = get(results, "run_id")?;
        require(
            run_id == get(plan, "run_id")?
                && run_id == get(decision, "run_id")?
                && run_id == get(review, "run_id")?,
            "run identity mismatch",
        )?;
        let evaluator = text(get(sub(plan, "assignment")?, "owner")?, "evaluator")?;
        require(
            evaluator != author,
            "creator cannot evaluate its own target",
        )?;
        require(
            is(
                get(review, "schema_version")?,
                "devforge.skill-ai-review/v2",
            ) && get(review, "plan")? == &package["plan"]
                && get(results, "ai_review")? == &package["review"]
                && get(decision, "ai_review")? == &package["review"],
            "independent review binding mismatch",
        )?;
        let reviewer = sub(review, "reviewer")?;
        let identity = text(get(reviewer, "identity")?, "reviewer")?;
        require(
            identity != author
                && identity != evaluator
                && is(get(policy, "selection_reviewer")?, identity),
            "reviewer is not separately assigned",
        )?;
        text(
            get(reviewer, "independence_evidence")?,
            "observed review independence",
        )?;
        require(
            is(get(review, "overall")?, "PASS")
                && is(get(sub(review, "selection_review")?, "outcome")?, "PASS"),
            "independent selection review did not pass",
        )?;
        let criteria = seq(review, "criteria")?;
        require(
            criteria.as_array().is_some_and(|c| c.len() == 10)
                && key_set(criteria, "id", &labels("R", 10))?,
            "review invariant coverage incomplete",
        )?;
        for criterion in list(criteria)? {
            let outcome = get(criterion, "outcome")?;
            require(
                is(outcome, "PASS") || is(outcome, "NOT_APPLICABLE"),
                "required semantic review incomplete/failed",
            )?;
            text(get(criterion, "reason")?, "review reason")?;
            evidence.refs(get(criterion, "evidence")?)?;
        }
        let disposition = format!("{}_PASS", mode.to_uppercase());
        require(
            is(get(decision, "validation_disposition")?, &disposition)
                && is(get(decision, "overall")?, "PASS")
                && get(decision, "coverage_complete")? == &Value::Bool(true)
                && is(get(decision, "external_acceptance")?, "NOT_GRANTED"),
            "required evaluation is incomplete/failed",
        )?;
        require(
            get(results, "validation_disposition")? == get(decision, "validation_disposition")?,
            "result/decision disposition differs",
        )?;
        let impact = sub(policy, "impact")?;
        if mode == "Routine" {
            routine(policy, impact, decision, &mut evidence)?;
        }
        let lineage = get(policy, "lineage")?;
        require(
            get(decision, "lineage")? == get(results, "lineage")?
                && get(results, "lineage")? == lineage
                && lineage.is_object(),
            "lineage differs/missing",
        )?;
        let obligations = catalog_coverage(&package["cases"], policy, &mut evidence)?;
        let selected = seq(policy, "assertions")?;
        require(
            selected.as_array().is_some_and(|s| !s.is_empty()),
            "missing assertion selection",
        )?;
        let selected = list(selected)?;
        let mut selected_ids = Vec::new();
        for row in selected {
            selected_ids.push(get(row, "assertion_id")?);
        }
        require(unique(&selected_ids), "duplicate selected assertion")?;
        let assertions = seq(results, "assertion_results")?;
        let mut assertion_ids = Vec::new();
        for row in assertions.as_array().into_iter().flatten() {
            assertion_ids.push(get(row, "assertion_id")?);
        }
        require(
            assertions
                .as_array()
                .is_some_and(|r| r.len() == selected_ids.len())
                && set_eq(&assertion_ids, &selected_ids)
                && get(decision, "assertion_results")? == assertions,
            "assertion results differ/missing",
        )?;
        let reviewed = list(seq(
            sub(review, "selection_review")?,
            "reviewed_assertion_ids",
        )?)?;
        require(
            reviewed.len() == selected_ids.len()
                && set_eq(&reviewed.iter().collect::<Vec<_>>(), &selected_ids),
            "review omits assertion selection",
        )?;
        let checks = seq(decision, "checks")?;
        let mut check_ids = Vec::new();
        for row in checks.as_array().into_iter().flatten() {
            check_ids.push(get(row, "check_id")?);
        }
        require(
            checks
                .as_array()
                .is_some_and(|c| c.len() == selected_ids.len())
                && set_eq(&check_ids, &selected_ids),
            "decision coverage differs",
        )?;
        let assertion_rows = list(assertions)?;
        let check_rows = list(checks)?;
        for selection in selected {
            let id = index(selection, "assertion_id")?;
            let obligation = id
                .as_str()
                .and_then(|s| obligations.get(s))
                .ok_or_else(malformed)?;
            let kinds = obligation.kinds.as_array().filter(|k| {
                !k.is_empty() && k.iter().all(|x| is(x, "D") || is(x, "S") || is(x, "N"))
            });
            let kinds = kinds.ok_or_else(|| refuse("invalid catalog evidence obligation"))?;
            let tier = get(selection, "tier")?;
            if kinds.contains(&json!("N")) {
                require(
                    native(tier.as_str())
                        && (!native(obligation.tier.as_deref())
                            || tier.as_str() == obligation.tier.as_deref()),
                    "native catalog tier/obligation changed",
                )?;
            }
            let result = assertion_rows
                .iter()
                .rev()
                .find(|row| get(row, "assertion_id").ok() == Some(id))
                .ok_or_else(malformed)?;
            require(
                get(result, "selection")? == get(selection, "selection")?,
                "post-hoc assertion exclusion",
            )?;
            let chosen = get(selection, "selection")?;
            if is(chosen, "REQUIRED") {
                let observation = is(get(selection, "expectation")?, "observation");
                let outcome = get(result, "outcome")?;
                require(
                    is(get(result, "integrity")?, "INTACT")
                        && (is(outcome, "PASS") || (observation && is(outcome, "FAIL"))),
                    "required assertion incomplete/failed",
                )?;
                evidence.refs(get(result, "observation_refs")?)?;
                let check = check_rows
                    .iter()
                    .find(|row| get(row, "check_id").ok() == Some(id))
                    .ok_or_else(malformed)?;
                require(
                    is(get(check, "effective_outcome")?, "PASS"),
                    "required decision check did not pass",
                )?;
                if kinds.iter().any(|k| is(k, "S") || is(k, "N"))
                    || tier
                        .as_str()
                        .is_some_and(|t| ["S", "C", "B", "A"].contains(&t))
                {
                    evidence.refs(get(result, "grade_refs")?)?;
                }
            } else {
                let outcome = get(result, "outcome")?;
                require(
                    ((is(chosen, "NOT_SELECTED") && is(outcome, "NOT_RUN"))
                        || (is(chosen, "NOT_APPLICABLE") && is(outcome, "NOT_APPLICABLE")))
                        && (mode == "Routine" || is(chosen, "NOT_APPLICABLE")),
                    "invalid assertion exclusion",
                )?;
            }
        }
        let tasks = seq(results, "task_results")?;
        require(
            task_order(tasks)?,
            "all six evaluator phases and twelve tasks are required",
        )?;
        require(
            get(decision, "task_results")? == tasks,
            "task decision differs from results",
        )?;
        let selections = seq(policy, "task_selection")?;
        require(task_order(selections)?, "plan task coverage differs")?;
        for (task, selection) in list(tasks)?.iter().zip(list(selections)?) {
            require(
                is(get(selection, "classification")?, "Enforced")
                    && get(selection, "selection")? == get(task, "selection")?,
                "task selection/classification changed",
            )?;
            require(
                is(get(task, "classification")?, "Enforced"),
                "evaluator classification changed",
            )?;
            let chosen = get(task, "selection")?;
            if is(chosen, "REQUIRED") {
                require(
                    is(get(task, "disposition")?, "SATISFIED") && is(get(task, "outcome")?, "PASS"),
                    "required task incomplete/failed",
                )?;
            } else {
                let task_id = index(task, "task_id")?;
                let outcome = get(task, "outcome")?;
                require(
                    NATIVE_TASKS.iter().any(|t| is(task_id, t))
                        && is(get(task, "disposition")?, "SATISFIED_BY_REVIEWED_SELECTION")
                        && ((is(chosen, "NOT_APPLICABLE") && is(outcome, "NOT_APPLICABLE"))
                            || (is(chosen, "NOT_SELECTED") && is(outcome, "NOT_RUN")))
                        && (mode == "Routine" || is(chosen, "NOT_APPLICABLE")),
                    "unreviewed task exclusion",
                )?;
            }
            evidence.refs(get(task, "evidence")?)?;
        }
        if mode == "Full" {
            let mut tiers = BTreeSet::new();
            for row in selected {
                if is(get(row, "selection")?, "REQUIRED") {
                    if let Some(tier) = get(row, "tier")?.as_str() {
                        tiers.insert(tier.to_string());
                    }
                }
            }
            require(
                ["D", "S", "C", "B", "A"].iter().all(|t| tiers.contains(*t)),
                "Full catalog must retain applicable deterministic, semantic and native tiers",
            )?;
            for task in list(tasks)? {
                let task_id = index(task, "task_id")?;
                if NATIVE_TASKS.iter().any(|t| is(task_id, t)) {
                    require(
                        is(get(task, "selection")?, "REQUIRED"),
                        "Full cannot exclude every native obligation",
                    )?;
                }
            }
            let transfer = sub(results, "receiving_transfer")?;
            require(
                is(get(transfer, "selection")?, "REQUIRED")
                    && is(get(transfer, "outcome")?, "PASS"),
                "Full receiving transfer unavailable",
            )?;
            for field in [
                "target_output",
                "receiver_contract",
                "receiver_observation",
                "completed_action",
            ] {
                evidence.pin(get(transfer, field)?, false)?;
            }
            text(get(transfer, "observed_at_utc")?, "actual transfer time")?;
        }
        let mut inputs = Map::new();
        for key in keys {
            if key != "name" && key != "acceptance" {
                inputs.insert(key.to_string(), package[key].clone());
            }
        }
        let expected = json!({
            "schema_version": "devforge.expert-install-acceptance/v1",
            "owner": owner,
            "project_root": project,
            "package": name,
            "action": "install",
            "inputs": inputs,
            "observation_basis": "operator-reviewed actual evidence",
        });
        require(
            docs["acceptance"] == expected,
            "missing exact owner acceptance and observation attestation",
        )?;
    }
    evidence.recheck()?;
    Ok(Validated {
        summary: json!({
            "record": root_pin,
            "owner": owner,
            "packages": packages.keys().collect::<Vec<_>>(),
            "predicate": "manual-expert-adoption/v1",
        }),
        evidence,
    })
}

fn task_order(rows: &Value) -> Result<bool> {
    let Some(rows) = rows.as_array() else {
        return Ok(false);
    };
    if rows.len() != TASKS.len() {
        return Ok(false);
    }
    for (row, expected) in rows.iter().zip(TASKS) {
        if !is(get(row, "task_id")?, expected) {
            return Ok(false);
        }
    }
    Ok(true)
}

fn routine(
    policy: &Value,
    impact: &Value,
    decision: &Value,
    evidence: &mut Evidence,
) -> Result<()> {
    let lineage = sub(policy, "lineage")?;
    let baseline = get(policy, "baseline_identity")?;
    require(
        baseline.is_object() && baseline == get(lineage, "current_routinely_accepted")?,
        "Routine requires an accepted baseline",
    )?;
    let scope = get(policy, "accepted_scope_ref")?;
    evidence.pin(scope, false)?;
    let chain = seq(lineage, "acceptance_chain")?;
    let previous_ref = get(lineage, "previous_acceptance")?;
    require(
        chain
            .as_array()
            .is_some_and(|c| c.last() == Some(previous_ref)),
        "Routine baseline acceptance chain missing",
    )?;
    let chain = list(chain)?;
    let previous = evidence.document(index(lineage, "previous_acceptance")?, false)?;
    require(
        get(&previous, "candidate_identity")? == baseline
            && get(&previous, "accepted_scope_ref")? == scope,
        "Routine baseline acceptance differs",
    )?;
    let old = sub(&previous, "lineage")?;
    require(
        get(old, "qualified_anchor")? == get(lineage, "qualified_anchor")?
            && get(old, "accepted_unqualified_baseline")?
                == get(lineage, "accepted_unqualified_baseline")?
            && get(old, "acceptance_chain")? == &Value::Array(chain[..chain.len() - 1].to_vec()),
        "Routine cumulative anchor/chain changed",
    )?;
    let anchor = sub(lineage, "qualified_anchor")?;
    let status = get(anchor, "status")?;
    let qualified = is(status, "QUALIFIED");
    require(
        qualified || is(status, "ABSENT") || is(status, "UNKNOWN"),
        "Routine anchor status missing",
    )?;
    require(
        qualified || get(lineage, "accepted_unqualified_baseline")?.is_object(),
        "Routine cumulative baseline missing",
    )?;
    require(
        qualified || (get(anchor, "identity")?.is_null() && get(anchor, "evidence")?.is_null()),
        "Routine invented qualification",
    )?;
    for field in ["immediate_diff", "cumulative_diff"] {
        evidence.pin(get(impact, field)?, false)?;
    }
    let compatibility = seq(policy, "compatibility")?;
    require(
        compatibility.as_array().is_some_and(|c| c.len() == 4)
            && key_set(compatibility, "id", &labels("CP-", 4))?,
        "Routine compatibility coverage missing",
    )?;
    for row in list(compatibility)? {
        let disposition = get(row, "disposition")?;
        require(
            is(disposition, "UNCHANGED")
                || is(disposition, "EVIDENCED")
                || is(disposition, "NOT_APPLICABLE"),
            "Routine compatibility unresolved",
        )?;
        text(get(row, "reason")?, "compatibility reason")?;
        evidence.refs(get(row, "evidence")?)?;
    }
    let matched = list(seq(impact, "matched_rules")?)?;
    require(
        get(impact, "bounded")? == &Value::Bool(true)
            && falsy(get(impact, "full_triggers")?)
            && !matched
                .iter()
                .any(|r| is(r, "CI-05") || is(r, "CI-06") || is(r, "CI-09"))
            && get(sub(policy, "requested_claim")?, "requires_full")? == &Value::Bool(false)
            && get(decision, "routine_adoption_eligible")? == &Value::Bool(true),
        "consequential/unbounded or ineligible Routine adoption",
    )?;
    Ok(())
}

const RESULTS_V1: &str = "devforge.manual-local-acceptance-results/v1";
const RESULTS_V2: &str = "devforge.manual-local-acceptance-results/v2";
const OBSERVATION_V1: &str = "devforge.manual-local-observation/v1";
const OBSERVATION_V2: &str = "devforge.manual-local-observation/v2";
const ALLOCATION_V1: &str = "devforge.manual-local-allocation/v1";
const ALLOCATION_V2: &str = "devforge.manual-local-allocation/v2";
const JUDGMENT_V1: &str = "devforge.manual-local-attempt-judgment/v1";
/// A preserved prior allocation whose recorded passing attempts may be reused.
struct Allocation {
    id: String,
    /// Ledger rows whose outcome is a separately recorded subsequent judgment.
    judged: usize,
    /// The reference's normalized status, equal to the closeout's normalized status.
    status: String,
    /// The preserved closeout's raw status, as written.
    closeout_status: String,
    start: i128,
    deadline: i128,
    /// The earliest `started_at_utc` across every ledger row, failed, timed-out,
    /// unjudged and unselected ones included; `None` when the ledger is empty.
    /// It is a minimum over the complete attempt record, never the ledger's first
    /// element or its first passing row.
    first_attempt: Option<i128>,
    /// The window as the reference wrote it, for reporting.
    started_at_utc: Value,
    deadline_utc: Value,
    max_attempts: i64,
    attempts: BTreeMap<String, Value>,
    used: bool,
}

/// The `{path, sha256}` pair of a pin-like object that may carry extra keys.
fn pin_of(value: &Value) -> Result<Value> {
    let map = obj(value)?;
    require(
        map.contains_key("path") && map.contains_key("sha256"),
        "invalid evidence pin",
    )?;
    Ok(json!({"path": map["path"], "sha256": map["sha256"]}))
}

/// What a preserved closeout says about its allocation, read through one of
/// the explicitly supported historical shapes. Raw fields stay as written;
/// only this reader normalizes them.
struct Closeout {
    status: &'static str,
    start: i128,
    deadline: i128,
    attempts: Value,
    /// Per-slot states when the closeout records them (`slot_states`).
    slots: Option<BTreeMap<String, Value>>,
    /// Native attempt facts the closeout records, each of which must match a ledger row.
    natives: Vec<Native>,
    /// The natives enumerate every attempt, so every ledger row must be named by exactly one.
    complete: bool,
    /// The approval the closeout pins when the preserved allocation records none.
    authorization: Option<Value>,
}

struct Native {
    role: Option<String>,
    actor: Option<String>,
    start: Option<i128>,
    end: Option<i128>,
    /// `Some(true)` for a recorded passing workflow judgment, `Some(false)` for any
    /// other recorded judgment, `None` when the closeout records no judgment.
    passed: Option<bool>,
    /// A `PASS` ledger outcome needs a recorded passing judgment; a process exit is not one.
    judgment_required: bool,
    launch: Option<Value>,
    completion: Option<Value>,
    transcript: Option<Value>,
    /// Completion facts the closeout records inline, compared with the pinned completion.
    returncode: Option<Value>,
    timed_out: Option<Value>,
}

fn optional_pin(value: &Value) -> Result<Option<Value>> {
    if value.is_null() {
        Ok(None)
    } else {
        pin_of(value).map(Some)
    }
}

fn optional_time(value: &Value) -> Result<Option<i128>> {
    if value.is_null() {
        Ok(None)
    } else {
        time(value).map(Some)
    }
}

fn closeout_facts(closeout: &Value, original: &Value) -> Result<Closeout> {
    let status = get(closeout, "status")?.as_str().unwrap_or_default();
    match status {
        "EXPIRED_WITH_PARTIAL_OBSERVATIONS" => {
            let mut slots = None;
            if let Some(states) = get(closeout, "slot_states")?.as_array() {
                let mut map = BTreeMap::new();
                for state in states {
                    let slot = text(get(state, "slot")?, "closeout slot")?.to_string();
                    map.insert(slot, state.clone());
                }
                slots = Some(map);
            }
            Ok(Closeout {
                status: "EXHAUSTED",
                start: time(get(closeout, "original_started_at_utc")?)?,
                deadline: time(get(closeout, "original_deadline_utc")?)?,
                attempts: get(closeout, "native_attempts")?.clone(),
                slots,
                natives: Vec::new(),
                complete: false,
                authorization: None,
            })
        }
        "COMPLETED_BOUNDED_EVALUATOR_RETURN" => {
            require(
                pin_of(get(closeout, "allocation")?)? == *original,
                "closeout is for a different allocation",
            )?;
            Ok(Closeout {
                status: "COMPLETED",
                start: time(get(closeout, "started_at_utc")?)?,
                deadline: time(get(closeout, "deadline_utc")?)?,
                attempts: get(closeout, "native_turns")?.clone(),
                slots: None,
                natives: vec![Native {
                    role: None,
                    actor: Some(
                        text(get(closeout, "native_actor")?, "closeout native actor")?.to_string(),
                    ),
                    start: None,
                    end: None,
                    passed: None,
                    judgment_required: false,
                    launch: Some(pin_of(get(closeout, "launch")?)?),
                    completion: Some(pin_of(get(closeout, "completion")?)?),
                    transcript: Some(pin_of(get(closeout, "transcript")?)?),
                    returncode: None,
                    timed_out: None,
                }],
                complete: false,
                authorization: None,
            })
        }
        "STOPPED_BLOCKED" => {
            require(
                get(closeout, "clock_restarted")? == &Value::Bool(false),
                "preserved closeout reports a restarted clock",
            )?;
            let attempts = get(closeout, "attempts_used")?.clone();
            let start = time(get(closeout, "started_at_utc")?)?;
            let deadline = time(get(closeout, "original_deadline_utc")?)?;
            if let Some(entries) = get(closeout, "native")?.as_array() {
                // The multi-attempt recording (2026-09-10 replacement closeout): one
                // native record per attempt with its own pins and an inline completion,
                // the allocation and approval pinned by the closeout, no `preserved` block,
                // and a workflow judgment only where one was actually made.
                require(
                    !entries.is_empty() || attempts == json!(0),
                    "stopped closeout has attempts but no native evidence",
                )?;
                require(
                    attempts == json!(entries.len()),
                    "stopped closeout native records differ from its attempt count",
                )?;
                require(
                    pin_of(get(closeout, "allocation")?)? == *original,
                    "closeout is for a different allocation",
                )?;
                let mut natives = Vec::new();
                let mut roles = BTreeSet::new();
                for entry in entries {
                    let role = text(get(entry, "role")?, "closeout native role")?;
                    require(
                        roles.insert(role.to_string()),
                        "duplicate closeout native record",
                    )?;
                    let end = optional_time(get(entry, "finished_at_utc")?)?;
                    let inline = get(entry, "native_completion")?;
                    let (mut returncode, mut timed_out) = (None, None);
                    if !inline.is_null() {
                        require(
                            optional_time(get(inline, "finished_at_utc")?)? == end,
                            "closeout native completion differs from its record",
                        )?;
                        returncode = Some(get(inline, "returncode")?.clone());
                        timed_out = Some(get(inline, "timed_out")?.clone());
                    }
                    natives.push(Native {
                        role: Some(role.to_string()),
                        actor: get(entry, "actor")?.as_str().map(str::to_string),
                        start: optional_time(get(entry, "started_at_utc")?)?,
                        end,
                        passed: get(entry, "workflow_outcome")?
                            .as_str()
                            .map(|outcome| outcome == "PASS"),
                        judgment_required: true,
                        launch: optional_pin(get(entry, "launch")?)?,
                        completion: optional_pin(get(entry, "completion")?)?,
                        transcript: optional_pin(get(entry, "transcript")?)?,
                        returncode,
                        timed_out,
                    });
                }
                return Ok(Closeout {
                    status: "STOPPED_BLOCKED",
                    start,
                    deadline,
                    attempts,
                    slots: None,
                    natives,
                    complete: true,
                    authorization: optional_pin(get(closeout, "authorization")?)?,
                });
            }
            let native = sub(closeout, "native")?;
            let preserved = sub(closeout, "preserved")?;
            require(
                !obj(native)?.is_empty() || attempts == json!(0),
                "stopped closeout has attempts but no native evidence",
            )?;
            let natives = if obj(native)?.is_empty() {
                Vec::new()
            } else {
                vec![Native {
                    role: get(native, "role")?.as_str().map(str::to_string),
                    actor: get(native, "actor")?.as_str().map(str::to_string),
                    start: optional_time(get(native, "started_at_utc")?)?,
                    end: optional_time(get(native, "finished_at_utc")?)?,
                    passed: get(native, "workflow_outcome")?
                        .as_str()
                        .map(|outcome| outcome == "PASS"),
                    judgment_required: false,
                    launch: optional_pin(get(preserved, "launch")?)?,
                    completion: optional_pin(get(preserved, "completion")?)?,
                    transcript: optional_pin(get(preserved, "transcript")?)?,
                    returncode: None,
                    timed_out: None,
                }]
            };
            Ok(Closeout {
                status: "STOPPED_BLOCKED",
                start,
                deadline,
                attempts,
                slots: None,
                natives,
                complete: false,
                authorization: None,
            })
        }
        other => Err(refuse(&format!(
            "unsupported preserved closeout format: status {other:?}; supported: EXPIRED_WITH_PARTIAL_OBSERVATIONS, COMPLETED_BOUNDED_EVALUATOR_RETURN, STOPPED_BLOCKED"
        ))),
    }
}

/// Validate one `devforge.manual-local-allocation/v1` reference against the
/// current record's owner, frozen set and candidate identity, and against the
/// preserved records it points at: the original allocation (window, cap, slots,
/// set and approval linkage), each attempt's launch and completion records, and
/// the closeout. Every attempt, including failed ones, stays in its ledger and
/// must agree with those sources. Nothing here re-executes, re-grades or
/// re-counts, and hashing an approval binds its bytes, not its meaning.
fn allocation(
    reference: &Value,
    record: &Value,
    owner: &str,
    frozen: i128,
    identities: &Value,
    authors: &BTreeSet<String>,
    evidence: &mut Evidence,
) -> Result<Allocation> {
    let loaded = bind_allocation(reference, record, owner, identities, authors, evidence)?;
    frozen_before_first_attempt(frozen, &loaded)?;
    Ok(loaded)
}

/// The owner-approved local MVP timing rule of 2026-09-10: "Preparation may begin
/// before freezing. Requirements, cases and selected skill files must be fixed
/// before the first native attempt in each allocation." The first native attempt is
/// the earliest recorded start across the allocation's complete ledger, so a failed,
/// timed-out or unjudged attempt fixes the instant just as a selected passing one
/// does. The comparison is strict and by normalized instant: a set frozen at the
/// same instant as the first attempt did not precede it. An allocation with no
/// recorded attempt has no instant to compare, so no timing verdict is produced;
/// it also supplies no native observation, because an observation must name a
/// recorded attempt. This applies only to the local, explicitly UNQUALIFIED path;
/// the original start, deadline, cap, counters and recorded failures are unchanged,
/// and Full/Routine qualification policy is untouched. Both `allocation` (the
/// installer) and the evidence preflight apply this one function so they cannot
/// drift.
fn frozen_before_first_attempt(frozen: i128, allocation: &Allocation) -> Result<()> {
    match allocation.first_attempt {
        Some(first) => require(
            frozen < first,
            "acceptance set must be frozen before the allocation's first native attempt",
        ),
        None => Ok(()),
    }
}

/// Bind one allocation reference to its preserved sources: everything
/// `allocation` checks except the frozen-set chronology, which the caller
/// applies. The preflight uses this to report the binding and that rule
/// separately without weakening either. A `v2` reference may bind a
/// separately recorded subsequent judgment to a ledger row (see
/// `attempt_judgment`); `authors` are the package authors that judgment's
/// reviewer must differ from.
fn bind_allocation(
    reference: &Value,
    record: &Value,
    owner: &str,
    identities: &Value,
    authors: &BTreeSet<String>,
    evidence: &mut Evidence,
) -> Result<Allocation> {
    let doc = evidence.document(reference, true)?;
    exact(
        &doc,
        &[
            "schema_version",
            "allocation_id",
            "owner",
            "authorization",
            "acceptance_set",
            "packages",
            "started_at_utc",
            "deadline_utc",
            "max_attempts",
            "attempts",
            "status",
            "original_allocation",
            "closeout",
        ],
        "allocation record",
    )?;
    let judgments = is(&doc["schema_version"], ALLOCATION_V2);
    require(
        judgments || is(&doc["schema_version"], ALLOCATION_V1),
        "unsupported allocation record",
    )?;
    let id = text(&doc["allocation_id"], "allocation ID")?.to_string();
    require(is(&doc["owner"], owner), "allocation owner differs")?;
    evidence.pin(&doc["authorization"], true)?;
    require(
        doc["acceptance_set"] == record["acceptance_set"],
        "allocation bound to a different acceptance set",
    )?;
    require(
        doc["packages"] == *identities,
        "allocation candidate identity differs",
    )?;
    let start = time(&doc["started_at_utc"])?;
    let deadline = time(&doc["deadline_utc"])?;
    require(start < deadline, "inverted allocation window")?;
    let max_attempts = doc["max_attempts"].as_i64().unwrap_or_default();
    require(
        is_int(&doc["max_attempts"]) && max_attempts > 0,
        "unbounded allocation",
    )?;
    // The preserved original allocation supplies the window, cap and slots the
    // reference claims, and links the approval and frozen set.
    let original = evidence.document(&doc["original_allocation"], false)?;
    require(
        time(get(&original, "started_at_utc")?)? == start
            && time(get(&original, "deadline_utc")?)? == deadline,
        "reference window differs from the preserved allocation",
    )?;
    require(
        get(&original, "child_turns")? == &doc["max_attempts"],
        "reference attempt limit differs from the preserved allocation",
    )?;
    let mut slot_ids = BTreeSet::new();
    for slot in list(get(&original, "slots")?)? {
        slot_ids.insert(text(get(slot, "id")?, "preserved slot ID")?.to_string());
    }
    let linked_set = match get(&original, "acceptance_set")? {
        Value::Null => {
            let prior = get(&original, "prior_allocation")?;
            require(
                !prior.is_null(),
                "preserved allocation records no acceptance set; supply its acceptance_set or prior_allocation pin",
            )?;
            let prior = evidence.document(&pin_of(prior)?, false)?;
            get(&prior, "acceptance_set")?.clone()
        }
        set => set.clone(),
    };
    require(
        !linked_set.is_null() && pin_of(&linked_set)? == doc["acceptance_set"],
        "preserved allocation is bound to a different acceptance set",
    )?;
    evidence.walk(&original)?;
    let rows = list(&doc["attempts"])?;
    require(
        i64::try_from(rows.len()).is_ok_and(|n| n <= max_attempts),
        "allocation attempt limit exceeded",
    )?;
    let closeout = evidence.document(&doc["closeout"], false)?;
    let facts = closeout_facts(&closeout, &doc["original_allocation"])?;
    // The approval is linked through whichever recorded form the preserved
    // allocation used: an `authorization` pin, an `authorization` text (then the
    // reference pins the allocation record itself), an `approval_ref` pin, or a
    // pin carried by its closeout.
    match get(&original, "authorization")? {
        Value::String(_) => require(
            doc["authorization"] == doc["original_allocation"],
            "text authorization must be pinned through the preserved allocation record",
        )?,
        Value::Object(_) => require(
            pin_of(get(&original, "authorization")?)? == doc["authorization"],
            "reference authorization differs from the preserved allocation",
        )?,
        Value::Null if get(&original, "approval_ref")?.is_object() => require(
            pin_of(get(&original, "approval_ref")?)? == doc["authorization"],
            "reference authorization differs from the preserved allocation",
        )?,
        Value::Null if facts.authorization.is_some() => require(
            facts.authorization.as_ref() == Some(&doc["authorization"]),
            "reference authorization differs from the preserved closeout",
        )?,
        _ => return Err(refuse("preserved allocation records no authorization")),
    }
    require(
        is(&doc["status"], facts.status),
        "reference status differs from the preserved closeout",
    )?;
    require(
        facts.start == start && facts.deadline == deadline,
        "preserved closeout window differs from the reference",
    )?;
    require(
        facts.attempts == json!(rows.len()),
        "allocation closeout differs from its ledger",
    )?;
    evidence.walk(&closeout)?;
    let mut matched = vec![false; facts.natives.len()];
    let mut attempts = BTreeMap::new();
    let mut judged = 0;
    // The earliest start over the whole ledger, accumulated as each row is read so
    // that no row can be skipped: failed, timed-out, unjudged and unselected rows
    // all contribute.
    let mut first_attempt: Option<i128> = None;
    let mut row_fields = vec![
        "attempt_id",
        "actor",
        "started_at_utc",
        "finished_at_utc",
        "outcome",
        "launch",
        "completion",
        "evidence",
    ];
    if judgments {
        row_fields.push("judgment");
    }
    for row in rows {
        exact(row, &row_fields, "allocation attempt")?;
        let attempt_id = text(&row["attempt_id"], "attempt ID")?.to_string();
        require(
            slot_ids.contains(&attempt_id),
            "attempt is not a slot of the preserved allocation",
        )?;
        text(&row["actor"], "attempt actor")?;
        let passed = is(&row["outcome"], "PASS");
        text(&row["outcome"], "attempt outcome")?;
        let from = time(&row["started_at_utc"])?;
        let to = time(&row["finished_at_utc"])?;
        require(
            start <= from && from <= to && to <= deadline,
            "allocation attempt outside its approved window",
        )?;
        first_attempt = Some(first_attempt.map_or(from, |earliest: i128| earliest.min(from)));
        evidence.refs(&row["evidence"])?;
        let launch = evidence.document(&row["launch"], false)?;
        require(
            is(get(sub(&launch, "slot")?, "id")?, &attempt_id)
                && time(get(&launch, "started_at_utc")?)? == from,
            "launch record differs from the attempt",
        )?;
        let completion = evidence.document(&row["completion"], false)?;
        require(
            time(get(&completion, "finished_at_utc")?)? == to,
            "completion record differs from the attempt",
        )?;
        let completed = get(&completion, "returncode")? == &json!(0)
            && get(&completion, "timed_out")? == &Value::Bool(false);
        require(
            !passed || completed,
            "completion record contradicts the PASS outcome",
        )?;
        // A separately recorded later judgment of this attempt's saved outputs. It
        // is validated after the execution evidence so that a failed process or an
        // adverse recorded judgment is never overridden by a new label.
        let judgment = judgments && !row["judgment"].is_null();
        if judgment {
            attempt_judgment(
                &row["judgment"],
                row,
                &doc,
                owner,
                identities,
                authors,
                (from, to),
                evidence,
            )?;
            judged += 1;
        }
        if let Some(slots) = &facts.slots {
            let state = slots
                .get(&attempt_id)
                .ok_or_else(|| refuse("closeout lists no state for the attempt's slot"))?;
            let recorded = get(state, "completion")?;
            require(
                get(state, "launched")? == &Value::Bool(true)
                    && recorded.is_object()
                    && get(recorded, "returncode")? == get(&completion, "returncode")?
                    && get(recorded, "timed_out")? == get(&completion, "timed_out")?
                    && time(get(recorded, "finished_at_utc")?)? == to,
                "closeout slot state differs from the attempt",
            )?;
        }
        let mut named_by = 0;
        for (native, seen) in facts.natives.iter().zip(matched.iter_mut()) {
            let named = native.role.as_deref() == Some(attempt_id.as_str())
                || native.launch.as_ref() == Some(&row["launch"])
                || (native.role.is_none()
                    && native.launch.is_none()
                    && native.actor.as_deref() == row["actor"].as_str()
                    && native.start == Some(from)
                    && native.end == Some(to));
            if !named {
                continue;
            }
            require(
                native
                    .actor
                    .as_deref()
                    .is_none_or(|actor| is(&row["actor"], actor))
                    && native.start.is_none_or(|s| s == from)
                    && native.end.is_none_or(|e| e == to)
                    && native
                        .completion
                        .as_ref()
                        .is_none_or(|c| *c == row["completion"])
                    && native
                        .returncode
                        .as_ref()
                        .is_none_or(|r| r == &completion["returncode"])
                    && native
                        .timed_out
                        .as_ref()
                        .is_none_or(|t| t == &completion["timed_out"]),
                "closeout native record differs from the attempt",
            )?;
            require(
                native.passed.is_none_or(|p| p || !passed),
                "preserved closeout records the attempt as not passed",
            )?;
            require(
                !(native.judgment_required && passed && native.passed.is_none() && !judgment),
                "preserved closeout records no workflow judgment for the attempt; a PASS outcome cannot be inferred from its process exit",
            )?;
            if let Some(transcript) = &native.transcript {
                require(
                    list(&row["evidence"])?.contains(transcript),
                    "closeout transcript is not among the attempt evidence",
                )?;
            }
            *seen = true;
            named_by += 1;
        }
        require(
            !facts.complete || named_by == 1,
            "closeout native records must name the attempt exactly once",
        )?;
        require(
            attempts.insert(attempt_id, row.clone()).is_none(),
            "duplicate allocation attempt",
        )?;
    }
    require(
        matched.iter().all(|seen| *seen),
        "closeout native record does not match any ledger attempt",
    )?;
    Ok(Allocation {
        id,
        judged,
        status: facts.status.to_string(),
        closeout_status: get(&closeout, "status")?
            .as_str()
            .unwrap_or_default()
            .to_string(),
        start,
        deadline,
        first_attempt,
        started_at_utc: doc["started_at_utc"].clone(),
        deadline_utc: doc["deadline_utc"].clone(),
        max_attempts,
        attempts,
        used: false,
    })
}

/// Validate a `devforge.manual-local-attempt-judgment/v1` record: a later
/// independent judgment of one preserved attempt's saved outputs, recorded
/// separately from the closeout that truthfully says no judgment existed. It
/// binds the frozen requirements, the candidate identity, the preserved
/// allocation and closeout, the exact attempt (ID, actor, instants, launch,
/// completion) and the attempt's recorded raw evidence; the ledger outcome must
/// be the judgment's outcome. It never pins the reference, results or review,
/// so no record digests itself. Independence is checked as distinct names plus
/// a pinned independence record; actual context independence remains observed
/// evidence, not a string guarantee.
#[allow(clippy::too_many_arguments)]
fn attempt_judgment(
    reference: &Value,
    row: &Value,
    doc: &Value,
    owner: &str,
    identities: &Value,
    authors: &BTreeSet<String>,
    (from, to): (i128, i128),
    evidence: &mut Evidence,
) -> Result<()> {
    let judgment = evidence.document(reference, true)?;
    exact(
        &judgment,
        &[
            "schema_version",
            "reviewer",
            "independence_evidence",
            "acceptance_set",
            "packages",
            "original_allocation",
            "closeout",
            "attempt_id",
            "actor",
            "started_at_utc",
            "finished_at_utc",
            "launch",
            "completion",
            "evidence",
            "outcome",
            "reason",
            "judged_at_utc",
        ],
        "attempt judgment",
    )?;
    require(
        is(&judgment["schema_version"], JUDGMENT_V1),
        "unsupported attempt judgment",
    )?;
    require(
        judgment["acceptance_set"] == doc["acceptance_set"],
        "attempt judgment bound to a different acceptance set",
    )?;
    require(
        judgment["packages"] == *identities,
        "attempt judgment candidate identity differs",
    )?;
    require(
        judgment["original_allocation"] == doc["original_allocation"]
            && judgment["closeout"] == doc["closeout"],
        "attempt judgment is for a different allocation",
    )?;
    require(
        judgment["attempt_id"] == row["attempt_id"],
        "attempt judgment is for a different attempt",
    )?;
    require(
        judgment["actor"] == row["actor"]
            && time(&judgment["started_at_utc"])? == from
            && time(&judgment["finished_at_utc"])? == to
            && judgment["launch"] == row["launch"]
            && judgment["completion"] == row["completion"],
        "attempt judgment differs from the recorded attempt",
    )?;
    let listed = list(&judgment["evidence"])?;
    require(
        list(&row["evidence"])?.iter().all(|e| listed.contains(e)),
        "attempt judgment omits the attempt's recorded evidence",
    )?;
    evidence.refs(&judgment["evidence"])?;
    text(&judgment["outcome"], "judgment outcome")?;
    require(
        row["outcome"] == judgment["outcome"],
        "attempt outcome differs from its subsequent judgment",
    )?;
    text(&judgment["reason"], "judgment reason")?;
    require(
        time(&judgment["judged_at_utc"])? > to,
        "attempt judgment precedes the attempt it judges",
    )?;
    let reviewer = text(&judgment["reviewer"], "judgment reviewer")?;
    require(
        !is(&row["actor"], reviewer) && reviewer != owner && !authors.contains(reviewer),
        "attempt judgment reviewer is not independent of the attempt actor, package authors or owner",
    )?;
    evidence.pin(&judgment["independence_evidence"], false)?;
    evidence.walk(&judgment)?;
    Ok(())
}

/// A separately authorized local claim; never a Routine or Full decision.
fn local_baseline(
    record: &Value,
    root_pin: &Value,
    packages: &BTreeMap<String, BTreeMap<String, String>>,
    mut evidence: Evidence,
) -> Result<Validated> {
    let redirected = is(&record["schema_version"], LOCAL_BASELINE_V2);
    let mut fields = vec![
        "schema_version",
        "project_root",
        "owner",
        "authorization",
        "packages",
        "acceptance_set",
        "results",
        "review",
        "acceptance",
        "historical_evidence",
    ];
    if redirected {
        fields.push("test_destination");
    }
    exact(record, &fields, "local baseline record")?;
    let historical = bind_destination(record, redirected, &evidence)?;
    let owner = text(&record["owner"], "local owner")?.to_string();
    evidence.pin(&record["authorization"], true)?;
    evidence.refs(&record["historical_evidence"])?;
    let LocalPackages {
        authors,
        catalogs,
        identities,
    } = local_packages(&record["packages"], packages, &mut evidence)?;
    let (plan, frozen) = local_set(record, &mut evidence)?;
    let results = evidence.document(&record["results"], false)?;
    // v2 results may reuse observations preserved from earlier approved allocations.
    let reuse = is(&results["schema_version"], RESULTS_V2);
    let mut fields = vec![
        "schema_version",
        "acceptance_set",
        "qualification_status",
        "checks",
        "qualification_cases",
        "started_at_utc",
        "finished_at_utc",
        "native_turns",
    ];
    if reuse {
        fields.extend(["allocations", "reused_observations"]);
    }
    exact(&results, &fields, "local results")?;
    require(
        (reuse || is(&results["schema_version"], RESULTS_V1))
            && results["acceptance_set"] == record["acceptance_set"]
            && is(&results["qualification_status"], "UNQUALIFIED"),
        "local result/claim mismatch",
    )?;
    require(
        results["qualification_cases"] == Value::Object(catalogs),
        "qualification cases must remain complete and NOT_RUN",
    )?;
    let start = time(&results["started_at_utc"])?;
    let end = time(&results["finished_at_utc"])?;
    require(
        frozen < start && start <= end,
        "acceptance set must be predefined",
    )?;
    require(
        (end - start) <= i128::from(plan["max_seconds"].as_i64().unwrap_or_default()) * 1_000_000,
        "local time allowance exceeded",
    )?;
    let turns = &results["native_turns"];
    require(
        is_int(turns)
            && turns.as_i64().is_some_and(|t| {
                t > 0 && t <= plan["max_native_turns"].as_i64().unwrap_or_default()
            }),
        "native turn allowance exceeded",
    )?;
    require(
        results["checks"].as_object().is_some_and(|c| {
            c.len() == LOCAL_CHECKS.len() && LOCAL_CHECKS.iter().all(|(k, _)| c.contains_key(*k))
        }),
        "local acceptance check coverage differs",
    )?;
    let mut observations = BTreeSet::new();
    let mut reused = BTreeSet::new();
    let mut actors = BTreeSet::new();
    let mut allocations: BTreeMap<String, Allocation> = BTreeMap::new();
    if reuse {
        for reference in list(&results["allocations"])? {
            let key = text(index(reference, "path")?, "allocation path")?.to_string();
            let loaded = allocation(
                reference,
                record,
                &owner,
                frozen,
                &identities,
                &authors,
                &mut evidence,
            )?;
            require(
                allocations.insert(key, loaded).is_none(),
                "duplicate allocation reference",
            )?;
        }
    }
    for (key, kind) in LOCAL_CHECKS {
        let result = exact(
            &results["checks"][key],
            &["outcome", "evidence", "native_observation"],
            "acceptance check",
        )?;
        require(
            is(&result["outcome"], "PASS"),
            "required acceptance check did not pass",
        )?;
        evidence.refs(&result["evidence"])?;
        if kind != "N" {
            require(
                result["native_observation"].is_null(),
                "deterministic/semantic check is not native",
            )?;
            continue;
        }
        require(
            result["evidence"]
                .as_array()
                .is_some_and(|e| e.contains(&result["native_observation"])),
            "native observation not bound to check",
        )?;
        let path = index(&result["native_observation"], "path")?
            .as_str()
            .unwrap_or_default()
            .to_string();
        let observed = local_observation(
            key,
            &result["native_observation"],
            record,
            &identities,
            reuse,
            Some((start, end)),
            &mut allocations,
            &mut evidence,
        )?;
        actors.insert(observed.actor);
        if observed.prior {
            reused.insert(path);
        } else {
            observations.insert(path);
        }
    }
    require(
        i64::try_from(observations.len()).is_ok_and(|n| n <= turns.as_i64().unwrap_or_default()),
        "native turn count omits observations",
    )?;
    if reuse {
        require(
            results["reused_observations"] == json!(reused.len()),
            "reused observation count differs",
        )?;
        require(
            allocations.values().all(|a| a.used),
            "listed allocation supplied no reused observation",
        )?;
    }
    let review = evidence.document(&record["review"], true)?;
    exact(
        &review,
        &[
            "schema_version",
            "reviewer",
            "independence_evidence",
            "overall",
            "criteria",
            "acceptance_set",
            "packages",
            "check_judgments",
        ],
        "local independent review",
    )?;
    let reviewer = text(&review["reviewer"], "reviewer")?;
    require(
        is(&review["schema_version"], "devforge.manual-local-review/v1")
            && review["acceptance_set"] == record["acceptance_set"]
            && review["packages"] == record["packages"]
            && !authors.contains(reviewer)
            && !actors.contains(reviewer)
            && reviewer != owner,
        "local independent reviewer mismatch",
    )?;
    evidence.pin(&review["independence_evidence"], false)?;
    require(
        is(&review["overall"], "PASS")
            && review["criteria"].as_object().is_some_and(|c| {
                c.len() == 10 && labels("R", 10).iter().all(|k| c.contains_key(k))
            })
            && review["check_judgments"].as_object().is_some_and(|c| {
                c.len() == LOCAL_CHECKS.len()
                    && LOCAL_CHECKS.iter().all(|(k, _)| c.contains_key(*k))
            }),
        "local semantic review coverage incomplete",
    )?;
    for judgment in obj(&review["criteria"])?
        .values()
        .chain(obj(&review["check_judgments"])?.values())
    {
        exact(
            judgment,
            &["outcome", "reason", "evidence"],
            "independent judgment",
        )?;
        require(
            is(&judgment["outcome"], "PASS"),
            "local independent judgment did not pass",
        )?;
        text(&judgment["reason"], "judgment reason")?;
        evidence.refs(&judgment["evidence"])?;
    }
    for (key, judgment) in obj(&review["check_judgments"])? {
        let expected = list(&results["checks"][key]["evidence"])?;
        let actual = list(&judgment["evidence"])?;
        require(
            expected.iter().all(|reference| actual.contains(reference)),
            "review omits actual acceptance evidence",
        )?;
    }
    let acceptance = evidence.document(&record["acceptance"], true)?;
    let mut inputs = obj(record)?.clone();
    inputs.remove("acceptance");
    require(
        acceptance
            == json!({
                "schema_version": "devforge.manual-local-owner-acceptance/v1",
                "owner": owner,
                "action": "install_unqualified_local_baseline",
                "qualification_status": "UNQUALIFIED",
                "inputs": inputs,
                "observation_basis": "operator-reviewed actual evidence",
            }),
        "missing exact local owner acceptance",
    )?;
    evidence.recheck()?;
    let mut summary = json!({
        "record": root_pin,
        "owner": owner,
        "packages": packages.keys().collect::<Vec<_>>(),
        "predicate": if redirected { "manual-local-baseline/v2" } else { "manual-local-baseline/v1" },
        "qualification_status": "UNQUALIFIED",
        "acceptance_status": "LOCAL_ACCEPTANCE_SET_PASS",
    });
    if let Some(historical) = historical {
        summary["historical_project_root"] = historical;
        summary["test_destination"] = json!(evidence.project);
    }
    if reuse {
        summary["reused_allocations"] = json!(
            allocations
                .values()
                .map(|a| a.id.as_str())
                .collect::<Vec<_>>()
        );
        summary["reused_observations"] = json!(reused.len());
        summary["subsequent_judgments"] =
            json!(allocations.values().map(|a| a.judged).sum::<usize>());
    }
    Ok(Validated { summary, evidence })
}

/// Bind a local record's declared destination to the actual `--project`. A `v1`
/// record installs at its `project_root`. A `v2` record keeps that root, which its
/// frozen acceptance set names, and selects a separate `test_destination` for the
/// actual installation: exactly `{historical_project_root, path, acceptance_set}`,
/// whose root and set pin must equal the record's own and whose `path` must be the
/// canonical actual destination. Installation and the preflight share this rule.
/// Returns the historical root when the record is redirected.
fn bind_destination(
    record: &Value,
    redirected: bool,
    evidence: &Evidence,
) -> Result<Option<Value>> {
    if !redirected {
        require(
            record["project_root"] == json!(evidence.project),
            "wrong local installation destination",
        )?;
        return Ok(None);
    }
    let selection = exact(
        &record["test_destination"],
        &["historical_project_root", "path", "acceptance_set"],
        "test destination selection",
    )?;
    text(&record["project_root"], "historical project root")?;
    require(
        selection["historical_project_root"] == record["project_root"],
        "test destination names a different historical project root",
    )?;
    require(
        selection["acceptance_set"] == record["acceptance_set"],
        "test destination is bound to a different acceptance set",
    )?;
    let path = PathBuf::from(text(&selection["path"], "test destination path")?);
    require(
        path.is_absolute() && canonical(&path)?,
        "noncanonical test destination",
    )?;
    require(
        path == evidence.project,
        "test destination differs from the installation destination",
    )?;
    Ok(Some(record["project_root"].clone()))
}

struct LocalPackages {
    authors: BTreeSet<String>,
    catalogs: Map<String, Value>,
    /// Package name to runtime manifest pin: the candidate identity every observation binds.
    identities: Value,
}

/// Bind the local package rows to the planned installation bytes and the current
/// canonical source, exactly as installation does.
fn local_packages(
    rows: &Value,
    packages: &BTreeMap<String, BTreeMap<String, String>>,
    evidence: &mut Evidence,
) -> Result<LocalPackages> {
    let framework = evidence.framework.clone();
    let listed = rows.as_array();
    let mut names = Vec::new();
    for row in listed.into_iter().flatten() {
        names.push(get(row, "name")?);
    }
    let expected_names: Vec<Value> = NAMES.iter().map(|n| json!(n)).collect();
    require(
        listed.is_some_and(|r| r.len() == NAMES.len())
            && set_eq(&names, &expected_names.iter().collect::<Vec<_>>())
            && packages.keys().eq(NAMES.iter()),
        "local baseline requires both exact manual packages",
    )?;
    let mut authors = BTreeSet::new();
    let mut catalogs = Map::new();
    let mut identities = Map::new();
    for package in list(rows)? {
        exact(
            package,
            &[
                "name",
                "manifest",
                "source_manifest",
                "specification",
                "cases",
                "author",
            ],
            "local package",
        )?;
        let name = package["name"].as_str().unwrap_or_default();
        authors.insert(text(&package["author"], "package author")?.to_string());
        let runtime = evidence.document(&package["manifest"], false)?;
        require(
            runtime
                == json!({"schema_version": "devforge.expert-runtime-manifest/v1", "name": name,
                          "files_sha256": packages[name]}),
            "local runtime manifest differs from planned bytes",
        )?;
        let source = evidence.document(&package["source_manifest"], false)?;
        let root = framework
            .join("providers/codex/plugins/devforgeai/skills")
            .join(name);
        exact(&source, &["source_root", "files_sha256"], "source manifest")?;
        let actual = source_files(&root)?;
        require(
            source == json!({"source_root": root, "files_sha256": actual}),
            "source identity changed",
        )?;
        for (relative, digest) in &actual {
            evidence.pin(
                &json!({"path": root.join(relative), "sha256": digest}),
                false,
            )?;
        }
        evidence.source_trees.insert(root.clone(), actual);
        require(
            index(&package["cases"], "path")? == &json!(root.join("evals/evals.json")),
            "wrong source case catalog",
        )?;
        let catalog = evidence.document(&package["cases"], false)?;
        let cases = match obj(&catalog)?.get("cases") {
            Some(cases) => cases,
            None => get(&catalog, "evals")?,
        };
        require(
            cases.as_array().is_some_and(|c| !c.is_empty()),
            "missing qualification cases",
        )?;
        let mut ids = Map::new();
        let mut count = 0;
        for case in list(cases)? {
            ids.insert(
                text(get(case, "id")?, "case ID")?.to_string(),
                json!("NOT_RUN"),
            );
            count += 1;
        }
        require(ids.len() == count, "duplicate qualification cases")?;
        catalogs.insert(name.to_string(), Value::Object(ids));
        evidence.pin(&package["specification"], false)?;
        identities.insert(name.to_string(), package["manifest"].clone());
    }
    Ok(LocalPackages {
        authors,
        catalogs,
        identities: Value::Object(identities),
    })
}

/// Read the frozen local acceptance set a record pins and check it against the
/// record's identity fields, the nine fixed checks and its limits. Returns the
/// set and its frozen instant.
fn local_set(record: &Value, evidence: &mut Evidence) -> Result<(Value, i128)> {
    let plan = evidence.document(&record["acceptance_set"], true)?;
    exact(
        &plan,
        &[
            "schema_version",
            "project_root",
            "owner",
            "authorization",
            "packages",
            "checks",
            "frozen_at_utc",
            "max_seconds",
            "max_native_turns",
            "historical_evidence",
        ],
        "local acceptance set",
    )?;
    require(
        is(
            &plan["schema_version"],
            "devforge.manual-local-acceptance-set/v1",
        ),
        "unsupported local set",
    )?;
    require(
        [
            "owner",
            "project_root",
            "authorization",
            "packages",
            "historical_evidence",
        ]
        .iter()
        .all(|key| plan[*key] == record[*key]),
        "local set authority/identity differs",
    )?;
    require(
        plan["checks"].as_object().is_some_and(|c| {
            c.len() == LOCAL_CHECKS.len() && LOCAL_CHECKS.iter().all(|(k, _)| c.contains_key(*k))
        }),
        "local acceptance check coverage differs",
    )?;
    for (key, kind) in LOCAL_CHECKS {
        let check = exact(
            &plan["checks"][key],
            &["kind", "expectations"],
            "predefined check",
        )?;
        require(
            is(&check["kind"], kind)
                && check["expectations"]
                    .as_array()
                    .is_some_and(|e| !e.is_empty()),
            "acceptance check weakened",
        )?;
        for expectation in list(&check["expectations"])? {
            text(expectation, "predefined expectation")?;
        }
    }
    require(
        ["max_seconds", "max_native_turns"]
            .iter()
            .all(|key| is_int(&plan[*key]) && plan[*key].as_i64().is_some_and(|v| v > 0)),
        "unbounded local set",
    )?;
    let frozen = time(&plan["frozen_at_utc"])?;
    Ok((plan, frozen))
}

struct Observed {
    actor: String,
    /// The observation reuses a preserved allocation attempt (`v2`), not the current interval.
    prior: bool,
}

/// Validate one native observation for check `key` exactly as installation does:
/// candidate identity, PASS outcome, native client, model, actor, state isolation,
/// transcript, artifacts, the receiving transfer, and either the current results
/// `interval` or the preserved allocation attempt a `v2` observation reuses.
#[allow(clippy::too_many_arguments)]
fn local_observation(
    key: &str,
    reference: &Value,
    record: &Value,
    identities: &Value,
    reuse: bool,
    interval: Option<(i128, i128)>,
    allocations: &mut BTreeMap<String, Allocation>,
    evidence: &mut Evidence,
) -> Result<Observed> {
    let observation = evidence.document(reference, false)?;
    let prior = is(&observation["schema_version"], OBSERVATION_V2);
    let mut fields = vec![
        "schema_version",
        "acceptance_set",
        "outcome",
        "packages",
        "actor",
        "native_client",
        "model",
        "reasoning_effort",
        "state_isolation",
        "transcript",
        "artifacts",
        "started_at_utc",
        "finished_at_utc",
        "manual_transfer",
    ];
    if prior {
        fields.extend(["allocation", "attempt_id"]);
    }
    exact(&observation, &fields, "native local observation")?;
    require(
        (prior || is(&observation["schema_version"], OBSERVATION_V1))
            && observation["acceptance_set"] == record["acceptance_set"]
            && observation["packages"] == *identities
            && is(&observation["outcome"], "PASS")
            && is(&observation["native_client"], "codex"),
        "native observation identity/result differs",
    )?;
    text(&observation["model"], "observed native model")?;
    let actor = text(&observation["actor"], "observed native actor")?.to_string();
    text(
        &observation["reasoning_effort"],
        "observed reasoning effort",
    )?;
    let observed_start = time(&observation["started_at_utc"])?;
    let observed_end = time(&observation["finished_at_utc"])?;
    if prior {
        // A preserved observation is validated against the allocation that
        // produced it and is never counted as a turn of this interval.
        require(reuse, "reused observation requires reuse results")?;
        let allocation_path = text(
            index(&observation["allocation"], "path")?,
            "allocation path",
        )?;
        let allocation = allocations
            .get_mut(allocation_path)
            .ok_or_else(|| refuse("reused observation references an unlisted allocation"))?;
        require(
            allocation.start <= observed_start
                && observed_start <= observed_end
                && observed_end <= allocation.deadline,
            "reused observation outside its allocation window",
        )?;
        let attempt_id = text(&observation["attempt_id"], "attempt ID")?;
        let attempt = allocation.attempts.get(attempt_id).ok_or_else(|| {
            refuse("reused observation is not a recorded attempt of its allocation")
        })?;
        require(
            is(&attempt["outcome"], "PASS"),
            "reused attempt did not pass",
        )?;
        require(
            attempt["actor"] == observation["actor"]
                && time(&attempt["started_at_utc"])? == observed_start
                && time(&attempt["finished_at_utc"])? == observed_end,
            "reused observation differs from its recorded attempt",
        )?;
        require(
            attempt["evidence"]
                .as_array()
                .is_some_and(|e| e.contains(&observation["transcript"])),
            "reused observation transcript is not the attempt's recorded evidence",
        )?;
        allocation.used = true;
    } else {
        let (start, end) = interval.ok_or_else(|| {
            refuse("current-interval observation has no results interval to check")
        })?;
        require(
            start <= observed_start && observed_start <= observed_end && observed_end <= end,
            "observation outside predefined set interval",
        )?;
    }
    evidence.pin(&observation["state_isolation"], false)?;
    evidence.pin(&observation["transcript"], false)?;
    evidence.refs(&observation["artifacts"])?;
    if RECEIVING.contains(&key) {
        let transfer = exact(
            &observation["manual_transfer"],
            &[
                "direction",
                "user",
                "user_request",
                "producer_output",
                "receiver_observation",
                "completed_action",
            ],
            "manual transfer",
        )?;
        require(
            is(&transfer["direction"], key),
            "wrong manual transfer direction",
        )?;
        text(&transfer["user"], "actual receiving user")?;
        for field in [
            "user_request",
            "producer_output",
            "receiver_observation",
            "completed_action",
        ] {
            evidence.pin(&transfer[field], false)?;
        }
    }
    evidence.walk(&observation)?;
    Ok(Observed { actor, prior })
}

// ---- evidence preflight -----------------------------------------------------

const PREFLIGHT_SCHEMA: &str = "devforge.manual-local-evidence-preflight/v1";
/// The `v1` packet plus the same `test_destination` selection a `v2` record carries.
const PREFLIGHT_V2: &str = "devforge.manual-local-evidence-preflight/v2";
const PREFLIGHT_MEANING: &str = "COMPATIBLE means only that every mechanical check within this preflight's documented scope passed for the selected bytes; it is not acceptance, qualification, an installation receipt or permission to run a model";
/// The timing rule the preflight reports for every allocation entry, quoted from the
/// owner's approval of 2026-09-10 and applied by `frozen_before_first_attempt`.
const FREEZE_RULE: &str = "owner-approved 2026-09-10: preparation may begin before freezing; requirements, cases and selected skill files must be fixed before the first native attempt in each allocation; the first attempt is the earliest start across every recorded attempt, failed ones included; the allocation's original start, deadline and cap are unchanged";
const TIMING_COMPATIBLE: &str =
    "the acceptance set was frozen before the allocation's first recorded native attempt";
const TIMING_NO_ATTEMPT: &str = "no native attempt is recorded under this allocation, so it has no first-attempt instant to compare and it supplies no native observation";
const TIMING_UNBOUND: &str =
    "prerequisite not satisfied: the reference did not bind, so its attempt ledger was not read";

/// Accumulates the preflight report: every independently detectable blocker is
/// recorded, and a check whose prerequisite failed is marked NOT_PERFORMED
/// rather than claimed.
#[derive(Default)]
struct Preflight {
    checks: Vec<Value>,
    blockers: Vec<String>,
}

impl Preflight {
    fn pass(&mut self, check: &str, detail: Value) {
        self.checks
            .push(json!({"check": check, "status": "PASS", "detail": detail}));
    }
    fn block(&mut self, check: &str, reason: &str) {
        self.blockers.push(format!("{check}: {reason}"));
        self.checks
            .push(json!({"check": check, "status": "BLOCKED", "reason": reason}));
    }
    fn skip(&mut self, check: &str, prerequisite: &str) {
        self.checks
            .push(json!({"check": check, "status": "NOT_PERFORMED",
            "reason": format!("prerequisite not satisfied: {prerequisite}")}));
    }
    fn record<T>(
        &mut self,
        check: &str,
        result: Result<T>,
        detail: impl FnOnce(&T) -> Value,
    ) -> Option<T> {
        match result {
            Ok(value) => {
                self.pass(check, detail(&value));
                Some(value)
            }
            Err(error) => {
                self.block(check, &format!("{error:#}"));
                None
            }
        }
    }
}

/// Read the preflight packet: the adoption record's identity fields plus the
/// selected allocation references and observation rows. No results, review or
/// acceptance record is expected; their absence is reported as pending.
fn read_packet(root_pin: &Value, evidence: &mut Evidence) -> Result<Value> {
    let packet = evidence.document(root_pin, true)?;
    let redirected = is(&packet["schema_version"], PREFLIGHT_V2);
    let mut fields = vec![
        "schema_version",
        "project_root",
        "owner",
        "authorization",
        "packages",
        "acceptance_set",
        "historical_evidence",
        "allocations",
        "observations",
    ];
    if redirected {
        fields.push("test_destination");
    }
    exact(&packet, &fields, "preflight packet")?;
    require(
        redirected || is(&packet["schema_version"], PREFLIGHT_SCHEMA),
        "unsupported preflight packet",
    )?;
    bind_destination(&packet, redirected, evidence)?;
    text(&packet["owner"], "local owner")?;
    evidence.pin(&packet["authorization"], true)?;
    evidence.refs(&packet["historical_evidence"])?;
    require(
        packet["allocations"].is_array() && packet["observations"].is_array(),
        "invalid preflight selection lists",
    )?;
    for row in list(&packet["observations"])? {
        let row = exact(row, &["check", "observation"], "observation selection")?;
        require(
            LOCAL_CHECKS
                .iter()
                .any(|(id, kind)| *kind == "N" && is(&row["check"], id)),
            "observation selection names no native check",
        )?;
        pin_of(&row["observation"])?;
    }
    Ok(packet)
}

/// Check-only preflight over preserved local-adoption evidence. It reuses the
/// installer's readers (`local_packages`, `local_set`, `bind_allocation`,
/// `local_observation`, `Evidence`) so its rules cannot drift from installation,
/// applies the owner-approved freeze timing through the same
/// `frozen_before_first_attempt` the installer uses, separately from the binding so
/// the owner sees the preserved instants either way, and never installs, retires,
/// mutates evidence, runs candidate code, executes hooks or calls a model.
fn check_local_evidence(
    project: &Path,
    framework: &Path,
    packet_path: &Path,
    authority: &Path,
) -> Result<Value> {
    let project = crate::resolved(project)?;
    ensure!(project.is_dir(), "project must already exist");
    let framework = crate::resolved(framework)?;
    // As for installation, the authority check precedes any evidence read; its
    // failure is a refusal rather than a report.
    let protected = verify_authority(authority, &project, &framework)?;
    let mut report = Preflight::default();
    report.pass(
        "protected_authority",
        json!({"executable": protected["executable"], "source_sha256": protected["source_sha256"]}),
    );
    if crate::separate(&project, &framework) {
        report.pass(
            "destination_separation",
            json!({"project": project, "framework": framework}),
        );
    } else {
        report.block(
            "destination_separation",
            &format!(
                "project and framework must be separate directories; manual-experts refuses project {} with framework {}",
                project.display(),
                framework.display()
            ),
        );
    }
    let mut evidence = Evidence {
        project: project.clone(),
        framework: framework.clone(),
        pins: BTreeMap::new(),
        source_trees: BTreeMap::new(),
    };
    let packet_path = crate::resolved(packet_path)?;
    let raw = fs::read(&packet_path).with_context(|| {
        format!(
            "manual expert evidence: cannot read {}",
            packet_path.display()
        )
    })?;
    let root_pin = json!({"path": packet_path, "sha256": crate::hash(&raw)});
    let packet = report.record(
        "packet_record",
        read_packet(&root_pin, &mut evidence),
        |p| {
            json!({"owner": p["owner"],
                "historical_project_root": p["project_root"],
                "test_destination": p["test_destination"]["path"],
                "allocations": p["allocations"].as_array().map(Vec::len).unwrap_or_default(),
                "observations": p["observations"].as_array().map(Vec::len).unwrap_or_default()})
        },
    );
    let mut allocation_rows = Vec::new();
    let mut observation_rows = Vec::new();
    let mut observed_checks = BTreeSet::new();
    let mut owner = String::new();
    match &packet {
        Some(packet) => {
            owner = packet["owner"].as_str().unwrap_or_default().to_string();
            let local = match planned_bytes(&framework).and_then(|p| selected_packages(&p)) {
                Ok(planned) => report.record(
                    "package_identities",
                    local_packages(&packet["packages"], &planned, &mut evidence),
                    |l| json!({"identities": l.identities, "authors": l.authors}),
                ),
                Err(error) => {
                    report.block("package_identities", &format!("{error:#}"));
                    None
                }
            };
            let set = report.record(
                "acceptance_set",
                local_set(packet, &mut evidence),
                |set| {
                    json!({"pin": packet["acceptance_set"], "frozen_at_utc": set.0["frozen_at_utc"],
                        "max_seconds": set.0["max_seconds"], "max_native_turns": set.0["max_native_turns"]})
                },
            );
            if let (Some(local), Some((plan, frozen))) = (&local, &set) {
                let mut allocations: BTreeMap<String, Allocation> = BTreeMap::new();
                let mut blocked_allocations = BTreeSet::new();
                let mut failures = 0;
                for reference in list(&packet["allocations"])? {
                    // Every entry carries the applied rule, the frozen instant it was
                    // applied against and its own timing disposition, whether or not the
                    // reference binds; an unbound reference has no ledger to judge.
                    let mut entry = json!({"reference": reference, "status": "COMPATIBLE",
                        "blockers": [], "frozen_at_utc": plan["frozen_at_utc"],
                        "rule": FREEZE_RULE, "timing": "NOT_PERFORMED",
                        "timing_reason": TIMING_UNBOUND});
                    let key = pin_of(reference)
                        .and_then(|p| text(&p["path"], "allocation path").map(str::to_string));
                    let key = match key {
                        Ok(key) => key,
                        Err(error) => {
                            let reason = format!("{error:#}");
                            entry["status"] = json!("BLOCKED");
                            entry["blockers"] = json!([reason]);
                            report.block("allocation", &reason);
                            failures += 1;
                            allocation_rows.push(entry);
                            continue;
                        }
                    };
                    // Name the allocation even when its binding fails, so the owner can
                    // find the blocked reference; the id is re-read from the pinned bytes.
                    entry["allocation_id"] = evidence
                        .document(reference, true)
                        .ok()
                        .and_then(|doc| doc.get("allocation_id").cloned())
                        .unwrap_or(Value::Null);
                    let mut blockers: Vec<String> = Vec::new();
                    match bind_allocation(
                        reference,
                        packet,
                        &owner,
                        &local.identities,
                        &local.authors,
                        &mut evidence,
                    ) {
                        Ok(loaded) => {
                            let mut rows: Vec<&Value> = loaded.attempts.values().collect();
                            rows.sort_by_key(|a| time(&a["started_at_utc"]).unwrap_or_default());
                            let ledger: Vec<Value> = rows
                                .iter()
                                .map(|a| {
                                    json!({"attempt_id": a["attempt_id"], "actor": a["actor"],
                                        "started_at_utc": a["started_at_utc"],
                                        "finished_at_utc": a["finished_at_utc"], "outcome": a["outcome"],
                                        "judgment": a.get("judgment").cloned().unwrap_or(Value::Null)})
                                })
                                .collect();
                            entry["allocation_id"] = json!(loaded.id);
                            entry["allocation_status"] = json!(loaded.status);
                            entry["closeout_status"] = json!(loaded.closeout_status);
                            entry["started_at_utc"] = loaded.started_at_utc.clone();
                            entry["deadline_utc"] = loaded.deadline_utc.clone();
                            entry["max_attempts"] = json!(loaded.max_attempts);
                            // Reported from the same minimum the rule applies, in the
                            // spelling its ledger row used; `null` for an empty ledger.
                            entry["earliest_attempt_started_at_utc"] = loaded
                                .first_attempt
                                .and_then(|first| {
                                    rows.iter()
                                        .find(|a| {
                                            time(&a["started_at_utc"]).is_ok_and(|t| t == first)
                                        })
                                        .map(|a| a["started_at_utc"].clone())
                                })
                                .unwrap_or(Value::Null);
                            entry["attempts"] = json!(ledger);
                            let timing = frozen_before_first_attempt(*frozen, &loaded);
                            match (&timing, loaded.first_attempt) {
                                (Ok(()), None) => {
                                    entry["timing"] = json!("NOT_APPLICABLE");
                                    entry["timing_reason"] = json!(TIMING_NO_ATTEMPT);
                                }
                                (Ok(()), Some(_)) => {
                                    entry["timing"] = json!("COMPATIBLE");
                                    entry["timing_reason"] = json!(TIMING_COMPATIBLE);
                                }
                                (Err(error), _) => {
                                    entry["timing"] = json!("BLOCKED");
                                    entry["timing_reason"] = json!(format!("{error:#}"));
                                }
                            }
                            if let Err(error) = timing {
                                blockers.push(format!("{error:#}"));
                            } else if allocations.contains_key(&key) {
                                blockers.push("duplicate allocation reference".to_string());
                            } else {
                                allocations.insert(key.clone(), loaded);
                            }
                        }
                        Err(error) => blockers.push(format!("{error:#}")),
                    }
                    if !blockers.is_empty() {
                        entry["status"] = json!("BLOCKED");
                        for blocker in &blockers {
                            report.block(&format!("allocation {key}"), blocker);
                        }
                        entry["blockers"] = json!(blockers);
                        failures += 1;
                        blocked_allocations.insert(key);
                    }
                    allocation_rows.push(entry);
                }
                if failures == 0 {
                    report.pass("allocations", json!({"count": allocation_rows.len()}));
                } else {
                    report.block(
                        "allocations",
                        &format!(
                            "{failures} of {} allocation references blocked",
                            allocation_rows.len()
                        ),
                    );
                }
                let mut observation_failures = 0;
                let mut not_performed = 0;
                for row in list(&packet["observations"])? {
                    let key = row["check"].as_str().unwrap_or_default().to_string();
                    let mut entry = json!({"check": key, "observation": row["observation"]});
                    // An observation bound to a blocked allocation cannot be checked against it.
                    let bound_to = evidence
                        .document(&row["observation"], false)
                        .ok()
                        .and_then(|o| o["allocation"]["path"].as_str().map(str::to_string));
                    if bound_to
                        .as_ref()
                        .is_some_and(|p| blocked_allocations.contains(p))
                    {
                        entry["status"] = json!("NOT_PERFORMED");
                        entry["reason"] = json!(
                            "prerequisite not satisfied: its allocation reference is blocked"
                        );
                        not_performed += 1;
                    } else {
                        match local_observation(
                            &key,
                            &row["observation"],
                            packet,
                            &local.identities,
                            true,
                            None,
                            &mut allocations,
                            &mut evidence,
                        ) {
                            Ok(observed) => {
                                entry["status"] = json!("PASS");
                                entry["actor"] = json!(observed.actor);
                                observed_checks.insert(key);
                            }
                            Err(error) => {
                                let reason = format!("{error:#}");
                                entry["status"] = json!("BLOCKED");
                                entry["reason"] = json!(reason);
                                report.block(&format!("observation {key}"), &reason);
                                observation_failures += 1;
                            }
                        }
                    }
                    observation_rows.push(entry);
                }
                if observation_failures > 0 {
                    report.block(
                        "observations",
                        &format!(
                            "{observation_failures} of {} observation rows blocked",
                            observation_rows.len()
                        ),
                    );
                } else if not_performed > 0 {
                    report.skip(
                        "observations",
                        "one or more allocation references are blocked",
                    );
                } else {
                    report.pass("observations", json!({"count": observation_rows.len()}));
                }
            } else {
                let missing = if local.is_none() {
                    "package_identities"
                } else {
                    "acceptance_set"
                };
                report.skip("allocations", missing);
                report.skip("observations", missing);
            }
            // Final freshness: every pinned byte and source tree exactly as first read,
            // and the running executable still the selected authority.
            let recheck = evidence.recheck();
            report.record("freshness", recheck, |()| {
                json!({"pins": evidence.pins.len(), "source_trees": evidence.source_trees.len()})
            });
            match verify_authority(authority, &project, &framework) {
                Ok(again) if again == protected => {}
                Ok(_) => report.block(
                    "protected_authority",
                    "authority record or executable identity changed during the preflight",
                ),
                Err(error) => report.block("protected_authority", &format!("{error:#}")),
            }
        }
        None => {
            for check in [
                "package_identities",
                "acceptance_set",
                "allocations",
                "observations",
                "freshness",
            ] {
                report.skip(check, "packet_record");
            }
        }
    }
    let mut pending = vec![
        json!(
            "acceptance results record (devforge.manual-local-acceptance-results/v2) with nine PASS check rows and a bounded current interval: NOT_PRESENT in preflight scope"
        ),
        json!(
            "independent nine-check semantic review (devforge.manual-local-review/v1): NOT_PRESENT in preflight scope"
        ),
        json!(
            "owner acceptance (devforge.manual-local-owner-acceptance/v1): NOT_PRESENT in preflight scope"
        ),
        json!(format!(
            "installation destination checks (inventory, local edits, aliases, project quiescence) and installation writes at {}: NOT_PERFORMED",
            project.display()
        )),
    ];
    for (key, kind) in LOCAL_CHECKS {
        if kind == "N" && !observed_checks.contains(key) {
            pending.push(json!(format!("native observation for {key}: NOT_PRESENT")));
        }
    }
    if !observed_checks.is_empty() {
        pending.push(json!(
            "semantic judgment of each observation against the frozen expectations: NOT_EVALUATED"
        ));
    }
    let status = if report.blockers.is_empty() {
        "COMPATIBLE"
    } else {
        "BLOCKED"
    };
    Ok(json!({
        "status": status,
        "predicate": "manual-local-evidence-preflight/v1",
        "packet": root_pin,
        "owner": owner,
        "project": project,
        "framework": framework,
        "protected_identity": protected,
        "checks": report.checks,
        "allocations": allocation_rows,
        "observations": observation_rows,
        "blockers": report.blockers,
        "pending": pending,
        "not_performed": "installation, retirement, evidence mutation, candidate execution, hooks, model calls, PATH or client authentication changes",
        "meaning": PREFLIGHT_MEANING,
        "authority": "compiled Rust CLI; no Python consulted",
        "behavior": "NOT_EVALUATED",
    }))
}

// ---- protected executable identity ---------------------------------------

fn executable_identity() -> Result<(PathBuf, String)> {
    let running = fs::canonicalize(
        std::env::current_exe().context("cannot resolve the running executable")?,
    )?;
    let meta = fs::symlink_metadata(&running)?;
    ensure!(
        meta.is_file() && meta.mode() & 0o111 != 0,
        "running executable is not a regular executable file"
    );
    Ok((running.clone(), crate::hash(&fs::read(&running)?)))
}

fn identity() -> Result<Value> {
    let (path, sha256) = executable_identity()?;
    Ok(json!({
        "schema_version": "devforge.executable-identity/v1",
        "executable": {"path": path, "sha256": sha256},
        "source_sha256": SOURCE_SHA256,
        "source_scope": "Cargo.toml, Cargo.lock, build.rs, src/, runners/, runtime/delivery/",
        "protection": "NOT_ESTABLISHED_BY_SELF_REPORT",
        "instruction": "Pin these values in an authority record kept outside the evaluated agent's writable boundary; a self-reported identity is not protection.",
    }))
}

/// Verify the running executable against the owner-selected authority record.
fn verify_authority(authority: &Path, project: &Path, framework: &Path) -> Result<Value> {
    const PREFIX: &str = "protected authority";
    let path = crate::resolved(authority)?;
    ensure!(
        crate::separate(&path, project) && crate::separate(&path, framework),
        "{PREFIX}: authority record must be outside the project and framework"
    );
    let meta = fs::symlink_metadata(&path)
        .with_context(|| format!("{PREFIX}: cannot read authority record {}", path.display()))?;
    ensure!(
        meta.is_file() && meta.len() <= AUTHORITY_LIMIT,
        "{PREFIX}: authority record must be a regular file within 1 MiB"
    );
    let raw = fs::read(&path)?;
    let record =
        strict_json(&raw).map_err(|reason| anyhow!("{PREFIX}: {reason} in authority record"))?;
    let fields = ["schema_version", "owner", "executable", "source_sha256"];
    let map = record
        .as_object()
        .filter(|m| m.len() == fields.len() && fields.iter().all(|f| m.contains_key(*f)))
        .with_context(|| format!("{PREFIX}: invalid authority record fields"))?;
    ensure!(
        is(&map["schema_version"], AUTHORITY_SCHEMA),
        "{PREFIX}: unsupported authority record schema"
    );
    let owner = map["owner"]
        .as_str()
        .filter(|s| !s.trim().is_empty())
        .with_context(|| format!("{PREFIX}: authority record owner missing"))?;
    let executable = map["executable"]
        .as_object()
        .filter(|e| e.len() == 2 && e.contains_key("path") && e.contains_key("sha256"))
        .with_context(|| format!("{PREFIX}: invalid authority record executable pin"))?;
    let pinned_path = executable["path"]
        .as_str()
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .with_context(|| format!("{PREFIX}: authority executable path must be absolute"))?;
    let pinned_sha = executable["sha256"]
        .as_str()
        .filter(|s| is_hex64(s))
        .with_context(|| format!("{PREFIX}: invalid authority executable digest"))?;
    let source = map["source_sha256"]
        .as_str()
        .filter(|s| is_hex64(s))
        .with_context(|| format!("{PREFIX}: invalid authority source identity"))?;
    let (running, digest) = executable_identity()?;
    ensure!(
        running == pinned_path,
        "{PREFIX}: executable identity mismatch; running {} is not the pinned {}",
        running.display(),
        pinned_path.display()
    );
    ensure!(
        crate::separate(&running, project) && crate::separate(&running, framework),
        "{PREFIX}: executable must be outside the project and framework"
    );
    ensure!(
        digest == pinned_sha,
        "{PREFIX}: executable identity mismatch; digest {digest} differs from the pinned {pinned_sha}"
    );
    ensure!(
        SOURCE_SHA256 == source,
        "{PREFIX}: source identity mismatch; this executable was built from {SOURCE_SHA256}, not the pinned {source}"
    );
    Ok(json!({
        "record": {"path": path, "sha256": crate::hash(&raw)},
        "owner": owner,
        "executable": {"path": running, "sha256": digest},
        "source_sha256": source,
        "verification": "executable digest and source identity matched the owner-selected authority record",
    }))
}

// ---- selected runtime capability probe -----------------------------------
//
// Rust owns runtime selection hygiene, bounded execution and the delivery
// capability contract. The eight-field `devforge.delivery-capabilities/v1` base
// set stays admitted; the same base plus every one of the seven declared
// extensions is the supported extended contract. A partial extension set, an
// unknown field, a missing base field and any malformed value are refused.
// Nothing here writes to the filesystem, and a successful probe is mechanical
// compatibility only: native activation stays NOT_VERIFIED.

const PROBE_TIMEOUT: Duration = Duration::from_secs(5);
const PROBE_OUTPUT_LIMIT: usize = 1024 * 1024;
const PROBE_POLL: Duration = Duration::from_millis(5);
const CAPABILITY_SCHEMA: &str = "devforge.delivery-capabilities/v1";
const DELIVERY_PROTOCOL: &str = "devforge.delivery-runtime/v1";
const REQUIRED_EVENTS: [&str; 4] = ["SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"];
const BASE_FIELDS: [&str; 8] = [
    "schema_version",
    "protocol",
    "supported_providers",
    "completion_modes",
    "io_modes",
    "hook_events",
    "native_admission",
    "mechanical_scope",
];
const EXTENSION_FIELDS: [&str; 7] = [
    "utility_workflows",
    "utility_session_schema",
    "utility_native_schedule_schema",
    "native_execution_enabled",
    "native_process_interface",
    "native_process_receipt_schema",
    "native_semantic_review",
];
const EXTENSION_SCHEMAS: [(&str, &str); 3] = [
    ("utility_session_schema", "devforge.utility-session/v1"),
    (
        "utility_native_schedule_schema",
        "devforge.utility-native-schedule/v1",
    ),
    (
        "native_process_receipt_schema",
        "devforge.native-process-receipt/v1",
    ),
];

/// Path hygiene and content identity of the selected runtime, refused before any
/// execution. A second hard link would let an installation destination alias it.
fn runtime_digest(runtime: &Path) -> Result<String> {
    ensure!(
        runtime.is_absolute(),
        "--runtime must be an absolute executable path"
    );
    let mut prefix = PathBuf::new();
    for part in runtime.components() {
        prefix.push(part.as_os_str());
        if let Ok(meta) = fs::symlink_metadata(&prefix) {
            ensure!(
                !meta.file_type().is_symlink(),
                "--runtime must be canonical and have no symlink components"
            );
        }
    }
    match fs::canonicalize(runtime) {
        Ok(real) => ensure!(
            real == runtime,
            "--runtime must be canonical and have no symlink components"
        ),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            bail!("--runtime must select a regular executable file")
        }
        Err(error) => bail!("--runtime is unreadable: {error}"),
    }
    let meta = fs::symlink_metadata(runtime)
        .map_err(|error| anyhow!("--runtime is unreadable: {error}"))?;
    ensure!(
        meta.is_file() && meta.mode() & 0o111 != 0,
        "--runtime must select a regular executable file"
    );
    ensure!(
        meta.nlink() == 1,
        "--runtime must have exactly one hard link"
    );
    let bytes = fs::read(runtime).map_err(|error| anyhow!("--runtime is unreadable: {error}"))?;
    Ok(crate::hash(&bytes))
}

/// Drain one pipe into memory, stopping as soon as the combined budget is spent.
fn reader<R: Read + Send + 'static>(mut source: R, total: Arc<AtomicUsize>) -> JoinHandle<Vec<u8>> {
    std::thread::spawn(move || {
        let mut collected = Vec::new();
        let mut buffer = [0u8; 65536];
        while let Ok(count) = source.read(&mut buffer) {
            if count == 0 || total.fetch_add(count, Ordering::Relaxed) + count > PROBE_OUTPUT_LIMIT
            {
                break;
            }
            collected.extend_from_slice(&buffer[..count]);
        }
        collected
    })
}

fn stop(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

/// Run `<runtime> delivery capabilities` with no shell, no PATH lookup and no
/// stdin, bounded by a wall deadline and a combined output budget. Only stdout
/// is returned; stderr counts against the budget and is discarded.
fn capability_output(runtime: &Path) -> Result<Vec<u8>> {
    let mut child = Command::new(runtime)
        .args(["delivery", "capabilities"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| anyhow!("cannot execute the selected runtime: {error}"))?;
    let total = Arc::new(AtomicUsize::new(0));
    let out = reader(child.stdout.take().expect("piped stdout"), total.clone());
    let err = reader(child.stderr.take().expect("piped stderr"), total.clone());
    let deadline = Instant::now() + PROBE_TIMEOUT;
    let spent = || total.load(Ordering::Relaxed) > PROBE_OUTPUT_LIMIT;
    // Drain first: an unread pipe would block a runtime that outlives the budget.
    while !(out.is_finished() && err.is_finished()) {
        if spent() {
            stop(&mut child);
            bail!("runtime capabilities output exceeds 1 MiB");
        }
        if Instant::now() >= deadline {
            stop(&mut child);
            bail!("runtime capabilities timed out after 5 seconds");
        }
        std::thread::sleep(PROBE_POLL);
    }
    if spent() {
        stop(&mut child);
        bail!("runtime capabilities output exceeds 1 MiB");
    }
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(PROBE_POLL),
            Ok(None) => {
                stop(&mut child);
                bail!("runtime capabilities timed out after 5 seconds");
            }
            Err(error) => {
                stop(&mut child);
                bail!("cannot wait for the selected runtime: {error}");
            }
        }
    };
    let stdout = out.join().unwrap_or_default();
    let _ = err.join();
    ensure!(!spent(), "runtime capabilities output exceeds 1 MiB");
    if !status.success() {
        let code = status
            .code()
            .unwrap_or_else(|| -status.signal().unwrap_or_default());
        bail!("runtime capabilities exited with status {code}");
    }
    Ok(stdout)
}

/// A nonempty list of nonempty, unique strings, as the contract requires.
fn string_list(value: &Value) -> Option<Vec<&str>> {
    let items = value.as_array().filter(|items| !items.is_empty())?;
    let mut values: Vec<&str> = Vec::with_capacity(items.len());
    for item in items {
        let text = item.as_str().filter(|text| !text.is_empty())?;
        if values.contains(&text) {
            return None;
        }
        values.push(text);
    }
    Some(values)
}

fn filled(value: &Value) -> bool {
    value.as_str().is_some_and(|text| !text.trim().is_empty())
}

/// Admit exactly the base contract or the base plus every declared extension,
/// and report which one was observed.
fn validate_capabilities(data: &Value, providers: &[String]) -> Result<&'static str> {
    let Some(map) = data.as_object() else {
        bail!("malformed runtime capabilities fields");
    };
    let present: BTreeSet<&str> = map.keys().map(String::as_str).collect();
    let base: BTreeSet<&str> = BASE_FIELDS.into_iter().collect();
    let known: BTreeSet<&str> = BASE_FIELDS.into_iter().chain(EXTENSION_FIELDS).collect();
    let contract = if present == base {
        "base"
    } else if present == known {
        "extended"
    } else if !base.is_subset(&present) || !present.is_subset(&known) {
        bail!("malformed runtime capabilities fields");
    } else {
        bail!("unsupported runtime capabilities extension combination");
    };
    ensure!(
        map["schema_version"].as_str() == Some(CAPABILITY_SCHEMA)
            && map["protocol"].as_str() == Some(DELIVERY_PROTOCOL)
            && matches!(
                map["native_admission"].as_str(),
                Some("NOT_VALIDATED" | "CONTRACT_REQUIRED")
            )
            && filled(&map["mechanical_scope"]),
        "unsupported runtime capabilities contract"
    );
    let mut lists = BTreeMap::new();
    for field in [
        "supported_providers",
        "completion_modes",
        "io_modes",
        "hook_events",
    ] {
        let Some(values) = string_list(&map[field]) else {
            bail!("malformed runtime capability list: {field}");
        };
        lists.insert(field, values);
    }
    ensure!(
        providers
            .iter()
            .all(|provider| lists["supported_providers"].contains(&provider.as_str()))
            && lists["completion_modes"].contains(&"managed-session")
            && REQUIRED_EVENTS
                .iter()
                .all(|event| lists["hook_events"].contains(event)),
        "runtime capabilities do not satisfy the package requirement"
    );
    if contract == "extended" {
        ensure!(
            string_list(&map["utility_workflows"]).is_some(),
            "malformed runtime capabilities extension: utility_workflows"
        );
        for (field, schema) in EXTENSION_SCHEMAS {
            ensure!(
                map[field].as_str() == Some(schema),
                "malformed runtime capabilities extension: {field}"
            );
        }
        ensure!(
            map["native_execution_enabled"].is_boolean(),
            "malformed runtime capabilities extension: native_execution_enabled"
        );
        for field in ["native_process_interface", "native_semantic_review"] {
            ensure!(
                filled(&map[field]),
                "malformed runtime capabilities extension: {field}"
            );
        }
    }
    Ok(contract)
}

fn probe_runtime(runtime: &Path, providers: &[String], project: Option<&Path>) -> Result<Value> {
    // Bind the validating executable first. When an installation project is named,
    // an executable inside it could be overwritten by the very installation this
    // probe admits, so refuse before reading or executing anything.
    let (validator, digest) = executable_identity()?;
    let project = match project {
        Some(selected) => {
            let selected = crate::resolved(selected)?;
            ensure!(selected.is_dir(), "project must already exist");
            ensure!(
                crate::separate(&validator, &selected),
                "validating executable must be outside the installation project"
            );
            Some(selected)
        }
        None => None,
    };
    ensure!(
        !providers.is_empty(),
        "--provider must select at least one provider"
    );
    for (index, provider) in providers.iter().enumerate() {
        ensure!(
            provider == "codex" || provider == "claude",
            "--provider must be codex or claude"
        );
        ensure!(
            !providers[..index].contains(provider),
            "--provider must not be repeated"
        );
    }
    let before = runtime_digest(runtime)?;
    let output = capability_output(runtime)?;
    let capabilities = strict_json(&output)
        .map_err(|reason| anyhow!("invalid runtime capabilities output: {reason}"))?;
    let contract = validate_capabilities(&capabilities, providers)?;
    let after = runtime_digest(runtime)?;
    ensure!(
        before == after,
        "selected runtime binary changed during capability verification"
    );
    let mut report = json!({
        "schema_version": "devforge.runtime-probe/v1",
        "path": runtime,
        "sha256_before": before,
        "sha256_after": after,
        "capabilities": capabilities,
        "contract": contract,
        "providers": providers,
        "native_activation": "NOT_VERIFIED",
        "validator": {
            "executable": {"path": validator, "sha256": digest},
            "source_sha256": SOURCE_SHA256,
        },
    });
    // Record the bound project so the caller cannot present this report for another one.
    if let Some(project) = project {
        report
            .as_object_mut()
            .expect("probe report object")
            .insert("project".into(), json!(project));
    }
    Ok(report)
}

/// The pre-write protections owed to the selected validating executable.
///
/// The caller names the installation and the destinations it is about to write;
/// this executable decides, about itself, whether that installation may proceed.
/// Nothing is written, nothing is executed, and a refusal is final: the reasons
/// below are the installer's, carried out verbatim, never re-decided in Python.
fn guard_validator(project: &Path) -> Result<Value> {
    let project = crate::resolved(project)?;
    ensure!(project.is_dir(), "project must already exist");
    let mut raw = Vec::new();
    std::io::stdin()
        .lock()
        .take(MAX_BYTES + 1)
        .read_to_end(&mut raw)
        .context("cannot read the validator guard request")?;
    ensure!(
        raw.len() as u64 <= MAX_BYTES,
        "validator guard request exceeds size limit"
    );
    let request = strict_json(&raw)
        .map_err(|reason| anyhow!("malformed validator guard request: {reason}"))?;
    const MALFORMED: &str = "malformed validator guard request";
    let map = request.as_object().ok_or_else(|| anyhow!(MALFORMED))?;
    ensure!(
        map.len() == 3
            && map.get("schema_version").and_then(Value::as_str) == Some(GUARD_REQUEST_SCHEMA)
            && map.contains_key("report")
            && map.contains_key("write_paths"),
        MALFORMED
    );
    // Every destination is a nonempty, unique, plainly relative path in the project.
    let entries = map["write_paths"]
        .as_array()
        .filter(|values| !values.is_empty())
        .ok_or_else(|| anyhow!(MALFORMED))?;
    let mut relatives: Vec<&str> = Vec::new();
    for entry in entries {
        let relative = entry.as_str().unwrap_or_default();
        ensure!(
            !relative.is_empty()
                && Path::new(relative).is_relative()
                && !Path::new(relative)
                    .components()
                    .any(|part| matches!(part, Component::CurDir | Component::ParentDir))
                && !relatives.contains(&relative),
            MALFORMED
        );
        relatives.push(relative);
    }
    // Bind the request to this executable and this installation before deciding.
    let (running, digest) = executable_identity()?;
    let report = &map["report"];
    let reported = report
        .pointer("/validator/executable/path")
        .and_then(Value::as_str)
        .map(Path::new)
        .ok_or_else(|| anyhow!(MALFORMED))?;
    ensure!(
        fs::canonicalize(reported).unwrap_or_else(|_| reported.to_path_buf()) == running,
        "guard invoked by a different executable than the validator that probed"
    );
    ensure!(
        report.pointer("/schema_version").and_then(Value::as_str) == Some(PROBE_SCHEMA),
        "runtime validation did not report {PROBE_SCHEMA}"
    );
    ensure!(
        report
            .pointer("/project")
            .and_then(Value::as_str)
            .map(Path::new)
            == Some(project.as_path()),
        "runtime validation bound a different project"
    );
    // 1. No destination may name this executable, however the caller spells it.
    for relative in &relatives {
        let destination = project.join(relative);
        ensure!(
            destination != running
                && !fs::canonicalize(&destination).is_ok_and(|real| real == running),
            "selected validator binary overlaps an installation destination"
        );
    }
    // 2. No existing destination may already be another name for its inode.
    let identity = fs::metadata(&running)?;
    for relative in &relatives {
        let Ok(destination) = fs::metadata(project.join(relative)) else {
            continue;
        };
        ensure!(
            !destination.is_file()
                || (destination.dev(), destination.ino()) != (identity.dev(), identity.ino()),
            "installation would overwrite the selected validator binary through an alias"
        );
    }
    // 3. The bytes that probed must still be the bytes about to be protected.
    ensure!(
        report
            .pointer("/validator/executable/sha256")
            .and_then(Value::as_str)
            == Some(digest.as_str()),
        "selected validator binary changed before installation writes"
    );
    Ok(json!({
        "schema_version": GUARD_SCHEMA,
        "project": project,
        "validator": {"executable": {"path": running, "sha256": digest}},
        "write_paths": relatives.len(),
    }))
}

// ---- installer -----------------------------------------------------------

fn authoring_only(parts: &[&str]) -> bool {
    let last = parts.last().copied().unwrap_or_default();
    matches!(parts.first().copied(), Some("evals" | "history"))
        || parts.contains(&"__pycache__")
        || Path::new(last).extension().is_some_and(|e| e == "pyc")
        || last == "provenance.json"
}

fn parts(relative: &Path) -> Vec<&str> {
    relative
        .components()
        .filter_map(|c| c.as_os_str().to_str())
        .collect()
}

fn regular_files(root: &Path) -> Result<Vec<(PathBuf, PathBuf)>> {
    fn visit(root: &Path, dir: &Path, files: &mut Vec<(PathBuf, PathBuf)>) -> Result<()> {
        for entry in fs::read_dir(dir)? {
            let path = entry?.path();
            let meta = fs::symlink_metadata(&path)?;
            ensure!(
                !meta.file_type().is_symlink(),
                "symlink source is unsupported: {}",
                path.display()
            );
            if meta.is_dir() {
                visit(root, &path, files)?;
            } else if meta.is_file() {
                files.push((path.clone(), path.strip_prefix(root)?.to_path_buf()));
            }
        }
        Ok(())
    }
    ensure!(
        fs::symlink_metadata(root).is_ok_and(|m| m.is_dir()),
        "missing or symlink source directory: {}",
        root.display()
    );
    let mut files = Vec::new();
    visit(root, root, &mut files)?;
    files.sort();
    Ok(files)
}

fn runtime_skill_files(skill: &Path) -> Result<Vec<(PathBuf, String)>> {
    ensure!(
        fs::symlink_metadata(skill.join("SKILL.md")).is_ok_and(|m| m.is_file()),
        "skill entry missing: {}",
        skill.display()
    );
    let mut files = Vec::new();
    for (path, relative) in regular_files(skill)? {
        if !authoring_only(&parts(&relative)) {
            let name = relative.to_str().context("non-UTF8 skill path")?;
            files.push((path, name.to_string()));
        }
    }
    Ok(files)
}

fn provider_plugin(framework: &Path) -> Result<PathBuf> {
    let mut prefix = framework.to_path_buf();
    for component in ["providers", "codex", "plugins", "devforgeai"] {
        prefix.push(component);
        if let Ok(meta) = fs::symlink_metadata(&prefix) {
            ensure!(
                !meta.file_type().is_symlink(),
                "symlink source: {}",
                prefix.display()
            );
        }
    }
    let skills = prefix.join("skills");
    ensure!(
        fs::symlink_metadata(&skills).is_ok_and(|m| m.is_dir()),
        "provider skill source missing or symlink: {}",
        skills.display()
    );
    Ok(prefix)
}

fn safe_destination(project: &Path, relative: &str) -> Result<PathBuf> {
    crate::relative(relative)?;
    let path = project.join(relative);
    let mut prefix = project.to_path_buf();
    for component in Path::new(relative).components() {
        prefix.push(component);
        if let Ok(meta) = fs::symlink_metadata(&prefix) {
            ensure!(
                !meta.file_type().is_symlink(),
                "symlink destination: {}",
                prefix.display()
            );
            if prefix != path {
                ensure!(
                    meta.is_dir(),
                    "non-directory destination parent: {}",
                    prefix.display()
                );
            }
        }
    }
    ensure!(
        path != project && path.starts_with(project),
        "destination escapes project: {relative}"
    );
    if let Ok(meta) = fs::symlink_metadata(&path) {
        ensure!(
            meta.is_file(),
            "destination is not a regular file: {}",
            path.display()
        );
    }
    Ok(path)
}

fn read_inventory(path: &Path) -> Result<Value> {
    const INVALID: &str = "invalid installation inventory";
    if !path.exists() {
        return Ok(json!({"schema": 1, "files": {}}));
    }
    let raw = fs::read(path)?;
    let previous = strict_json(&raw).map_err(|reason| anyhow!("{INVALID}: {reason}"))?;
    let valid = previous.as_object().is_some_and(|p| {
        p.get("schema").is_none_or(|s| s.as_f64() == Some(1.0))
            && p.get("files")
                .and_then(Value::as_object)
                .is_some_and(|files| files.values().all(Value::is_string))
            && p.get("managed_hooks").is_none_or(Value::is_object)
            && p.get("runtime_evidence").is_none_or(Value::is_object)
    });
    ensure!(valid, "{INVALID}");
    Ok(previous)
}

/// The runtime bytes `manual-experts` would install for every promoted package
/// present in the framework, keyed by project-relative destination.
fn planned_bytes(framework: &Path) -> Result<BTreeMap<String, Vec<u8>>> {
    let plugin = provider_plugin(framework)?;
    let mut skills = Vec::new();
    for entry in fs::read_dir(plugin.join("skills"))? {
        let path = entry?.path();
        let promoted = path
            .file_name()
            .and_then(|n| n.to_str())
            .is_some_and(|n| NAMES.contains(&n));
        // Select through the link like the legacy installer; regular_files then
        // refuses a symlinked source instead of silently omitting the package.
        if promoted && fs::metadata(&path).is_ok_and(|m| m.is_dir()) {
            skills.push(path);
        }
    }
    skills.sort();
    ensure!(!skills.is_empty(), "no promoted Codex experts found");
    let mut planned: BTreeMap<String, Vec<u8>> = BTreeMap::new();
    for skill in &skills {
        let name = skill
            .file_name()
            .and_then(|n| n.to_str())
            .context("non-UTF8 skill name")?;
        for (path, relative) in runtime_skill_files(skill)? {
            let key = format!("{SKILL_ROOT}/{name}/{relative}");
            ensure!(!planned.contains_key(&key), "skill name collision: {key}");
            planned.insert(key, fs::read(&path)?);
        }
    }
    Ok(planned)
}

fn manual_experts(
    project: &Path,
    framework: &Path,
    evidence: &Path,
    authority: &Path,
) -> Result<Value> {
    let project = crate::resolved(project)?;
    ensure!(project.is_dir(), "project must already exist");
    let framework = crate::resolved(framework)?;
    ensure!(
        crate::separate(&project, &framework),
        "project and framework must be separate directories"
    );
    let protected = verify_authority(authority, &project, &framework)?;
    let planned = planned_bytes(&framework)?;
    let evidence_path = crate::resolved(evidence)?;
    let mut adoption = validate(&evidence_path, &planned, &project, &framework)?;
    let record_path = safe_destination(&project, INVENTORY)?;
    let previous = read_inventory(&record_path)?;
    let previous_files = previous["files"].as_object().cloned().unwrap_or_default();
    let managed_hooks = previous
        .get("managed_hooks")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let mut retired = Vec::new();
    for (relative, old_digest) in &previous_files {
        let segments = parts(Path::new(relative));
        // Only retire previously managed authoring files in the selected skill scope.
        if segments.len() >= 4
            && segments[0] == ".agents"
            && segments[1] == "skills"
            && NAMES.contains(&segments[2])
            && authoring_only(&segments[3..])
        {
            let destination = safe_destination(&project, relative)?;
            if destination.exists() {
                ensure!(
                    old_digest.as_str() == Some(crate::hash(&fs::read(&destination)?).as_str()),
                    "local edit/collision; refusing removal: {relative}"
                );
            }
            retired.push(relative.clone());
        }
    }
    for (relative, data) in &planned {
        let destination = safe_destination(&project, relative)?;
        if destination.exists() {
            let old = fs::read(&destination)?;
            if old != *data {
                ensure!(
                    previous_files.get(relative).and_then(Value::as_str)
                        == Some(crate::hash(&old).as_str()),
                    "local edit/collision; refusing replacement: {relative}"
                );
            }
        }
    }
    // Preflight every destination before writes. Existing identical installs are idempotent.
    let mut tracked = previous_files.clone();
    for relative in &retired {
        tracked.remove(relative);
    }
    for (relative, data) in &planned {
        tracked.insert(relative.clone(), json!(crate::hash(data)));
    }
    let mut updated = previous.clone();
    updated["schema"] = json!(1);
    updated["files"] = Value::Object(tracked);
    updated["managed_hooks"] = managed_hooks;
    updated["manual_expert_adoption"] = adoption.summary.clone();
    let mut record_bytes = serde_json::to_vec_pretty(&updated)?;
    record_bytes.push(b'\n');
    let mut replacements: BTreeMap<&str, &[u8]> = planned
        .iter()
        .map(|(relative, data)| (relative.as_str(), data.as_slice()))
        .collect();
    replacements.insert(INVENTORY, &record_bytes);
    // One existing inode may be reached through several destinations; it can only
    // receive one payload, so differing payloads cannot both be installed.
    let mut overwritten: BTreeMap<(u64, u64), BTreeSet<String>> = BTreeMap::new();
    for (relative, payload) in &replacements {
        if let Ok(meta) = fs::metadata(project.join(relative)) {
            if meta.is_file() {
                let digests = overwritten.entry((meta.dev(), meta.ino())).or_default();
                digests.insert(crate::hash(payload));
                ensure!(
                    digests.len() == 1,
                    "destination alias would receive conflicting replacement payloads: {relative}"
                );
            }
        }
    }
    // The selected authority record and the running executable must not be reachable
    // through any destination, whatever payload that destination would receive.
    for key in ["record", "executable"] {
        let path = protected[key]["path"]
            .as_str()
            .with_context(|| format!("protected {key} path"))?;
        let meta = fs::metadata(path)?;
        ensure!(
            !overwritten.contains_key(&(meta.dev(), meta.ino())),
            "protected authority: installation would overwrite the selected {key} through a destination alias"
        );
    }
    for (path, sha) in adoption.evidence.pins.clone() {
        let meta = fs::metadata(&path)?;
        if overwritten
            .get(&(meta.dev(), meta.ino()))
            .is_some_and(|digests| digests.iter().any(|value| *value != sha))
        {
            bail!(
                "manual expert evidence: installation would invalidate selected evidence through an alias"
            );
        }
        if let Ok(relative) = path.strip_prefix(&project) {
            let relative = relative.to_str().context("non-UTF8 evidence path")?;
            if retired.iter().any(|r| r == relative)
                || replacements
                    .get(relative)
                    .is_some_and(|payload| crate::hash(payload) != sha)
            {
                bail!("manual expert evidence: installation would invalidate selected evidence");
            }
        }
    }
    adoption.evidence.recheck()?;
    // Recheck the selected authority record and the running executable immediately
    // before the writes. This bounds drift between checks; it is not a defense
    // against a concurrent writer racing these path-based checks.
    ensure!(
        verify_authority(authority, &project, &framework)? == protected,
        "protected authority: authority record or executable identity changed before installation writes"
    );
    for relative in &retired {
        let destination = safe_destination(&project, relative)?;
        if destination.exists() {
            fs::remove_file(destination)?;
        }
    }
    for (relative, data) in &planned {
        let destination = safe_destination(&project, relative)?;
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(&destination, data)?;
    }
    fs::write(&record_path, &record_bytes)?;
    let mut result = json!({
        "status": "INSTALLED",
        "project": project,
        "providers": ["codex"],
        "files": planned.len(),
        "removed_authoring_files": retired,
        "scope": "promoted Codex experts only; agents/hooks preserved",
        "authority": "compiled Rust CLI; no Python consulted",
        "protected_identity": protected,
        "behavior": "NOT_EVALUATED",
    });
    if is(&adoption.summary["qualification_status"], "UNQUALIFIED") {
        result["qualification_status"] = json!("UNQUALIFIED");
        result["acceptance_status"] = json!("LOCAL_ACCEPTANCE_SET_PASS");
    }
    if let Some(historical) = adoption.summary.get("historical_project_root") {
        result["historical_project_root"] = historical.clone();
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::{parse_iso, time};
    use serde_json::json;

    #[test]
    fn timestamps_require_a_timezone_and_compare_by_instant() {
        let base = parse_iso("2026-09-08T12:00:00+00:00").unwrap();
        assert_eq!(parse_iso("2026-09-08T13:00:00+01:00"), Some(base));
        assert_eq!(
            parse_iso("2026-09-08T12:00:00.5+00:00"),
            Some(base + 500_000)
        );
        assert_eq!(parse_iso("2026-09-08 12:00-00:00"), Some(base));
        assert!(parse_iso("2026-09-08T12:00:00").is_none());
        assert!(parse_iso("2026-02-30T00:00:00+00:00").is_none());
        // Forms the legacy datetime.fromisoformat path accepted (probed on Python 3.12).
        for accepted in [
            "20260908T120000+0000",
            "2026-09-08T12:00:00+0000",
            "2026-09-08T12:00:00+00",
            "2026-09-08T12+00:00",
            "2026-09-08T1200+00:00",
            "20260908T12+00:00",
            "2026-W37-2T12:00:00+00:00",
            "2026W372T120000+0000",
            "2026-09-08t12:00:00+00:00",
            "2026-09-08_12:00:00+00:00",
            "2026-09-08T12:00:00.+00:00",
            "2026-09-08T13:00:30+01:00:30",
        ] {
            assert_eq!(parse_iso(accepted), Some(base), "{accepted}");
        }
        assert_eq!(
            parse_iso("2026-09-08T12:00:00.1234567+00:00"),
            Some(base + 123_456)
        );
        assert_eq!(
            parse_iso("2026-09-08T12:00:00,5+00:00"),
            Some(base + 500_000)
        );
        // A fraction may follow the hour or minute component of the time.
        for (text, micros) in [
            ("2026-09-08T12.5+00:00", 500_000),
            ("2026-09-08T12:00.25+00:00", 250_000),
            ("2026-09-08T1200.5+00:00", 500_000),
        ] {
            assert_eq!(parse_iso(text), Some(base + micros), "{text}");
        }
        // Fractional offsets shift the instant; large minute/second fields are summed.
        for (text, micros) in [
            ("2026-09-08T12:00:00+00:00:01.5", -1_500_000),
            ("2026-09-08T12:00:00-00:00:01.5", 1_500_000),
            ("2026-09-08T12:00:00+000001.5", -1_500_000),
            ("2026-09-08T12:00:00+00:00:01,5", -1_500_000),
            ("2026-09-08T12:00:00+00:00:01.1234567", -1_123_456),
            ("2026-09-08T12:00:00+23:59:59.999999", -86_399_999_999),
            ("2026-09-08T12:00:00+00:99", -5_940_000_000),
            ("2026-09-08T12:00:00+00:00:99", -99_000_000),
            // A fraction may follow the offset's last component, as in the time.
            ("2026-09-08T12:00:00+00:01.5", -60_500_000),
            ("2026-09-08T12:00:00+0001.5", -60_500_000),
            ("2026-09-08T12:00:00+00:01,5", -60_500_000),
            ("2026-09-08T12:00:00+01.5", -3_600_500_000),
            ("2026-09-08T12:00:00+2359.5", -86_340_500_000),
            // The legacy parser applied the fraction only to a non-zero whole offset.
            ("2026-09-08T12:00:00+00:00:00.5", 0),
            ("2026-09-08T12:00:00-00:00:00.5", 0),
            ("2026-09-08T12:00:00+00:00:00.000001", 0),
            ("2026-09-08T12:00:00+00:00.5", 0),
            ("2026-09-08T12:00:00+0000.5", 0),
            ("2026-09-08T12:00:00+00.5", 0),
        ] {
            assert_eq!(parse_iso(text), Some(base + micros), "{text}");
        }
        // ISO week 53 exists only in long years (January 1 on Thursday, or Wednesday when leap).
        for (week_date, calendar) in [
            ("2020-W53-1", "2020-12-28"),
            ("2020-W53-7", "2021-01-03"),
            ("2015-W53-5", "2016-01-01"),
            ("2026-W53-7", "2027-01-03"),
            ("2032-W53-1", "2032-12-27"),
            ("2021-W52-7", "2022-01-02"),
        ] {
            assert_eq!(
                parse_iso(&format!("{week_date}T00:00:00+00:00")),
                parse_iso(&format!("{calendar}T00:00:00+00:00")),
                "{week_date}"
            );
        }
        // Forms it rejected.
        for rejected in [
            "2026-251T12:00:00+00:00",
            "2026-09-08T24:00:00+00:00",
            "2026-09-08T12:00:60+00:00",
            "2026-09-08T12:00:00+24:00",
            "2026-09-08T12:00:00+23:99",
            "2026-09-08T12:00:00+0",
            "2026-09-08T12:00:00+000",
            "2026-09-08T12:00:00+00:0",
            "2026-09-08T12:00:00+0000:00",
            "2026-09-08T12:00:00+00:0000",
            "2026-09-08T12:00:00+00:00:01.",
            "2026-09-08T12:00:00+00:00.",
            "2026-09-08T12:00:00+00.",
            "2026-09-08T12:00:00+00:00.5abc",
            "2026-09-08T12:00:00+00:00:01.5abc",
            "2026-09-08T12:00:00+00:00:01.5.5",
            "2026-09-08T12:00:00+00:00xyz",
            "2026-09-08T12:00:00.5abc+00:00",
            "2026-09-08T12:00:00.5.5+00:00",
            "2026-W54-1T00:00:00+00:00",
            "2026-W00-1T00:00:00+00:00",
            "2021-W53-1T00:00:00+00:00",
            "2024-W53-1T00:00:00+00:00",
            "2028-W53-1T00:00:00+00:00",
        ] {
            assert!(parse_iso(rejected).is_none(), "{rejected}");
        }
        assert_eq!(
            time(&json!("2026-09-08T12:00:01Z")).unwrap() - base,
            1_000_000
        );
        assert!(time(&json!("2026-09-08T12:00:01")).is_err());
        assert!(time(&json!(1)).is_err());
    }
}
