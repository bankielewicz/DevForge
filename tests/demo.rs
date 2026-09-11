//! Black-box tests for `devforge demo`.
//!
//! The demonstration drives the real gates over the real example fixtures. It
//! calls no model and claims no expert behavioral evaluation: every report says
//! `model_behavior: NOT_EVALUATED` and `model_calls: 0`. These cases assert the
//! mechanical contract only, and that the selected framework checkout is left
//! byte-identical.
use serde_json::Value;
use std::collections::BTreeMap;
use std::fs;
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};

const BIN: &str = env!("CARGO_BIN_EXE_devforge");
const REPO: &str = env!("CARGO_MANIFEST_DIR");
const SLUGS: [&str; 2] = ["notes-sqlite", "notes-json"];
static COUNTER: AtomicU64 = AtomicU64::new(0);

fn framework() -> Option<PathBuf> {
    ["../DevForgeAI", "../../framework/DevForgeAI"]
        .iter()
        .map(|relative| Path::new(REPO).join(relative))
        .find(|candidate| candidate.join("examples/notes-sqlite/seed").is_dir())
        .and_then(|path| fs::canonicalize(path).ok())
}

struct Scratch(PathBuf);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

impl Scratch {
    fn new() -> Self {
        let dir = std::env::temp_dir().join(format!(
            "devforge-demo-{}-{}",
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
}

fn sha(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    format!("{:x}", Sha256::digest(bytes))
}

/// Every file under `root`, keyed by relative path, for byte-preservation checks.
fn inventory(root: &Path) -> BTreeMap<String, String> {
    let mut files = BTreeMap::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        let Ok(entries) = fs::read_dir(&directory) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                pending.push(path);
            } else if let Ok(bytes) = fs::read(&path) {
                files.insert(
                    path.strip_prefix(root).unwrap().display().to_string(),
                    sha(&bytes),
                );
            }
        }
    }
    files
}

/// A policy directory whose `tooling_files` pins match what a
/// `--provider claude --include-experts` installation of the selected framework
/// actually writes. The repository's own `policies/` has drifted from the
/// framework's current Claude package; see the integration document.
fn policies(scratch: &Scratch, framework: &Path) -> PathBuf {
    let directory = scratch.join("policies");
    fs::create_dir_all(&directory).unwrap();
    let skills = framework.join("providers/claude/plugins/devforgeai/skills");
    let mut pins = serde_json::Map::new();
    let mut names: Vec<PathBuf> = fs::read_dir(&skills)
        .unwrap()
        .flatten()
        .map(|entry| entry.path())
        .collect();
    names.sort();
    for skill in names {
        let Ok(scripts) = fs::read_dir(skill.join("scripts")) else {
            continue;
        };
        let mut files: Vec<PathBuf> = scripts.flatten().map(|entry| entry.path()).collect();
        files.sort();
        for script in files {
            let name = skill.file_name().unwrap().to_string_lossy().into_owned();
            let file = script.file_name().unwrap().to_string_lossy().into_owned();
            // Installation normalizes the mode to 0644 regardless of the source.
            pins.insert(
                format!(".claude/skills/{name}/scripts/{file}"),
                serde_json::json!({"sha256": sha(&fs::read(&script).unwrap()), "mode": 0o644}),
            );
        }
    }
    for slug in SLUGS {
        let source = Path::new(REPO).join(format!("policies/{slug}.json"));
        let mut policy: Value = serde_json::from_slice(&fs::read(&source).unwrap()).unwrap();
        policy["tooling_files"] = Value::Object(pins.clone());
        fs::write(
            directory.join(format!("{slug}.json")),
            serde_json::to_vec_pretty(&policy).unwrap(),
        )
        .unwrap();
    }
    directory
}

fn demo(arguments: &[&str]) -> Output {
    Command::new(BIN)
        .arg("demo")
        .args(arguments)
        .output()
        .unwrap()
}

fn text(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
}

fn blocked(output: &Output, needle: &str) {
    assert_eq!(
        output.status.code(),
        Some(2),
        "stdout={:?} stderr={:?}",
        text(&output.stdout),
        text(&output.stderr)
    );
    let line = text(&output.stdout)
        .lines()
        .rev()
        .find(|line| line.starts_with('{'))
        .unwrap_or_default()
        .to_owned();
    let refusal: Value = serde_json::from_str(&line)
        .unwrap_or_else(|error| panic!("no BLOCKED object ({error}): {:?}", text(&output.stdout)));
    assert_eq!(refusal["status"], "BLOCKED");
    let reason = refusal["reason"].as_str().unwrap_or_default();
    assert!(reason.contains(needle), "reason={reason:?}");
}

