//! Black-box regression tests for `devforge validate mvp`.
//!
//! Every fixture is synthetic and Rust-owned. These cases prove the structural
//! document checks ported from `scripts/validate_mvp.py`; a structural PASS is
//! never MVP completion, native skill behavior, qualification or acceptance.
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::fs;
use std::os::unix::fs::symlink;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};

const BIN: &str = env!("CARGO_BIN_EXE_devforge");
const LEGACY: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/scripts/validate_mvp.py");
const PYTHON: &str = "/usr/bin/python3";
const SCOPE: &str = "Local links, JSON, index/source mappings, required sections, cached research digests, whitespace/fences";
const NOT_CHECKED: [&str; 4] = [
    "Full YAML/schema semantics",
    "semantic provenance",
    "Mermaid rendering",
    "native skill behavior",
];
const HEADINGS: [&str; 7] = [
    "User goal",
    "Inputs and provenance",
    "Workflow and phase exits",
    "Outputs and standardized templates",
    "Validation and behavioral acceptance",
    "Native creator authoring prompt",
    "Shared authoring requirements",
];
static COUNTER: AtomicU64 = AtomicU64::new(0);

fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn skill_name(index: usize) -> String {
    format!("devforge-s{index:02}")
}

fn spec_relative(index: usize) -> String {
    format!("specifications/skill-{index:02}-{}.md", skill_name(index))
}

fn template_relative(index: usize) -> String {
    format!("templates/{}/output.md", skill_name(index))
}

fn provider_source(provider: &str, name: &str) -> String {
    format!("providers/{provider}/plugins/devforgeai/skills/{name}")
}

fn spec_text(index: usize) -> String {
    let mut text = format!("# Skill {index:02}\n\n");
    for heading in HEADINGS {
        text.push_str(&format!("## {heading}\n\nSynthetic body.\n\n"));
    }
    text.push_str(&format!(
        "Template: [output](../{})\n\n```text\nexample\n```\n\nSee [contract](../artifact-contract.md#scope), \
         [site](https://example.invalid/page), [anchor](#user-goal), [placeholder]({{{{artifact}}}}) and [empty]().\n",
        template_relative(index)
    ));
    text
}

/// One synthetic framework root holding `docs/mvp` plus twelve provider skill sources.
struct Fixture {
    dir: PathBuf,
    framework: PathBuf,
    mvp: PathBuf,
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.dir);
    }
}

impl Fixture {
    fn new() -> Self {
        let dir = std::env::temp_dir().join(format!(
            "devforge-validate-mvp-{}-{}",
            std::process::id(),
            COUNTER.fetch_add(1, Ordering::SeqCst)
        ));
        let _ = fs::remove_dir_all(&dir);
        let framework = dir.join("framework");
        let mvp = framework.join("docs/mvp");
        let fixture = Self {
            dir,
            framework,
            mvp,
        };
        let mut skills = Vec::new();
        for index in 1..=12 {
            let name = skill_name(index);
            let consumer = skill_name(index % 12 + 1);
            fixture.write(&spec_relative(index), spec_text(index).as_bytes());
            fixture.write(
                &template_relative(index),
                b"# Output\n\n```text\nexample\n```\n",
            );
            for provider in ["claude", "codex"] {
                let skill = fixture
                    .framework
                    .join(provider_source(provider, &name))
                    .join("SKILL.md");
                write_file(
                    &skill,
                    format!("---\nname: {name}\ndescription: Synthetic.\n---\n").as_bytes(),
                );
            }
            skills.push(json!({
                "specification_id": format!("SKILL-{index:03}"),
                "name": name,
                "specification": spec_relative(index),
                "templates": [{
                    "artifact_type": "output",
                    "path": template_relative(index),
                    "consumers": [consumer],
                }],
                "implementations": {
                    "claude": {"source": provider_source("claude", &name), "status": "NOT_IMPLEMENTED"},
                    "codex": {"source": provider_source("codex", &name), "status": "NOT_IMPLEMENTED"},
                },
            }));
        }
        let index = json!({
            "schema_version": 1,
            "status": "SYNTHETIC",
            "shared_contracts": ["artifact-contract.md"],
            "shared_templates": ["templates/shared/handoff.md", "templates/shared/session-record.md"],
            "skills": skills,
            "authoring_templates": ["templates/skill-authoring/evals.json"],
        });
        fixture.write_index(&index);
        fixture.write(
            "README.md",
            b"# Synthetic MVP\n\nSee [index](package-index.json).\n",
        );
        fixture.write(
            "artifact-contract.md",
            b"# Artifact contract\n\n## Scope\n\nSynthetic.\n",
        );
        fixture.write("templates/shared/handoff.md", b"# Handoff\n");
        fixture.write("templates/shared/session-record.md", b"# Session record\n");
        fixture.write(
            "templates/skill-authoring/evals.json",
            b"{\"schema_version\": 1, \"cases\": []}\n",
        );
        fixture.write(".hidden.md", b"# Hidden but inventoried\n");
        let snapshot = b"{\"title\": \"overview\", \"retrieved\": \"2026-09-04\"}\n";
        fixture.write("research/agentskills/overview.json", snapshot);
        fixture.write(
            "research/sources.json",
            serde_json::to_vec(&json!({
                "schema_version": 1,
                "sources": [
                    {"id": "SRC-001", "title": "Unsnapshotted", "url": "https://example.invalid/a"},
                    {"id": "SRC-002", "title": "Snapshotted", "url": "https://example.invalid/b",
                     "snapshot": "agentskills/overview.json", "sha256": sha(snapshot)},
                ],
            }))
            .unwrap()
            .as_slice(),
        );
        // Both validation locations are excluded from inventory and JSON parsing.
        fixture.write("validation/20260101-old.json", b"not json\n");
        fixture.write("validation.json", b"{ not json either\n");
        fixture
    }

