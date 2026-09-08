"""Synthetic broker/dispatch tests; no native callback provenance claimed."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from test_utility_state import Fixture, NOW, digest, encoded
from delivery import controller, phase_state, supervisor, utility_state, workflow_runtime


class UtilityRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = Fixture(Path(self.tmp.name))
        self.clock = mock.patch.object(phase_state, "utc_now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.broker = None

    def start(self):
        result = controller.operate("init", self.f.state, self.f.session_path)
        self.assertEqual(result["status"], "ACTIVE", result)
        observations = self.f.owner / "observations"
        observations.mkdir()
        self.broker = supervisor.Broker(self.f.state, self.f.session, self.f.delivery,
                                        digest(self.f.session_path.read_bytes()), observations,
                                        completion_mode="managed-session")
        self.addCleanup(self.broker.close)
        return result

    def callback(self, name, **fields):
        event = {"hook_event_name": name, "session_id": "synthetic-utility-client", "cwd": str(self.f.project), **fields}
        if name == "Stop":
            event.setdefault("stop_hook_active", True)
        return self.broker.handle({"provider": "codex", "contract_sha256": digest(self.f.session_path.read_bytes()), "event": event})

    def checkpoint(self):
        context = utility_state.context(self.f.state)
        evidence = []
        for row in self.f.delivery["outputs"]:
            if row["phase"] == context["phase"]:
                path = self.f.project / row["path"]
                path.write_bytes(encoded({"schema_version": "fixture/evidence/v1", "facts": [{"source": "requirements.md", "fact": "Synthetic phase evidence"}]}))
                evidence.append({"id": row["id"], "path": str(path), "sha256": digest(path.read_bytes())})
        value = {"schema_version": utility_state.CHECKPOINT_SCHEMA, "task_id": "UTILITY-001", "phase": context["phase"],
                 "sequence": context["sequence"], "challenge": context["challenge"], "inputs_sha256": context["inputs_sha256"],
                 "state": "ready", "evidence": evidence, "question_id": None}
        self.f.checkpoint_path.write_bytes(encoded(value))
        return value

    def test_dispatch_and_managed_stop_publish_receipt_only_after_selected_phases(self):
        self.start()
        self.assertIs(workflow_runtime.engine_for_state(self.f.state), utility_state)
        reply = self.callback("SessionStart")
        self.assertIn("devforge.utility-checkpoint/v1", reply["hookSpecificOutput"]["additionalContext"])
        for phase in utility_state.PHASES["skill-builder"]:
            self.assertEqual(utility_state.context(self.f.state)["phase"], phase)
            self.checkpoint()
            reply = self.callback("Stop")
            if phase != "PreparedTransfer":
                self.assertEqual(reply.get("decision"), "block", reply)
                self.assertFalse(self.f.receipt.exists())
        self.assertIn(digest(self.f.receipt.read_bytes()), reply["systemMessage"])
        original = self.f.receipt.read_bytes()
        self.assertEqual(self.callback("Stop"), reply)
        self.assertEqual(self.f.receipt.read_bytes(), original)
        self.assertIsNotNone(self.broker.task_result)

    def test_failed_callback_does_not_complete_ready_task(self):
        self.start()
        self.callback("SessionStart")
        for phase in utility_state.PHASES["skill-builder"]:
            self.checkpoint()
            if phase == "PreparedTransfer":
                self.assertEqual(utility_state.advance(self.f.state)["status"], "READY")
            else:
                self.callback("Stop")
        self.broker.error = "Synthetic callback transport failed"
        self.assertEqual(self.broker._complete_managed()["status"], "COULD_NOT_RUN")
        self.assertFalse(self.f.receipt.exists())

    def test_missing_session_start_and_wrong_session_cannot_advance(self):
        self.start()
        with self.assertRaises(ValueError):
            self.callback("Stop")
        self.callback("SessionStart")
        event = {"hook_event_name": "Stop", "session_id": "another-session", "cwd": str(self.f.project), "stop_hook_active": False}
        with self.assertRaises(ValueError):
            self.broker.handle({"provider": "codex", "contract_sha256": digest(self.f.session_path.read_bytes()), "event": event})
        self.assertFalse(self.f.receipt.exists())

    def test_user_prompt_is_checked_against_pending_answer_not_message_arrival(self):
        self.f.delivery["questions"] = [{"id": "Q-001", "phase": "Intake", "question": "Which selected repository?",
                                          "blocking_dependency": "Source selection", "choices": ["Repository A", "Repository B"], "decision_path": None}]
        self.f.write_contracts()
        self.start()
        self.callback("SessionStart")
        value = self.checkpoint()
        value.update(state="awaiting_user", evidence=[], question_id="Q-001")
        self.f.checkpoint_path.write_bytes(encoded(value))
        self.callback("Stop")
        self.callback("UserPromptSubmit", prompt="Please give me a status update.")
        self.assertEqual(utility_state.context(self.f.state)["status"], "WAITING_USER")
        self.callback("UserPromptSubmit", prompt="Repository A")
        self.assertEqual(utility_state.context(self.f.state)["status"], "ACTIVE")

    def test_unknown_schema_does_not_fall_back_to_brainstorm(self):
        self.f.session["schema_version"] = "made-up/v1"
        with self.assertRaises(Exception):
            workflow_runtime.engine(self.f.session)


if __name__ == "__main__":
    unittest.main()
