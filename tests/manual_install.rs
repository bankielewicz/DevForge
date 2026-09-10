//! Black-box acceptance tests for the compiled manual-only expert installation path.
//!
//! Every fixture is synthetic. These cases prove the mechanical predicates of the
//! Rust guard and installer; they are not native qualification or owner acceptance.
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};

const BIN: &str = env!("CARGO_BIN_EXE_devforge");
const EVALUATOR: &str = "devforge-evaluate-expert";
const CREATOR: &str = "devforge-project-expert-creator";
const PHASES: [&str; 5] = [
    "Intake",
    "Selection",
    "Design",
    "Authoring",
    "PreparedTransfer",
];
const CHECKS: [(&str, &str); 9] = [
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
static COUNTER: AtomicU64 = AtomicU64::new(0);

fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn write(path: &Path, bytes: &[u8]) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, bytes).unwrap();
}

fn pin(path: &Path) -> Value {
    json!({"path": path.to_str().unwrap(), "sha256": sha(&fs::read(path).unwrap())})
}

fn pin_path(pin: &Value) -> PathBuf {
    PathBuf::from(pin["path"].as_str().unwrap())
}

fn tasks() -> Vec<String> {
    (1..=12).map(|i| format!("T{i:02}")).collect()
}

struct Temp(PathBuf);
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn temp() -> Temp {
    // Fixtures live beside the test binary so hard-link alias cases share its filesystem.
    let path = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!(
        "devforge-manual-install-{}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&path).unwrap();
    Temp(path)
}

struct Run {
    code: i32,
    output: Value,
    text: String,
}

fn run(bin: &str, args: &[&str]) -> Run {
    let out = Command::new(bin).args(args).output().unwrap();
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).to_string();
    Run {
        code: out.status.code().unwrap_or(-1),
        output: serde_json::from_str(&stdout).unwrap_or(Value::Null),
        text: format!("{stdout}{stderr}"),
    }
}

fn identity() -> Value {
    let result = run(BIN, &["install", "identity"]);
    assert_eq!(
        result.code, 0,
        "identity command unavailable: {}",
        result.text
    );
    result.output
}

fn authority_record(executable: &Value, source: &Value) -> Value {
    json!({
        "schema_version": "devforge.manual-install-authority/v1",
        "owner": "fixture-operator",
        "executable": executable,
        "source_sha256": source,
    })
}

fn write_authority(path: &Path, record: &Value) {
    write(path, serde_json::to_string(record).unwrap().as_bytes());
}

fn snapshot(root: &Path) -> BTreeMap<String, Vec<u8>> {
    fn visit(root: &Path, dir: &Path, out: &mut BTreeMap<String, Vec<u8>>) {
        if !dir.is_dir() {
            return;
        }
        for entry in fs::read_dir(dir).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                visit(root, &path, out);
            } else {
                let rel = path
                    .strip_prefix(root)
                    .unwrap()
                    .to_str()
                    .unwrap()
                    .to_string();
                out.insert(rel, fs::read(&path).unwrap());
            }
        }
    }
    let mut out = BTreeMap::new();
    visit(root, root, &mut out);
    out
}

fn source_files(root: &Path) -> Map<String, Value> {
    let mut files = Map::new();
    for (rel, bytes) in snapshot(root) {
        files.insert(rel, Value::String(sha(&bytes)));
    }
    files
}

/// Shared synthetic workspace: framework source, destination project and authority.
struct Workspace {
    _temp: Temp,
    root: PathBuf,
    framework: PathBuf,
    project: PathBuf,
    authority: PathBuf,
}

impl Workspace {
    fn new() -> Self {
        let temp = temp();
        let root = temp.0.clone();
        let framework = root.join("framework");
        let project = root.join("project");
        fs::create_dir(&project).unwrap();
        write(
            &framework.join("providers/codex/plugins/devforgeai/.codex-plugin/plugin.json"),
            br#"{"name": "devforgeai"}"#,
        );
        fs::create_dir_all(framework.join("providers/codex/plugins/devforgeai/skills")).unwrap();
        let authority = root.join("authority/manual-install-authority.json");
        let id = identity();
        write_authority(
            &authority,
            &authority_record(&id["executable"], &id["source_sha256"]),
        );
        Self {
            _temp: temp,
            root,
            framework,
            project,
            authority,
        }
    }

    fn skills(&self) -> PathBuf {
        self.framework
            .join("providers/codex/plugins/devforgeai/skills")
    }

    fn put(&self, name: &str, value: &Value) -> Value {
        let path = self.root.join("evidence").join(name);
        let bytes = match value {
            Value::String(text) => text.clone().into_bytes(),
            other => serde_json::to_string(other).unwrap().into_bytes(),
        };
        write(&path, &bytes);
        pin(&path)
    }

    fn args(&self, evidence: &Path) -> Vec<String> {
        [
            "install",
            "manual-experts",
            "--project",
            self.project.to_str().unwrap(),
            "--framework",
            self.framework.to_str().unwrap(),
            "--evidence",
            evidence.to_str().unwrap(),
            "--authority",
            self.authority.to_str().unwrap(),
        ]
        .iter()
        .map(|s| s.to_string())
        .collect()
    }

    fn install_with(&self, bin: &str, evidence: &Path) -> Run {
        let args = self.args(evidence);
        run(bin, &args.iter().map(String::as_str).collect::<Vec<_>>())
    }

    fn install(&self, evidence: &Path) -> Run {
        self.install_with(BIN, evidence)
    }

    fn installed(&self, evidence: &Path) -> Value {
        let result = self.install(evidence);
        assert_eq!(result.code, 0, "expected installation: {}", result.text);
        assert_eq!(result.output["status"], "INSTALLED", "{}", result.text);
        result.output
    }

    fn refused_with(&self, bin: &str, evidence: &Path, needles: &[&str]) -> String {
        let before = snapshot(&self.project);
        let result = self.install_with(bin, evidence);
        assert_eq!(result.code, 2, "expected refusal exit 2: {}", result.text);
        assert_eq!(result.output["status"], "BLOCKED", "{}", result.text);
        let reason = result.output["reason"]
            .as_str()
            .unwrap_or_default()
            .to_string();
        assert!(
            needles.iter().any(|needle| reason.contains(needle)),
            "reason {reason:?} lacks {needles:?}"
        );
        assert_eq!(snapshot(&self.project), before, "refusal must not write");
        reason
    }

    fn refused(&self, evidence: &Path, needles: &[&str]) -> String {
        self.refused_with(BIN, evidence, needles)
    }

    fn inventory(&self) -> Value {
        serde_json::from_slice(&fs::read(self.project.join(".devforge-install.json")).unwrap())
            .unwrap()
    }

    /// Run the evidence preflight against `packet` with this workspace's framework and authority.
    fn preflight_with(&self, framework: &Path, packet: &Path) -> Run {
        run(
            BIN,
            &[
                "install",
                "check-local-evidence",
                "--project",
                self.project.to_str().unwrap(),
                "--framework",
                framework.to_str().unwrap(),
                "--packet",
                packet.to_str().unwrap(),
                "--authority",
                self.authority.to_str().unwrap(),
            ],
        )
    }

    fn preflight(&self, packet: &Path) -> Run {
        self.preflight_with(&self.framework, packet)
    }
}

/// Full/Routine adoption fixture mirroring the legacy synthetic evaluation records.
struct Adoption {
    ws: Workspace,
    name: String,
    skill: PathBuf,
    raw: Value,
    manifest: Value,
    specification: Value,
    cases: Value,
    creator: Value,
    plan: Value,
    review: Value,
    results: Value,
    decision: Value,
    evidence_path: PathBuf,
}

impl Adoption {
    fn new(mode: &str) -> Self {
        let ws = Workspace::new();
        let name = EVALUATOR.to_string();
        let skill = ws.skills().join(&name).join("SKILL.md");
        write(&skill, b"codex skill");
        let raw = ws.put(
            "raw.txt",
            &json!("Synthetic evidence only. No real model or acceptance."),
        );
        let manifest = ws.put(
            "manifest.json",
            &json!({"schema_version": "devforge.expert-runtime-manifest/v1", "name": name,
                    "files_sha256": {"SKILL.md": sha(&fs::read(&skill).unwrap())}}),
        );
        let specification = ws.put(
            "spec.md",
            &json!("Frozen fixture requirements, independent of implementation."),
        );
        let tiers: Vec<&str> = if mode == "Full" {
            vec!["D", "S", "C", "B", "A"]
        } else {
            vec!["D"]
        };
        let cases = ws.put(
            "cases.json",
            &json!({"cases": tiers.iter().enumerate().map(|(i, tier)| json!({
                "id": format!("CASE-{}", i + 1), "tier": tier,
                "required_observations": ["Preserve the declared fixture boundary"]})).collect::<Vec<_>>()}),
        );
        let mut phases = Map::new();
        for phase in PHASES {
            phases.insert(
                phase.to_string(),
                json!({"classification": "Enforced", "evidence": [raw]}),
            );
        }
        let creator = json!({"schema_version": "devforge.expert-creator-completion/v1",
            "author": "fixture-author", "candidate": manifest, "specification": specification,
            "phases": phases});
        let routine = mode == "Routine";
        let mut plan = json!({"schema_version": "devforge.skill-validation-plan/v2", "run_id": "synthetic-install",
            "assignment": {"owner": "fixture-evaluator"},
            "input_refs": [
                {"kind": "specification", "path": specification["path"], "sha256": specification["sha256"]},
                {"kind": "cases", "path": cases["path"], "sha256": cases["sha256"]}],
            "validation_policy": {
                "version": "VPR-2", "mode": mode, "selection_reviewer": "fixture-reviewer",
                "candidate_identity": {"candidate": manifest},
                "impact": {"bounded": true,
                           "full_triggers": if routine { json!([]) } else { json!(["TRANSFER_CHANGE"]) },
                           "matched_rules": if routine { json!(["CI-01"]) } else { json!(["CI-06"]) }},
                "requested_claim": {"requires_full": mode == "Full"}, "lineage": {"fixture": "unqualified"},
                "assertions": [],
                "task_selection": tasks().iter().map(|task| json!({"task_id": task, "classification": "Enforced", "selection": "REQUIRED"})).collect::<Vec<_>>()}});
        let mut review = json!({"schema_version": "devforge.skill-ai-review/v2", "run_id": "synthetic-install",
            "candidate_ref": manifest,
            "reviewer": {"identity": "fixture-reviewer", "independence_evidence": "Synthetic distinct producer"},
            "overall": "PASS", "selection_review": {"outcome": "PASS", "reviewed_assertion_ids": []},
            "criteria": (1..=10).map(|i| json!({"id": format!("R{i:02}"), "outcome": "PASS",
                "reason": "Synthetic fixture judgment", "evidence": [raw]})).collect::<Vec<_>>()});
        let disposition = format!("{}_PASS", mode.to_uppercase());
        let mut results = json!({"schema_version": "devforge.skill-validation-results/v2", "run_id": "synthetic-install",
            "validation_disposition": disposition, "lineage": {"fixture": "unqualified"},
            "task_results": tasks().iter().map(|task| json!({"task_id": task, "classification": "Enforced",
                "selection": "REQUIRED", "disposition": "SATISFIED", "outcome": "PASS", "evidence": [raw]})).collect::<Vec<_>>(),
            "assertion_results": [],
            "receiving_transfer": {"selection": "REQUIRED", "outcome": "PASS", "observed_at_utc": "2026-09-08T12:00:00Z",
                "target_output": raw, "receiver_contract": raw, "receiver_observation": raw, "completed_action": raw}});
        let mut decision = json!({"schema_version": "devforge.skill-validation-decision/v2", "run_id": "synthetic-install",
            "overall": "PASS", "coverage_complete": true, "external_acceptance": "NOT_GRANTED",
            "validation_disposition": disposition, "routine_adoption_eligible": routine,
            "lineage": {"fixture": "unqualified"}, "checks": []});
        if routine {
            let baseline = json!({"candidate": manifest, "environment": raw});
            let mut lineage = json!({"qualified_anchor": {"status": "ABSENT", "identity": null, "evidence": null},
                "accepted_unqualified_baseline": baseline, "current_routinely_accepted": baseline,
                "previous_acceptance": null, "acceptance_chain": []});
            let previous = ws.put(
                "previous.json",
                &json!({"candidate_identity": baseline, "accepted_scope_ref": raw, "lineage": lineage.clone()}),
            );
            lineage["previous_acceptance"] = previous.clone();
            lineage["acceptance_chain"] = json!([previous]);
            let policy = &mut plan["validation_policy"];
            policy["baseline_identity"] = baseline;
            policy["accepted_scope_ref"] = raw.clone();
            policy["lineage"] = lineage.clone();
            policy["compatibility"] = (1..=4)
                .map(|i| {
                    json!({"id": format!("CP-{i:02}"), "disposition": "UNCHANGED",
                        "reason": "Synthetic unchanged environment", "evidence": [raw]})
                })
                .collect();
            policy["impact"]["immediate_diff"] = raw.clone();
            policy["impact"]["cumulative_diff"] = raw.clone();
            results["lineage"] = lineage.clone();
            decision["lineage"] = lineage;
        }
        let policy = &mut plan["validation_policy"];
        policy["catalog_refs"] = json!([cases]);
        policy["catalog_assertions"] = tiers
            .iter()
            .enumerate()
            .map(|(i, tier)| {
                json!({"assertion_id": format!("CASE-{}", i + 1), "case_id": format!("CASE-{}", i + 1),
                    "source_ref": cases, "source_pointer": format!("/cases/{i}/required_observations/0"),
                    "evidence_kinds": [if ["C", "B", "A"].contains(tier) { "N" } else { tier }]})
            })
            .collect();
        policy["assertions"] = tiers
            .iter()
            .enumerate()
            .map(|(i, tier)| {
                json!({"assertion_id": format!("CASE-{}", i + 1), "task_id": "T03", "tier": tier,
                    "selection": "REQUIRED", "expectation": "pass"})
            })
            .collect();
        review["selection_review"]["reviewed_assertion_ids"] = (1..=tiers.len())
            .map(|i| json!(format!("CASE-{i}")))
            .collect();
        results["assertion_results"] = tiers
            .iter()
            .enumerate()
            .map(|(i, tier)| {
                json!({"assertion_id": format!("CASE-{}", i + 1), "selection": "REQUIRED", "integrity": "INTACT",
                    "outcome": "PASS", "observation_refs": [raw],
                    "grade_refs": if *tier == "D" { json!([]) } else { json!([raw]) }})
            })
            .collect();
        decision["checks"] = (1..=tiers.len())
            .map(|i| json!({"check_id": format!("CASE-{i}"), "effective_outcome": "PASS"}))
            .collect();
        let mut fixture = Self {
            ws,
            name,
            skill,
            raw,
            manifest,
            specification,
            cases,
            creator,
            plan,
            review,
            results,
            decision,
            evidence_path: PathBuf::new(),
        };
        fixture.freeze();
        fixture
    }