    fn path(&self, relative: &str) -> PathBuf {
        self.mvp.join(relative)
    }

    fn write(&self, relative: &str, bytes: &[u8]) {
        write_file(&self.path(relative), bytes);
    }

    fn remove(&self, relative: &str) {
        fs::remove_file(self.path(relative)).unwrap();
    }

    fn index(&self) -> Value {
        serde_json::from_slice(&fs::read(self.path("package-index.json")).unwrap()).unwrap()
    }

    fn write_index(&self, index: &Value) {
        self.write(
            "package-index.json",
            serde_json::to_string(index).unwrap().as_bytes(),
        );
    }

    fn edit_index(&self, edit: impl FnOnce(&mut Value)) {
        let mut index = self.index();
        edit(&mut index);
        self.write_index(&index);
    }

    fn report_path(&self, name: &str) -> PathBuf {
        self.dir.join("out").join(name)
    }

    fn run(&self, report: Option<&Path>) -> Output {
        run_rust(&self.mvp, report)
    }
}

fn write_file(path: &Path, bytes: &[u8]) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, bytes).unwrap();
}

fn run_rust(mvp: &Path, report: Option<&Path>) -> Output {
    let mut command = Command::new(BIN);
    command.args(["validate", "mvp", "--mvp"]).arg(mvp);
    if let Some(report) = report {
        command.arg("--report").arg(report);
    }
    command.output().unwrap()
}

fn run_legacy(mvp: &Path, report: Option<&Path>) -> Output {
    assert!(
        Path::new(PYTHON).is_file() && Path::new(LEGACY).is_file(),
        "compatibility baseline unavailable: {PYTHON} and {LEGACY} are required"
    );
    let mut command = Command::new(PYTHON);
    command.arg(LEGACY).arg("--mvp").arg(mvp);
    if let Some(report) = report {
        command.arg("--report").arg(report);
    }
    command.output().unwrap()
}

fn text(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
}

fn stdout_json(output: &Output) -> Value {
    serde_json::from_slice(&output.stdout).unwrap_or_else(|error| {
        panic!(
            "stdout was not JSON ({error}); stdout={:?} stderr={:?}",
            text(&output.stdout),
            text(&output.stderr)
        )
    })
}

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item.as_str().unwrap().to_owned())
        .collect()
}

fn errors(report: &Value) -> Vec<String> {
    strings(&report["errors"])
}