fn report(output: &Output, root: &Path, run: &str) -> Value {
    assert_eq!(
        output.status.code(),
        Some(0),
        "stdout={:?} stderr={:?}",
        text(&output.stdout),
        text(&output.stderr)
    );
    let path = root.join(run).join("demo-report.json");
    assert!(
        text(&output.stdout).contains(&format!("Report: {}", path.display())),
        "stdout={:?}",
        text(&output.stdout)
    );
    serde_json::from_slice(&fs::read(&path).unwrap()).unwrap()
}

fn commands(project: &Value) -> Vec<String> {
    project["events"]
        .as_array()
        .unwrap()
        .iter()
        .map(|event| {
            event["command"]
                .as_array()
                .unwrap()
                .iter()
                .map(|part| part.as_str().unwrap())
                .collect::<Vec<_>>()
                .join(" ")
        })
        .collect()
}

fn exits(project: &Value) -> Vec<i64> {
    project["events"]
        .as_array()
        .unwrap()
        .iter()
        .map(|event| event["exit_code"].as_i64().unwrap())
        .collect()
}

#[test]
fn prepare_only_initializes_both_fixtures_outside_the_framework() {
    let Some(framework) = framework() else {
        eprintln!("companion framework absent; demo NOT_RUN");
        return;
    };
    let scratch = Scratch::new();
    let policies = policies(&scratch, &framework);
    let root = scratch.join("out");
    let before = inventory(&framework.join("examples"));
    let output = demo(&[
        "--framework",
        framework.to_str().unwrap(),
        "--policies",
        policies.to_str().unwrap(),
        "--output-root",
        root.to_str().unwrap(),
        "--run-id",
        "fixed-prepare",
        "--prepare-only",
    ]);
    let report = report(&output, &root, "fixed-prepare");

    assert_eq!(report["schema"], 1);
    assert_eq!(report["run_id"], "fixed-prepare");
    assert_eq!(report["fixture_execution"], "PASS");
    assert_eq!(report["model_calls"], 0);
    assert_eq!(report["projects"].as_array().unwrap().len(), 2);
    for (index, slug) in SLUGS.iter().enumerate() {
        let project = &report["projects"][index];
        assert_eq!(project["model_behavior"], "NOT_EVALUATED");
        assert_eq!(
            project["project"],
            root.join("fixed-prepare/candidates")
                .join(slug)
                .display()
                .to_string()
        );
        assert_eq!(
            project["state"],
            root.join("fixed-prepare/authority")
                .join(slug)
                .display()
                .to_string()
        );
        assert_eq!(
            project["policy"],
            policies.join(format!("{slug}.json")).display().to_string()
        );
        assert!(
            project["interactive_prompt"]
                .as_str()
                .unwrap()
                .contains(&format!("{slug}-persistence"))
        );
        assert_eq!(
            commands(project),
            vec![
                "expert prepare".to_owned(),
                format!("expert bind --expert experts/{slug}-persistence"),
                "check".to_owned(),
                "init".to_owned(),
            ]
        );
        assert_eq!(exits(project), [0, 0, 0, 0]);
        assert!(
            root.join("fixed-prepare/authority")
                .join(slug)
                .join("state.json")
                .is_file()
        );
        assert!(text(&output.stdout).contains(&format!("{slug}: INITIALIZED")));
    }

    // The single-hard-link runtime copy, recorded by digest.
    let runtime = root.join("fixed-prepare/runtime/devforge");
    let metadata = fs::metadata(&runtime).unwrap();
    assert_eq!(metadata.nlink(), 1, "the runtime copy must not be aliased");
    assert_eq!(metadata.permissions().mode() & 0o7777, 0o755);
    assert_eq!(
        report["runtime_sha256"],
        sha(&fs::read(&runtime).unwrap()),
        "the report must pin the copy it selected"
    );
    assert_eq!(report["runtime"], runtime.display().to_string());

    // Nothing in the framework changed: candidates and authority live outside it.
    assert_eq!(inventory(&framework.join("examples")), before);
    assert!(!framework.join(".poc/fixed-prepare").exists());
}

