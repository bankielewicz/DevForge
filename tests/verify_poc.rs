//! Black-box tests for `devforge verify-poc`.
//!
//! The command runs a fixed stage list and records what each stage did. A
//! recorded `PASS` proves a command exited 0, never that behavior is accepted;
//! the report keeps `model_behavior: NOT_EVALUATED` and `hosted_ci: NOT_RUN`.
use serde_json::Value;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};

const BIN: &str = env!("CARGO_BIN_EXE_devforge");
const REPO: &str = env!("CARGO_MANIFEST_DIR");
const STAGES: [&str; 7] = [
    "format",
    "clippy",
    "build",
    "tests",
    "framework-structure",
    "mvp-documents",
    "fixture-demo",
];
static COUNTER: AtomicU64 = AtomicU64::new(0);

struct Scratch(PathBuf);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

impl Scratch {
    fn new() -> Self {
        let dir = std::env::temp_dir().join(format!(
            "devforge-verify-poc-{}-{}",
            std::process::id(),
            COUNTER.fetch_add(1, Ordering::SeqCst)
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        Scratch(fs::canonicalize(&dir).unwrap())
    }

    fn join(&self, relative: &str) -> PathBuf {
        self.0.join(relative)
    }

    fn write(&self, relative: &str, bytes: &[u8]) -> PathBuf {
        let path = self.join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, bytes).unwrap();
        path
    }
}

fn real_framework() -> Option<PathBuf> {
    ["../DevForgeAI", "../../framework/DevForgeAI"]
        .iter()
        .map(|relative| Path::new(REPO).join(relative))
        .find(|candidate| candidate.join("providers").is_dir())
        .and_then(|path| fs::canonicalize(path).ok())
}

/// A stand-in for `cargo` so the cheap stages do not rebuild the crate. It
/// records its argv and working directory, then exits with `code`.
fn cargo_stub(scratch: &Scratch, code: i32) -> PathBuf {
    let log = scratch.join("cargo-argv.log");
    let script = format!(
        "#!/bin/sh\nprintf '%s|%s\\n' \"$PWD\" \"$*\" >> {}\nexit {code}\n",
        log.display()
    );
    let path = scratch.write("cargo-stub", script.as_bytes());
    fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
    path
}

/// A minimal `--repo`: one passing Python case for the `tests` stage and the
/// `policies/` directory the `fixture-demo` stage names.
fn synthetic_repo(scratch: &Scratch) -> PathBuf {
    let repo = scratch.join("repo");
    fs::create_dir_all(&repo).unwrap();
    fs::create_dir_all(repo.join("policies")).unwrap();
    fs::create_dir_all(repo.join("tests")).unwrap();
    fs::write(
        repo.join("tests/test_ok.py"),
        b"import unittest\n\n\nclass Synthetic(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
    )
    .unwrap();
    fs::write(repo.join("README.md"), b"# Synthetic repo\n").unwrap();
    repo
}

fn verify(arguments: &[&str]) -> Output {
    Command::new(BIN)
        .arg("verify-poc")
        .args(arguments)
        .output()
        .unwrap()
}

fn text(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
}

/// The single timestamped evidence directory under `root`.
fn evidence(root: &Path) -> PathBuf {
    let mut entries: Vec<PathBuf> = fs::read_dir(root)
        .unwrap_or_else(|error| panic!("no evidence under {}: {error}", root.display()))
        .flatten()
        .map(|entry| entry.path())
        .collect();
    entries.sort();
    assert_eq!(entries.len(), 1, "expected one evidence run: {entries:?}");
    entries.pop().unwrap()
}

fn report(directory: &Path) -> Value {
    serde_json::from_slice(&fs::read(directory.join("report.json")).unwrap()).unwrap()
}

fn checks(report: &Value) -> Vec<(String, String, i64)> {
    report["checks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|check| {
            (
                check["check"].as_str().unwrap().to_owned(),
                check["status"].as_str().unwrap().to_owned(),
                check["exit_code"].as_i64().unwrap(),
            )
        })
        .collect()
}

