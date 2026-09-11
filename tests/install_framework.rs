//! Black-box acceptance tests for `devforge install framework`, the compiled
//! replacement for `scripts/install_framework.py`'s project-installation modes.
//!
//! Every case here drives the built executable through its command line and
//! inspects only stdout, the exit status and the filesystem. Stand-in runtimes
//! are `/bin/sh` scripts, so no interpreter outside the evaluation exception
//! participates, except in the legacy-oracle case, which deliberately runs the
//! unchanged legacy Python installer as the comparison baseline.
//!
//! A refusal proves the mechanical predicate it names; it is never native
//! activation, runtime admission or human acceptance.
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Mutex, MutexGuard};

const BIN: &str = env!("CARGO_BIN_EXE_devforge");
/// The legacy baseline the oracle case compares against; absent is a failure.
const PYTHON: &str = "/usr/bin/python3";
const PROVIDERS: [&str; 2] = ["codex", "claude"];
const REQUIRED_EVENTS: [&str; 4] = ["SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"];

static COUNTER: AtomicU64 = AtomicU64::new(0);

/// Writing a file this suite later executes races with forking in another test
/// thread: the forked child inherits the writer's descriptor and its exec then
/// fails with ETXTBSY. Every such write and every spawn takes this lock.
static EXECUTABLES: Mutex<()> = Mutex::new(());

fn executables() -> MutexGuard<'static, ()> {
    EXECUTABLES
        .lock()
        .unwrap_or_else(|error| error.into_inner())
}

// ---- fixtures ------------------------------------------------------------

struct Temp(PathBuf);
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn temp() -> Temp {
    // Fixtures live beside the test binary so hard-link cases share its filesystem.
    let path = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!(
        "devforge-install-framework-{}-{}",
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

fn write(path: &Path, bytes: &[u8]) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, bytes).unwrap();
}

fn read_json(path: &Path) -> Value {
    serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
}

/// Write an executable `/bin/sh` stand-in.
fn standin(dir: &Path, name: &str, body: &str) -> PathBuf {
    let path = dir.join(name);
    let lock = executables();
    fs::write(&path, format!("#!/bin/sh\n{body}")).unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
    drop(lock);
    assert_eq!(fs::metadata(&path).unwrap().nlink(), 1);
    path
}

/// A single-hard-link copy of the built CLI: selectable as `--runtime`, and
/// runnable as the validating executable of an installation.
fn copied(dir: &Path, name: &str) -> PathBuf {
    let path = dir.join(name);
    fs::create_dir_all(dir).unwrap();
    let lock = executables();
    fs::copy(BIN, &path).unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
    drop(lock);
    assert_eq!(fs::metadata(&path).unwrap().nlink(), 1);
    path
}

fn base_capabilities() -> Value {
    json!({
        "schema_version": "devforge.delivery-capabilities/v1",
        "protocol": "devforge.delivery-runtime/v1",
        "supported_providers": ["codex", "claude"],
        "completion_modes": ["managed-session"],
        "io_modes": ["inherited", "interactive-tty"],
        "hook_events": REQUIRED_EVENTS,
        "native_admission": "NOT_VALIDATED",
        "mechanical_scope":
            "phase evidence and persisted artifact verification; no semantic acceptance",
    })
}

fn extended_capabilities() -> Value {
    let mut value = base_capabilities();
    let map = value.as_object_mut().unwrap();
    for (key, item) in [
        (
            "utility_workflows",
            json!(["skill-builder", "skill-validator"]),
        ),
        (
            "utility_session_schema",
            json!("devforge.utility-session/v1"),
        ),
        (
            "utility_native_schedule_schema",
            json!("devforge.utility-native-schedule/v1"),
        ),
        ("native_execution_enabled", json!(false)),
        (
            "native_process_interface",
            json!("EXPLICIT_FROZEN_CONFIGURATION_REQUIRED"),
        ),
        (
            "native_process_receipt_schema",
            json!("devforge.native-process-receipt/v1"),
        ),
        (
            "native_semantic_review",
            json!("SEPARATE_SELECTED_OPERATOR_OR_INDEPENDENT_REVIEW"),
        ),
    ] {
        map.insert(key.into(), item);
    }
    value
}

/// The `InstallerTest.setUp` tree: one `demo` skill and a plugin manifest per
/// provider, an agents directory for each, and an empty project.
struct Fixture {
    dir: Temp,
    framework: PathBuf,
    project: PathBuf,
}

fn fixture() -> Fixture {
    let dir = temp();
    let framework = dir.0.join("framework");
    let project = dir.0.join("project");
    fs::create_dir(&project).unwrap();
    for provider in PROVIDERS {
        let plugin = framework.join(format!("providers/{provider}/plugins/devforgeai"));
        write(
            &plugin.join("skills/demo/SKILL.md"),
            format!("{provider} skill").as_bytes(),
        );
        write(
            &plugin.join(format!(".{provider}-plugin/plugin.json")),
            br#"{"name": "devforgeai"}"#,
        );
    }
    fs::create_dir(framework.join("providers/claude/plugins/devforgeai/agents")).unwrap();
    fs::create_dir_all(framework.join("providers/codex/agents")).unwrap();
    Fixture {
        dir,
        framework,
        project,
    }
}

impl Fixture {
    fn root(&self) -> &Path {
        &self.dir.0
    }

    fn plugin(&self, provider: &str) -> PathBuf {
        self.framework
            .join(format!("providers/{provider}/plugins/devforgeai"))
    }

    /// The Codex `demo` skill entrypoint the legacy suite mutates.
    fn skill(&self) -> PathBuf {
        self.plugin("codex").join("skills/demo/SKILL.md")
    }

    fn settings_path(&self, provider: &str) -> PathBuf {
        self.project.join(if provider == "codex" {
            ".codex/hooks.json"
        } else {
            ".claude/settings.local.json"
        })
    }

    fn settings(&self, provider: &str, document: &Value) -> PathBuf {
        let path = self.settings_path(provider);
        write(&path, document.to_string().as_bytes());
        path
    }

    fn settings_text(&self, provider: &str, raw: &str) -> PathBuf {
        let path = self.settings_path(provider);
        write(&path, raw.as_bytes());
        path
    }

    /// Write one framework hook source, optionally declaring it in the manifest.
    fn hook_source(&self, provider: &str, group: &Value, declared: bool) -> PathBuf {
        let plugin = self.plugin(provider);
        let path = plugin.join("hooks/hooks.json");
        write(
            &path,
            json!({"description": "framework delivery hooks", "hooks": {"Stop": [group]}})
                .to_string()
                .as_bytes(),
        );
        if declared {
            write(
                &plugin.join(format!(".{provider}-plugin/plugin.json")),
                json!({"name": "devforgeai", "hooks": "./hooks/hooks.json"})
                    .to_string()
                    .as_bytes(),
            );
        }
        path
    }

    /// The delivery sidecar plus the exact four-event synchronous hook source.
    fn delivery_requirement(&self, provider: &str) -> (PathBuf, Value) {
        let plugin = self.plugin(provider);
        let path = plugin.join("hooks/runtime-requirements.json");
        let requirement = json!({
            "schema_version": "devforge.runtime-requirement/v1",
            "runtime": "devforge.delivery",
            "protocol": "devforge.delivery-runtime/v1",
            "provider": provider,
            "completion_mode": "managed-session",
            "required_events": REQUIRED_EVENTS,
        });
        write(&path, requirement.to_string().as_bytes());
        let command = format!(
            "\"${{DEVFORGE_DELIVERY_EXECUTABLE:-devforge}}\" delivery hook --provider {provider}"
        );
        let mut hooks = serde_json::Map::new();
        for event in REQUIRED_EVENTS {
            hooks.insert(
                event.into(),
                json!([{"hooks": [{"type": "command", "command": command}]}]),
            );
        }
        write(
            &plugin.join("hooks/hooks.json"),
            json!({"hooks": hooks}).to_string().as_bytes(),
        );
        (path, requirement)
    }

    fn inventory(&self) -> Value {
        read_json(&self.project.join(".devforge-install.json"))
    }

    /// Every regular, non-symlink file below the project with its exact bytes.
    fn snapshot(&self) -> BTreeMap<String, Vec<u8>> {
        tree(&self.project)
    }

    fn entries(&self) -> Vec<String> {
        let mut names: Vec<String> = fs::read_dir(&self.project)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        names.sort();
        names
    }