    fn freeze(&mut self) {
        let creator = self.ws.put("creator.json", &self.creator);
        let plan = self.ws.put("plan.json", &self.plan);
        self.review["plan"] = plan.clone();
        let review = self.ws.put("review.json", &self.review);
        self.results["plan"] = plan.clone();
        self.results["ai_review"] = review.clone();
        let results = self.ws.put("results.json", &self.results);
        self.decision["plan"] = plan.clone();
        self.decision["ai_review"] = review.clone();
        self.decision["results"] = results.clone();
        self.decision["task_results"] = self.results["task_results"].clone();
        self.decision["assertion_results"] = self.results["assertion_results"].clone();
        let decision = self.ws.put("decision.json", &self.decision);
        let mut inputs = json!({"manifest": self.manifest, "specification": self.specification,
            "cases": self.cases, "creator": creator, "plan": plan, "review": review,
            "results": results, "decision": decision});
        let acceptance = json!({"schema_version": "devforge.expert-install-acceptance/v1", "owner": "fixture-operator",
            "project_root": self.ws.project.to_str().unwrap(), "package": self.name, "action": "install",
            "inputs": inputs, "observation_basis": "operator-reviewed actual evidence"});
        let acceptance = self.ws.put("acceptance.json", &acceptance);
        let package = inputs.as_object_mut().unwrap();
        package.insert("name".into(), json!(self.name));
        package.insert("acceptance".into(), acceptance);
        let bundle = json!({"schema_version": "devforge.manual-expert-adoption/v1", "owner": "fixture-operator",
            "project_root": self.ws.project.to_str().unwrap(), "authorization": self.raw, "packages": [inputs]});
        self.evidence_path = pin_path(&self.ws.put("adoption.json", &bundle));
    }

    fn policy(&mut self) -> &mut Value {
        &mut self.plan["validation_policy"]
    }

    fn installed(&self) -> Value {
        self.ws.installed(&self.evidence_path)
    }

    fn refused(&self, needles: &[&str]) -> String {
        self.ws.refused(&self.evidence_path, needles)
    }

    fn rebind_cases(&mut self, catalog: &Value) {
        self.cases = self.ws.put("cases.json", catalog);
        self.plan["input_refs"] = json!([
            {"kind": "specification", "path": self.specification["path"], "sha256": self.specification["sha256"]},
            {"kind": "cases", "path": self.cases["path"], "sha256": self.cases["sha256"]}]);
        let cases = self.cases.clone();
        let policy = self.policy();
        policy["catalog_refs"] = json!([cases]);
        for row in policy["catalog_assertions"].as_array_mut().unwrap() {
            row["source_ref"] = cases.clone();
        }
    }
}

#[test]
fn exact_full_evidence_installs_and_records_custody() {
    let fixture = Adoption::new("Full");
    let first = fixture.installed();
    assert_eq!(
        first["scope"],
        "promoted Codex experts only; agents/hooks preserved"
    );
    assert_eq!(first["providers"], json!(["codex"]));
    assert_eq!(first["files"], 1);
    fixture.installed();
    let installed = fixture
        .ws
        .project
        .join(".agents/skills")
        .join(&fixture.name)
        .join("SKILL.md");
    assert_eq!(
        fs::read(&installed).unwrap(),
        fs::read(&fixture.skill).unwrap()
    );
    let inventory = fixture.ws.inventory();
    let adoption = &inventory["manual_expert_adoption"];
    assert_eq!(
        adoption["record"]["sha256"],
        sha(&fs::read(&fixture.evidence_path).unwrap())
    );
    assert_eq!(adoption["owner"], "fixture-operator");
    assert_eq!(adoption["predicate"], "manual-expert-adoption/v1");
    assert_eq!(adoption["packages"], json!([EVALUATOR]));
    assert!(adoption.get("_evidence").is_none());
    assert_eq!(inventory["schema"], 1);
    let files = inventory["files"].as_object().unwrap();
    assert_eq!(files.len(), 1);
    assert_eq!(
        files[".agents/skills/devforge-evaluate-expert/SKILL.md"],
        sha(b"codex skill")
    );
    let id = identity();
    assert_eq!(first["protected_identity"]["executable"], id["executable"]);
    assert_eq!(
        first["protected_identity"]["source_sha256"],
        id["source_sha256"]
    );
}

#[test]
fn bounded_routine_can_be_adopted_without_qualification() {
    let fixture = Adoption::new("Routine");
    fixture.installed();
    assert_eq!(fixture.decision["validation_disposition"], "ROUTINE_PASS");
}

#[test]
fn creator_phase_and_evaluator_task_cannot_be_omitted() {
    let mut fixture = Adoption::new("Full");
    fixture.creator["phases"]
        .as_object_mut()
        .unwrap()
        .remove("Design");
    fixture.freeze();
    fixture.refused(&["five creator phases"]);
    fixture.creator["phases"]["Design"] =
        json!({"classification": "Enforced", "evidence": [fixture.raw]});
    fixture.results["task_results"]
        .as_array_mut()
        .unwrap()
        .remove(3);
    fixture.freeze();
    fixture.refused(&["twelve tasks"]);
}

#[test]
fn self_review_and_optional_classification_are_refused() {
    let mut fixture = Adoption::new("Full");
    fixture.review["reviewer"]["identity"] = json!("fixture-author");
    fixture.freeze();
    fixture.refused(&["reviewer is not separately assigned"]);
    fixture.review["reviewer"]["identity"] = json!("fixture-reviewer");
    fixture.creator["phases"]["Authoring"]["classification"] = json!("Optional");
    fixture.freeze();
    fixture.refused(&["classification changed"]);
}

#[test]
fn missing_evidence_does_not_become_pass_from_summary() {
    let mut fixture = Adoption::new("Full");
    fixture.results["assertion_results"][0]["outcome"] = json!("NOT_RUN");
    fixture.freeze();
    fixture.refused(&["assertion incomplete"]);
}

#[test]
fn stale_candidate_or_leaf_evidence_refuses_before_writes() {
    let fixture = Adoption::new("Full");
    fs::write(&fixture.skill, b"new unreviewed bytes").unwrap();
    fixture.refused(&["planned bytes"]);
    fs::write(&fixture.skill, b"codex skill").unwrap();
    fs::write(pin_path(&fixture.raw), b"changed evidence").unwrap();
    fixture.refused(&["stale evidence"]);
}

#[test]
fn full_trigger_cannot_be_relabelled_routine() {
    let mut fixture = Adoption::new("Routine");
    fixture.policy()["impact"]["matched_rules"] = json!(["CI-05"]);
    fixture.freeze();
    fixture.refused(&["ineligible Routine"]);
}

#[test]
fn full_requires_actual_transfer_references() {
    let mut fixture = Adoption::new("Full");
    fixture.results["receiving_transfer"]["receiver_observation"] = Value::Null;
    fixture.freeze();
    fixture.refused(&["invalid evidence pin"]);
}

#[test]
fn duplicate_record_and_wrong_destination_are_refused() {
    let mut fixture = Adoption::new("Full");
    let raw = fs::read_to_string(&fixture.evidence_path).unwrap();
    let forged = format!("{}, \"owner\": \"another\"}}", &raw[..raw.len() - 1]);
    fs::write(&fixture.evidence_path, forged).unwrap();
    fixture.refused(&["duplicate JSON key"]);
    fixture.freeze();
    let mut bundle: Value =
        serde_json::from_slice(&fs::read(&fixture.evidence_path).unwrap()).unwrap();
    bundle["project_root"] = json!(fixture.ws.root.to_str().unwrap());
    fs::write(&fixture.evidence_path, serde_json::to_vec(&bundle).unwrap()).unwrap();
    fixture.refused(&["wrong installation destination"]);
}

#[test]
fn case_catalog_cannot_be_dropped_from_mutually_consistent_summaries() {
    let mut fixture = Adoption::new("Full");
    fixture.rebind_cases(&json!({"cases": [
        {"id": "CASE-1", "tier": "D", "required_observations": ["Must inspect exact bytes"]},
        {"id": "MISSING-CASE", "tier": "B", "required_observations": ["Must exercise the failure path"]}]}));
    fixture.freeze();
    fixture.refused(&["catalog"]);
}

#[test]
fn install_cannot_invalidate_its_own_accepted_evidence() {
    let mut fixture = Adoption::new("Full");
    fixture.installed();
    let record = fixture.ws.project.join(".devforge-install.json");
    fixture.creator["phases"]["Intake"]["evidence"]
        .as_array_mut()
        .unwrap()
        .push(pin(&record));
    fixture.freeze();
    fixture.refused(&["invalidate"]);
}

#[test]
fn install_cannot_invalidate_evidence_through_hardlink_alias() {
    let mut fixture = Adoption::new("Full");
    fixture.installed();
    let record = fixture.ws.project.join(".devforge-install.json");
    let alias = fixture.ws.root.join("evidence/inventory-alias.json");
    fs::hard_link(&record, &alias).unwrap();
    fixture.creator["phases"]["Intake"]["evidence"]
        .as_array_mut()
        .unwrap()
        .push(pin(&alias));
    fixture.freeze();
    fixture.refused(&["invalidate"]);
}

#[test]
fn malformed_nested_record_is_structured_refusal() {
    let mut fixture = Adoption::new("Full");
    fixture.review["reviewer"] = Value::Null;
    fixture.freeze();
    fixture.refused(&["malformed", "invalid"]);
}

#[test]
fn routine_cannot_invent_an_accepted_baseline() {
    let mut fixture = Adoption::new("Routine");
    fixture.policy()["baseline_identity"] = Value::Null;
    fixture.freeze();
    let reason = fixture.refused(&["Routine"]);
    assert!(reason.contains("baseline"), "{reason}");
}

#[test]
fn native_catalog_assertion_cannot_be_relabelled_deterministic() {
    let mut fixture = Adoption::new("Full");
    let mut catalog: Value =
        serde_json::from_slice(&fs::read(pin_path(&fixture.cases)).unwrap()).unwrap();
    catalog["cases"]
        .as_array_mut()
        .unwrap()
        .push(json!({"id": "NATIVE-EXTRA", "tier": "B",
        "required_observations": ["Observe actual bounded native behavior"]}));
    fixture.rebind_cases(&catalog);
    let cases = fixture.cases.clone();
    let raw = fixture.raw.clone();
    let policy = fixture.policy();
    policy["catalog_assertions"]
        .as_array_mut()
        .unwrap()
        .push(json!({
        "assertion_id": "NATIVE-EXTRA", "case_id": "NATIVE-EXTRA", "source_ref": cases,
        "source_pointer": "/cases/5/required_observations/0", "evidence_kinds": ["N"]}));
    policy["assertions"]
        .as_array_mut()
        .unwrap()
        .push(json!({"assertion_id": "NATIVE-EXTRA",
        "task_id": "T07", "tier": "D", "selection": "REQUIRED", "expectation": "pass"}));
    fixture.review["selection_review"]["reviewed_assertion_ids"]
        .as_array_mut()
        .unwrap()
        .push(json!("NATIVE-EXTRA"));
    fixture.results["assertion_results"]
        .as_array_mut()
        .unwrap()
        .push(json!({
        "assertion_id": "NATIVE-EXTRA", "selection": "REQUIRED", "integrity": "INTACT",
        "outcome": "PASS", "observation_refs": [raw], "grade_refs": []}));
    fixture.decision["checks"]
        .as_array_mut()
        .unwrap()
        .push(json!({"check_id": "NATIVE-EXTRA", "effective_outcome": "PASS"}));
    fixture.freeze();
    let reason = fixture.refused(&["native"]);
    assert!(
        reason.contains("tier") || reason.contains("obligation"),
        "{reason}"
    );
}