/// A UTC stamp shaped like `datetime.strftime("%Y%m%dT%H%M%S%fZ")`.
fn is_stamp(name: &str) -> bool {
    let Some(body) = name.strip_suffix('Z') else {
        return false;
    };
    body.len() == 21
        && body.as_bytes()[8] == b'T'
        && body
            .bytes()
            .enumerate()
            .all(|(index, byte)| index == 8 || byte.is_ascii_digit())
}

#[test]
fn the_first_failing_stage_stops_the_run_and_exits_two() {
    let scratch = Scratch::new();
    let repo = synthetic_repo(&scratch);
    let cargo = cargo_stub(&scratch, 1);
    let root = scratch.join("evidence");
    fs::create_dir_all(scratch.join("framework")).unwrap();
    let output = verify(&[
        "--framework",
        scratch.join("framework").to_str().unwrap(),
        "--repo",
        repo.to_str().unwrap(),
        "--evidence-root",
        root.to_str().unwrap(),
        "--cargo",
        cargo.to_str().unwrap(),
    ]);
    assert_eq!(
        output.status.code(),
        Some(2),
        "stdout={:?}",
        text(&output.stdout)
    );
    let directory = evidence(&root);
    assert_eq!(
        checks(&report(&directory)),
        [("format".into(), "FAIL".into(), 1)]
    );
    assert!(directory.join("format.log").is_file());
    for stage in &STAGES[1..] {
        assert!(
            !directory.join(format!("{stage}.log")).exists(),
            "{stage} must not run after a failure"
        );
    }
    assert!(text(&output.stdout).contains("format: FAIL"));
    // Exactly one cargo invocation happened, in the selected repository.
    let argv = fs::read_to_string(scratch.join("cargo-argv.log")).unwrap();
    assert_eq!(
        argv,
        format!("{}|fmt --check\n", repo.display()),
        "the format stage must run `cargo fmt --check` with cwd = --repo"
    );
}

#[test]
fn the_cheap_stages_run_in_order_before_the_structural_ones() {
    let Some(framework) = real_framework() else {
        eprintln!("companion framework absent; verify-poc NOT_RUN");
        return;
    };
    let scratch = Scratch::new();
    let repo = synthetic_repo(&scratch);
    let cargo = cargo_stub(&scratch, 0);
    let root = scratch.join("evidence");
    let output = verify(&[
        "--framework",
        framework.to_str().unwrap(),
        "--repo",
        repo.to_str().unwrap(),
        "--evidence-root",
        root.to_str().unwrap(),
        "--cargo",
        cargo.to_str().unwrap(),
    ]);
    // The companion checkout has pre-existing structural defects, so the
    // framework-structure stage is expected to fail and to stop the run. That
    // failure is recorded, never swallowed.
    assert_eq!(output.status.code(), Some(2));
    let directory = evidence(&root);
    let recorded = checks(&report(&directory));
    assert_eq!(
        recorded
            .iter()
            .map(|(name, _, _)| name.as_str())
            .collect::<Vec<_>>(),
        ["format", "clippy", "build", "tests", "framework-structure"]
    );
    for (name, status, code) in &recorded[..4] {
        assert_eq!((status.as_str(), *code), ("PASS", 0), "{name}");
    }
    assert_eq!(recorded[4].1, "FAIL");
    assert_eq!(recorded[4].2, 2);
    let log = fs::read_to_string(directory.join("framework-structure.log")).unwrap();
    assert!(log.contains("BLOCKED: "), "log={log:?}");
    for stage in ["mvp-documents", "fixture-demo"] {
        assert!(!directory.join(format!("{stage}.log")).exists());
    }
    // The three cargo stages ran in order, each with cwd = --repo.
    let argv = fs::read_to_string(scratch.join("cargo-argv.log")).unwrap();
    assert_eq!(
        argv.lines()
            .map(|line| line.split_once('|').unwrap().1.to_owned())
            .collect::<Vec<_>>(),
        [
            "fmt --check",
            "clippy --locked --all-targets -- -D warnings",
            "build --locked"
        ]
    );
    assert!(
        fs::read_to_string(directory.join("tests.log"))
            .unwrap()
            .contains("OK"),
        "the legacy unittest baseline ran in the selected repository"
    );
}

