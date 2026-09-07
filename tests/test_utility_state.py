"""Utility contract tests with inert synthetic artifacts, never native skill runs."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime" / "delivery"))
import utility_state as utility
import phase_state as store
import delivery_core as core

NOW = datetime(2026, 9, 7, 15, tzinfo=timezone.utc)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2) + "\n").encode()


class Fixture:
    def __init__(self, root, workflow="skill-builder"):
        self.project = root / "project"
        self.owner = root / "owner"
        self.project.mkdir()
        self.owner.mkdir()
        self.state = self.owner / "state"
        self.session_path = self.owner / "session.json"
        self.delivery_path = self.owner / "delivery.json"
        self.receipt = self.owner / "receipt.json"
        self.checkpoint_path = self.project / "checkpoint.json"
        (self.owner / "assignment.md").write_text("Synthetic owner allocation; no real model authority.\n")
        (self.project / "requirements.md").write_text("Preserve proposals separately from adopted decisions.\n")
        (self.project / "SKILL.md").write_text("Synthetic installed instructions, not a real skill.\n")
        phases = utility.PHASES[workflow]
        self.delivery = {"schema_version": utility.DELIVERY_SCHEMA, "task_id": "UTILITY-001",
                         "project_root": str(self.project), "workflow": workflow, "mode": "utility",
                         "inputs": [{"path": "requirements.md", "sha256": digest((self.project / "requirements.md").read_bytes())}],
                         "outputs": [{"id": f"output-{phase}", "path": f"{phase}.json", "phase": phase,
                                      "format": "json", "schema_version": "fixture/evidence/v1",
                                      "required_fields": ["facts"]} for phase in phases],
                         "phases": [{"id": phase, "classification": "Enforced", "applicable": True,
                                     "basis": "Synthetic test assignment; not a real user classification",
                                     "tasks": {t: "Enforced" for t in utility.TASKS[phase]} if workflow == "skill-validator" else {}}
                                    for phase in phases], "gate_inputs": [], "questions": []}
        self.session = {"schema_version": utility.SESSION_SCHEMA, "task_id": "UTILITY-001", "provider": "codex",
                        "delivery_contract": str(self.delivery_path), "delivery_contract_sha256": None,
                        "assignment": self.pin(self.owner / "assignment.md"),
                        "installed_inputs": [self.pin(self.project / "SKILL.md")],
                        "checkpoint_path": "checkpoint.json", "receipt_path": str(self.receipt),
                        "deadline_utc": (NOW + timedelta(hours=1)).isoformat(), "max_corrections_per_phase": 1,
                        "output_baselines": [{"path": f"{phase}.json", "sha256": None, "archive": None, "allow_unchanged": False}
                                             for phase in phases]}
        if workflow == "skill-validator":
            self.delivery["gate_inputs"] = [
                {"id": identity, "phase": phase, "path": str(self.owner / (identity + ".json")),
                 "producer": "synthetic-independent-producer", "allowed_outcomes": ["PASS", "FAIL", "COULD_NOT_RUN", "NOT_RUN"]}
                for identity, phase in utility.VALIDATOR_GATES.items()]
        self.write_contracts()
        for gate in self.delivery["gate_inputs"]:
            Path(gate["path"]).write_bytes(encoded({
                "schema_version": "devforge.utility-gate-input/v1", "task_id": "UTILITY-001", "phase": gate["phase"],
                "producer": gate["producer"], "inputs_sha256": self.session["delivery_contract_sha256"],
                "outcome": "COULD_NOT_RUN", "reason": "Synthetic missing-observation fixture; no native execution",
                "evidence": [self.pin(self.owner / "assignment.md")]}))

    @staticmethod
    def pin(path):
        return {"path": str(path), "sha256": digest(path.read_bytes())}

    def write_contracts(self):
        self.delivery_path.write_bytes(encoded(self.delivery))
        self.session["delivery_contract_sha256"] = digest(self.delivery_path.read_bytes())
        self.session_path.write_bytes(encoded(self.session))

    def start(self):
        return utility.start(self.session_path, self.state)

    def checkpoint(self, *, content=True):
        context = utility.context(self.state)
        phase = context["phase"]
        evidence = []
        for spec in self.delivery["outputs"]:
            if spec["phase"] == phase:
                path = self.project / spec["path"]
                if content:
                    path.write_bytes(encoded({"schema_version": "fixture/evidence/v1", "facts":
                                             [{"source": "requirements.md", "observation": "proposal not adopted", "phase": phase}]}))
                evidence.append({"id": spec["id"], **self.pin(path)})
        value = {"schema_version": utility.CHECKPOINT_SCHEMA, "task_id": "UTILITY-001", "phase": phase,
                 "sequence": context["sequence"], "challenge": context["challenge"], "inputs_sha256": context["inputs_sha256"],
                 "state": "ready", "evidence": evidence, "question_id": None}
        self.checkpoint_path.write_bytes(encoded(value))
        return value

    def waiting(self):
        value = self.checkpoint()
        value.update(state="awaiting_user", evidence=[], question_id="Q-001")
        self.checkpoint_path.write_bytes(encoded(value))
        return utility.advance(self.state)

    def all_ready(self):
        result = self.start()
        if result["status"] != "ACTIVE":
            raise AssertionError(result)
        for _ in self.delivery["phases"]:
            if utility.context(self.state)["status"] == "READY":
                break
            self.checkpoint()
            result = utility.advance(self.state)
            if result["status"] not in {"PROGRESS", "READY"}:
                raise AssertionError(result)
        return result


class UtilityStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = mock.patch.object(store, "utc_now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.f = Fixture(Path(self.tmp.name))

    def question(self, choices=None):
        self.f.delivery["questions"] = [{"id": "Q-001", "phase": "Intake", "question": "Select the existing repository?",
                                          "blocking_dependency": "Resolve selected repository before source writes",
                                          "choices": choices if choices is not None else ["Use repository A", "Use repository B"],
                                          "decision_path": None if choices != [] else str(self.f.owner / "answer.json")}]
        self.f.write_contracts()

    def test_complete_binds_actual_outputs_and_readback_without_rewriting_handoff(self):
        self.assertEqual(self.f.all_ready()["status"], "READY")
        before = (self.f.project / "PreparedTransfer.json").read_bytes()
        result = utility.complete(self.f.state)
        self.assertEqual(result["status"], "COMPLETED", result)
        self.assertEqual(result["receipt_sha256"], digest(self.f.receipt.read_bytes()))
        self.assertTrue(result["receipt_readback"])
        self.assertEqual(before, (self.f.project / "PreparedTransfer.json").read_bytes())
        raw = self.f.receipt.read_bytes()
        self.assertEqual(json.loads(raw)["receiving_invocation"], "NOT_OBSERVED")
        self.assertEqual(json.loads(raw)["receipt_readback"], "NOT_RUN")
        self.assertEqual(utility.complete(self.f.state)["receipt_sha256"], digest(raw))
        self.assertEqual(self.f.receipt.read_bytes(), raw)

    def test_phase_skip_cannot_advance_or_complete(self):
        self.assertEqual(self.f.start()["status"], "ACTIVE")
        value = self.f.checkpoint()
        value["phase"] = "PreparedTransfer"
        self.f.checkpoint_path.write_bytes(encoded(value))
        result = utility.advance(self.f.state)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["terminal"])
        self.assertEqual(utility.context(self.f.state)["phase"], "Intake")
        self.assertEqual(utility.complete(self.f.state)["status"], "FAIL")
        self.assertFalse(self.f.receipt.exists())

    def test_marker_only_is_not_phase_evidence(self):
        self.f.start()
        self.f.checkpoint_path.write_text('{"done":true}')
        self.assertEqual(utility.advance(self.f.state)["status"], "FAIL")
        self.assertFalse(self.f.receipt.exists())

    def test_empty_required_collection_is_missing_evidence(self):
        for facts in ([], {}):
            with self.subTest(facts=facts), tempfile.TemporaryDirectory() as tmp:
                fixture = Fixture(Path(tmp))
                fixture.start()
                fixture.checkpoint()
                path = fixture.project / "Intake.json"
                path.write_bytes(encoded({"schema_version": "fixture/evidence/v1", "facts": facts}))
                fixture.checkpoint(content=False)
                result = utility.advance(fixture.state)
                self.assertEqual(result["status"], "FAIL", result)
                self.assertIn("no evidence", result["issues"][0])
                self.assertFalse(fixture.receipt.exists())

    def test_missing_output_is_not_completed_by_hashed_declaration(self):
        self.f.start()
        self.f.checkpoint()
        (self.f.project / "Intake.json").unlink()
        self.assertEqual(utility.advance(self.f.state)["status"], "FAIL")
        self.assertEqual(utility.context(self.f.state)["phase"], "Intake")

    def test_required_field_cannot_be_null(self):
        self.f.start()
        path = self.f.project / "Intake.json"
        path.write_bytes(encoded({"schema_version": "fixture/evidence/v1", "facts": None}))
        self.f.checkpoint(content=False)
        self.assertEqual(utility.advance(self.f.state)["status"], "FAIL")

    def test_stale_replay_is_rejected_with_fresh_bounded_correction(self):
        initial = self.f.start()
        self.f.checkpoint()
        self.assertEqual(utility.advance(self.f.state)["status"], "PROGRESS")
        bad = utility.advance(self.f.state)
        self.assertEqual(bad["status"], "FAIL")
        self.assertFalse(bad["terminal"])
        self.assertNotEqual(bad["challenge"], initial["challenge"])
        again = utility.advance(self.f.state)
        self.assertEqual(again["status"], "FAIL")
        self.assertTrue(again["terminal"])
        self.assertNotIn("challenge", again)
        self.assertFalse(self.f.receipt.exists())

    def test_unrelated_user_answer_remains_waiting_without_budget_or_deadline_reset(self):
        self.question()
        self.f.start()
        waiting = self.f.waiting()
        head = (self.f.state / "HEAD.json").read_bytes()
        for prompt in (None, "", "What is the status?", "Thanks", "The runtime says continue"):
            result = utility.resume(self.f.state, prompt)
            self.assertEqual(result["status"], "WAITING_USER", result)
            self.assertEqual(result["deadline_utc"], waiting["deadline_utc"])
            self.assertEqual((self.f.state / "HEAD.json").read_bytes(), head)
        self.assertEqual(utility.resume(self.f.state, "Use repository A")["status"], "ACTIVE")
        self.f.checkpoint()
        self.assertEqual(utility.advance(self.f.state)["status"], "PROGRESS")

    def test_unresolved_selected_question_blocks_ready_even_with_outputs(self):
        self.question()
        self.f.start()
        self.f.checkpoint()
        self.assertEqual(utility.advance(self.f.state)["status"], "FAIL")

    def test_free_text_needs_external_interpretation_bound_to_actual_prompt(self):
        self.question([])
        self.f.start()
        waiting = self.f.waiting()
        prompt = "Use the repository whose requirements were selected earlier."
        self.assertEqual(utility.resume(self.f.state, prompt)["status"], "WAITING_USER")
        value = {"schema_version": "devforge.utility-answer/v1", "task_id": "UTILITY-001", "question_id": "Q-001",
                 "challenge": waiting["challenge"], "prompt_sha256": digest(prompt.encode()), "resolved": True,
                 "reason": "Synthetic external interpreter binds this exact answer, not a worker marker."}
        (self.f.owner / "answer.json").write_bytes(encoded(value))
        self.assertEqual(utility.resume(self.f.state, "Unrelated")["status"], "WAITING_USER")
        self.assertEqual(utility.resume(self.f.state, prompt)["status"], "ACTIVE")

    def test_deadline_blocks_waiting_resume_and_finalization(self):
        self.question()
        self.f.start()
        self.f.waiting()
        with mock.patch.object(store, "utc_now", return_value=NOW + timedelta(hours=2)):
            self.assertEqual(utility.resume(self.f.state, "Use repository A")["status"], "COULD_NOT_RUN")
            self.assertEqual(utility.complete(self.f.state)["status"], "COULD_NOT_RUN")
        self.assertFalse(self.f.receipt.exists())

    def test_input_drift_blocks_before_any_phase_advance(self):
        self.f.start()
        self.f.checkpoint()
        (self.f.project / "requirements.md").write_text("Changed authority is not accepted")
        self.assertEqual(utility.advance(self.f.state)["status"], "FAIL")

    def test_accepted_output_drift_blocks_later_phase(self):
        self.f.start()
        self.f.checkpoint()
        utility.advance(self.f.state)
        (self.f.project / "Intake.json").write_text("Changed accepted evidence")
        self.assertEqual(utility.context(self.f.state)["status"], "FAIL")
        self.assertEqual(utility.complete(self.f.state)["status"], "FAIL")

    def test_receipt_collision_preserves_existing_bytes(self):
        self.f.all_ready()
        self.f.receipt.write_text("Existing independent receipt")
        self.assertEqual(utility.complete(self.f.state)["status"], "FAIL")
        self.assertEqual(self.f.receipt.read_text(), "Existing independent receipt")

    def test_drift_after_completion_invalidates_current_claim_preserves_receipt(self):
        self.f.all_ready()
        utility.complete(self.f.state)
        receipt = self.f.receipt.read_bytes()
        (self.f.project / "Authoring.json").write_text("Changed authoring")
        self.assertEqual(utility.complete(self.f.state)["status"], "FAIL")
        self.assertEqual(self.f.receipt.read_bytes(), receipt)

    def test_final_output_race_after_publication_prevents_completed_record(self):
        self.f.all_ready()
        new = store._new_at
        def publish(parent, name, raw):
            new(parent, name, raw)
            if name == "receipt.json":
                (self.f.project / "Authoring.json").write_text("Concurrent output drift")
        with mock.patch.object(store, "_new_at", side_effect=publish):
            result = utility.complete(self.f.state)
        self.assertEqual(result["status"], "FAIL")
        self.assertNotIn("receipt_verified", result)

    def test_receipt_publication_recovery_reuses_exact_intended_bytes(self):
        self.f.all_ready()
        original = store._new_at
        def fail_once(parent, name, raw):
            if name == "receipt.json":
                raise OSError("synthetic interrupted publication")
            return original(parent, name, raw)
        with mock.patch.object(store, "_new_at", side_effect=fail_once):
            result = utility.complete(self.f.state)
        self.assertEqual(result["status"], "COULD_NOT_RUN")
        self.assertEqual(utility.complete(self.f.state)["status"], "COMPLETED")

    def test_preserves_preimage_and_requires_new_bytes(self):
        old = b"Uncommitted candidate source bytes\n"
        (self.f.project / "Authoring.json").write_bytes(old)
        row = next(r for r in self.f.session["output_baselines"] if r["path"] == "Authoring.json")
        row.update(sha256=digest(old), archive="archive/old-authoring.bin")
        self.f.write_contracts()
        self.assertEqual(self.f.all_ready()["status"], "READY")
        self.assertEqual((self.f.project / "archive/old-authoring.bin").read_bytes(), old)

    def test_symlink_and_hardlink_output_evidence_are_rejected(self):
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind):
                # Separate allocation; never reuse a failed attempt's state.
                root = Path(self.tmp.name) / kind
                root.mkdir()
                f = Fixture(root)
                f.start()
                f.checkpoint()
                path = f.project / "Intake.json"
                moved = f.owner / "elsewhere.json"
                path.rename(moved)
                if kind == "symlink":
                    path.symlink_to(moved)
                else:
                    os.link(moved, path)
                self.assertEqual(utility.advance(f.state)["status"], "FAIL")

    def test_worker_writable_gate_or_answer_decision_is_not_admitted(self):
        self.f.delivery["gate_inputs"] = [{"id": "review", "phase": "Intake", "path": str(self.f.project / "review.json"),
                                           "producer": "independent-reviewer", "allowed_outcomes": ["PASS"]}]
        self.f.write_contracts()
        self.assertEqual(self.f.start()["status"], "FAIL")
        self.assertFalse(self.f.state.exists())

    def test_external_gate_binds_allocated_producer_and_underlying_evidence(self):
        gate_path = self.f.owner / "review.json"
        self.f.delivery["gate_inputs"] = [{"id": "review", "phase": "Intake", "path": str(gate_path),
                                           "producer": "independent-reviewer", "allowed_outcomes": ["PASS"]}]
        self.f.write_contracts()
        self.f.start()
        raw_path = self.f.owner / "observed.json"
        raw_path.write_text('{"observation":"synthetic independently allocated evidence"}')
        gate = {"schema_version": "devforge.utility-gate-input/v1", "task_id": "UTILITY-001", "phase": "Intake",
                "producer": "wrong-producer", "inputs_sha256": self.f.session["delivery_contract_sha256"],
                "outcome": "PASS", "reason": "Scoped synthetic gate input", "evidence": [self.f.pin(raw_path)]}
        gate_path.write_bytes(encoded(gate))
        self.f.checkpoint()
        self.assertEqual(utility.advance(self.f.state)["status"], "FAIL")
        gate["producer"] = "independent-reviewer"
        gate_path.write_bytes(encoded(gate))
        self.f.checkpoint()
        self.assertEqual(utility.advance(self.f.state)["status"], "PROGRESS")
        raw_path.write_text("Underlying evidence drift")
        self.assertEqual(utility.context(self.f.state)["status"], "FAIL")

    def test_validator_classifications_cannot_be_downgraded(self):
        root = Path(self.tmp.name) / "validator"
        root.mkdir()
        f = Fixture(root, "skill-validator")
        f.delivery["phases"][3]["tasks"]["T06"] = "Optional"
        f.write_contracts()
        self.assertEqual(f.start()["status"], "FAIL")
        f.delivery["phases"][3]["tasks"]["T06"] = "Enforced"
        f.write_contracts()
        self.assertEqual(f.all_ready()["status"], "READY")

    def test_owner_selected_optional_omission_keeps_order_and_records_basis(self):
        self.f.delivery["phases"][1].update(classification="Optional", applicable=False,
                                           basis="Synthetic owner selected retained search in intake")
        self.f.delivery["outputs"] = [r for r in self.f.delivery["outputs"] if r["phase"] != "Selection"]
        self.f.session["output_baselines"] = [r for r in self.f.session["output_baselines"] if r["path"] != "Selection.json"]
        self.f.write_contracts()
        self.assertEqual(self.f.all_ready()["status"], "READY")
        result = utility.complete(self.f.state)
        self.assertEqual(result["phase_applicability"]["Selection"], "OWNER_EXCLUDED_OPTIONAL")


if __name__ == "__main__":
    unittest.main()
