//! Compiled reader for the hash-bound phase-state journal behind `delivery status`.
//!
//! This module ports the read path of `runtime/delivery/phase_state.py`
//! (`context`, `_State`, `_verify_current`, `_view`) together with the fixed
//! contract preflight it depends on (`_configuration`, `delivery_core`
//! `_load_contract`/`prepare`). It never creates, renames, truncates or chmods
//! anything: every filesystem entry point below opens read-only.
//!
//! Directory traversal reproduces the Python no-follow walk. Python holds a
//! directory descriptor and reads through `dir_fd=`; this crate forbids
//! `unsafe_code` and the pinned `rustix` feature set does not expose `openat`,
//! so a held descriptor is addressed through `/proc/self/fd/<fd>/<name>`. That
//! is the same anti-substitution property: the parent directory is pinned by an
//! open descriptor rather than re-resolved by name, and the final component is
//! opened with `O_NOFOLLOW`.
//!
//! Conditions this port cannot answer with byte-identical wording (CPython JSON
//! and `strerror` diagnostics), or has not ported yet (delivery-task/v2
//! reference coverage, the READY/COMPLETED final delivery checks, the utility
//! workflow engine, and exclusive `flock` acquisition) return [`Stop::Delegate`]
//! so the legacy controller answers instead. No refusal is dropped.

use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, Metadata, OpenOptions};
use std::io::Read;
use std::os::fd::AsRawFd;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

// Linux open(2) flags; the pinned dependency set exposes no libc constants.
const O_DIRECTORY: i32 = 0o200_000;
const O_NOFOLLOW: i32 = 0o400_000;
const O_NONBLOCK: i32 = 0o4_000;
const ENOENT: i32 = 2;
const ENOTDIR: i32 = 20;
const ELOOP: i32 = 40;

const SESSION_LIMIT: u64 = 64 * 1024;
const CHECKPOINT_LIMIT: u64 = 64 * 1024;
const ASSIGNMENT_LIMIT: u64 = 8 * 1024 * 1024;
const PREIMAGE_LIMIT: u64 = 8 * 1024 * 1024;
const INSTALLED_LIMIT: u64 = 64 * 1024 * 1024;
const STATE_JSON_LIMIT: u64 = 1024 * 1024;
const CONTRACT_LIMIT: u64 = 64 * 1024;
const INPUT_LIMIT: u64 = 8 * 1024 * 1024;
const RECEIPT_LIMIT: u64 = 128 * 1024;

const SCOPE: &str = "Mechanical phase evidence and delivery persistence only; no semantic \
quality, human adoption, native callback provenance or native completion certification.";
const STATE_SCHEMA: &str = "devforge.brainstorm-state/v1";
const SESSION_SCHEMA: &str = "devforge.brainstorm-session/v1";
const HEAD_SCHEMA: &str = "devforge.brainstorm-head/v1";
const OPERATION_SCHEMA: &str = "devforge.brainstorm-operation/v1";
const CHECKPOINT_SCHEMA: &str = "devforge.brainstorm-checkpoint/v1";
const V1: &str = "devforge.delivery-task/v1";
const V2: &str = "devforge.delivery-task/v2";

const BRAINSTORM_PHASES: &[&str] = &["Recover", "Explore", "Record", "Focus"];
const HANDOFF_PHASES: &[&str] = &["Recover", "Focus"];
const ALL_PHASES: &[&str] = &["Recover", "Explore", "Record", "Focus"];
const OPERATIONS: &[&str] = &[
    "start",
    "accepted",
    "waiting_user",
    "resume",
    "correction",
    "completion_intent",
    "completed",
];
const SESSION_KEYS: &[&str] = &[
    "schema_version",
    "task_id",
    "provider",
    "delivery_contract",
    "delivery_contract_sha256",
    "assignment",
    "installed_inputs",
    "checkpoint_path",
    "receipt_path",
    "deadline_utc",
    "max_corrections_per_phase",
    "output_baselines",
];
const MANIFEST_KEYS: &[&str] = &[
    "schema_version",
    "state_root",
    "session_path",
    "session_sha256",
    "task_id",
    "project_root",
    "provider",
    "mode",
    "started_at_utc",
    "deadline_utc",
    "snapshots",
];
const RECORD_KEYS: &[&str] = &[
    "schema_version",
    "sequence",
    "previous",
    "at_utc",
    "operation",
    "data",
    "snapshots",
];
const REF_KEYS: &[&str] = &["kind", "source", "path", "sha256", "bytes"];
const RESERVED_STATE_ENTRIES: &[&str] = &[
    "LOCK",
    "MANIFEST.json",
    "HEAD.json",
    "records",
    "snapshots",
    "pending",
];

/// A legacy `delivery_core._Problem`, or a condition the port declines to answer.
#[derive(Debug)]
pub(crate) enum Stop {
    Problem {
        result: &'static str,
        issue: String,
    },
    /// The legacy controller must answer; this port cannot match it byte for byte.
    Delegate,
}

type R<T> = Result<T, Stop>;

fn problem<T>(result: &'static str, issue: String) -> R<T> {
    Err(Stop::Problem { result, issue })
}

fn fail<T>(issue: impl Into<String>) -> R<T> {
    problem("FAIL", issue.into())
}

fn delegate<T>() -> R<T> {
    Err(Stop::Delegate)
}

fn hash(raw: &[u8]) -> String {
    format!("{:x}", Sha256::digest(raw))
}

// ---------------------------------------------------------------------------
// Held-descriptor directory access
// ---------------------------------------------------------------------------

/// An open directory descriptor addressed through `/proc/self/fd`.
struct Dir(fs::File);

impl Dir {
    fn own(&self) -> PathBuf {
        PathBuf::from(format!("/proc/self/fd/{}", self.0.as_raw_fd()))
    }

    fn path(&self, name: &str) -> PathBuf {
        self.own().join(name)
    }

    fn reopen(&self) -> std::io::Result<Dir> {
        OpenOptions::new()
            .read(true)
            .custom_flags(O_DIRECTORY)
            .open(self.own())
            .map(Dir)
    }

    fn child(&self, name: &str) -> std::io::Result<Dir> {
        OpenOptions::new()
            .read(true)
            .custom_flags(O_DIRECTORY | O_NOFOLLOW)
            .open(self.path(name))
            .map(Dir)
    }
}

fn os_problem(error: &std::io::Error, label: &str, missing: &'static str) -> Stop {
    match error.raw_os_error() {
        Some(ELOOP) | Some(ENOTDIR) => Stop::Problem {
            result: "FAIL",
            issue: format!("{label}: a path component is a symlink or is not a directory"),
        },
        Some(ENOENT) => Stop::Problem {
            result: missing,
            issue: format!("{label}: required path does not exist"),
        },
        // `_operational(f"...unavailable ({exc.strerror})")` needs libc wording.
        _ => Stop::Delegate,
    }
}

fn absent(stop: &Stop) -> bool {
    matches!(stop, Stop::Problem { result, .. } if *result == "_PHASE_ABSENT")
}

/// `delivery_core._directory`: walk from `/` without resolving a symlink component.
fn directory(path: &Path, label: &str, missing: &'static str) -> R<Dir> {
    let mut dir = Dir(OpenOptions::new()
        .read(true)
        .custom_flags(O_DIRECTORY)
        .open("/")
        .map_err(|error| os_problem(&error, label, missing))?);
    for component in path.components().skip(1) {
        let Some(name) = component.as_os_str().to_str() else {
            return delegate();
        };
        dir = dir
            .child(name)
            .map_err(|error| os_problem(&error, label, missing))?;
    }
    Ok(dir)
}

/// `delivery_core._parent`: descend the relative path, leaving the final name.
fn parent<'a>(
    dir: &Dir,
    relative: &'a str,
    label: &str,
    missing: &'static str,
) -> R<(Dir, &'a str)> {
    let mut parts = relative.split('/').collect::<Vec<_>>();
    let name = parts.pop().unwrap_or_default();
    let mut current = dir
        .reopen()
        .map_err(|error| os_problem(&error, label, missing))?;
    for component in parts {
        current = current
            .child(component)
            .map_err(|error| os_problem(&error, label, missing))?;
    }
    Ok((current, name))
}

fn identity(meta: &Metadata) -> (u64, u64, u64, i64, i64, i64, u64) {
    (
        meta.dev(),
        meta.ino(),
        meta.size(),
        meta.mtime(),
        meta.mtime_nsec(),
        meta.ctime(),
        meta.ctime_nsec() as u64,
    )
}

fn read_bytes(file: &mut fs::File, limit: u64) -> std::io::Result<Vec<u8>> {
    let mut raw = Vec::new();
    file.take(limit + 1).read_to_end(&mut raw)?;
    Ok(raw)
}

/// `phase_state._read_at`: bounded, identity-stable read through a held parent.
fn read_at(
    dir: &Dir,
    relative: &str,
    limit: u64,
    label: &str,
    missing: &'static str,
    optional: bool,
) -> R<Option<Vec<u8>>> {
    let gone = if optional { "_PHASE_ABSENT" } else { missing };
    let (holder, name) = match parent(dir, relative, label, gone) {
        Ok(value) => value,
        Err(stop) if optional && absent(&stop) => return Ok(None),
        Err(stop) => return Err(stop),
    };
    let path = holder.path(name);
    let initial = match fs::symlink_metadata(&path) {
        Ok(value) => value,
        Err(error) if optional && error.raw_os_error() == Some(ENOENT) => return Ok(None),
        Err(error) => return Err(os_problem(&error, label, missing)),
    };
    if !initial.file_type().is_file() || initial.nlink() != 1 {
        return fail(format!("{label}: expected a regular single-link file"));
    }
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(O_NOFOLLOW | O_NONBLOCK)
        .open(&path)
        .map_err(|error| os_problem(&error, label, missing))?;
    let before = file
        .metadata()
        .map_err(|error| os_problem(&error, label, missing))?;
    if !before.file_type().is_file()
        || before.nlink() != 1
        || identity(&initial) != identity(&before)
    {
        return fail(format!(
            "{label}: unsafe file or identity changed while opening"
        ));
    }
    if before.size() > limit {
        return fail(format!("{label}: file exceeds {limit} bytes"));
    }
    let raw = read_bytes(&mut file, limit).map_err(|error| os_problem(&error, label, missing))?;
    let after = file
        .metadata()
        .map_err(|error| os_problem(&error, label, missing))?;
    let current =
        fs::symlink_metadata(&path).map_err(|error| os_problem(&error, label, missing))?;
    if identity(&before) != identity(&after)
        || identity(&after) != identity(&current)
        || raw.len() as u64 != before.size()
        || raw.len() as u64 > limit
    {
        return fail(format!(
            "{label}: bytes or identity changed during readback"
        ));
    }
    Ok(Some(raw))
}

