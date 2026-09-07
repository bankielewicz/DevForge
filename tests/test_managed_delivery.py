"""Independent fixtures for the pinned managed-completion contract.

Authored from MANAGED-COMPLETION-CONTRACT.md (898ab0fc...fab5), its two
fixed API/admission clarifications, and preserved baseline APIs. No new
supervisor or hook implementation was inspected to choose these expectations.
These tests use local fixtures, broker calls, inert transport/process doubles,
and existing phase/core logic. They establish neither native execution nor TUI
rendering. Root owns execution; this file was syntax-checked only by its author.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import mock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_delivery_runtime as support  # noqa: E402

supervisor = support.supervisor
phase = support.phase
hooks = support.hooks
digest = support.digest
json_bytes = support.json_bytes


class ManagedFixtures(support.RuntimeFixtureTests):
    def broker(self, fixture, provider="codex", *, completion_mode="managed-session", start=True,
               observations=None):
        if start:
            fixture.session["provider"] = provider
            fixture.write_session()
            self.assertEqual(phase.start(fixture.session_path, fixture.state)["status"], "ACTIVE")
        if observations is None:
            observations = fixture.operator / "observations"
        observations.mkdir(exist_ok=True)
        broker = supervisor.Broker(fixture.state, fixture.session, fixture.delivery,
                                   digest(fixture.session_path.read_bytes()), observations,
                                   completion_mode=completion_mode)
        self.addCleanup(lambda: broker.close() if broker.socket_root.exists() else None)
        return broker

    @staticmethod
    def content(name):
        author = support.fixture_support.PhaseStateTests
        return {"Recover": author.recover_content, "Explore": author.explore_content,
                "Record": lambda: {"ledger_path": "docs/ideas.md"}, "Focus": author.focus_content}[name]()

    def to_focus(self, fixture, broker):
        self.callback(fixture, broker, "SessionStart")
        for name in (("Recover", "Explore", "Record") if fixture.mode == "brainstorm" else ("Recover",)):
            if name == "Record":
                fixture.write_ledger()
            self.checkpoint(fixture, name, self.content(name))
            reply = self.callback(fixture, broker, "Stop")
            self.assertEqual(reply.get("decision"), "block", reply)
            self.assertFalse(fixture.receipt_path.exists())
            self.assertIsNone(broker.task_result)
        self.assertEqual(phase.context(fixture.state)["phase"], "Focus")

    def focus_checkpoint(self, fixture):
        fixture.write_handoff()
        self.checkpoint(fixture, "Focus", self.content("Focus"))

    def committed(self, fixture, broker):
        self.to_focus(fixture, broker)
        self.focus_checkpoint(fixture)
        reply = self.callback(fixture, broker, "Stop")
        self.verified_reply(reply, fixture)
        self.assertEqual(broker.task_result["status"], "COMPLETED")
        return reply

    def verified_reply(self, reply, fixture):
        self.assertEqual(set(reply), {"systemMessage"}, reply)
        text = reply["systemMessage"]
        self.assertIn(fixture.session["task_id"], text)
        self.assertIn(str(fixture.receipt_path), text)
        self.assertIn(digest(fixture.receipt_path.read_bytes()), text)
        self.assertIn("mechanical task receipt verified", text.lower())
        self.assertIn("native turn completion NOT_OBSERVED", text)
        self.assertIn("semantic acceptance NOT_EVALUATED", text)
        self.assertLessEqual(len(json.dumps(reply).encode("utf-8")), 8192)

    def no_verified_reply(self, reply):
        self.assertIsInstance(reply, dict)
        self.assertNotIn("mechanical task receipt verified", reply.get("systemMessage", "").lower())

    @staticmethod
    def read_document(path):
        return json.loads(path.read_bytes())

    def attempt(self, fixture, broker, reply, sequence=None):
        if sequence is None:
            sequence = broker.count
        path = broker.observations / f"delivery-attempt-{sequence:06d}.json"
        value = self.read_document(path)
        self.assertEqual(set(value), {"schema_version", "sequence", "task_id", "contract_sha256",
                                     "completion_mode", "attempted_at_utc", "receipt_path", "receipt_sha256",
                                     "response_sha256", "user_delivery"})
        self.assertEqual(value["schema_version"], "devforge.managed-delivery-attempt/v1")
        self.assertEqual(value["sequence"], sequence)
        self.assertEqual(value["task_id"], fixture.session["task_id"])
        self.assertEqual(value["contract_sha256"], digest(fixture.session_path.read_bytes()))
        self.assertEqual(value["completion_mode"], "managed-session")
        self.assertEqual(value["receipt_path"], str(fixture.receipt_path))
        self.assertEqual(value["receipt_sha256"], digest(fixture.receipt_path.read_bytes()))
        self.assertEqual(value["response_sha256"], digest(json.dumps(reply, sort_keys=True).encode()))
        self.assertEqual(value["user_delivery"], "NOT_OBSERVED")
        self.assertEqual(datetime.fromisoformat(value["attempted_at_utc"]).utcoffset(), timedelta(0))
        event = self.read_document(broker.observations / f"event-{sequence:06d}.json")
        self.assertEqual(event["delivery_attempt_sha256"], digest(path.read_bytes()))
        self.assertEqual(event["response_sha256"], value["response_sha256"])
        return path, value

    def stored_task(self, fixture, broker):
        path = broker.observations / "task-result.json"
        value = self.read_document(path)
        self.assertEqual(set(value), {"schema_version", "task_id", "contract_sha256", "recorded_at_utc", "result"})
        self.assertEqual(value["schema_version"], "devforge.managed-task-result/v1")
        self.assertEqual(value["task_id"], fixture.session["task_id"])
        self.assertEqual(value["contract_sha256"], digest(fixture.session_path.read_bytes()))
        self.assertEqual(value["result"], broker.task_result)
        self.assertEqual(value["result"]["receipt_path"], str(fixture.receipt_path))
        self.assertEqual(value["result"]["receipt_sha256"], digest(fixture.receipt_path.read_bytes()))
        self.assertEqual(datetime.fromisoformat(value["recorded_at_utc"]).utcoffset(), timedelta(0))
        self.assertFalse(path.is_relative_to(fixture.project))
        return path, value

    def one_transport(self, fixture, broker, *, fail=False):
        """One inert connection; sendall assertions inspect persistence ordering."""
        raw_request = json.dumps(self.request(fixture, broker, "Stop")).encode() + b"\n"
        original_server = broker.server
        original_server.close()
        captured = {"accepts": 0, "sends": 0}
        owner = self

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def settimeout(self, seconds):
                pass

            def recv(self, size):
                raw, captured["request_read"] = (raw_request if not captured.get("request_read") else b""), True
                return raw

            def sendall(self, wire):
                captured["sends"] += 1
                captured["wire"] = wire
                reply = json.loads(wire)
                owner.verified_reply(reply, fixture)
                owner.stored_task(fixture, broker)
                owner.attempt(fixture, broker, reply)
                owner.assertFalse(list(broker.observations.glob("transport-*.json")))
                if fail:
                    raise BrokenPipeError("Synthetic socket write failed after response preparation")

        def accept():
            captured["accepts"] += 1
            if captured["accepts"] == 1:
                return Connection(), None
            broker.stopping.set()
            raise OSError("Synthetic one-connection fixture ended")

        broker.server = SimpleNamespace(accept=accept, close=lambda: None)
        broker.serve()
        self.assertEqual(captured["sends"], 1)
        sequence = broker.count
        transport_path = broker.observations / f"transport-{sequence:06d}.json"
        value = self.read_document(transport_path)
        self.assertEqual(set(value), {"schema_version", "sequence", "delivery_attempt_sha256", "event_sha256",
                                     "response_sha256", "wire_sha256", "observed_at_utc", "transport_status",
                                     "user_delivery", "issues"})
        self.assertEqual(value["schema_version"], "devforge.managed-delivery-transport/v1")
        self.assertEqual(value["sequence"], sequence)
        attempt_path = broker.observations / f"delivery-attempt-{sequence:06d}.json"
        event_path = broker.observations / f"event-{sequence:06d}.json"
        self.assertEqual(value["delivery_attempt_sha256"], digest(attempt_path.read_bytes()))
        self.assertEqual(value["event_sha256"], digest(event_path.read_bytes()))
        self.assertEqual(value["response_sha256"], digest(json.dumps(json.loads(captured["wire"]), sort_keys=True).encode()))
        self.assertEqual(value["wire_sha256"], digest(captured["wire"]))
        self.assertEqual(value["transport_status"], "SOCKET_WRITE_FAILED" if fail else "SOCKET_WRITE_COMPLETED")
        self.assertEqual(value["user_delivery"], "NOT_OBSERVED")
        self.assertEqual(bool(value["issues"]), fail)
        self.assertEqual(datetime.fromisoformat(value["observed_at_utc"]).utcoffset(), timedelta(0))
        self.assertGreaterEqual(datetime.fromisoformat(value["observed_at_utc"]),
                                datetime.fromisoformat(self.read_document(attempt_path)["attempted_at_utc"]))
        return captured, value


class ManagedBrokerTests(ManagedFixtures):
    def test_both_providers_commit_full_ordered_path_before_handle_returns(self):
        for provider in ("codex", "claude"):
            with self.subTest(provider=provider):
                f = self.fixture()
                b = self.broker(f, provider)
                self.assertIsNone(b.task_result)
                with mock.patch.object(supervisor, "stop_owned", side_effect=AssertionError("Commit cannot wait for process cleanup")):
                    reply = self.committed(f, b)
                self.stored_task(f, b)
                self.attempt(f, b, reply)
                self.assertFalse(list(b.observations.glob("transport-*.json")))
                self.assertEqual(phase.context(f.state)["status"], "COMPLETED")

    def test_default_process_mode_reaches_ready_without_receipt(self):
        for provider in ("codex", "claude"):
            with self.subTest(provider=provider):
                f = self.fixture()
                b = self.broker(f, provider, completion_mode="process")
                self.to_focus(f, b)
                self.focus_checkpoint(f)
                self.assertEqual(self.callback(f, b, "Stop"), {})
                self.assertFalse(f.receipt_path.exists())
                self.assertIsNone(b.task_result)
                self.assertEqual(phase.context(f.state)["status"], "READY")
                self.assertFalse(list(b.observations.glob("delivery-attempt-*.json")))

    def test_ready_without_valid_stop_is_not_initial_publication_authority(self):
        f = self.fixture()
        b = self.broker(f)
        for name in ("Recover", "Explore", "Record", "Focus"):
            if name == "Record":
                f.write_ledger()
            if name == "Focus":
                f.write_handoff()
            self.checkpoint(f, name, self.content(name))
            self.assertIn(phase.advance(f.state)["status"], {"PROGRESS", "READY"})
        with mock.patch.object(phase, "complete", side_effect=AssertionError("Only Stop may first commit")):
            for name in ("SessionStart", "UserPromptSubmit"):
                self.no_verified_reply(self.callback(f, b, name))
                self.assertFalse(f.receipt_path.exists())
                self.assertIsNone(b.task_result)
        self.verified_reply(self.callback(f, b, "Stop"), f)

    def test_files_and_claimed_pass_with_future_phase_cannot_commit(self):
        f = self.fixture()
        b = self.broker(f)
        self.callback(f, b, "SessionStart")
        f.write_outputs()
        self.checkpoint(f, "Focus", self.content("Focus"))
        self.assertEqual(self.callback(f, b, "Stop", last_assistant_message="PASS all phases complete").get("decision"), "block")
        second = self.callback(f, b, "Stop", last_assistant_message="PASS all phases complete")
        self.assertIs(second.get("continue"), False)
        self.assertIsNone(b.task_result)
        self.assertFalse(f.receipt_path.exists())

    def test_worker_event_cannot_select_managed_completion_for_process_broker(self):
        f = self.fixture("handoff-only")
        b = self.broker(f, completion_mode="process")
        self.callback(f, b, "SessionStart", completion_mode="managed-session")
        self.checkpoint(f, "Recover", self.content("Recover"))
        self.callback(f, b, "Stop", completion_mode="managed-session")
        self.focus_checkpoint(f)
        self.assertEqual(self.callback(f, b, "Stop", completion_mode="managed-session"), {})
        self.assertIsNone(b.task_result)
        self.assertFalse(f.receipt_path.exists())

    def test_wait_resume_and_one_correction_preserve_phase_and_budget(self):
        f = self.fixture()
        b = self.broker(f)
        self.callback(f, b, "SessionStart")
        initial = phase.context(f.state)
        invalid = {"known_ideas": [], "known_decisions": [], "missing_inputs": []}
        self.checkpoint(f, "Recover", invalid)
        self.assertEqual(self.callback(f, b, "Stop").get("decision"), "block")
        self.checkpoint(f, "Recover", {"question": "Which group?", "blocking_dependency": "The group is unknown."}, state="awaiting_user")
        self.assertIs(self.callback(f, b, "Stop").get("continue"), False)
        self.assertEqual(phase.context(f.state)["status"], "WAITING_USER")
        reply = self.callback(f, b, "UserPromptSubmit")
        self.assertIn("not a human", reply["hookSpecificOutput"]["additionalContext"])
        resumed = phase.context(f.state)
        self.assertEqual(resumed["phase"], "Recover")
        self.assertNotEqual(resumed["challenge"], initial["challenge"])
        self.assertEqual(resumed["deadline_utc"], initial["deadline_utc"])
        self.checkpoint(f, "Recover", invalid)
        self.assertIs(self.callback(f, b, "Stop").get("continue"), False)
        self.assertIsNone(b.task_result)
        self.assertFalse(f.receipt_path.exists())

    def test_preobserved_interrupt_error_or_shutdown_prevents_commit(self):
        for cause in ("interrupt", "service-error", "shutdown"):
            with self.subTest(cause=cause):
                f = self.fixture("handoff-only")
                b = self.broker(f)
                self.to_focus(f, b)
                self.focus_checkpoint(f)
                if cause == "interrupt":
                    self.assertEqual(self.callback(f, b, "Interrupt"), {})
                elif cause == "service-error":
                    b.error = "Synthetic observed service failure"
                else:
                    b.stopping.set()
                with mock.patch.object(phase, "complete", side_effect=AssertionError("Observed cancellation precludes commit")):
                    self.no_verified_reply(self.callback(f, b, "Stop"))
                self.assertIsNone(b.task_result)
                self.assertFalse(f.receipt_path.exists())

    def test_advisory_events_do_not_publish_or_reemit_verified_receipt(self):
        for provider, names in (("codex", ("Interrupt", "SessionEnd")), ("claude", ("SessionEnd",))):
            for completed in (False, True):
                for name in names:
                    with self.subTest(provider=provider, completed=completed, event=name):
                        f = self.fixture("handoff-only")
                        b = self.broker(f, provider)
                        if completed:
                            self.committed(f, b)
                        else:
                            self.to_focus(f, b)
                            self.focus_checkpoint(f)
                        prior = copy.deepcopy(b.task_result)
                        attempts = list(b.observations.glob("delivery-attempt-*.json"))
                        with mock.patch.object(phase, "complete", side_effect=AssertionError("Advisory event cannot publish")):
                            self.assertEqual(self.callback(f, b, name), {})
                        self.assertEqual(b.task_result, prior)
                        self.assertEqual(list(b.observations.glob("delivery-attempt-*.json")), attempts)
                        self.assertEqual(f.receipt_path.exists(), completed)

    def test_later_invalid_callback_does_not_erase_historical_task_result(self):
        f = self.fixture("handoff-only")
        b = self.broker(f)
        self.committed(f, b)
        original = copy.deepcopy(b.task_result)
        receipt_raw = f.receipt_path.read_bytes()
        request = self.request(f, b, "Stop")
        request["contract_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            b.handle(request)
        self.assertEqual(b.task_result, original)
        self.assertEqual(f.receipt_path.read_bytes(), receipt_raw)

    def test_reemission_revalidates_same_receipt_with_fresh_attempt(self):
        for provider in ("codex", "claude"):
            with self.subTest(provider=provider), mock.patch.object(supervisor, "datetime", wraps=datetime) as clock:
                ticks = iter(range(1000))
                clock.now.side_effect = lambda tz=None: support.fixture_support.NOW + timedelta(milliseconds=next(ticks))
                f = self.fixture()
                b = self.broker(f, provider)
                first = self.committed(f, b)
                _, earlier = self.attempt(f, b, first)
                receipt_raw = f.receipt_path.read_bytes()
                original = copy.deepcopy(b.task_result)
                task_raw = (b.observations / "task-result.json").read_bytes()
                initial = phase.context(f.state)
                fixed = {key: initial.get(key) for key in ("task_id", "phase", "challenge", "corrections", "deadline_utc")}
                for event in ("SessionStart", "UserPromptSubmit", "Stop"):
                    with mock.patch.object(phase, "complete", wraps=phase.complete) as verify, \
                            mock.patch.object(phase.delivery_core, "finalize", side_effect=AssertionError("Existing receipt cannot be republished")):
                        reply = self.callback(f, b, event)
                    verify.assert_called_once_with(f.state)
                    self.verified_reply(reply, f)
                    _, current = self.attempt(f, b, reply)
                    self.assertGreater(current["sequence"], earlier["sequence"])
                    self.assertGreater(datetime.fromisoformat(current["attempted_at_utc"]), datetime.fromisoformat(earlier["attempted_at_utc"]))
                    self.assertEqual(f.receipt_path.read_bytes(), receipt_raw)
                    self.assertEqual(b.task_result, original)
                    self.assertEqual((b.observations / "task-result.json").read_bytes(), task_raw)
                    current_state = phase.context(f.state)
                    self.assertEqual(current_state["status"], "COMPLETED")
                    self.assertEqual({key: current_state.get(key) for key in fixed}, fixed)
                    earlier = current

    def test_same_verified_persisted_task_result_can_be_reopened_without_overwrite(self):
        f = self.fixture("handoff-only")
        b = self.broker(f)
        self.committed(f, b)
        original = copy.deepcopy(b.task_result)
        task_raw = (b.observations / "task-result.json").read_bytes()
        receipt_raw = f.receipt_path.read_bytes()
        b.close()
        observations = f.operator / "reopened-observations"
        observations.mkdir()
        (observations / "task-result.json").write_bytes(task_raw)
        reopened = self.broker(f, start=False, observations=observations)
        with mock.patch.object(phase.delivery_core, "finalize", side_effect=AssertionError("Reopening cannot republish")):
            self.verified_reply(self.callback(f, reopened, "SessionStart"), f)
        self.assertEqual(reopened.task_result, original)
        self.assertEqual((observations / "task-result.json").read_bytes(), task_raw)
        self.assertEqual(f.receipt_path.read_bytes(), receipt_raw)

    def test_current_drift_warns_without_replacement_or_new_verified_attempt(self):
        for event in ("SessionStart", "UserPromptSubmit", "Stop"):
            for target in ("output", "input", "receipt"):
                with self.subTest(event=event, target=target):
                    f = self.fixture("handoff-only")
                    b = self.broker(f)
                    self.committed(f, b)
                    original = copy.deepcopy(b.task_result)
                    target_path = {"output": f.handoff_path, "input": f.input_path, "receipt": f.receipt_path}[target]
                    target_path.write_bytes(target_path.read_bytes() + b"\n")
                    changed_raw = target_path.read_bytes()
                    receipt_raw = f.receipt_path.read_bytes()
                    before = list(b.observations.glob("delivery-attempt-*.json"))
                    with mock.patch.object(phase.delivery_core, "finalize", side_effect=AssertionError("Drift cannot create a replacement")):
                        reply = self.callback(f, b, event)
                    self.no_verified_reply(reply)
                    text = reply.get("systemMessage", "").lower()
                    self.assertIn("current applicability", text)
                    self.assertIn("could not be verified", text)
                    self.assertTrue(any(word in text for word in ("changed", "differs", "hash", "receipt", "input", "output")))
                    self.assertNotEqual(reply.get("decision"), "block")
                    self.assertIsNot(reply.get("continue"), False)
                    self.assertEqual(b.task_result, original)
                    self.assertEqual(target_path.read_bytes(), changed_raw)
                    self.assertEqual(f.receipt_path.read_bytes(), receipt_raw)
                    self.assertEqual(list(b.observations.glob("delivery-attempt-*.json")), before)

    def test_drift_warning_includes_the_actual_verification_issue(self):
        f = self.fixture("handoff-only")
        b = self.broker(f)
        self.committed(f, b)
        original = copy.deepcopy(b.task_result)
        refused = {**original, "status": "FAIL", "receipt_verified": False,
                   "issues": ["MANAGED-UNIQUE-ISSUE: selected output bytes changed after verified commit"]}
        with mock.patch.object(phase, "complete", return_value=refused):
            reply = self.callback(f, b, "UserPromptSubmit")
        self.no_verified_reply(reply)
        self.assertIn(refused["issues"][0], reply.get("systemMessage", ""))
        self.assertEqual(b.task_result, original)

    def test_published_but_unread_completion_recovers_same_bytes_without_finalizing_again(self):
        f = self.fixture("handoff-only")
        b = self.broker(f)
        self.to_focus(f, b)
        self.focus_checkpoint(f)
        original_finalize = phase.delivery_core.finalize

        def publish_then_fail(*args, **kwargs):
            result = original_finalize(*args, **kwargs)
            self.assertTrue(f.receipt_path.exists(), result)
            raise OSError("Synthetic failure after publication before completed readback")

        with mock.patch.object(phase.delivery_core, "finalize", side_effect=publish_then_fail):
            self.no_verified_reply(self.callback(f, b, "Stop"))
        self.assertTrue(f.receipt_path.exists())
        receipt_raw = f.receipt_path.read_bytes()
        self.assertIsNone(b.task_result)
        self.assertFalse(list(b.observations.glob("delivery-attempt-*.json")))
        b.close()
        recovered = self.broker(f, start=False, observations=f.operator / "recovery-observations")
        self.no_verified_reply(self.callback(f, recovered, "SessionStart"))
        with mock.patch.object(phase.delivery_core, "finalize", side_effect=AssertionError("Recovery must verify existing receipt")):
            reply = self.callback(f, recovered, "Stop")
        self.verified_reply(reply, f)
        self.assertEqual(f.receipt_path.read_bytes(), receipt_raw)
        self.stored_task(f, recovered)

    def test_commit_failure_before_publication_has_no_verified_reply(self):
        f = self.fixture("handoff-only")
        b = self.broker(f)
        self.to_focus(f, b)
        self.focus_checkpoint(f)
        with mock.patch.object(phase.delivery_core, "finalize", side_effect=OSError("Synthetic publish failure")):
            self.no_verified_reply(self.callback(f, b, "Stop"))
        self.assertFalse(f.receipt_path.exists())
        self.assertIsNone(b.task_result)
        self.assertFalse(list(b.observations.glob("delivery-attempt-*.json")))

    def test_completed_result_persistence_precedes_delivery_attempt(self):
        f = self.fixture("handoff-only")
        b = self.broker(f)
        self.to_focus(f, b)
        self.focus_checkpoint(f)
        original_write = supervisor.write_new
        observed = []

        def write(path, value):
            if path.name.startswith("delivery-attempt-"):
                self.assertTrue((b.observations / "task-result.json").is_file())
                self.assertEqual(self.read_document(b.observations / "task-result.json")["result"], b.task_result)
                observed.append("attempt")
            if path.name == "task-result.json":
                observed.append("task")
            return original_write(path, value)

        with mock.patch.object(supervisor, "write_new", side_effect=write):
            self.verified_reply(self.callback(f, b, "Stop"), f)
        self.assertEqual(observed, ["task", "attempt"])

    def test_occupied_foreign_task_result_is_not_adopted_or_overwritten(self):
        f = self.fixture("handoff-only")
        b = self.broker(f)
        self.to_focus(f, b)
        self.focus_checkpoint(f)
        path = b.observations / "task-result.json"
        raw = json_bytes({"schema_version": "devforge.managed-task-result/v1", "task_id": "UNRELATED-TASK",
                          "contract_sha256": "0" * 64, "recorded_at_utc": "2026-09-07T00:00:00+00:00",
                          "result": {"status": "COMPLETED", "receipt_path": "/unrelated", "receipt_sha256": "0" * 64}})
        path.write_bytes(raw)
        try:
            reply = self.callback(f, b, "Stop")
        except (OSError, ValueError):
            reply = None
        if reply is not None:
            self.no_verified_reply(reply)
        self.assertEqual(path.read_bytes(), raw)
        self.assertFalse(list(b.observations.glob("delivery-attempt-*.json")))
        if b.task_result is not None:
            self.assertNotEqual(b.task_result["receipt_sha256"], "0" * 64)

    def test_task_result_persistence_failure_withholds_verified_delivery_and_preserves_commit(self):
        f = self.fixture("handoff-only")
        b = self.broker(f)
        self.to_focus(f, b)
        self.focus_checkpoint(f)
        actual_complete = phase.complete
        actual_write = supervisor.write_new
        completed = []

        def complete(*args, **kwargs):
            result = actual_complete(*args, **kwargs)
            completed.append(copy.deepcopy(result))
            return result

        def write(path, value):
            if path.name == "task-result.json":
                raise OSError("Synthetic external task-result persistence failure")
            return actual_write(path, value)

        with mock.patch.object(phase, "complete", side_effect=complete), mock.patch.object(supervisor, "write_new", side_effect=write):
            try:
                reply = self.callback(f, b, "Stop")
            except OSError:
                reply = None
        if reply is not None:
            self.no_verified_reply(reply)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["status"], "COMPLETED")
        self.assertEqual(b.task_result, completed[0])
        self.assertTrue(f.receipt_path.exists())
        self.assertFalse(list(b.observations.glob("delivery-attempt-*.json")))
        self.assertFalse(list(b.observations.glob("transport-*.json")))

    def test_response_construction_failure_retains_commit_without_delivery_attempt(self):
        f = self.fixture("handoff-only")
        b = self.broker(f)
        self.to_focus(f, b)
        self.focus_checkpoint(f)
        with mock.patch.object(hooks, "completion_response", side_effect=ValueError("Synthetic serialized response exceeds 8192 bytes")):
            self.no_verified_reply(self.callback(f, b, "Stop"))
        self.assertTrue(f.receipt_path.exists())
        self.assertEqual(b.task_result["status"], "COMPLETED")
        self.assertEqual(b.task_result["receipt_sha256"], digest(f.receipt_path.read_bytes()))
        self.assertFalse(list(b.observations.glob("delivery-attempt-*.json")))

    def test_successful_send_records_socket_write_only_for_both_providers(self):
        for provider in ("codex", "claude"):
            with self.subTest(provider=provider):
                f = self.fixture("handoff-only")
                b = self.broker(f, provider)
                self.to_focus(f, b)
                self.focus_checkpoint(f)
                self.one_transport(f, b)
                self.assertEqual(b.task_result["status"], "COMPLETED")

    def test_failed_send_preserves_receipt_without_claiming_display_for_both_providers(self):
        for provider in ("codex", "claude"):
            with self.subTest(provider=provider):
                f = self.fixture("handoff-only")
                b = self.broker(f, provider)
                self.to_focus(f, b)
                self.focus_checkpoint(f)
                self.one_transport(f, b, fail=True)
                self.assertTrue(b.error)
                self.assertEqual(b.task_result["status"], "COMPLETED")
                self.assertEqual(b.task_result["receipt_sha256"], digest(f.receipt_path.read_bytes()))


class CompletionResponseTests(ManagedFixtures):
    @staticmethod
    def verified_result():
        return {"status": "COMPLETED", "task_id": "TASK-001", "receipt_verified": True,
                "receipt_readback": True, "receipt_published": True,
                "receipt_path": "/synthetic/operator/receipt.json", "receipt_sha256": "a" * 64}

    def test_complete_success_message_preserves_exact_identity_and_scope(self):
        result = self.verified_result()
        reply = hooks.completion_response(result, "TASK-001")
        self.assertEqual(set(reply), {"systemMessage"})
        for required in ("TASK-001", result["receipt_path"], result["receipt_sha256"],
                         "native turn completion NOT_OBSERVED", "semantic acceptance NOT_EVALUATED"):
            self.assertIn(required, reply["systemMessage"])
        self.assertIn("mechanical task receipt verified", reply["systemMessage"].lower())
        self.assertLessEqual(len(json.dumps(reply).encode()), 8192)

    def test_invalid_required_result_identity_or_verification_is_refused(self):
        variants = [{"status": "READY"}, {"receipt_verified": False}, {"receipt_path": None},
                    {"receipt_path": "relative/receipt.json"}, {"receipt_sha256": "A" * 64},
                    {"receipt_sha256": "a" * 63}, {"receipt_sha256": 1}]
        for change in variants:
            with self.subTest(change=change):
                value = self.verified_result()
                value.update(change)
                with self.assertRaises(ValueError):
                    hooks.completion_response(value, "TASK-001")
        for task_id in (None, "", 1):
            with self.subTest(task_id=task_id), self.assertRaises(ValueError):
                hooks.completion_response(self.verified_result(), task_id)

    def test_oversize_serialized_reply_is_refused_without_locator_truncation(self):
        value = self.verified_result()
        value["receipt_path"] = "/synthetic/" + "a" * 7000 + "/receipt.json"
        reply = hooks.completion_response(value, "TASK-001")
        self.assertIn(value["receipt_path"], reply["systemMessage"])
        self.assertIn(value["receipt_sha256"], reply["systemMessage"])
        self.assertLessEqual(len(json.dumps(reply).encode()), 8192)
        value["receipt_path"] = "/synthetic/" + "a" * 9000 + "/receipt.json"
        with self.assertRaises(ValueError):
            hooks.completion_response(value, "TASK-001")

    def test_unicode_json_escaping_counts_toward_serialized_response_limit(self):
        value = self.verified_result()
        value["receipt_path"] = "/synthetic/" + "é" * 1400 + "/receipt.json"
        self.assertLess(len(value["receipt_path"].encode("utf-8")), 8192)
        self.assertGreater(len(json.dumps({"systemMessage": value["receipt_path"]}).encode("utf-8")), 8192)
        with self.assertRaises(ValueError):
            hooks.completion_response(value, "TASK-001")


class ManagedSupervisorTests(ManagedFixtures):
    def scope(self, result):
        self.assertEqual(result["completion_mode"], "managed-session")
        self.assertEqual(result["native_completion"], "NOT_EVALUATED")
        self.assertEqual(result["long_lived_task_completion"], "MECHANICAL_TASK_BOUNDARY_IMPLEMENTED")
        self.assertEqual(result["execution_kind"], "SYNTHETIC")
        self.assertIs(result["native_launch_admitted"], False)
        self.assertEqual(result["native_provider_admission"], "NOT_VALIDATED")
        self.assertEqual(result["user_delivery"], "NOT_OBSERVED")
        self.assertIn("mechanical", result["scope"].lower())
        self.assertIn("native", result["scope"].lower())

    def fake_run(self, fixture, *, provider="codex", behavior="commit", exit_code=0,
                 completion_mode="managed-session", cleanup_absent=True):
        fixture.session["provider"] = provider
        fixture.write_session()
        profile, client, executable, gate, home = self.launch_inputs(fixture)
        original_broker = supervisor.Broker
        original_read_text = Path.read_text
        captured = {}
        fake = SimpleNamespace(pid=987654321, returncode=None)

        def make_broker(*args, **kwargs):
            captured["broker"] = original_broker(*args, **kwargs)
            return captured["broker"]

        def read_text(path, *args, **kwargs):
            if path == Path("/proc/987654321/stat"):
                return "987654321 (inert managed test double) " + " ".join(["0"] * 20)
            return original_read_text(path, *args, **kwargs)

        def popen(argv, **kwargs):
            captured["argv"] = argv
            b = captured["broker"]
            if behavior == "none":
                fixture.write_outputs()
            elif behavior == "waiting":
                self.callback(fixture, b, "SessionStart")
                self.checkpoint(fixture, "Recover", {"question": "Which group?", "blocking_dependency": "The group is unknown."}, state="awaiting_user")
                self.callback(fixture, b, "Stop")
            else:
                self.to_focus(fixture, b)
                self.focus_checkpoint(fixture)
                reply = self.callback(fixture, b, "Stop")
                self.assertIsNone(fake.returncode)
                if completion_mode == "managed-session":
                    self.verified_reply(reply, fixture)
                    captured["committed_while_live"] = True
                    captured["historical"] = copy.deepcopy(b.task_result)
                    captured["receipt_raw"] = fixture.receipt_path.read_bytes()
                else:
                    self.assertEqual(reply, {})
                    self.assertFalse(fixture.receipt_path.exists())
                if behavior == "interrupt":
                    self.callback(fixture, b, "Interrupt")
                elif behavior == "service-error":
                    b.error = "Synthetic post-commit callback service failure"
                elif behavior == "drift":
                    fixture.handoff_path.write_bytes(fixture.handoff_path.read_bytes() + b"\nChanged after commit.\n")
            return fake

        def cleanup(process):
            fake.returncode = -15 if behavior == "timeout" else exit_code
            return {"signals": [], "leader_reaped": True, "group_absent": cleanup_absent, "issues": []}

        ticks = iter([0.0, 100.0, 101.0])
        clock = (lambda: next(ticks, 101.0)) if behavior == "timeout" else None
        with mock.patch.object(supervisor, "Broker", side_effect=make_broker), \
                mock.patch.object(supervisor.subprocess, "Popen", side_effect=popen), \
                mock.patch.object(supervisor, "owned_exited", return_value=behavior not in {"timeout", "interrupt", "service-error"}), \
                mock.patch.object(supervisor, "stop_owned", side_effect=cleanup), \
                mock.patch.object(Path, "read_text", read_text), \
                mock.patch.object(supervisor, "sandbox_command", return_value=["synthetic-never-executed"]), \
                mock.patch.dict(os.environ, {"DEVFORGE_DELIVERY_EXECUTABLE": str(gate)}):
            if clock:
                with mock.patch.object(supervisor.time, "monotonic", side_effect=clock):
                    result = supervisor.run(fixture.session_path, fixture.state, profile, [str(executable)], 30,
                                            client, synthetic=True, completion_mode=completion_mode)
            else:
                result = supervisor.run(fixture.session_path, fixture.state, profile, [str(executable)], 30,
                                        client, synthetic=True, completion_mode=completion_mode)
        return result, captured

    def test_both_providers_commit_while_fake_process_live_and_exit_separately(self):
        for provider in ("codex", "claude"):
            with self.subTest(provider=provider):
                f = self.fixture()
                result, captured = self.fake_run(f, provider=provider)
                self.assertTrue(captured["committed_while_live"])
                self.assertEqual(result["status"], "COMPLETED", result)
                self.scope(result)
                self.assertEqual(result["current_applicability"], "VERIFIED")
                self.assertEqual(result["worker"]["completion_boundary"], "MECHANICAL_TASK_COMMIT")
                self.assertEqual(result["worker"]["completion_mode"], "managed-session")
                self.assertEqual(result["worker"]["exit_code"], 0)
                self.assertEqual(result["receipt_path"], str(f.receipt_path))
                self.assertEqual(result["receipt_sha256"], digest(f.receipt_path.read_bytes()))
                self.assertIs(result["receipt_verified"], True)
                owned = self.read_document(Path(result["worker"]["observations"]) / "owned-process.json")
                self.assertEqual(owned["completion_mode"], "managed-session")
                self.assertEqual(f.receipt_path.read_bytes(), captured["receipt_raw"])

    def test_successful_exit_without_hooks_cannot_commit_valid_artifacts(self):
        f = self.fixture()
        result, captured = self.fake_run(f, behavior="none")
        self.assertEqual(result["status"], "FAIL", result)
        self.scope(result)
        self.assertEqual(result["worker"]["event_count"], 0)
        self.assertEqual(result["worker"]["exit_code"], 0)
        self.assertFalse(f.receipt_path.exists())
        self.assertIsNone(captured["broker"].task_result)

    def test_waiting_user_remains_pending_after_successful_exit(self):
        f = self.fixture()
        result, captured = self.fake_run(f, behavior="waiting")
        self.assertEqual(result["status"], "WAITING_USER", result)
        self.scope(result)
        self.assertFalse(f.receipt_path.exists())
        self.assertIsNone(captured["broker"].task_result)

    def test_nonzero_exit_without_committed_task_cannot_create_receipt(self):
        f = self.fixture()
        result, captured = self.fake_run(f, behavior="none", exit_code=3)
        self.assertEqual(result["status"], "FAIL", result)
        self.scope(result)
        self.assertEqual(result["worker"]["exit_code"], 3)
        self.assertFalse(f.receipt_path.exists())
        self.assertIsNone(captured["broker"].task_result)

    def test_late_nonzero_exit_preserves_historical_committed_receipt(self):
        f = self.fixture()
        result, captured = self.fake_run(f, exit_code=3)
        self.assertEqual(result["status"], "FAIL", result)
        self.scope(result)
        self.assertEqual(result["worker"]["exit_code"], 3)
        self.assertEqual(result["task_result"], captured["historical"])
        self.assertEqual(f.receipt_path.read_bytes(), captured["receipt_raw"])

    def test_late_timeout_preserves_historical_committed_receipt(self):
        f = self.fixture()
        result, captured = self.fake_run(f, behavior="timeout")
        self.assertEqual(result["status"], "COULD_NOT_RUN", result)
        self.scope(result)
        self.assertEqual(result["worker"]["exit_code"], -15)
        self.assertEqual(result["task_result"], captured["historical"])
        self.assertEqual(f.receipt_path.read_bytes(), captured["receipt_raw"])

    def test_late_interrupt_service_or_cleanup_failure_preserves_commit(self):
        for cause in ("interrupt", "service-error", "cleanup"):
            with self.subTest(cause=cause):
                f = self.fixture()
                result, captured = self.fake_run(f, behavior=cause, cleanup_absent=cause != "cleanup")
                self.assertEqual(result["status"], "COULD_NOT_RUN", result)
                self.scope(result)
                self.assertEqual(result["task_result"], captured["historical"])
                self.assertEqual(f.receipt_path.read_bytes(), captured["receipt_raw"])

    def test_postcommit_drift_without_another_callback_is_not_current_success(self):
        f = self.fixture()
        result, captured = self.fake_run(f, behavior="drift")
        self.assertEqual(result["status"], "FAIL", result)
        self.scope(result)
        self.assertEqual(result["worker"]["exit_code"], 0)
        self.assertEqual(result["current_applicability"], "NOT_VERIFIED")
        self.assertTrue(result["issues"])
        self.assertEqual(result["task_result"], captured["historical"])
        self.assertEqual(f.receipt_path.read_bytes(), captured["receipt_raw"])

    def test_process_mode_nonzero_exit_still_withholds_previously_absent_receipt(self):
        f = self.fixture()
        result, _ = self.fake_run(f, exit_code=3, completion_mode="process")
        self.assertEqual(result["status"], "FAIL", result)
        self.assertEqual(result["worker"]["completion_boundary"], "OWNED_PROCESS_EXIT")
        self.assertEqual(result["long_lived_task_completion"], "NOT_IMPLEMENTED")
        self.assertFalse(f.receipt_path.exists())

    def test_malformed_mode_refuses_before_state_process_or_socket_admission(self):
        for mode in (None, True, [], "", "managed", "MANAGED-SESSION"):
            with self.subTest(mode=mode):
                f = self.fixture()
                with mock.patch.object(phase, "start", side_effect=AssertionError("Invalid mode cannot admit state")), \
                        mock.patch.object(supervisor.tempfile, "mkdtemp", side_effect=AssertionError("Invalid mode cannot create socket directory")):
                    result = supervisor.run(f.session_path, f.state, f.root / "missing-profile", ["never-executed"],
                                            30, f.root / "missing-client", synthetic=True, completion_mode=mode)
                    self.assertEqual(result["status"], "FAIL", result)
                    with self.assertRaises(ValueError):
                        supervisor.Broker(f.state, f.session, f.delivery, digest(f.session_path.read_bytes()),
                                          f.operator, completion_mode=mode)
                self.assertFalse(f.state.exists())
                self.assertFalse(f.receipt_path.exists())

    def test_managed_selection_does_not_enable_default_native_launch(self):
        f = self.fixture()
        with mock.patch.object(phase, "start", side_effect=AssertionError("Native admission remains closed")):
            result = supervisor.run(f.session_path, f.state, f.root / "missing-profile", ["codex"],
                                    30, f.root / "missing-client", completion_mode="managed-session")
        self.assertEqual(result["status"], "COULD_NOT_RUN", result)
        self.scope(result)
        self.assertFalse(f.state.exists())
        self.assertFalse(f.receipt_path.exists())


if __name__ == "__main__":
    unittest.main()