#[test]
fn targeted_install_preserves_other_skills_agents_and_hook_inventory() {
    let fixture = Adoption::new("Full");
    write(
        &fixture.ws.skills().join("unrelated/SKILL.md"),
        b"unrelated upstream source",
    );
    let settings = fixture.ws.project.join(".codex/hooks.json");
    write(&settings, br#"{"hooks":{},"user_setting":true}"#);
    let inventory = fixture.ws.project.join(".devforge-install.json");
    write(
        &inventory,
        serde_json::to_vec(&json!({"schema": 1, "files": {}, "managed_hooks": {},
            "runtime_evidence": {"codex": {"retained": "unrelated runtime"}}}))
        .unwrap()
        .as_slice(),
    );
    let before = fs::read(&settings).unwrap();
    let result = fixture.installed();
    assert_eq!(fs::read(&settings).unwrap(), before);
    assert!(!fixture.ws.project.join(".agents/skills/unrelated").exists());
    assert!(!fixture.ws.project.join(".codex/agents").exists());
    assert_eq!(
        result["scope"],
        "promoted Codex experts only; agents/hooks preserved"
    );
    assert_eq!(
        fixture.ws.inventory()["runtime_evidence"],
        json!({"codex": {"retained": "unrelated runtime"}})
    );
}

#[test]
fn managed_authoring_file_is_retired_but_edited_one_blocks() {
    let fixture = Adoption::new("Full");
    fixture.installed();
    let relative = ".agents/skills/devforge-evaluate-expert/evals/evals.json";
    let stale = fixture.ws.project.join(relative);
    write(&stale, b"old cases");
    let record = fixture.ws.project.join(".devforge-install.json");
    let mut inventory = fixture.ws.inventory();
    inventory["files"][relative] = json!(sha(b"old cases"));
    fs::write(&record, serde_json::to_vec(&inventory).unwrap()).unwrap();
    fs::write(&stale, b"user cases").unwrap();
    fixture.refused(&["local edit/collision"]);
    assert_eq!(fs::read(&stale).unwrap(), b"user cases");
    fs::write(&stale, b"old cases").unwrap();
    let result = fixture.installed();
    assert!(!stale.exists());
    assert_eq!(result["removed_authoring_files"], json!([relative]));
    assert!(fixture.ws.inventory()["files"].get(relative).is_none());
}

#[test]
fn locally_edited_installed_skill_is_preserved() {
    let fixture = Adoption::new("Full");
    fixture.installed();
    let dest = fixture
        .ws
        .project
        .join(".agents/skills/devforge-evaluate-expert/SKILL.md");
    fs::write(&dest, b"user modification").unwrap();
    fixture.refused(&["local edit/collision"]);
    assert_eq!(fs::read(&dest).unwrap(), b"user modification");
}

#[test]
fn missing_evidence_record_refuses_before_writes() {
    let fixture = Adoption::new("Full");
    let missing = fixture.ws.root.join("evidence/absent.json");
    fixture.ws.refused(&missing, &["evidence"]);
    assert!(!fixture.ws.project.join(".agents").exists());
}

/// Owner-approved unqualified local baseline fixture for the exact package pair.
/// A preserved prior allocation whose passing attempts are reused as observations.
/// `original`, `prior` and `closeout` are templates of the preserved records; freeze()
/// materializes them with the frozen set's pin, then the reference record points at them.
struct Allocation {
    id: String,
    record: Value,
    original: Value,
    prior: Option<Value>,
    closeout: Value,
    /// Point the reference's authorization at the preserved allocation record (text approval).
    bind_text_authorization: bool,
    /// Point the closeout's `allocation` pin at the materialized original record.
    closeout_binds_original: bool,
    /// Omit the preserved allocation's `authorization`; the closeout pins the approval instead.
    closeout_carries_authorization: bool,
    /// Record the preserved allocation's approval under `approval_ref` instead of `authorization`.
    approval_ref: bool,
    keys: Vec<String>,
    pin: Value,
}

/// One preserved attempt: its launch and completion records in the historical shape.
fn attempt_row(
    ws: &Workspace,
    slot: &str,
    from: &str,
    to: &str,
    returncode: i64,
    outcome: &str,
    evidence: &Value,
) -> Value {
    let launch = ws.put(
        &format!("{slot}-launch.json"),
        &json!({"slot": {"id": slot, "seconds": 1200, "parent": null}, "started_at_utc": from,
            "seconds_cap": 1200, "pid": 4242, "client": "/opt/codex/codex"}),
    );
    let completion = ws.put(
        &format!("{slot}-completion.json"),
        &json!({"returncode": returncode, "timed_out": false, "finished_at_utc": to}),
    );
    json!({"attempt_id": slot, "actor": "fixture-worker", "started_at_utc": from, "finished_at_utc": to,
        "outcome": outcome, "launch": launch, "completion": completion, "evidence": [evidence]})
}

struct Local {
    ws: Workspace,
    raw: Value,
    history: Value,
    packages: Vec<Value>,
    plan: Value,
    observations: BTreeMap<String, Value>,
    /// Check ID -> check ID whose observation it shares (compatible reuse).
    shared: BTreeMap<String, String>,
    allocations: Vec<Allocation>,
    results: Value,
    review: Value,
    evidence_path: PathBuf,
}

impl Local {
    fn new() -> Self {
        let ws = Workspace::new();
        let raw = ws.put(
            "raw.txt",
            &json!("Synthetic fixture: not real native observation or acceptance."),
        );
        let history = ws.put(
            "historical-failure.txt",
            &json!("Retained original FAIL; never rewritten."),
        );
        let mut packages = Vec::new();
        for name in [EVALUATOR, CREATOR] {
            let source = ws.skills().join(name);
            write(
                &source.join("SKILL.md"),
                format!("Exact synthetic {name}").as_bytes(),
            );
            let cases = source.join("evals/evals.json");
            write(
                &cases,
                serde_json::to_vec(&json!({"cases": [
                    {"id": "ORIGINAL-1", "required_observations": ["Original native assertion"]},
                    {"id": "ORIGINAL-2", "required_observations": ["Remaining qualification case"]}]}))
                .unwrap()
                .as_slice(),
            );
            let files = source_files(&source);
            let manifest = ws.put(
                &format!("{name}-runtime.json"),
                &json!({"schema_version": "devforge.expert-runtime-manifest/v1", "name": name,
                    "files_sha256": {"SKILL.md": files["SKILL.md"]}}),
            );
            let source_manifest = ws.put(
                &format!("{name}-source.json"),
                &json!({"source_root": source.to_str().unwrap(), "files_sha256": files}),
            );
            packages.push(
                json!({"name": name, "manifest": manifest, "source_manifest": source_manifest,
                "specification": raw, "cases": pin(&cases), "author": "fixture-author"}),
            );
        }
        let mut checks = Map::new();
        for (key, kind) in CHECKS {
            checks.insert(
                key.into(),
                json!({"kind": kind, "expectations": [format!("Frozen observable requirement for {key}")]}),
            );
        }
        let plan = json!({"schema_version": "devforge.manual-local-acceptance-set/v1",
            "project_root": ws.project.to_str().unwrap(), "owner": "fixture-owner", "authorization": raw,
            "packages": packages, "historical_evidence": [history],
            "frozen_at_utc": "2026-09-08T12:00:00Z", "max_seconds": 600, "max_native_turns": 7,
            "checks": checks});
        let identities: Map<String, Value> = packages
            .iter()
            .map(|p| {
                (
                    p["name"].as_str().unwrap().to_string(),
                    p["manifest"].clone(),
                )
            })
            .collect();
        let mut observations = BTreeMap::new();
        for (key, kind) in CHECKS {
            if kind != "N" {
                continue;
            }
            let mut observation = json!({"schema_version": "devforge.manual-local-observation/v1",
                "outcome": "PASS", "packages": identities, "native_client": "codex", "model": "gpt-6-astra",
                "reasoning_effort": "medium", "actor": "fixture-worker",
                "state_isolation": raw, "transcript": raw, "artifacts": [raw],
                "started_at_utc": "2026-09-08T12:00:01Z", "finished_at_utc": "2026-09-08T12:00:02Z",
                "manual_transfer": null});
            if key == "creator_to_evaluator" || key == "evaluator_to_creator" {
                observation["manual_transfer"] = json!({"direction": key, "user": "fixture-user",
                    "user_request": raw, "producer_output": raw, "receiver_observation": raw, "completed_action": raw});
            }
            observations.insert(key.to_string(), observation);
        }
        let mut qualification = Map::new();
        for package in &packages {
            qualification.insert(
                package["name"].as_str().unwrap().into(),
                json!({"ORIGINAL-1": "NOT_RUN", "ORIGINAL-2": "NOT_RUN"}),
            );
        }
        let results = json!({"schema_version": "devforge.manual-local-acceptance-results/v1",
            "qualification_status": "UNQUALIFIED", "started_at_utc": "2026-09-08T12:00:01Z",
            "finished_at_utc": "2026-09-08T12:00:03Z", "native_turns": 6,
            "qualification_cases": qualification});
        let mut criteria = Map::new();
        for i in 1..=10 {
            criteria.insert(
                format!("R{i:02}"),
                json!({"outcome": "PASS", "reason": "Synthetic semantic judgment", "evidence": [raw]}),
            );
        }
        let review = json!({"schema_version": "devforge.manual-local-review/v1", "reviewer": "fixture-independent-reviewer",
            "independence_evidence": raw, "overall": "PASS", "criteria": criteria});
        let mut fixture = Self {
            ws,
            raw,
            history,
            packages,
            plan,
            observations,
            shared: BTreeMap::new(),
            allocations: Vec::new(),
            results,
            review,
            evidence_path: PathBuf::new(),
        };
        fixture.freeze();
        fixture
    }

    fn identities(&self) -> Value {
        let map: Map<String, Value> = self
            .packages
            .iter()
            .map(|p| {
                (
                    p["name"].as_str().unwrap().to_string(),
                    p["manifest"].clone(),
                )
            })
            .collect();
        Value::Object(map)
    }

    /// Move the named checks' observations into a preserved prior allocation `id`
    /// closed out in the `STOPPED_BLOCKED` shape, whose ledger retains one failed attempt.
    fn reuse(&mut self, id: &str, keys: &[&str]) {
        self.reuse_format(id, keys, "stopped");
    }

    /// Synthetic copies of the preserved historical shapes: `stopped` (a
    /// STOPPED_BLOCKED closeout with a `native` block), `expired` (an
    /// EXPIRED_WITH_PARTIAL_OBSERVATIONS closeout with `slot_states`),
    /// `evaluator-return` (a COMPLETED_BOUNDED_EVALUATOR_RETURN closeout whose
    /// original allocation chains to a prior allocation and carries a text approval)
    /// and `stopped-multi` (a STOPPED_BLOCKED closeout recording one `native` entry
    /// per attempt, pinning its approval and its allocation, with an inline
    /// `native_completion` and a workflow judgment only where one was made).
    fn reuse_format(&mut self, id: &str, keys: &[&str], format: &str) {
        self.plan["frozen_at_utc"] = json!("2026-09-07T09:00:00Z");
        let (start, deadline) = ("2026-09-07T10:00:00Z", "2026-09-07T13:00:00Z");
        let mut attempts = Vec::new();
        if format != "evaluator-return" {
            attempts.push(attempt_row(
                &self.ws,
                &format!("{id}-a0"),
                start,
                "2026-09-07T10:00:00.5Z",
                -15,
                "COULD_NOT_RUN",
                &self.history,
            ));
        }
        if format == "stopped-multi" {
            // Completed with exit 0, but no workflow judgment was ever recorded for it.
            attempts.push(attempt_row(
                &self.ws,
                &format!("{id}-a1"),
                "2026-09-07T10:00:00.6Z",
                "2026-09-07T10:00:00.9Z",
                0,
                "NOT_EVALUATED",
                &self.raw,
            ));
        }
        for key in keys {
            let observation = self.observations.get_mut(*key).unwrap();
            observation["schema_version"] = json!("devforge.manual-local-observation/v2");
            observation["started_at_utc"] = json!("2026-09-07T10:00:01Z");
            observation["finished_at_utc"] = json!("2026-09-07T10:00:02Z");
            observation["attempt_id"] = json!(format!("{id}-{key}"));
            attempts.push(attempt_row(
                &self.ws,
                &format!("{id}-{key}"),
                "2026-09-07T10:00:01Z",
                "2026-09-07T10:00:02Z",
                0,
                "PASS",
                &self.raw,
            ));
        }
        let mut slots: Vec<Value> = attempts
            .iter()
            .map(|a| json!({"id": a["attempt_id"], "seconds": 1200, "parent": null}))
            .collect();
        slots.push(json!({"id": format!("{id}-unused"), "seconds": 1200, "parent": null}));
        let mut original = json!({"started_at_utc": start, "deadline_utc": deadline, "max_seconds": 10800,
            "child_turns": 5, "operator_returns": 5, "concurrency": 1, "retries": 0,
            "unlisted_continuations": 0, "client": "/home/fixture/.local/bin/codex",
            "model": "gpt-6-astra", "reasoning_effort": "medium", "authorization": self.raw,
            "slots": slots, "native_work_authorized": true});
        let mut prior = None;
        let (status, closeout) = match format {
            "stopped" => {
                let failed = &attempts[0];
                (
                    "STOPPED_BLOCKED",
                    json!({"status": "STOPPED_BLOCKED", "reason": "Synthetic preserved failure; never rewritten",
                        "started_at_utc": start, "original_deadline_utc": deadline,
                        "closed_at_utc": "2026-09-07T10:09:37+00:00", "clock_restarted": false,
                        "attempts_used": attempts.len(), "operator_returns_used": 1, "retries": 0,
                        "native": {"actor": "fixture-worker", "role": failed["attempt_id"],
                            "started_at_utc": failed["started_at_utc"], "finished_at_utc": failed["finished_at_utc"],
                            "process_exit": -15, "workflow_outcome": "COULD_NOT_RUN", "source_edits": 0, "timed_out": false},
                        "preserved": {"transcript": self.history, "launch": failed["launch"],
                            "completion": failed["completion"], "old_failures_unchanged": true},
                        "installation": "NOT_PERFORMED", "acceptance": "NOT_GRANTED"}),
                )
            }
            "expired" => {
                let mut states: Vec<Value> = attempts
                    .iter()
                    .map(|a| {
                        json!({"slot": a["attempt_id"], "launched": true,
                            "completion": {"returncode": if a["outcome"] == "PASS" { 0 } else { -15 },
                                "timed_out": false, "finished_at_utc": a["finished_at_utc"]}})
                    })
                    .collect();
                states.push(
                    json!({"slot": format!("{id}-unused"), "launched": false, "completion": null}),
                );
                (
                    "EXHAUSTED",
                    json!({"recorded_at_utc": "2026-09-07T13:03:16.8707712+00:00",
                        "status": "EXPIRED_WITH_PARTIAL_OBSERVATIONS",
                        "original_started_at_utc": "2026-09-07T06:00:00-04:00",
                        "original_deadline_utc": "2026-09-07T09:00:00-04:00",
                        "post_deadline_action": "Bookkeeping and exact-byte readback only; no native execution, test, source mutation, installation or budget reset",
                        "slot_states": states, "native_attempts": attempts.len(), "operator_returns": attempts.len(),
                        "unused_slots": [format!("{id}-unused")], "installation_status": "NOT_PERFORMED"}),
                )
            }
            "stopped-multi" => {
                let natives: Vec<Value> = attempts
                    .iter()
                    .map(|a| {
                        let failed = a["outcome"] == "COULD_NOT_RUN";
                        let mut entry = json!({"role": a["attempt_id"], "actor": "fixture-worker",
                            "started_at_utc": a["started_at_utc"], "finished_at_utc": a["finished_at_utc"],
                            "launch": a["launch"], "completion": a["completion"], "transcript": a["evidence"][0],
                            "packet": self.raw, "elapsed_seconds": 1.0,
                            "native_completion": {"returncode": if failed { -15 } else { 0 }, "timed_out": false,
                                "finished_at_utc": a["finished_at_utc"]}});
                        if a["outcome"] == "PASS" {
                            entry["workflow_outcome"] = json!("PASS");
                        } else if failed {
                            entry["workflow_outcome"] = json!("COULD_NOT_RUN");
                        }
                        entry
                    })
                    .collect();
                (
                    "STOPPED_BLOCKED",
                    json!({"status": "STOPPED_BLOCKED", "started_at_utc": start, "original_deadline_utc": deadline,
                        "finished_at_utc": "2026-09-07T10:39:37+00:00", "clock_restarted": false,
                        "attempts_used": attempts.len(), "max_attempts": 5, "operator_returns_used": attempts.len(),
                        "retries_after_stop": 0, "separately_authorized_replacement_clock": true,
                        "native": natives, "blocker": self.history, "installation": "NOT_PERFORMED",
                        "acceptance": "NOT_GRANTED", "final_review": "NOT_RUN",
                        "requirements": "All original nine unchanged; no final judgment or local acceptance result synthesized"}),
                )
            }
            "evaluator-return" => {
                assert_eq!(keys.len(), 1, "one bounded evaluator return");
                let row = &attempts[0];
                original.as_object_mut().unwrap().remove("authorization");
                original["authorization"] = json!(
                    "User said proceed to the proposed new window for one evaluator return, no retries or installation"
                );
                prior = Some(
                    json!({"started_at_utc": "2026-09-07T05:00:00Z", "deadline_utc": "2026-09-07T09:30:00Z",
                    "max_seconds": 14400, "child_turns": 13, "slots": []}),
                );
                (
                    "COMPLETED",
                    json!({"status": "COMPLETED_BOUNDED_EVALUATOR_RETURN", "closed_at_utc": "2026-09-07T10:20:05+00:00",
                        "started_at_utc": start, "deadline_utc": deadline, "elapsed_seconds": 959.1,
                        "native_turns": 1, "operator_returns": 1, "retries": 0, "native_actor": "fixture-worker",
                        "launch": row["launch"], "completion": row["completion"], "transcript": self.raw,
                        "evaluation": {"overall": "FAIL", "qualification_status": "UNQUALIFIED", "external_acceptance": "NOT_GRANTED"},
                        "installation": "NOT_PERFORMED"}),
                )
            }
            other => panic!("unknown fixture format {other}"),
        };
        let record = json!({"schema_version": "devforge.manual-local-allocation/v1", "allocation_id": id,
            "owner": "fixture-owner", "authorization": self.raw, "packages": self.identities(),
            "started_at_utc": start, "deadline_utc": deadline, "max_attempts": 5,
            "attempts": attempts, "status": status, "original_allocation": null, "closeout": null});
        let evaluator_return = format == "evaluator-return";
        let multi = format == "stopped-multi";
        self.allocations.push(Allocation {
            id: id.to_string(),
            record,
            original,
            prior,
            closeout,
            bind_text_authorization: evaluator_return,
            closeout_binds_original: evaluator_return || multi,
            closeout_carries_authorization: multi,
            approval_ref: false,
            keys: keys.iter().map(|k| k.to_string()).collect(),
            pin: Value::Null,
        });
        self.results["schema_version"] = json!("devforge.manual-local-acceptance-results/v2");
        let reused: usize = self.allocations.iter().map(|a| a.keys.len()).sum();
        self.results["reused_observations"] = json!(reused);
    }

    fn entry(&mut self, id: &str) -> &mut Allocation {
        self.allocations.iter_mut().find(|a| a.id == id).unwrap()
    }

    fn allocation(&mut self, id: &str) -> &mut Value {
        &mut self.entry(id).record
    }

    fn original(&mut self, id: &str) -> &mut Value {
        &mut self.entry(id).original
    }

    fn closeout(&mut self, id: &str) -> &mut Value {
        &mut self.entry(id).closeout
    }

    /// Bind the preserved-record templates to the frozen set, then materialize everything.
    fn freeze(&mut self) {
        let plan = self.ws.put("set.json", &self.plan);
        for allocation in &mut self.allocations {
            if let Some(prior) = &mut allocation.prior {
                prior["acceptance_set"] = plan.clone();
                let mut pin = self
                    .ws
                    .put(&format!("{}-prior-allocation.json", allocation.id), prior);
                pin["status"] = json!("Expired; not extended or rewritten");
                allocation.original["prior_allocation"] = pin;
            } else {
                allocation.original["acceptance_set"] = plan.clone();
            }
        }
        self.materialize();
    }

    /// Write the preserved records, references, observations, results, review and
    /// acceptance from the current templates without re-binding them to the set.
    fn materialize(&mut self) {
        let plan = self.ws.put("set.json", &self.plan);
        let mut listed = Vec::new();
        for allocation in &mut self.allocations {
            allocation.record["acceptance_set"] = plan.clone();
            if allocation.closeout_carries_authorization {
                allocation
                    .original
                    .as_object_mut()
                    .unwrap()
                    .remove("authorization");
                allocation.closeout["authorization"] = self.raw.clone();
            }
            if allocation.approval_ref {
                allocation
                    .original
                    .as_object_mut()
                    .unwrap()
                    .remove("authorization");
                allocation.original["approval_ref"] = self.raw.clone();
            }
            let original = self.ws.put(
                &format!("{}-original-allocation.json", allocation.id),
                &allocation.original,
            );
            if allocation.closeout_binds_original {
                allocation.closeout["allocation"] = original.clone();
            }
            if allocation.bind_text_authorization {
                allocation.record["authorization"] = original.clone();
            }
            allocation.record["original_allocation"] = original;
            allocation.record["closeout"] = self.ws.put(
                &format!("{}-closeout.json", allocation.id),
                &allocation.closeout,
            );
            allocation.pin = self.ws.put(
                &format!("allocation-{}.json", allocation.id),
                &allocation.record,
            );
            listed.push(allocation.pin.clone());
            for key in &allocation.keys {
                self.observations.get_mut(key).unwrap()["allocation"] = allocation.pin.clone();
            }
        }
        if !self.allocations.is_empty() {
            self.results["allocations"] = json!(listed);
        }
        let mut rows = Map::new();
        let mut written: BTreeMap<String, Value> = BTreeMap::new();
        for (key, kind) in CHECKS {
            let mut observation = Value::Null;
            if kind == "N" {
                if let Some(source) = self.shared.get(key) {
                    observation = written[source].clone();
                } else {
                    let entry = self.observations.get_mut(key).unwrap();
                    entry["acceptance_set"] = plan.clone();
                    observation = self.ws.put(&format!("{key}.json"), entry);
                    written.insert(key.to_string(), observation.clone());
                }
            }
            let evidence = if observation.is_null() {
                self.raw.clone()
            } else {
                observation.clone()
            };
            rows.insert(
                key.into(),
                json!({"outcome": "PASS", "evidence": [evidence], "native_observation": observation}),
            );
        }
        self.results["acceptance_set"] = plan.clone();
        self.results["checks"] = Value::Object(rows.clone());
        self.review["acceptance_set"] = plan;
        self.review["packages"] = json!(self.packages);
        let mut judgments = Map::new();
        for (key, row) in rows {
            judgments.insert(
                key,
                json!({"outcome": "PASS", "reason": "Synthetic separate judgment", "evidence": row["evidence"]}),
            );
        }
        self.review["check_judgments"] = Value::Object(judgments);
        self.publish();
    }

    fn publish(&mut self) {
        let mut record = json!({"schema_version": "devforge.manual-expert-local-baseline/v1",
            "project_root": self.ws.project.to_str().unwrap(), "owner": "fixture-owner", "authorization": self.raw,
            "packages": self.packages, "acceptance_set": self.ws.put("set.json", &self.plan),
            "results": self.ws.put("results.json", &self.results), "review": self.ws.put("review.json", &self.review),
            "historical_evidence": [self.history]});
        let acceptance = self.ws.put(
            "acceptance.json",
            &json!({"schema_version": "devforge.manual-local-owner-acceptance/v1", "owner": "fixture-owner",
                "action": "install_unqualified_local_baseline", "qualification_status": "UNQUALIFIED",
                "inputs": record.clone(), "observation_basis": "operator-reviewed actual evidence"}),
        );
        record["acceptance"] = acceptance;
        self.evidence_path = pin_path(&self.ws.put("adoption.json", &record));
    }

    fn installed(&self) -> Value {
        self.ws.installed(&self.evidence_path)
    }

    fn refused(&self, needles: &[&str]) -> String {
        self.ws.refused(&self.evidence_path, needles)
    }

    fn source(&self, index: usize) -> PathBuf {
        self.ws
            .skills()
            .join(self.packages[index]["name"].as_str().unwrap())
    }

    /// The preflight packet for the currently materialized set, packages, allocation
    /// references and the observations those allocations supplied.
    fn packet_record(&self) -> Value {
        let mut observations = Vec::new();
        for allocation in &self.allocations {
            for key in &allocation.keys {
                observations.push(json!({"check": key,
                    "observation": self.results["checks"][key]["native_observation"]}));
            }
        }
        json!({"schema_version": "devforge.manual-local-evidence-preflight/v1",
            "project_root": self.ws.project.to_str().unwrap(), "owner": "fixture-owner",
            "authorization": self.raw, "packages": self.packages,
            "acceptance_set": self.results["acceptance_set"], "historical_evidence": [self.history],
            "allocations": self.results.get("allocations").cloned().unwrap_or_else(|| json!([])),
            "observations": observations})
    }

    fn packet(&self) -> PathBuf {
        pin_path(&self.ws.put("preflight-packet.json", &self.packet_record()))
    }
}

/// The report entry for the named preflight check.
fn check<'a>(report: &'a Value, name: &str) -> &'a Value {
    report["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["check"] == name)
        .unwrap_or_else(|| panic!("no check {name} in {report}"))
}