/// `delivery_core._read_at`: the stricter walker used for contracts and inputs.
fn core_read_at(
    dir: &Dir,
    relative: &str,
    limit: u64,
    label: &str,
    missing: &'static str,
) -> R<Vec<u8>> {
    let (holder, name) = parent(dir, relative, label, missing)?;
    let path = holder.path(name);
    let initial =
        fs::symlink_metadata(&path).map_err(|error| os_problem(&error, label, missing))?;
    if !initial.file_type().is_file() || initial.nlink() != 1 {
        return fail(format!(
            "{label}: expected a regular file with exactly one link"
        ));
    }
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(O_NOFOLLOW | O_NONBLOCK)
        .open(&path)
        .map_err(|error| os_problem(&error, label, missing))?;
    let before = file
        .metadata()
        .map_err(|error| os_problem(&error, label, missing))?;
    if !before.file_type().is_file() || before.nlink() != 1 {
        return fail(format!(
            "{label}: expected a regular file with exactly one link"
        ));
    }
    if identity(&initial) != identity(&before) {
        return fail(format!("{label}: file changed while opening"));
    }
    if before.size() == 0 || before.size() > limit {
        return fail(format!("{label}: file must contain 1 to {limit} bytes"));
    }
    let raw = read_bytes(&mut file, limit).map_err(|error| os_problem(&error, label, missing))?;
    let after = file
        .metadata()
        .map_err(|error| os_problem(&error, label, missing))?;
    let current =
        fs::symlink_metadata(&path).map_err(|error| os_problem(&error, label, missing))?;
    if identity(&before) != identity(&after) || identity(&after) != identity(&current) {
        return fail(format!("{label}: file changed during readback"));
    }
    if raw.len() as u64 != before.size() || raw.is_empty() || raw.len() as u64 > limit {
        return fail(format!("{label}: incomplete, empty or oversized readback"));
    }
    if std::str::from_utf8(&raw).is_err() {
        return fail(format!("{label}: bytes are not valid UTF-8"));
    }
    Ok(raw)
}

/// `phase_state._external`.
fn external(
    path: &Path,
    limit: u64,
    label: &str,
    missing: &'static str,
    optional: bool,
) -> R<Option<Vec<u8>>> {
    let gone = if optional { "_PHASE_ABSENT" } else { missing };
    let Some(holder) = path.parent() else {
        return delegate();
    };
    let dir = match directory(holder, label, gone) {
        Ok(value) => value,
        Err(stop) if optional && absent(&stop) => return Ok(None),
        Err(stop) => return Err(stop),
    };
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return delegate();
    };
    read_at(&dir, name, limit, label, missing, optional)
}

/// `delivery_core._read_external`.
fn core_external(path: &Path, limit: u64, label: &str, missing: &'static str) -> R<Vec<u8>> {
    let Some(holder) = path.parent() else {
        return delegate();
    };
    let dir = directory(holder, label, missing)?;
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return delegate();
    };
    core_read_at(&dir, name, limit, label, missing)
}

/// `delivery_core._inspect_destination`.
fn inspect_destination(dir: &Dir, relative: &str) -> R<()> {
    let label = format!("output {relative}");
    let mut parts = relative.split('/').collect::<Vec<_>>();
    let name = parts.pop().unwrap_or_default();
    let mut current = match dir.reopen() {
        Ok(value) => value,
        Err(error) => return Err(os_problem(&error, &label, "COULD_NOT_RUN")),
    };
    for component in parts {
        current = match current.child(component) {
            Ok(value) => value,
            Err(error) if error.raw_os_error() == Some(ENOENT) => return Ok(()),
            Err(error) => return Err(os_problem(&error, &label, "COULD_NOT_RUN")),
        };
    }
    match fs::symlink_metadata(current.path(name)) {
        Ok(info) => {
            if !info.file_type().is_file() || info.nlink() != 1 {
                return fail(format!(
                    "output {relative}: existing destination is not a regular single-link file"
                ));
            }
            Ok(())
        }
        Err(error) if error.raw_os_error() == Some(ENOENT) => Ok(()),
        Err(error) => Err(os_problem(&error, &label, "COULD_NOT_RUN")),
    }
}

/// `phase_state._object_directory`: every published object re-hashed on read.
fn object_directory(
    dir: &Dir,
    name: &str,
    suffix: &str,
    limit: u64,
) -> R<BTreeMap<String, Vec<u8>>> {
    let probe = format!("{name}/unused");
    let (holder, _) = parent(dir, &probe, name, "FAIL")?;
    let entries = match fs::read_dir(holder.own()) {
        Ok(entries) => entries,
        Err(_) => return delegate(),
    };
    let mut names = Vec::new();
    for entry in entries {
        let Ok(entry) = entry else { return delegate() };
        let Some(filename) = entry.file_name().to_str().map(str::to_owned) else {
            return fail(format!("unexpected protected {name} entry"));
        };
        names.push(filename);
    }
    let mut result = BTreeMap::new();
    for filename in names {
        if !filename.ends_with(suffix) {
            return fail(format!("unexpected protected {name} entry"));
        }
        let digest = &filename[..filename.len() - suffix.len()];
        hex_digest(
            &Value::String(digest.to_owned()),
            &format!("protected {name} filename"),
        )?;
        let label = format!("protected {name} object");
        let relative = format!("{name}/{filename}");
        let raw = read_at(dir, &relative, limit, &label, "FAIL", false)?
            .expect("a non-optional read returns bytes");
        if hash(&raw) != digest {
            return fail(format!("protected {name} object hash mismatch"));
        }
        result.insert(digest.to_owned(), raw);
    }
    Ok(result)
}

// ---------------------------------------------------------------------------
// Value helpers mirroring delivery_core / phase_state validation
// ---------------------------------------------------------------------------

fn parse_json(raw: &[u8], _label: &str) -> R<Value> {
    // CPython's decoder diagnostics are quoted verbatim by `delivery_core._json`.
    match serde_json::from_slice::<crate::delivery::StrictJson>(raw) {
        Ok(crate::delivery::StrictJson(value)) => Ok(value),
        Err(_) => delegate(),
    }
}

fn field<'a>(map: &'a Map<String, Value>, key: &str) -> R<&'a Value> {
    map.get(key).map_or_else(delegate, Ok)
}

fn object<'a>(value: &'a Value, _label: &str) -> R<&'a Map<String, Value>> {
    value.as_object().map_or_else(delegate, Ok)
}

fn exact<'a>(value: &'a Value, keys: &[&str], label: &str) -> R<&'a Map<String, Value>> {
    let matched = value
        .as_object()
        .filter(|map| map.len() == keys.len() && keys.iter().all(|key| map.contains_key(*key)));
    match matched {
        Some(map) => Ok(map),
        None => {
            let mut sorted = keys.to_vec();
            sorted.sort_unstable();
            fail(format!(
                "{label}: expected exactly the fields {}",
                sorted.join(", ")
            ))
        }
    }
}

/// CPython `str.isspace()`.
///
/// Rust's `char::is_whitespace` is the Unicode `White_Space` property; CPython
/// also treats the four bidirectional-class B/S control characters
/// U+001C..U+001F as space. The full CPython set was enumerated with
/// `/usr/bin/python3 -c 'print([hex(i) for i in range(0x110000) if chr(i).isspace()])'`
/// and is pinned by `tests::python_isspace_matches_the_enumerated_cpython_set`.
fn python_isspace(value: char) -> bool {
    value.is_whitespace() || matches!(value, '\u{1c}'..='\u{1f}')
}

/// `str.strip()` over CPython's whitespace set.
fn python_strip(value: &str) -> &str {
    value.trim_matches(python_isspace)
}

/// `delivery_core._text`.
fn text(value: &Value, label: &str) -> R<String> {
    let Some(raw) = value.as_str() else {
        return fail(format!(
            "{label}: expected a nonempty string without surrounding whitespace"
        ));
    };
    if python_strip(raw).is_empty() || raw != python_strip(raw) {
        return fail(format!(
            "{label}: expected a nonempty string without surrounding whitespace"
        ));
    }
    if raw.chars().any(|c| (c as u32) < 32 || c as u32 == 127) {
        return fail(format!("{label}: control characters are forbidden"));
    }
    Ok(raw.to_owned())
}

/// `delivery_core._absolute`.
fn absolute(value: &Value, label: &str) -> R<PathBuf> {
    let raw = text(value, label)?;
    if !raw.starts_with('/') || raw.contains('\\') || raw.contains('\0') {
        return fail(format!(
            "{label}: expected an absolute path without backslashes or NUL"
        ));
    }
    if raw != "/"
        && raw[1..]
            .split('/')
            .any(|part| matches!(part, "" | "." | ".."))
    {
        return fail(format!(
            "{label}: empty, dot and parent path components are forbidden"
        ));
    }
    Ok(PathBuf::from(raw))
}

/// `delivery_core._relative`.
fn relative(value: &Value, label: &str) -> R<String> {
    let raw = text(value, label)?;
    let drive = raw.as_bytes().first().is_some_and(u8::is_ascii_alphabetic)
        && raw.as_bytes().get(1) == Some(&b':');
    if raw.starts_with('/') || drive || raw.contains('\\') || raw.contains('\0') {
        return fail(format!(
            "{label}: expected a project-relative path without backslashes or NUL"
        ));
    }
    if raw.split('/').any(|part| matches!(part, "" | "." | "..")) {
        return fail(format!(
            "{label}: empty, dot and parent path components are forbidden"
        ));
    }
    Ok(raw)
}

