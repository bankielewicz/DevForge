//! Parity, refusal and read-only tests for the compiled phase-state reader.
//!
//! Every expectation is an oracle comparison against the unchanged legacy
//! runtime: `python3 -I -B runtime/delivery/controller.py status --state <dir>`
//! is executed from the repository source tree and its JSON is re-serialized
//! exactly as `devforge delivery` prints its own result, so the two stdout byte
//! strings and the two exit codes must be equal. Fixtures are built only with
//! the legacy runtime (`devforge delivery init` / `advance`).

use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::fs;
use std::os::unix::fs::{PermissionsExt, symlink};
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant, SystemTime};

static COUNTER: AtomicU64 = AtomicU64::new(0);

fn binary() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_devforge"))
}

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn digest(raw: &[u8]) -> String {
    format!("{:x}", Sha256::digest(raw))
}

fn json_bytes(value: &Value) -> Vec<u8> {
    let mut raw = serde_json::to_vec_pretty(value).expect("serializable fixture");
    raw.push(b'\n');
    raw
}

fn write(path: &Path, raw: &[u8]) {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).expect("fixture parent");
    }
    fs::write(path, raw).expect("fixture bytes");
}

fn unique_root(tag: &str) -> PathBuf {
    let root = std::env::temp_dir().join(format!(
        "devforge-phase-state-{}-{}-{tag}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir_all(&root).expect("fixture root");
    root
}

fn utc(offset: i64) -> String {
    // A fixed-offset aware UTC stamp, formatted like `datetime.isoformat()`.
    let seconds = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .expect("clock")
        .as_secs() as i64
        + offset;
    let (days, rest) = (seconds.div_euclid(86_400), seconds.rem_euclid(86_400));
    let (year, month, day) = civil(days);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}+00:00",
        rest / 3600,
        (rest % 3600) / 60,
        rest % 60
    )
}

fn civil(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    (if month <= 2 { year + 1 } else { year }, month, day)
}

// ---------------------------------------------------------------------------
// Fixture construction, legacy runtime only
// ---------------------------------------------------------------------------

struct Fixture {
    root: PathBuf,
    project: PathBuf,
    operator: PathBuf,
    state: PathBuf,
    session_path: PathBuf,
    delivery_path: PathBuf,
    installed_path: PathBuf,
    input_path: PathBuf,
    mode: String,
}

impl Fixture {
    /// `devforge delivery init --contract <session> --state <state>` on a
    /// synthetic project; the returned fixture is ACTIVE in Recover.
    fn build(tag: &str, mode: &str, deadline_offset: i64) -> Fixture {
        let root = unique_root(tag);
        let project = root.join("project");
        let operator = root.join("operator");
        let state = root.join("state");
        let input_path = project.join("inputs/context.txt");
        let installed_path = project.join("installed/SKILL.md");
        let assignment_path = operator.join("assignment.md");
        write(
            &input_path,
            b"A volunteer mentioned uncertainty about shift handoffs.\n",
        );
        write(&installed_path, b"Synthetic installed resource bytes.\n");
        write(
            &assignment_path,
            b"Synthetic selected assignment bytes; no native authority.\n",
        );
        let mut outputs = Vec::new();
        if mode == "brainstorm" {
            outputs.push(json!({
                "path": "docs/ideas.md", "artifact_id": "IDEAS-001",
                "artifact_type": "idea-ledger", "revision": 1,
                "sections": ["IDEAS-SECTION-001"]
            }));
        }
        outputs.push(json!({
            "path": "docs/handoff.md", "artifact_id": "HANDOFF-001",
            "artifact_type": "handoff", "revision": 1, "sections": ["NEXT-001"]
        }));
        let delivery = json!({
            "schema_version": "devforge.delivery-task/v1",
            "task_id": "PHASE-TEST-001",
            "project_root": project.to_str().expect("utf-8 fixture path"),
            "mode": mode,
            "inputs": [{"path": "inputs/context.txt",
                        "sha256": digest(&fs::read(&input_path).expect("input"))}],
            "outputs": outputs,
        });
        let delivery_path = operator.join("delivery-contract.json");
        write(&delivery_path, &json_bytes(&delivery));
        let baselines: Vec<Value> = delivery["outputs"]
            .as_array()
            .expect("outputs")
            .iter()
            .map(|output| {
                json!({"path": output["path"], "sha256": Value::Null,
                       "archive": Value::Null, "allow_unchanged": false})
            })
            .collect();
        let session = json!({
            "schema_version": "devforge.brainstorm-session/v1",
            "task_id": "PHASE-TEST-001",
            "provider": "codex",
            "delivery_contract": delivery_path.to_str().expect("utf-8 fixture path"),
            "delivery_contract_sha256": digest(&fs::read(&delivery_path).expect("contract")),
            "assignment": {"path": assignment_path.to_str().expect("utf-8 fixture path"),
                           "sha256": digest(&fs::read(&assignment_path).expect("assignment"))},
            "installed_inputs": [{"path": installed_path.to_str().expect("utf-8 fixture path"),
                                  "sha256": digest(&fs::read(&installed_path).expect("installed"))}],
            "checkpoint_path": "checkpoints/current.json",
            "receipt_path": operator.join("receipt.json").to_str().expect("utf-8 fixture path"),
            "deadline_utc": utc(deadline_offset),
            "max_corrections_per_phase": 1,
            "output_baselines": baselines,
        });
        let session_path = operator.join("session-contract.json");
        write(&session_path, &json_bytes(&session));
        let initialized = run(&[
            "delivery",
            "init",
            "--contract",
            session_path.to_str().expect("utf-8"),
            "--state",
            state.to_str().expect("utf-8"),
        ]);
        assert!(
            initialized.status.success(),
            "legacy init failed: {}{}",
            String::from_utf8_lossy(&initialized.stdout),
            String::from_utf8_lossy(&initialized.stderr)
        );
        Fixture {
            root,
            project,
            operator,
            state,
            session_path,
            delivery_path,
            installed_path,
            input_path,
            mode: mode.to_owned(),
        }
    }

