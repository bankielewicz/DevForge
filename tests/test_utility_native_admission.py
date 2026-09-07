"""Frozen native-plan admission; all files are synthetic, no client is executed."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime" / "delivery"))
from test_utility_state import Fixture, NOW, encoded, digest
import utility_state as utility
import utility_evidence
import phase_state as store
import delivery_core as core


class NativePlanFixture:
    def __init__(self, root):
        self.owner = root / "evidence"
        self.owner.mkdir()
        self.workspace = root / "candidate-workspace"
        self.private = root / "candidate-client-state"
        self.workspace.mkdir()
        self.private.mkdir()
        self.plan_path = self.owner / "plan.json"
        refs = {}
        for name in ("candidate", "baseline", "specification", "cases", "runtime_configuration", "auth", "raw-boundary"):
            path = self.owner / (name + ".json")
            path.write_bytes(encoded({"kind": name, "scope": "Synthetic evidence only"}))
            refs[name] = Fixture.pin(path)
        self.binary = self.owner / "client"
        self.binary.write_bytes(b"Synthetic inert client bytes, never executed")
        self.binary.chmod(0o700)
        boundary = {"schema_version": "devforge.utility-boundary-observation/v1", "task_id": "UTILITY-001",
                    "attempts": [{"attempt_id": "C-01", "workspace": str(self.workspace), "client_state": str(self.private),
                                  "observations": {key: {"outcome": "PASS", "evidence": refs["raw-boundary"]}
                                                   for key in ("filesystem", "source_visibility", "history_memory", "authentication", "process_ownership", "callbacks")}}]}
        self.boundary_path = self.owner / "boundary.json"
        self.boundary_path.write_bytes(encoded(boundary))
        self.plan = {"schema_version": "devforge.utility-native-plan/v1", "task_id": "UTILITY-001",
                     **{key: refs[key] for key in ("candidate", "baseline", "specification", "cases", "runtime_configuration")},
                     "client": {**Fixture.pin(self.binary), "version": "synthetic-0"}, "model": "synthetic-model",
                     "authentication": {"kind": "subscription", "arrangement_ref": refs["auth"], "credential_copying": False},
                     "repetitions": 1, "max_attempts": 1, "max_seconds": 60,
                     "observation_methods": ["Synthetic external boundary evidence for admission test"],
                     "boundary_evidence": Fixture.pin(self.boundary_path),
                     "attempts": [{"attempt_id": "C-01", "case_id": "C-RESOURCES", "tier": "C", "arm": "candidate",
                                   "repetition": 1, "workspace": str(self.workspace), "client_state": str(self.private), "max_seconds": 60}]}
        self.save()

    def save(self):
        self.plan_path.write_bytes(encoded(self.plan))


class NativeAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.f = NativePlanFixture(self.root)
        self.clock = mock.patch.object(store, "utc_now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_complete_frozen_plan_identifies_prepared_independent_workspace_without_execution(self):
        plan, evidence = utility_evidence.native_plan(self.f.plan_path.read_bytes(), "UTILITY-001")
        self.assertEqual(plan["attempts"][0]["workspace"], str(self.f.workspace))
        self.assertTrue(evidence)
        self.assertEqual(list(self.f.workspace.iterdir()), [])
        self.assertEqual(list(self.f.private.iterdir()), [])

    def test_pending_choices_block_test_admission_but_keep_prepared_workspace(self):
        for key in ("model", "authentication", "repetitions", "max_attempts", "max_seconds", "observation_methods", "boundary_evidence"):
            with self.subTest(key=key):
                plan = copy.deepcopy(self.f.plan)
                plan[key] = None
                with self.assertRaises((core._Problem, TypeError)):
                    utility_evidence.native_plan(encoded(plan), "UTILITY-001")
                self.assertTrue(self.f.workspace.is_dir())
                self.assertEqual(list(self.f.workspace.iterdir()), [])

    def test_unobserved_boundary_blocks_even_with_model_auth_budget_fields(self):
        boundary = json.loads(self.f.boundary_path.read_bytes())
        boundary["attempts"][0]["observations"]["history_memory"]["outcome"] = "COULD_NOT_RUN"
        self.f.boundary_path.write_bytes(encoded(boundary))
        self.f.plan["boundary_evidence"] = Fixture.pin(self.f.boundary_path)
        with self.assertRaises(core._Problem):
            utility_evidence.native_plan(encoded(self.f.plan), "UTILITY-001")

    def test_reused_workspace_or_client_state_is_rejected(self):
        for field in ("workspace", "client_state"):
            with self.subTest(field=field):
                plan = copy.deepcopy(self.f.plan)
                plan["max_attempts"] = 2
                other_workspace = self.root / (field + "-second-project")
                other_client = self.root / (field + "-second-client")
                other_workspace.mkdir()
                other_client.mkdir()
                attempt = {**plan["attempts"][0], "attempt_id": "C-02", "case_id": "another-case",
                           "workspace": str(other_workspace), "client_state": str(other_client)}
                attempt[field] = plan["attempts"][0][field]
                plan["attempts"].append(attempt)
                with self.assertRaises(core._Problem):
                    utility_evidence.native_plan(encoded(plan), "UTILITY-001")

    def test_changed_client_or_case_bytes_invalidate_plan(self):
        self.f.binary.write_text("Changed selected client")
        with self.assertRaises(core._Problem):
            utility_evidence.native_plan(self.f.plan_path.read_bytes(), "UTILITY-001")

    def validator(self):
        root = self.root / "validator"
        root.mkdir()
        validator = Fixture(root, "skill-validator")
        for gate in validator.delivery["gate_inputs"]:
            if gate["id"] in {"native-C", "native-B", "native-A"}:
                continue
            evidence = self.f.plan_path if gate["id"] == "native-prerequisites" else validator.owner / "assignment.md"
            value = {"schema_version": "devforge.utility-gate-input/v1", "task_id": "UTILITY-001", "phase": gate["phase"],
                     "producer": gate["producer"], "inputs_sha256": validator.session["delivery_contract_sha256"],
                     "outcome": "PASS", "reason": "Synthetic allocated producer evidence", "evidence": [Fixture.pin(evidence)]}
            Path(gate["path"]).write_bytes(encoded(value))
        return validator

    def test_native_admission_requires_prior_phases_and_does_not_launch_or_reuse_attempt(self):
        validator = self.validator()
        self.assertEqual(validator.start()["status"], "ACTIVE")
        self.assertEqual(utility.native_admission(validator.state, "C-01")["status"], "FAIL")
        for phase in ("P1", "P2", "P3"):
            self.assertEqual(utility.context(validator.state)["phase"], phase)
            validator.checkpoint()
            self.assertEqual(utility.advance(validator.state)["status"], "PROGRESS")
        result = utility.native_admission(validator.state, "C-01")
        self.assertEqual(result["status"], "NATIVE_ADMISSION_RECORDED", result)
        self.assertFalse(result["native_launch_admitted"])
        self.assertEqual(result["execution"], "NOT_RUN")
        self.assertEqual(utility.native_admission(validator.state, "C-01")["status"], "FAIL")
        self.assertEqual(list(self.f.workspace.iterdir()), [])

    def test_client_binary_cannot_be_selected_from_attempt_writable_roots(self):
        for root in (self.f.workspace, self.f.private):
            with self.subTest(root=root):
                path = root / "client"
                path.write_bytes(self.f.binary.read_bytes())
                path.chmod(0o700)
                plan = copy.deepcopy(self.f.plan)
                plan["client"] = {**Fixture.pin(path), "version": "synthetic-0"}
                with self.assertRaisesRegex(core._Problem, "outside every attempt-writable"):
                    utility_evidence.native_plan(encoded(plan), "UTILITY-001")

    def test_later_tier_cannot_be_reserved_without_authenticated_preceding_results(self):
        for tier in ("B", "A"):
            with self.subTest(tier=tier), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                fixture = NativePlanFixture(root)
                fixture.plan["attempts"][0]["tier"] = tier
                fixture.save()
                old = self.f
                self.f = fixture
                old_root = self.root
                self.root = root
                validator = self.validator()
                self.f, self.root = old, old_root
                validator.start()
                for _ in range(3):
                    validator.checkpoint()
                    utility.advance(validator.state)
                result = utility.native_admission(validator.state, "C-01")
                self.assertEqual(result["status"], "FAIL", result)
                self.assertIn("authenticated preceding native results", result["issues"][0])

    def test_validator_cannot_omit_mandatory_independent_or_native_gate(self):
        for identity in utility.VALIDATOR_GATES:
            with self.subTest(identity=identity), tempfile.TemporaryDirectory() as tmp:
                validator = Fixture(Path(tmp), "skill-validator")
                validator.delivery["gate_inputs"] = [g for g in validator.delivery["gate_inputs"] if g["id"] != identity]
                validator.write_contracts()
                self.assertEqual(validator.start()["status"], "FAIL")

    def test_declared_native_pass_cannot_substitute_for_missing_result_importer(self):
        validator = self.validator()
        native = next(g for g in validator.delivery["gate_inputs"] if g["id"] == "native-C")
        path = Path(native["path"])
        value = json.loads(path.read_bytes())
        value["outcome"] = "PASS"
        path.write_bytes(encoded(value))
        validator.start()
        for _ in range(3):
            validator.checkpoint()
            utility.advance(validator.state)
        validator.checkpoint()
        result = utility.advance(validator.state)
        self.assertEqual(result["status"], "FAIL", result)
        self.assertIn("authenticated result importer", result["issues"][0])
        self.assertFalse(validator.receipt.exists())

    def test_incomplete_native_gate_preserves_reporting_without_native_admission(self):
        validator = self.validator()
        gate = Path(next(row["path"] for row in validator.delivery["gate_inputs"] if row["id"] == "native-prerequisites"))
        value = json.loads(gate.read_bytes())
        value.update(outcome="COULD_NOT_RUN", reason="Authentication and boundary observations unavailable")
        gate.write_bytes(encoded(value))
        validator.start()
        for phase in ("P1", "P2", "P3"):
            validator.checkpoint()
            utility.advance(validator.state)
        self.assertEqual(utility.native_admission(validator.state, "C-01")["status"], "FAIL")
        for phase in ("P4", "P5", "P6"):
            validator.checkpoint()
            self.assertIn(utility.advance(validator.state)["status"], {"PROGRESS", "READY"})
        self.assertEqual(utility.complete(validator.state)["status"], "COMPLETED")
        self.assertEqual(list(self.f.workspace.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