fn is_hex64(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

/// `phase_state._digest`.
fn hex_digest(value: &Value, label: &str) -> R<String> {
    match value.as_str() {
        Some(raw) if is_hex64(raw) => Ok(raw.to_owned()),
        _ => fail(format!("{label}: expected a complete lowercase SHA-256")),
    }
}

/// `phase_state._nonce_value`.
fn nonce(value: &Value, label: &str) -> R<String> {
    let ok = value.as_str().is_some_and(|raw| {
        raw.len() == 32
            && raw
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    });
    match value.as_str() {
        Some(raw) if ok => Ok(raw.to_owned()),
        _ => fail(format!(
            "{label}: expected 32 lowercase hexadecimal characters"
        )),
    }
}

/// `delivery_core._revision`: a positive integer that is not a boolean.
fn revision(value: &Value, label: &str) -> R<i64> {
    match value.as_i64() {
        Some(number) if number >= 1 && !value.is_boolean() => Ok(number),
        _ => fail(format!(
            "{label}: revision must be a positive integer, not a boolean or float"
        )),
    }
}

/// `delivery_core._sections`.
fn sections(value: &Value, label: &str) -> R<Vec<String>> {
    let Some(items) = value.as_array().filter(|items| !items.is_empty()) else {
        return fail(format!("{label}: expected a nonempty section-ID array"));
    };
    let values = items
        .iter()
        .map(|item| text(item, label))
        .collect::<R<Vec<_>>>()?;
    if values.iter().collect::<BTreeSet<_>>().len() != values.len() {
        return fail(format!("{label}: duplicate section IDs"));
    }
    if values
        .iter()
        .any(|item| item.contains('[') || item.contains(']'))
    {
        return fail(format!(
            "{label}: section IDs must not include marker brackets"
        ));
    }
    Ok(values)
}

/// `phase_state._string`.
fn bounded_string(value: &Value, label: &str) -> R<String> {
    let Some(raw) = value.as_str().filter(|raw| !python_strip(raw).is_empty()) else {
        return fail(format!("{label}: expected a nonempty string"));
    };
    if raw.len() > 8192 || has_placeholder(raw) {
        return fail(format!(
            "{label}: oversized string or unresolved double-brace template marker"
        ));
    }
    Ok(raw.to_owned())
}

/// `phase_state._strings`.
fn bounded_strings(value: &Value, label: &str) -> R<Vec<String>> {
    let Some(items) = value.as_array().filter(|items| items.len() <= 100) else {
        return fail(format!("{label}: expected an array of at most 100 strings"));
    };
    items
        .iter()
        .map(|item| bounded_string(item, label))
        .collect()
}

/// `phase_state._PLACEHOLDER`: a non-greedy `{{...}}` marker anywhere in the text.
fn has_placeholder(value: &str) -> bool {
    match value.find("{{") {
        Some(start) => value[start + 2..].contains("}}"),
        None => false,
    }
}

fn within(path: &Path, root: &Path) -> bool {
    path.starts_with(root)
}

fn collide(left: &Path, right: &Path) -> bool {
    left.starts_with(right) || right.starts_with(left)
}

// ---------------------------------------------------------------------------
// Timestamps
// ---------------------------------------------------------------------------

/// Microseconds since the epoch for an aware ISO UTC timestamp, or `None`
/// when this parser will not commit to CPython's `datetime.fromisoformat`.
fn parse_utc(value: &str) -> Option<i128> {
    let (date, rest) = value.split_once(['T', 't', ' '])?;
    let mut parts = date.split('-');
    let year: i64 = parts.next()?.parse().ok()?;
    let month: u32 = parts.next()?.parse().ok()?;
    let day: u32 = parts.next()?.parse().ok()?;
    if parts.next().is_some() || !(1..=12).contains(&month) || day == 0 {
        return None;
    }
    if day > days_in_month(year, month) {
        return None;
    }
    let offset_at = rest
        .find(['Z', 'z', '+'])
        .or_else(|| rest.rfind('-').filter(|index| *index > 0))?;
    let (clock, zone) = rest.split_at(offset_at);
    if !matches!(
        zone,
        "Z" | "z" | "+00:00" | "-00:00" | "+0000" | "-0000" | "+00" | "-00"
    ) {
        // A nonzero or unusual offset is rejected by `_stamp`; leave it to CPython.
        return None;
    }
    let (clock, fraction) = match clock.split_once('.') {
        Some((clock, fraction)) => (clock, fraction),
        None => (clock, ""),
    };
    let mut units = clock.split(':');
    let hour: i64 = units.next()?.parse().ok()?;
    let minute: i64 = units.next()?.parse().ok()?;
    let second: i64 = match units.next() {
        Some(raw) => raw.parse().ok()?,
        None => 0,
    };
    if units.next().is_some() || hour > 23 || minute > 59 || second > 59 {
        return None;
    }
    if !fraction.is_empty() && (fraction.len() > 6 || !fraction.bytes().all(|b| b.is_ascii_digit()))
    {
        return None;
    }
    let mut micros: i128 = 0;
    if !fraction.is_empty() {
        let padded = format!("{fraction:0<6}");
        micros = padded.parse::<i128>().ok()?;
    }
    let days = civil_days(year, month, day)?;
    Some((days * 86_400 + hour * 3_600 + minute * 60 + second) as i128 * 1_000_000 + micros)
}

fn days_in_month(year: i64, month: u32) -> u32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 => 29,
        2 => 28,
        _ => 0,
    }
}

/// Howard Hinnant's days-from-civil algorithm.
fn civil_days(year: i64, month: u32, day: u32) -> Option<i64> {
    if !(1..=9999).contains(&year) {
        return None;
    }
    let year = if month <= 2 { year - 1 } else { year };
    let era = if year >= 0 { year } else { year - 399 } / 400;
    let year_of_era = year - era * 400;
    let month = i64::from(month);
    let doy = (153 * (if month > 2 { month - 3 } else { month + 9 }) + 2) / 5 + i64::from(day) - 1;
    let doe = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + doy;
    Some(era * 146_097 + doe - 719_468)
}

/// `phase_state._stamp`.
fn stamp(value: &Value, label: &str) -> R<i128> {
    let Some(raw) = value.as_str() else {
        return fail(format!("{label}: expected an aware ISO UTC timestamp"));
    };
    if raw.is_empty() || raw != python_strip(raw) {
        return fail(format!("{label}: expected an aware ISO UTC timestamp"));
    }
    match parse_utc(raw) {
        Some(micros) => Ok(micros),
        None => delegate(),
    }
}

fn now_micros() -> R<i128> {
    match SystemTime::now().duration_since(UNIX_EPOCH) {
        Ok(elapsed) => Ok(elapsed.as_micros() as i128),
        Err(_) => delegate(),
    }
}

// ---------------------------------------------------------------------------
// delivery_core._load_contract and prepare
// ---------------------------------------------------------------------------

struct Contract {
    value: Map<String, Value>,
    raw: Vec<u8>,
    root: PathBuf,
}

fn load_contract(path: &Path) -> R<Contract> {
    let raw = core_external(path, CONTRACT_LIMIT, "contract", "COULD_NOT_RUN")?;
    let parsed = parse_json(&raw, "contract")?;
    let version = parsed.get("schema_version").and_then(Value::as_str);
    match version {
        Some(V1) => {}
        Some(V2) => return delegate(),
        _ => return fail("contract: unsupported schema_version"),
    }
    let data = exact(
        &parsed,
        &[
            "schema_version",
            "task_id",
            "project_root",
            "mode",
            "inputs",
            "outputs",
        ],
        "contract",
    )?;
    text(field(data, "task_id")?, "task_id")?;
    let root = absolute(field(data, "project_root")?, "project_root")?;
    if within(path, &root) {
        return fail("contract: contract file must be outside the writable project");
    }
    let mode = data
        .get("mode")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    if mode != "brainstorm" && mode != "handoff-only" {
        return fail("contract: unsupported mode");
    }
    let (Some(inputs), Some(outputs)) = (
        field(data, "inputs")?.as_array(),
        field(data, "outputs")?.as_array(),
    ) else {
        return fail("contract: inputs and outputs must be arrays");
    };
    let mut paths: Vec<String> = Vec::new();
    for entry in inputs {
        let input = exact(entry, &["path", "sha256"], "input")?;
        let rel = relative(field(input, "path")?, "input path")?;
        if !field(input, "sha256")?.as_str().is_some_and(is_hex64) {
            return fail(format!(
                "input {rel}: expected a lowercase complete SHA-256"
            ));
        }
        if paths.contains(&rel) {
            return fail(format!("contract: duplicate/colliding path {rel}"));
        }
        paths.push(rel);
    }
    let mut ids: Vec<String> = Vec::new();
    let mut roles: BTreeSet<String> = BTreeSet::new();
    for entry in outputs {
        let output = exact(
            entry,
            &[
                "path",
                "artifact_id",
                "artifact_type",
                "revision",
                "sections",
            ],
            "output",
        )?;
        let rel = relative(field(output, "path")?, "output path")?;
        let aid = text(field(output, "artifact_id")?, "output artifact_id")?;
        let role = field(output, "artifact_type")?
            .as_str()
            .unwrap_or_default()
            .to_owned();
        if role != "idea-ledger" && role != "handoff" {
            return fail(format!("output {rel}: unsupported artifact_type"));
        }
        revision(field(output, "revision")?, "output revision")?;
        sections(field(output, "sections")?, "output sections")?;
        if paths.contains(&rel) || ids.contains(&aid) || roles.contains(&role) {
            return fail(format!(
                "contract: colliding output path, artifact ID or role for {rel}"
            ));
        }
        paths.push(rel);
        ids.push(aid);
        roles.insert(role);
    }
    let wanted: BTreeSet<String> = if mode == "brainstorm" {
        ["handoff", "idea-ledger"]
            .iter()
            .map(|s| (*s).to_owned())
            .collect()
    } else {
        ["handoff"].iter().map(|s| (*s).to_owned()).collect()
    };
    if roles != wanted {
        return fail(format!(
            "contract: {mode} requires exactly {}",
            wanted.iter().cloned().collect::<Vec<_>>().join(", ")
        ));
    }
    for rel in &paths {
        let parts = rel.split('/').collect::<Vec<_>>();
        for count in 1..parts.len() {
            if paths.contains(&parts[..count].join("/")) {
                return fail(format!("contract: file/directory path collision for {rel}"));
            }
        }
    }
    Ok(Contract {
        value: data.clone(),
        raw,
        root,
    })
}