fn allocation_entry<'a>(report: &'a Value, id: &str) -> &'a Value {
    report["allocations"]
        .as_array()
        .unwrap()
        .iter()
        .find(|a| a["allocation_id"] == id)
        .unwrap_or_else(|| panic!("no allocation {id} in {report}"))
}

#[test]
fn owner_approved_local_set_installs_exact_unqualified_baseline() {
    let fixture = Local::new();
    let result = fixture.installed();
    assert_eq!(result["qualification_status"], "UNQUALIFIED");
    assert_eq!(result["acceptance_status"], "LOCAL_ACCEPTANCE_SET_PASS");
    let inventory = fixture.ws.inventory();
    assert_eq!(
        inventory["manual_expert_adoption"]["acceptance_status"],
        "LOCAL_ACCEPTANCE_SET_PASS"
    );
    assert_eq!(
        inventory["manual_expert_adoption"]["qualification_status"],
        "UNQUALIFIED"
    );
    assert_eq!(
        inventory["manual_expert_adoption"]["predicate"],
        "manual-local-baseline/v1"
    );
    assert_eq!(
        inventory["manual_expert_adoption"]["packages"],
        json!([EVALUATOR, CREATOR])
    );
    for (index, package) in fixture.packages.iter().enumerate() {
        let installed = fixture
            .ws
            .project
            .join(".agents/skills")
            .join(package["name"].as_str().unwrap());
        assert_eq!(
            fs::read(installed.join("SKILL.md")).unwrap(),
            fs::read(fixture.source(index).join("SKILL.md")).unwrap()
        );
        assert!(!installed.join("evals").exists());
    }
    assert_eq!(
        fs::read_to_string(pin_path(&fixture.history)).unwrap(),
        "Retained original FAIL; never rewritten."
    );
}

#[test]
fn remaining_cases_cannot_be_omitted_or_relabelled_pass() {
    let mut fixture = Local::new();
    let first = fixture.packages[0]["name"].as_str().unwrap().to_string();
    fixture.results["qualification_cases"][&first]
        .as_object_mut()
        .unwrap()
        .remove("ORIGINAL-2");
    fixture.publish();
    fixture.refused(&["qualification cases"]);
    fixture.results["qualification_cases"][&first]["ORIGINAL-2"] = json!("PASS");
    fixture.publish();
    fixture.refused(&["qualification cases"]);
}

#[test]
fn failed_selected_check_blocks_even_with_passing_summary() {
    let mut fixture = Local::new();
    fixture.results["checks"]["grounded_creation"]["outcome"] = json!("NOT_RUN");
    fixture.publish();
    fixture.refused(&["acceptance check"]);
}

#[test]
fn plan_must_precede_observations() {
    let mut fixture = Local::new();
    fixture.plan["frozen_at_utc"] = json!("2026-09-08T12:00:04Z");
    fixture.freeze();
    fixture.refused(&["predefined"]);
}

#[test]
fn manual_handoff_cannot_be_replaced_with_prepared_text() {
    let mut fixture = Local::new();
    fixture
        .observations
        .get_mut("creator_to_evaluator")
        .unwrap()["manual_transfer"] = Value::Null;
    fixture.freeze();
    fixture.refused(&["manual transfer"]);
}

