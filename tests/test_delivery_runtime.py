"""Synthetic hook/runtime contracts; no client, worker, bwrap, or API execution.

Real local Unix sockets exercise the broker. Sandbox tests inspect argv only.
Supervisor tests replace Popen and process cleanup with inert test doubles;
their receipt evidence proves mechanical process-mode logic, not native behavior.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from delivery import hook_protocol as hooks, supervisor  # noqa: E402
import test_phase_state as fixture_support  # noqa: E402

phase = supervisor.phase_state
Fixture = fixture_support.Fixture
NOW = fixture_support.NOW
digest = fixture_support.digest
json_bytes = fixture_support.json_bytes


class RuntimeFixtureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="devforge-runtime-unit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.counter = 0
        patcher = mock.patch.object(phase, "utc_now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        clock = mock.patch.object(supervisor, "datetime", wraps=datetime)
        clock.start().now.return_value = NOW
        self.addCleanup(clock.stop)
        process_guard = mock.patch.object(supervisor.subprocess, "Popen",
                                          side_effect=AssertionError("Actual processes are forbidden in this suite"))
        process_guard.start()
        self.addCleanup(process_guard.stop)

    def fixture(self, mode="brainstorm"):
        self.counter += 1
        return Fixture(self.root / ("case-" + str(self.counter)), mode)

    def checkpoint(self, fixture, phase_name, content, state="ready"):
        value = {"schema_version": "devforge.brainstorm-checkpoint/v1",
                 "task_id": fixture.session["task_id"], "phase": phase_name,
                 "challenge": phase.context(fixture.state)["challenge"],
                 "state": state, "content": content}
        fixture.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        fixture.checkpoint_path.write_bytes(json_bytes(value))
        return fixture.checkpoint_path.read_bytes()

    def broker(self, fixture, provider="codex", *, start_thread=False):
        fixture.session["provider"] = provider
        fixture.write_session()
        self.assertEqual(phase.start(fixture.session_path, fixture.state)["status"], "ACTIVE")
        observations = fixture.operator / "observations"
        observations.mkdir()
        broker = supervisor.Broker(fixture.state, fixture.session, fixture.delivery,
                                   digest(fixture.session_path.read_bytes()), observations)
        self.addCleanup(lambda: broker.close() if broker.socket_root.exists() else None)
        if start_thread:
            broker.thread.start()
        return broker

    @staticmethod
    def event(fixture, name, session_id="SYNTHETIC-SESSION"):
        event = {"hook_event_name": name, "session_id": session_id, "cwd": str(fixture.project)}
        if name == "Stop":
            event["stop_hook_active"] = False
        return event

    def request(self, fixture, broker, name, **event_changes):
        event = self.event(fixture, name)
        event.update(event_changes)
        return {"provider": fixture.session["provider"], "contract_sha256": broker.contract_digest,
                "event": event}

    def callback(self, fixture, broker, name, **event_changes):
        return broker.handle(self.request(fixture, broker, name, **event_changes))

    @staticmethod
    def wire(broker, raw):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(4)
            connection.connect(str(broker.socket_path))
            connection.sendall(raw)
            connection.shutdown(socket.SHUT_WR)
            response = bytearray()
            while b"\n" not in response:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                response.extend(chunk)
        return json.loads(bytes(response))

    def launch_inputs(self, fixture):
        profile = fixture.root / "profile"
        profile.mkdir()
        client = fixture.root / "client"
        (client / "bin").mkdir(parents=True)
        executable = client / "bin/synthetic-client"
        executable.write_bytes(b"This fixture is never executed.\n")
        gate = fixture.root / "delivery-gate"
        gate.write_bytes(b"This fixture is never executed.\n")
        home = fixture.root / "host-home"
        home.mkdir()
        return profile, client, executable, gate, home

    def select_reference_source(self, fixture, physical):
        fixture.delivery.update(
            schema_version="devforge.delivery-task/v2", project_id="synthetic-reference-project",
            primary_ledger_path="docs/ideas.md" if fixture.mode == "brainstorm" else None,
            reference_catalog=[{
                "store": "authority", "path": "evidence/source.bin", "kind": "raw-file", "identity": None,
                "source": {"kind": "fixed", "physical_path": str(physical),
                           "sha256": digest(b"Synthetic selected reference.\n")},
            }])
        fixture.refresh_delivery()


class HookMappingTests(RuntimeFixtureTests):
    def test_record_context_names_the_selected_primary_in_each_output_order(self):
        f = self.fixture()
        primary = "docs/ideas.md"
        second = {"path": "docs/second-ideas.md", "artifact_id": "IDEAS-002",
                  "artifact_type": "idea-ledger", "revision": 1,
                  "sections": ["IDEAS-SECTION-002"]}
        current = {"status": "PROGRESS", "phase": "Record", "challenge": "a" * 32}
        for version, order in (("v1", f.delivery["outputs"]),
                               ("v2", [*f.delivery["outputs"], second]),
                               ("v2", [second, *f.delivery["outputs"]])):
            with self.subTest(version=version, paths=[row["path"] for row in order]):
                contract = copy.deepcopy(f.delivery)
                contract["schema_version"] = "devforge.delivery-task/" + version
                contract["outputs"] = order
                if version == "v2":
                    contract["primary_ledger_path"] = primary
                reply = hooks.response("Stop", current, f.session, contract)
                self.assertEqual(reply["decision"], "block")
                start = reply["reason"].index('{"schema_version":')
                checkpoint, _ = json.JSONDecoder().raw_decode(reply["reason"][start:])
                self.assertEqual(checkpoint["content"], {"ledger_path": primary})
                self.assertEqual(checkpoint["phase"], "Record")
                self.assertEqual(checkpoint["challenge"], current["challenge"])

    def test_start_and_prompt_context_are_provenance_labeled_and_phase_bound(self):
        f = self.fixture()
        current = phase.start(f.session_path, f.state)
        for name in ("SessionStart", "UserPromptSubmit"):
            with self.subTest(event=name):
                reply = hooks.response(name, current, f.session, f.delivery)
                self.assertEqual(set(reply), {"hookSpecificOutput"})
                output = reply["hookSpecificOutput"]
                self.assertEqual(output["hookEventName"], name)
                text = output["additionalContext"]
                self.assertIn("Runtime-generated", text)
                self.assertIn("not a human", text)
                self.assertIn("approval", text)
                self.assertIn(current["challenge"], text)
                self.assertIn(str(f.checkpoint_path), text)
                self.assertIn("Unknown facts", text)
                self.assertIn("null", text)
                self.assertLessEqual(len(text), hooks.MAX_MESSAGE)

    def test_stop_progress_and_one_correction_block_without_claiming_receipt(self):
        f = self.fixture()
        for status in ("PROGRESS", "FAIL"):
            with self.subTest(status=status):
                result = {"status": status, "terminal": False, "phase": "Explore", "challenge": "a" * 32,
                          "issues": ["One selected fact is missing."]}
                reply = hooks.response("Stop", result, f.session, f.delivery)
                self.assertEqual(reply["decision"], "block")
                self.assertTrue(reply["reason"])
                self.assertIn("Runtime-generated", reply["reason"])
                self.assertLessEqual(len(reply["reason"]), hooks.MAX_MESSAGE)
                self.assertNotIn("continue", reply)
        self.assertFalse(f.receipt_path.exists())

    def test_waiting_terminal_and_unavailable_stop_prevent_completion(self):
        f = self.fixture()
        for result in ({"status": "WAITING_USER", "question": "Which group?"},
                       {"status": "FAIL", "terminal": True, "issues": ["Budget exhausted"]},
                       {"status": "COULD_NOT_RUN", "issues": ["State unavailable"]}):
            with self.subTest(status=result["status"]):
                reply = hooks.response("Stop", result, f.session, f.delivery)
                self.assertIs(reply["continue"], False)
                self.assertTrue(reply["stopReason"])
                self.assertIn("receipt", reply["systemMessage"])
        self.assertFalse(f.receipt_path.exists())

    def test_ready_does_not_allocate_more_work_or_publish_from_hook(self):
        f = self.fixture()
        for status in ("READY", "COMPLETED"):
            with self.subTest(status=status):
                result = {"status": status}
                self.assertEqual(hooks.response("Stop", result, f.session, f.delivery), {})
                reply = hooks.response("UserPromptSubmit", result, f.session, f.delivery)
                self.assertIn("No new task is allocated", reply["hookSpecificOutput"]["additionalContext"])
        self.assertFalse(f.receipt_path.exists())

    def test_advisory_end_events_never_block_termination(self):
        f = self.fixture()
        for name in ("Interrupt", "SessionEnd"):
            for status in ("ACTIVE", "WAITING_USER", "FAIL", "COULD_NOT_RUN", "READY"):
                self.assertEqual(hooks.response(name, {"status": status}, f.session, f.delivery), {})

    def test_provider_event_shape_and_project_binding(self):
        f = self.fixture()
        for provider in ("codex", "claude"):
            hooks.validate_event(provider, self.event(f, "Stop"), str(f.project))
            for change in ({"stop_hook_active": 1}, {"stop_hook_active": None},
                           {"hook_event_name": ["Stop"]}, {"hook_event_name": "PreToolUse"},
                           {"session_id": " "}, {"session_id": 1}, {"cwd": "."},
                           {"cwd": str(f.operator)}):
                with self.subTest(provider=provider, change=change):
                    event = self.event(f, "Stop")
                    event.update(change)
                    with self.assertRaises(ValueError):
                        hooks.validate_event(provider, event, str(f.project))
            event = self.event(f, "Stop")
            event.pop("stop_hook_active")
            with self.assertRaises(ValueError):
                hooks.validate_event(provider, event, str(f.project))
        with self.assertRaises(ValueError):
            hooks.validate_event("claude", self.event(f, "Interrupt"), str(f.project))


class BrokerTests(RuntimeFixtureTests):
    def test_real_socket_start_prompt_and_stop_observe_required_progress(self):
        for provider in ("codex", "claude"):
            with self.subTest(provider=provider):
                f = self.fixture("handoff-only")
                b = self.broker(f, provider, start_thread=True)
                for name in ("SessionStart", "UserPromptSubmit"):
                    reply = self.wire(b, json.dumps(self.request(f, b, name)).encode() + b"\n")
                    self.assertEqual(reply["hookSpecificOutput"]["hookEventName"], name)
                self.checkpoint(f, "Recover", fixture_support.PhaseStateTests.recover_content())
                reply = self.wire(b, json.dumps(self.request(f, b, "Stop")).encode() + b"\n")
                self.assertEqual(reply["decision"], "block")
                self.assertIn("Focus", reply["reason"])
                self.assertEqual(phase.context(f.state)["phase"], "Focus")
                self.assertEqual(phase.context(f.state)["status"], "ACTIVE")
                self.assertFalse(f.receipt_path.exists())
                self.assertEqual(b.count, 3)
                previous = None
                for sequence, name in enumerate(("SessionStart", "UserPromptSubmit", "Stop"), 1):
                    path = b.observations / f"event-{sequence:06d}.json"
                    raw = path.read_bytes()
                    observation = json.loads(raw)
                    self.assertEqual(observation["sequence"], sequence)
                    self.assertEqual(observation["event"], name)
                    self.assertEqual(observation["previous_sha256"], previous)
                    self.assertEqual(observation["authority"], "CALLBACK_NOT_HUMAN_AUTHORITY")
                    self.assertNotIn("SYNTHETIC-SESSION", raw.decode())
                    previous = hashlib.sha256(raw).hexdigest()

    def test_wait_and_prompt_resume_same_phase_fresh_challenge_preserve_budget(self):
        f = self.fixture()
        b = self.broker(f)
        self.callback(f, b, "SessionStart")
        initial = phase.context(f.state)
        invalid = {"known_ideas": [], "known_decisions": [], "missing_inputs": []}
        self.checkpoint(f, "Recover", invalid)
        self.assertEqual(self.callback(f, b, "Stop")["decision"], "block")
        self.checkpoint(f, "Recover", {"question": "Which group?", "blocking_dependency": "The group is unknown."},
                        state="awaiting_user")
        reply = self.callback(f, b, "Stop")
        self.assertIs(reply["continue"], False)
        self.assertEqual(phase.context(f.state)["status"], "WAITING_USER")
        resumed_reply = self.callback(f, b, "UserPromptSubmit")
        resumed = phase.context(f.state)
        self.assertEqual((resumed["status"], resumed["phase"]), ("ACTIVE", "Recover"))
        self.assertNotEqual(resumed["challenge"], initial["challenge"])
        text = resumed_reply["hookSpecificOutput"]["additionalContext"]
        self.assertIn("not a human", text)
        self.assertIn("approval", text)
        self.checkpoint(f, "Recover", invalid)
        self.assertIs(self.callback(f, b, "Stop")["continue"], False)
        self.assertEqual(phase.context(f.state)["status"], "FAIL")
        self.assertFalse(f.receipt_path.exists())

    def test_repeated_invalid_stop_consumes_one_correction_then_stops(self):
        f = self.fixture()
        b = self.broker(f)
        self.callback(f, b, "SessionStart")
        self.checkpoint(f, "Recover", {"known_ideas": [], "known_decisions": [], "missing_inputs": []})
        request = self.request(f, b, "Stop")
        checkpoint = f.checkpoint_path.read_bytes()
        self.assertEqual(b.handle(copy.deepcopy(request))["decision"], "block")
        self.assertIs(b.handle(copy.deepcopy(request))["continue"], False)
        self.assertEqual(f.checkpoint_path.read_bytes(), checkpoint)
        self.assertEqual(phase.context(f.state)["status"], "FAIL")
        self.assertFalse(f.receipt_path.exists())

    def test_repeated_old_success_checkpoint_is_rejected_in_next_phase(self):
        f = self.fixture("handoff-only")
        b = self.broker(f)
        self.callback(f, b, "SessionStart")
        self.checkpoint(f, "Recover", fixture_support.PhaseStateTests.recover_content())
        request = self.request(f, b, "Stop")
        self.assertEqual(b.handle(copy.deepcopy(request))["decision"], "block")
        self.assertEqual(phase.context(f.state)["phase"], "Focus")
        self.assertEqual(b.handle(copy.deepcopy(request))["decision"], "block")
        self.assertIs(b.handle(copy.deepcopy(request))["continue"], False)
        self.assertEqual(phase.context(f.state)["status"], "FAIL")

    def test_record_repair_is_reinspected_with_identical_stop_and_checkpoint(self):
        f = self.fixture()
        b = self.broker(f)
        self.callback(f, b, "SessionStart")
        for name, content in (("Recover", fixture_support.PhaseStateTests.recover_content()),
                              ("Explore", fixture_support.PhaseStateTests.explore_content())):
            self.checkpoint(f, name, content)
            self.assertEqual(self.callback(f, b, "Stop")["decision"], "block")
        self.assertEqual(phase.context(f.state)["phase"], "Record")
        checkpoint = self.checkpoint(f, "Record", {"ledger_path": "docs/ideas.md"})
        request = self.request(f, b, "Stop")
        self.assertEqual(b.handle(copy.deepcopy(request))["decision"], "block")
        self.assertEqual(phase.context(f.state)["phase"], "Record")
        f.write_ledger()
        reply = b.handle(copy.deepcopy(request))
        self.assertEqual(reply["decision"], "block")
        self.assertEqual(phase.context(f.state)["phase"], "Focus")
        self.assertEqual(f.checkpoint_path.read_bytes(), checkpoint)
        self.assertFalse(f.receipt_path.exists())

    def test_unbound_wrong_provider_wrong_session_and_invalid_stop_refused(self):
        f = self.fixture()
        b = self.broker(f)
        with self.assertRaises(ValueError):
            self.callback(f, b, "Stop")
        self.callback(f, b, "SessionStart")
        for change in ({"provider": "claude"}, {"contract_sha256": "0" * 64}, {"extra": True}):
            with self.subTest(change=change):
                request = self.request(f, b, "Stop")
                request.update(change)
                with self.assertRaises(ValueError):
                    b.handle(request)
        for change in ({"session_id": "OTHER-SESSION"}, {"stop_hook_active": "false"},
                       {"cwd": str(f.operator)}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.callback(f, b, "Stop", **change)
        self.assertEqual(b.count, 1)
        self.assertEqual(phase.context(f.state)["phase"], "Recover")
        self.assertFalse(f.receipt_path.exists())

    def test_malformed_duplicate_key_multiline_and_oversize_wire_refused(self):
        variants = [b"not-json\n", b"[]\n", b"{}", b"{}\n{}\n",
                    b'{"provider":"codex","provider":"claude"}\n',
                    b"x" * (supervisor.MAX_WIRE + 1), b"\xff\n"]
        for index, raw in enumerate(variants):
            with self.subTest(variant=index):
                f = self.fixture()
                b = self.broker(f, start_thread=True)
                reply = self.wire(b, raw)
                self.assertIs(reply["continue"], False)
                self.assertIn("refused", reply["stopReason"])
                self.assertTrue(b.error)
                self.assertEqual(b.count, 0)
                self.assertFalse(f.receipt_path.exists())

    def test_unhashable_event_name_is_typed_wire_refusal(self):
        f = self.fixture()
        b = self.broker(f, start_thread=True)
        request = self.request(f, b, "SessionStart", hook_event_name=["SessionStart"])
        reply = self.wire(b, json.dumps(request).encode() + b"\n")
        self.assertIs(reply["continue"], False)
        self.assertTrue(b.error)
        self.assertEqual(b.count, 0)

    def test_close_reports_nonquiescent_listener_without_claiming_clean_shutdown(self):
        f = self.fixture()
        b = self.broker(f)
        inert_thread = SimpleNamespace(ident=123, is_alive=lambda: True, join=mock.Mock())
        b.thread = inert_thread
        self.assertIs(b.close(), False)
        self.assertIn("quiescent", b.error)
        inert_thread.join.assert_called_once_with(timeout=4)
        self.assertFalse(b.socket_root.exists())


class SandboxArgvTests(RuntimeFixtureTests):
    def arguments(self, f, *, synthetic=True):
        profile, client, executable, gate, home = self.launch_inputs(f)
        socket_root = f.root / "socket"
        socket_root.mkdir()
        (socket_root / "scratch").mkdir()
        broker = SimpleNamespace(socket_root=socket_root, socket_path=socket_root / "hook.sock",
                                 contract_digest="d" * 64)
        command = [str(executable), "--literal", "argument with spaces", "$(not a command)"]
        environment = {"DEVFORGE_DELIVERY_EXECUTABLE": str(gate),
                       "OPENAI_API_KEY": "SYNTHETIC-OPENAI-DO-NOT-COPY",
                       "ANTHROPIC_API_KEY": "SYNTHETIC-ANTHROPIC-DO-NOT-COPY",
                       "TMPDIR": "SYNTHETIC-HOST-TMPDIR-DO-NOT-COPY", "TERM": "synthetic-term"}
        with mock.patch.object(supervisor.shutil, "which", side_effect=lambda name: "/usr/bin/bwrap" if name == "bwrap" else None), \
                mock.patch.object(Path, "home", return_value=home), mock.patch.dict(os.environ, environment):
            argv = supervisor.sandbox_command(f.project, profile, client, command, f.session_path,
                                              f.session, f.delivery, broker, synthetic=synthetic)
        return argv, profile, client, executable, gate, home, broker, command

    def test_masks_precede_writable_restores_and_all_selected_readonly_mounts(self):
        f = self.fixture()
        archives = f.seed_baselines()
        for relative, raw in archives.items():
            path = f.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        argv, profile, client, executable, gate, home, broker, command = self.arguments(f)
        mounts = [(index, argv[index], argv[index + 1], argv[index + 2])
                  for index in range(len(argv)) if argv[index] in {"--bind", "--ro-bind"}]
        root_mount = next(index for index, kind, src, dst in mounts if (kind, src, dst) == ("--ro-bind", "/", "/"))
        tmpfs = [(index, argv[index + 1]) for index, value in enumerate(argv) if value == "--tmpfs"]
        scratch = broker.socket_root / "scratch"
        self.assertEqual(len(tmpfs), 3)
        self.assertEqual({destination for _, destination in tmpfs}, {str(home), "/tmp", str(scratch)})
        masks = [index for index, destination in tmpfs if destination in {str(home), "/tmp"}]
        writable = [(index, src, dst) for index, kind, src, dst in mounts if kind == "--bind"]
        self.assertEqual({(src, dst) for _, src, dst in writable},
                         {(str(f.project), str(f.project)), (str(profile), str(profile))})
        self.assertLess(root_mount, min(masks))
        self.assertLess(max(masks), min(index for index, _, _ in writable))
        scratch_index = next(index for index, destination in tmpfs if destination == str(scratch))
        self.assertGreater(scratch_index, max(index for index, _, _, _ in mounts))
        self.assertFalse(any(source == str(scratch) or destination == str(scratch)
                             for _, _, source, destination in mounts))
        # This scratch tmpfs is the only extra writable space. Both masked
        # ancestors become read-only after every selected bind and scratch mount,
        # so omitted authority paths cannot be recreated as writable shadows.
        remounts = [(index, argv[index + 1]) for index, value in enumerate(argv) if value == "--remount-ro"]
        self.assertEqual(len(remounts), 2)
        self.assertEqual({destination for _, destination in remounts}, {str(home), "/tmp"})
        self.assertGreater(min(index for index, _ in remounts), scratch_index)
        self.assertLess(max(index for index, _ in remounts), argv.index("--clearenv"))
        protected = {f.session_path, f.delivery_path, f.assignment_path,
                     Path(supervisor.__file__).resolve().parent.parent, gate, client,
                     f.installed_path, f.input_path, broker.socket_root,
                     *(f.project / relative for relative in archives)}
        for path in protected:
            selected = [(index, src, dst) for index, kind, src, dst in mounts
                        if kind == "--ro-bind" and dst == str(path)]
            self.assertEqual(len(selected), 1, str(path))
            index, source, destination = selected[0]
            self.assertEqual(source, destination)
            self.assertGreater(index, max(index for index, _, _ in writable), str(path))
        readonly = [Path(dst) for _, kind, _, dst in mounts if kind == "--ro-bind" and dst not in {"/", str(broker.socket_root)}]
        self.assertEqual(readonly, sorted(set(readonly), key=lambda path: (len(path.parts), str(path))))
        separator = argv.index("--")
        self.assertEqual(argv[separator + 1:], [str(executable.resolve()), *command[1:]])
        for flag in ("--die-with-parent", "--new-session", "--unshare-pid", "--unshare-net"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--cap-drop") + 1], "ALL")
        self.assertEqual(argv[argv.index("--chdir") + 1], str(f.project))

    def test_environment_is_selected_and_raw_api_keys_are_not_inherited(self):
        f = self.fixture()
        argv, profile, client, executable, gate, home, broker, command = self.arguments(f)
        offsets = [index for index, value in enumerate(argv) if value == "--setenv"]
        environment = {argv[index + 1]: argv[index + 2] for index in offsets}
        self.assertLess(argv.index("--clearenv"), min(offsets))
        expected = {"HOME": str(profile), "CODEX_HOME": str(profile / ".codex"),
                    "CLAUDE_CONFIG_DIR": str(profile / ".claude"),
                    "XDG_CONFIG_HOME": str(profile / ".config"), "XDG_CACHE_HOME": str(profile / ".cache"),
                    "XDG_DATA_HOME": str(profile / ".local/share"), "LANG": "C.UTF-8",
                    "PATH": str(executable.parent) + ":/usr/local/bin:/usr/bin:/bin",
                    "PYTHONDONTWRITEBYTECODE": "1", "DEVFORGE_DELIVERY_EXECUTABLE": str(gate),
                    "TMPDIR": str(broker.socket_root / "scratch"),
                    "DEVFORGE_DELIVERY_SOCKET": str(broker.socket_path),
                    "DEVFORGE_DELIVERY_CONTRACT_SHA256": broker.contract_digest, "TERM": "synthetic-term"}
        self.assertEqual(environment, expected)
        joined = json.dumps(argv)
        for forbidden in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SYNTHETIC-OPENAI-DO-NOT-COPY",
                          "SYNTHETIC-ANTHROPIC-DO-NOT-COPY", "SYNTHETIC-HOST-TMPDIR-DO-NOT-COPY"):
            self.assertNotIn(forbidden, joined)

    def test_explicit_reference_source_is_restored_readonly_after_mutable_mounts(self):
        f = self.fixture()
        selected = f.operator / "reference.bin"
        selected.write_bytes(b"Synthetic selected reference.\n")
        self.select_reference_source(f, selected)
        argv, *_ = self.arguments(f)
        mounts = [(i, argv[i], argv[i + 1], argv[i + 2]) for i, value in enumerate(argv)
                  if value in {"--bind", "--ro-bind"}]
        entries = [entry for entry in mounts if entry[3] == str(selected)]
        self.assertEqual(len(entries), 1)
        offset, kind, source, target = entries[0]
        self.assertEqual((kind, source, target), ("--ro-bind", str(selected), str(selected)))
        self.assertGreater(offset, max(i for i, kind, _, _ in mounts if kind == "--bind"))
        self.assertNotIn(str(selected.parent), [target for _, kind, _, target in mounts if kind == "--bind"])

    def test_missing_bwrap_has_no_fallback(self):
        f = self.fixture()
        profile, client, executable, gate, home = self.launch_inputs(f)
        with mock.patch.object(supervisor.shutil, "which", return_value=None):
            with self.assertRaises(OSError):
                supervisor.sandbox_command(f.project, profile, client, [str(executable)], f.session_path,
                                           f.session, f.delivery, SimpleNamespace())

    def test_executable_symlink_outside_intact_client_root_is_refused(self):
        f = self.fixture()
        profile, client, executable, gate, home = self.launch_inputs(f)
        outside = f.root / "outside-client"
        outside.write_bytes(b"Never executed.\n")
        alias = client / "bin/escape"
        alias.symlink_to(outside)
        with mock.patch.object(supervisor.shutil, "which", side_effect=lambda name: "/usr/bin/bwrap" if name == "bwrap" else None):
            with self.assertRaises(ValueError):
                supervisor.sandbox_command(f.project, profile, client, [str(alias)], f.session_path,
                                           f.session, f.delivery, SimpleNamespace())

    def test_project_git_directory_is_readonly_but_pointer_cannot_select_host_home(self):
        normal = self.fixture()
        (normal.project / ".git").mkdir()
        argv, *_ = self.arguments(normal)
        triples = [argv[index:index + 3] for index, value in enumerate(argv) if value == "--ro-bind"]
        git_path = str(normal.project / ".git")
        self.assertIn(["--ro-bind", git_path, git_path], triples)
        malicious = self.fixture()
        (malicious.project / ".git").write_text("gitdir: " + str(malicious.root / "host-home") + "\n")
        with self.assertRaises(ValueError):
            self.arguments(malicious)


class SupervisorSyntheticTests(RuntimeFixtureTests):
    def test_reference_inside_private_profile_refuses_before_admission(self):
        f = self.fixture("handoff-only")
        profile, client, executable, gate, home = self.launch_inputs(f)
        selected = profile / "reference.bin"
        selected.write_bytes(b"Synthetic selected reference.\n")
        self.select_reference_source(f, selected)
        with mock.patch.object(phase, "start", side_effect=AssertionError("Colliding source cannot admit")) as start, \
                mock.patch.object(Path, "home", return_value=home), \
                mock.patch.dict(os.environ, {"DEVFORGE_DELIVERY_EXECUTABLE": str(gate)}):
            result = supervisor.run(f.session_path, f.state, profile, [str(executable)], 30, client,
                                    synthetic=True)
        start.assert_not_called()
        self.assertEqual(result["status"], "FAIL", result)
        self.assertTrue(any("Private profile overlaps" in issue for issue in result["issues"]), result)
        self.assertFalse(f.state.exists())
        self.assertEqual(selected.read_bytes(), b"Synthetic selected reference.\n")

    def run_fake_process(self, f, behavior="none", returncode=0, cleanup_absent=True):
        profile, client, executable, gate, home = self.launch_inputs(f)
        original_broker = supervisor.Broker
        original_read_text = Path.read_text
        captured = {}
        fake = SimpleNamespace(pid=987654321, returncode=returncode, poll=lambda: returncode)

        def make_broker(*args, **kwargs):
            captured["broker"] = original_broker(*args, **kwargs)
            if behavior == "ready-close-unquiescent":
                b = captured["broker"]
                original_close = b.close

                def close_with_unverified_quiescence():
                    original_close()
                    b.error = "Runtime callback service did not become quiescent"
                    return False

                b.close = close_with_unverified_quiescence
            return captured["broker"]

        def read_text(path, *args, **kwargs):
            if path == Path("/proc/987654321/stat"):
                return "987654321 (synthetic inert test double) " + " ".join(["0"] * 20)
            return original_read_text(path, *args, **kwargs)

        def popen(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            b = captured["broker"]
            if behavior != "none":
                self.callback(f, b, "SessionStart")
                f.write_outputs()
            if behavior.startswith("ready"):
                self.checkpoint(f, "Recover", fixture_support.PhaseStateTests.recover_content())
                self.assertEqual(self.callback(f, b, "Stop")["decision"], "block")
                self.checkpoint(f, "Focus", fixture_support.PhaseStateTests.focus_content())
                self.assertEqual(self.callback(f, b, "Stop"), {})
                self.assertEqual(phase.context(f.state)["status"], "READY")
                self.assertFalse(f.receipt_path.exists())
                if behavior == "ready-interrupt":
                    self.assertEqual(self.callback(f, b, "Interrupt"), {})
            return fake

        with mock.patch.object(supervisor, "Broker", side_effect=make_broker), \
                mock.patch.object(supervisor.subprocess, "Popen", side_effect=popen), \
                mock.patch.object(supervisor, "owned_exited", return_value=True), \
                mock.patch.object(supervisor, "stop_owned", return_value={"signals": [], "leader_reaped": True, "group_absent": cleanup_absent}), \
                mock.patch.object(Path, "read_text", read_text), mock.patch.object(Path, "home", return_value=home), \
                mock.patch.object(supervisor.shutil, "which", side_effect=lambda name: "/usr/bin/bwrap" if name == "bwrap" else None), \
                mock.patch.dict(os.environ, {"DEVFORGE_DELIVERY_EXECUTABLE": str(gate)}), \
                mock.patch.object(phase, "complete", wraps=phase.complete) as complete:
            result = supervisor.run(f.session_path, f.state, profile, [str(executable)], 30, client, synthetic=True)
            captured["complete_calls"] = complete.call_count
        return result, captured

    def test_default_native_refusal_occurs_before_task_admission_or_process(self):
        f = self.fixture("handoff-only")
        with mock.patch.object(phase, "start", side_effect=AssertionError("No task admission authorized")) as start, \
                mock.patch.object(supervisor.subprocess, "Popen", side_effect=AssertionError("No native launch authorized")) as popen:
            result = supervisor.run(f.session_path, f.state, f.root / "absent-profile", ["codex"], 30,
                                    f.root / "absent-client")
        self.assertEqual(result["status"], "COULD_NOT_RUN")
        self.assertIs(result["native_launch_admitted"], False)
        self.assertEqual(result["native_completion"], "NOT_EVALUATED")
        start.assert_not_called()
        popen.assert_not_called()
        self.assertFalse(f.state.exists())
        self.assertFalse(f.receipt_path.exists())

    def test_successful_process_without_hook_observations_cannot_publish(self):
        f = self.fixture("handoff-only")
        result, captured = self.run_fake_process(f)
        self.assertEqual(result["status"], "FAIL", result)
        self.assertEqual(result["worker"]["event_count"], 0)
        self.assertEqual(captured["complete_calls"], 0)
        self.assertFalse(f.receipt_path.exists())

    def test_artifacts_and_start_callback_without_ready_cannot_publish(self):
        f = self.fixture("handoff-only")
        result, captured = self.run_fake_process(f, behavior="artifacts")
        self.assertEqual(result["status"], "FAIL", result)
        self.assertEqual(result["worker"]["event_count"], 1)
        self.assertTrue(f.handoff_path.exists())
        self.assertEqual(captured["complete_calls"], 0)
        self.assertFalse(f.receipt_path.exists())

    def test_ready_then_successful_synthetic_exit_publishes_with_explicit_limits(self):
        f = self.fixture("handoff-only")
        result, captured = self.run_fake_process(f, behavior="ready")
        self.assertEqual(result["status"], "COMPLETED", result)
        self.assertTrue(f.receipt_path.exists())
        self.assertEqual(captured["complete_calls"], 1)
        self.assertEqual(result["native_completion"], "NOT_EVALUATED")
        self.assertEqual(result["long_lived_task_completion"], "NOT_IMPLEMENTED")
        self.assertEqual(result["native_provider_admission"], "NOT_VALIDATED")
        self.assertIs(result["native_launch_admitted"], False)
        self.assertEqual(result["execution_kind"], "SYNTHETIC")
        self.assertEqual(result["user_delivery"], "NOT_RUN")
        self.assertEqual(result["worker"]["callback_origin"], "NOT_AUTHENTICATED")
        self.assertEqual(result["worker"]["completion_boundary"], "OWNED_PROCESS_EXIT")
        self.assertIn("--unshare-net", captured["argv"])
        self.assertIs(captured["kwargs"]["start_new_session"], True)

    def test_ready_with_unsuccessful_synthetic_exit_cannot_publish(self):
        f = self.fixture("handoff-only")
        result, captured = self.run_fake_process(f, behavior="ready", returncode=3)
        self.assertEqual(result["status"], "FAIL", result)
        self.assertEqual(result["worker"]["exit_code"], 3)
        self.assertEqual(captured["complete_calls"], 0)
        self.assertFalse(f.receipt_path.exists())

    def test_ready_with_interrupt_callback_cannot_publish_after_zero_exit(self):
        f = self.fixture("handoff-only")
        result, captured = self.run_fake_process(f, behavior="ready-interrupt")
        self.assertEqual(result["status"], "COULD_NOT_RUN", result)
        self.assertEqual(result["worker"]["exit_code"], 0)
        self.assertEqual(captured["complete_calls"], 0)
        self.assertFalse(f.receipt_path.exists())

    def test_ready_with_unverified_group_cleanup_cannot_publish(self):
        f = self.fixture("handoff-only")
        result, captured = self.run_fake_process(f, behavior="ready", cleanup_absent=False)
        self.assertEqual(result["status"], "COULD_NOT_RUN", result)
        self.assertEqual(result["worker"]["exit_code"], 0)
        self.assertIs(result["worker"]["cleanup"]["group_absent"], False)
        self.assertEqual(captured["complete_calls"], 0)
        self.assertFalse(f.receipt_path.exists())

    def test_ready_with_unverified_broker_quiescence_cannot_publish(self):
        f = self.fixture("handoff-only")
        result, captured = self.run_fake_process(f, behavior="ready-close-unquiescent")
        self.assertEqual(result["status"], "COULD_NOT_RUN", result)
        self.assertEqual(result["worker"]["exit_code"], 0)
        self.assertIn("quiescent", " ".join(result["issues"]))
        self.assertEqual(captured["complete_calls"], 0)
        self.assertFalse(f.receipt_path.exists())

    def test_deadline_rechecked_after_sandbox_preparation_before_spawn(self):
        f = self.fixture("handoff-only")
        profile, client, executable, gate, home = self.launch_inputs(f)
        def preparation_expires(*args, **kwargs):
            clock.now.return_value = NOW + timedelta(hours=2)
            return ["never-executed"]

        with mock.patch.object(supervisor, "sandbox_command", side_effect=preparation_expires) as prepare, \
                mock.patch.object(supervisor.subprocess, "Popen", side_effect=AssertionError("Expired work cannot launch")) as popen, \
                mock.patch.dict(os.environ, {"DEVFORGE_DELIVERY_EXECUTABLE": str(gate)}), \
                mock.patch.object(supervisor, "datetime") as clock:
            clock.fromisoformat.side_effect = datetime.fromisoformat
            clock.now.return_value = NOW
            result = supervisor.run(f.session_path, f.state, profile, [str(executable)], 30, client, synthetic=True)
        self.assertEqual(result["status"], "COULD_NOT_RUN", result)
        self.assertIn("deadline", " ".join(result["issues"]).lower())
        prepare.assert_called_once()
        popen.assert_not_called()
        self.assertFalse(f.receipt_path.exists())


class ResultDestinationTests(RuntimeFixtureTests):
    def test_result_cannot_overwrite_selected_reference_even_before_it_exists(self):
        f = self.fixture()
        profile, client, executable, gate, home = self.launch_inputs(f)
        selected = f.operator / "reference.bin"
        self.assertFalse(selected.exists())
        self.select_reference_source(f, selected)
        args = SimpleNamespace(contract=f.session_path, state=f.state, profile=profile,
                               client_root=client, result=selected)
        with self.assertRaisesRegex(ValueError, "overlaps a selected task path"):
            supervisor.result_destination(args)
        self.assertFalse(selected.exists())

    def test_external_result_must_be_disjoint_and_exclusive(self):
        f = self.fixture()
        profile, client, executable, gate, home = self.launch_inputs(f)
        args = SimpleNamespace(contract=f.session_path, state=f.state, profile=profile, client_root=client,
                               result=f.operator / "final-result.json")
        self.assertEqual(supervisor.result_destination(args), args.result)
        self.assertFalse(args.result.exists())
        for destination in (f.project / "result.json", profile / "result.json", f.receipt_path,
                            f.session_path, f.installed_path, client / "result.json"):
            with self.subTest(destination=str(destination)):
                args.result = destination
                with self.assertRaises(ValueError):
                    supervisor.result_destination(args)
        args.result = f.operator / "occupied.json"
        args.result.write_bytes(b"Retain exact existing output bytes.\n")
        with self.assertRaises(ValueError):
            supervisor.result_destination(args)
        self.assertEqual(args.result.read_bytes(), b"Retain exact existing output bytes.\n")

    def test_cli_result_preflight_refusal_precedes_run_and_any_result_write(self):
        f = self.fixture()
        profile, client, executable, gate, home = self.launch_inputs(f)
        argv = ["delivery-runtime", "run", "--contract", str(f.session_path), "--state", str(f.state),
                "--profile", str(profile), "--result", str(f.receipt_path), "--client-root", str(client),
                "--synthetic", "--", str(executable)]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(sys, "stderr", io.StringIO()), \
                mock.patch.object(supervisor, "run", side_effect=AssertionError("Refused destination cannot admit")) as run, \
                mock.patch.object(supervisor, "write_new", side_effect=AssertionError("Refused destination cannot write")) as write:
            self.assertEqual(supervisor.main(), 2)
        run.assert_not_called()
        write.assert_not_called()
        self.assertFalse(f.receipt_path.exists())
        self.assertFalse(f.state.exists())


class OwnedProcessObservationTests(RuntimeFixtureTests):
    def test_exit_observation_preserves_child_identity_with_wnowait(self):
        fake = SimpleNamespace(pid=987654321)
        with mock.patch.object(os, "waitid", return_value=None) as waitid:
            self.assertFalse(supervisor.owned_exited(fake))
        waitid.assert_called_once_with(os.P_PID, fake.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        with mock.patch.object(os, "waitid", return_value=SimpleNamespace(si_pid=fake.pid)):
            self.assertTrue(supervisor.owned_exited(fake))

    def test_all_group_signals_precede_reaping_and_no_signal_follows_wait(self):
        trace = []
        fake = SimpleNamespace(pid=987654321, returncode=None)

        def reap(**kwargs):
            trace.append("wait")
            fake.returncode = 0

        fake.wait = reap
        with mock.patch.object(supervisor, "owned_exited", return_value=True), \
                mock.patch.object(supervisor, "group_exists", side_effect=lambda pid: fake.returncode is None), \
                mock.patch.object(os, "killpg", side_effect=lambda pid, sig: trace.append(sig.name)):
            result = supervisor.stop_owned(fake)
        self.assertEqual(trace, ["SIGTERM", "SIGKILL", "wait"])
        self.assertIs(result["leader_reaped"], True)
        self.assertIs(result["group_absent"], True)
        self.assertEqual(result["issues"], [])

    def test_already_reaped_child_refuses_signals_without_retry(self):
        fake = SimpleNamespace(pid=987654321, returncode=0, wait=mock.Mock())
        with mock.patch.object(supervisor, "owned_exited", side_effect=ChildProcessError("Already reaped")), \
                mock.patch.object(os, "killpg", side_effect=AssertionError("Identity was not retained")) as kill:
            result = supervisor.stop_owned(fake)
        self.assertIs(result["group_absent"], False)
        self.assertTrue(result["issues"])
        kill.assert_not_called()
        fake.wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