    fn active(tag: &str) -> Fixture {
        Fixture::build(tag, "brainstorm", 6 * 3600)
    }

    fn challenge(&self) -> String {
        legacy(&self.state)
            .0
            .get("challenge")
            .and_then(Value::as_str)
            .expect("an admitted challenge")
            .to_owned()
    }

    fn phase(&self) -> String {
        legacy(&self.state)
            .0
            .get("phase")
            .and_then(Value::as_str)
            .expect("a current phase")
            .to_owned()
    }

    fn checkpoint(&self, state: &str, content: Value) {
        let value = json!({
            "schema_version": "devforge.brainstorm-checkpoint/v1",
            "task_id": "PHASE-TEST-001",
            "phase": self.phase(),
            "challenge": self.challenge(),
            "state": state,
            "content": content,
        });
        write(
            &self.project.join("checkpoints/current.json"),
            &json_bytes(&value),
        );
    }

    fn advance(&self) -> Output {
        run(&[
            "delivery",
            "advance",
            "--state",
            self.state.to_str().expect("utf-8"),
        ])
    }

    fn artifact(&self, identity: &str, role: &str, body: &str, upstream: Option<Value>) -> Vec<u8> {
        let mut envelope = Map::new();
        envelope.insert("schema_version".to_owned(), json!("devforge.artifact/v1"));
        envelope.insert("artifact_id".to_owned(), json!(identity));
        envelope.insert("artifact_type".to_owned(), json!(role));
        envelope.insert("revision".to_owned(), json!(1));
        envelope.insert("status".to_owned(), json!("draft"));
        if let Some(upstream) = upstream {
            envelope.insert("upstream".to_owned(), upstream);
        }
        let mut raw = b"---\n".to_vec();
        raw.extend_from_slice(&json_bytes(&Value::Object(envelope)));
        raw.extend_from_slice(b"---\n\n");
        raw.extend_from_slice(body.as_bytes());
        raw
    }

    fn write_outputs(&self) {
        let ledger = self.artifact(
            "IDEAS-001",
            "idea-ledger",
            "## Ideas [IDEAS-SECTION-001]\n\nA reminder remains an unadopted proposal.\n",
            None,
        );
        let upstream = if self.mode == "brainstorm" {
            write(&self.project.join("docs/ideas.md"), &ledger);
            Some(json!([{
                "artifact_id": "IDEAS-001", "revision": 1, "store": "project",
                "path": "docs/ideas.md", "sha256": digest(&ledger),
                "sections": ["IDEAS-SECTION-001"]
            }]))
        } else {
            None
        };
        let handoff = self.artifact(
            "HANDOFF-001",
            "handoff",
            "## Next [NEXT-001]\n\nAsk a volunteer for one concrete observation.\n",
            upstream,
        );
        write(&self.project.join("docs/handoff.md"), &handoff);
    }