fn assert_fail(output: &Output, expected: &[&str]) -> Value {
    assert_eq!(
        output.status.code(),
        Some(2),
        "stderr={:?}",
        text(&output.stderr)
    );
    let report = stdout_json(output);
    assert_eq!(report["status"], "FAIL");
    assert_eq!(errors(&report), expected);
    report
}

fn assert_blocked(output: &Output, message: &str, report: &Path) {
    assert_eq!(output.status.code(), Some(2));
    assert_eq!(text(&output.stderr), format!("BLOCKED: {message}\n"));
    assert!(
        output.stdout.is_empty(),
        "stdout={:?}",
        text(&output.stdout)
    );
    assert!(!report.exists(), "BLOCKED must not write a report");
}

/// Python `datetime.isoformat()` in UTC: fraction present only when nonzero.
fn is_utc_isoformat(value: &str) -> bool {
    let Some(stamp) = value.strip_suffix("+00:00") else {
        return false;
    };
    let (main, fraction) = match stamp.split_once('.') {
        Some((main, fraction)) => (main, Some(fraction)),
        None => (stamp, None),
    };
    let digits = |s: &str, len: usize| s.len() == len && s.bytes().all(|b| b.is_ascii_digit());
    let shape = main.len() == 19
        && digits(&main[0..4], 4)
        && &main[4..5] == "-"
        && digits(&main[5..7], 2)
        && &main[7..8] == "-"
        && digits(&main[8..10], 2)
        && &main[10..11] == "T"
        && digits(&main[11..13], 2)
        && &main[13..14] == ":"
        && digits(&main[14..16], 2)
        && &main[16..17] == ":"
        && digits(&main[17..19], 2);
    shape && fraction.is_none_or(|fraction| digits(fraction, 6) && fraction != "000000")
}

fn without_timestamp(mut value: Value) -> Value {
    if let Some(object) = value.as_object_mut() {
        object.remove("created_at_utc");
    }
    value
}

#[test]
fn valid_tree_passes_with_legacy_report_shape_on_stdout() {
    let fixture = Fixture::new();
    let output = fixture.run(None);
    assert_eq!(
        output.status.code(),
        Some(0),
        "stderr={:?}",
        text(&output.stderr)
    );
    assert!(output.stderr.is_empty());
    let raw = text(&output.stdout);
    let report = stdout_json(&output);
    assert_eq!(report["schema_version"], 2);
    assert!(is_utc_isoformat(report["created_at_utc"].as_str().unwrap()));
    assert_eq!(report["status"], "PASS");
    assert_eq!(errors(&report), Vec::<String>::new());
    assert_eq!(report["specifications"], 12);
    assert_eq!(report["skill_output_templates"], 12);
    assert_eq!(report["shared_templates"], 2);
    assert_eq!(report["authoring_templates"], 1);
    assert_eq!(report["scope"], SCOPE);
    assert_eq!(strings(&report["not_checked"]), NOT_CHECKED);
    assert_eq!(report["native_skill_behavior"], "NOT_EVALUATED");
    assert!(
        report.get("files_sha256").is_none(),
        "stdout omits the inventory"
    );
    let keys: Vec<&str> = report
        .as_object()
        .unwrap()
        .keys()
        .map(String::as_str)
        .collect();
    assert_eq!(keys.len(), 11);
    // Legacy key order is preserved in the printed document.
    let expected_order = [
        "schema_version",
        "created_at_utc",
        "status",
        "errors",
        "specifications",
        "skill_output_templates",
        "shared_templates",
        "authoring_templates",
        "scope",
        "not_checked",
        "native_skill_behavior",
    ];
    let positions: Vec<usize> = expected_order
        .iter()
        .map(|key| raw.find(&format!("\"{key}\"")).unwrap())
        .collect();
    assert!(positions.windows(2).all(|pair| pair[0] < pair[1]), "{raw}");
    assert!(raw.ends_with("}\n"));
}