#[test]
fn source_only_addition_refuses_before_writes() {
    let fixture = Local::new();
    write(
        &fixture.source(0).join("evals/new.md"),
        b"Unreviewed source-only addition",
    );
    fixture.refused(&["source identity"]);
}

#[test]
fn historical_failure_changes_refuse_before_writes() {
    let fixture = Local::new();
    fs::write(pin_path(&fixture.history), b"Rewritten to PASS").unwrap();
    fixture.refused(&["stale evidence"]);
}

#[test]
fn exact_owner_acceptance_cannot_be_downgraded_to_generic_install() {
    let fixture = Local::new();
    let mut root: Value =
        serde_json::from_slice(&fs::read(&fixture.evidence_path).unwrap()).unwrap();
    let mut acceptance: Value =
        serde_json::from_slice(&fs::read(pin_path(&root["acceptance"])).unwrap()).unwrap();
    acceptance["action"] = json!("install");
    root["acceptance"] = fixture.ws.put("acceptance.json", &acceptance);
    fs::write(&fixture.evidence_path, serde_json::to_vec(&root).unwrap()).unwrap();
    fixture.refused(&["owner acceptance"]);
}

#[test]
fn author_or_native_actor_cannot_supply_independent_review() {
    let mut fixture = Local::new();
    fixture.review["reviewer"] = json!("fixture-author");
    fixture.publish();
    fixture.refused(&["independent reviewer"]);
    fixture.review["reviewer"] = json!("fixture-worker");
    fixture.publish();
    fixture.refused(&["independent reviewer"]);
}

#[test]
fn identity_reports_running_executable_digest_and_source_identity() {
    let id = identity();
    assert_eq!(id["schema_version"], "devforge.executable-identity/v1");
    let executable = PathBuf::from(id["executable"]["path"].as_str().unwrap());
    assert_eq!(executable, fs::canonicalize(BIN).unwrap());
    assert_eq!(
        id["executable"]["sha256"],
        sha(&fs::read(&executable).unwrap())
    );
    let source = id["source_sha256"].as_str().unwrap();
    assert_eq!(source.len(), 64);
    assert!(source.bytes().all(|b| b.is_ascii_hexdigit()));
}

#[test]
fn changed_expected_executable_digest_is_refused_before_writes() {
    let fixture = Adoption::new("Full");
    let id = identity();
    let mut executable = id["executable"].clone();
    executable["sha256"] = json!("0".repeat(64));
    write_authority(
        &fixture.ws.authority,
        &authority_record(&executable, &id["source_sha256"]),
    );
    let reason = fixture.refused(&["executable identity"]);
    assert!(reason.contains("protected authority"), "{reason}");
    assert!(!fixture.ws.project.join(".agents").exists());
}

#[test]
fn changed_source_identity_is_refused_before_writes() {
    let fixture = Adoption::new("Full");
    let id = identity();
    write_authority(
        &fixture.ws.authority,
        &authority_record(&id["executable"], &json!("f".repeat(64))),
    );
    fixture.refused(&["source identity"]);
}

#[test]
fn copied_executable_at_another_path_is_not_the_pinned_authority() {
    let fixture = Adoption::new("Full");
    let copy = fixture.ws.root.join("devforge-copy");
    fs::copy(BIN, &copy).unwrap();
    fs::set_permissions(&copy, fs::Permissions::from_mode(0o755)).unwrap();
    fixture.ws.refused_with(
        copy.to_str().unwrap(),
        &fixture.evidence_path,
        &["executable identity"],
    );
}

#[test]
fn authority_record_inside_project_or_framework_is_refused() {
    let mut fixture = Adoption::new("Full");
    let record = fs::read(&fixture.ws.authority).unwrap();
    for inside in [
        fixture.ws.project.join("authority.json"),
        fixture.ws.framework.join("authority.json"),
    ] {
        write(&inside, &record);
        fixture.ws.authority = inside.clone();
        let reason = fixture.refused(&["authority"]);
        assert!(reason.contains("outside"), "{reason}");
        fs::remove_file(&inside).unwrap();
    }
}

#[test]
fn malformed_authority_record_is_refused() {
    let fixture = Adoption::new("Full");
    let id = identity();
    let mut extra = authority_record(&id["executable"], &id["source_sha256"]);
    extra["extra"] = json!(true);
    write_authority(&fixture.ws.authority, &extra);
    fixture.refused(&["authority record"]);
    let mut missing = authority_record(&id["executable"], &id["source_sha256"]);
    missing.as_object_mut().unwrap().remove("owner");
    write_authority(&fixture.ws.authority, &missing);
    fixture.refused(&["authority record"]);
    write(
        &fixture.ws.authority,
        b"{\"schema_version\": 1, \"schema_version\": 2}",
    );
    fixture.refused(&["duplicate JSON key"]);
}

/// The unchanged legacy Python installer, invoked only as a compatibility baseline.
/// It supplies comparison evidence for a preserved contract; it makes no decision here.
fn legacy_install(ws: &Workspace, evidence: &Path) -> Run {
    let script = Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts/install_framework.py");
    run(
        "/usr/bin/python3",
        &[
            script.to_str().unwrap(),
            "--framework",
            ws.framework.to_str().unwrap(),
            "--project",
            ws.project.to_str().unwrap(),
            "--provider",
            "codex",
            "--manual-experts-only",
            "--manual-evidence",
            evidence.to_str().unwrap(),
        ],
    )
}

// Review regressions (PR 3 review, 2026-09-09): each failed against bf6edc1.

#[test]
fn install_must_not_overwrite_authority_record_through_destination_alias() {
    let fixture = Local::new();
    let before_authority = fs::read(&fixture.ws.authority).unwrap();
    let relative = format!(".agents/skills/{EVALUATOR}/SKILL.md");
    let destination = fixture.ws.project.join(&relative);
    fs::create_dir_all(destination.parent().unwrap()).unwrap();
    fs::hard_link(&fixture.ws.authority, &destination).unwrap();
    let inventory = json!({"schema": 1, "files": {relative.clone(): sha(&before_authority)},
        "managed_hooks": {}});
    write(
        &fixture.ws.project.join(".devforge-install.json"),
        &serde_json::to_vec(&inventory).unwrap(),
    );
    let outcome = fixture.ws.install(&fixture.evidence_path);
    assert_eq!(
        fs::read(&fixture.ws.authority).unwrap(),
        before_authority,
        "installation changed authority record via destination hardlink; exit={} result={}",
        outcome.code,
        outcome.text
    );
    assert_eq!(
        outcome.code, 2,
        "must refuse authority alias before writes: {}",
        outcome.text
    );
    assert_eq!(outcome.output["status"], "BLOCKED");
    assert!(
        outcome.output["reason"]
            .as_str()
            .unwrap_or_default()
            .contains("alias"),
        "{}",
        outcome.text
    );
}

#[test]
fn install_must_not_overwrite_pinned_executable_through_destination_alias() {
    let fixture = Local::new();
    let executable = pin_path(&identity()["executable"]);
    let before = fs::read(&executable).unwrap();
    let relative = format!(".agents/skills/{CREATOR}/SKILL.md");
    let destination = fixture.ws.project.join(&relative);
    fs::create_dir_all(destination.parent().unwrap()).unwrap();
    fs::hard_link(&executable, &destination).unwrap();
    let inventory =
        json!({"schema": 1, "files": {relative.clone(): sha(&before)}, "managed_hooks": {}});
    write(
        &fixture.ws.project.join(".devforge-install.json"),
        &serde_json::to_vec(&inventory).unwrap(),
    );
    let outcome = fixture.ws.install(&fixture.evidence_path);
    assert_eq!(fs::read(&executable).unwrap(), before, "{}", outcome.text);
    assert_eq!(outcome.code, 2, "{}", outcome.text);
    assert_eq!(outcome.output["status"], "BLOCKED");
}

#[test]
fn symlinked_promoted_skill_must_refuse_not_silently_skip() {
    let fixture = Adoption::new("Full");
    std::os::unix::fs::symlink(
        fixture.ws.skills().join(EVALUATOR),
        fixture.ws.skills().join(CREATOR),
    )
    .unwrap();
    let legacy = legacy_install(&fixture.ws, &fixture.evidence_path);
    assert_ne!(
        legacy.code, 0,
        "baseline unexpectedly allowed symlink: {}",
        legacy.text
    );
    let reason = fixture.refused(&["symlink"]);
    assert!(
        reason.contains("missing or symlink source directory"),
        "{reason}"
    );
}

#[test]
fn basic_iso_timestamps_preserve_accepted_evidence_compatibility() {
    let mut fixture = Local::new();
    fixture.plan["frozen_at_utc"] = json!("20260908T120000+0000");
    fixture.results["started_at_utc"] = json!("20260908T120001+0000");
    fixture.results["finished_at_utc"] = json!("20260908T120003+0000");
    for observation in fixture.observations.values_mut() {
        observation["started_at_utc"] = json!("20260908T120001+0000");
        observation["finished_at_utc"] = json!("20260908T120002+0000");
    }
    fixture.freeze();
    let legacy = legacy_install(&fixture.ws, &fixture.evidence_path);
    assert_eq!(
        legacy.code, 0,
        "baseline did not accept basic ISO fixture: {}",
        legacy.text
    );
    let outcome = fixture.ws.install(&fixture.evidence_path);
    assert_eq!(
        outcome.code, 0,
        "Rust rejected baseline-compatible timestamps: {}",
        outcome.text
    );
    assert_eq!(outcome.output["status"], "INSTALLED");
}

/// Local-baseline fixture whose every timestamp uses one date prefix and offset suffix.
fn timestamp_fixture(prefix: &str, offset: &str) -> Local {
    let mut fixture = Local::new();
    fixture.plan["frozen_at_utc"] = json!(format!("{prefix}T12:00:00{offset}"));
    fixture.results["started_at_utc"] = json!(format!("{prefix}T12:00:01{offset}"));
    fixture.results["finished_at_utc"] = json!(format!("{prefix}T12:00:03{offset}"));
    for observation in fixture.observations.values_mut() {
        observation["started_at_utc"] = json!(format!("{prefix}T12:00:01{offset}"));
        observation["finished_at_utc"] = json!(format!("{prefix}T12:00:02{offset}"));
    }
    fixture.freeze();
    fixture
}

// Review regressions at 5a490fe (repair-5a490fe review, 2026-09-09).

#[test]
fn invalid_iso_week_53_must_be_refused() {
    let fixture = timestamp_fixture("2021-W53-1", "+00:00");
    let legacy = legacy_install(&fixture.ws, &fixture.evidence_path);
    assert_ne!(
        legacy.code, 0,
        "baseline accepted invalid week date: {}",
        legacy.text
    );
    assert!(
        legacy.text.contains("invalid timestamp"),
        "unexpected baseline refusal: {}",
        legacy.text
    );
    assert!(!fixture.ws.project.join(".agents").exists());
    fixture.refused(&["invalid timestamp"]);
    assert!(!fixture.ws.project.join(".agents").exists());
}

#[test]
fn fractional_offset_must_preserve_legacy_acceptance() {
    let fixture = timestamp_fixture("2026-09-08", "+00:00:01.5");
    let legacy = legacy_install(&fixture.ws, &fixture.evidence_path);
    assert_eq!(
        legacy.code, 0,
        "baseline did not accept fractional offset: {}",
        legacy.text
    );
    let outcome = fixture.ws.install(&fixture.evidence_path);
    assert_eq!(
        outcome.code, 0,
        "Rust rejected baseline-valid offset: {}",
        outcome.text
    );
    assert_eq!(outcome.output["status"], "INSTALLED");
}

#[test]
fn fractional_offset_chronology_is_compared_by_instant() {
    // Frozen 12:00:01.2+00:00:01.5 is 11:59:59.7 UTC only when the offset fraction is
    // applied (12:00:00.2 if it were ignored); results start at exactly 12:00:00 UTC, so
    // the "predefined" order holds only with the fraction applied.
    let mut fixture = timestamp_fixture("2026-09-08", "+00:00");
    fixture.plan["frozen_at_utc"] = json!("2026-09-08T12:00:01.2+00:00:01.5");
    fixture.results["started_at_utc"] = json!("2026-09-08T12:00:00+00:00");
    fixture.freeze();
    let legacy = legacy_install(&fixture.ws, &fixture.evidence_path);
    assert_eq!(
        legacy.code, 0,
        "baseline chronology differs: {}",
        legacy.text
    );
    fixture.installed();
    // Matched control: the same digits swapped, 12:00:01.5+00:00:01.2, is 12:00:00.3 UTC,
    // after the start, and must refuse.
    let mut control = timestamp_fixture("2026-09-08", "+00:00");
    control.plan["frozen_at_utc"] = json!("2026-09-08T12:00:01.5+00:00:01.2");
    control.results["started_at_utc"] = json!("2026-09-08T12:00:00+00:00");
    control.freeze();
    let legacy = legacy_install(&control.ws, &control.evidence_path);
    assert_eq!(legacy.code, 2, "baseline control differs: {}", legacy.text);
    control.refused(&["predefined"]);
}

// Review regressions at c3eb99d (repair-c3eb99d review, 2026-09-10).

#[test]
fn short_offset_fraction_preserves_legacy_contract() {
    let fixture = timestamp_fixture("2026-09-08", "+00:00.5");
    let legacy = legacy_install(&fixture.ws, &fixture.evidence_path);
    assert_eq!(
        legacy.code, 0,
        "legacy rejected the documented accepted form: {}",
        legacy.text
    );
    let outcome = fixture.ws.install(&fixture.evidence_path);
    assert_eq!(
        outcome.code, legacy.code,
        "timestamp acceptance contract differs: {}",
        outcome.text
    );
    assert_eq!(outcome.output["status"], "INSTALLED");
}