    /// A stand-in that records execution before serving valid capabilities.
    fn marking(&self, marker: &Path) -> PathBuf {
        standin(
            self.root(),
            "devforge-marking",
            &format!(
                "printf 'executed\\n' > {}\nprintf '%s' {}\n",
                quote(marker.to_str().unwrap()),
                quote(&base_capabilities().to_string())
            ),
        )
    }

    fn serving(&self, payload: &str) -> PathBuf {
        standin(
            self.root(),
            "devforge-standin",
            &format!("printf '%s' {}\n", quote(payload)),
        )
    }
}

fn tree(root: &Path) -> BTreeMap<String, Vec<u8>> {
    fn visit(root: &Path, dir: &Path, seen: &mut BTreeMap<String, Vec<u8>>) {
        for entry in fs::read_dir(dir).unwrap() {
            let path = entry.unwrap().path();
            let meta = fs::symlink_metadata(&path).unwrap();
            if meta.is_dir() {
                visit(root, &path, seen);
            } else if meta.is_file() {
                seen.insert(
                    path.strip_prefix(root)
                        .unwrap()
                        .to_string_lossy()
                        .into_owned(),
                    fs::read(&path).unwrap(),
                );
            }
        }
    }
    let mut seen = BTreeMap::new();
    visit(root, root, &mut seen);
    seen
}

fn hook_group(command: &str) -> Value {
    json!({
        "hooks": [{"type": "command", "command": command, "timeout": 3}],
        "matcher": "",
        "custom_group_metadata": {"retained": true},
    })
}

fn codex_group() -> Value {
    hook_group("devforge delivery hook --provider codex")
}

/// The canonical group identity the inventory records, exactly as the legacy
/// `group_digest` computed it: compact, key-sorted, UTF-8 JSON.
fn group_digest(group: &Value) -> String {
    sha(group.to_string().as_bytes())
}

// ---- invocation ----------------------------------------------------------

struct Run {
    code: i32,
    stdout: String,
    stderr: String,
}

fn spawn(binary: &Path, args: &[&str], envs: &[(&str, &str)]) -> Run {
    let lock = executables();
    let mut command = Command::new(binary);
    command
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for (key, value) in envs {
        command.env(key, value);
    }
    let child = command.spawn().unwrap();
    drop(lock);
    let output = child.wait_with_output().unwrap();
    Run {
        code: output.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    }
}

/// Run `install framework` through an explicitly selected DevForge executable,
/// which is the validating authority for that installation.
fn install_from(binary: &Path, fixture: &Fixture, extra: &[&str], envs: &[(&str, &str)]) -> Run {
    let mut args: Vec<String> = vec![
        "--project".into(),
        fixture.project.to_string_lossy().into_owned(),
        "install".into(),
        "framework".into(),
        "--framework".into(),
        fixture.framework.to_string_lossy().into_owned(),
    ];
    args.extend(extra.iter().map(|value| (*value).to_string()));
    let refs: Vec<&str> = args.iter().map(String::as_str).collect();
    spawn(binary, &refs, envs)
}

fn install(fixture: &Fixture, extra: &[&str]) -> Run {
    install_from(Path::new(BIN), fixture, extra, &[])
}

fn installed(fixture: &Fixture, extra: &[&str]) -> Value {
    let result = install(fixture, extra);
    assert_eq!(
        result.code, 0,
        "expected INSTALLED; stdout={} stderr={}",
        result.stdout, result.stderr
    );
    let value: Value = serde_json::from_str(&result.stdout).expect("result must be JSON");
    assert_eq!(value["status"], "INSTALLED", "result={value}");
    value
}

fn reason(result: &Run) -> String {
    assert_eq!(
        result.code, 2,
        "expected a refusal; stdout={} stderr={}",
        result.stdout, result.stderr
    );
    let report: Value = serde_json::from_str(&result.stdout).expect("refusal must be JSON");
    assert_eq!(report["status"], "BLOCKED", "report={report}");
    report["reason"].as_str().unwrap_or_default().to_string()
}

fn blocked(result: &Run, expected: &str) {
    assert_eq!(reason(result), expected);
}

fn blocked_with(result: &Run, fragment: &str) {
    let text = reason(result);
    assert!(text.contains(fragment), "reason={text}");
}

// ---- installation ---------------------------------------------------------

/// Legacy `test_promoted_codex_expert_requires_evidence_before_any_install_write`.
#[test]
fn promoted_codex_expert_requires_evidence_before_any_install_write() {
    let fixture = fixture();
    let skills = fixture.plugin("codex").join("skills");
    fs::rename(skills.join("demo"), skills.join("devforge-evaluate-expert")).unwrap();
    let before = fixture.snapshot();
    blocked(
        &install(&fixture, &[]),
        "manual expert evidence: manual adoption evidence is required for promoted Codex packages",
    );
    assert_eq!(fixture.snapshot(), before);
    // The promoted name only matters in the Codex skill root.
    blocked(
        &install(&fixture, &["--provider", "codex"]),
        "manual expert evidence: manual adoption evidence is required for promoted Codex packages",
    );
    installed(&fixture, &["--provider", "claude"]);
}

/// Legacy `test_installs_both_providers_and_repeats`.
#[test]
fn installs_both_providers_and_repeats() {
    let fixture = fixture();
    let first = installed(&fixture, &[]);
    assert_eq!(first["providers"], json!(["codex", "claude"]));
    assert_eq!(first["files"], 2);
    assert_eq!(
        first["scope"],
        "project-local; no global configuration changed"
    );
    assert_eq!(first["authority"], "compiled Rust CLI; no Python consulted");
    assert_eq!(first["behavior"], "NOT_EVALUATED");
    assert_eq!(first["removed_authoring_files"], json!([]));
    assert!(first.get("runtime_requirements").is_none(), "{first}");
    let before = fixture.snapshot();
    installed(&fixture, &[]);
    assert_eq!(fixture.snapshot(), before);
    assert_eq!(
        fs::read(fixture.project.join(".agents/skills/demo/SKILL.md")).unwrap(),
        b"codex skill"
    );
    assert_eq!(
        fs::read(fixture.project.join(".claude/skills/demo/SKILL.md")).unwrap(),
        b"claude skill"
    );
    // The inventory is pretty JSON with a trailing newline, schema 1.
    let raw = fs::read(fixture.project.join(".devforge-install.json")).unwrap();
    assert!(
        raw.ends_with(b"\n") && raw.starts_with(b"{\n  \""),
        "{raw:?}"
    );
    assert_eq!(fixture.inventory()["schema"], 1);
}

/// Legacy `test_authoring_material_excluded_and_runtime_resources_preserved`.
#[test]
fn authoring_material_excluded_and_runtime_resources_preserved() {
    let fixture = fixture();
    let skill = fixture.skill().parent().unwrap().to_path_buf();
    for relative in [
        "evals/evals.json",
        "evals/files/input.md",
        "__pycache__/helper.pyc",
        "history/old.json",
        "provenance.json",
        "assets/template.md",
        "references/rules.md",
        "scripts/check.py",
    ] {
        write(&skill.join(relative), relative.as_bytes());
    }
    installed(&fixture, &[]);
    let destination = fixture.project.join(".agents/skills/demo");
    for excluded in ["evals", "__pycache__", "history", "provenance.json"] {
        assert!(!destination.join(excluded).exists(), "{excluded}");
    }
    for relative in [
        "assets/template.md",
        "references/rules.md",
        "scripts/check.py",
    ] {
        assert_eq!(
            fs::read(destination.join(relative)).unwrap(),
            relative.as_bytes()
        );
    }
}

/// Legacy `test_missing_provider_source_cannot_fall_back_to_shared_tree`.
#[test]
fn missing_provider_source_cannot_fall_back_to_shared_tree() {
    let fixture = fixture();
    fs::remove_dir_all(fixture.plugin("codex").join("skills")).unwrap();
    write(
        &fixture
            .framework
            .join("plugins/devforgeai/skills/demo/SKILL.md"),
        b"wrong provider",
    );
    blocked_with(&install(&fixture, &[]), "provider skill source missing");
    assert!(!fixture.project.join(".claude").exists());
}

/// Legacy `test_managed_old_eval_file_is_removed_but_edited_one_blocks`.
#[test]
fn managed_old_eval_file_is_removed_but_edited_one_blocks() {
    let fixture = fixture();
    installed(&fixture, &[]);
    let relative = ".agents/skills/demo/evals/evals.json";
    let destination = fixture.project.join(relative);
    write(&destination, b"old cases");
    let record = fixture.project.join(".devforge-install.json");
    let mut data = fixture.inventory();
    data["files"][relative] = json!(sha(b"old cases"));
    fs::write(&record, data.to_string()).unwrap();
    fs::write(&destination, b"user cases").unwrap();
    blocked(
        &install(&fixture, &[]),
        &format!("local edit/collision; refusing removal: {relative}"),
    );
    assert_eq!(fs::read(&destination).unwrap(), b"user cases");
    fs::write(&destination, b"old cases").unwrap();
    installed(&fixture, &[]);
    assert!(!destination.exists());
    assert!(fixture.inventory()["files"].get(relative).is_none());
}

