"""Independent phase-contract fixtures; no native client or worker execution.

Expectations derive from PHASE-CONTRACT.md (c6d71def...e481d2) and the
integration owner's API clarifications. No phase implementation was read to
author these tests. Hashes are computed from independently authored bytes.
Temporary operator/project directories demonstrate logic, not a real sandbox.
"""

from __future__ import annotations

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


IMPLEMENTATION = Path(__file__).resolve().parents[1] / "runtime" / "delivery"
sys.path.insert(0, str(IMPLEMENTATION))
import phase_state as phase  # noqa: E402


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
STATUSES = {"ACTIVE", "PROGRESS", "WAITING_USER", "READY", "COMPLETED", "FAIL", "COULD_NOT_RUN"}
CHALLENGE = r"^[0-9a-f]{32}$"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def artifact_bytes(identity: str, role: str, body: str, upstream: list | None = None) -> bytes:
    env = {"schema_version": "devforge.artifact/v1", "artifact_id": identity,
           "artifact_type": role, "revision": 1, "status": "draft"}
    if upstream is not None:
        env["upstream"] = upstream
    return b"---\n" + json_bytes(env) + b"---\n\n" + body.encode("utf-8")


class Fixture:
    def __init__(self, root: Path, mode: str = "brainstorm"):
        self.root = root
        self.project = root / "project"
        self.operator = root / "operator"
        self.project.mkdir(parents=True)
        self.operator.mkdir()
        self.state = root / "state"
        self.delivery_path = self.operator / "delivery-contract.json"
        self.session_path = self.operator / "session-contract.json"
        self.assignment_path = self.operator / "assignment.md"
        self.assignment_path.write_bytes(b"Synthetic selected assignment bytes; no native authority.\n")
        self.input_path = self.project / "inputs/context.txt"
        self.input_path.parent.mkdir()
        self.input_path.write_bytes(b"A volunteer has mentioned uncertainty about shift handoffs.\n")
        self.installed_path = self.project / "installed/SKILL.md"
        self.installed_path.parent.mkdir()
        self.installed_path.write_bytes(b"Synthetic installed resource bytes.\n")
        self.ledger_path = self.project / "docs/ideas.md"
        self.handoff_path = self.project / "docs/handoff.md"
        self.checkpoint_path = self.project / "checkpoints/current.json"
        self.receipt_path = self.operator / "receipt.json"
        self.mode = mode
        outputs = []
        if mode == "brainstorm":
            outputs.append({"path": "docs/ideas.md", "artifact_id": "IDEAS-001",
                            "artifact_type": "idea-ledger", "revision": 1,
                            "sections": ["IDEAS-SECTION-001"]})
        outputs.append({"path": "docs/handoff.md", "artifact_id": "HANDOFF-001",
                        "artifact_type": "handoff", "revision": 1,
                        "sections": ["NEXT-001"]})
        self.delivery = {
            "schema_version": "devforge.delivery-task/v1", "task_id": "PHASE-TEST-001",
            "project_root": str(self.project), "mode": mode,
            "inputs": [{"path": "inputs/context.txt", "sha256": digest(self.input_path.read_bytes())}],
            "outputs": outputs,
        }
        self.delivery_path.write_bytes(json_bytes(self.delivery))
        self.session = {
            "schema_version": "devforge.brainstorm-session/v1", "task_id": "PHASE-TEST-001",
            "provider": "codex", "delivery_contract": str(self.delivery_path),
            "delivery_contract_sha256": digest(self.delivery_path.read_bytes()),
            "assignment": {"path": str(self.assignment_path), "sha256": digest(self.assignment_path.read_bytes())},
            "installed_inputs": [{"path": str(self.installed_path), "sha256": digest(self.installed_path.read_bytes())}],
            "checkpoint_path": "checkpoints/current.json", "receipt_path": str(self.receipt_path),
            "deadline_utc": (NOW + timedelta(hours=1)).isoformat(), "max_corrections_per_phase": 1,
            "output_baselines": [{"path": output["path"], "sha256": None,
                                  "archive": None, "allow_unchanged": False} for output in outputs],
        }
        self.write_session()

    def write_session(self) -> None:
        self.session_path.write_bytes(json_bytes(self.session))

    def refresh_delivery(self) -> None:
        self.delivery_path.write_bytes(json_bytes(self.delivery))
        self.session["delivery_contract_sha256"] = digest(self.delivery_path.read_bytes())
        self.write_session()

    def ledger(self, suffix: str = "") -> bytes:
        return artifact_bytes("IDEAS-001", "idea-ledger",
                              "## Ideas [IDEAS-SECTION-001]\n\nA reminder remains an unadopted proposal.\n" + suffix)

    def handoff(self, ledger: bytes | None = None, suffix: str = "") -> bytes:
        upstream = []
        if self.mode == "brainstorm":
            assert ledger is not None
            upstream = [{"artifact_id": "IDEAS-001", "revision": 1, "store": "project",
                         "path": "docs/ideas.md", "sha256": digest(ledger),
                         "sections": ["IDEAS-SECTION-001"]}]
        return artifact_bytes("HANDOFF-001", "handoff",
                              "## Next [NEXT-001]\n\nAsk a volunteer for one concrete observation.\n" + suffix,
                              upstream)

    def write_ledger(self, suffix: str = "") -> bytes:
        raw = self.ledger(suffix)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_bytes(raw)
        return raw

    def write_handoff(self, suffix: str = "") -> bytes:
        ledger = self.ledger_path.read_bytes() if self.mode == "brainstorm" else None
        raw = self.handoff(ledger, suffix)
        self.handoff_path.parent.mkdir(parents=True, exist_ok=True)
        self.handoff_path.write_bytes(raw)
        return raw

    def write_outputs(self, suffix: str = "") -> None:
        if self.mode == "brainstorm":
            self.write_ledger(suffix)
        self.write_handoff(suffix)

    def seed_baselines(self, allow_unchanged: bool = False, raw_values: list[bytes] | None = None) -> dict[str, bytes]:
        if raw_values is None:
            self.write_outputs()
        result = {}
        for index, row in enumerate(self.session["output_baselines"]):
            live = self.project / row["path"]
            live.parent.mkdir(parents=True, exist_ok=True)
            if raw_values is not None:
                live.write_bytes(raw_values[index])
            raw = live.read_bytes()
            row.update(sha256=digest(raw), archive="archive/prelaunch-" + str(index) + ".bin",
                       allow_unchanged=allow_unchanged)
            result[row["archive"]] = raw
        self.write_session()
        return result


class PhaseStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="devforge-phase-contract-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.counter = 0
        clock_patch = mock.patch.object(phase, "utc_now", return_value=NOW)
        self.clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)

    def fixture(self, mode: str = "brainstorm") -> Fixture:
        self.counter += 1
        return Fixture(self.root / ("case-" + str(self.counter)), mode)

    def result(self, actual: dict, status: str, phase_name: str | None = None) -> dict:
        self.assertIsInstance(actual, dict)
        self.assertIn(actual.get("status"), STATUSES, actual)
        self.assertEqual(actual["status"], status, actual)
        self.assertIsInstance(actual.get("issues"), list, actual)
        self.assertTrue(actual.get("scope"), actual)
        self.assertEqual(actual.get("native_completion"), "NOT_EVALUATED", actual)
        if phase_name is not None:
            self.assertEqual(actual.get("phase"), phase_name, actual)
        return actual

    def admitted(self, actual: dict, status: str, phase_name: str) -> dict:
        self.result(actual, status, phase_name)
        self.assertEqual(actual.get("task_id"), "PHASE-TEST-001")
        self.assertRegex(actual.get("challenge", ""), CHALLENGE)
        self.assertTrue(actual.get("instructions"))
        return actual

    def begin(self, f: Fixture) -> dict:
        return self.admitted(phase.start(f.session_path, f.state), "ACTIVE", "Recover")

    @staticmethod
    def recover_content() -> dict:
        return {"known_ideas": [], "known_decisions": [],
                "missing_inputs": ["No prior ledger is available.", "No human decision is known."]}

    @staticmethod
    def explore_content() -> dict:
        return {"ideas": [{"idea_id": "IDEA-TEST-001", "people": None,
                            "problem": "A shift handoff may be missed.", "outcome": None,
                            "alternatives": ["Ask for one concrete example before choosing a product."],
                            "open_questions": ["Who experiences this and what would improve?"]}],
                "missing_inputs": ["The affected group has not been selected."]}

    @staticmethod
    def focus_content() -> dict:
        return {"next_action": "Ask one volunteer about a recent handoff.", "owner": "Human project owner",
                "completion_evidence": "One written observation with remaining uncertainty.",
                "non_goals": ["No release or automatic adoption."], "handoff_path": "docs/handoff.md"}

    def checkpoint(self, f: Fixture, phase_name: str, content: dict, *, state: str = "ready",
                   challenge: str | None = None, extra: dict | None = None) -> bytes:
        if challenge is None:
            challenge = phase.context(f.state)["challenge"]
        value = {"schema_version": "devforge.brainstorm-checkpoint/v1", "task_id": "PHASE-TEST-001",
                 "phase": phase_name, "challenge": challenge, "state": state, "content": content}
        if extra:
            value.update(extra)
        raw = json_bytes(value)
        f.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        f.checkpoint_path.write_bytes(raw)
        return raw

    def recover(self, f: Fixture) -> dict:
        self.checkpoint(f, "Recover", self.recover_content())
        following = "Explore" if f.mode == "brainstorm" else "Focus"
        return self.admitted(phase.advance(f.state), "PROGRESS", following)

    def to_record(self, f: Fixture) -> None:
        self.begin(f)
        self.recover(f)
        self.checkpoint(f, "Explore", self.explore_content())
        self.admitted(phase.advance(f.state), "PROGRESS", "Record")

    def to_focus(self, f: Fixture, *, keep_ledger: bool = False) -> None:
        if f.mode == "handoff-only":
            self.begin(f)
            self.recover(f)
            return
        self.to_record(f)
        if not keep_ledger:
            f.write_ledger()
        self.checkpoint(f, "Record", {"ledger_path": "docs/ideas.md"})
        self.admitted(phase.advance(f.state), "PROGRESS", "Focus")

    def to_ready(self, f: Fixture, *, keep_outputs: bool = False) -> dict:
        self.to_focus(f, keep_ledger=keep_outputs)
        if not keep_outputs:
            f.write_handoff()
        self.checkpoint(f, "Focus", self.focus_content())
        return self.result(phase.advance(f.state), "READY", "Focus")

    @staticmethod
    def tree_bytes(root: Path) -> dict[str, bytes]:
        return {str(path.relative_to(root)): path.read_bytes()
                for path in root.rglob("*") if path.is_file() and not path.is_symlink()}

    def snapshot_of(self, f: Fixture, raw: bytes) -> Path:
        matches = [path for path in f.state.rglob("*")
                   if path.is_file() and not path.is_symlink() and path.read_bytes() == raw]
        self.assertTrue(matches, "Accepted exact bytes must be preserved under protected state")
        return sorted(matches)[0]

    def refusal(self, actual: dict) -> None:
        self.assertIn(actual.get("status"), {"FAIL", "COULD_NOT_RUN"}, actual)
        self.assertEqual(actual.get("native_completion"), "NOT_EVALUATED", actual)

    def test_four_phase_path_binds_independent_output_hashes_and_completes(self):
        f = self.fixture()
        first = self.begin(f)
        self.assertEqual(phase.context(f.state).get("phase_applicability"),
                         {name: "REQUIRED" for name in ("Recover", "Explore", "Record", "Focus")})
        self.assertFalse(f.receipt_path.exists())
        following = self.recover(f)
        self.assertNotEqual(first["challenge"], following["challenge"])
        self.checkpoint(f, "Explore", self.explore_content())
        record_admission = self.admitted(phase.advance(f.state), "PROGRESS", "Record")
        ledger = f.write_ledger()
        self.checkpoint(f, "Record", {"ledger_path": "docs/ideas.md"})
        focus_admission = self.admitted(phase.advance(f.state), "PROGRESS", "Focus")
        self.assertEqual(len({first["challenge"], following["challenge"],
                              record_admission["challenge"], focus_admission["challenge"]}), 4)
        self.assertFalse(f.handoff_path.exists(), "Record must not require a handoff yet")
        handoff = f.write_handoff()
        self.checkpoint(f, "Focus", self.focus_content())
        self.result(phase.advance(f.state), "READY", "Focus")
        self.assertFalse(f.receipt_path.exists())
        completed = self.result(phase.complete(f.state), "COMPLETED")
        self.assertEqual(completed["receipt_path"], str(f.receipt_path))
        self.assertEqual(completed["receipt_sha256"], digest(f.receipt_path.read_bytes()))
        receipt = json.loads(f.receipt_path.read_bytes())
        self.assertEqual(receipt["outputs"], {"docs/ideas.md": digest(ledger), "docs/handoff.md": digest(handoff)})
        self.assertEqual(receipt["task_id"], f.delivery["task_id"])
        self.assertEqual(receipt["contract_sha256"], digest(f.delivery_path.read_bytes()))
        self.assertEqual(receipt["receipt_publication"], "NOT_RUN")
        self.assertEqual(receipt["receipt_readback"], "NOT_RUN")

    def test_handoff_only_admits_recover_then_focus_and_records_skips(self):
        f = self.fixture("handoff-only")
        self.to_ready(f)
        self.assertFalse(f.ledger_path.exists())
        self.assertEqual(phase.context(f.state).get("phase_applicability"),
                         {"Recover": "REQUIRED", "Explore": "NOT_APPLICABLE",
                          "Record": "NOT_APPLICABLE", "Focus": "REQUIRED"})
        self.result(phase.complete(f.state), "COMPLETED")
        self.assertEqual(set(json.loads(f.receipt_path.read_bytes())["outputs"]), {"docs/handoff.md"})

    def test_context_is_read_only_and_keeps_durable_challenge(self):
        f = self.fixture()
        original = self.begin(f)
        before = self.tree_bytes(f.root)
        for _ in range(2):
            observed = self.admitted(phase.context(f.state), "ACTIVE", "Recover")
            self.assertEqual(observed["challenge"], original["challenge"])
        self.assertEqual(self.tree_bytes(f.root), before)

    def test_both_provider_values_are_supported(self):
        for provider in ("codex", "claude"):
            with self.subTest(provider=provider):
                f = self.fixture()
                f.session["provider"] = provider
                f.write_session()
                self.begin(f)

    def test_future_phase_cannot_skip_even_with_valid_final_outputs(self):
        f = self.fixture()
        admitted = self.begin(f)
        f.write_outputs()
        self.checkpoint(f, "Focus", self.focus_content(), challenge=admitted["challenge"])
        failure = self.result(phase.advance(f.state), "FAIL")
        self.assertIs(failure.get("terminal"), False)
        self.assertEqual(failure.get("corrections"), 1)
        self.assertEqual(phase.context(f.state)["phase"], "Recover")
        self.assertFalse(f.receipt_path.exists())

    def test_wrong_challenge_preserves_current_phase_and_one_repair(self):
        f = self.fixture()
        admitted = self.begin(f)
        wrong = ("0" if admitted["challenge"][0] != "0" else "1") + admitted["challenge"][1:]
        self.checkpoint(f, "Recover", self.recover_content(), challenge=wrong)
        failure = self.result(phase.advance(f.state), "FAIL")
        self.assertIs(failure.get("terminal"), False)
        self.assertEqual(failure["challenge"], admitted["challenge"])
        self.recover(f)

    def test_previous_valid_checkpoint_is_replay_and_cannot_advance_twice(self):
        f = self.fixture()
        self.begin(f)
        previous = self.checkpoint(f, "Recover", self.recover_content())
        next_phase = self.admitted(phase.advance(f.state), "PROGRESS", "Explore")
        self.assertEqual(f.checkpoint_path.read_bytes(), previous)
        self.result(phase.advance(f.state), "FAIL")
        self.assertEqual(phase.context(f.state)["phase"], "Explore")
        self.assertEqual(phase.context(f.state)["challenge"], next_phase["challenge"])
        self.checkpoint(f, "Explore", self.explore_content())
        self.admitted(phase.advance(f.state), "PROGRESS", "Record")

    def test_one_content_repair_then_success_is_permitted(self):
        f = self.fixture()
        admitted = self.begin(f)
        self.checkpoint(f, "Recover", {"known_ideas": [], "known_decisions": [], "missing_inputs": []})
        failure = self.result(phase.advance(f.state), "FAIL")
        self.assertIs(failure.get("terminal"), False)
        self.assertEqual(failure.get("corrections"), 1)
        self.assertEqual(failure.get("challenge"), admitted["challenge"])
        self.assertTrue(failure.get("instructions"))
        self.admitted(phase.context(f.state), "ACTIVE", "Recover")
        self.recover(f)

    def test_second_content_failure_durably_terminates_admission(self):
        f = self.fixture()
        self.begin(f)
        bad = {"known_ideas": [], "known_decisions": [], "missing_inputs": []}
        self.checkpoint(f, "Recover", bad)
        self.assertIs(self.result(phase.advance(f.state), "FAIL").get("terminal"), False)
        self.assertIs(self.result(phase.advance(f.state), "FAIL").get("terminal"), True)
        self.result(phase.context(f.state), "FAIL")
        self.refusal(phase.resume(f.state))
        self.assertFalse(f.receipt_path.exists())

    def test_each_phase_has_one_independent_correction_budget(self):
        f = self.fixture()
        self.begin(f)
        cases = [
            ("Recover", self.recover_content(), {"known_ideas": [], "known_decisions": [], "missing_inputs": []}, "Explore"),
            ("Explore", self.explore_content(), {"ideas": [], "missing_inputs": []}, "Record"),
            ("Record", {"ledger_path": "docs/ideas.md"}, {"ledger_path": "docs/wrong.md"}, "Focus"),
            ("Focus", self.focus_content(), {**self.focus_content(), "handoff_path": "docs/wrong.md"}, None),
        ]
        for phase_name, valid, invalid, following in cases:
            with self.subTest(phase=phase_name):
                if phase_name == "Record":
                    f.write_ledger()
                elif phase_name == "Focus":
                    f.write_handoff()
                self.checkpoint(f, phase_name, invalid)
                failure = self.result(phase.advance(f.state), "FAIL")
                self.assertIs(failure.get("terminal"), False)
                self.assertEqual(failure.get("corrections"), 1)
                self.checkpoint(f, phase_name, valid)
                if following is None:
                    self.result(phase.advance(f.state), "READY", "Focus")
                else:
                    self.admitted(phase.advance(f.state), "PROGRESS", following)
        self.assertFalse(f.receipt_path.exists())

    def test_waiting_user_preserves_question_phase_and_no_receipt(self):
        f = self.fixture()
        current = self.begin(f)
        question = "Which volunteer handoff should we investigate?"
        raw = self.checkpoint(f, "Recover", {"question": question, "blocking_dependency": "The relevant group is unknown."},
                              state="awaiting_user")
        self.admitted(phase.advance(f.state), "WAITING_USER", "Recover")
        self.snapshot_of(f, raw)
        context = self.result(phase.context(f.state), "WAITING_USER", "Recover")
        self.assertIn(question, json.dumps(context))
        self.assertFalse(f.receipt_path.exists())
        resumed = self.admitted(phase.resume(f.state), "ACTIVE", "Recover")
        self.assertNotEqual(resumed["challenge"], current["challenge"])
        self.assertFalse(f.receipt_path.exists())

    def test_waiting_checkpoint_requires_question_and_blocking_dependency(self):
        for variant in ("empty-question", "missing-dependency", "extra-authority-claim"):
            with self.subTest(variant=variant):
                f = self.fixture()
                self.begin(f)
                content = {"question": "Which group?", "blocking_dependency": "The group is unknown."}
                if variant == "empty-question":
                    content["question"] = "   "
                elif variant == "missing-dependency":
                    content.pop("blocking_dependency")
                else:
                    content["user_authorized"] = True
                self.checkpoint(f, "Recover", content, state="awaiting_user")
                self.result(phase.advance(f.state), "FAIL")
                self.admitted(phase.context(f.state), "ACTIVE", "Recover")
                self.assertFalse(f.receipt_path.exists())

    def test_resume_active_recovers_same_phase_with_fresh_challenge(self):
        f = self.fixture()
        admitted = self.begin(f)
        resumed = self.admitted(phase.resume(f.state), "ACTIVE", "Recover")
        self.assertNotEqual(resumed["challenge"], admitted["challenge"])
        self.checkpoint(f, "Recover", self.recover_content(), challenge=admitted["challenge"])
        self.result(phase.advance(f.state), "FAIL")
        self.assertEqual(phase.context(f.state)["phase"], "Recover")

    def test_resume_does_not_reset_correction_budget(self):
        for waiting in (False, True):
            with self.subTest(waiting=waiting):
                f = self.fixture()
                self.begin(f)
                invalid = {"known_ideas": [], "known_decisions": [], "missing_inputs": []}
                self.checkpoint(f, "Recover", invalid)
                self.result(phase.advance(f.state), "FAIL")
                if waiting:
                    self.checkpoint(f, "Recover", {"question": "Which group?", "blocking_dependency": "Group is unknown."},
                                    state="awaiting_user")
                    self.result(phase.advance(f.state), "WAITING_USER")
                resumed = self.admitted(phase.resume(f.state), "ACTIVE", "Recover")
                self.checkpoint(f, "Recover", invalid, challenge=resumed["challenge"])
                self.assertIs(self.result(phase.advance(f.state), "FAIL").get("terminal"), True)

    def test_checkpoint_strict_shapes_types_and_bounds(self):
        variants = {
            "extra-field": lambda value: value.update(claimed_pass=True),
            "wrong-task": lambda value: value.update(task_id="ANOTHER-001"),
            "wrong-schema": lambda value: value.update(schema_version="other/v1"),
            "lowercase-phase": lambda value: value.update(phase="recover"),
            "extra-content-field": lambda value: value["content"].update(adopted=True),
            "boolean-array-item": lambda value: value["content"].update(known_ideas=[True]),
            "empty-array-item": lambda value: value["content"].update(missing_inputs=["   "]),
            "too-many-items": lambda value: value["content"].update(missing_inputs=["Unknown"] * 101),
            "oversized-string": lambda value: value["content"].update(missing_inputs=["x" * 8193]),
            "multiline-placeholder": lambda value: value["content"].update(missing_inputs=["{{\nTODO\n}}"]),
        }
        for name, mutate in variants.items():
            with self.subTest(case=name):
                f = self.fixture()
                self.begin(f)
                raw = self.checkpoint(f, "Recover", self.recover_content())
                value = json.loads(raw)
                mutate(value)
                f.checkpoint_path.write_bytes(json_bytes(value))
                failure = self.result(phase.advance(f.state), "FAIL")
                self.assertIs(failure.get("terminal"), False)
                self.assertEqual(phase.context(f.state)["phase"], "Recover")

    def test_checkpoint_duplicate_keys_invalid_utf8_and_size_fail(self):
        for mutation in ("duplicate", "utf8", "oversized"):
            with self.subTest(mutation=mutation):
                f = self.fixture()
                self.begin(f)
                raw = self.checkpoint(f, "Recover", self.recover_content())
                if mutation == "duplicate":
                    raw = raw.replace(b'"state": "ready"', b'"state": "ready", "state": "ready"')
                elif mutation == "utf8":
                    raw = b"\xff\xfe"
                else:
                    raw = raw + b" " * (64 * 1024)
                f.checkpoint_path.write_bytes(raw)
                self.result(phase.advance(f.state), "FAIL")

    def test_checkpoint_symlink_and_hardlink_cannot_authorize_transition(self):
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind):
                f = self.fixture()
                self.begin(f)
                raw = self.checkpoint(f, "Recover", self.recover_content())
                target = f.operator / "checkpoint-target.json"
                target.write_bytes(raw)
                f.checkpoint_path.unlink()
                if kind == "symlink":
                    f.checkpoint_path.symlink_to(target)
                else:
                    os.link(target, f.checkpoint_path)
                self.result(phase.advance(f.state), "FAIL")
                self.assertFalse(f.receipt_path.exists())

    def test_unknown_explore_fields_are_allowed_only_with_open_question(self):
        f = self.fixture()
        self.begin(f)
        self.recover(f)
        content = self.explore_content()
        content["ideas"][0].update(people=None, problem=None, outcome=None)
        self.checkpoint(f, "Explore", content)
        self.admitted(phase.advance(f.state), "PROGRESS", "Record")

    def test_explore_rejects_missing_question_and_duplicate_idea_identity(self):
        for variant in ("unanswered-null", "duplicate-identity", "boolean-fact", "no-ideas"):
            with self.subTest(variant=variant):
                f = self.fixture()
                self.begin(f)
                self.recover(f)
                content = self.explore_content()
                if variant == "unanswered-null":
                    content["ideas"][0]["open_questions"] = []
                elif variant == "duplicate-identity":
                    content["ideas"].append(copy.deepcopy(content["ideas"][0]))
                elif variant == "boolean-fact":
                    content["ideas"][0]["people"] = True
                else:
                    content["ideas"] = []
                self.checkpoint(f, "Explore", content)
                self.result(phase.advance(f.state), "FAIL")

    def test_record_uses_actual_selected_ledger_not_claimed_path_or_hash(self):
        for variant in ("wrong-path", "wrong-identity", "placeholder"):
            with self.subTest(variant=variant):
                f = self.fixture()
                self.to_record(f)
                raw = f.write_ledger()
                content = {"ledger_path": "docs/ideas.md"}
                if variant == "wrong-path":
                    content["ledger_path"] = "docs/unselected.md"
                elif variant == "wrong-identity":
                    f.ledger_path.write_bytes(raw.replace(b"IDEAS-001", b"IDEAS-999"))
                else:
                    f.ledger_path.write_bytes(raw + b"\n{{\nUNRESOLVED\n}}\n")
                self.checkpoint(f, "Record", content)
                self.result(phase.advance(f.state), "FAIL")
                self.assertEqual(phase.context(f.state)["phase"], "Record")

    def test_record_snapshot_remains_historical_when_ledger_gains_focus_content(self):
        f = self.fixture()
        self.to_focus(f)
        recorded = f.ledger_path.read_bytes()
        snapshot = self.snapshot_of(f, recorded)
        enriched = f.write_ledger("\n## Focus observation\n\nThe next observation is still a human task.\n")
        self.assertNotEqual(enriched, recorded)
        handoff = f.write_handoff()
        self.checkpoint(f, "Focus", self.focus_content())
        self.result(phase.advance(f.state), "READY", "Focus")
        self.result(phase.complete(f.state), "COMPLETED")
        self.assertEqual(snapshot.read_bytes(), recorded)
        self.assertEqual(json.loads(f.receipt_path.read_bytes())["outputs"],
                         {"docs/ideas.md": digest(enriched), "docs/handoff.md": digest(handoff)})

    def test_focus_requires_selected_handoff_and_current_ledger_binding(self):
        for variant in ("wrong-path", "missing-handoff", "stale-ledger-binding"):
            with self.subTest(variant=variant):
                f = self.fixture()
                self.to_focus(f)
                content = self.focus_content()
                if variant != "missing-handoff":
                    f.write_handoff()
                if variant == "wrong-path":
                    content["handoff_path"] = "docs/unselected.md"
                elif variant == "stale-ledger-binding":
                    f.write_ledger("\nA later independently recorded observation.\n")
                self.checkpoint(f, "Focus", content)
                self.result(phase.advance(f.state), "FAIL")
                self.assertFalse(f.receipt_path.exists())

    def test_complete_before_ready_does_not_publish_or_skip(self):
        f = self.fixture()
        self.begin(f)
        f.write_outputs()
        self.result(phase.complete(f.state), "FAIL")
        context = phase.context(f.state)
        self.assertEqual(context.get("phase"), "Recover")
        self.assertFalse(f.receipt_path.exists())

    def test_fixed_identity_byte_changes_block_continuation(self):
        for target in ("input", "assignment", "installed", "session", "delivery"):
            with self.subTest(target=target):
                f = self.fixture()
                admitted = self.begin(f)
                self.checkpoint(f, "Recover", self.recover_content(), challenge=admitted["challenge"])
                selected = {"input": f.input_path, "assignment": f.assignment_path,
                            "installed": f.installed_path, "session": f.session_path,
                            "delivery": f.delivery_path}[target]
                selected.write_bytes(selected.read_bytes() + b"\n")
                self.result(phase.advance(f.state), "FAIL")
                self.refusal(phase.resume(f.state))
                self.assertFalse(f.receipt_path.exists())

    def test_missing_fixed_input_is_unavailable_and_cannot_advance(self):
        f = self.fixture()
        admitted = self.begin(f)
        self.checkpoint(f, "Recover", self.recover_content(), challenge=admitted["challenge"])
        f.input_path.unlink()
        self.refusal(phase.advance(f.state))
        self.assertFalse(f.receipt_path.exists())

    def test_baseline_archives_preserve_exact_bytes_before_admission(self):
        f = self.fixture()
        preimages = f.seed_baselines()
        self.begin(f)
        for relative, raw in preimages.items():
            self.assertEqual((f.project / relative).read_bytes(), raw)
            self.snapshot_of(f, raw)

    def test_null_baseline_requires_output_absence_at_admission(self):
        f = self.fixture()
        f.write_outputs()
        before = self.tree_bytes(f.project)
        self.result(phase.start(f.session_path, f.state), "FAIL")
        self.assertEqual(self.tree_bytes(f.project), before)
        self.assertFalse(f.receipt_path.exists())

    def test_selected_preimage_missing_at_admission_never_creates_archives(self):
        f = self.fixture()
        f.seed_baselines()
        f.ledger_path.unlink()
        self.refusal(phase.start(f.session_path, f.state))
        self.assertFalse((f.project / "archive").exists())
        self.refusal(phase.context(f.state))

    def test_identical_preexisting_archives_are_accepted_without_overwrite(self):
        f = self.fixture()
        preimages = f.seed_baselines()
        identities = {}
        for rel, raw in preimages.items():
            target = f.project / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            identities[rel] = (target.stat().st_ino, target.stat().st_mtime_ns)
        self.begin(f)
        for rel, raw in preimages.items():
            target = f.project / rel
            self.assertEqual(target.read_bytes(), raw)
            self.assertEqual((target.stat().st_ino, target.stat().st_mtime_ns), identities[rel])

    def test_archive_collision_is_preserved_and_initialization_not_admitted(self):
        f = self.fixture()
        f.seed_baselines()
        target = f.project / f.session["output_baselines"][0]["archive"]
        target.parent.mkdir(parents=True)
        target.write_bytes(b"Existing unrelated archive sentinel\n")
        self.result(phase.start(f.session_path, f.state), "FAIL")
        self.assertEqual(target.read_bytes(), b"Existing unrelated archive sentinel\n")
        self.refusal(phase.context(f.state))

    def test_full_preflight_precedes_archive_directory_creation(self):
        f = self.fixture()
        f.seed_baselines()
        f.session["output_baselines"][0]["archive"] = "not-created/deep/archive.bin"
        f.session["output_baselines"][1]["sha256"] = "0" * 64
        f.write_session()
        self.result(phase.start(f.session_path, f.state), "FAIL")
        self.assertFalse((f.project / "not-created").exists())
        self.assertFalse((f.project / "archive").exists())
        self.refusal(phase.context(f.state))

    def test_changed_or_deleted_archive_blocks_continuation(self):
        for variant in ("changed", "deleted"):
            with self.subTest(variant=variant):
                f = self.fixture()
                f.seed_baselines()
                admitted = self.begin(f)
                self.checkpoint(f, "Recover", self.recover_content(), challenge=admitted["challenge"])
                target = f.project / f.session["output_baselines"][0]["archive"]
                if variant == "changed":
                    target.write_bytes(b"Changed historical archive\n")
                    self.result(phase.advance(f.state), "FAIL")
                else:
                    target.unlink()
                    self.refusal(phase.advance(f.state))
                self.assertFalse(f.receipt_path.exists())

    def test_unchanged_valid_outputs_need_explicit_reuse_selection(self):
        for allowed in (False, True):
            with self.subTest(allow_unchanged=allowed):
                f = self.fixture()
                f.seed_baselines(allow_unchanged=allowed)
                self.to_focus(f, keep_ledger=True)
                self.checkpoint(f, "Focus", self.focus_content())
                expected = "READY" if allowed else "FAIL"
                self.result(phase.advance(f.state), expected)
                if allowed:
                    self.result(phase.complete(f.state), "COMPLETED")
                else:
                    self.assertFalse(f.receipt_path.exists())

    def test_changed_one_output_does_not_excuse_other_undeclared_reuse(self):
        f = self.fixture()
        f.seed_baselines(allow_unchanged=False)
        self.to_focus(f, keep_ledger=True)
        f.write_handoff("\nThis handoff has been revised, but the ledger has not.\n")
        self.checkpoint(f, "Focus", self.focus_content())
        self.result(phase.advance(f.state), "FAIL")
        self.assertFalse(f.receipt_path.exists())

    def test_empty_and_binary_preimages_and_installed_bytes_remain_opaque(self):
        f = self.fixture()
        preimages = f.seed_baselines(raw_values=[b"", b"\x00\xffbinary old output\n"])
        f.installed_path.write_bytes(b"\x00\xffinstalled resource\n")
        f.session["installed_inputs"][0]["sha256"] = digest(f.installed_path.read_bytes())
        f.write_session()
        self.to_ready(f)
        for rel, raw in preimages.items():
            self.assertEqual((f.project / rel).read_bytes(), raw)
        self.result(phase.complete(f.state), "COMPLETED")

    def test_session_strict_schema_and_baseline_selection(self):
        variants = {
            "unknown-top-level": lambda value: value.update(worker_pass=True),
            "missing-required-field": lambda value: value.pop("provider"),
            "wrong-task": lambda value: value.update(task_id="OTHER-001"),
            "wrong-provider": lambda value: value.update(provider="other"),
            "boolean-correction-limit": lambda value: value.update(max_corrections_per_phase=True),
            "wrong-correction-limit": lambda value: value.update(max_corrections_per_phase=2),
            "short-digest": lambda value: value.update(delivery_contract_sha256="abcd"),
            "uppercase-digest": lambda value: value.update(delivery_contract_sha256="A" * 64),
            "missing-baseline": lambda value: value["output_baselines"].pop(),
            "duplicate-baseline": lambda value: value["output_baselines"].append(copy.deepcopy(value["output_baselines"][0])),
            "absent-reuse": lambda value: value["output_baselines"][0].update(allow_unchanged=True),
            "integer-reuse-flag": lambda value: value["output_baselines"][0].update(allow_unchanged=1),
            "absent-with-archive": lambda value: value["output_baselines"][0].update(archive="archive/impossible.bin"),
            "extra-baseline-field": lambda value: value["output_baselines"][0].update(accepted=True),
            "checkpoint-output-collision": lambda value: value.update(checkpoint_path="docs/ideas.md"),
            "checkpoint-output-ancestor": lambda value: value.update(checkpoint_path="docs"),
            "checkpoint-parent-escape": lambda value: value.update(checkpoint_path="../checkpoint.json"),
            "naive-deadline": lambda value: value.update(deadline_utc="2026-09-07T13:00:00"),
            "deadline-now": lambda value: value.update(deadline_utc=NOW.isoformat()),
            "duplicate-installed": lambda value: value["installed_inputs"].append(copy.deepcopy(value["installed_inputs"][0])),
        }
        for name, mutate in variants.items():
            with self.subTest(case=name):
                f = self.fixture()
                mutate(f.session)
                f.write_session()
                self.result(phase.start(f.session_path, f.state), "FAIL")
                self.refusal(phase.context(f.state))
                self.assertFalse(f.receipt_path.exists())

    def test_session_duplicate_json_keys_and_size_limit(self):
        for variant in ("duplicate", "nested-duplicate", "oversized"):
            with self.subTest(variant=variant):
                f = self.fixture()
                raw = f.session_path.read_bytes()
                if variant == "duplicate":
                    raw = raw.replace(b'"provider": "codex"', b'"provider": "codex", "provider": "codex"')
                elif variant == "nested-duplicate":
                    entry = b'"sha256": "' + f.session["assignment"]["sha256"].encode() + b'"'
                    raw = raw.replace(entry, entry + b", " + entry, 1)
                else:
                    raw += b" " * (64 * 1024)
                f.session_path.write_bytes(raw)
                self.result(phase.start(f.session_path, f.state), "FAIL")
                self.refusal(phase.context(f.state))

    def test_archive_collision_topology_is_rejected_before_writes(self):
        for variant in ("output", "checkpoint", "installed", "ancestor", "other-archive"):
            with self.subTest(variant=variant):
                f = self.fixture()
                f.seed_baselines()
                selected = {"output": "docs/handoff.md", "checkpoint": "checkpoints/current.json",
                            "installed": "installed/SKILL.md", "ancestor": "docs",
                            "other-archive": f.session["output_baselines"][1]["archive"]}[variant]
                f.session["output_baselines"][0]["archive"] = selected
                f.write_session()
                before = self.tree_bytes(f.project)
                self.result(phase.start(f.session_path, f.state), "FAIL")
                self.assertEqual(self.tree_bytes(f.project), before)

    def test_state_destination_reuse_or_project_overlap_preserves_existing_bytes(self):
        for variant in ("existing", "inside-project", "project-ancestor", "assignment"):
            with self.subTest(variant=variant):
                f = self.fixture()
                if variant == "existing":
                    target = f.state
                    target.mkdir()
                    (target / "sentinel").write_bytes(b"Do not replace existing state\n")
                elif variant == "inside-project":
                    target = f.project / "state"
                elif variant == "project-ancestor":
                    target = f.root
                else:
                    target = f.assignment_path
                before = self.tree_bytes(f.root)
                self.result(phase.start(f.session_path, target), "FAIL")
                self.assertEqual(self.tree_bytes(f.root), before)

    def test_symlink_state_parent_and_hardlinked_installed_input_fail(self):
        for variant in ("state-parent-symlink", "installed-hardlink"):
            with self.subTest(variant=variant):
                f = self.fixture()
                if variant == "state-parent-symlink":
                    link = f.root / "operator-alias"
                    link.symlink_to(f.operator, target_is_directory=True)
                    state = link / "state"
                else:
                    os.link(f.installed_path, f.operator / "installed-alias")
                    state = f.state
                self.result(phase.start(f.session_path, state), "FAIL")
                self.assertFalse(f.receipt_path.exists())

    def test_assignment_must_be_external_and_installed_resource_cannot_be_mutable_output(self):
        for variant in ("assignment-in-project", "installed-output-overlap"):
            with self.subTest(variant=variant):
                f = self.fixture()
                if variant == "assignment-in-project":
                    f.session["assignment"] = {"path": str(f.input_path), "sha256": digest(f.input_path.read_bytes())}
                else:
                    f.seed_baselines()
                    f.session["installed_inputs"] = [{"path": str(f.ledger_path), "sha256": digest(f.ledger_path.read_bytes())}]
                f.write_session()
                before = self.tree_bytes(f.project)
                self.result(phase.start(f.session_path, f.state), "FAIL")
                self.assertEqual(self.tree_bytes(f.project), before)

    def test_accepted_checkpoint_snapshot_corruption_or_deletion_fails_reopen(self):
        for variant in ("changed", "truncated", "deleted"):
            with self.subTest(variant=variant):
                f = self.fixture()
                self.begin(f)
                checkpoint = self.checkpoint(f, "Recover", self.recover_content())
                self.result(phase.advance(f.state), "PROGRESS")
                snapshot = self.snapshot_of(f, checkpoint)
                if variant == "changed":
                    snapshot.write_bytes(checkpoint + b"\n")
                elif variant == "truncated":
                    snapshot.write_bytes(b"{")
                else:
                    snapshot.unlink()
                self.result(phase.context(f.state), "FAIL")
                self.refusal(phase.resume(f.state))
                self.assertFalse(f.receipt_path.exists())

    def test_record_artifact_snapshot_corruption_fails_despite_valid_live_files(self):
        f = self.fixture()
        self.to_focus(f)
        recorded = f.ledger_path.read_bytes()
        snapshot = self.snapshot_of(f, recorded)
        snapshot.write_bytes(recorded + b"\nCorrupt protected historical bytes\n")
        f.write_handoff()
        self.result(phase.context(f.state), "FAIL")
        self.refusal(phase.complete(f.state))

    def test_missing_durable_state_records_cannot_reset_task(self):
        f = self.fixture()
        self.begin(f)
        records = [path for path in f.state.rglob("*") if path.is_file() and not path.is_symlink()]
        self.assertTrue(records, "Admission must produce durable state")
        for path in records:
            path.unlink()
        self.assertTrue(f.state.is_dir())
        self.result(phase.context(f.state), "FAIL")
        self.refusal(phase.resume(f.state))
        self.refusal(phase.start(f.session_path, f.state))

    def test_changed_final_bytes_after_ready_cannot_publish(self):
        f = self.fixture()
        self.to_ready(f)
        f.handoff_path.write_bytes(f.handoff_path.read_bytes() + b"\nA later valid-looking addition.\n")
        self.result(phase.complete(f.state), "FAIL")
        self.assertFalse(f.receipt_path.exists())

    def test_receipt_collision_at_start_or_without_completion_intent_is_preserved(self):
        for stage in ("start", "ready"):
            with self.subTest(stage=stage):
                f = self.fixture()
                if stage == "ready":
                    self.to_ready(f)
                sentinel = b'{"unrelated":"existing receipt"}\n'
                f.receipt_path.write_bytes(sentinel)
                result = phase.start(f.session_path, f.state) if stage == "start" else phase.complete(f.state)
                self.result(result, "FAIL")
                self.assertEqual(f.receipt_path.read_bytes(), sentinel)

    def publish_then_interrupt(self, f: Fixture) -> bytes:
        real_finalize = phase.delivery_core.finalize

        def interrupted(contract, receipt):
            actual = real_finalize(contract, receipt)
            self.assertEqual(actual["result"], "PASS", actual)
            raise OSError("Synthetic interruption after receipt publication/readback")

        with mock.patch.object(phase.delivery_core, "finalize", side_effect=interrupted) as publish:
            self.result(phase.complete(f.state), "COULD_NOT_RUN")
            self.assertEqual(publish.call_count, 1)
        self.assertTrue(f.receipt_path.is_file())
        return f.receipt_path.read_bytes()

    def test_interrupted_completion_recovers_exact_same_receipt_without_republication(self):
        f = self.fixture()
        self.to_ready(f)
        original = self.publish_then_interrupt(f)
        with mock.patch.object(phase.delivery_core, "finalize", side_effect=AssertionError("Must reuse existing bound receipt")):
            complete = self.result(phase.complete(f.state), "COMPLETED")
        self.assertEqual(f.receipt_path.read_bytes(), original)
        self.assertEqual(complete["receipt_path"], str(f.receipt_path))
        self.assertEqual(complete["receipt_sha256"], digest(original))

    def test_interrupted_completion_refuses_wrong_bound_existing_receipt(self):
        f = self.fixture()
        self.to_ready(f)
        original = self.publish_then_interrupt(f)
        altered = json.loads(original)
        altered["task_id"] = "WRONG-TASK-001"
        bad = json_bytes(altered)
        f.receipt_path.write_bytes(bad)
        with mock.patch.object(phase.delivery_core, "finalize", side_effect=AssertionError("Never overwrite wrong receipt")):
            self.result(phase.complete(f.state), "FAIL")
        self.assertEqual(f.receipt_path.read_bytes(), bad)

    def test_repeated_completion_verifies_and_retains_receipt_identity(self):
        f = self.fixture()
        self.to_ready(f)
        first = self.result(phase.complete(f.state), "COMPLETED")
        original = f.receipt_path.read_bytes()
        with mock.patch.object(phase.delivery_core, "finalize", side_effect=AssertionError("No second publication")):
            second = self.result(phase.complete(f.state), "COMPLETED")
        self.assertEqual(second["receipt_path"], first["receipt_path"])
        self.assertEqual(second["receipt_sha256"], first["receipt_sha256"])
        self.assertEqual(f.receipt_path.read_bytes(), original)
        self.result(phase.context(f.state), "COMPLETED")

    def test_completed_state_still_rechecks_current_outputs_and_fixed_inputs(self):
        for variant in ("output", "input"):
            with self.subTest(variant=variant):
                f = self.fixture()
                self.to_ready(f)
                self.result(phase.complete(f.state), "COMPLETED")
                receipt = f.receipt_path.read_bytes()
                target = f.handoff_path if variant == "output" else f.input_path
                target.write_bytes(target.read_bytes() + b"\nChanged after completed delivery\n")
                with mock.patch.object(phase.delivery_core, "finalize", side_effect=AssertionError("No new publication")):
                    self.result(phase.complete(f.state), "FAIL")
                self.assertEqual(f.receipt_path.read_bytes(), receipt)

    def test_completed_receipt_relocated_to_another_path_cannot_be_recreated(self):
        f = self.fixture()
        self.to_ready(f)
        self.result(phase.complete(f.state), "COMPLETED")
        other = f.operator / "copied-receipt.json"
        f.receipt_path.rename(other)
        with mock.patch.object(phase.delivery_core, "finalize", side_effect=AssertionError("No replacement after completion")):
            self.refusal(phase.complete(f.state))
        self.assertFalse(f.receipt_path.exists())
        self.assertTrue(other.exists())

    def test_expiry_prevents_new_transition_and_context_does_not_reset_deadline(self):
        f = self.fixture()
        admitted = self.begin(f)
        self.checkpoint(f, "Recover", self.recover_content(), challenge=admitted["challenge"])
        original = f.session_path.read_bytes()
        self.clock.return_value = NOW + timedelta(hours=1)
        self.refusal(phase.advance(f.state))
        context = phase.context(f.state)
        self.assertNotIn(context.get("status"), {"PROGRESS", "READY", "COMPLETED"})
        self.assertEqual(f.session_path.read_bytes(), original)
        self.assertFalse(f.receipt_path.exists())

    def test_wait_and_resume_do_not_extend_deadline(self):
        f = self.fixture()
        self.begin(f)
        self.checkpoint(f, "Recover", {"question": "Which group?", "blocking_dependency": "Group is unknown."},
                        state="awaiting_user")
        self.result(phase.advance(f.state), "WAITING_USER")
        original = f.session_path.read_bytes()
        self.clock.return_value = NOW + timedelta(hours=1)
        self.refusal(phase.resume(f.state))
        self.assertEqual(f.session_path.read_bytes(), original)
        self.assertFalse(f.receipt_path.exists())

    def test_expiry_before_first_completion_never_calls_finalizer(self):
        f = self.fixture()
        self.to_ready(f)
        self.clock.return_value = NOW + timedelta(hours=1)
        with mock.patch.object(phase.delivery_core, "finalize", side_effect=AssertionError("No post-deadline finalization")):
            self.refusal(phase.complete(f.state))
        self.assertFalse(f.receipt_path.exists())

    def test_expired_recovery_may_verify_predeadline_published_receipt(self):
        f = self.fixture()
        self.to_ready(f)
        original = self.publish_then_interrupt(f)
        self.clock.return_value = NOW + timedelta(hours=2)
        with mock.patch.object(phase.delivery_core, "finalize", side_effect=AssertionError("Recovery must only verify")):
            self.result(phase.complete(f.state), "COMPLETED")
        self.assertEqual(f.receipt_path.read_bytes(), original)

    def test_expired_unpublished_intent_cannot_start_new_finalization(self):
        f = self.fixture()
        self.to_ready(f)
        with mock.patch.object(phase.delivery_core, "finalize", side_effect=OSError("Synthetic failure before publication")):
            self.result(phase.complete(f.state), "COULD_NOT_RUN")
        self.assertFalse(f.receipt_path.exists())
        self.clock.return_value = NOW + timedelta(hours=2)
        with mock.patch.object(phase.delivery_core, "finalize", side_effect=AssertionError("Expired intent must not publish")):
            self.refusal(phase.complete(f.state))
        self.assertFalse(f.receipt_path.exists())

    def test_unavailable_clock_returns_typed_failure_before_admission(self):
        f = self.fixture()
        self.clock.side_effect = OSError("Synthetic clock prerequisite unavailable")
        self.result(phase.start(f.session_path, f.state), "COULD_NOT_RUN")
        self.assertFalse(f.receipt_path.exists())

    def test_advance_terminal_views_revalidate_current_artifacts(self):
        for completed in (False, True):
            with self.subTest(completed=completed):
                f = self.fixture()
                self.to_ready(f)
                if completed:
                    self.result(phase.complete(f.state), "COMPLETED")
                f.handoff_path.write_bytes(f.handoff_path.read_bytes() + b"\nLater unreviewed edit.\n")
                self.result(phase.advance(f.state), "FAIL")

    def test_receipt_changed_after_readback_cannot_commit_completion(self):
        f = self.fixture()
        self.to_ready(f)
        verify_current = phase._verify_current
        changed = []

        def mutate_receipt_after_current_check(state):
            result = verify_current(state)
            if not changed and state.intent is not None and state.status == "READY" and f.receipt_path.exists():
                original = f.receipt_path.read_bytes()
                f.receipt_path.write_bytes(original + b" ")
                changed.append(original)
            return result

        with mock.patch.object(phase, "_verify_current", side_effect=mutate_receipt_after_current_check):
            self.result(phase.complete(f.state), "FAIL")
        self.assertEqual(len(changed), 1)
        self.assertEqual(f.receipt_path.read_bytes(), changed[0] + b" ")
        self.assertNotEqual(phase.context(f.state).get("status"), "COMPLETED")


if __name__ == "__main__":
    unittest.main()