#[test]
fn zero_whole_offset_fraction_preserves_legacy_chronology() {
    // The legacy parser treats +00:00:00.5 as UTC, so frozen 12:00:00.2 is after the
    // 12:00:00.1 start and the set is not predefined.
    let mut fixture = timestamp_fixture("2026-09-08", "+00:00");
    fixture.plan["frozen_at_utc"] = json!("2026-09-08T12:00:00.2+00:00:00.5");
    fixture.results["started_at_utc"] = json!("2026-09-08T12:00:00.1+00:00");
    fixture.freeze();
    let legacy = legacy_install(&fixture.ws, &fixture.evidence_path);
    assert_eq!(
        legacy.code, 2,
        "legacy chronology control differs: {}",
        legacy.text
    );
    assert!(
        legacy.text.contains("predefined"),
        "unexpected legacy rejection: {}",
        legacy.text
    );
    assert!(!fixture.ws.project.join(".agents").exists());
    fixture.refused(&["predefined"]);
    assert!(!fixture.ws.project.join(".agents").exists());
}

#[test]
fn conflicting_replacement_aliases_must_not_report_installed() {
    let fixture = Local::new();
    fixture.installed();
    let erel = format!(".agents/skills/{EVALUATOR}/SKILL.md");
    let crel = format!(".agents/skills/{CREATOR}/SKILL.md");
    let evaluator = fixture.ws.project.join(&erel);
    let creator = fixture.ws.project.join(&crel);
    fs::remove_file(&creator).unwrap();
    fs::hard_link(&evaluator, &creator).unwrap();
    let shared = sha(&fs::read(&evaluator).unwrap());
    let mut inventory = fixture.ws.inventory();
    inventory["files"][&erel] = json!(shared);
    inventory["files"][&crel] = json!(shared);
    write(
        &fixture.ws.project.join(".devforge-install.json"),
        &serde_json::to_vec(&inventory).unwrap(),
    );
    let before = snapshot(&fixture.ws.project);
    let outcome = fixture.ws.install(&fixture.evidence_path);
    assert_eq!(
        outcome.code,
        2,
        "conflicting inode payloads must refuse; result={} creator={} evaluator={}",
        outcome.text,
        String::from_utf8_lossy(&fs::read(&creator).unwrap()),
        String::from_utf8_lossy(&fs::read(&evaluator).unwrap())
    );
    assert_eq!(outcome.output["status"], "BLOCKED");
    assert_eq!(
        snapshot(&fixture.ws.project),
        before,
        "refusal must not write"
    );
}

// Cross-allocation evidence reuse (2026-09-10).

#[test]
fn reused_observations_from_a_prior_allocation_install_without_counting_as_new_turns() {
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation", "creator_to_evaluator"]);
    // Only the four observations of the current interval are new native turns.
    fixture.results["native_turns"] = json!(4);
    fixture.freeze();
    let result = fixture.installed();
    assert_eq!(result["qualification_status"], "UNQUALIFIED");
    assert_eq!(result["acceptance_status"], "LOCAL_ACCEPTANCE_SET_PASS");
    let adoption = &fixture.ws.inventory()["manual_expert_adoption"];
    assert_eq!(adoption["reused_allocations"], json!(["alloc-01"]));
    assert_eq!(adoption["reused_observations"], 2);
    assert_eq!(adoption["acceptance_status"], "LOCAL_ACCEPTANCE_SET_PASS");
    // Every original record, including the retained failure, is untouched.
    let closeout: Value = serde_json::from_slice(
        &fs::read(pin_path(&fixture.allocation("alloc-01")["closeout"])).unwrap(),
    )
    .unwrap();
    // One retained failed attempt plus the two reused passing attempts.
    assert_eq!(closeout["attempts_used"], 3);
    assert_eq!(closeout["status"], "STOPPED_BLOCKED");
    assert!(
        fixture.allocation("alloc-01")["attempts"]
            .as_array()
            .unwrap()
            .iter()
            .any(|a| a["outcome"] == "COULD_NOT_RUN")
    );
    assert_eq!(
        fs::read_to_string(pin_path(&fixture.history)).unwrap(),
        "Retained original FAIL; never rewritten."
    );
    // A turn count below the current observations still refuses.
    fixture.results["native_turns"] = json!(3);
    fixture.freeze();
    fixture.refused(&["native turn count omits observations"]);
}

#[test]
fn reused_observation_requires_listed_allocation() {
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.results["schema_version"] = json!("devforge.manual-local-acceptance-results/v1");
    fixture
        .results
        .as_object_mut()
        .unwrap()
        .remove("reused_observations");
    fixture.freeze();
    fixture
        .results
        .as_object_mut()
        .unwrap()
        .remove("allocations");
    fixture.publish();
    fixture.refused(&["reused observation requires"]);
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.freeze();
    let unlisted = fixture
        .ws
        .put("unlisted-allocation.json", &json!({"other": true}));
    fixture.observations.get_mut("grounded_creation").unwrap()["allocation"] = unlisted.clone();
    fixture.observations.get_mut("grounded_creation").unwrap()["acceptance_set"] =
        fixture.results["acceptance_set"].clone();
    let observation = fixture.ws.put(
        "grounded_creation.json",
        &fixture.observations["grounded_creation"],
    );
    fixture.results["checks"]["grounded_creation"]["native_observation"] = observation.clone();
    fixture.results["checks"]["grounded_creation"]["evidence"] = json!([observation]);
    fixture.review["check_judgments"]["grounded_creation"]["evidence"] = json!([observation]);
    fixture.publish();
    fixture.refused(&["unlisted allocation"]);
}

#[test]
fn allocation_without_authorization_or_with_wrong_identity_is_refused() {
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture
        .allocation("alloc-01")
        .as_object_mut()
        .unwrap()
        .remove("authorization");
    fixture.freeze();
    fixture.refused(&["invalid allocation record"]);
    fixture.allocation("alloc-01")["authorization"] = json!({"path": "/nonexistent/approval.txt",
        "sha256": "0".repeat(64)});
    fixture.freeze();
    fixture.refused(&["evidence"]);
    let raw = fixture.raw.clone();
    fixture.allocation("alloc-01")["authorization"] = raw;
    fixture.allocation("alloc-01")["packages"][EVALUATOR]["sha256"] = json!("f".repeat(64));
    fixture.freeze();
    fixture.refused(&["allocation candidate identity differs"]);
    let identities = fixture.identities();
    fixture.allocation("alloc-01")["packages"] = identities;
    fixture.allocation("alloc-01")["owner"] = json!("someone-else");
    fixture.freeze();
    fixture.refused(&["allocation owner differs"]);
}

#[test]
fn allocation_must_bind_the_same_frozen_acceptance_set() {
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.freeze();
    // Re-point the allocation at a different frozen set without touching anything else.
    let other = fixture
        .ws
        .put("other-set.json", &json!({"frozen": "elsewhere"}));
    let mut record = fixture.allocation("alloc-01").clone();
    record["acceptance_set"] = other;
    let pin = fixture.ws.put("allocation-alloc-01.json", &record);
    fixture.results["allocations"] = json!([pin.clone()]);
    fixture.observations.get_mut("grounded_creation").unwrap()["allocation"] = pin;
    fixture.observations.get_mut("grounded_creation").unwrap()["acceptance_set"] =
        fixture.results["acceptance_set"].clone();
    let observation = fixture.ws.put(
        "grounded_creation.json",
        &fixture.observations["grounded_creation"],
    );
    fixture.results["checks"]["grounded_creation"]["native_observation"] = observation.clone();
    fixture.results["checks"]["grounded_creation"]["evidence"] = json!([observation]);
    fixture.review["check_judgments"]["grounded_creation"]["evidence"] = json!([observation]);
    fixture.publish();
    fixture.refused(&["different acceptance set"]);
}

#[test]
fn reused_observation_outside_its_allocation_window_is_refused() {
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    let observation = fixture.observations.get_mut("grounded_creation").unwrap();
    observation["started_at_utc"] = json!("2026-09-07T13:00:01Z");
    observation["finished_at_utc"] = json!("2026-09-07T13:00:02Z");
    fixture.freeze();
    fixture.refused(&["outside its allocation window"]);
    // An allocation that starts before the set was frozen is not an approved window for it.
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.plan["frozen_at_utc"] = json!("2026-09-07T10:30:00Z");
    fixture.freeze();
    fixture.refused(&["allocation window must follow the frozen acceptance set"]);
}

#[test]
fn concealed_failed_or_exhausted_attempts_are_refused() {
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    // Dropping the retained failed attempt contradicts the preserved closeout.
    fixture.allocation("alloc-01")["attempts"]
        .as_array_mut()
        .unwrap()
        .remove(0);
    fixture.freeze();
    fixture.refused(&["closeout differs from its ledger"]);
    // Claiming more attempts than the preserved allocation approved.
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.allocation("alloc-01")["max_attempts"] = json!(1);
    fixture.original("alloc-01")["child_turns"] = json!(1);
    fixture.freeze();
    fixture.refused(&["allocation attempt limit exceeded"]);
    // A reference cap that differs from the preserved allocation's own cap.
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.allocation("alloc-01")["max_attempts"] = json!(9);
    fixture.freeze();
    fixture.refused(&["reference attempt limit differs from the preserved allocation"]);
    // Relabelling the allocation status without its closeout.
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.allocation("alloc-01")["status"] = json!("COMPLETED");
    fixture.freeze();
    fixture.refused(&["reference status differs from the preserved closeout"]);
    // Reusing the attempt that did not pass.
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.observations.get_mut("grounded_creation").unwrap()["attempt_id"] = json!("alloc-01-a0");
    fixture.freeze();
    fixture.refused(&["reused attempt did not pass"]);
    // An attempt recorded outside the approved window.
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.allocation("alloc-01")["attempts"][0]["finished_at_utc"] =
        json!("2026-09-07T13:00:01Z");
    fixture.freeze();
    fixture.refused(&["allocation attempt outside its approved window"]);
}

#[test]
fn reused_observation_must_match_its_recorded_attempt() {
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.observations.get_mut("grounded_creation").unwrap()["attempt_id"] =
        json!("alloc-01-unknown");
    fixture.freeze();
    fixture.refused(&["not a recorded attempt"]);
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.observations.get_mut("grounded_creation").unwrap()["actor"] = json!("another-worker");
    fixture.freeze();
    fixture.refused(&["differs from its recorded attempt"]);
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    let other = fixture
        .ws
        .put("other-transcript.txt", &json!("Unrecorded transcript"));
    fixture.observations.get_mut("grounded_creation").unwrap()["transcript"] = other;
    fixture.freeze();
    fixture.refused(&["not the attempt's recorded evidence"]);
}

#[test]
fn compatible_reuse_across_checks_still_needs_separate_judgments() {
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["reuse"]);
    fixture
        .shared
        .insert("bounded_enhancement".into(), "reuse".into());
    fixture.results["reused_observations"] = json!(1);
    fixture.results["native_turns"] = json!(4);
    fixture.freeze();
    fixture.installed();
    assert_eq!(
        fixture.ws.inventory()["manual_expert_adoption"]["reused_observations"],
        1
    );
    fixture.review["check_judgments"]
        .as_object_mut()
        .unwrap()
        .remove("bounded_enhancement");
    fixture.publish();
    fixture.refused(&["local semantic review coverage incomplete"]);
}

// PR 5 review regressions (2026-09-10): the reference must agree with the preserved sources.

#[test]
fn original_window_cannot_be_extended_by_reference() {
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    // The preserved allocation ended at 10:00:01; the reference claims 13:00:00 and reuses
    // an attempt finishing at 10:00:02.
    fixture.original("alloc-01")["deadline_utc"] = json!("2026-09-07T10:00:01Z");
    fixture.freeze();
    fixture.refused(&["reference window differs from the preserved allocation"]);
}

#[test]
fn closeout_failed_outcome_cannot_be_relabelled_pass() {
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    // Same actor, instants and count, but the preserved native result did not complete.
    let row = fixture.allocation("alloc-01")["attempts"][1].clone();
    let native = &mut fixture.closeout("alloc-01")["native"];
    native["role"] = row["attempt_id"].clone();
    native["started_at_utc"] = row["started_at_utc"].clone();
    native["finished_at_utc"] = row["finished_at_utc"].clone();
    native["workflow_outcome"] = json!("COULD_NOT_RUN");
    fixture.closeout("alloc-01")["preserved"]["launch"] = row["launch"].clone();
    fixture.closeout("alloc-01")["preserved"]["completion"] = row["completion"].clone();
    fixture.closeout("alloc-01")["preserved"]["transcript"] = fixture.raw.clone();
    fixture.freeze();
    fixture.refused(&["preserved closeout records the attempt as not passed"]);
    // The attempt's own completion record contradicts a PASS label.
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    let failed = fixture.ws.put(
        "alloc-01-grounded_creation-completion.json",
        &json!({"returncode": -15, "timed_out": false, "finished_at_utc": "2026-09-07T10:00:02Z"}),
    );
    fixture.allocation("alloc-01")["attempts"][1]["completion"] = failed;
    fixture.freeze();
    fixture.refused(&["completion record contradicts the PASS outcome"]);
    // A launch record for a different slot or instant.
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    let other = fixture.allocation("alloc-01")["attempts"][0]["launch"].clone();
    fixture.allocation("alloc-01")["attempts"][1]["launch"] = other;
    fixture.freeze();
    fixture.refused(&["launch record differs from the attempt"]);
}