/// Legacy `test_local_edit_collision_is_preserved`.
#[test]
fn local_edit_collision_is_preserved() {
    let fixture = fixture();
    installed(&fixture, &[]);
    let destination = fixture.project.join(".agents/skills/demo/SKILL.md");
    fs::write(&destination, b"user modification").unwrap();
    fs::write(fixture.skill(), b"upstream update").unwrap();
    blocked(
        &install(&fixture, &[]),
        "local edit/collision; refusing replacement: .agents/skills/demo/SKILL.md",
    );
    assert_eq!(fs::read(&destination).unwrap(), b"user modification");
}

/// Legacy `test_managed_refresh_updates_unmodified_copy`.
#[test]
fn managed_refresh_updates_unmodified_copy() {
    let fixture = fixture();
    installed(&fixture, &[]);
    fs::write(fixture.skill(), b"upstream update").unwrap();
    installed(&fixture, &[]);
    assert_eq!(
        fs::read(fixture.project.join(".agents/skills/demo/SKILL.md")).unwrap(),
        b"upstream update"
    );
}

/// Legacy `test_symlink_destination_is_rejected`.
#[test]
fn symlink_destination_is_rejected() {
    let fixture = fixture();
    std::os::unix::fs::symlink(
        fixture.root().join("elsewhere"),
        fixture.project.join(".agents"),
    )
    .unwrap();
    blocked_with(&install(&fixture, &[]), "symlink destination: ");
    assert!(!fixture.root().join("elsewhere").exists());
}

/// New: the packet's path hygiene. The legacy installer never compared the two.
#[test]
fn project_and_framework_must_be_separate() {
    let fixture = fixture();
    let inside = fixture.framework.join("nested-project");
    fs::create_dir(&inside).unwrap();
    let mut args: Vec<String> = vec![
        "--project".into(),
        inside.to_string_lossy().into_owned(),
        "install".into(),
        "framework".into(),
        "--framework".into(),
        fixture.framework.to_string_lossy().into_owned(),
    ];
    let refs: Vec<&str> = args.iter().map(String::as_str).collect();
    blocked(
        &spawn(Path::new(BIN), &refs, &[]),
        "project and framework must be separate directories",
    );
    // The reverse nesting is refused by the same predicate.
    args[1] = fixture.project.to_string_lossy().into_owned();
    args[5] = fixture
        .project
        .join("inner-framework")
        .to_string_lossy()
        .into_owned();
    let refs: Vec<&str> = args.iter().map(String::as_str).collect();
    blocked(
        &spawn(Path::new(BIN), &refs, &[]),
        "project and framework must be separate directories",
    );
    assert!(fixture.entries().is_empty());
}

/// New: project experts are explicit additions, and collide by name like any skill.
#[test]
fn project_experts_are_added_only_when_selected_and_collide_by_name() {
    let fixture = fixture();
    write(
        &fixture.project.join("experts/notes-persistence/SKILL.md"),
        b"expert",
    );
    write(
        &fixture
            .project
            .join("experts/notes-persistence/references/schema.md"),
        b"schema",
    );
    // A directory without an entrypoint is not a package and is skipped.
    fs::create_dir_all(fixture.project.join("experts/not-a-skill")).unwrap();
    installed(&fixture, &[]);
    assert!(
        !fixture
            .project
            .join(".agents/skills/notes-persistence")
            .exists()
    );
    let result = installed(&fixture, &["--include-experts"]);
    assert_eq!(result["files"], 6);
    for root in [".agents/skills", ".claude/skills"] {
        assert_eq!(
            fs::read(
                fixture
                    .project
                    .join(format!("{root}/notes-persistence/references/schema.md"))
            )
            .unwrap(),
            b"schema"
        );
    }
    write(&fixture.project.join("experts/demo/SKILL.md"), b"clash");
    blocked(
        &install(&fixture, &["--include-experts"]),
        "skill name collision: .agents/skills/demo/SKILL.md",
    );
}

/// The evidence-gated `manual-experts` record is another action's, and survives.
/// This action refuses the flags that would produce one by not offering them.
#[test]
fn a_recorded_manual_expert_adoption_is_preserved_and_its_flags_are_not_offered() {
    let fixture = fixture();
    let adoption = json!({"schema_version": "devforge.manual-expert-adoption/v1",
                          "owner": "integration owner", "packages": ["devforge-evaluate-expert"]});
    write(
        &fixture.project.join(".devforge-install.json"),
        json!({"schema": 1, "files": {}, "manual_expert_adoption": adoption})
            .to_string()
            .as_bytes(),
    );
    installed(&fixture, &[]);
    assert_eq!(fixture.inventory()["manual_expert_adoption"], adoption);
    // Refused by not offering them: clap rejects both before anything is planned.
    for flag in ["--manual-evidence", "--manual-experts-only"] {
        let result = install(&fixture, &[flag, "ignored"]);
        assert_eq!(result.code, 2, "flag={flag} stdout={}", result.stdout);
        assert!(result.stdout.is_empty(), "flag={flag}");
        assert!(
            result.stderr.contains("unexpected argument"),
            "flag={flag} stderr={}",
            result.stderr
        );
    }
    assert_eq!(fixture.inventory()["manual_expert_adoption"], adoption);
}

// ---- hook registry --------------------------------------------------------

/// Legacy `test_hook_sources_install_for_both_providers_and_repeat_without_duplicates`.
#[test]
fn hook_sources_install_for_both_providers_and_repeat_without_duplicates() {
    let fixture = fixture();
    let codex = codex_group();
    let claude = hook_group("devforge delivery hook --provider claude");
    fixture.hook_source("codex", &codex, false);
    fixture.hook_source("claude", &claude, true);
    installed(&fixture, &[]);
    let before = fixture.snapshot();
    installed(&fixture, &[]);
    assert_eq!(fixture.snapshot(), before);
    for (provider, group) in [("codex", &codex), ("claude", &claude)] {
        assert_eq!(
            read_json(&fixture.settings_path(provider)),
            json!({"hooks": {"Stop": [group]}})
        );
        let inventory = fixture.inventory();
        let record = &inventory["managed_hooks"][provider];
        assert_eq!(record["owned"][0]["definition"], *group);
        assert_eq!(record["owned"][0]["event"], "Stop");
        assert_eq!(record["owned"][0]["sha256"], group_digest(group));
        assert_eq!(record["reused"], json!([]));
        let relative = record["path"].as_str().unwrap();
        assert!(inventory["files"].get(relative).is_none(), "{inventory}");
    }
}

/// Legacy `test_hook_merge_preserves_settings_and_noncommand_user_groups`.
#[test]
fn hook_merge_preserves_settings_and_noncommand_user_groups() {
    let fixture = fixture();
    let user = json!({"hooks": [{"type": "prompt", "prompt": "user check", "timeout": 12}]});
    let unrelated =
        json!({"hooks": [{"type": "mcp_tool", "server": "existing", "tool": "inspect"}]});
    let before = json!({
        "description": "user description",
        "permissions": {"allow": ["Read"]},
        "hooks": {"Stop": [user], "SessionStart": [unrelated]},
    });
    fixture.settings("codex", &before);
    let wanted = codex_group();
    fixture.hook_source("codex", &wanted, false);
    installed(&fixture, &[]);
    let mut expected = before.clone();
    expected["hooks"] = json!({"Stop": [user, wanted], "SessionStart": [unrelated]});
    assert_eq!(read_json(&fixture.settings_path("codex")), expected);
}

/// Legacy `test_owned_group_updates_and_retires_without_losing_unrelated_group`.
#[test]
fn owned_group_updates_and_retires_without_losing_unrelated_group() {
    let fixture = fixture();
    let user = hook_group("user-check");
    fixture.settings("codex", &json!({"hooks": {"Stop": [user]}}));
    let source = fixture.hook_source("codex", &codex_group(), false);
    installed(&fixture, &[]);
    let changed = hook_group("new-delivery-command");
    fixture.hook_source("codex", &changed, false);
    installed(&fixture, &[]);
    assert_eq!(
        read_json(&fixture.settings_path("codex"))["hooks"]["Stop"],
        json!([user, changed])
    );
    fs::remove_file(&source).unwrap();
    fs::remove_dir(source.parent().unwrap()).unwrap();
    installed(&fixture, &[]);
    assert_eq!(
        read_json(&fixture.settings_path("codex"))["hooks"]["Stop"],
        json!([user])
    );
    assert_eq!(
        fixture.inventory()["managed_hooks"]["codex"]["owned"],
        json!([])
    );
}