#[test]
fn a_full_run_reaches_verify_and_the_refresh_rebind() {
    let Some(framework) = framework() else {
        eprintln!("companion framework absent; demo NOT_RUN");
        return;
    };
    let scratch = Scratch::new();
    let policies = policies(&scratch, &framework);
    let root = scratch.join("out");
    let before = inventory(&framework.join("examples"));
    let output = demo(&[
        "--framework",
        framework.to_str().unwrap(),
        "--policies",
        policies.to_str().unwrap(),
        "--output-root",
        root.to_str().unwrap(),
        "--run-id",
        "fixed-full",
    ]);
    let report = report(&output, &root, "fixed-full");
    for (index, slug) in SLUGS.iter().enumerate() {
        let project = &report["projects"][index];
        let bind = format!("expert bind --expert experts/{slug}-persistence");
        assert_eq!(
            commands(project),
            vec![
                "expert prepare".to_owned(),
                bind.clone(),
                "check".to_owned(),
                "init".to_owned(),
                "green".to_owned(),
                "red".to_owned(),
                "green".to_owned(),
                "accept".to_owned(),
                "verify".to_owned(),
                "expert status".to_owned(),
                "check".to_owned(),
                bind,
                "check".to_owned(),
            ]
        );
        // The two deliberate refusals: GREEN without RED, and the stale expert.
        assert_eq!(exits(project), [0, 0, 0, 0, 2, 0, 0, 0, 0, 0, 2, 0, 0]);
        let events = project["events"].as_array().unwrap();
        assert_eq!(events[4]["result"]["status"], "BLOCKED");
        assert_eq!(events[8]["result"]["status"], "VERIFIED");
        assert_eq!(events[10]["result"]["status"], "STALE");
        assert!(
            text(&output.stdout).contains(&format!("{slug}: VERIFIED + STALE/REFRESH CHECKED"))
        );
        // The accepted original is preserved beside the refresh copy.
        let candidates = root.join("fixed-full/candidates");
        assert!(candidates.join(slug).join("src/store.py").is_file());
        assert!(
            candidates
                .join(format!("{slug}-refresh"))
                .join("docs/story.md")
                .is_file()
        );
        assert_eq!(
            project["project"],
            candidates.join(slug).display().to_string()
        );
    }
    assert_eq!(inventory(&framework.join("examples")), before);
}

#[test]
fn an_output_root_inside_the_framework_is_refused_before_any_write() {
    let Some(framework) = framework() else {
        eprintln!("companion framework absent; demo NOT_RUN");
        return;
    };
    let scratch = Scratch::new();
    let policies = policies(&scratch, &framework);
    let before = inventory(&framework.join("examples"));
    // A scratch copy of the framework layout, so a broken guard could only ever
    // write here rather than into the shared checkout.
    let copy = seeded_framework(&scratch, &framework);
    let inside = copy.join(".poc/demo-refusal-probe");
    let output = demo(&[
        "--framework",
        copy.to_str().unwrap(),
        "--policies",
        policies.to_str().unwrap(),
        "--output-root",
        inside.to_str().unwrap(),
    ]);
    blocked(&output, "output root must be outside the framework");
    assert!(
        !inside.exists(),
        "a refusal must not create the output root"
    );
    assert_eq!(inventory(&framework.join("examples")), before);
}

#[test]
fn a_missing_policy_is_refused_before_any_candidate_is_written() {
    let Some(framework) = framework() else {
        eprintln!("companion framework absent; demo NOT_RUN");
        return;
    };
    let scratch = Scratch::new();
    let policies = policies(&scratch, &framework);
    fs::remove_file(policies.join("notes-json.json")).unwrap();
    let root = scratch.join("out");
    let output = demo(&[
        "--framework",
        framework.to_str().unwrap(),
        "--policies",
        policies.to_str().unwrap(),
        "--output-root",
        root.to_str().unwrap(),
        "--run-id",
        "fixed-missing",
        "--prepare-only",
    ]);
    blocked(&output, "notes-json.json");
    assert!(
        !root.join("fixed-missing/candidates").exists(),
        "every policy is read before the first candidate is copied"
    );
}

#[test]
fn an_existing_run_directory_is_refused() {
    let Some(framework) = framework() else {
        eprintln!("companion framework absent; demo NOT_RUN");
        return;
    };
    let scratch = Scratch::new();
    let policies = policies(&scratch, &framework);
    let root = scratch.join("out");
    fs::create_dir_all(root.join("fixed-existing")).unwrap();
    fs::write(root.join("fixed-existing/keep.txt"), b"prior evidence\n").unwrap();
    let output = demo(&[
        "--framework",
        framework.to_str().unwrap(),
        "--policies",
        policies.to_str().unwrap(),
        "--output-root",
        root.to_str().unwrap(),
        "--run-id",
        "fixed-existing",
        "--prepare-only",
    ]);
    blocked(&output, "run directory already exists");
    assert_eq!(
        fs::read(root.join("fixed-existing/keep.txt")).unwrap(),
        b"prior evidence\n",
        "prior evidence must never be overwritten"
    );
}