#[test]
fn historical_expiry_closeout_format_is_supported_without_rewriting() {
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-01", &["grounded_creation", "reuse"], "expired");
    fixture.results["native_turns"] = json!(4);
    fixture.freeze();
    let before = fs::read(pin_path(&fixture.allocation("alloc-01")["closeout"])).unwrap();
    let result = fixture.installed();
    assert_eq!(
        fixture.ws.inventory()["manual_expert_adoption"]["reused_observations"],
        2
    );
    assert_eq!(result["acceptance_status"], "LOCAL_ACCEPTANCE_SET_PASS");
    // The historical bytes and raw status are untouched; normalization lives in the reference.
    let after = fs::read(pin_path(&fixture.allocation("alloc-01")["closeout"])).unwrap();
    assert_eq!(after, before);
    assert!(String::from_utf8_lossy(&after).contains("EXPIRED_WITH_PARTIAL_OBSERVATIONS"));
    // A ledger attempt the closeout recorded as unlaunched, or a slot the allocation never had.
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-01", &["grounded_creation"], "expired");
    fixture.closeout("alloc-01")["slot_states"][1]["launched"] = json!(false);
    fixture.closeout("alloc-01")["slot_states"][1]["completion"] = Value::Null;
    fixture.freeze();
    fixture.refused(&["closeout slot state differs from the attempt"]);
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-01", &["grounded_creation"], "expired");
    fixture.allocation("alloc-01")["attempts"][1]["attempt_id"] = json!("alloc-01-elsewhere");
    fixture.observations.get_mut("grounded_creation").unwrap()["attempt_id"] =
        json!("alloc-01-elsewhere");
    fixture.freeze();
    fixture.refused(&["not a slot of the preserved allocation"]);
}

#[test]
fn historical_evaluator_return_closeout_format_is_supported() {
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-b", &["missing_evidence_refusal"], "evaluator-return");
    fixture.results["native_turns"] = json!(5);
    fixture.freeze();
    fixture.installed();
    let adoption = fixture.ws.inventory()["manual_expert_adoption"].clone();
    assert_eq!(adoption["reused_allocations"], json!(["alloc-b"]));
    // The text approval is bound through the preserved allocation record; another pin is refused.
    let raw = fixture.raw.clone();
    fixture.entry("alloc-b").bind_text_authorization = false;
    fixture.allocation("alloc-b")["authorization"] = raw;
    fixture.materialize();
    fixture.refused(&["text authorization must be pinned through the preserved allocation record"]);
    // A closeout written for another allocation, or naming a different actor.
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-b", &["missing_evidence_refusal"], "evaluator-return");
    fixture.closeout("alloc-b")["native_actor"] = json!("someone-else");
    fixture.freeze();
    fixture.refused(&["closeout native record differs from the attempt"]);
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-b", &["missing_evidence_refusal"], "evaluator-return");
    fixture.freeze();
    let elsewhere = fixture
        .ws
        .put("elsewhere-allocation.json", &json!({"other": true}));
    fixture.entry("alloc-b").closeout_binds_original = false;
    fixture.closeout("alloc-b")["allocation"] = elsewhere;
    fixture.materialize();
    fixture.refused(&["closeout is for a different allocation"]);
}

#[test]
fn preserved_allocation_without_linkage_or_unknown_closeout_is_refused() {
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-b", &["missing_evidence_refusal"], "evaluator-return");
    fixture.entry("alloc-b").prior = None;
    fixture.freeze();
    // freeze() then puts acceptance_set directly; drop it to expose the missing linkage.
    fixture
        .original("alloc-b")
        .as_object_mut()
        .unwrap()
        .remove("acceptance_set");
    fixture.materialize();
    fixture.refused(&["preserved allocation records no acceptance set"]);
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.closeout("alloc-01")["status"] = json!("SOMETHING_NEW");
    fixture.freeze();
    fixture.refused(&["unsupported preserved closeout format"]);
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.closeout("alloc-01")["clock_restarted"] = json!(true);
    fixture.freeze();
    fixture.refused(&["preserved closeout reports a restarted clock"]);
}

#[test]
fn reuse_results_must_declare_every_listed_allocation_and_count() {
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.results["reused_observations"] = json!(2);
    fixture.freeze();
    fixture.refused(&["reused observation count differs"]);
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.reuse("alloc-02", &[]);
    fixture.freeze();
    fixture.refused(&["supplied no reused observation"]);
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.freeze();
    fixture.results["allocations"] = json!([]);
    fixture.publish();
    fixture.refused(&["unlisted allocation"]);
}

#[test]
fn stopped_closeout_with_attempts_requires_native_evidence() {
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.closeout("alloc-01")["native"] = json!({});
    fixture.freeze();
    fixture.refused(&["stopped closeout has attempts but no native evidence"]);
}

// Local evidence preflight (2026-09-10): the recorded multi-attempt stopped closeout,
// its approval linkage, and a check-only command built on the installer's own readers.

fn ledger_index(fixture: &mut Local, id: &str, attempt: &str) -> usize {
    fixture.allocation(id)["attempts"]
        .as_array()
        .unwrap()
        .iter()
        .position(|a| a["attempt_id"] == attempt)
        .unwrap()
}

fn native_entry<'a>(closeout: &'a mut Value, role: &str) -> &'a mut Value {
    closeout["native"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .find(|e| e["role"] == role)
        .unwrap()
}

#[test]
fn multi_attempt_stopped_closeout_is_supported_without_rewriting() {
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation", "reuse"], "stopped-multi");
    fixture.results["native_turns"] = json!(4);
    fixture.freeze();
    let closeout_path = pin_path(&fixture.allocation("alloc-m")["closeout"]);
    let before = fs::read(&closeout_path).unwrap();
    let result = fixture.installed();
    assert_eq!(result["acceptance_status"], "LOCAL_ACCEPTANCE_SET_PASS");
    let adoption = fixture.ws.inventory()["manual_expert_adoption"].clone();
    assert_eq!(adoption["reused_allocations"], json!(["alloc-m"]));
    assert_eq!(adoption["reused_observations"], 2);
    // The preserved closeout keeps its recorded array shape, count and facts.
    assert_eq!(fs::read(&closeout_path).unwrap(), before);
    let closeout: Value = serde_json::from_slice(&before).unwrap();
    assert_eq!(closeout["native"].as_array().unwrap().len(), 4);
    assert_eq!(closeout["attempts_used"], 4);
    assert!(closeout.get("preserved").is_none());
    let ledger = fixture.allocation("alloc-m")["attempts"].clone();
    let outcomes: Vec<&str> = ledger
        .as_array()
        .unwrap()
        .iter()
        .map(|a| a["outcome"].as_str().unwrap())
        .collect();
    assert!(outcomes.contains(&"COULD_NOT_RUN") && outcomes.contains(&"NOT_EVALUATED"));
    // Every previously accepted closeout shape still installs alongside it.
    fixture.reuse_format("alloc-s", &["bounded_enhancement"], "stopped");
    fixture.reuse_format("alloc-x", &["missing_evidence_refusal"], "expired");
    fixture.reuse_format("alloc-b", &["creator_to_evaluator"], "evaluator-return");
    fixture.results["native_turns"] = json!(1);
    fixture.freeze();
    fixture.installed();
}

#[test]
fn multi_attempt_closeout_without_workflow_judgment_cannot_supply_pass() {
    // The unjudged attempt completed with exit 0; a PASS label for it is refused.
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.results["native_turns"] = json!(5);
    let index = ledger_index(&mut fixture, "alloc-m", "alloc-m-a1");
    fixture.allocation("alloc-m")["attempts"][index]["outcome"] = json!("PASS");
    fixture.freeze();
    fixture.refused(&["no workflow judgment"]);
    // Removing the recorded judgment from the reused passing attempt refuses the same way.
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.results["native_turns"] = json!(5);
    native_entry(fixture.closeout("alloc-m"), "alloc-m-grounded_creation")
        .as_object_mut()
        .unwrap()
        .remove("workflow_outcome");
    fixture.freeze();
    fixture.refused(&["no workflow judgment"]);
}

#[test]
fn multi_attempt_closeout_must_account_for_every_attempt_exactly_once() {
    // An entry dropped while its count and ledger row remain.
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.results["native_turns"] = json!(5);
    fixture.closeout("alloc-m")["native"]
        .as_array_mut()
        .unwrap()
        .remove(0);
    fixture.freeze();
    fixture.refused(&["native records differ from its attempt count"]);
    // The entry and count dropped together still disagree with the complete ledger.
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.results["native_turns"] = json!(5);
    fixture.closeout("alloc-m")["native"]
        .as_array_mut()
        .unwrap()
        .remove(0);
    fixture.closeout("alloc-m")["attempts_used"] = json!(2);
    fixture.freeze();
    fixture.refused(&["closeout differs from its ledger"]);
    // Two entries for one attempt.
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.results["native_turns"] = json!(5);
    let first = fixture.closeout("alloc-m")["native"][0]["role"].clone();
    fixture.closeout("alloc-m")["native"][1]["role"] = first;
    fixture.freeze();
    fixture.refused(&["duplicate closeout native record"]);
    // An entry re-pointed at another attempt's launch names that attempt twice and
    // leaves its own ledger row unaccounted for.
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.results["native_turns"] = json!(5);
    let first_launch = fixture.closeout("alloc-m")["native"][0]["launch"].clone();
    fixture.closeout("alloc-m")["native"][1]["role"] = json!("alloc-m-elsewhere");
    fixture.closeout("alloc-m")["native"][1]["launch"] = first_launch;
    fixture.freeze();
    fixture.refused(&[
        "must name the attempt exactly once",
        "closeout native record differs from the attempt",
    ]);
    // A ledger row omitted while the closeout still records the attempt.
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.results["native_turns"] = json!(5);
    fixture.allocation("alloc-m")["attempts"]
        .as_array_mut()
        .unwrap()
        .remove(0);
    fixture.freeze();
    fixture.refused(&["closeout differs from its ledger"]);
}

#[test]
fn multi_attempt_closeout_contradictions_are_refused() {
    // Inline completion facts that differ from the pinned completion record.
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.results["native_turns"] = json!(5);
    native_entry(fixture.closeout("alloc-m"), "alloc-m-grounded_creation")["native_completion"]["returncode"] =
        json!(1);
    fixture.freeze();
    fixture.refused(&["closeout native record differs from the attempt"]);
    // A different actor for the same attempt.
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.results["native_turns"] = json!(5);
    native_entry(fixture.closeout("alloc-m"), "alloc-m-grounded_creation")["actor"] =
        json!("someone-else");
    fixture.freeze();
    fixture.refused(&["closeout native record differs from the attempt"]);
    // A recorded failure relabelled PASS in the ledger, even with a clean process exit.
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.results["native_turns"] = json!(5);
    let clean = fixture.ws.put(
        "alloc-m-a0-completion.json",
        &json!({"returncode": 0, "timed_out": false, "finished_at_utc": "2026-09-07T10:00:00.5Z"}),
    );
    let index = ledger_index(&mut fixture, "alloc-m", "alloc-m-a0");
    fixture.allocation("alloc-m")["attempts"][index]["completion"] = clean.clone();
    fixture.allocation("alloc-m")["attempts"][index]["outcome"] = json!("PASS");
    let entry = native_entry(fixture.closeout("alloc-m"), "alloc-m-a0");
    entry["completion"] = clean;
    entry["native_completion"]["returncode"] = json!(0);
    fixture.freeze();
    fixture.refused(&["preserved closeout records the attempt as not passed"]);
    // A closeout written for another allocation.
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.results["native_turns"] = json!(5);
    fixture.freeze();
    let elsewhere = fixture
        .ws
        .put("elsewhere-allocation.json", &json!({"other": true}));
    fixture.entry("alloc-m").closeout_binds_original = false;
    fixture.closeout("alloc-m")["allocation"] = elsewhere;
    fixture.materialize();
    fixture.refused(&["closeout is for a different allocation"]);
}

#[test]
fn preserved_approval_may_be_recorded_as_approval_ref_or_pinned_by_the_closeout() {
    let mut fixture = Local::new();
    fixture.reuse("alloc-01", &["grounded_creation"]);
    fixture.entry("alloc-01").approval_ref = true;
    fixture.results["native_turns"] = json!(5);
    fixture.freeze();
    fixture.installed();
    // A reference approval that differs from the recorded approval_ref.
    let history = fixture.history.clone();
    fixture.allocation("alloc-01")["authorization"] = history.clone();
    fixture.materialize();
    fixture.refused(&["reference authorization differs from the preserved allocation"]);
    // The multi-attempt closeout pins the approval; a different reference pin is refused.
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.results["native_turns"] = json!(5);
    fixture.allocation("alloc-m")["authorization"] = history;
    fixture.freeze();
    fixture.refused(&["reference authorization differs from the preserved closeout"]);
    // Without either linkage the preserved allocation records no authorization.
    let raw = fixture.raw.clone();
    fixture.allocation("alloc-m")["authorization"] = raw;
    fixture.entry("alloc-m").closeout_carries_authorization = false;
    fixture
        .closeout("alloc-m")
        .as_object_mut()
        .unwrap()
        .remove("authorization");
    fixture.materialize();
    fixture.refused(&["preserved allocation records no authorization"]);
}