/// `delivery_core.prepare` reduced to its v1 outcome: PASS, or the first problem.
fn prepare(path: &Path) -> R<()> {
    let contract = load_contract(path)?;
    let dir = directory(&contract.root, "project_root", "COULD_NOT_RUN")?;
    let outputs = field(&contract.value, "outputs")?
        .as_array()
        .map_or_else(delegate, Ok)?
        .clone();
    for spec in &outputs {
        let rel = spec
            .get("path")
            .and_then(Value::as_str)
            .map_or_else(delegate, Ok)?;
        inspect_destination(&dir, rel)?;
    }
    let inputs = field(&contract.value, "inputs")?
        .as_array()
        .map_or_else(delegate, Ok)?
        .clone();
    for spec in &inputs {
        let rel = spec
            .get("path")
            .and_then(Value::as_str)
            .map_or_else(delegate, Ok)?;
        let label = format!("input {rel}");
        let value = core_read_at(&dir, rel, INPUT_LIMIT, &label, "COULD_NOT_RUN")?;
        let expected = spec
            .get("sha256")
            .and_then(Value::as_str)
            .map_or_else(delegate, Ok)?;
        if hash(&value) != expected {
            return fail(format!(
                "input {rel}: selected SHA-256 does not match current bytes"
            ));
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// phase_state._configuration
// ---------------------------------------------------------------------------

struct Configuration {
    session: Map<String, Value>,
    raw: Vec<u8>,
    delivery: Map<String, Value>,
    project: PathBuf,
    receipt: PathBuf,
    sources: Vec<(String, String, String)>,
    outputs: Map<String, Value>,
    baselines: Map<String, Value>,
}

fn configuration(session_path: &Path, state_root: &Path, package: &Path) -> R<Configuration> {
    let raw = external(
        session_path,
        SESSION_LIMIT,
        "session contract",
        "COULD_NOT_RUN",
        false,
    )?
    .expect("a non-optional read returns bytes");
    let parsed = parse_json(&raw, "session contract")?;
    let session = exact(&parsed, SESSION_KEYS, "session contract")?.clone();
    if field(&session, "schema_version")?.as_str() != Some(SESSION_SCHEMA) {
        return fail("session contract: unsupported schema_version");
    }
    let task_id = text(field(&session, "task_id")?, "task_id")?;
    if !matches!(
        field(&session, "provider")?.as_str(),
        Some("codex") | Some("claude")
    ) {
        return fail("session contract: provider must be codex or claude");
    }
    let corrections = field(&session, "max_corrections_per_phase")?;
    if corrections.is_boolean() || corrections.as_i64() != Some(1) {
        return fail("max_corrections_per_phase must be integer 1, not boolean");
    }
    stamp(field(&session, "deadline_utc")?, "deadline_utc")?;
    let delivery_path = absolute(field(&session, "delivery_contract")?, "delivery_contract")?;
    let delivery_digest = hex_digest(
        field(&session, "delivery_contract_sha256")?,
        "delivery contract digest",
    )?;
    let contract = load_contract(&delivery_path)?;
    if hash(&contract.raw) != delivery_digest {
        return fail("delivery contract: selected SHA-256 mismatch");
    }
    if Some(task_id.as_str()) != field(&contract.value, "task_id")?.as_str() {
        return fail("session task_id does not match the selected delivery task");
    }
    let project = contract.root.clone();
    if within(session_path, &project) {
        return fail("session contract must be outside the writable project");
    }
    let assignment = exact(
        field(&session, "assignment")?,
        &["path", "sha256"],
        "assignment",
    )?
    .clone();
    let assignment_path = absolute(field(&assignment, "path")?, "assignment path")?;
    hex_digest(field(&assignment, "sha256")?, "assignment digest")?;
    if within(&assignment_path, &project) {
        return fail("assignment must be outside the writable project");
    }
    let assignment_raw = external(
        &assignment_path,
        ASSIGNMENT_LIMIT,
        "assignment",
        "COULD_NOT_RUN",
        false,
    )?
    .expect("a non-optional read returns bytes");
    if Some(hash(&assignment_raw).as_str()) != field(&assignment, "sha256")?.as_str() {
        return fail("assignment: selected SHA-256 mismatch");
    }
    let mut sources = vec![
        ("session".to_owned(), display(session_path)?, hash(&raw)),
        (
            "delivery".to_owned(),
            display(&delivery_path)?,
            hash(&contract.raw),
        ),
        (
            "assignment".to_owned(),
            display(&assignment_path)?,
            hash(&assignment_raw),
        ),
    ];
    let Some(installed) = field(&session, "installed_inputs")?.as_array().cloned() else {
        return fail("installed_inputs must be an array");
    };
    let mut installed_paths: Vec<PathBuf> = Vec::new();
    for entry in &installed {
        let item = exact(entry, &["path", "sha256"], "installed input")?;
        let path = absolute(field(item, "path")?, "installed input path")?;
        hex_digest(field(item, "sha256")?, "installed input digest")?;
        if installed_paths.contains(&path) {
            return fail("installed_inputs contains a duplicate path");
        }
        installed_paths.push(path.clone());
        let value = external(
            &path,
            INSTALLED_LIMIT,
            "installed input",
            "COULD_NOT_RUN",
            false,
        )?
        .expect("a non-optional read returns bytes");
        if Some(hash(&value).as_str()) != field(item, "sha256")?.as_str() {
            return fail(format!(
                "installed input {}: selected SHA-256 mismatch",
                display(&path)?
            ));
        }
        sources.push(("installed".to_owned(), display(&path)?, hash(&value)));
    }
    let checkpoint = relative(field(&session, "checkpoint_path")?, "checkpoint_path")?;
    let receipt = absolute(field(&session, "receipt_path")?, "receipt_path")?;
    if within(&receipt, &project) {
        return fail("receipt_path must be outside the writable project");
    }
    let mut outputs = Map::new();
    for entry in field(&contract.value, "outputs")?
        .as_array()
        .map_or_else(delegate, Ok)?
    {
        let path = entry
            .get("path")
            .and_then(Value::as_str)
            .map_or_else(delegate, Ok)?;
        outputs.insert(path.to_owned(), entry.clone());
    }
    let Some(baselines) = field(&session, "output_baselines")?.as_array().cloned() else {
        return fail("output_baselines must select each delivery output exactly once");
    };
    if baselines.len() != outputs.len() {
        return fail("output_baselines must select each delivery output exactly once");
    }
    let mut baseline_map = Map::new();
    let mut archives: Vec<String> = Vec::new();
    for entry in &baselines {
        let item = exact(
            entry,
            &["path", "sha256", "archive", "allow_unchanged"],
            "output baseline",
        )?;
        let rel = relative(field(item, "path")?, "baseline path")?;
        if !outputs.contains_key(&rel) || baseline_map.contains_key(&rel) {
            return fail("output_baselines contains an unselected or duplicate output");
        }
        if !field(item, "allow_unchanged")?.is_boolean() {
            return fail("output baseline allow_unchanged must be boolean");
        }
        if field(item, "sha256")?.is_null() {
            if !field(item, "archive")?.is_null()
                || field(item, "allow_unchanged")? == &Value::Bool(true)
            {
                return fail("an absent output needs archive null and allow_unchanged false");
            }
        } else {
            hex_digest(field(item, "sha256")?, "output baseline digest")?;
            archives.push(relative(field(item, "archive")?, "baseline archive")?);
        }
        baseline_map.insert(rel, entry.clone());
    }
    let mut selected: Vec<String> = Vec::new();
    for entry in field(&contract.value, "inputs")?
        .as_array()
        .map_or_else(delegate, Ok)?
    {
        selected.push(
            entry
                .get("path")
                .and_then(Value::as_str)
                .map_or_else(delegate, Ok)?
                .to_owned(),
        );
    }
    selected.extend(outputs.keys().cloned());
    selected.push(checkpoint.clone());
    selected.extend(archives.iter().cloned());
    let absolute_selected: Vec<PathBuf> = selected.iter().map(|item| project.join(item)).collect();
    for (index, path) in absolute_selected.iter().enumerate() {
        if absolute_selected[..index]
            .iter()
            .any(|other| collide(path, other))
        {
            return fail("input/output/checkpoint/archive paths collide");
        }
    }
    let mut mutable: Vec<PathBuf> = outputs.keys().map(|item| project.join(item)).collect();
    mutable.push(project.join(&checkpoint));
    mutable.extend(archives.iter().map(|item| project.join(item)));
    if installed_paths
        .iter()
        .any(|path| mutable.iter().any(|other| collide(path, other)))
    {
        return fail("installed input overlaps a mutable/checkpoint/archive destination");
    }
    let mut protected = vec![
        project.clone(),
        session_path.to_path_buf(),
        delivery_path.clone(),
        assignment_path.clone(),
        package.to_path_buf(),
    ];
    protected.extend(installed_paths.iter().cloned());
    if protected.iter().any(|path| collide(state_root, path)) {
        return fail("state_root overlaps the project, selected code or fixed input");
    }
    if protected
        .iter()
        .filter(|path| *path != &project)
        .any(|path| collide(&receipt, path))
    {
        return fail("receipt destination overlaps selected code or fixed input");
    }
    if within(&receipt, state_root) {
        let tail = receipt
            .strip_prefix(state_root)
            .map_or_else(|_| delegate(), Ok)?;
        let first = tail.components().next();
        match first {
            None => return fail("receipt destination collides with protected state storage"),
            Some(component) => {
                let name = component.as_os_str().to_string_lossy().into_owned();
                if RESERVED_STATE_ENTRIES.contains(&name.as_str()) {
                    return fail("receipt destination collides with protected state storage");
                }
            }
        }
    } else if collide(&receipt, state_root) {
        return fail("receipt destination is an ancestor of state_root");
    }
    let Some(state_parent) = state_root.parent() else {
        return delegate();
    };
    directory(state_parent, "state_root parent", "COULD_NOT_RUN")?;
    let root = directory(Path::new("/"), "destination root", "COULD_NOT_RUN")?;
    let receipt_text = display(&receipt)?;
    inspect_destination(&root, &receipt_text[1..])?;
    if !within(&receipt, state_root) {
        let Some(receipt_parent) = receipt.parent() else {
            return delegate();
        };
        directory(receipt_parent, "receipt parent", "COULD_NOT_RUN")?;
    }
    prepare(&delivery_path)?;
    let project_dir = directory(&project, "project_root", "COULD_NOT_RUN")?;
    inspect_destination(&project_dir, &checkpoint)?;
    for entry in field(&contract.value, "inputs")?
        .as_array()
        .map_or_else(delegate, Ok)?
    {
        let rel = entry
            .get("path")
            .and_then(Value::as_str)
            .map_or_else(delegate, Ok)?;
        let value = core_read_at(
            &project_dir,
            rel,
            INPUT_LIMIT,
            "fixed delivery input",
            "COULD_NOT_RUN",
        )?;
        let expected = entry
            .get("sha256")
            .and_then(Value::as_str)
            .map_or_else(delegate, Ok)?;
        if hash(&value) != expected {
            return fail("fixed delivery input changed during preparation");
        }
        sources.push((
            "input".to_owned(),
            display(&project.join(rel))?,
            hash(&value),
        ));
    }
    for entry in &baselines {
        let item = object(entry, "output baseline")?;
        if field(item, "archive")?.is_null() {
            continue;
        }
        let archive = field(item, "archive")?
            .as_str()
            .map_or_else(delegate, Ok)?
            .to_owned();
        inspect_destination(&project_dir, &archive)?;
        let value = read_at(
            &project_dir,
            &archive,
            PREIMAGE_LIMIT,
            "preserved output archive",
            "FAIL",
            false,
        )?
        .expect("a non-optional read returns bytes");
        if Some(hash(&value).as_str()) != field(item, "sha256")?.as_str() {
            return fail(format!(
                "archive {archive}: collision or changed preserved bytes"
            ));
        }
    }
    Ok(Configuration {
        session,
        raw,
        delivery: contract.value,
        project,
        receipt,
        sources,
        outputs,
        baselines: baseline_map,
    })
}

fn display(path: &Path) -> R<String> {
    path.to_str().map(str::to_owned).map_or_else(delegate, Ok)
}

// ---------------------------------------------------------------------------
// phase_state._State
// ---------------------------------------------------------------------------

#[derive(Default)]
pub(crate) struct Info {
    task_id: Option<String>,
    phase: Option<String>,
    applicability: Option<Value>,
}

struct State {
    root: PathBuf,
    manifest: Map<String, Value>,
    task: String,
    mode: String,
    phases: &'static [&'static str],
    started: i128,
    deadline: i128,
    head_records: Vec<String>,
    blobs: BTreeMap<String, Vec<u8>>,
    outputs: Map<String, Value>,
    reference_v2: bool,
    primary_ledger: Option<String>,
    records: BTreeMap<String, Map<String, Value>>,
    phase: String,
    status: String,
    challenge: Option<String>,
    corrections: i64,
    issues: Vec<Value>,
    pending: Option<Map<String, Value>>,
    focus_outputs: Option<Value>,
    intent: Option<Map<String, Value>>,
    receipt: Option<Map<String, Value>>,
    record_outputs: Option<Map<String, Value>>,
    nonces: BTreeSet<String>,
}

fn applicability(mode: &str) -> Value {
    let mut map = Map::new();
    for phase in ALL_PHASES {
        let value = if mode == "handoff-only" && matches!(*phase, "Explore" | "Record") {
            "NOT_APPLICABLE"
        } else {
            "REQUIRED"
        };
        map.insert((*phase).to_owned(), Value::String(value.to_owned()));
    }
    Value::Object(map)
}

fn validate_ref(value: &Value, blobs: &BTreeMap<String, Vec<u8>>) -> R<()> {
    let reference = exact(value, REF_KEYS, "snapshot reference")?;
    let digest = hex_digest(field(reference, "sha256")?, "snapshot digest")?;
    if field(reference, "path")?.as_str() != Some(&format!("snapshots/{digest}.bin")) {
        return fail("snapshot locator does not match its digest");
    }
    let bytes = field(reference, "bytes")?;
    match bytes.as_i64() {
        // CPython's `type(x) is int` accepts an arbitrarily large integer and
        // fails later at the blob length comparison. Without serde_json's
        // `arbitrary_precision` (which would move `Cargo.lock`) an out-of-range
        // integer and a float are the same `Number`, so both are delegated.
        Some(count) if count >= 0 => {}
        Some(_) => return fail("snapshot byte count must be a nonnegative integer"),
        None if bytes.is_number() => return delegate(),
        None => return fail("snapshot byte count must be a nonnegative integer"),
    }
    if !field(reference, "kind")?.is_string() || !field(reference, "source")?.is_string() {
        return fail("snapshot kind/source must be strings");
    }
    match blobs.get(&digest) {
        Some(raw) if Some(raw.len() as i64) == bytes.as_i64() => Ok(()),
        _ => fail("protected snapshot is missing or its byte count changed"),
    }
}

impl State {
    fn load(root: &Path, dir: &Dir, info: &mut Info) -> R<State> {
        let manifest_raw = read_at(
            dir,
            "MANIFEST.json",
            STATE_JSON_LIMIT,
            "protected manifest",
            "FAIL",
            false,
        )?
        .expect("a non-optional read returns bytes");
        let parsed = parse_json(&manifest_raw, "protected manifest")?;
        let manifest = exact(&parsed, MANIFEST_KEYS, "protected manifest")?.clone();
        if field(&manifest, "schema_version")?.as_str() != Some(STATE_SCHEMA)
            || field(&manifest, "state_root")?.as_str() != Some(&display(root)?)
        {
            return fail("protected state schema/root binding mismatch");
        }
        let task = text(field(&manifest, "task_id")?, "protected task_id")?;
        let mode = field(&manifest, "mode")?
            .as_str()
            .unwrap_or_default()
            .to_owned();
        let phases: &'static [&'static str] = match mode.as_str() {
            "brainstorm" => BRAINSTORM_PHASES,
            "handoff-only" => HANDOFF_PHASES,
            _ => return fail("protected state mode/provider is invalid"),
        };
        if !matches!(
            field(&manifest, "provider")?.as_str(),
            Some("codex") | Some("claude")
        ) {
            return fail("protected state mode/provider is invalid");
        }
        let started = stamp(field(&manifest, "started_at_utc")?, "protected start")?;
        let deadline = stamp(field(&manifest, "deadline_utc")?, "protected deadline")?;
        if started >= deadline {
            return fail("protected initialization was not admitted before its deadline");
        }
        absolute(field(&manifest, "session_path")?, "protected session path")?;
        absolute(field(&manifest, "project_root")?, "protected project root")?;
        hex_digest(
            field(&manifest, "session_sha256")?,
            "protected session digest",
        )?;
        let head_raw = read_at(
            dir,
            "HEAD.json",
            STATE_JSON_LIMIT,
            "protected HEAD",
            "FAIL",
            false,
        )?
        .expect("a non-optional read returns bytes");
        let parsed_head = parse_json(&head_raw, "protected HEAD")?;
        let head = exact(
            &parsed_head,
            &["schema_version", "manifest_sha256", "records"],
            "protected HEAD",
        )?
        .clone();
        if field(&head, "schema_version")?.as_str() != Some(HEAD_SCHEMA)
            || field(&head, "manifest_sha256")?.as_str() != Some(&hash(&manifest_raw))
        {
            return fail("protected manifest/HEAD binding mismatch");
        }
        let Some(entries) = field(&head, "records")?
            .as_array()
            .filter(|items| !items.is_empty())
        else {
            return fail("protected HEAD has no admitted journal");
        };
        let mut head_records = Vec::new();
        for entry in entries {
            head_records.push(hex_digest(entry, "journal digest")?);
        }
        if head_records.iter().collect::<BTreeSet<_>>().len() != head_records.len() {
            return fail("protected HEAD repeats a record");
        }
        let blobs = object_directory(dir, "snapshots", ".bin", INSTALLED_LIMIT)?;
        let Some(snapshots) = field(&manifest, "snapshots")?.as_array().cloned() else {
            return fail("protected manifest snapshots must be an array");
        };
        for reference in &snapshots {
            validate_ref(reference, &blobs)?;
        }
        let delivery_refs: Vec<&Value> = snapshots
            .iter()
            .filter(|reference| reference.get("kind") == Some(&json!("delivery")))
            .collect();
        if delivery_refs.len() != 1 {
            return fail("protected delivery snapshot is missing or ambiguous");
        }
        let digest = delivery_refs[0]
            .get("sha256")
            .and_then(Value::as_str)
            .map_or_else(delegate, Ok)?;
        let blob = blobs.get(digest).map_or_else(delegate, Ok)?;
        let delivery = parse_json(blob, "delivery snapshot")?;
        let Some(specs) = delivery
            .as_object()
            .and_then(|map| map.get("outputs"))
            .and_then(Value::as_array)
        else {
            return fail("protected delivery snapshot has invalid output specifications");
        };
        let mut outputs = Map::new();
        for item in specs {
            let path = item
                .get("path")
                .and_then(Value::as_str)
                .map_or_else(delegate, Ok)?;
            outputs.insert(path.to_owned(), item.clone());
        }
        let reference_v2 = delivery.get("schema_version") == Some(&json!(V2));
        let primary_ledger = delivery
            .get("primary_ledger_path")
            .and_then(Value::as_str)
            .map(str::to_owned);
        let objects = object_directory(dir, "records", ".json", STATE_JSON_LIMIT)?;
        let mut records = BTreeMap::new();
        for (digest, raw) in &objects {
            let parsed = parse_json(raw, "protected journal record")?;
            let record = exact(&parsed, RECORD_KEYS, "protected journal record")?.clone();
            let sequence = field(&record, "sequence")?;
            let operation = field(&record, "operation")?.as_str().unwrap_or_default();
            if field(&record, "schema_version")?.as_str() != Some(OPERATION_SCHEMA)
                || !OPERATIONS.contains(&operation)
                || sequence.is_boolean()
                || !sequence.is_i64()
                || sequence.as_i64().is_some_and(|value| value < 0)
                || !field(&record, "data")?.is_object()
                || !field(&record, "snapshots")?.is_array()
            {
                return fail("protected journal record has malformed fields");
            }
            stamp(field(&record, "at_utc")?, "journal timestamp")?;
            if !field(&record, "previous")?.is_null() {
                hex_digest(field(&record, "previous")?, "prior journal digest")?;
            }
            for reference in field(&record, "snapshots")?
                .as_array()
                .map_or_else(delegate, Ok)?
            {
                validate_ref(reference, &blobs)?;
            }
            records.insert(digest.clone(), record);
        }
        let mut state = State {
            root: root.to_path_buf(),
            manifest,
            task,
            mode,
            phases,
            started,
            deadline,
            head_records,
            blobs,
            outputs,
            reference_v2,
            primary_ledger,
            records,
            phase: "Recover".to_owned(),
            status: "ACTIVE".to_owned(),
            challenge: None,
            corrections: 0,
            issues: Vec::new(),
            pending: None,
            focus_outputs: None,
            intent: None,
            receipt: None,
            record_outputs: None,
            nonces: BTreeSet::new(),
        };
        for index in 0..state.head_records.len() {
            let digest = state.head_records[index].clone();
            let Some(record) = state.records.get(&digest).cloned() else {
                return fail("protected committed journal record is missing");
            };
            let previous = if index == 0 {
                Value::Null
            } else {
                Value::String(state.head_records[index - 1].clone())
            };
            if field(&record, "sequence")?.as_i64() != Some(index as i64)
                || field(&record, "previous")? != &previous
            {
                return fail("protected journal sequence/hash chain mismatch");
            }
            state.apply(&record, &digest)?;
        }
        info.task_id = Some(state.task.clone());
        info.phase = Some(state.phase.clone());
        info.applicability = Some(applicability(&state.mode));
        Ok(state)
    }

    fn take_challenge(&mut self, value: &Value) -> R<()> {
        let issued = nonce(value, "challenge")?;
        if self.nonces.contains(&issued) {
            return fail("protected journal reused an admission challenge");
        }
        self.nonces.insert(issued.clone());
        self.challenge = Some(issued);
        Ok(())
    }

    fn binds_current(&self, data: &Map<String, Value>) -> R<()> {
        let phase = data.get("phase").cloned().unwrap_or(Value::Null);
        let challenge = data.get("challenge").cloned().unwrap_or(Value::Null);
        let expected = self.challenge.clone().map_or(Value::Null, Value::String);
        if phase != json!(self.phase) || challenge != expected {
            return fail("protected journal transition does not bind its current admission");
        }
        Ok(())
    }

    fn checkpoint_ref(&self, record: &Map<String, Value>, expected: &str) -> R<Map<String, Value>> {
        let snapshots = field(record, "snapshots")?
            .as_array()
            .map_or_else(delegate, Ok)?;
        let refs: Vec<&Value> = snapshots
            .iter()
            .filter(|item| item.get("kind") == Some(&json!("checkpoint")))
            .collect();
        if refs.len() != 1
            || field(record, "data")?.get("checkpoint_sha256") != refs[0].get("sha256")
        {
            return fail("protected transition checkpoint binding is missing");
        }
        let digest = refs[0]
            .get("sha256")
            .and_then(Value::as_str)
            .map_or_else(delegate, Ok)?;
        let raw = self.blobs.get(digest).map_or_else(delegate, Ok)?;
        let checkpoint = self.parse_checkpoint(raw)?;
        if field(&checkpoint, "state")?.as_str() != Some(expected) {
            return fail("protected transition used the wrong checkpoint state");
        }
        Ok(checkpoint)
    }

    /// `phase_state._checkpoint`.
    fn parse_checkpoint(&self, raw: &[u8]) -> R<Map<String, Value>> {
        if raw.is_empty() || raw.len() as u64 > CHECKPOINT_LIMIT {
            return fail("checkpoint must contain 1 to 65536 bytes");
        }
        let parsed = parse_json(raw, "checkpoint")?;
        let value = exact(
            &parsed,
            &[
                "schema_version",
                "task_id",
                "phase",
                "challenge",
                "state",
                "content",
            ],
            "checkpoint",
        )?
        .clone();
        if field(&value, "schema_version")?.as_str() != Some(CHECKPOINT_SCHEMA) {
            return fail("checkpoint: unsupported schema_version");
        }
        let challenge = self.challenge.clone().map_or(Value::Null, Value::String);
        if field(&value, "task_id")? != &json!(self.task)
            || field(&value, "phase")? != &json!(self.phase)
            || field(&value, "challenge")? != &challenge
        {
            return fail(
                "checkpoint task/phase/challenge mismatch or replay; current admission is required",
            );
        }
        let content = field(&value, "content")?.clone();
        let state = field(&value, "state")?
            .as_str()
            .unwrap_or_default()
            .to_owned();
        if state == "awaiting_user" {
            let fields = exact(
                &content,
                &["question", "blocking_dependency"],
                "awaiting_user content",
            )?;
            for key in fields.keys() {
                bounded_string(field(fields, key)?, key)?;
            }
        } else if state != "ready" {
            return fail("checkpoint state must be ready or awaiting_user");
        } else if self.phase == "Recover" {
            let fields = exact(
                &content,
                &["known_ideas", "known_decisions", "missing_inputs"],
                "Recover content",
            )?;
            let mut total = 0;
            for key in fields.keys() {
                total += bounded_strings(field(fields, key)?, key)?.len();
            }
            if total == 0 {
                return fail("Recover must record at least one known item or missing input");
            }
        } else if self.phase == "Explore" {
            let fields = exact(&content, &["ideas", "missing_inputs"], "Explore content")?;
            bounded_strings(field(fields, "missing_inputs")?, "missing_inputs")?;
            let Some(ideas) = field(fields, "ideas")?
                .as_array()
                .filter(|items| (1..=100).contains(&items.len()))
            else {
                return fail("Explore ideas must contain 1 to 100 ideas");
            };
            let mut seen: BTreeSet<String> = BTreeSet::new();
            for idea in ideas {
                let item = exact(
                    idea,
                    &[
                        "idea_id",
                        "people",
                        "problem",
                        "outcome",
                        "alternatives",
                        "open_questions",
                    ],
                    "idea",
                )?;
                let identity = bounded_string(field(item, "idea_id")?, "idea_id")?;
                if !seen.insert(identity) {
                    return fail("Explore contains a duplicate idea_id");
                }
                bounded_strings(field(item, "alternatives")?, "alternatives")?;
                let questions = bounded_strings(field(item, "open_questions")?, "open_questions")?;
                for key in ["people", "problem", "outcome"] {
                    if field(item, key)?.is_null() {
                        if questions.is_empty() {
                            return fail(
                                "unknown people/problem/outcome requires an open question",
                            );
                        }
                    } else {
                        bounded_string(field(item, key)?, key)?;
                    }
                }
            }
        } else if self.phase == "Record" {
            let fields = exact(&content, &["ledger_path"], "Record content")?;
            let expected = match &self.primary_ledger {
                Some(path) => Some(path.clone()),
                None => self.ledger_output()?,
            };
            let declared = field(fields, "ledger_path")?;
            let matched = expected
                .as_deref()
                .is_some_and(|path| declared == &json!(path));
            if !matched {
                return fail("Record ledger_path must equal the selected idea-ledger output");
            }
        } else if self.phase == "Focus" {
            let fields = exact(
                &content,
                &[
                    "next_action",
                    "owner",
                    "completion_evidence",
                    "non_goals",
                    "handoff_path",
                ],
                "Focus content",
            )?;
            for key in ["next_action", "owner", "completion_evidence"] {
                bounded_string(field(fields, key)?, key)?;
            }
            bounded_strings(field(fields, "non_goals")?, "non_goals")?;
            let Some(expected) = self.role_output("handoff")? else {
                return delegate();
            };
            if field(fields, "handoff_path")? != &json!(expected) {
                return fail("Focus handoff_path must equal the selected handoff output");
            }
        } else {
            return fail("checkpoint phase is unsupported");
        }
        Ok(value)
    }

    fn ledger_output(&self) -> R<Option<String>> {
        self.role_output("idea-ledger")
    }

    fn role_output(&self, role: &str) -> R<Option<String>> {
        for (path, spec) in &self.outputs {
            let observed = spec
                .as_object()
                .and_then(|map| map.get("artifact_type"))
                .map_or_else(delegate, Ok)?;
            if observed == &json!(role) {
                return Ok(Some(path.clone()));
            }
        }
        Ok(None)
    }

    fn apply(&mut self, record: &Map<String, Value>, digest: &str) -> R<()> {
        let operation = field(record, "operation")?
            .as_str()
            .unwrap_or_default()
            .to_owned();
        let data = object(field(record, "data")?, "protected journal record")?.clone();
        let at = stamp(field(record, "at_utc")?, "journal timestamp")?;
        if at < self.started {
            return fail("journal timestamp precedes task initialization");
        }
        let sequence = field(record, "sequence")?.as_i64().unwrap_or(-1);
        if operation == "start" {
            exact(field(record, "data")?, &["challenge"], "start operation")?;
            if sequence != 0 || at >= self.deadline {
                return fail("start must be the first pre-deadline journal operation");
            }
            self.take_challenge(field(&data, "challenge")?)?;
            return Ok(());
        }
        if sequence == 0 || (self.challenge.is_none() && self.status == "ACTIVE") {
            return fail("protected journal lacks initialization");
        }
        if matches!(
            operation.as_str(),
            "accepted" | "waiting_user" | "correction" | "resume"
        ) {
            let admitted = if operation == "resume" {
                matches!(self.status.as_str(), "ACTIVE" | "WAITING_USER")
            } else {
                self.status == "ACTIVE"
            };
            if !admitted {
                return fail("protected operation is not admitted in its prior state");
            }
            self.binds_current(&data)?;
            if at >= self.deadline {
                return fail("journal admits a phase operation after the fixed deadline");
            }
        }
        match operation.as_str() {
            "accepted" => self.apply_accepted(record, &data)?,
            "waiting_user" => {
                exact(
                    field(record, "data")?,
                    &["phase", "challenge", "checkpoint_sha256"],
                    "waiting_user operation",
                )?;
                let checkpoint = self.checkpoint_ref(record, "awaiting_user")?;
                self.pending = Some(object(field(&checkpoint, "content")?, "checkpoint")?.clone());
                self.status = "WAITING_USER".to_owned();
            }
            "resume" => {
                exact(
                    field(record, "data")?,
                    &["phase", "challenge", "next_challenge"],
                    "resume operation",
                )?;
                self.take_challenge(field(&data, "next_challenge")?)?;
                self.pending = None;
                self.status = "ACTIVE".to_owned();
            }
            "correction" => {
                exact(
                    field(record, "data")?,
                    &["phase", "challenge", "corrections", "issues", "terminal"],
                    "correction operation",
                )?;
                let count = field(&data, "corrections")?;
                let terminal = field(&data, "terminal")?;
                if count.is_boolean()
                    || count.as_i64() != Some(self.corrections + 1)
                    || count.as_i64().is_some_and(|value| value > 2)
                    || !terminal.is_boolean()
                    || terminal != &json!(count.as_i64() == Some(2))
                {
                    return fail("protected correction counter/termination is malformed");
                }
                let issues = field(&data, "issues")?.clone();
                let valid = issues.as_array().is_some_and(|items| {
                    (1..=100).contains(&items.len())
                        && items.iter().all(|item| {
                            item.as_str().is_some_and(|text| {
                                !python_strip(text).is_empty() && text.len() <= 32768
                            })
                        })
                });
                if !valid {
                    return fail("correction record has no failure evidence");
                }
                self.issues = issues.as_array().cloned().unwrap_or_default();
                self.corrections = count.as_i64().unwrap_or_default();
                if terminal == &json!(true) {
                    self.status = "FAIL".to_owned();
                }
            }
            "completion_intent" => {
                exact(
                    field(record, "data")?,
                    &["receipt_path", "output_hashes", "delivery_contract_sha256"],
                    "completion intent",
                )?;
                if self.status != "READY" || self.intent.is_some() || at >= self.deadline {
                    return fail("completion intent is not a single pre-deadline READY operation");
                }
                if Some(field(&data, "output_hashes")?) != self.focus_outputs.as_ref() {
                    return fail("completion intent differs from the accepted Focus outputs");
                }
                absolute(field(&data, "receipt_path")?, "intent receipt path")?;
                hex_digest(
                    field(&data, "delivery_contract_sha256")?,
                    "intent contract digest",
                )?;
                let mut intent = data.clone();
                intent.insert("record_sha256".to_owned(), json!(digest));
                intent.insert("at_utc".to_owned(), field(record, "at_utc")?.clone());
                self.intent = Some(intent);
            }
            "completed" => {
                exact(
                    field(record, "data")?,
                    &[
                        "intent_sha256",
                        "receipt_path",
                        "receipt_sha256",
                        "receipt_readback_at_utc",
                    ],
                    "completed operation",
                )?;
                let Some(intent) = self.intent.clone() else {
                    return fail("completed record lacks a protected READY completion intent");
                };
                if self.status != "READY" {
                    return fail("completed record lacks a protected READY completion intent");
                }
                if field(&data, "intent_sha256")? != field(&intent, "record_sha256")?
                    || field(&data, "receipt_path")? != field(&intent, "receipt_path")?
                {
                    return fail("completed record is bound to another intent/receipt");
                }
                hex_digest(field(&data, "receipt_sha256")?, "completed receipt digest")?;
                if stamp(field(&data, "receipt_readback_at_utc")?, "receipt readback")? != at {
                    return fail(
                        "completed readback timestamp differs from its actual operation record",
                    );
                }
                let snapshots = field(record, "snapshots")?
                    .as_array()
                    .map_or_else(delegate, Ok)?;
                let refs: Vec<&Value> = snapshots
                    .iter()
                    .filter(|item| item.get("kind") == Some(&json!("receipt")))
                    .collect();
                if refs.len() != 1 || refs[0].get("sha256") != data.get("receipt_sha256") {
                    return fail("completed receipt snapshot binding is missing");
                }
                self.receipt = Some(data.clone());
                self.status = "COMPLETED".to_owned();
            }
            _ => return fail("unknown protected operation"),
        }
        Ok(())
    }

    fn apply_accepted(&mut self, record: &Map<String, Value>, data: &Map<String, Value>) -> R<()> {
        exact(
            field(record, "data")?,
            &[
                "phase",
                "challenge",
                "checkpoint_sha256",
                "next_phase",
                "next_challenge",
                "output_hashes",
            ],
            "accepted operation",
        )?;
        self.checkpoint_ref(record, "ready")?;
        let Some(hashes) = field(data, "output_hashes")?.as_object().cloned() else {
            return fail("accepted output hashes must be a mapping");
        };
        let mut expected: BTreeSet<String> = BTreeSet::new();
        if self.phase == "Focus" {
            expected.extend(self.outputs.keys().cloned());
        } else if self.phase == "Record" {
            for (path, spec) in &self.outputs {
                let role = spec
                    .as_object()
                    .and_then(|map| map.get("artifact_type"))
                    .map_or_else(delegate, Ok)?;
                if role == &json!("idea-ledger") {
                    expected.insert(path.clone());
                }
            }
        }
        let mut actual = Map::new();
        for reference in field(record, "snapshots")?
            .as_array()
            .map_or_else(delegate, Ok)?
        {
            if reference.get("kind") == Some(&json!("output")) {
                let source = reference
                    .get("source")
                    .and_then(Value::as_str)
                    .map_or_else(delegate, Ok)?;
                let digest = reference.get("sha256").map_or_else(delegate, Ok)?;
                actual.insert(source.to_owned(), digest.clone());
            }
        }
        let declared: BTreeSet<String> = hashes.keys().cloned().collect();
        if declared != expected || Value::Object(actual.clone()) != Value::Object(hashes.clone()) {
            return fail("accepted artifact snapshots/output hashes do not match phase coverage");
        }
        let reports: Vec<&Value> = field(record, "snapshots")?
            .as_array()
            .map_or_else(delegate, Ok)?
            .iter()
            .filter(|item| item.get("kind") == Some(&json!("reference_coverage")))
            .collect();
        if self.reference_v2 && matches!(self.phase.as_str(), "Record" | "Focus") {
            return delegate();
        }
        if !reports.is_empty() {
            return fail("unexpected reference coverage snapshot for this contract or phase");
        }
        if self.phase == "Record" {
            self.record_outputs = Some(actual);
        }
        let position = self
            .phases
            .iter()
            .position(|phase| *phase == self.phase)
            .map_or_else(delegate, Ok)?;
        self.issues = Vec::new();
        self.pending = None;
        if position == self.phases.len() - 1 {
            if field(data, "next_phase")? != &json!("Focus")
                || !field(data, "next_challenge")?.is_null()
            {
                return fail("final phase cannot issue another admission");
            }
            self.focus_outputs = Some(field(data, "output_hashes")?.clone());
            self.status = "READY".to_owned();
            self.challenge = None;
        } else {
            if field(data, "next_phase")? != &json!(self.phases[position + 1]) {
                return fail("protected transition skips a required phase");
            }
            self.phase = self.phases[position + 1].to_owned();
            self.take_challenge(field(data, "next_challenge")?)?;
            self.corrections = 0;
        }
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// phase_state._verify_current and _view
// ---------------------------------------------------------------------------

fn verify_current(state: &State, package: &Path) -> R<Configuration> {
    let session_path = PathBuf::from(
        field(&state.manifest, "session_path")?
            .as_str()
            .map_or_else(delegate, Ok)?,
    );
    let cfg = configuration(&session_path, &state.root, package)?;
    let manifest = &state.manifest;
    let session = &cfg.session;
    if field(manifest, "session_sha256")?.as_str() != Some(&hash(&cfg.raw))
        || field(manifest, "task_id")? != field(session, "task_id")?
        || field(manifest, "project_root")?.as_str() != Some(&display(&cfg.project)?)
        || field(manifest, "mode")? != field(&cfg.delivery, "mode")?
        || field(manifest, "provider")? != field(session, "provider")?
        || field(manifest, "deadline_utc")? != field(session, "deadline_utc")?
    {
        return fail("current session contract differs from protected initialization");
    }
    let mut expected: BTreeMap<(String, String), String> = BTreeMap::new();
    for (kind, source, digest) in &cfg.sources {
        expected.insert((kind.clone(), source.clone()), digest.clone());
    }
    let snapshots = field(manifest, "snapshots")?
        .as_array()
        .map_or_else(delegate, Ok)?;
    let mut actual: BTreeMap<(String, String), String> = BTreeMap::new();
    let mut counted = 0;
    for reference in snapshots {
        let kind = reference
            .get("kind")
            .and_then(Value::as_str)
            .map_or_else(delegate, Ok)?;
        if kind == "preimage" {
            continue;
        }
        counted += 1;
        let source = reference
            .get("source")
            .and_then(Value::as_str)
            .map_or_else(delegate, Ok)?;
        let digest = reference
            .get("sha256")
            .and_then(Value::as_str)
            .map_or_else(delegate, Ok)?;
        actual.insert((kind.to_owned(), source.to_owned()), digest.to_owned());
    }
    if actual.len() != counted || actual != expected {
        return fail("fixed selected source identities differ from their protected snapshots");
    }
    let mut preimages: BTreeMap<String, String> = BTreeMap::new();
    for reference in snapshots {
        if reference.get("kind") == Some(&json!("preimage")) {
            let source = reference
                .get("source")
                .and_then(Value::as_str)
                .map_or_else(delegate, Ok)?;
            let digest = reference
                .get("sha256")
                .and_then(Value::as_str)
                .map_or_else(delegate, Ok)?;
            preimages.insert(source.to_owned(), digest.to_owned());
        }
    }
    let mut expected_preimages: BTreeMap<String, String> = BTreeMap::new();
    for (path, item) in &cfg.baselines {
        let digest = item.get("sha256").map_or_else(delegate, Ok)?;
        if let Some(text) = digest.as_str() {
            expected_preimages.insert(path.clone(), text.to_owned());
        }
    }
    if preimages != expected_preimages {
        return fail("protected preimage coverage differs from the selected output baselines");
    }
    if state.outputs != cfg.outputs {
        return fail("protected output selection differs from the fixed delivery contract");
    }
    if state.reference_v2 && state.record_outputs.is_some() {
        return delegate();
    }
    let receipt = external(
        &cfg.receipt,
        RECEIPT_LIMIT,
        "receipt",
        "COULD_NOT_RUN",
        true,
    )?;
    if receipt.is_some() && state.intent.is_none() {
        return fail("existing receipt has no matching protected completion intent");
    }
    if let Some(intent) = &state.intent {
        if field(intent, "receipt_path")?.as_str() != Some(&display(&cfg.receipt)?)
            || field(intent, "delivery_contract_sha256")?
                != field(session, "delivery_contract_sha256")?
        {
            return fail("protected completion intent differs from the selected receipt/contract");
        }
    }
    Ok(cfg)
}

fn result_value(status: &str, info: &Info, issues: Vec<Value>) -> Value {
    let mut map = Map::new();
    map.insert("status".to_owned(), json!(status));
    map.insert("issues".to_owned(), Value::Array(issues));
    map.insert("scope".to_owned(), json!(SCOPE));
    map.insert("native_completion".to_owned(), json!("NOT_EVALUATED"));
    if let Some(task) = &info.task_id {
        map.insert("task_id".to_owned(), json!(task));
    }
    if let Some(phase) = &info.phase {
        map.insert("phase".to_owned(), json!(phase));
    }
    if let Some(value) = &info.applicability {
        map.insert("phase_applicability".to_owned(), value.clone());
    }
    Value::Object(map)
}

/// `phase_state._view`.
fn view(state: &State) -> R<Map<String, Value>> {
    let info = Info {
        task_id: Some(state.task.clone()),
        phase: Some(state.phase.clone()),
        applicability: Some(applicability(&state.mode)),
    };
    let mut value = result_value(&state.status, &info, state.issues.clone())
        .as_object()
        .cloned()
        .expect("result_value builds an object");
    value.insert("corrections".to_owned(), json!(state.corrections));
    value.insert("terminal".to_owned(), json!(state.status == "FAIL"));
    value.insert(
        "deadline_utc".to_owned(),
        field(&state.manifest, "deadline_utc")?.clone(),
    );
    if state.mode == "handoff-only" {
        value.insert(
            "not_applicable".to_owned(),
            json!({"Explore": "handoff-only", "Record": "handoff-only"}),
        );
    }
    if matches!(state.status.as_str(), "ACTIVE" | "WAITING_USER") {
        value.insert(
            "challenge".to_owned(),
            state.challenge.clone().map_or(Value::Null, Value::String),
        );
        let mut instructions = format!(
            "Write the selected JSON checkpoint for {} using this task and challenge; \
record actual evidence and explicit unknowns. Runtime performs mechanical checks.",
            state.phase
        );
        if !state.issues.is_empty() {
            instructions
                .push_str(" Correct the reported checkpoint issues; no phase has advanced.");
        }
        value.insert("instructions".to_owned(), json!(instructions));
    }
    if let Some(pending) = &state.pending {
        value.insert("question".to_owned(), field(pending, "question")?.clone());
        value.insert(
            "blocking_dependency".to_owned(),
            field(pending, "blocking_dependency")?.clone(),
        );
    }
    if let Some(receipt) = &state.receipt {
        value.insert(
            "receipt_path".to_owned(),
            field(receipt, "receipt_path")?.clone(),
        );
        value.insert(
            "receipt_sha256".to_owned(),
            field(receipt, "receipt_sha256")?.clone(),
        );
        value.insert("receipt_published".to_owned(), json!(true));
        value.insert("receipt_readback".to_owned(), json!(true));
        value.insert("receipt_verified".to_owned(), json!(true));
        value.insert(
            "receipt_readback_at_utc".to_owned(),
            field(receipt, "receipt_readback_at_utc")?.clone(),
        );
    }
    Ok(value)
}

// ---------------------------------------------------------------------------
// The exclusive lock
// ---------------------------------------------------------------------------

/// `phase_state._lock`: the same open, the same checks, the same acquisition.
///
/// The returned `File` owns the open file description that holds
/// `LOCK_EX | LOCK_NB`; the lock is released when it is dropped, which happens
/// on every exit path from [`context`], including the paths that hand the call
/// back to the legacy controller so that it can take the same lock.
fn lock(root: &Path) -> R<(Dir, fs::File)> {
    let dir = directory(root, "protected state_root", "FAIL")?;
    let path = dir.path("LOCK");
    let before = fs::symlink_metadata(&path)
        .map_err(|error| os_problem(&error, "protected state lock", "FAIL"))?;
    if !before.file_type().is_file() || before.nlink() != 1 || before.size() != 0 {
        return fail("protected state lock is not an empty regular single-link file");
    }
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(O_NOFOLLOW | O_NONBLOCK)
        .open(&path)
        .map_err(|error| os_problem(&error, "protected state lock", "FAIL"))?;
    let opened = file
        .metadata()
        .map_err(|error| os_problem(&error, "protected state lock", "FAIL"))?;
    if identity(&before) != identity(&opened) {
        return fail("protected lock identity changed");
    }
    acquire(&file)?;
    let current = fs::symlink_metadata(&path)
        .map_err(|error| os_problem(&error, "protected state lock", "FAIL"))?;
    if identity(&before) != identity(&current) {
        return fail("protected lock was replaced");
    }
    Ok((dir, file))
}

/// `fcntl.flock(descriptor, LOCK_EX | LOCK_NB)` with the legacy refusals.
///
/// CPython retries an interrupted `flock` (PEP 475), reports `EWOULDBLOCK` as
/// `BlockingIOError`, which `_lock` converts to its own operational refusal,
/// and lets every other `OSError` fall through to `_os_problem`.
fn acquire(file: &fs::File) -> R<()> {
    loop {
        return match rustix::fs::flock(file, rustix::fs::FlockOperation::NonBlockingLockExclusive) {
            Ok(()) => Ok(()),
            Err(rustix::io::Errno::INTR) => continue,
            Err(rustix::io::Errno::WOULDBLOCK) => problem(
                "COULD_NOT_RUN",
                "another protected phase operation owns the exclusive lock".to_owned(),
            ),
            Err(errno) => Err(os_problem(
                &std::io::Error::from_raw_os_error(errno.raw_os_error()),
                "protected state lock",
                "FAIL",
            )),
        };
    }
}

// ---------------------------------------------------------------------------
// Entry points
// ---------------------------------------------------------------------------

/// `workflow_runtime.engine_for_state` restricted to the phase-state engine.
fn phase_engine(state: &Path) -> R<()> {
    let raw = external(
        &state.join("MANIFEST.json"),
        STATE_JSON_LIMIT,
        "workflow manifest",
        "COULD_NOT_RUN",
        false,
    )?
    .expect("a non-optional read returns bytes");
    let value = parse_json(&raw, "workflow manifest")?;
    match value.get("schema_version").and_then(Value::as_str) {
        Some(STATE_SCHEMA) => Ok(()),
        // The utility engine and the unsupported-schema refusal stay in Python.
        _ => delegate(),
    }
}

/// `phase_state.context` under `_guard`.
fn context(state: &Path, package: &Path, info: &mut Info) -> R<Value> {
    let root = absolute(&json!(display(state)?), "state_root")?;
    // `_held` owns the exclusive flock: it must stay bound until this function
    // returns, on every exit path. Renaming it to `_` would drop it here and
    // silently remove mutual exclusion for the rest of the read.
    let (dir, _held) = lock(&root)?;
    let state = State::load(&root, &dir, info)?;
    if state.reference_v2 {
        return delegate();
    }
    verify_current(&state, package)?;
    let now = now_micros()?;
    if matches!(state.status.as_str(), "COMPLETED" | "READY") {
        // `_verified_receipt`/`_current_final` need delivery_core check/verify.
        return delegate();
    }
    let mut result = view(&state)?;
    let expired = now >= state.deadline;
    result.insert("expired".to_owned(), json!(expired));
    result.insert(
        "admitted".to_owned(),
        json!(state.status == "ACTIVE" && !expired),
    );
    result.insert("persisted_status".to_owned(), json!(state.status));
    if expired {
        result.remove("challenge");
        result.remove("instructions");
        if state.status == "ACTIVE" {
            result.insert("status".to_owned(), json!("COULD_NOT_RUN"));
            let mut issues = state.issues.clone();
            issues.push(json!(
                "original deadline expired; historical context only, no new admission"
            ));
            result.insert("issues".to_owned(), Value::Array(issues));
        }
    }
    Ok(Value::Object(result))
}

/// Answer `devforge delivery status` from Rust, or `None` to use the legacy path.
pub(crate) fn status(state: &Path, package: &Path) -> Option<Value> {
    match phase_engine(state) {
        Ok(()) => {}
        Err(Stop::Delegate) => return None,
        Err(Stop::Problem { result, issue }) => {
            // `controller.main` catches this outside `_guard`: status and issues only.
            return Some(json!({"status": result, "issues": [issue]}));
        }
    }
    let mut info = Info::default();
    match context(state, package, &mut info) {
        Ok(value) => Some(value),
        Err(Stop::Delegate) => None,
        Err(Stop::Problem { result, issue }) => {
            Some(result_value(result, &info, vec![json!(issue)]))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{civil_days, has_placeholder, parse_utc, python_isspace, python_strip};

    #[test]
    fn aware_utc_timestamps_parse_to_their_epoch_microsecond() {
        assert_eq!(parse_utc("1970-01-01T00:00:00+00:00"), Some(0));
        assert_eq!(
            parse_utc("2026-09-11T18:58:12.087953+00:00"),
            Some(1_789_153_092_087_953)
        );
        assert_eq!(
            parse_utc("2026-09-11T18:58:12Z"),
            Some(1_789_153_092_000_000)
        );
    }

    #[test]
    fn naive_or_offset_timestamps_are_left_to_the_legacy_parser() {
        assert_eq!(parse_utc("2026-09-11T18:58:12"), None);
        assert_eq!(parse_utc("2026-09-11T18:58:12+01:00"), None);
        assert_eq!(parse_utc("2026-02-30T00:00:00+00:00"), None);
    }

    #[test]
    fn civil_days_matches_the_proleptic_gregorian_epoch() {
        assert_eq!(civil_days(1970, 1, 1), Some(0));
        assert_eq!(civil_days(2000, 3, 1), Some(11017));
    }

    /// The complete CPython set, from
    /// `/usr/bin/python3 -c 'print([hex(i) for i in range(0x110000) if chr(i).isspace()])'`.
    const CPYTHON_SPACE: [u32; 29] = [
        0x9, 0xa, 0xb, 0xc, 0xd, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680, 0x2000, 0x2001,
        0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029,
        0x202f, 0x205f, 0x3000,
    ];

    #[test]
    fn python_isspace_matches_the_enumerated_cpython_set() {
        for code in 0..0x11_0000u32 {
            let Some(value) = char::from_u32(code) else {
                continue;
            };
            assert_eq!(
                python_isspace(value),
                CPYTHON_SPACE.contains(&code),
                "U+{code:04X} disagrees with CPython str.isspace()"
            );
        }
        // The four characters Rust does not call whitespace at all.
        for code in 0x1c..=0x1fu32 {
            let value = char::from_u32(code).expect("a control character");
            assert!(!value.is_whitespace() && python_isspace(value));
        }
    }

    #[test]
    fn python_strip_removes_what_cpython_str_strip_removes() {
        assert_eq!(python_strip("\u{1c}"), "");
        assert_eq!(python_strip("\u{1f} a \u{1c}"), "a");
        assert_eq!(python_strip(" a "), "a");
        assert_eq!(python_strip("a"), "a");
    }

    #[test]
    fn double_brace_markers_are_detected_without_a_regex_engine() {
        assert!(has_placeholder("a {{ b }} c"));
        assert!(!has_placeholder("a {{ b c"));
        assert!(!has_placeholder("plain"));
    }
}
