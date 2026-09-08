"""G1 VPI discriminators. Synthetic mechanics only; no qualification claims.

Helper-only entrypoint: VPI_VALIDATOR_PACKAGE=/absolute/package
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover is NOT helper-only.
Use: PYTHONPATH=tests python3 -m unittest test_validation_policy.HelperDiscriminator -v
"""
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_utility_state import Fixture, encoded, digest, NOW, utility, store

ROOT = Path(__file__).resolve().parents[1]
TASKS = [f"T{i:02}" for i in range(1, 13)]


class PolicyFixture(Fixture):
    def __init__(self, root):
        super().__init__(root, "skill-validator")
        self.policy_ref = self.put("policy.txt", "Accepted synthetic VPR-2 policy revision")
        self.acceptance_ref = self.put("acceptance.txt", "Synthetic owner acceptance of selected policy; not real qualification")
        self.scope = self.put("scope.txt", "Existing spelling-only scope")
        env = self.put("environment.json", {"capabilities": ["source-review"], "executable": "unchanged"})
        self.identity = {"candidate": self.put("candidate.json", {"SKILL.md": digest((self.project / "SKILL.md").read_bytes())}), "environment": env}
        self.baseline_identity = {"candidate": self.put("baseline.json", {"SKILL.md": "a" * 64}), "environment": env}
        self.lineage = {"qualified_anchor": {"status": "UNKNOWN", "identity": None, "evidence": None},
                        "accepted_unqualified_baseline": self.baseline_identity,
                        "current_routinely_accepted": self.baseline_identity, "previous_acceptance": None, "acceptance_chain": []}
        previous = self.put("previous-acceptance.json", {"candidate_identity": self.baseline_identity,
                            "accepted_scope_ref": self.scope, "lineage": copy.deepcopy(self.lineage)})
        self.lineage.update(previous_acceptance=previous, acceptance_chain=[previous])
        self.catalog_ref = self.put("original-catalog.json", {f"A{i:02}": {"requirement": f"R{i:02}", "expectation": "Original frozen expectation"} for i in range(1, 13)})
        catalog, assertions, tasks = [], [], []
        for i, task in enumerate(TASKS, 1):
            aid = f"A{i:02}"
            native = 5 <= i <= 8
            tier = {5: "C", 6: "C", 7: "B", 8: "A"}.get(i, "S" if i == 4 else "D")
            selection = "NOT_SELECTED" if native else "REQUIRED"
            catalog.append({"assertion_id": aid, "case_id": f"CASE-{i}", "source_ref": self.catalog_ref,
                            "source_pointer": "/" + aid, "variant": "normal", "arm": "candidate", "repetition": 1,
                            "requirement_ids": [f"R{i:02}"], "evidence_kinds": ["N" if native else tier]})
            assertions.append({"assertion_id": aid, "task_id": task, "tier": tier, "selection": selection,
                               "rule_ids": ["CI-01"], "reason": "Reviewed spelling-only impact", "expectation": "pass",
                               "dependency_ids": [], "observation_ids": []})
            tasks.append({"task_id": task, "classification": "Enforced", "selection": selection, "assertion_ids": [aid], "reason": "Reviewed spelling-only impact"})
        diff = self.put("diff.json", {"requirements": ["R01"], "change": "spelling"})
        self.vp = {"version": "VPR-2", "policy_ref": self.policy_ref, "acceptance_ref": self.acceptance_ref,
                   "mode": "Routine", "requested_claim": {"kind": "scoped_update", "text": "Spelling update in accepted scope", "requires_full": False, "contract_ref": None},
                   "accepted_scope_ref": self.scope, "baseline_identity": self.baseline_identity, "candidate_identity": self.identity,
                   "lineage": self.lineage, "impact": {"immediate_diff": diff, "cumulative_diff": diff,
                   "immediate_requirements": ["R01"], "cumulative_requirements": ["R01"], "dependency_closure": ["R01"],
                   "matched_rules": ["CI-01"], "bounded": True, "full_triggers": []},
                   "compatibility": [{"id": f"CP-{i:02}", "disposition": "UNCHANGED", "old_environment": env,
                     "new_environment": env, "used_capabilities": ["source-review"], "affected_assertions": [], "evidence": [env], "reason": "Exact environment identity unchanged"} for i in range(1, 5)],
                   "catalog_refs": [self.catalog_ref], "catalog_assertions": catalog, "assertions": assertions,
                   "task_selection": tasks, "observations": [], "call_graph": [{"call_id": "T04-review", "kind": "static_review", "parent_call_id": None, "attempt_id": None,
                   "assertion_ids": ["A04"], "observation_ids": [], "depends_on": [], "producer": "independent-reviewer",
                   "reviewer": None, "review_path": None, "interaction": "single-turn", "managed_worker_required": False, "max_seconds": 60}],
                   "selection_reviewer": "independent-reviewer"}
        self.plan_path = self.owner / "plan.json"
        self.review_path = self.owner / "review.json"
        self.plan = {"schema_version": "devforge.skill-validation-plan/v2", "run_id": "UTILITY-001", "provider": "codex",
                     "candidate_root": str(self.project), "input_refs": [{"kind": k, **self.catalog_ref} for k in ("specification", "rubric", "cases", "framework_contract")],
                     "baseline": {"kind": "old_skill", "source_snapshot": self.baseline_identity["candidate"], "reason": "Accepted baseline"},
                     "assignment": {"owner": "external-owner", "execution_ref": None, "authorization_source": "External synthetic allocation",
                       "allowed_output_root": str(self.project), "protected_roots": [str(self.owner)], "missing_inputs": []},
                     "runtime": None, "budget": None, "checks": [{"id": a["assertion_id"], "group": a["tier"], "expectation": a["expectation"], "description": "Original requirement", "requirement_ids": [f"R{i:02}"]} for i, a in enumerate(assertions, 1)],
                     "findings_from_previous_iteration": [], "criteria_freeze_record": "Frozen before review", "scope_exclusions": [], "plan_scope": "Synthetic Routine", "validation_policy": self.vp}
        self.delivery["schema_version"] = "devforge.utility-delivery/v2"
        for gate in self.delivery["gate_inputs"]:
            if gate["id"] == "independent-review":
                gate["producer"] = "independent-reviewer"
        self.freeze()

    def put(self, name, value):
        path = self.owner / name
        path.write_bytes(value.encode() if isinstance(value, str) else encoded(value))
        return self.pin(path)

    def freeze(self):
        self.plan_path.write_bytes(encoded(self.plan))
        self.delivery["validation_policy"] = {"version": "VPR-2", "policy_ref": self.policy_ref,
                                              "acceptance_ref": self.acceptance_ref, "plan": self.pin(self.plan_path)}
        assignment = self.owner / "assignment.md"
        assignment.write_bytes(encoded({"owner": "external-owner", "author": "candidate-author", "authorization": {
            "validation_policy": self.delivery["validation_policy"], "selection_reviewer": "independent-reviewer"}}))
        self.session["assignment"] = self.pin(assignment)
        self.session["deadline_utc"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.write_contracts()
        raw_review = self.put("raw-review.txt", "Synthetic independent review of original inventory and R01-R10; no actual model run")
        self.review = {"schema_version": "devforge.skill-ai-review/v2", "run_id": "UTILITY-001", "candidate_ref": self.identity["candidate"],
                       "reviewer": {"identity": "independent-reviewer", "model": None, "runtime": "synthetic fixture", "independence_evidence": "Separately selected producer", "limits": ["Synthetic"]},
                       "input_refs": [{"kind": "delivery", **self.pin(self.delivery_path)}],
                       "criteria": [{"id": f"R{i:02}", "outcome": "PASS", "reason": "Synthetic criterion observation", "evidence": [raw_review], "finding_ids": [], "applicability": "Required"} for i in range(1, 11)],
                       "disagreements": [], "additional_reviewer_refs": [], "overall": "PASS", "plan": self.pin(self.plan_path),
                       "selection_review": {"outcome": "PASS", "reason": "Synthetic reviewed selection", "evidence": [raw_review],
                         "reviewed_assertion_ids": [a["assertion_id"] for a in self.vp["assertions"]], "reviewed_rule_ids": ["CI-01", "CP-01", "CP-02", "CP-03", "CP-04"], "invariant_ids": [f"R{i:02}" for i in range(1, 11)]}}
        self.review_path.write_bytes(encoded(self.review))
        for gate in self.delivery["gate_inputs"]:
            native = gate["id"].startswith("native-")
            refs = [self.pin(self.plan_path), self.pin(self.review_path)] if native else [self.pin(self.review_path)] if gate["id"] == "independent-review" else [self.catalog_ref]
            Path(gate["path"]).write_bytes(encoded({"schema_version": "devforge.utility-gate-input/v2", "task_id": "UTILITY-001", "phase": gate["phase"], "producer": gate["producer"],
                "inputs_sha256": self.session["delivery_contract_sha256"], "outcome": "NOT_RUN" if native else "PASS", "reason": "Synthetic reviewed no-native disposition", "evidence": refs,
                "gate_id": gate["id"], "selection": "NOT_SELECTED" if native else "REQUIRED", "disposition": "SATISFIED_BY_REVIEWED_SELECTION" if native else "SATISFIED",
                "validation_plan_sha256": self.pin(self.plan_path)["sha256"], "review_sha256": self.pin(self.review_path)["sha256"] if native else None}))

    def results(self):
        proof = self.put("mechanical-proof.txt", "Actual synthetic mechanical fixture output")
        return {"schema_version": "devforge.skill-validation-results/v2", "run_id": "UTILITY-001", "plan": self.pin(self.plan_path),
                "structural_report": proof, "ai_review": self.pin(self.review_path), "results": [], "findings": [],
                "task_results": [{**{k: t[k] for k in ("task_id", "classification", "selection")}, "disposition": "SATISFIED_BY_REVIEWED_SELECTION" if t["selection"] == "NOT_SELECTED" else "SATISFIED", "outcome": "NOT_RUN" if t["selection"] == "NOT_SELECTED" else "PASS", "reason": "Synthetic mechanical reporting", "evidence": [proof]} for t in self.vp["task_selection"]],
                "assertion_results": [{"assertion_id": a["assertion_id"], "selection": a["selection"], "outcome": "NOT_RUN", "integrity": "NOT_OBSERVED", "observation_refs": [], "grade_refs": [], "reason": "Unattempted; no invented evidence"} for a in self.vp["assertions"]],
                "report_completion": "COMPLETE", "validation_disposition": "INSUFFICIENT_EVIDENCE", "routine_adoption_eligible": False,
                "lineage": copy.deepcopy(self.lineage), "owner_acceptance_ref": None,
                "receiving_transfer": {"selection": "NOT_SELECTED", "outcome": "NOT_RUN", "target_output": None, "receiver_contract": None, "receiver_observation": None, "completed_action": None, "observed_at_utc": None, "reason": "No selected transfer"}}


class RuntimePolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = PolicyFixture(Path(self.tmp.name))
        self.clock = mock.patch.object(store, "utc_now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_routine_six_phases_receipt_preserves_native_not_run(self):
        self.assertEqual(self.f.all_ready()["status"], "READY")
        result = utility.complete(self.f.state)
        self.assertEqual(result["status"], "COMPLETED", result)
        receipt = json.loads(self.f.receipt.read_bytes())
        self.assertEqual(receipt["schema_version"], "devforge.utility-receipt/v1")
        self.assertEqual([receipt["gate_outcomes"]["native-" + x] for x in ("C", "B", "A")], ["NOT_RUN"] * 3)

    def test_cli_v2_admission_is_real_consumer(self):
        run = subprocess.run([str(ROOT / "target/debug/devforge"), "delivery", "--state", str(self.f.state), "init", "--contract", str(self.f.session_path)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(json.loads(run.stdout)["status"], "ACTIVE")

    def test_reject_bad_authority_versions_coverage_and_cumulative_impact_without_state(self):
        changes = [lambda f: f.vp.update(version="VPR-99"), lambda f: f.vp.update(selection_reviewer="candidate-author"),
                   lambda f: f.vp["assertions"].pop(), lambda f: f.vp["catalog_assertions"].append(f.vp["catalog_assertions"][0]),
                   lambda f: f.vp["impact"].update(dependency_closure=["R99"]), lambda f: f.vp["impact"].update(full_triggers=["CONTROL_AUTHORITY_CHANGE"]),
                   lambda f: f.vp["requested_claim"].update(kind="qualification"), lambda f: f.vp["compatibility"][0].update(disposition="UNRESOLVED"),
                   lambda f: f.vp["call_graph"].clear(), lambda f: f.vp["catalog_assertions"][0].update(source_pointer="/missing"),
                   lambda f: f.vp["task_selection"][3].update(selection="NOT_SELECTED"), lambda f: f.vp["lineage"].update(current_routinely_accepted=f.identity)]
        for change in changes:
            with self.subTest(change=changes.index(change)), tempfile.TemporaryDirectory() as tmp:
                f = PolicyFixture(Path(tmp)); change(f); f.freeze()
                self.assertEqual(f.start()["status"], "FAIL")
                self.assertFalse(f.state.exists())

    def test_stale_acceptance_or_worker_plan_is_denied_atomically(self):
        Path(self.f.acceptance_ref["path"]).write_text("Changed owner acceptance")
        self.assertEqual(self.f.start()["status"], "FAIL")
        self.assertFalse(self.f.state.exists())

    def test_review_failure_cannot_be_typed_away_and_no_phase_acceptance(self):
        self.assertEqual(self.f.start()["status"], "ACTIVE")
        for _ in range(2):
            self.f.checkpoint(); self.assertEqual(utility.advance(self.f.state)["status"], "PROGRESS")
        self.f.review["reviewer"]["identity"] = "candidate-author"
        self.f.review_path.write_bytes(encoded(self.f.review))
        gate = self.f.owner / "independent-review.json"
        value = json.loads(gate.read_bytes()); value["evidence"] = [self.f.pin(self.f.review_path)]; gate.write_bytes(encoded(value))
        self.f.checkpoint()
        result = utility.advance(self.f.state)
        self.assertEqual(result["status"], "FAIL", result)
        self.assertEqual(utility.context(self.f.state)["phase"], "P3")
        self.assertFalse(self.f.receipt.exists())

    def test_unselected_native_cannot_claim_pass(self):
        self.assertEqual(self.f.start()["status"], "ACTIVE")
        for _ in range(3):
            self.f.checkpoint(); self.assertEqual(utility.advance(self.f.state)["status"], "PROGRESS")
        gate = self.f.owner / "native-C.json"
        value = json.loads(gate.read_bytes()); value.update(outcome="PASS", disposition="SATISFIED"); gate.write_bytes(encoded(value))
        self.f.checkpoint()
        self.assertEqual(utility.advance(self.f.state)["status"], "FAIL")
        self.assertEqual(utility.context(self.f.state)["phase"], "P4")

    def test_claimed_result_summary_is_reduced_before_publication(self):
        self.assertEqual(self.f.start()["status"], "ACTIVE")
        for _ in range(5):
            self.f.checkpoint(); self.assertEqual(utility.advance(self.f.state)["status"], "PROGRESS")
        # Existing structured consumer must not admit arbitrary v2 summary text.
        spec = next(s for s in self.f.delivery["outputs"] if s["phase"] == "P6")
        record = self.f.results(); record.update(validation_disposition="FULL_PASS", routine_adoption_eligible=True)
        from unittest.mock import patch
        with patch.dict(spec, schema_version="devforge.skill-validation-results/v2", required_fields=[]):
            with self.assertRaises(Exception):
                utility._structured(encoded(record), spec)


class HelperDiscriminator(unittest.TestCase):
    def test_honest_unattempted_v2_emits_typed_decision(self):
        package = os.environ.get("VPI_VALIDATOR_PACKAGE")
        self.assertTrue(package, "Set VPI_VALIDATOR_PACKAGE to the selected immutable skill package")
        helper = Path(package) / "scripts/assess_evidence.py"
        self.assertTrue(helper.is_file(), "Selected helper must exist; setup absence is not RED")
        with tempfile.TemporaryDirectory() as tmp:
            f = PolicyFixture(Path(tmp)); results = f.owner / "results.json"; results.write_bytes(encoded(f.results()))
            output = f.owner / "decision.json"
            run = subprocess.run([sys.executable, str(helper), "--plan", str(f.plan_path), "--results", str(results), "--output", str(output)],
                                 capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertTrue(output.exists(), "Honest unattempted v2 must emit a typed decision, not reject the supported schema: " + run.stdout + run.stderr)
            decision = json.loads(output.read_bytes())
            self.assertEqual(decision["schema_version"], "devforge.skill-validation-decision/v2")
            self.assertEqual(decision["overall"], "NOT_RUN")
            self.assertEqual(decision["validation_disposition"], "INSUFFICIENT_EVIDENCE")
            self.assertEqual(decision["report_completion"], "COMPLETE")
            self.assertFalse(decision["routine_adoption_eligible"])
            self.assertFalse(decision["coverage_complete"])
            self.assertEqual(decision["external_acceptance"], "NOT_GRANTED")
            self.assertEqual([decision["groups"][tier] for tier in ("C", "B", "A")], ["NOT_RUN"] * 3)
            self.assertEqual(decision["lineage"], f.lineage)


if __name__ == "__main__":
    unittest.main()