#[test]
fn report_file_adds_inventory_and_creates_parent_directories() {
    let fixture = Fixture::new();
    let report_path = fixture.report_path("nested/deeper/report.json");
    let output = fixture.run(Some(&report_path));
    assert_eq!(
        output.status.code(),
        Some(0),
        "stderr={:?}",
        text(&output.stderr)
    );
    let printed = stdout_json(&output);
    let raw = fs::read_to_string(&report_path).unwrap();
    assert!(raw.ends_with("}\n"));
    let mut report: Value = serde_json::from_str(&raw).unwrap();
    let files = report
        .as_object_mut()
        .unwrap()
        .remove("files_sha256")
        .unwrap();
    assert_eq!(report, printed, "report equals stdout plus files_sha256");
    let files = files.as_object().unwrap();
    let mut expected = Map::new();
    let mut expected_paths = vec![
        ".hidden.md".to_owned(),
        "README.md".to_owned(),
        "artifact-contract.md".to_owned(),
        "package-index.json".to_owned(),
        "research/agentskills/overview.json".to_owned(),
        "research/sources.json".to_owned(),
        "templates/shared/handoff.md".to_owned(),
        "templates/shared/session-record.md".to_owned(),
        "templates/skill-authoring/evals.json".to_owned(),
    ];
    for index in 1..=12 {
        expected_paths.push(spec_relative(index));
        expected_paths.push(template_relative(index));
    }
    for relative in expected_paths {
        let digest = sha(&fs::read(fixture.path(&relative)).unwrap());
        expected.insert(relative, Value::String(digest));
    }
    assert_eq!(files, &expected);
    assert!(!files.contains_key("validation.json"));
    assert!(!files.keys().any(|key| key.starts_with("validation/")));
}

#[test]
fn relative_inputs_and_bare_report_names_resolve_from_the_working_directory() {
    let fixture = Fixture::new();
    let output = Command::new(BIN)
        .current_dir(&fixture.framework)
        .args([
            "validate",
            "mvp",
            "--mvp",
            "docs/mvp",
            "--report",
            "bare-report.json",
        ])
        .output()
        .unwrap();
    assert_eq!(
        output.status.code(),
        Some(0),
        "stderr={:?}",
        text(&output.stderr)
    );
    let report: Value =
        serde_json::from_slice(&fs::read(fixture.framework.join("bare-report.json")).unwrap())
            .unwrap();
    assert_eq!(report["status"], "PASS");
    assert_eq!(
        report["files_sha256"]["README.md"],
        sha(b"# Synthetic MVP\n\nSee [index](package-index.json).\n")
    );
}

#[test]
fn missing_template_and_unknown_consumer_are_collected_in_order() {
    let fixture = Fixture::new();
    fixture.remove(&template_relative(3));
    fixture.edit_index(|index| {
        index["skills"][2]["templates"][0]["consumers"] = json!(["devforge-s04", "nobody"]);
    });
    let report_path = fixture.report_path("fail.json");
    let output = fixture.run(Some(&report_path));
    let report = assert_fail(
        &output,
        &[
            "missing template: templates/devforge-s03/output.md",
            "unknown consumer: nobody",
            "broken local link: specifications/skill-03-devforge-s03.md -> ../templates/devforge-s03/output.md",
        ],
    );
    assert_eq!(
        report["skill_output_templates"], 12,
        "templates are counted even when missing"
    );
    let written: Value = serde_json::from_slice(&fs::read(&report_path).unwrap()).unwrap();
    assert_eq!(written["status"], "FAIL");
    assert!(
        written["files_sha256"].is_object(),
        "FAIL reports still carry the inventory"
    );
}

#[test]
fn missing_required_section_names_the_specification_file() {
    let fixture = Fixture::new();
    let relative = spec_relative(5);
    let edited = spec_text(5).replace("## Native creator authoring prompt", "## Prompt");
    fixture.write(&relative, edited.as_bytes());
    let output = fixture.run(None);
    assert_fail(
        &output,
        &["skill-05-devforge-s05.md: missing Native creator authoring prompt"],
    );
}

#[test]
fn provider_source_mapping_checks_path_and_framework_skill_file() {
    let fixture = Fixture::new();
    fixture.edit_index(|index| {
        index["skills"][0]["implementations"]["codex"]["source"] =
            json!("providers/codex/skills/devforge-s01");
        index["skills"][1]["implementations"]["claude"]["source"] = json!("");
        index["skills"][2]["implementations"]["codex"]["source"] = Value::Null;
    });
    fs::remove_file(
        fixture
            .framework
            .join(provider_source("claude", "devforge-s04"))
            .join("SKILL.md"),
    )
    .unwrap();
    let output = fixture.run(None);
    assert_fail(
        &output,
        &[
            "incorrect provider source: providers/codex/skills/devforge-s01",
            "incorrect provider source: providers/claude/plugins/devforgeai/skills/devforge-s04",
        ],
    );
}

