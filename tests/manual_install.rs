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
struct Local {
    ws: Workspace,
    raw: Value,
    history: Value,
    packages: Vec<Value>,
    plan: Value,
    observations: BTreeMap<String, Value>,
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
            results,
            review,
            evidence_path: PathBuf::new(),
        };
        fixture.freeze();
        fixture
    }

    fn freeze(&mut self) {
        let plan = self.ws.put("set.json", &self.plan);
        let mut rows = Map::new();
        for (key, kind) in CHECKS {
            let mut observation = Value::Null;
            if kind == "N" {
                let entry = self.observations.get_mut(key).unwrap();
                entry["acceptance_set"] = plan.clone();
                observation = self.ws.put(&format!("{key}.json"), entry);
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