#[test]
fn an_unexpected_gate_status_stops_the_run_and_writes_no_report() {
    let Some(framework) = framework() else {
        eprintln!("companion framework absent; demo NOT_RUN");
        return;
    };
    let scratch = Scratch::new();
    let policies = policies(&scratch, &framework);
    // A test root the fixture does not have: `check` refuses where the demo
    // requires success, which must stop the run rather than be recorded.
    let path = policies.join("notes-sqlite.json");
    let mut policy: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    policy["test_root"] = serde_json::json!("absent-tests");
    fs::write(&path, serde_json::to_vec_pretty(&policy).unwrap()).unwrap();
    let root = scratch.join("out");
    let output = demo(&[
        "--framework",
        framework.to_str().unwrap(),
        "--policies",
        policies.to_str().unwrap(),
        "--output-root",
        root.to_str().unwrap(),
        "--run-id",
        "fixed-unexpected",
        "--prepare-only",
    ]);
    blocked(
        &output,
        "unexpected gate result: check exited 2 where success=true",
    );
    // The refusal names the corrupted policy's effect and carries the refusing
    // command's own captured output, not just "something failed".
    blocked(&output, "source outside approved layout");
    blocked(&output, "\"status\":\"BLOCKED\"");
    assert!(
        !root.join("fixed-unexpected/demo-report.json").exists(),
        "a stopped run must not leave a report claiming PASS"
    );
    // The candidate that was already prepared stays for inspection, and the
    // captured output of the refusing command is in the reason.
    assert!(
        root.join("fixed-unexpected/candidates/notes-sqlite")
            .is_dir()
    );
}

/// A scratch framework carrying only `examples/notes-sqlite/seed`, so a case can
/// mutate a fixture without touching the shared checkout.
fn seeded_framework(scratch: &Scratch, real: &Path) -> PathBuf {
    let root = scratch.join("framework-copy");
    let seed = root.join("examples/notes-sqlite/seed");
    fs::create_dir_all(&seed).unwrap();
    let source = real.join("examples/notes-sqlite/seed");
    for entry in fs::read_dir(&source).unwrap().flatten() {
        let path = entry.path();
        if path.is_file() {
            fs::copy(&path, seed.join(path.file_name().unwrap())).unwrap();
        }
    }
    root
}

#[test]
fn a_run_id_that_escapes_the_output_root_is_refused() {
    let Some(framework) = framework() else {
        eprintln!("companion framework absent; demo NOT_RUN");
        return;
    };
    let scratch = Scratch::new();
    let policies = policies(&scratch, &framework);
    let root = scratch.join("out/nested");
    for escaping in ["../escape", "..", "a/b", "with space", ""] {
        let output = demo(&[
            "--framework",
            framework.to_str().unwrap(),
            "--policies",
            policies.to_str().unwrap(),
            "--output-root",
            root.to_str().unwrap(),
            "--run-id",
            escaping,
            "--prepare-only",
        ]);
        blocked(
            &output,
            "--run-id must be a single nonempty [A-Za-z0-9_-] component",
        );
        // Nothing is created inside the output root, beside it, or above it.
        assert!(!root.exists(), "{escaping:?} created the output root");
        assert!(
            !scratch.join("out/escape").exists() && !scratch.join("escape").exists(),
            "{escaping:?} created a directory outside the output root"
        );
    }
}

#[test]
fn a_symlink_inside_a_fixture_is_refused_rather_than_followed() {
    let Some(framework) = framework() else {
        eprintln!("companion framework absent; demo NOT_RUN");
        return;
    };
    let scratch = Scratch::new();
    let policies = policies(&scratch, &framework);
    let copy = seeded_framework(&scratch, &framework);
    // Sorted first, so the refusal precedes every other entry's copy.
    let target = scratch.join("outside-target");
    fs::write(&target, b"never copied\n").unwrap();
    let link = copy.join("examples/notes-sqlite/seed/aaa-link");
    std::os::unix::fs::symlink(&target, &link).unwrap();
    let root = scratch.join("out");
    let output = demo(&[
        "--framework",
        copy.to_str().unwrap(),
        "--policies",
        policies.to_str().unwrap(),
        "--output-root",
        root.to_str().unwrap(),
        "--run-id",
        "fixed-symlink",
        "--prepare-only",
    ]);
    blocked(&output, "symlink in fixture: ");
    blocked(&output, "aaa-link");
    let candidate = root.join("fixed-symlink/candidates/notes-sqlite");
    assert!(
        !candidate.join("aaa-link").exists(),
        "the symlink must not be followed into the candidate"
    );
    assert!(
        !root.join("fixed-symlink/demo-report.json").exists(),
        "a refused copy must not leave a report"
    );
}