/// Legacy `test_identical_unowned_group_is_reused_and_never_removed_on_source_update`.
#[test]
fn identical_unowned_group_is_reused_and_never_removed_on_source_update() {
    let fixture = fixture();
    let user = codex_group();
    fixture.settings(
        "codex",
        &json!({"description": "mine", "hooks": {"Stop": [user]}}),
    );
    fixture.hook_source("codex", &user, false);
    installed(&fixture, &[]);
    let inventory = fixture.inventory();
    let record = &inventory["managed_hooks"]["codex"];
    assert_eq!(record["owned"], json!([]));
    assert_eq!(record["reused"][0]["definition"], user);
    let changed = hook_group("changed-upstream");
    fixture.hook_source("codex", &changed, false);
    installed(&fixture, &[]);
    assert_eq!(
        read_json(&fixture.settings_path("codex"))["hooks"]["Stop"],
        json!([user, changed])
    );
    fixture.hook_source("codex", &user, false);
    installed(&fixture, &[]);
    assert_eq!(
        read_json(&fixture.settings_path("codex"))["hooks"]["Stop"],
        json!([user])
    );
    assert_eq!(
        fixture.inventory()["managed_hooks"]["codex"]["owned"],
        json!([])
    );
}

/// Legacy `test_removed_edited_or_duplicated_owned_group_blocks_all_writes`.
#[test]
fn removed_edited_or_duplicated_owned_group_blocks_all_writes() {
    let fixture = fixture();
    fixture.hook_source("codex", &codex_group(), false);
    installed(&fixture, &[]);
    let original = fs::read(fixture.settings_path("codex")).unwrap();
    let group = codex_group();
    for groups in [
        json!([]),
        json!([hook_group("local edit")]),
        json!([group, group]),
    ] {
        fixture.settings("codex", &json!({"hooks": {"Stop": groups}}));
        let before = fixture.snapshot();
        fs::write(fixture.skill(), b"upstream skill update").unwrap();
        blocked(
            &install(&fixture, &[]),
            "local edit/collision in owned codex hook: Stop",
        );
        assert_eq!(fixture.snapshot(), before, "groups={groups}");
        fs::write(fixture.settings_path("codex"), &original).unwrap();
    }
}

/// Legacy `test_owned_definition_digest_distinguishes_json_boolean_from_integer`.
#[test]
fn owned_definition_digest_distinguishes_json_boolean_from_integer() {
    let fixture = fixture();
    let mut group = codex_group();
    group["extra"] = json!(1);
    fixture.hook_source("codex", &group, false);
    installed(&fixture, &[]);
    let mut changed = group.clone();
    changed["extra"] = json!(true);
    assert_ne!(group_digest(&group), group_digest(&changed));
    fixture.settings("codex", &json!({"hooks": {"Stop": [changed]}}));
    blocked(
        &install(&fixture, &[]),
        "local edit/collision in owned codex hook: Stop",
    );
}

/// Legacy `test_missing_settings_rebuilds_current_groups_only`.
#[test]
fn missing_settings_rebuilds_current_groups_only() {
    let fixture = fixture();
    fixture.settings(
        "codex",
        &json!({"description": "lost user setting", "hooks": {"Stop": [hook_group("user")]}}),
    );
    let group = codex_group();
    fixture.hook_source("codex", &group, false);
    installed(&fixture, &[]);
    fs::remove_file(fixture.settings_path("codex")).unwrap();
    installed(&fixture, &[]);
    assert_eq!(
        read_json(&fixture.settings_path("codex")),
        json!({"hooks": {"Stop": [group]}})
    );
}

/// Legacy `test_selected_provider_preserves_unselected_hook_inventory_and_settings`.
#[test]
fn selected_provider_preserves_unselected_hook_inventory_and_settings() {
    let fixture = fixture();
    fixture.hook_source("codex", &codex_group(), false);
    fixture.hook_source("claude", &hook_group("claude"), false);
    installed(&fixture, &[]);
    let claude_settings = fs::read(fixture.settings_path("claude")).unwrap();
    let claude_record = fixture.inventory()["managed_hooks"]["claude"].clone();
    fixture.hook_source("codex", &hook_group("new codex"), false);
    installed(&fixture, &["--provider", "codex"]);
    assert_eq!(
        fs::read(fixture.settings_path("claude")).unwrap(),
        claude_settings
    );
    assert_eq!(
        fixture.inventory()["managed_hooks"]["claude"],
        claude_record
    );
}

/// Legacy `test_malformed_or_duplicate_settings_preserve_all_existing_bytes`.
#[test]
fn malformed_or_duplicate_settings_preserve_all_existing_bytes() {
    let fixture = fixture();
    fixture.hook_source("codex", &codex_group(), false);
    fixture.settings("codex", &json!({}));
    for raw in [
        "{",
        "[]",
        r#"{"hooks": [], "description": "user"}"#,
        r#"{"hooks":{},"hooks":{}}"#,
        r#"{"hooks":{"Stop":{}}}"#,
        r#"{"hooks":{"Stop":[{"hooks":[]}]}}"#,
        r#"{"bad":NaN}"#,
    ] {
        fixture.settings_text("codex", raw);
        let before = fixture.snapshot();
        let result = install(&fixture, &[]);
        assert_eq!(result.code, 2, "raw={raw} stdout={}", result.stdout);
        assert_eq!(fixture.snapshot(), before, "raw={raw}");
    }
    // A well-formed document installs, so the refusals above were specific.
    fixture.settings("codex", &json!({"hooks": {}}));
    installed(&fixture, &[]);
    assert_eq!(
        read_json(&fixture.settings_path("codex"))["hooks"]["Stop"],
        json!([codex_group()])
    );
}