#[test]
fn provider_errors_follow_index_object_order() {
    let fixture = Fixture::new();
    let original = fs::read_to_string(fixture.path("package-index.json")).unwrap();
    let before = format!(
        "\"implementations\":{{\"claude\":{{\"source\":\"{}\",\"status\":\"NOT_IMPLEMENTED\"}},\"codex\":{{\"source\":\"{}\",\"status\":\"NOT_IMPLEMENTED\"}}}}",
        provider_source("claude", "devforge-s12"),
        provider_source("codex", "devforge-s12")
    );
    let after = "\"implementations\":{\"codex\":{\"source\":\"wrong-codex\"},\"claude\":{\"source\":\"wrong-claude\"}}";
    let edited = original.replace(&before, after);
    assert_ne!(
        edited, original,
        "fixture layout changed; update the replacement"
    );
    fixture.write("package-index.json", edited.as_bytes());
    let output = fixture.run(None);
    assert_fail(
        &output,
        &[
            "incorrect provider source: wrong-codex",
            "incorrect provider source: wrong-claude",
        ],
    );
}

#[test]
fn index_requires_twelve_uniquely_named_skills() {
    let fixture = Fixture::new();
    fixture.edit_index(|index| index["skills"][11]["name"] = json!("devforge-s11"));
    let output = fixture.run(None);
    assert_fail(
        &output,
        &[
            "expected 12 unique skill specifications",
            "unknown consumer: devforge-s12",
            "incorrect provider source: providers/claude/plugins/devforgeai/skills/devforge-s12",
            "incorrect provider source: providers/codex/plugins/devforgeai/skills/devforge-s12",
        ],
    );

    let fixture = Fixture::new();
    fixture.edit_index(|index| {
        index["skills"].as_array_mut().unwrap().pop();
        index["skills"][10]["templates"][0]["consumers"] = json!(["devforge-s01"]);
    });
    let output = fixture.run(None);
    let report = assert_fail(&output, &["expected 12 unique skill specifications"]);
    assert_eq!(report["specifications"], 11);
    assert_eq!(report["skill_output_templates"], 11);
}

#[test]
fn missing_indexed_documents_are_errors() {
    let fixture = Fixture::new();
    fixture.remove("templates/shared/handoff.md");
    fixture.remove("templates/skill-authoring/evals.json");
    let output = fixture.run(None);
    let report = assert_fail(
        &output,
        &[
            "missing indexed document: templates/shared/handoff.md",
            "missing indexed document: templates/skill-authoring/evals.json",
        ],
    );
    assert_eq!(report["shared_templates"], 2);
    assert_eq!(report["authoring_templates"], 1);
}

#[test]
fn invalid_inputs_block_without_report_even_when_errors_were_collected() {
    let fixture = Fixture::new();
    let report = fixture.report_path("never.json");
    fixture.remove(&template_relative(2));
    fixture.edit_index(|index| index["skills"][7]["name"] = json!("Bad_Name"));
    assert_blocked(
        &fixture.run(Some(&report)),
        "invalid indexed skill name",
        &report,
    );

    let fixture = Fixture::new();
    fixture.edit_index(|index| index["skills"][3]["specification"] = json!("/etc/hostname"));
    assert_blocked(
        &fixture.run(Some(&report)),
        "path outside selected document root: /etc/hostname",
        &report,
    );

    let fixture = Fixture::new();
    fixture.write("../escape.md", spec_text(4).as_bytes());
    fixture.edit_index(|index| index["skills"][3]["specification"] = json!("../escape.md"));
    assert_blocked(
        &fixture.run(Some(&report)),
        "path outside selected document root: ../escape.md",
        &report,
    );

    let fixture = Fixture::new();
    let relative = spec_relative(6);
    fixture.remove(&relative);
    symlink(fixture.path(&spec_relative(7)), fixture.path(&relative)).unwrap();
    assert_blocked(
        &fixture.run(Some(&report)),
        &format!("symlink document: {relative}"),
        &report,
    );
}