#[test]
fn the_report_records_scope_and_a_source_inventory_with_the_legacy_exclusions() {
    let scratch = Scratch::new();
    let repo = synthetic_repo(&scratch);
    let cargo = cargo_stub(&scratch, 1);
    let framework = scratch.join("framework");
    fs::create_dir_all(&framework).unwrap();
    fs::write(framework.join("kept.md"), b"framework file\n").unwrap();
    for excluded in [
        ".git/config",
        "target/debug/binary",
        ".poc/run/evidence.json",
        "__pycache__/module.pyc",
        "docs/validation/20260101/report.json",
        "nested/target/artifact",
        "nested/__pycache__/cached.pyc",
    ] {
        let path = repo.join(excluded);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, b"excluded\n").unwrap();
    }
    fs::write(repo.join("docs/kept.md"), b"kept\n").unwrap();
    let root = scratch.join("evidence");
    let output = verify(&[
        "--framework",
        framework.to_str().unwrap(),
        "--repo",
        repo.to_str().unwrap(),
        "--evidence-root",
        root.to_str().unwrap(),
        "--cargo",
        cargo.to_str().unwrap(),
    ]);
    assert_eq!(output.status.code(), Some(2));
    let directory = evidence(&root);
    assert!(is_stamp(directory.file_name().unwrap().to_str().unwrap()));
    let report = report(&directory);
    assert_eq!(report["schema"], 1);
    assert_eq!(
        report["created_at"],
        directory.file_name().unwrap().to_str().unwrap()
    );
    assert_eq!(report["model_behavior"], "NOT_EVALUATED");
    assert_eq!(report["hosted_ci"], "NOT_RUN");
    assert!(
        report["scope"]
            .as_str()
            .unwrap()
            .contains("Local structural")
    );
    let sources = report["sources_sha256"].as_object().unwrap();
    let mut keys: Vec<&str> = sources.keys().map(String::as_str).collect();
    keys.sort();
    assert_eq!(
        keys,
        [
            "DevForge/README.md",
            "DevForge/docs/kept.md",
            "DevForge/tests/test_ok.py",
            "DevForgeAI/kept.md",
        ],
        "cargo-stub and its log live outside both repositories"
    );
    assert!(text(&output.stdout).contains(&format!(
        "Evidence: {}",
        directory.join("report.json").display()
    )));
}

#[test]
fn evidence_defaults_into_the_selected_repository_when_no_root_is_given() {
    let scratch = Scratch::new();
    let repo = synthetic_repo(&scratch);
    let cargo = cargo_stub(&scratch, 1);
    let framework = scratch.join("framework");
    fs::create_dir_all(&framework).unwrap();
    let output = verify(&[
        "--framework",
        framework.to_str().unwrap(),
        "--repo",
        repo.to_str().unwrap(),
        "--cargo",
        cargo.to_str().unwrap(),
    ]);
    assert_eq!(output.status.code(), Some(2));
    let directory = evidence(&repo.join("docs/validation"));
    assert!(directory.join("report.json").is_file());
    assert!(directory.join("format.log").is_file());
}

#[test]
fn a_missing_repository_or_framework_is_refused_without_evidence() {
    let scratch = Scratch::new();
    let repo = synthetic_repo(&scratch);
    let cargo = cargo_stub(&scratch, 0);
    let root = scratch.join("evidence");
    for (framework, repository) in [
        (scratch.join("absent-framework"), repo.clone()),
        (scratch.join("framework"), scratch.join("absent-repo")),
    ] {
        let output = verify(&[
            "--framework",
            framework.to_str().unwrap(),
            "--repo",
            repository.to_str().unwrap(),
            "--evidence-root",
            root.to_str().unwrap(),
            "--cargo",
            cargo.to_str().unwrap(),
        ]);
        assert_eq!(output.status.code(), Some(2));
        let refusal: Value = serde_json::from_str(
            text(&output.stdout)
                .lines()
                .rev()
                .find(|line| line.starts_with('{'))
                .unwrap_or_default(),
        )
        .unwrap_or_else(|error| panic!("no BLOCKED object ({error}): {:?}", text(&output.stdout)));
        assert_eq!(refusal["status"], "BLOCKED");
        assert!(!root.exists(), "a refusal must not create an evidence run");
    }
}