    /// Drive the legacy runtime to READY through every required phase.
    fn drive_to_ready(&self) {
        loop {
            let phase = self.phase();
            let content = match phase.as_str() {
                "Recover" => json!({
                    "known_ideas": ["Volunteers lose context between shifts"],
                    "known_decisions": [], "missing_inputs": []
                }),
                "Explore" => json!({
                    "ideas": [{"idea_id": "IDEA-1", "people": "Volunteers",
                               "problem": "Handoff context is lost",
                               "outcome": "One shared note", "alternatives": [],
                               "open_questions": []}],
                    "missing_inputs": []
                }),
                "Record" => {
                    self.write_outputs();
                    json!({"ledger_path": "docs/ideas.md"})
                }
                "Focus" => {
                    self.write_outputs();
                    json!({
                        "next_action": "Ask a volunteer for one concrete observation",
                        "owner": "Shift coordinator",
                        "completion_evidence": "One written observation exists",
                        "non_goals": [], "handoff_path": "docs/handoff.md"
                    })
                }
                other => panic!("unexpected phase {other}"),
            };
            self.checkpoint("ready", content);
            let advanced = self.advance();
            assert!(
                advanced.status.success(),
                "legacy advance failed in {phase}: {}",
                String::from_utf8_lossy(&advanced.stdout)
            );
            if legacy(&self.state).0["status"] == json!("READY") {
                return;
            }
        }
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        // The runtime cache is created read-only next to the state root.
        let _ = restore_modes(&self.root);
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn restore_modes(root: &Path) -> std::io::Result<()> {
    let mut stack = vec![root.to_path_buf()];
    while let Some(path) = stack.pop() {
        let meta = fs::symlink_metadata(&path)?;
        if meta.is_dir() {
            fs::set_permissions(&path, fs::Permissions::from_mode(0o755))?;
            for entry in fs::read_dir(&path)? {
                stack.push(entry?.path());
            }
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// The oracle
// ---------------------------------------------------------------------------

fn run(arguments: &[&str]) -> Output {
    Command::new(binary())
        .args(arguments)
        .current_dir(repository())
        .stdin(Stdio::null())
        .output()
        .expect("devforge runs")
}

fn compiled(state: &Path) -> (String, i32) {
    let output = run(&[
        "delivery",
        "status",
        "--state",
        state.to_str().expect("utf-8"),
    ]);
    (
        String::from_utf8_lossy(&output.stdout).into_owned(),
        output.status.code().expect("an exit code"),
    )
}

/// The unchanged legacy controller, executed from the repository source tree.
fn legacy(state: &Path) -> (Value, i32) {
    let output = Command::new("/usr/bin/python3")
        .args(["-I", "-B"])
        .arg(repository().join("runtime/delivery/controller.py"))
        .arg("status")
        .arg("--state")
        .arg(state)
        .current_dir(repository())
        .stdin(Stdio::null())
        .output()
        .expect("the legacy controller runs");
    let value: Value = serde_json::from_slice(&output.stdout).unwrap_or_else(|error| {
        panic!(
            "legacy controller emitted non-JSON ({error}): {}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        )
    });
    (value, output.status.code().expect("an exit code"))
}

/// `devforge delivery` prints `serde_json::to_string_pretty` of the result.
fn rendered(value: &Value) -> String {
    format!("{}\n", serde_json::to_string_pretty(value).expect("JSON"))
}

fn assert_parity(state: &Path, note: &str) -> Value {
    let (expected, expected_code) = legacy(state);
    let (observed, observed_code) = compiled(state);
    assert_eq!(
        observed,
        rendered(&expected),
        "stdout parity broke for {note}"
    );
    assert_eq!(
        observed_code, expected_code,
        "exit-code parity broke for {note}"
    );
    expected
}

fn issue(value: &Value) -> String {
    value["issues"][0]
        .as_str()
        .unwrap_or_else(|| panic!("an issue string in {value}"))
        .to_owned()
}

// ---------------------------------------------------------------------------
// Parity across every constructed journal
// ---------------------------------------------------------------------------

#[test]
fn the_legacy_controller_and_the_compiled_status_agree() {
    let active = Fixture::active("agree-active");
    let observed = assert_parity(&active.state, "a freshly admitted Recover journal");
    assert_eq!(observed["status"], json!("ACTIVE"));
    assert_eq!(observed["phase"], json!("Recover"));
    assert_eq!(observed["persisted_status"], json!("ACTIVE"));
    assert_eq!(observed["admitted"], json!(true));

    let handoff = Fixture::build("agree-handoff", "handoff-only", 6 * 3600);
    let observed = assert_parity(&handoff.state, "a handoff-only journal");
    assert_eq!(
        observed["phase_applicability"]["Record"],
        json!("NOT_APPLICABLE")
    );
    assert_eq!(observed["not_applicable"]["Explore"], json!("handoff-only"));

    let waiting = Fixture::active("agree-waiting");
    waiting.checkpoint(
        "awaiting_user",
        json!({"question": "Which volunteer group?",
               "blocking_dependency": "A named coordinator"}),
    );
    assert!(waiting.advance().status.success());
    let observed = assert_parity(&waiting.state, "a pending user question");
    assert_eq!(observed["status"], json!("WAITING_USER"));
    assert_eq!(observed["question"], json!("Which volunteer group?"));
    assert_eq!(
        observed["blocking_dependency"],
        json!("A named coordinator")
    );

    let corrected = Fixture::active("agree-correction");
    corrected.checkpoint(
        "ready",
        json!({"known_ideas": [], "known_decisions": [], "missing_inputs": []}),
    );
    assert!(!corrected.advance().status.success());
    let observed = assert_parity(&corrected.state, "one spent correction");
    assert_eq!(observed["status"], json!("ACTIVE"));
    assert_eq!(observed["corrections"], json!(1));
    assert!(
        observed["instructions"]
            .as_str()
            .expect("instructions")
            .ends_with("Correct the reported checkpoint issues; no phase has advanced.")
    );

    corrected.checkpoint(
        "ready",
        json!({"known_ideas": [], "known_decisions": [], "missing_inputs": []}),
    );
    assert!(!corrected.advance().status.success());
    let observed = assert_parity(&corrected.state, "an exhausted correction budget");
    assert_eq!(observed["status"], json!("FAIL"));
    assert_eq!(observed["terminal"], json!(true));
    assert_eq!(observed["corrections"], json!(2));
}

#[test]
fn every_intermediate_phase_view_matches_the_legacy_controller() {
    let fixture = Fixture::active("phases");
    for expected in ["Recover", "Explore", "Record"] {
        let observed = assert_parity(&fixture.state, expected);
        assert_eq!(observed["phase"], json!(expected));
        let content = match expected {
            "Recover" => json!({"known_ideas": ["One known idea"], "known_decisions": [],
                                "missing_inputs": []}),
            "Explore" => json!({"ideas": [{"idea_id": "IDEA-1", "people": "Volunteers",
                                           "problem": "Lost context", "outcome": "A shared note",
                                           "alternatives": [], "open_questions": []}],
                                "missing_inputs": []}),
            _ => {
                fixture.write_outputs();
                json!({"ledger_path": "docs/ideas.md"})
            }
        };
        fixture.checkpoint("ready", content);
        assert!(fixture.advance().status.success());
    }
    let observed = assert_parity(&fixture.state, "Focus");
    assert_eq!(observed["phase"], json!("Focus"));
}

// ---------------------------------------------------------------------------
// Refusals
// ---------------------------------------------------------------------------

#[test]
fn a_missing_or_unreadable_state_root_is_refused_exactly_as_before() {
    let fixture = Fixture::active("missing-state");
    let absent = fixture.root.join("absent-state");
    // `checked_path` in the CLI boundary refuses before any engine selection.
    let (observed, code) = compiled(&absent);
    assert_eq!(code, 2);
    let value: Value = serde_json::from_str(&observed).expect("JSON result");
    assert_eq!(value["status"], json!("COULD_NOT_RUN"));

    let (legacy_value, legacy_code) = legacy(&absent);
    assert_eq!(legacy_code, 2);
    assert_eq!(
        issue(&legacy_value),
        "workflow manifest: required path does not exist"
    );

    // A state root that exists but holds no manifest is refused by the CLI
    // boundary (`project_for` reads MANIFEST.json in Rust before any engine is
    // selected). That predates this port; the reader is never consulted.
    let empty = fixture.root.join("empty-state");
    fs::create_dir_all(&empty).expect("empty state");
    let (observed, code) = compiled(&empty);
    assert_eq!(code, 2);
    let value: Value = serde_json::from_str(&observed).expect("JSON result");
    assert_eq!(value["status"], json!("COULD_NOT_RUN"));
    assert_eq!(
        issue(&legacy(&empty).0),
        "workflow manifest: required path does not exist"
    );
}

#[test]
fn a_state_root_reached_through_a_symlink_component_is_refused() {
    let fixture = Fixture::active("symlink-root");
    let link = fixture.root.join("linked");
    symlink(&fixture.state, &link).expect("symlink");
    let (observed, code) = compiled(&link);
    assert_eq!(code, 2, "a symlinked state root must not be admitted");
    assert!(
        observed.contains("symlink"),
        "the CLI boundary reports the symlink: {observed}"
    );
    let (value, legacy_code) = legacy(&link);
    assert_eq!(legacy_code, 2);
    assert_eq!(
        issue(&value),
        "workflow manifest: a path component is a symlink or is not a directory"
    );
}

#[test]
fn a_symlinked_journal_component_is_refused_exactly_as_before() {
    let fixture = Fixture::active("symlink-records");
    let records = fixture.state.join("records");
    let moved = fixture.state.join("records-real");
    fs::rename(&records, &moved).expect("move records");
    symlink(&moved, &records).expect("symlink records");
    let value = assert_parity(&fixture.state, "a symlinked records directory");
    assert_eq!(value["status"], json!("FAIL"));
    assert_eq!(
        issue(&value),
        "records: a path component is a symlink or is not a directory"
    );
}

#[test]
fn a_malformed_manifest_or_head_is_refused_exactly_as_before() {
    let fixture = Fixture::active("malformed-head");
    let head = fixture.state.join("HEAD.json");
    let original = fs::read(&head).expect("HEAD");
    let mut value: Value = serde_json::from_slice(&original).expect("HEAD JSON");
    value["manifest_sha256"] = json!("0".repeat(64));
    write(&head, &json_bytes(&value));
    let observed = assert_parity(&fixture.state, "a rebound HEAD digest");
    assert_eq!(issue(&observed), "protected manifest/HEAD binding mismatch");

    write(&head, b"{ this is not JSON");
    let observed = assert_parity(&fixture.state, "an unparsable HEAD");
    assert_eq!(observed["status"], json!("FAIL"));
    assert!(
        issue(&observed).starts_with("protected HEAD: invalid strict UTF-8 JSON ("),
        "unexpected refusal: {}",
        issue(&observed)
    );

    write(&head, &original);
    let manifest = fixture.state.join("MANIFEST.json");
    let mut padded = fs::read(&manifest).expect("manifest");
    padded.resize(1024 * 1024 + 1, b' ');
    write(&manifest, &padded);
    // An oversized manifest is refused by the CLI boundary's own bounded read
    // before any engine selection; that predates this port. The legacy engine
    // wording is recorded here so the divergence stays visible.
    let (observed, code) = compiled(&fixture.state);
    assert_eq!(code, 2, "{observed}");
    assert!(observed.contains("oversized file"), "{observed}");
    assert_eq!(
        issue(&legacy(&fixture.state).0),
        "workflow manifest: file exceeds 1048576 bytes"
    );
}

#[test]
fn an_object_whose_digest_does_not_match_its_name_is_refused() {
    let fixture = Fixture::active("object-digest");
    let records = fixture.state.join("records");
    let entry = fs::read_dir(&records)
        .expect("records")
        .next()
        .expect("one record")
        .expect("record entry")
        .path();
    let original = fs::read(&entry).expect("record bytes");
    let mut tampered = original.clone();
    tampered.extend_from_slice(b" ");
    fs::set_permissions(&records, fs::Permissions::from_mode(0o700)).expect("records mode");
    fs::write(&entry, &tampered).expect("tamper");
    let observed = assert_parity(&fixture.state, "a rewritten record object");
    assert_eq!(issue(&observed), "protected records object hash mismatch");

    fs::write(&entry, &original).expect("restore");
    let snapshots = fixture.state.join("snapshots");
    fs::set_permissions(&snapshots, fs::Permissions::from_mode(0o700)).expect("snapshots mode");
    let blob = fs::read_dir(&snapshots)
        .expect("snapshots")
        .next()
        .expect("one snapshot")
        .expect("snapshot entry")
        .path();
    let saved = fs::read(&blob).expect("snapshot bytes");
    let mut tampered = saved.clone();
    tampered.extend_from_slice(b" ");
    fs::write(&blob, &tampered).expect("tamper snapshot");
    let observed = assert_parity(&fixture.state, "a rewritten snapshot object");
    assert_eq!(issue(&observed), "protected snapshots object hash mismatch");
}

#[test]
fn an_unexpected_or_oversized_journal_object_is_refused() {
    let fixture = Fixture::active("object-shape");
    let records = fixture.state.join("records");
    fs::set_permissions(&records, fs::Permissions::from_mode(0o700)).expect("records mode");
    let stray = records.join("stray.txt");
    fs::write(&stray, b"stray\n").expect("stray record");
    let observed = assert_parity(&fixture.state, "a stray journal entry");
    assert_eq!(issue(&observed), "unexpected protected records entry");
    fs::remove_file(&stray).expect("remove stray");

    let entry = fs::read_dir(&records)
        .expect("records")
        .next()
        .expect("one record")
        .expect("record entry")
        .path();
    let mut padded = fs::read(&entry).expect("record bytes");
    padded.resize(1024 * 1024 + 1, b' ');
    fs::write(&entry, &padded).expect("oversize");
    let observed = assert_parity(&fixture.state, "an oversized journal object");
    assert_eq!(
        issue(&observed),
        "protected records object: file exceeds 1048576 bytes"
    );
}

#[test]
fn a_committed_record_removed_from_the_journal_cannot_reset_the_task() {
    let fixture = Fixture::active("missing-record");
    let records = fixture.state.join("records");
    fs::set_permissions(&records, fs::Permissions::from_mode(0o700)).expect("records mode");
    let entry = fs::read_dir(&records)
        .expect("records")
        .next()
        .expect("one record")
        .expect("record entry")
        .path();
    fs::remove_file(&entry).expect("remove record");
    let observed = assert_parity(&fixture.state, "a deleted committed record");
    assert_eq!(
        issue(&observed),
        "protected committed journal record is missing"
    );
}

#[test]
fn a_replaced_or_unusable_lock_is_refused_exactly_as_before() {
    let fixture = Fixture::active("lock-shape");
    let lock = fixture.state.join("LOCK");
    fs::set_permissions(&fixture.state, fs::Permissions::from_mode(0o700)).expect("state mode");
    fs::write(&lock, b"held\n").expect("nonempty lock");
    let observed = assert_parity(&fixture.state, "a nonempty lock file");
    assert_eq!(
        issue(&observed),
        "protected state lock is not an empty regular single-link file"
    );

    fs::remove_file(&lock).expect("remove lock");
    let observed = assert_parity(&fixture.state, "an absent lock file");
    assert_eq!(
        issue(&observed),
        "protected state lock: required path does not exist"
    );

    symlink(fixture.state.join("MANIFEST.json"), &lock).expect("symlinked lock");
    let observed = assert_parity(&fixture.state, "a symlinked lock file");
    assert_eq!(
        issue(&observed),
        "protected state lock is not an empty regular single-link file"
    );
}

#[test]
fn a_held_exclusive_lock_is_refused_by_the_legacy_acquisition() {
    let fixture = Fixture::active("lock-held");
    let lock = fixture.state.join("LOCK");
    // `flock(1)` holds LOCK_EX on the same inode for the duration of `sleep`.
    let mut holder = Command::new("/usr/bin/flock")
        .arg("--exclusive")
        .arg(&lock)
        .arg("/usr/bin/sleep")
        .arg("5")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("flock(1) runs");
    // Wait until the kernel actually records the lock before observing.
    let deadline = Instant::now() + Duration::from_secs(4);
    while Instant::now() < deadline {
        if fs::read_to_string("/proc/locks")
            .map(|table| table.contains("FLOCK"))
            .unwrap_or(false)
        {
            break;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    let (observed, code) = compiled(&fixture.state);
    let _ = holder.kill();
    let _ = holder.wait();
    assert_eq!(code, 2, "a contended journal must not be read: {observed}");
    let value: Value = serde_json::from_str(&observed).expect("JSON result");
    assert_eq!(
        issue(&value),
        "another protected phase operation owns the exclusive lock"
    );
}

#[test]
fn a_changed_contract_or_installed_pin_is_refused_exactly_as_before() {
    let fixture = Fixture::active("pins");
    let original = fs::read(&fixture.delivery_path).expect("contract");
    // Same JSON, different bytes: only the selected digest can notice.
    let mut respaced = original.clone();
    respaced.push(b'\n');
    write(&fixture.delivery_path, &respaced);
    let observed = assert_parity(&fixture.state, "a rewritten delivery contract");
    assert_eq!(
        issue(&observed),
        "delivery contract: selected SHA-256 mismatch"
    );
    write(&fixture.delivery_path, &original);

    let installed = fs::read(&fixture.installed_path).expect("installed");
    write(
        &fixture.installed_path,
        b"Changed installed resource bytes.\n",
    );
    let observed = assert_parity(&fixture.state, "a rewritten installed input");
    assert!(
        issue(&observed).ends_with(": selected SHA-256 mismatch"),
        "unexpected refusal: {}",
        issue(&observed)
    );
    write(&fixture.installed_path, &installed);

    let input = fs::read(&fixture.input_path).expect("input");
    write(
        &fixture.input_path,
        b"Changed fixed delivery input bytes.\n",
    );
    let observed = assert_parity(&fixture.state, "a rewritten fixed input");
    assert_eq!(
        issue(&observed),
        "input inputs/context.txt: selected SHA-256 does not match current bytes"
    );
    write(&fixture.input_path, &input);

    let session = fs::read(&fixture.session_path).expect("session");
    let mut value: Value = serde_json::from_slice(&session).expect("session JSON");
    value["provider"] = json!("claude");
    write(&fixture.session_path, &json_bytes(&value));
    let observed = assert_parity(&fixture.state, "a rewritten session contract");
    assert_eq!(
        issue(&observed),
        "current session contract differs from protected initialization"
    );
    write(&fixture.session_path, &session);
    assert_parity(&fixture.state, "the restored selection");
}

#[test]
fn an_existing_receipt_without_a_completion_intent_is_refused() {
    let fixture = Fixture::active("stray-receipt");
    write(&fixture.operator.join("receipt.json"), b"{}\n");
    let observed = assert_parity(&fixture.state, "a receipt with no intent");
    assert_eq!(
        issue(&observed),
        "existing receipt has no matching protected completion intent"
    );
}

#[test]
fn an_expired_deadline_reports_historical_context_only() {
    let fixture = Fixture::build("expired", "brainstorm", 3);
    assert_eq!(legacy(&fixture.state).0["status"], json!("ACTIVE"));
    std::thread::sleep(Duration::from_secs(4));
    let observed = assert_parity(&fixture.state, "an expired original deadline");
    assert_eq!(observed["status"], json!("COULD_NOT_RUN"));
    assert_eq!(observed["expired"], json!(true));
    assert_eq!(observed["admitted"], json!(false));
    assert_eq!(observed["persisted_status"], json!("ACTIVE"));
    assert!(observed.get("challenge").is_none());
    assert!(observed.get("instructions").is_none());
    assert_eq!(
        issue(&observed),
        "original deadline expired; historical context only, no new admission"
    );
}

#[test]
fn a_leftover_partial_publication_is_inspected_without_recovery() {
    let fixture = Fixture::active("pending");
    let pending = fixture.state.join("pending");
    fs::set_permissions(&pending, fs::Permissions::from_mode(0o700)).expect("pending mode");
    let leftover = pending.join("0123456789abcdef0123456789abcdef.pending");
    fs::write(&leftover, b"interrupted publication bytes\n").expect("leftover");
    let observed = assert_parity(&fixture.state, "a leftover partial transition");
    assert_eq!(observed["status"], json!("ACTIVE"));
    assert!(
        leftover.exists(),
        "the reader must not remove interrupted publication work"
    );
}

// ---------------------------------------------------------------------------
// Read-only guarantee
// ---------------------------------------------------------------------------

fn manifest_of(root: &Path) -> Vec<String> {
    let mut entries = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(path) = stack.pop() {
        let meta = fs::symlink_metadata(&path).expect("state entry");
        let mode = meta.permissions().mode();
        let bytes = if meta.is_file() {
            digest(&fs::read(&path).unwrap_or_default())
        } else {
            String::from("-")
        };
        entries.push(format!(
            "{} {mode:o} {} {} {} {bytes}",
            path.display(),
            meta.len(),
            std::os::unix::fs::MetadataExt::mtime_nsec(&meta),
            std::os::unix::fs::MetadataExt::ino(&meta)
        ));
        if meta.is_dir() {
            for entry in fs::read_dir(&path).expect("state directory") {
                stack.push(entry.expect("state entry").path());
            }
        }
    }
    entries.sort();
    entries
}

#[test]
fn the_reader_changes_no_byte_mode_or_timestamp_under_the_state_root() {
    let fixture = Fixture::active("read-only");
    let before = manifest_of(&fixture.state);
    let (_, code) = compiled(&fixture.state);
    assert_eq!(code, 0);
    let after = manifest_of(&fixture.state);
    assert_eq!(before, after, "the compiled reader must not write");

    let waiting = Fixture::active("read-only-waiting");
    waiting.checkpoint(
        "awaiting_user",
        json!({"question": "Which group?", "blocking_dependency": "A coordinator"}),
    );
    assert!(waiting.advance().status.success());
    let before = manifest_of(&waiting.state);
    assert_eq!(compiled(&waiting.state).1, 0);
    assert_eq!(manifest_of(&waiting.state), before);
}

// ---------------------------------------------------------------------------
// Delegation: the legacy controller still owns every unported decision
// ---------------------------------------------------------------------------

#[test]
fn a_ready_journal_is_still_answered_by_the_legacy_delivery_checks() {
    let fixture = Fixture::active("ready");
    fixture.drive_to_ready();
    let observed = assert_parity(&fixture.state, "a READY journal");
    assert_eq!(observed["status"], json!("READY"));
    assert_eq!(observed["persisted_status"], json!("READY"));
}

#[test]
fn an_unsupported_workflow_state_schema_is_still_answered_by_the_controller() {
    let fixture = Fixture::active("schema");
    let manifest = fixture.state.join("MANIFEST.json");
    let mut value: Value =
        serde_json::from_slice(&fs::read(&manifest).expect("manifest")).expect("manifest JSON");
    value["schema_version"] = json!("devforge.brainstorm-state/v99");
    write(&manifest, &json_bytes(&value));
    let observed = assert_parity(&fixture.state, "an unsupported state schema");
    assert_eq!(issue(&observed), "unsupported workflow state schema");
}

#[test]
fn a_v2_reference_contract_is_still_answered_by_the_legacy_runtime() {
    let fixture = Fixture::active("v2-delegation");
    let mut value: Value =
        serde_json::from_slice(&fs::read(&fixture.delivery_path).expect("contract"))
            .expect("contract JSON");
    value["schema_version"] = json!("devforge.delivery-task/v2");
    write(&fixture.delivery_path, &json_bytes(&value));
    // The compiled reader does not decide delivery-task/v2 reference coverage.
    let observed = assert_parity(&fixture.state, "a v2 delivery contract");
    assert_eq!(observed["status"], json!("FAIL"));
    assert!(
        issue(&observed).starts_with("contract: expected exactly the fields"),
        "unexpected refusal: {}",
        issue(&observed)
    );
}

#[test]
fn other_delivery_actions_still_reach_the_python_controller() {
    let fixture = Fixture::active("other-actions");
    let checked = run(&[
        "delivery",
        "check",
        "--contract",
        fixture.delivery_path.to_str().expect("utf-8"),
    ]);
    let value: Value =
        serde_json::from_slice(&checked.stdout).expect("the controller answers check");
    assert_eq!(value["task_id"], json!("PHASE-TEST-001"));
    assert!(
        value.get("scope").is_some(),
        "check is still the legacy delivery_core result: {value}"
    );

    // `advance` is a mutation path and is not ported; it must still commit.
    fixture.checkpoint(
        "ready",
        json!({"known_ideas": [], "known_decisions": [], "missing_inputs": []}),
    );
    assert!(!fixture.advance().status.success());
    assert_eq!(legacy(&fixture.state).0["corrections"], json!(1));
    assert_parity(&fixture.state, "a journal advanced by the legacy runtime");
}

#[test]
fn the_ported_status_path_does_not_start_the_python_controller() {
    // The same binary, the same CLI boundary and the same runtime-cache
    // verification on both sides: the only difference is whether the reader
    // answers or hands the call to the Python controller. Comparing the binary
    // against itself cancels every fixed cost, and the minimum of several runs
    // keeps the observation usable under parallel test load.
    let ported = Fixture::active("no-python-ported");
    let delegated = Fixture::active("no-python-delegated");
    let mut value: Value =
        serde_json::from_slice(&fs::read(&delegated.delivery_path).expect("contract"))
            .expect("contract JSON");
    value["schema_version"] = json!("devforge.delivery-task/v2");
    write(&delegated.delivery_path, &json_bytes(&value));
    assert_eq!(compiled(&ported.state).1, 0);
    assert_eq!(compiled(&delegated.state).1, 2);

    let sample = |state: &Path| {
        (0..5)
            .map(|_| {
                let started = Instant::now();
                let _ = compiled(state);
                started.elapsed()
            })
            .min()
            .expect("one sample")
    };
    let without_python = sample(&ported.state);
    let with_python = sample(&delegated.state);
    assert!(
        without_python * 2 < with_python,
        "the compiled status path should not pay Python start-up: \
{without_python:?} answered in Rust vs {with_python:?} delegated"
    );

    // Source audit: the reader is consulted before the controller is built.
    let source = fs::read_to_string(repository().join("src/delivery.rs")).expect("delivery.rs");
    let route = source
        .find("crate::phase_state::status")
        .expect("the status route exists");
    let controller = source
        .find("python(&package, \"controller.py\")")
        .expect("the controller command exists");
    assert!(
        route < controller,
        "the reader must answer before the controller is built"
    );
}