#[test]
fn preflight_reports_compatible_synthetic_evidence_without_writing() {
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation", "reuse"], "stopped-multi");
    fixture.reuse_format("alloc-x", &["bounded_enhancement"], "expired");
    fixture.results["native_turns"] = json!(3);
    fixture.freeze();
    let packet = fixture.packet();
    let before = snapshot(&fixture.ws.root);
    let result = fixture.ws.preflight(&packet);
    assert_eq!(result.code, 0, "{}", result.text);
    let report = &result.output;
    assert_eq!(report["status"], "COMPATIBLE", "{}", result.text);
    assert_eq!(
        snapshot(&fixture.ws.root),
        before,
        "preflight must not write"
    );
    assert!(!fixture.ws.project.join(".agents").exists());
    for name in [
        "protected_authority",
        "destination_separation",
        "packet_record",
        "package_identities",
        "acceptance_set",
        "allocations",
        "observations",
        "freshness",
    ] {
        assert_eq!(check(report, name)["status"], "PASS", "{name}: {report}");
    }
    assert_eq!(report["blockers"], json!([]));
    assert_eq!(report["behavior"], "NOT_EVALUATED");
    assert_eq!(
        report["protected_identity"]["source_sha256"],
        identity()["source_sha256"]
    );
    let multi = allocation_entry(report, "alloc-m");
    assert_eq!(multi["status"], "COMPATIBLE");
    assert_eq!(multi["closeout_status"], "STOPPED_BLOCKED");
    assert_eq!(multi["attempts"].as_array().unwrap().len(), 4);
    assert_eq!(
        multi["earliest_attempt_started_at_utc"],
        "2026-09-07T10:00:00Z"
    );
    assert_eq!(allocation_entry(report, "alloc-x")["status"], "COMPATIBLE");
    let observations = report["observations"].as_array().unwrap();
    assert_eq!(observations.len(), 3);
    assert!(observations.iter().all(|o| o["status"] == "PASS"));
    // Unperformed obligations are listed, never implied by the compatible result.
    let pending = serde_json::to_string(&report["pending"]).unwrap();
    for needle in [
        "independent",
        "owner acceptance",
        "results",
        "installation destination",
        "native observation for missing_evidence_refusal: NOT_PRESENT",
        "native observation for creator_to_evaluator: NOT_PRESENT",
    ] {
        assert!(pending.contains(needle), "{needle}: {pending}");
    }
    assert!(
        report["meaning"]
            .as_str()
            .unwrap()
            .contains("not acceptance")
    );
    // The same bytes still install through the production path.
    fixture.installed();
}

#[test]
fn preflight_reports_unchanged_freeze_rule_with_first_attempt_time() {
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.reuse_format("alloc-x", &["reuse"], "expired");
    fixture.results["native_turns"] = json!(4);
    // Move alloc-m's window to open before the freeze while its first attempt follows it.
    fixture.plan["frozen_at_utc"] = json!("2026-09-07T09:59:59.5Z");
    fixture.allocation("alloc-m")["started_at_utc"] = json!("2026-09-07T09:59:59Z");
    fixture.original("alloc-m")["started_at_utc"] = json!("2026-09-07T09:59:59Z");
    fixture.closeout("alloc-m")["started_at_utc"] = json!("2026-09-07T09:59:59Z");
    fixture.freeze();
    let packet = fixture.packet();
    let before = snapshot(&fixture.ws.root);
    let result = fixture.ws.preflight(&packet);
    assert_eq!(result.code, 2, "{}", result.text);
    let report = &result.output;
    assert_eq!(report["status"], "BLOCKED");
    assert_eq!(
        snapshot(&fixture.ws.root),
        before,
        "preflight must not write"
    );
    let blocked = allocation_entry(report, "alloc-m");
    assert_eq!(blocked["status"], "BLOCKED", "{report}");
    let blockers = serde_json::to_string(&blocked["blockers"]).unwrap();
    assert!(
        blockers.contains("allocation window must follow the frozen acceptance set"),
        "{blockers}"
    );
    // The binding itself completed, so the owner sees the real first-attempt instant.
    assert_eq!(blocked["attempts"].as_array().unwrap().len(), 3);
    assert_eq!(blocked["frozen_at_utc"], "2026-09-07T09:59:59.5Z");
    assert_eq!(blocked["started_at_utc"], "2026-09-07T09:59:59Z");
    assert_eq!(
        blocked["earliest_attempt_started_at_utc"],
        "2026-09-07T10:00:00Z"
    );
    assert!(blocked["rule"].as_str().unwrap().contains("unchanged"));
    assert_eq!(allocation_entry(report, "alloc-x")["status"], "COMPATIBLE");
    let observations = report["observations"].as_array().unwrap();
    let grounded = observations
        .iter()
        .find(|o| o["check"] == "grounded_creation")
        .unwrap();
    assert_eq!(grounded["status"], "NOT_PERFORMED", "{report}");
    let reuse = observations.iter().find(|o| o["check"] == "reuse").unwrap();
    assert_eq!(reuse["status"], "PASS", "{report}");
    assert!(!report["pending"].as_array().unwrap().is_empty());
    // The installer applies the same unchanged rule.
    fixture.refused(&["allocation window must follow the frozen acceptance set"]);
}

#[test]
fn preflight_surfaces_each_blocker_and_marks_dependent_checks_not_performed() {
    // A stale acceptance-set pin blocks the set and everything bound to it.
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.results["native_turns"] = json!(5);
    fixture.freeze();
    let packet = fixture.packet();
    let set_path = pin_path(&fixture.results["acceptance_set"]);
    let set_bytes = fs::read(&set_path).unwrap();
    fs::write(&set_path, b"{}").unwrap();
    let result = fixture.ws.preflight(&packet);
    assert_eq!(result.code, 2, "{}", result.text);
    let report = &result.output;
    assert_eq!(report["status"], "BLOCKED");
    assert_eq!(check(report, "packet_record")["status"], "PASS", "{report}");
    assert_eq!(check(report, "package_identities")["status"], "PASS");
    assert_eq!(check(report, "acceptance_set")["status"], "BLOCKED");
    assert!(
        check(report, "acceptance_set")["reason"]
            .as_str()
            .unwrap()
            .contains("stale evidence")
    );
    assert_eq!(check(report, "allocations")["status"], "NOT_PERFORMED");
    assert_eq!(check(report, "observations")["status"], "NOT_PERFORMED");
    fs::write(&set_path, set_bytes).unwrap();
    // A framework inside the project is an incompatible destination; the rest still runs.
    let nested = fixture.ws.project.join("framework-copy");
    for (relative, bytes) in snapshot(&fixture.ws.framework) {
        write(&nested.join(relative), &bytes);
    }
    let result = fixture.ws.preflight_with(&nested, &packet);
    assert_eq!(result.code, 2, "{}", result.text);
    let report = &result.output;
    assert_eq!(check(report, "destination_separation")["status"], "BLOCKED");
    assert!(
        check(report, "destination_separation")["reason"]
            .as_str()
            .unwrap()
            .contains("separate")
    );
    assert_eq!(check(report, "packet_record")["status"], "PASS", "{report}");
    assert_eq!(
        check(report, "acceptance_set")["status"],
        "PASS",
        "{report}"
    );
    assert_eq!(check(report, "package_identities")["status"], "BLOCKED");
    assert_eq!(check(report, "allocations")["status"], "NOT_PERFORMED");
    fs::remove_dir_all(&nested).unwrap();
    // A packet missing a required field blocks before any selection is read.
    let mut record = fixture.packet_record();
    record.as_object_mut().unwrap().remove("observations");
    let partial = pin_path(&fixture.ws.put("partial-packet.json", &record));
    let result = fixture.ws.preflight(&partial);
    assert_eq!(result.code, 2, "{}", result.text);
    let report = &result.output;
    assert_eq!(check(report, "packet_record")["status"], "BLOCKED");
    for name in [
        "package_identities",
        "acceptance_set",
        "allocations",
        "observations",
        "freshness",
    ] {
        assert_eq!(
            check(report, name)["status"],
            "NOT_PERFORMED",
            "{name}: {report}"
        );
    }
    assert!(!report["pending"].as_array().unwrap().is_empty());
    // An omitted ledger attempt blocks only its allocation; a second allocation stays
    // compatible and the observation bound to the blocked one is not performed.
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.reuse_format("alloc-x", &["reuse"], "expired");
    fixture.results["native_turns"] = json!(4);
    fixture.allocation("alloc-m")["attempts"]
        .as_array_mut()
        .unwrap()
        .remove(0);
    fixture.freeze();
    let packet = fixture.packet();
    let result = fixture.ws.preflight(&packet);
    assert_eq!(result.code, 2, "{}", result.text);
    let report = &result.output;
    let blocked = allocation_entry(report, "alloc-m");
    assert_eq!(blocked["status"], "BLOCKED");
    assert!(
        serde_json::to_string(&blocked["blockers"])
            .unwrap()
            .contains("closeout differs from its ledger")
    );
    assert_eq!(allocation_entry(report, "alloc-x")["status"], "COMPATIBLE");
    let observations = report["observations"].as_array().unwrap();
    assert_eq!(
        observations
            .iter()
            .find(|o| o["check"] == "grounded_creation")
            .unwrap()["status"],
        "NOT_PERFORMED"
    );
    assert_eq!(
        observations.iter().find(|o| o["check"] == "reuse").unwrap()["status"],
        "PASS"
    );
    // A duplicate ledger attempt and a contradicted outcome are each reported.
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.results["native_turns"] = json!(5);
    let row = fixture.allocation("alloc-m")["attempts"][0].clone();
    fixture.allocation("alloc-m")["attempts"]
        .as_array_mut()
        .unwrap()
        .push(row);
    fixture.closeout("alloc-m")["attempts_used"] = json!(5);
    fixture.freeze();
    let result = fixture.ws.preflight(&fixture.packet());
    assert_eq!(result.code, 2, "{}", result.text);
    let text = serde_json::to_string(&result.output["blockers"]).unwrap();
    assert!(
        text.contains("duplicate allocation attempt")
            || text.contains("native records differ from its attempt count"),
        "{text}"
    );
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.results["native_turns"] = json!(5);
    let index = ledger_index(&mut fixture, "alloc-m", "alloc-m-a0");
    fixture.allocation("alloc-m")["attempts"][index]["outcome"] = json!("PASS");
    fixture.freeze();
    let result = fixture.ws.preflight(&fixture.packet());
    assert_eq!(result.code, 2, "{}", result.text);
    assert!(
        serde_json::to_string(&result.output["blockers"])
            .unwrap()
            .contains("completion record contradicts the PASS outcome"),
        "{}",
        result.text
    );
}

#[test]
fn preflight_observation_needs_a_recorded_passing_attempt() {
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.results["native_turns"] = json!(5);
    fixture.freeze();
    // An observation claiming PASS for the attempt nobody judged.
    let mut invented = fixture.observations["missing_evidence_refusal"].clone();
    invented["schema_version"] = json!("devforge.manual-local-observation/v2");
    invented["acceptance_set"] = fixture.results["acceptance_set"].clone();
    invented["allocation"] = fixture.entry("alloc-m").pin.clone();
    invented["attempt_id"] = json!("alloc-m-a1");
    invented["started_at_utc"] = json!("2026-09-07T10:00:00.6Z");
    invented["finished_at_utc"] = json!("2026-09-07T10:00:00.9Z");
    let invented = fixture.ws.put("invented-observation.json", &invented);
    let mut record = fixture.packet_record();
    record["observations"]
        .as_array_mut()
        .unwrap()
        .push(json!({"check": "missing_evidence_refusal", "observation": invented}));
    // A current-interval (v1) observation has no interval to check in a preflight.
    record["observations"].as_array_mut().unwrap().push(
        json!({"check": "bounded_enhancement",
            "observation": fixture.results["checks"]["bounded_enhancement"]["native_observation"]}),
    );
    let packet = pin_path(&fixture.ws.put("invented-packet.json", &record));
    let result = fixture.ws.preflight(&packet);
    assert_eq!(result.code, 2, "{}", result.text);
    let report = &result.output;
    let observations = report["observations"].as_array().unwrap();
    let judged = observations
        .iter()
        .find(|o| o["check"] == "grounded_creation")
        .unwrap();
    assert_eq!(judged["status"], "PASS", "{report}");
    let invented = observations
        .iter()
        .find(|o| o["check"] == "missing_evidence_refusal")
        .unwrap();
    assert_eq!(invented["status"], "BLOCKED");
    assert!(
        invented["reason"]
            .as_str()
            .unwrap()
            .contains("reused attempt did not pass"),
        "{report}"
    );
    let current = observations
        .iter()
        .find(|o| o["check"] == "bounded_enhancement")
        .unwrap();
    assert_eq!(current["status"], "BLOCKED");
    assert!(
        current["reason"]
            .as_str()
            .unwrap()
            .contains("results interval"),
        "{report}"
    );
}

#[test]
fn preflight_requires_the_pinned_authority_and_never_installs() {
    let mut fixture = Local::new();
    fixture.reuse_format("alloc-m", &["grounded_creation"], "stopped-multi");
    fixture.results["native_turns"] = json!(5);
    fixture.freeze();
    let packet = fixture.packet();
    let id = identity();
    let mut executable = id["executable"].clone();
    executable["sha256"] = json!("0".repeat(64));
    write_authority(
        &fixture.ws.authority,
        &authority_record(&executable, &id["source_sha256"]),
    );
    let before = snapshot(&fixture.ws.root);
    let result = fixture.ws.preflight(&packet);
    assert_eq!(result.code, 2, "{}", result.text);
    assert_eq!(result.output["status"], "BLOCKED");
    let reason = result.output["reason"].as_str().unwrap();
    assert!(reason.contains("protected authority"), "{reason}");
    assert!(reason.contains("executable identity"), "{reason}");
    assert!(result.output.get("checks").is_none(), "{}", result.text);
    assert_eq!(snapshot(&fixture.ws.root), before);
    // A copy of the executable at another path is not the pinned authority either.
    write_authority(
        &fixture.ws.authority,
        &authority_record(&id["executable"], &id["source_sha256"]),
    );
    let copy = fixture.ws.root.join("devforge-copy");
    fs::copy(BIN, &copy).unwrap();
    fs::set_permissions(&copy, fs::Permissions::from_mode(0o755)).unwrap();
    let args = [
        "install",
        "check-local-evidence",
        "--project",
        fixture.ws.project.to_str().unwrap(),
        "--framework",
        fixture.ws.framework.to_str().unwrap(),
        "--packet",
        packet.to_str().unwrap(),
        "--authority",
        fixture.ws.authority.to_str().unwrap(),
    ];
    let result = run(copy.to_str().unwrap(), &args);
    assert_eq!(result.code, 2, "{}", result.text);
    assert!(
        result.output["reason"]
            .as_str()
            .unwrap()
            .contains("executable identity"),
        "{}",
        result.text
    );
    assert!(!fixture.ws.project.join(".agents").exists());
}