#[test]
fn unreadable_or_malformed_inputs_block_with_a_reason() {
    let report = std::env::temp_dir().join(format!(
        "devforge-validate-mvp-{}-blocked-report.json",
        std::process::id()
    ));
    let _ = fs::remove_file(&report);
    let blocked = |fixture: &Fixture| {
        let output = fixture.run(Some(&report));
        assert_eq!(output.status.code(), Some(2));
        let stderr = text(&output.stderr);
        assert!(stderr.starts_with("BLOCKED: "), "stderr={stderr:?}");
        assert!(stderr.ends_with('\n'));
        assert!(
            output.stdout.is_empty(),
            "stdout={:?}",
            text(&output.stdout)
        );
        assert!(!report.exists());
        stderr
    };

    let fixture = Fixture::new();
    fixture.write("package-index.json", b"{\"skills\": [");
    blocked(&fixture);

    let fixture = Fixture::new();
    fixture.remove("package-index.json");
    blocked(&fixture);

    let fixture = Fixture::new();
    fixture.edit_index(|index| index["skills"] = json!(5));
    blocked(&fixture);

    let fixture = Fixture::new();
    fixture.edit_index(|index| {
        index["skills"][0]
            .as_object_mut()
            .unwrap()
            .remove("templates");
    });
    blocked(&fixture);

    let fixture = Fixture::new();
    fixture.remove(&spec_relative(9));
    let stderr = blocked(&fixture);
    assert!(
        stderr.contains(&spec_relative(9)),
        "missing specification names the file: {stderr}"
    );

    let fixture = Fixture::new();
    fixture.write("templates/skill-authoring/evals.json", b"{");
    blocked(&fixture);

    let fixture = Fixture::new();
    fixture.write("notes.md", &[0xff, 0xfe, b'#', b'\n']);
    blocked(&fixture);

    let fixture = Fixture::new();
    fixture.remove("research/sources.json");
    blocked(&fixture);

    let fixture = Fixture::new();
    fixture.write(
        "research/sources.json",
        b"{\"sources\": [{\"id\": \"SRC-009\", \"snapshot\": \"missing.json\"}]}",
    );
    let output = fixture.run(None);
    assert_fail(&output, &["research response digest mismatch: SRC-009"]);

    let missing = std::env::temp_dir().join(format!(
        "devforge-validate-mvp-{}-does-not-exist",
        std::process::id()
    ));
    let output = run_rust(&missing, Some(&report));
    assert_eq!(output.status.code(), Some(2));
    assert!(text(&output.stderr).starts_with("BLOCKED: "));
    assert!(output.stdout.is_empty());
    assert!(!report.exists());
}

#[test]
fn symlinks_are_errors_before_inventory_exclusions() {
    let fixture = Fixture::new();
    symlink(fixture.path("README.md"), fixture.path("extra.md")).unwrap();
    symlink(fixture.path("templates"), fixture.path("linked")).unwrap();
    symlink(
        fixture.path("package-index.json"),
        fixture.path("validation/link.json"),
    )
    .unwrap();
    symlink(
        fixture.path("templates/shared/handoff.md"),
        fixture.path("validation.json.bak"),
    )
    .unwrap();
    let report_path = fixture.report_path("symlinks.json");
    let output = fixture.run(Some(&report_path));
    assert_fail(
        &output,
        &[
            "symlink document: extra.md",
            "symlink document: linked",
            "symlink document: validation/link.json",
            "symlink document: validation.json.bak",
        ],
    );
    let report: Value = serde_json::from_slice(&fs::read(&report_path).unwrap()).unwrap();
    let files = report["files_sha256"].as_object().unwrap();
    for key in [
        "extra.md",
        "linked",
        "validation.json.bak",
        "validation/link.json",
    ] {
        assert!(!files.contains_key(key), "{key} must not be inventoried");
    }
    assert!(!files.keys().any(|key| key.starts_with("linked/")));
    assert!(files.contains_key("templates/shared/handoff.md"));
}