/// Legacy `test_symlink_settings_preserves_target_and_does_not_install_skills`.
#[test]
fn symlink_settings_preserves_target_and_does_not_install_skills() {
    let fixture = fixture();
    fixture.hook_source("codex", &codex_group(), false);
    let outside = fixture.root().join("outside-settings.json");
    write(&outside, br#"{"hooks":{}}"#);
    fs::create_dir(fixture.project.join(".codex")).unwrap();
    std::os::unix::fs::symlink(&outside, fixture.settings_path("codex")).unwrap();
    blocked_with(&install(&fixture, &[]), "symlink destination: ");
    assert_eq!(fs::read(&outside).unwrap(), br#"{"hooks":{}}"#);
    assert!(!fixture.project.join(".agents").exists());
}

/// Legacy `test_skill_collision_does_not_update_hooks`.
#[test]
fn skill_collision_does_not_update_hooks() {
    let fixture = fixture();
    fixture.hook_source("codex", &codex_group(), false);
    installed(&fixture, &[]);
    fs::write(
        fixture.project.join(".agents/skills/demo/SKILL.md"),
        b"local edit",
    )
    .unwrap();
    fs::write(fixture.skill(), b"upstream edit").unwrap();
    fixture.hook_source("codex", &hook_group("updated hook"), false);
    let before = fixture.snapshot();
    blocked(
        &install(&fixture, &[]),
        "local edit/collision; refusing replacement: .agents/skills/demo/SKILL.md",
    );
    assert_eq!(fixture.snapshot(), before);
}

/// Legacy `test_parent_file_collision_preflights_before_other_writes`.
#[test]
fn parent_file_collision_preflights_before_other_writes() {
    let fixture = fixture();
    fixture.hook_source("claude", &hook_group("claude"), false);
    fs::write(fixture.project.join(".claude"), b"not a directory").unwrap();
    let before = fixture.snapshot();
    blocked_with(
        &install(&fixture, &[]),
        "non-directory destination parent: ",
    );
    assert_eq!(fixture.snapshot(), before);
}

/// Legacy `test_hook_default_and_exact_declarations_export_runtime_only`, install half.
#[test]
fn hook_default_and_exact_declarations_are_both_read() {
    for declaration in [None, Some("hooks/hooks.json"), Some("./hooks/hooks.json")] {
        let fixture = fixture();
        let group = codex_group();
        fixture.hook_source("codex", &group, false);
        let mut manifest = json!({"name": "devforgeai"});
        if let Some(value) = declaration {
            manifest["hooks"] = json!(value);
        }
        write(
            &fixture.plugin("codex").join(".codex-plugin/plugin.json"),
            manifest.to_string().as_bytes(),
        );
        installed(&fixture, &["--provider", "codex"]);
        assert_eq!(
            read_json(&fixture.settings_path("codex"))["hooks"]["Stop"],
            json!([group]),
            "declaration={declaration:?}"
        );
    }
}

/// Legacy `test_unsupported_hook_declarations_fail_install_and_export_before_writes`, install half.
#[test]
fn unsupported_hook_declarations_fail_install_before_writes() {
    let fixture = fixture();
    fixture.hook_source("codex", &codex_group(), false);
    let manifest = fixture.plugin("codex").join(".codex-plugin/plugin.json");
    for value in [
        json!(null),
        json!([]),
        json!({}),
        json!(["./hooks/hooks.json"]),
        json!("../hooks/hooks.json"),
        json!("./other.json"),
        json!(1),
    ] {
        write(
            &manifest,
            json!({"name": "devforgeai", "hooks": value})
                .to_string()
                .as_bytes(),
        );
        blocked(
            &install(&fixture, &[]),
            "framework hooks must select hooks/hooks.json",
        );
        assert!(fixture.entries().is_empty(), "value={value}");
    }
}

/// Legacy `test_missing_malformed_duplicate_or_symlink_hook_source_is_rejected`, install half.
#[test]
fn missing_malformed_duplicate_or_symlink_hook_source_is_rejected() {
    let fixture = fixture();
    let source = fixture.hook_source("codex", &codex_group(), true);
    let valid = fs::read(&source).unwrap();
    for (raw, expected) in [
        (
            "[]",
            "hook source needs hooks and optional description only",
        ),
        (
            r#"{"hooks":[]}"#,
            "hooks must be an event-to-group-list object",
        ),
        (r#"{"hooks":{},"hooks":{}}"#, "duplicate JSON key: hooks"),
        (
            r#"{"hooks":{"Stop":[{"hooks":[{"type":"command","command":""}]}]}}"#,
            "command hook needs a nonempty command string",
        ),
        (
            r#"{"hooks":{"Stop":[{"hooks":[{"type":"prompt","prompt":"x"}]}]}}"#,
            "framework hook handlers must be command handlers",
        ),
    ] {
        fs::write(&source, raw).unwrap();
        blocked_with(&install(&fixture, &[]), expected);
        assert!(fixture.entries().is_empty(), "raw={raw}");
    }
    fs::remove_file(&source).unwrap();
    blocked_with(
        &install(&fixture, &[]),
        "missing framework hook component: ",
    );
    let outside = fixture.root().join("source.json");
    fs::write(&outside, &valid).unwrap();
    std::os::unix::fs::symlink(&outside, &source).unwrap();
    blocked_with(&install(&fixture, &[]), "symlink hook source: ");
    assert!(fixture.entries().is_empty());
}

/// Legacy `test_empty_default_hook_directory_and_duplicate_manifest_are_rejected`.
#[test]
fn empty_default_hook_directory_and_duplicate_manifest_are_rejected() {
    let fixture = fixture();
    let source = fixture.hook_source("codex", &codex_group(), false);
    fs::remove_file(&source).unwrap();
    blocked_with(
        &install(&fixture, &[]),
        "missing framework hook component: ",
    );
    fixture.hook_source("codex", &codex_group(), false);
    write(
        &fixture.plugin("codex").join(".codex-plugin/plugin.json"),
        br#"{"name":"devforgeai","hooks":"hooks/hooks.json","hooks":"hooks/hooks.json"}"#,
    );
    blocked_with(&install(&fixture, &[]), "duplicate JSON key: hooks");
    assert!(fixture.entries().is_empty());
}

// ---- delivery-aware installation ------------------------------------------

/// Legacy `test_legacy_absence_never_probes_a_runtime`.
#[test]
fn legacy_absence_never_probes_a_runtime() {
    let fixture = fixture();
    fixture.hook_source("codex", &codex_group(), false);
    let marker = fixture.root().join("probe-executed.marker");
    let runtime = fixture.marking(&marker);
    installed(&fixture, &["--runtime", runtime.to_str().unwrap()]);
    assert!(
        !marker.exists(),
        "a package without a requirement never probes"
    );
    assert!(fixture.inventory().get("runtime_evidence").is_none());
    // A relative, nonexistent selection is equally ignored.
    installed(&fixture, &["--runtime", "ignored-relative-runtime"]);
    assert!(!marker.exists());
    assert!(fixture.inventory().get("runtime_evidence").is_none());
}

/// Legacy `test_delivery_install_requires_explicit_runtime_even_with_path_and_environment`.
#[test]
fn delivery_install_requires_explicit_runtime_even_with_path_and_environment() {
    let fixture = fixture();
    fixture.delivery_requirement("codex");
    let marker = fixture.root().join("probe-executed.marker");
    let runtime = fixture.marking(&marker);
    // A discoverable `devforge` becomes neither the runtime nor the authority.
    let discoverable = fixture.root().join("devforge");
    let lock = executables();
    fs::copy(&runtime, &discoverable).unwrap();
    fs::set_permissions(&discoverable, fs::Permissions::from_mode(0o755)).unwrap();
    drop(lock);
    let root = fixture.root().to_string_lossy().into_owned();
    let discoverable = discoverable.to_string_lossy().into_owned();
    let result = install_from(
        Path::new(BIN),
        &fixture,
        &["--provider", "codex"],
        &[
            ("PATH", root.as_str()),
            ("DEVFORGE_BIN", discoverable.as_str()),
            ("DEVFORGE_DELIVERY_EXECUTABLE", discoverable.as_str()),
        ],
    );
    blocked(
        &result,
        "delivery-aware project installation requires --runtime ABSOLUTE_PATH",
    );
    assert!(!marker.exists());
    assert!(fixture.entries().is_empty());
}

/// Legacy `test_delivery_sidecar_rejects_malformed_duplicate_unknown_and_unsupported_values`.
#[test]
fn delivery_sidecar_rejects_malformed_duplicate_unknown_and_unsupported_values() {
    let fixture = fixture();
    let (path, requirement) = fixture.delivery_requirement("codex");
    let marker = fixture.root().join("probe-executed.marker");
    let runtime = fixture.marking(&marker);
    let mut cases: Vec<String> = vec![
        "{".into(),
        "[]".into(),
        r#"{"runtime": "x", "runtime": "x"}"#.into(),
    ];
    for (key, value) in [
        ("extra", json!(true)),
        ("protocol", json!("devforge.delivery-runtime/v999")),
        ("provider", json!("claude")),
        ("required_events", json!("SessionStart")),
        ("completion_mode", json!(true)),
    ] {
        let mut changed = requirement.clone();
        changed[key] = value;
        cases.push(changed.to_string());
    }
    for raw in cases {
        fs::write(&path, &raw).unwrap();
        let result = install(&fixture, &["--runtime", runtime.to_str().unwrap()]);
        assert_eq!(result.code, 2, "raw={raw} stdout={}", result.stdout);
        assert!(
            !marker.exists(),
            "a malformed sidecar must precede the probe"
        );
        assert!(fixture.entries().is_empty(), "raw={raw}");
    }
    // The unchanged sidecar is admitted, so the refusals above were specific.
    fs::write(&path, requirement.to_string()).unwrap();
    installed(&fixture, &["--runtime", runtime.to_str().unwrap()]);
    assert!(marker.exists(), "the accepted sidecar probes the runtime");
}

/// Legacy `test_delivery_requires_complete_unique_synchronous_hook_selection`.
#[test]
fn delivery_requires_complete_unique_synchronous_hook_selection() {
    let fixture = fixture();
    let (path, _) = fixture.delivery_requirement("codex");
    let source = path.parent().unwrap().join("hooks.json");
    let valid = read_json(&source);
    let marker = fixture.root().join("probe-executed.marker");
    let runtime = fixture.marking(&marker);
    let mut cases: Vec<Value> = vec![json!({"hooks": {}}), json!({"hooks": {"Stop": []}})];
    for event in REQUIRED_EVENTS {
        let mut missing = valid.clone();
        missing["hooks"].as_object_mut().unwrap().remove(event);
        cases.push(missing);
    }
    for alteration in [
        "empty", "groups", "handlers", "provider", "matcher", "async", "timeout",
    ] {
        let mut changed = valid.clone();
        match alteration {
            "empty" => changed["hooks"]["Stop"][0]["hooks"] = json!([]),
            "groups" => {
                let group = changed["hooks"]["Stop"][0].clone();
                changed["hooks"]["Stop"].as_array_mut().unwrap().push(group);
            }
            "handlers" => {
                let handler = changed["hooks"]["Stop"][0]["hooks"][0].clone();
                changed["hooks"]["Stop"][0]["hooks"]
                    .as_array_mut()
                    .unwrap()
                    .push(handler);
            }
            "provider" => {
                let command = changed["hooks"]["Stop"][0]["hooks"][0]["command"]
                    .as_str()
                    .unwrap()
                    .replace("codex", "claude");
                changed["hooks"]["Stop"][0]["hooks"][0]["command"] = json!(command);
            }
            "matcher" => changed["hooks"]["Stop"][0]["matcher"] = json!("sometimes"),
            "async" => changed["hooks"]["Stop"][0]["hooks"][0]["async"] = json!(true),
            _ => changed["hooks"]["Stop"][0]["hooks"][0]["timeout"] = json!(true),
        }
        cases.push(changed);
    }
    let mut extra = valid.clone();
    let stop = extra["hooks"]["Stop"].clone();
    extra["hooks"]["PreToolUse"] = stop;
    cases.push(extra);
    for document in cases {
        fs::write(&source, document.to_string()).unwrap();
        let result = install(&fixture, &["--runtime", runtime.to_str().unwrap()]);
        assert_eq!(
            result.code, 2,
            "document={document} stdout={}",
            result.stdout
        );
        assert!(!marker.exists(), "invalid hooks must precede the probe");
        assert!(fixture.entries().is_empty(), "document={document}");
    }
    fs::remove_file(&source).unwrap();
    blocked_with(
        &install(&fixture, &["--runtime", runtime.to_str().unwrap()]),
        "missing framework hook component",
    );
    assert!(fixture.entries().is_empty());
}

/// Legacy `test_delivery_runtime_must_be_absolute_canonical_regular_and_executable`.
#[test]
fn delivery_runtime_must_be_absolute_canonical_regular_and_executable() {
    let fixture = fixture();
    fixture.delivery_requirement("codex");
    let runtime = fixture.serving(&base_capabilities().to_string());
    let link = fixture.root().join("runtime-link");
    std::os::unix::fs::symlink(&runtime, &link).unwrap();
    let parent_link = fixture.root().join("runtime-parent-link");
    std::os::unix::fs::symlink(fixture.root(), &parent_link).unwrap();
    let folder = fixture.root().join("folder");
    fs::create_dir(&folder).unwrap();
    let no_execute = fixture.root().join("not-executable");
    fs::write(&no_execute, fs::read(&runtime).unwrap()).unwrap();
    for path in [
        PathBuf::from("devforge"),
        link,
        parent_link.join("devforge-standin"),
        folder.clone(),
        no_execute,
        folder.join("../devforge-standin"),
        fixture.root().join("missing"),
    ] {
        let result = install(&fixture, &["--runtime", path.to_str().unwrap()]);
        assert_eq!(
            result.code,
            2,
            "path={} stdout={}",
            path.display(),
            result.stdout
        );
        assert!(fixture.entries().is_empty(), "path={}", path.display());
    }
    // The same bytes, selected absolutely and canonically, are admitted.
    installed(&fixture, &["--runtime", runtime.to_str().unwrap()]);
}

/// Legacy `test_delivery_incompatible_capabilities_block_all_installation_writes`.
#[test]
fn delivery_incompatible_capabilities_block_all_installation_writes() {
    let fixture = fixture();
    fixture.delivery_requirement("codex");
    let valid = base_capabilities();
    let mut cases: Vec<String> = vec![
        "{".into(),
        "[]".into(),
        r#"{"schema_version":"x","schema_version":"x"}"#.into(),
    ];
    for (key, value) in [
        ("schema_version", json!("devforge.delivery-capabilities/v2")),
        ("protocol", json!("other")),
        ("supported_providers", json!(["claude"])),
        ("completion_modes", json!(["unmanaged"])),
        ("hook_events", json!(["Stop"])),
        ("io_modes", json!("inherited")),
        ("native_admission", json!(true)),
        ("mechanical_scope", json!(null)),
        ("extra", json!("unknown")),
    ] {
        let mut changed = valid.clone();
        changed[key] = value;
        cases.push(changed.to_string());
    }
    let mut missing = valid.clone();
    missing.as_object_mut().unwrap().remove("native_admission");
    cases.push(missing.to_string());
    for raw in cases {
        let runtime = fixture.serving(&raw);
        let result = install(&fixture, &["--runtime", runtime.to_str().unwrap()]);
        assert_eq!(result.code, 2, "raw={raw} stdout={}", result.stdout);
        assert!(fixture.entries().is_empty(), "raw={raw}");
    }
    // The extended contract is admitted only in full and only well formed.
    let extended = extended_capabilities();
    let mut partial = extended.clone();
    partial
        .as_object_mut()
        .unwrap()
        .remove("native_semantic_review");
    let mut boolean = extended.clone();
    boolean["native_execution_enabled"] = json!("false");
    let mut schema = extended.clone();
    schema["utility_session_schema"] = json!("devforge.utility-session/v2");
    let mut workflows = extended.clone();
    workflows["utility_workflows"] = json!([]);
    for (value, expected) in [
        (
            partial,
            "unsupported runtime capabilities extension combination",
        ),
        (
            boolean,
            "malformed runtime capabilities extension: native_execution_enabled",
        ),
        (
            schema,
            "malformed runtime capabilities extension: utility_session_schema",
        ),
        (
            workflows,
            "malformed runtime capabilities extension: utility_workflows",
        ),
    ] {
        let runtime = fixture.serving(&value.to_string());
        blocked(
            &install(&fixture, &["--runtime", runtime.to_str().unwrap()]),
            expected,
        );
        assert!(fixture.entries().is_empty());
    }
}

/// Legacy `test_delivery_compatible_explicit_runtime_records_exact_evidence_and_preserves_user_hooks`
/// and `test_delivery_guard_receives_the_resolved_project_and_every_sorted_destination`.
#[test]
fn delivery_compatible_runtime_records_exact_evidence_and_preserves_user_hooks() {
    let fixture = fixture();
    let (_, codex) = fixture.delivery_requirement("codex");
    let (_, claude) = fixture.delivery_requirement("claude");
    let user = hook_group("user-only");
    fixture.settings(
        "codex",
        &json!({"permissions": {"allow": ["Read"]},
                "hooks": {"Stop": [user], "PreToolUse": [user]}}),
    );
    let runtime = fixture.serving(&base_capabilities().to_string());
    let digest = sha(&fs::read(&runtime).unwrap());
    let result = installed(&fixture, &["--runtime", runtime.to_str().unwrap()]);
    assert_eq!(result["runtime_compatibility"], "VERIFIED");
    assert_eq!(result["native_activation"], "NOT_VERIFIED");
    assert_eq!(result["runtime_requirements"]["codex"], codex);
    assert_eq!(result["runtime_requirements"]["claude"], claude);
    let identity = validator_identity(Path::new(BIN));
    let inventory = fixture.inventory();
    for (provider, requirement) in [("codex", &codex), ("claude", &claude)] {
        assert_eq!(
            inventory["runtime_evidence"][provider],
            json!({
                "schema_version": "devforge.runtime-probe/v1",
                "path": runtime,
                "sha256_before": digest,
                "sha256_after": digest,
                "capabilities": base_capabilities(),
                "contract": "base",
                "providers": ["codex", "claude"],
                "native_activation": "NOT_VERIFIED",
                "project": fixture.project,
                "validator": identity,
                "requirement": requirement,
            }),
            "provider={provider}"
        );
    }
    let settings = read_json(&fixture.settings_path("codex"));
    assert_eq!(settings["hooks"]["PreToolUse"], json!([user]));
    assert_eq!(settings["hooks"]["Stop"][0], user);
    assert_eq!(settings["hooks"]["Stop"].as_array().unwrap().len(), 2);
    assert_eq!(settings["permissions"], json!({"allow": ["Read"]}));
    // The settings destination is a managed hook document, never a tracked file.
    assert!(inventory["files"].get(".codex/hooks.json").is_none());
    let before = fixture.snapshot();
    installed(&fixture, &["--runtime", runtime.to_str().unwrap()]);
    assert_eq!(fixture.snapshot(), before);
}

fn validator_identity(binary: &Path) -> Value {
    let report = spawn(binary, &["install", "identity"], &[]);
    assert_eq!(report.code, 0, "{}", report.stderr);
    let value: Value = serde_json::from_str(&report.stdout).unwrap();
    json!({"executable": value["executable"], "source_sha256": value["source_sha256"]})
}

/// Legacy `test_delivery_binary_mutation_during_probe_blocks_all_installation_writes`.
#[test]
fn delivery_binary_mutation_during_probe_blocks_all_installation_writes() {
    let fixture = fixture();
    fixture.delivery_requirement("codex");
    let runtime = standin(
        fixture.root(),
        "devforge-mutating",
        &format!(
            "printf '%s' {}\nprintf '\\n# changed\\n' >> \"$0\"\n",
            quote(&base_capabilities().to_string())
        ),
    );
    blocked(
        &install(&fixture, &["--runtime", runtime.to_str().unwrap()]),
        "selected runtime binary changed during capability verification",
    );
    assert!(fixture.entries().is_empty());
}

/// Legacy `test_delivery_selected_runtime_cannot_be_overwritten_by_installation`.
#[test]
fn delivery_selected_runtime_cannot_be_overwritten_by_installation() {
    let fixture = fixture();
    fixture.delivery_requirement("codex");
    let standin = fixture.serving(&base_capabilities().to_string());
    let bytes = fs::read(&standin).unwrap();
    fs::write(fixture.skill(), &bytes).unwrap();
    let runtime = fixture.project.join(".agents/skills/demo/SKILL.md");
    write(&runtime, &bytes);
    fs::set_permissions(&runtime, fs::Permissions::from_mode(0o700)).unwrap();
    let before = fixture.snapshot();
    blocked(
        &install(&fixture, &["--runtime", runtime.to_str().unwrap()]),
        "selected runtime binary overlaps an installation destination",
    );
    assert_eq!(fixture.snapshot(), before);
}

/// Legacy `test_delivery_runtime_hardlink_to_managed_destination_blocks_before_probe`.
#[test]
fn delivery_runtime_hardlink_to_managed_destination_blocks_before_probe() {
    let fixture = fixture();
    let marker = fixture.root().join("probe-executed.marker");
    let runtime = fixture.marking(&marker);
    fs::write(fixture.skill(), fs::read(&runtime).unwrap()).unwrap();
    installed(&fixture, &[]);
    let destination = fixture.project.join(".agents/skills/demo/SKILL.md");
    fs::set_permissions(&destination, fs::Permissions::from_mode(0o700)).unwrap();
    fs::remove_file(&runtime).unwrap();
    fs::hard_link(&destination, &runtime).unwrap();
    fixture.delivery_requirement("codex");
    let mut updated = fs::read(fixture.skill()).unwrap();
    updated.extend_from_slice(b"# updated candidate\n");
    fs::write(fixture.skill(), &updated).unwrap();
    let before = fixture.snapshot();
    let runtime_before = fs::read(&runtime).unwrap();
    blocked(
        &install(&fixture, &["--runtime", runtime.to_str().unwrap()]),
        "--runtime must have exactly one hard link",
    );
    assert!(!marker.exists(), "nothing was executed before the refusal");
    assert_eq!(fixture.snapshot(), before);
    assert_eq!(fs::read(&runtime).unwrap(), runtime_before);
}

/// Legacy `test_delivery_capability_probe_limits_time_output_and_exit_status`.
#[test]
fn delivery_capability_probe_limits_time_output_and_exit_status() {
    let fixture = fixture();
    fixture.delivery_requirement("codex");
    let valid = quote(&base_capabilities().to_string());
    for (body, expected) in [
        ("exec sleep 10\n".to_string(), "timed out"),
        (
            "head -c 1048577 /dev/zero | tr '\\0' 'x'\n".to_string(),
            "exceeds 1 MiB",
        ),
        (
            format!("head -c 1048577 /dev/zero | tr '\\0' 'x' 1>&2\nprintf '%s' {valid}\n"),
            "exceeds 1 MiB",
        ),
        (format!("printf '%s' {valid}\nexit 7\n"), "status 7"),
    ] {
        let runtime = standin(fixture.root(), "devforge-bounded", &body);
        let text = reason(&install(
            &fixture,
            &["--runtime", runtime.to_str().unwrap()],
        ));
        assert!(text.contains(expected), "reason={text} body={body}");
        assert!(fixture.entries().is_empty(), "body={body}");
    }
}

/// Legacy `test_delivery_selected_validator_admits_the_real_extended_runtime_contract`.
#[test]
fn the_real_extended_runtime_contract_is_admitted_and_recorded() {
    let fixture = fixture();
    let (_, requirement) = fixture.delivery_requirement("claude");
    let runtime = copied(fixture.root(), "devforge-runtime");
    let reported = spawn(&runtime, &["delivery", "capabilities"], &[]);
    assert_eq!(reported.code, 0, "{}", reported.stderr);
    let capabilities: Value = serde_json::from_str(&reported.stdout).unwrap();
    assert_eq!(capabilities.as_object().unwrap().len(), 15);
    let result = installed(
        &fixture,
        &[
            "--provider",
            "claude",
            "--runtime",
            runtime.to_str().unwrap(),
        ],
    );
    assert_eq!(result["runtime_compatibility"], "VERIFIED");
    assert_eq!(result["native_activation"], "NOT_VERIFIED");
    assert_eq!(
        fs::read(fixture.project.join(".claude/skills/demo/SKILL.md")).unwrap(),
        b"claude skill"
    );
    let digest = sha(&fs::read(&runtime).unwrap());
    assert_eq!(
        fixture.inventory()["runtime_evidence"]["claude"],
        json!({
            "schema_version": "devforge.runtime-probe/v1",
            "path": runtime,
            "sha256_before": digest,
            "sha256_after": digest,
            "capabilities": capabilities,
            "contract": "extended",
            "providers": ["claude"],
            "native_activation": "NOT_VERIFIED",
            "project": fixture.project,
            "validator": validator_identity(Path::new(BIN)),
            "requirement": requirement,
        })
    );
}

/// Legacy `test_delivery_validator_refusal_blocks_every_installation_write`.
#[test]
fn a_validator_refusal_blocks_every_installation_write() {
    let fixture = fixture();
    fixture.delivery_requirement("claude");
    let mut malformed = extended_capabilities();
    malformed["native_process_interface"] = json!("  ");
    let runtime = fixture.serving(&malformed.to_string());
    blocked(
        &install(
            &fixture,
            &[
                "--provider",
                "claude",
                "--runtime",
                runtime.to_str().unwrap(),
            ],
        ),
        "malformed runtime capabilities extension: native_process_interface",
    );
    assert!(fixture.entries().is_empty());
}

/// Legacy `test_delivery_validator_inside_the_project_is_refused_by_the_compiled_authority`,
/// which in the compiled installer is the running executable itself.
#[test]
fn a_validating_executable_inside_the_project_is_refused_before_execution() {
    let fixture = fixture();
    fixture.delivery_requirement("codex");
    let marker = fixture.root().join("probe-executed.marker");
    let runtime = fixture.marking(&marker);
    let inside = copied(&fixture.project.join("tools"), "devforge");
    let before = fixture.snapshot();
    blocked(
        &install_from(
            &inside,
            &fixture,
            &["--runtime", runtime.to_str().unwrap()],
            &[],
        ),
        "validating executable must be outside the installation project",
    );
    assert!(
        !marker.exists(),
        "the refusal precedes executing the runtime"
    );
    assert_eq!(fixture.snapshot(), before);
}

/// Legacy `test_delivery_validator_aliased_by_a_destination_blocks_all_installation_writes`
/// and `test_delivery_guard_refusal_blocks_every_installation_write`: the guard's
/// refusal, decided by the running executable, blocks every write.
#[test]
fn a_destination_aliasing_the_validating_executable_blocks_all_installation_writes() {
    let fixture = fixture();
    fixture.delivery_requirement("codex");
    let runtime = fixture.serving(&base_capabilities().to_string());
    let validator = copied(fixture.root(), "validator-selected");
    // A managed destination is already a second name for the validator's inode,
    // and the inventory records those bytes, so no local-edit refusal precedes it.
    let relative = ".agents/skills/demo/SKILL.md";
    let destination = fixture.project.join(relative);
    fs::create_dir_all(destination.parent().unwrap()).unwrap();
    fs::hard_link(&validator, &destination).unwrap();
    let digest = sha(&fs::read(&validator).unwrap());
    write(
        &fixture.project.join(".devforge-install.json"),
        json!({"schema": 1, "files": {relative: digest}})
            .to_string()
            .as_bytes(),
    );
    let before = fixture.snapshot();
    blocked(
        &install_from(
            &validator,
            &fixture,
            &[
                "--provider",
                "codex",
                "--runtime",
                runtime.to_str().unwrap(),
            ],
            &[],
        ),
        "installation would overwrite the selected validator binary through an alias",
    );
    assert_eq!(fixture.snapshot(), before);
    assert_eq!(sha(&fs::read(&validator).unwrap()), digest);
    assert_eq!(
        fs::metadata(&destination).unwrap().ino(),
        fs::metadata(&validator).unwrap().ino()
    );
}

// ---- legacy oracle --------------------------------------------------------

/// Sort the managed-hook rows so the two installers' event iteration orders
/// (Python: hook-source document order; Rust: sorted keys) compare equal.
fn normalized(mut inventory: Value, project: &Path) -> Value {
    if let Some(hooks) = inventory
        .get_mut("managed_hooks")
        .and_then(Value::as_object_mut)
    {
        for entry in hooks.values_mut() {
            for category in ["owned", "reused"] {
                if let Some(rows) = entry[category].as_array_mut() {
                    rows.sort_by_key(|row| format!("{}\u{0}{}", row["event"], row["sha256"]));
                }
            }
        }
    }
    if let Some(evidence) = inventory
        .get_mut("runtime_evidence")
        .and_then(Value::as_object_mut)
    {
        for report in evidence.values_mut() {
            report["project"] = json!(project);
        }
    }
    inventory
}

/// The discriminating parity case: the unchanged legacy Python installer and
/// the compiled command install the same framework fixture into two projects,
/// and each refreshes the other's tree idempotently, with no local-edit refusal
/// and no rewrite of a semantically identical settings document.
#[test]
fn the_legacy_installer_and_the_compiled_command_agree_and_cross_refresh() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let legacy = manifest.join("scripts/install_framework.py");
    assert!(
        Path::new(PYTHON).is_file() && legacy.is_file(),
        "the legacy baseline must be present: {PYTHON} and {}",
        legacy.display()
    );
    let dir = temp();
    let root = dir.0.clone();
    let framework = root.join("framework");
    // One framework fixture, delivery-aware for both providers, with runtime
    // resources, excluded authoring material and provider agents.
    for provider in PROVIDERS {
        let plugin = framework.join(format!("providers/{provider}/plugins/devforgeai"));
        write(
            &plugin.join("skills/demo/SKILL.md"),
            format!("{provider} skill").as_bytes(),
        );
        write(&plugin.join("skills/demo/assets/template.md"), b"template");
        write(&plugin.join("skills/demo/evals/evals.json"), b"{}");
        write(
            &plugin.join(format!(".{provider}-plugin/plugin.json")),
            json!({"name": "devforgeai", "hooks": "./hooks/hooks.json"})
                .to_string()
                .as_bytes(),
        );
    }
    write(
        &framework.join("providers/claude/plugins/devforgeai/agents/reviewer.md"),
        b"claude agent",
    );
    write(
        &framework.join("providers/codex/agents/reviewer.toml"),
        b"codex agent",
    );
    let projects: Vec<PathBuf> = ["python", "rust"]
        .iter()
        .map(|name| {
            let project = root.join(name);
            fs::create_dir_all(&project).unwrap();
            project
        })
        .collect();
    let user = hook_group("user-check");
    for project in &projects {
        // A pre-existing user group exercises the merge and the digest rules.
        write(
            &project.join(".codex/hooks.json"),
            json!({"description": "mine", "hooks": {"Stop": [user]}})
                .to_string()
                .as_bytes(),
        );
    }
    // One single-link copy is both installers' runtime and validating authority,
    // so the recorded validator identity is identical on both sides.
    let authority = copied(&root, "devforge-authority");
    let requirement_of = |provider: &str| {
        let plugin = framework.join(format!("providers/{provider}/plugins/devforgeai"));
        let requirement = json!({
            "schema_version": "devforge.runtime-requirement/v1",
            "runtime": "devforge.delivery",
            "protocol": "devforge.delivery-runtime/v1",
            "provider": provider,
            "completion_mode": "managed-session",
            "required_events": REQUIRED_EVENTS,
        });
        write(
            &plugin.join("hooks/runtime-requirements.json"),
            requirement.to_string().as_bytes(),
        );
        let command = format!(
            "\"${{DEVFORGE_DELIVERY_EXECUTABLE:-devforge}}\" delivery hook --provider {provider}"
        );
        let mut hooks = serde_json::Map::new();
        for event in REQUIRED_EVENTS {
            hooks.insert(
                event.into(),
                json!([{"hooks": [{"type": "command", "command": command}]}]),
            );
        }
        write(
            &plugin.join("hooks/hooks.json"),
            json!({"hooks": hooks}).to_string().as_bytes(),
        );
    };
    requirement_of("codex");
    requirement_of("claude");

    let python_install = |project: &Path| -> Run {
        spawn(
            Path::new(PYTHON),
            &[
                legacy.to_str().unwrap(),
                "--framework",
                framework.to_str().unwrap(),
                "--project",
                project.to_str().unwrap(),
                "--provider",
                "both",
                "--runtime",
                authority.to_str().unwrap(),
                "--validator",
                authority.to_str().unwrap(),
            ],
            &[],
        )
    };
    let rust_install = |project: &Path| -> Run {
        spawn(
            &authority,
            &[
                "--project",
                project.to_str().unwrap(),
                "install",
                "framework",
                "--framework",
                framework.to_str().unwrap(),
                "--provider",
                "both",
                "--runtime",
                authority.to_str().unwrap(),
            ],
            &[],
        )
    };

    let first = python_install(&projects[0]);
    assert_eq!(
        first.code, 0,
        "legacy install: {} {}",
        first.stdout, first.stderr
    );
    let second = rust_install(&projects[1]);
    assert_eq!(
        second.code, 0,
        "rust install: {} {}",
        second.stdout, second.stderr
    );

    // Every installed byte outside the two rewritten JSON documents matches.
    let mut python_tree = tree(&projects[0]);
    let mut rust_tree = tree(&projects[1]);
    let rewritten = [
        ".devforge-install.json",
        ".codex/hooks.json",
        ".claude/settings.local.json",
    ];
    let mut python_docs = BTreeMap::new();
    let mut rust_docs = BTreeMap::new();
    for relative in rewritten {
        python_docs.insert(relative, python_tree.remove(relative).expect(relative));
        rust_docs.insert(relative, rust_tree.remove(relative).expect(relative));
    }
    assert_eq!(
        python_tree.keys().collect::<Vec<_>>(),
        rust_tree.keys().collect::<Vec<_>>()
    );
    assert_eq!(python_tree, rust_tree);
    for relative in [".codex/hooks.json", ".claude/settings.local.json"] {
        let left: Value = serde_json::from_slice(&python_docs[relative]).unwrap();
        let right: Value = serde_json::from_slice(&rust_docs[relative]).unwrap();
        assert_eq!(left, right, "{relative}");
    }
    let left: Value = serde_json::from_slice(&python_docs[".devforge-install.json"]).unwrap();
    let right: Value = serde_json::from_slice(&rust_docs[".devforge-install.json"]).unwrap();
    assert_eq!(
        normalized(left, Path::new("")),
        normalized(right, Path::new("")),
        "inventories must agree once row order and the bound project are normalized"
    );

    // Cross-refresh: each tool refreshes the other's tree without refusing, and
    // leaves the semantically identical settings documents byte-for-byte alone.
    let settings_before: Vec<Vec<u8>> = [".codex/hooks.json", ".claude/settings.local.json"]
        .iter()
        .map(|relative| fs::read(projects[0].join(relative)).unwrap())
        .collect();
    let refresh = rust_install(&projects[0]);
    assert_eq!(
        refresh.code, 0,
        "rust refresh of the legacy tree: {} {}",
        refresh.stdout, refresh.stderr
    );
    for (index, relative) in [".codex/hooks.json", ".claude/settings.local.json"]
        .iter()
        .enumerate()
    {
        assert_eq!(
            fs::read(projects[0].join(relative)).unwrap(),
            settings_before[index],
            "a semantically identical {relative} is never rewritten"
        );
    }
    let back = python_install(&projects[1]);
    assert_eq!(
        back.code, 0,
        "legacy refresh of the compiled tree: {} {}",
        back.stdout, back.stderr
    );
    let again = python_install(&projects[0]);
    assert_eq!(again.code, 0, "{} {}", again.stdout, again.stderr);
    let again = rust_install(&projects[1]);
    assert_eq!(again.code, 0, "{} {}", again.stdout, again.stderr);
}
