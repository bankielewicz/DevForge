//! Black-box acceptance tests for `devforge install probe-runtime`.
//!
//! Compiled Rust owns runtime probing and delivery-capability validation. Every
//! stand-in runtime here is a `/bin/sh` script, so no interpreter outside the
//! evaluation exception participates. A refusal proves the mechanical predicate
//! it names; it is never native activation or human acceptance.
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};

const BIN: &str = env!("CARGO_BIN_EXE_devforge");

/// The exact `devforge delivery capabilities` stdout recorded when the Claude
/// walkthrough installer refused, minus its trailing newline:
/// `worktrees/claude-manual-20260911T015447Z/observations/runtime-capabilities.json`
/// (859 bytes, sha256 e2a664f0e2f6cf0526fb1a767532bf635dcc2563599405278b7d9f60206c8ef6).
const OBSERVED: &str = r#"{"completion_modes":["process","managed-session"],"hook_events":["SessionStart","UserPromptSubmit","Stop","SessionEnd"],"io_modes":["inherited","interactive-tty"],"mechanical_scope":"phase evidence and persisted artifact verification; no semantic acceptance","native_admission":"NOT_VALIDATED","native_execution_enabled":false,"native_process_interface":"EXPLICIT_FROZEN_CONFIGURATION_REQUIRED","native_process_receipt_schema":"devforge.native-process-receipt/v1","native_semantic_review":"SEPARATE_SELECTED_OPERATOR_OR_INDEPENDENT_REVIEW","protocol":"devforge.delivery-runtime/v1","schema_version":"devforge.delivery-capabilities/v1","supported_providers":["codex","claude"],"utility_native_schedule_schema":"devforge.utility-native-schedule/v1","utility_session_schema":"devforge.utility-session/v1","utility_workflows":["skill-builder","skill-validator"]}"#;

const EXTENSION_FIELDS: [&str; 7] = [
    "utility_workflows",
    "utility_session_schema",
    "utility_native_schedule_schema",
    "native_execution_enabled",
    "native_process_interface",
    "native_process_receipt_schema",
    "native_semantic_review",
];
const SCOPE: &str = "phase evidence and persisted artifact verification; no semantic acceptance";

static COUNTER: AtomicU64 = AtomicU64::new(0);

// ---- fixtures ------------------------------------------------------------

struct Temp(PathBuf);
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
impl Temp {
    fn path(&self) -> &Path {
        &self.0
    }
}