#[test]
fn research_snapshot_identities_are_verified_inside_research() {
    let fixture = Fixture::new();
    fixture.write(
        "research/agentskills/overview.json",
        b"{\"title\": \"altered\"}\n",
    );
    assert_fail(
        &fixture.run(None),
        &["research response digest mismatch: SRC-002"],
    );

    let fixture = Fixture::new();
    fixture.remove("research/agentskills/overview.json");
    assert_fail(
        &fixture.run(None),
        &["research response digest mismatch: SRC-002"],
    );

    let fixture = Fixture::new();
    let mut sources: Value =
        serde_json::from_slice(&fs::read(fixture.path("research/sources.json")).unwrap()).unwrap();
    let digest = sources["sources"][1]["sha256"]
        .as_str()
        .unwrap()
        .to_ascii_uppercase();
    sources["sources"][1]["sha256"] = json!(digest);
    fixture.write(
        "research/sources.json",
        serde_json::to_vec(&sources).unwrap().as_slice(),
    );
    assert_fail(
        &fixture.run(None),
        &["research response digest mismatch: SRC-002"],
    );

    let fixture = Fixture::new();
    let report = fixture.report_path("snapshot.json");
    let mut sources: Value =
        serde_json::from_slice(&fs::read(fixture.path("research/sources.json")).unwrap()).unwrap();
    sources["sources"][1]["snapshot"] = json!("../package-index.json");
    fixture.write(
        "research/sources.json",
        serde_json::to_vec(&sources).unwrap().as_slice(),
    );
    assert_blocked(
        &fixture.run(Some(&report)),
        "path outside selected document root: ../package-index.json",
        &report,
    );

    let fixture = Fixture::new();
    fixture.remove("research/agentskills/overview.json");
    symlink(
        fixture.path("research/sources.json"),
        fixture.path("research/agentskills/overview.json"),
    )
    .unwrap();
    let output = fixture.run(Some(&report));
    assert_blocked(
        &output,
        "symlink document: agentskills/overview.json",
        &report,
    );
}

#[test]
fn markdown_checks_keep_legacy_scope_and_component_ordering() {
    let fixture = Fixture::new();
    fixture.write("a/x.md", b"trailing space \n");
    fixture.write("a-b/x.md", b"trailing tab\t\n");
    fixture.write("crlf.md", b"clean line\r\nspace before newline \r\n");
    fixture.write(
        "notes.md",
        b"# Notes\n\n```text\nunterminated\n\nSee [missing](missing.md#section), [dir](../mvp), \
          [fragment](README.md#top), [remote](ftp://example.invalid/x), [anchor](#notes) and [tpl]({{path}}).\n",
    );
    let output = fixture.run(None);
    assert_fail(
        &output,
        &[
            "trailing whitespace: a/x.md",
            "trailing whitespace: a-b/x.md",
            "trailing whitespace: crlf.md",
            "unbalanced code fence: notes.md",
            "broken local link: notes.md -> missing.md",
        ],
    );
}

#[test]
fn matches_the_legacy_validator_on_synthetic_trees() {
    type Mutation = fn(&Fixture);
    // (name, stderr compared verbatim, mutation). JSON decoder wording is the one
    // accepted diagnostic difference, so that case compares only the BLOCKED prefix.
    let cases: [(&str, bool, Mutation); 14] = [
        ("valid", true, |_| {}),
        ("missing-template", true, |fixture| {
            fixture.remove(&template_relative(3))
        }),
        ("missing-section", true, |fixture| {
            let edited = spec_text(5).replace("## Shared authoring requirements", "## Other");
            fixture.write(&spec_relative(5), edited.as_bytes());
        }),
        ("wrong-provider", true, |fixture| {
            fixture.edit_index(|index| {
                index["skills"][0]["implementations"]["codex"]["source"] =
                    json!("providers/codex/other");
            });
        }),
        ("duplicate-name", true, |fixture| {
            fixture.edit_index(|index| index["skills"][11]["name"] = json!("devforge-s11"));
        }),
        ("invalid-name", true, |fixture| {
            fixture.edit_index(|index| index["skills"][2]["name"] = json!("Bad Name"));
        }),
        ("malformed-index", false, |fixture| {
            fixture.write("package-index.json", b"[1,")
        }),
        ("missing-spec", true, |fixture| {
            fixture.remove(&spec_relative(8))
        }),
        ("escape-path", true, |fixture| {
            fixture
                .edit_index(|index| index["skills"][3]["specification"] = json!("../../escape.md"));
        }),
        ("symlink-spec", true, |fixture| {
            fixture.remove(&spec_relative(6));
            symlink(
                fixture.path(&spec_relative(7)),
                fixture.path(&spec_relative(6)),
            )
            .unwrap();
        }),
        ("symlink-in-validation", true, |fixture| {
            symlink(
                fixture.path("README.md"),
                fixture.path("validation/link.md"),
            )
            .unwrap();
        }),
        ("altered-snapshot", true, |fixture| {
            fixture.write(
                "research/agentskills/overview.json",
                b"{\"title\": \"altered\"}\n",
            );
        }),
        ("markdown", true, |fixture| {
            fixture.write("a/x.md", b"space \n");
            fixture.write("a-b/x.md", b"tab\t\n");
            fixture.write("crlf.md", b"ok\r\nbad \r\n");
            fixture.write("notes.md", b"```\nopen\n[m](missing.md#x) [f](README.md#y) [u](http://x/) [a](#a) [t]({{t}})\n");
        }),
        ("missing-sources", true, |fixture| {
            fixture.remove("research/sources.json")
        }),
    ];
    for (name, exact_stderr, mutate) in cases {
        let fixture = Fixture::new();
        mutate(&fixture);
        let legacy_report = fixture.report_path(&format!("{name}-legacy.json"));
        let rust_report = fixture.report_path(&format!("{name}-rust.json"));
        let legacy = run_legacy(&fixture.mvp, Some(&legacy_report));
        let rust = fixture.run(Some(&rust_report));
        assert_eq!(
            rust.status.code(),
            legacy.status.code(),
            "{name}: exit status"
        );
        if exact_stderr {
            assert_eq!(text(&rust.stderr), text(&legacy.stderr), "{name}: stderr");
        } else {
            assert_eq!(
                text(&rust.stderr).starts_with("BLOCKED: "),
                text(&legacy.stderr).starts_with("BLOCKED: "),
                "{name}: stderr={:?} legacy={:?}",
                text(&rust.stderr),
                text(&legacy.stderr)
            );
        }
        assert_eq!(
            rust_report.exists(),
            legacy_report.exists(),
            "{name}: report presence"
        );
        if legacy.stdout.is_empty() {
            assert!(
                rust.stdout.is_empty(),
                "{name}: stdout must be empty when blocked"
            );
            continue;
        }
        let legacy_stdout: Value = serde_json::from_slice(&legacy.stdout).unwrap();
        let rust_stdout = stdout_json(&rust);
        assert_eq!(
            without_timestamp(rust_stdout),
            without_timestamp(legacy_stdout),
            "{name}: stdout"
        );
        let legacy_file: Value =
            serde_json::from_slice(&fs::read(&legacy_report).unwrap()).unwrap();
        let rust_file: Value = serde_json::from_slice(&fs::read(&rust_report).unwrap()).unwrap();
        assert_eq!(
            without_timestamp(rust_file),
            without_timestamp(legacy_file),
            "{name}: report file"
        );
        let legacy_keys: Vec<String> =
            json_object_key_order(&fs::read_to_string(&legacy_report).unwrap());
        let rust_keys: Vec<String> =
            json_object_key_order(&fs::read_to_string(&rust_report).unwrap());
        assert_eq!(rust_keys, legacy_keys, "{name}: files_sha256 key order");
    }
}

/// Keys of the `files_sha256` object in document order, taken from the pretty-printed text.
fn json_object_key_order(raw: &str) -> Vec<String> {
    let start = raw.find("\"files_sha256\": {").unwrap() + "\"files_sha256\": {".len();
    raw[start..]
        .lines()
        .take_while(|line| line.trim() != "}")
        .filter_map(|line| {
            let line = line.trim();
            let end = line.find("\": ")?;
            Some(line[1..end].to_owned())
        })
        .collect()
}