fn temp() -> Temp {
    // Fixtures live beside the test binary so hard-link cases share its filesystem.
    let path = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!(
        "devforge-probe-runtime-{}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir_all(&path).unwrap();
    Temp(path)
}

fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn quote(text: &str) -> String {
    format!("'{}'", text.replace('\'', r"'\''"))
}

/// Write an executable `/bin/sh` stand-in runtime.
fn standin(dir: &Path, name: &str, body: &str) -> PathBuf {
    let path = dir.join(name);
    fs::write(&path, format!("#!/bin/sh\n{body}")).unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
    assert_eq!(fs::metadata(&path).unwrap().nlink(), 1);
    path
}

/// A stand-in that prints `payload` on stdout verbatim and exits 0.
fn serving(dir: &Path, payload: &str) -> PathBuf {
    standin(
        dir,
        "devforge-standin",
        &format!("printf '%s' {}\n", quote(payload)),
    )
}

/// A stand-in that records execution in `marker` before serving valid capabilities.
fn marking(dir: &Path, marker: &Path) -> PathBuf {
    standin(
        dir,
        "devforge-marking",
        &format!(
            "printf 'executed\\n' > {}\nprintf '%s' {}\n",
            quote(marker.to_str().unwrap()),
            quote(&extended().to_string())
        ),
    )
}

/// A single-hard-link copy of the built CLI, selectable as a runtime or, when run
/// directly, as the validating executable.
fn copied(dir: &Path, name: &str) -> PathBuf {
    let path = dir.join(name);
    fs::create_dir_all(dir).unwrap();
    fs::copy(BIN, &path).unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
    assert_eq!(fs::metadata(&path).unwrap().nlink(), 1);
    path
}

fn base() -> Value {
    json!({
        "schema_version": "devforge.delivery-capabilities/v1",
        "protocol": "devforge.delivery-runtime/v1",
        "supported_providers": ["codex", "claude"],
        "completion_modes": ["process", "managed-session"],
        "io_modes": ["inherited", "interactive-tty"],
        "hook_events": ["SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"],
        "native_admission": "NOT_VALIDATED",
        "mechanical_scope": SCOPE,
    })
}

fn extended() -> Value {
    let mut value = base();
    let map = value.as_object_mut().unwrap();
    map.insert(
        "utility_workflows".into(),
        json!(["skill-builder", "skill-validator"]),
    );
    map.insert(
        "utility_session_schema".into(),
        json!("devforge.utility-session/v1"),
    );
    map.insert(
        "utility_native_schedule_schema".into(),
        json!("devforge.utility-native-schedule/v1"),
    );
    map.insert("native_execution_enabled".into(), json!(false));
    map.insert(
        "native_process_interface".into(),
        json!("EXPLICIT_FROZEN_CONFIGURATION_REQUIRED"),
    );
    map.insert(
        "native_process_receipt_schema".into(),
        json!("devforge.native-process-receipt/v1"),
    );
    map.insert(
        "native_semantic_review".into(),
        json!("SEPARATE_SELECTED_OPERATOR_OR_INDEPENDENT_REVIEW"),
    );
    value
}

fn with(source: &Value, key: &str, replacement: Value) -> String {
    let mut value = source.clone();
    value
        .as_object_mut()
        .unwrap()
        .insert(key.into(), replacement);
    value.to_string()
}

fn without(source: &Value, key: &str) -> String {
    let mut value = source.clone();
    assert!(value.as_object_mut().unwrap().remove(key).is_some());
    value.to_string()
}

// ---- invocation ----------------------------------------------------------

struct Run {
    code: i32,
    stdout: String,
    stderr: String,
}

/// Invoke an explicitly selected DevForge executable, which is the validating
/// authority for that run: its own location decides the project refusals below.
fn run_with(binary: &Path, args: &[&str]) -> Run {
    let output = Command::new(binary).args(args).output().unwrap();
    Run {
        code: output.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    }
}

fn run(args: &[&str]) -> Run {
    run_with(Path::new(BIN), args)
}

fn probe(runtime: &str, providers: &[&str]) -> Run {
    let mut args = vec!["install", "probe-runtime", "--runtime", runtime];
    for provider in providers {
        args.push("--provider");
        args.push(provider);
    }
    run(&args)
}

fn accepted(runtime: &Path, providers: &[&str]) -> Value {
    let result = probe(runtime.to_str().unwrap(), providers);
    assert_eq!(
        result.code, 0,
        "expected acceptance; stdout={} stderr={}",
        result.stdout, result.stderr
    );
    serde_json::from_str(&result.stdout).expect("probe report must be JSON")
}

fn refused(result: &Run, reason: &str) {
    assert_eq!(
        result.code, 2,
        "expected refusal {reason}; stdout={} stderr={}",
        result.stdout, result.stderr
    );
    let report: Value = serde_json::from_str(&result.stdout).expect("refusal must be JSON");
    assert_eq!(report["status"], "BLOCKED", "report={report}");
    assert_eq!(report["reason"], reason, "report={report}");
}

fn refuses(dir: &Path, body: &str, providers: &[&str], reason: &str) {
    let runtime = serving(dir, body);
    refused(&probe(runtime.to_str().unwrap(), providers), reason);
}

/// Path, size, mode and mtime of every entry below `root`.
fn snapshot(root: &Path) -> BTreeMap<String, (u64, u32, i64, i64)> {
    fn visit(root: &Path, dir: &Path, seen: &mut BTreeMap<String, (u64, u32, i64, i64)>) {
        for entry in fs::read_dir(dir).unwrap() {
            let path = entry.unwrap().path();
            let meta = fs::symlink_metadata(&path).unwrap();
            seen.insert(
                path.strip_prefix(root)
                    .unwrap()
                    .to_string_lossy()
                    .into_owned(),
                (meta.len(), meta.mode(), meta.mtime(), meta.mtime_nsec()),
            );
            if meta.is_dir() {
                visit(root, &path, seen);
            }
        }
    }
    let mut seen = BTreeMap::new();
    visit(root, root, &mut seen);
    seen
}

fn identity() -> Value {
    let result = run(&["install", "identity"]);
    assert_eq!(result.code, 0, "stderr={}", result.stderr);
    serde_json::from_str(&result.stdout).unwrap()
}

// ---- acceptance ----------------------------------------------------------

#[test]
fn recorded_walkthrough_capabilities_are_accepted_as_the_extended_contract() {
    // The exact bytes that made the legacy eight-field check refuse installation.
    let dir = temp();
    let runtime = serving(dir.path(), OBSERVED);
    let report = accepted(&runtime, &["claude"]);
    assert_eq!(report["schema_version"], "devforge.runtime-probe/v1");
    assert_eq!(report["contract"], "extended");
    assert_eq!(report["providers"], json!(["claude"]));
    assert_eq!(report["native_activation"], "NOT_VERIFIED");
    assert_eq!(report["path"], runtime.to_str().unwrap());
    let expected: Value = serde_json::from_str(OBSERVED).unwrap();
    assert_eq!(report["capabilities"], expected);
    assert_eq!(report["capabilities"].as_object().unwrap().len(), 15);
    let digest = sha(&fs::read(&runtime).unwrap());
    assert_eq!(report["sha256_before"], digest);
    assert_eq!(report["sha256_after"], digest);
}

#[test]
fn the_running_delivery_capabilities_still_serve_the_recorded_bytes() {
    // Keeps the regression above discriminating: the shipped runtime emits those bytes.
    let result = run(&["delivery", "capabilities"]);
    assert_eq!(result.code, 0, "stderr={}", result.stderr);
    assert_eq!(result.stdout, format!("{OBSERVED}\n"));
}

#[test]
fn the_built_binary_is_accepted_when_probed_as_its_own_runtime() {
    let dir = temp();
    let runtime = dir.path().join("devforge");
    fs::copy(BIN, &runtime).unwrap();
    fs::set_permissions(&runtime, fs::Permissions::from_mode(0o755)).unwrap();
    assert_eq!(fs::metadata(&runtime).unwrap().nlink(), 1);
    let report = accepted(&runtime, &["codex", "claude"]);
    assert_eq!(report["contract"], "extended");
    assert_eq!(report["providers"], json!(["codex", "claude"]));
    assert_eq!(
        report["capabilities"],
        serde_json::from_str::<Value>(OBSERVED).unwrap()
    );
    let digest = sha(&fs::read(&runtime).unwrap());
    assert_eq!(report["sha256_before"], digest);
    assert_eq!(report["sha256_after"], digest);
    // The validating executable is reported separately from the probed runtime.
    let identity = identity();
    assert_eq!(report["validator"]["executable"], identity["executable"]);
    assert_eq!(
        report["validator"]["source_sha256"],
        identity["source_sha256"]
    );
    assert_ne!(report["validator"]["executable"]["path"], report["path"]);
    assert_eq!(report["validator"]["executable"]["sha256"], digest);
}

#[test]
fn the_eight_field_base_contract_is_accepted() {
    let dir = temp();
    let runtime = serving(dir.path(), &base().to_string());
    let report = accepted(&runtime, &["codex"]);
    assert_eq!(report["contract"], "base");
    assert_eq!(report["capabilities"], base());
    assert_eq!(report["capabilities"].as_object().unwrap().len(), 8);
}

// ---- capability refusals -------------------------------------------------

#[test]
fn malformed_base_capabilities_are_refused_with_exact_reasons() {
    let dir = temp();
    let base = base();
    let text = base.to_string();
    let surrogate = text.replace(
        &format!("\"mechanical_scope\":{}", json!(SCOPE)),
        "\"mechanical_scope\":\"\\ud800\"",
    );
    assert_ne!(surrogate, text);
    let cases: Vec<(String, &[&str], &str)> = vec![
        ("{".into(), &["codex"], "invalid runtime capabilities output: invalid JSON"),
        ("[]".into(), &["codex"], "malformed runtime capabilities fields"),
        (
            r#"{"schema_version":"devforge.delivery-capabilities/v1","schema_version":"devforge.delivery-capabilities/v1"}"#.into(),
            &["codex"],
            "invalid runtime capabilities output: duplicate JSON key: schema_version at line 1 column 107",
        ),
        (surrogate, &["codex"], "invalid runtime capabilities output: invalid JSON"),
        (
            with(&base, "schema_version", json!("devforge.delivery-capabilities/v2")),
            &["codex"],
            "unsupported runtime capabilities contract",
        ),
        (
            with(&base, "protocol", json!("other")),
            &["codex"],
            "unsupported runtime capabilities contract",
        ),
        (
            with(&base, "native_admission", json!(true)),
            &["codex"],
            "unsupported runtime capabilities contract",
        ),
        (
            with(&base, "native_admission", json!("ADMITTED")),
            &["codex"],
            "unsupported runtime capabilities contract",
        ),
        (
            with(&base, "mechanical_scope", Value::Null),
            &["codex"],
            "unsupported runtime capabilities contract",
        ),
        (
            with(&base, "mechanical_scope", json!("   ")),
            &["codex"],
            "unsupported runtime capabilities contract",
        ),
        (
            with(&base, "io_modes", json!("inherited")),
            &["codex"],
            "malformed runtime capability list: io_modes",
        ),
        (
            with(&base, "supported_providers", json!([])),
            &["codex"],
            "malformed runtime capability list: supported_providers",
        ),
        (
            with(&base, "completion_modes", json!(["managed-session", "managed-session"])),
            &["codex"],
            "malformed runtime capability list: completion_modes",
        ),
        (
            with(&base, "hook_events", json!(["Stop", ""])),
            &["codex"],
            "malformed runtime capability list: hook_events",
        ),
        (
            with(&base, "supported_providers", json!(["claude"])),
            &["codex"],
            "runtime capabilities do not satisfy the package requirement",
        ),
        (
            with(&base, "completion_modes", json!(["unmanaged"])),
            &["codex"],
            "runtime capabilities do not satisfy the package requirement",
        ),
        (
            with(&base, "hook_events", json!(["SessionStart", "UserPromptSubmit", "Stop"])),
            &["codex"],
            "runtime capabilities do not satisfy the package requirement",
        ),
        (
            with(&base, "extra", json!("unknown")),
            &["codex"],
            "malformed runtime capabilities fields",
        ),
        (
            without(&base, "native_admission"),
            &["codex"],
            "malformed runtime capabilities fields",
        ),
    ];
    for (body, providers, reason) in cases {
        refuses(dir.path(), &body, providers, reason);
    }
}

#[test]
fn a_requested_provider_outside_the_runtime_support_is_refused() {
    let dir = temp();
    refuses(
        dir.path(),
        &with(&extended(), "supported_providers", json!(["claude"])),
        &["codex", "claude"],
        "runtime capabilities do not satisfy the package requirement",
    );
}

#[test]
fn malformed_extension_values_are_refused_field_by_field() {
    let dir = temp();
    let extended = extended();
    let cases = [
        ("utility_workflows", json!("skill-builder")),
        ("utility_workflows", json!([])),
        (
            "utility_workflows",
            json!(["skill-builder", "skill-builder"]),
        ),
        (
            "utility_session_schema",
            json!("devforge.utility-session/v2"),
        ),
        ("utility_native_schedule_schema", json!(false)),
        ("native_execution_enabled", json!("false")),
        ("native_process_interface", json!("  ")),
        ("native_process_receipt_schema", Value::Null),
        ("native_semantic_review", json!(["review"])),
    ];
    for (field, replacement) in cases {
        refuses(
            dir.path(),
            &with(&extended, field, replacement),
            &["claude"],
            &format!("malformed runtime capabilities extension: {field}"),
        );
    }
}

#[test]
fn a_partial_extension_set_is_refused_as_an_unsupported_combination() {
    let dir = temp();
    let extended = extended();
    for field in EXTENSION_FIELDS {
        refuses(
            dir.path(),
            &without(&extended, field),
            &["claude"],
            "unsupported runtime capabilities extension combination",
        );
    }
    // A base contract carrying one extension is equally unsupported.
    let mut single = base();
    single.as_object_mut().unwrap().insert(
        "utility_session_schema".into(),
        json!("devforge.utility-session/v1"),
    );
    refuses(
        dir.path(),
        &single.to_string(),
        &["claude"],
        "unsupported runtime capabilities extension combination",
    );
    // An unknown key alongside a partial extension set is a malformed field set.
    refuses(
        dir.path(),
        &with(
            &without(&extended, "native_semantic_review")
                .parse::<Value>()
                .unwrap(),
            "extra",
            json!(1),
        ),
        &["claude"],
        "malformed runtime capabilities fields",
    );
}

// ---- runtime selection hygiene -------------------------------------------

#[test]
fn runtime_path_hygiene_refuses_before_any_execution() {
    let dir = temp();
    let marker = dir.path().join("executed.marker");
    let runtime = marking(dir.path(), &marker);

    refused(
        &probe("devforge-marking", &["codex"]),
        "--runtime must be an absolute executable path",
    );
    let link = dir.path().join("runtime-link");
    std::os::unix::fs::symlink(&runtime, &link).unwrap();
    refused(
        &probe(link.to_str().unwrap(), &["codex"]),
        "--runtime must be canonical and have no symlink components",
    );
    let parent_link = dir.path().join("parent-link");
    std::os::unix::fs::symlink(dir.path(), &parent_link).unwrap();
    refused(
        &probe(
            parent_link.join("devforge-marking").to_str().unwrap(),
            &["codex"],
        ),
        "--runtime must be canonical and have no symlink components",
    );
    let folder = dir.path().join("folder");
    fs::create_dir(&folder).unwrap();
    refused(
        &probe(
            folder.join("../devforge-marking").to_str().unwrap(),
            &["codex"],
        ),
        "--runtime must be canonical and have no symlink components",
    );
    refused(
        &probe(folder.to_str().unwrap(), &["codex"]),
        "--runtime must select a regular executable file",
    );
    refused(
        &probe(dir.path().join("absent").to_str().unwrap(), &["codex"]),
        "--runtime must select a regular executable file",
    );
    let plain = dir.path().join("not-executable");
    fs::copy(&runtime, &plain).unwrap();
    fs::set_permissions(&plain, fs::Permissions::from_mode(0o644)).unwrap();
    refused(
        &probe(plain.to_str().unwrap(), &["codex"]),
        "--runtime must select a regular executable file",
    );
    // A second hard link lets an installation destination alias the selected runtime.
    let alias = dir.path().join("runtime-alias");
    fs::hard_link(&runtime, &alias).unwrap();
    assert_eq!(fs::metadata(&alias).unwrap().nlink(), 2);
    refused(
        &probe(alias.to_str().unwrap(), &["codex"]),
        "--runtime must have exactly one hard link",
    );
    assert!(
        !marker.exists(),
        "path hygiene must refuse before executing the candidate runtime"
    );
}

#[test]
fn probe_bounds_time_output_and_exit_status() {
    let dir = temp();
    let valid = quote(&extended().to_string());
    let cases = [
        (
            "exec sleep 10\n".to_string(),
            "runtime capabilities timed out after 5 seconds",
        ),
        (
            "head -c 1048577 /dev/zero | tr '\\0' 'x'\n".to_string(),
            "runtime capabilities output exceeds 1 MiB",
        ),
        (
            format!("head -c 1048577 /dev/zero | tr '\\0' 'x' 1>&2\nprintf '%s' {valid}\n"),
            "runtime capabilities output exceeds 1 MiB",
        ),
        (
            format!("printf '%s' {valid}\nexit 7\n"),
            "runtime capabilities exited with status 7",
        ),
    ];
    for (body, reason) in cases {
        let runtime = standin(dir.path(), "devforge-bounded", &body);
        refused(&probe(runtime.to_str().unwrap(), &["codex"]), reason);
    }
}

#[test]
fn a_runtime_that_rewrites_itself_during_the_probe_is_refused() {
    let dir = temp();
    let runtime = standin(
        dir.path(),
        "devforge-mutating",
        &format!(
            "printf '%s' {}\nprintf '\\n# changed\\n' >> \"$0\"\n",
            quote(&extended().to_string())
        ),
    );
    let before = fs::read(&runtime).unwrap();
    refused(
        &probe(runtime.to_str().unwrap(), &["codex"]),
        "selected runtime binary changed during capability verification",
    );
    assert_ne!(fs::read(&runtime).unwrap(), before);
}

#[test]
fn provider_selection_arguments_are_validated() {
    let dir = temp();
    let runtime = serving(dir.path(), &extended().to_string());
    let path = runtime.to_str().unwrap();
    refused(
        &probe(path, &[]),
        "--provider must select at least one provider",
    );
    refused(
        &probe(path, &["codex", "codex"]),
        "--provider must not be repeated",
    );
    refused(
        &probe(path, &["gemini"]),
        "--provider must be codex or claude",
    );
    refused(
        &probe(path, &["claude", "gemini"]),
        "--provider must be codex or claude",
    );
}

#[test]
fn the_probe_writes_nothing_to_the_filesystem() {
    let dir = temp();
    let accepted_runtime = serving(dir.path(), OBSERVED);
    let refused_runtime = standin(dir.path(), "devforge-refused", "printf '%s' '[]'\n");
    let before = snapshot(dir.path());
    let report = accepted(&accepted_runtime, &["codex", "claude"]);
    assert_eq!(report["contract"], "extended");
    refused(
        &probe(refused_runtime.to_str().unwrap(), &["codex"]),
        "malformed runtime capabilities fields",
    );
    assert_eq!(snapshot(dir.path()), before);
}

// ---- selected installation project ---------------------------------------
//
// A delivery-aware installation names the project it is about to write. The
// validating executable must sit outside that project, or an installation
// destination could overwrite the authority that admitted it. The refusal is
// decided before the selected runtime is read or executed.

/// `<validator> --project P install probe-runtime --runtime R --provider ...`.
fn probe_bound(binary: &Path, project: &Path, runtime: &Path, providers: &[&str]) -> Run {
    let mut args = vec![
        "--project",
        project.to_str().unwrap(),
        "install",
        "probe-runtime",
        "--runtime",
        runtime.to_str().unwrap(),
    ];
    for provider in providers {
        args.push("--provider");
        args.push(provider);
    }
    run_with(binary, &args)
}

#[test]
fn a_validating_executable_inside_the_selected_project_is_refused_before_execution() {
    let dir = temp();
    let root = fs::canonicalize(dir.path()).unwrap();
    let project = root.join("project");
    fs::create_dir(&project).unwrap();
    let marker = root.join("executed.marker");
    let runtime = marking(&root, &marker);
    // The same bytes, one copy inside the project and one outside it.
    let inside = copied(&project.join("tools"), "devforge");
    let outside = copied(&root, "devforge-authority");
    assert_eq!(fs::read(&inside).unwrap(), fs::read(&outside).unwrap());

    refused(
        &probe_bound(&inside, &project, &runtime, &["codex"]),
        "validating executable must be outside the installation project",
    );
    assert!(
        !marker.exists(),
        "the project refusal must precede executing the selected runtime"
    );

    // Only the validator's location was refused: the identical copy outside is admitted.
    let result = probe_bound(&outside, &project, &runtime, &["codex"]);
    assert_eq!(
        result.code, 0,
        "expected acceptance; stdout={} stderr={}",
        result.stdout, result.stderr
    );
    let report: Value = serde_json::from_str(&result.stdout).unwrap();
    assert_eq!(report["contract"], "extended");
    assert_eq!(report["project"], project.to_str().unwrap());
    assert_eq!(report["path"], runtime.to_str().unwrap());
    // The report binds the running validator itself: canonical path, true digest.
    let reported = report["validator"]["executable"]["path"].as_str().unwrap();
    assert_eq!(Path::new(reported), fs::canonicalize(&outside).unwrap());
    assert_eq!(
        report["validator"]["executable"]["sha256"],
        sha(&fs::read(&outside).unwrap())
    );
    assert_eq!(
        report["validator"]["source_sha256"],
        identity()["source_sha256"]
    );
    assert!(marker.exists(), "the admitted probe executes the runtime");
}

#[test]
fn a_missing_or_non_directory_project_is_refused_before_execution() {
    let dir = temp();
    let root = fs::canonicalize(dir.path()).unwrap();
    let marker = root.join("executed.marker");
    let runtime = marking(&root, &marker);
    let file = root.join("project-file");
    fs::write(&file, b"not a project directory\n").unwrap();
    for candidate in [
        root.join("absent-project"),
        file,
        root.join("absent/deeper"),
    ] {
        refused(
            &probe_bound(Path::new(BIN), &candidate, &runtime, &["codex"]),
            "project must already exist",
        );
        assert!(
            !marker.exists(),
            "an unusable project must refuse before executing the runtime"
        );
    }
}

#[test]
fn an_unbound_probe_reports_no_project_and_a_canonical_validator_identity() {
    let dir = temp();
    let runtime = serving(dir.path(), OBSERVED);
    let report = accepted(&runtime, &["codex", "claude"]);
    assert!(
        report.as_object().unwrap().get("project").is_none(),
        "an unbound probe must not claim a project; report={report}"
    );
    let reported = report["validator"]["executable"]["path"].as_str().unwrap();
    let path = Path::new(reported);
    assert!(
        path.is_absolute(),
        "validator path must be absolute: {reported}"
    );
    assert_eq!(path, fs::canonicalize(path).unwrap());
    assert_eq!(
        report["validator"]["executable"]["sha256"],
        sha(&fs::read(path).unwrap()),
        "the reported digest must be the running executable's own bytes"
    );
}
