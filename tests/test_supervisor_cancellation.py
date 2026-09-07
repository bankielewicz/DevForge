"""Independent quiescence and latched-cancellation regressions.

Expectations were frozen before the cancellation implementation was inspected.
All subprocesses, signal registrations, terminal descriptors, and broker threads
are inert doubles. Existing phase/core logic supplies real local receipt bytes.
This is neither actual OS signal propagation nor native client evidence.
"""

from __future__ import annotations

import copy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_managed_delivery as support  # noqa: E402

supervisor = support.supervisor
phase = support.phase


class SupervisorCancellationTests(support.ManagedFixtures):
    def signal_registry(self, stack):
        previous = {number: object() for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
        registry = dict(previous)
        calls = []

        def register(number, handler):
            calls.append((number, handler))
            old = registry[number]
            registry[number] = handler
            return old

        stack.enter_context(mock.patch.object(signal, "getsignal", side_effect=lambda number: registry[number]))
        stack.enter_context(mock.patch.object(signal, "signal", side_effect=register))
        return previous, registry, calls

    def controlled_run(self, *, cause="pump", late_commit=True, drift=False,
                       signal_number=None, commit_before_signal=False, io_mode="interactive-tty"):
        fixture = self.fixture("handoff-only")
        profile, client, executable, gate, _ = self.launch_inputs(fixture)
        real_broker = supervisor.Broker
        real_read_text = Path.read_text
        trace = []
        captured = {"trace": trace, "fixture": fixture, "quiesced": False}
        process = SimpleNamespace(pid=987654321, returncode=None)
        terminal_state = {"stdin_eof": False, "pty_eof": False, "pending_input": 0, "pending_output": 0}

        class Terminal:
            def child_stdio(self):
                return 91

            def entry_command(self, argv):
                return list(argv)

            def child_started(self):
                trace.append("slave_released")

            def enter(self):
                trace.append("terminal_entered")

            def resize(self):
                pass

            def pump(self, timeout=0.05):
                if "main_pump" not in trace and not captured["quiesced"]:
                    trace.append("main_pump")
                    if cause == "pump":
                        raise OSError("FROZEN_MAIN_PUMP_FAILURE")
                    if signal_number is not None:
                        captured["cancellation"].handle(signal_number, None)
                        trace.append("signal_latched")
                if captured["quiesced"]:
                    trace.append("terminal_drained")
                    return {**terminal_state, "pty_eof": True}
                return dict(terminal_state)

            def close(self):
                trace.append("terminal_restored")

        terminal = Terminal()

        def finalize_inflight():
            # Resume the completion portion of a Stop admitted before the pump
            # failed. This does not dispatch a new callback after cancellation.
            trace.append("inflight_completion_resumed")
            value = phase.complete(fixture.state)
            self.assertEqual(value["status"], "COMPLETED", value)
            captured["broker"]._remember_completed(value)
            captured["historical"] = copy.deepcopy(captured["broker"].task_result)
            captured["receipt_bytes"] = fixture.receipt_path.read_bytes()

        def make_broker(*args, **kwargs):
            broker = real_broker(*args, **kwargs)
            captured["broker"] = broker
            original_close = broker.close
            broker.thread = SimpleNamespace(ident=None, start=lambda: trace.append("broker_started"),
                                            is_alive=lambda: not captured["quiesced"], join=lambda timeout=None: None)

            def close():
                trace.append("broker_close_entered")
                if not captured["quiesced"]:
                    if late_commit and "historical" not in captured:
                        self.assertIsNone(broker.task_result)
                        finalize_inflight()
                    captured["quiesced"] = True
                    trace.append("broker_quiesced")
                    if drift:
                        fixture.handoff_path.write_bytes(fixture.handoff_path.read_bytes() + b"\nFROZEN_POST_JOIN_DRIFT\n")
                        trace.append("post_join_drift")
                original_close()

            broker.close = close
            self.addCleanup(lambda: original_close() if broker.socket_root.exists() else None)
            return broker

        def popen(*args, **kwargs):
            trace.append("process_admitted")
            broker = captured["broker"]
            if late_commit or commit_before_signal:
                self.to_focus(fixture, broker)
                self.focus_checkpoint(fixture)
                ready = phase.advance(fixture.state)
                self.assertEqual(ready["status"], "READY", ready)
                self.assertIsNone(broker.task_result)
                self.assertFalse(fixture.receipt_path.exists())
                trace.append("stop_completion_already_admitted")
            if commit_before_signal:
                finalize_inflight()
            return process

        def owned_exited(_):
            if cause == "operation" and "operation_failed" not in trace:
                trace.append("operation_failed")
                raise OSError("FROZEN_OWNED_OPERATION_FAILURE")
            return process.returncode is not None

        def cleanup(_):
            trace.append("owned_cleanup")
            process.returncode = -signal.SIGTERM
            return {"signals": ["SIGTERM"], "leader_reaped": True, "group_absent": True, "issues": []}

        def read_text(path, *args, **kwargs):
            if path == Path("/proc/987654321/stat"):
                return "987654321 (inert cancellation fixture) " + " ".join(["0"] * 20)
            return real_read_text(path, *args, **kwargs)

        with ExitStack() as stack:
            self.signal_registry(stack)
            cancellation_type = supervisor.Cancellation

            def cancellation():
                captured["cancellation"] = cancellation_type()
                return captured["cancellation"]

            stack.enter_context(mock.patch.object(supervisor, "Cancellation", side_effect=cancellation))
            stack.enter_context(mock.patch.object(supervisor, "Broker", side_effect=make_broker))
            stack.enter_context(mock.patch.object(supervisor.subprocess, "Popen", side_effect=popen))
            stack.enter_context(mock.patch.object(supervisor, "owned_exited", side_effect=owned_exited))
            stack.enter_context(mock.patch.object(supervisor, "stop_owned", side_effect=cleanup))
            stack.enter_context(mock.patch.object(Path, "read_text", read_text))
            stack.enter_context(mock.patch.object(supervisor, "sandbox_command", return_value=["inert-command"]))
            stack.enter_context(mock.patch.object(supervisor.native_terminal, "NativeTerminal", return_value=terminal))
            stack.enter_context(mock.patch.dict(os.environ, {"DEVFORGE_DELIVERY_EXECUTABLE": str(gate)}))
            result = supervisor.run(fixture.session_path, fixture.state, profile, [str(executable)], 30,
                                    client, synthetic=True, completion_mode="managed-session", io_mode=io_mode)
        return result, captured

    def assert_quiescent_failure(self, result, captured, expected_issue):
        self.assertEqual(result["status"], "COULD_NOT_RUN", result)
        self.assertIn(expected_issue, " ".join(result["issues"]))
        self.assertEqual(result["task_result"], captured["historical"])
        self.assertEqual(captured["fixture"].receipt_path.read_bytes(), captured["receipt_bytes"])
        self.assertEqual(result["task_result"]["receipt_sha256"], support.digest(captured["receipt_bytes"]))
        self.assertTrue(result["worker"]["cleanup"]["leader_reaped"])
        self.assertTrue(result["worker"]["cleanup"]["group_absent"])
        trace = captured["trace"]
        self.assertLess(trace.index("owned_cleanup"), trace.index("terminal_restored"))
        self.assertLess(trace.index("broker_quiesced"), trace.index("terminal_restored"))
        self.assertIs(result["native_launch_admitted"], False)
        self.assertEqual(result["native_completion"], "NOT_EVALUATED")
        self.assertEqual(result["user_delivery"], "NOT_OBSERVED")

    def test_main_pump_error_captures_commit_that_finishes_during_broker_join(self):
        result, captured = self.controlled_run()
        self.assert_quiescent_failure(result, captured, "FROZEN_MAIN_PUMP_FAILURE")
        self.assertEqual(result["current_applicability"], "VERIFIED")
        self.assertLess(captured["trace"].index("main_pump"), captured["trace"].index("inflight_completion_resumed"))

    def test_owned_operation_error_captures_commit_that_finishes_during_broker_join(self):
        result, captured = self.controlled_run(cause="operation")
        self.assert_quiescent_failure(result, captured, "FROZEN_OWNED_OPERATION_FAILURE")
        self.assertEqual(result["current_applicability"], "VERIFIED")

    def test_post_join_drift_retains_history_and_rechecks_current_applicability(self):
        result, captured = self.controlled_run(drift=True)
        self.assert_quiescent_failure(result, captured, "FROZEN_MAIN_PUMP_FAILURE")
        self.assertEqual(result["current_applicability"], "NOT_VERIFIED")
        self.assertTrue(result["current_verification_issues"])
        self.assertTrue(result["task_result"]["receipt_verified"])
        self.assertNotEqual(support.digest(captured["fixture"].handoff_path.read_bytes()),
                            json.loads(captured["receipt_bytes"])["outputs"]["docs/handoff.md"])

    def test_pump_failure_without_inflight_completion_cannot_publish(self):
        result, captured = self.controlled_run(late_commit=False)
        self.assertEqual(result["status"], "COULD_NOT_RUN", result)
        self.assertNotIn("task_result", result)
        self.assertFalse(captured["fixture"].receipt_path.exists())
        trace = captured["trace"]
        self.assertLess(trace.index("owned_cleanup"), trace.index("terminal_restored"))
        self.assertLess(trace.index("broker_quiesced"), trace.index("terminal_restored"))

    def test_each_signal_is_latched_without_throwing_or_performing_io(self):
        for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(signal=number), mock.patch.object(phase, "complete", side_effect=AssertionError("Handler cannot perform phase I/O")), \
                    mock.patch.object(supervisor, "stop_owned", side_effect=AssertionError("Handler cannot perform cleanup")), \
                    mock.patch.object(os, "kill", side_effect=AssertionError("Handler cannot send signals")), \
                    mock.patch.object(os, "open", side_effect=AssertionError("Handler cannot open files")), \
                    mock.patch.object(Path, "open", side_effect=AssertionError("Handler cannot open files")), \
                    mock.patch("builtins.open", side_effect=AssertionError("Handler cannot open files")):
                cancellation = supervisor.Cancellation()
                self.assertIsNone(cancellation.signal_number)
                cancellation.handle(number, None)
                self.assertEqual(cancellation.signal_number, number)

    def test_repeated_signals_preserve_first_cancellation_reason(self):
        cancellation = supervisor.Cancellation()
        cancellation.handle(signal.SIGHUP, None)
        cancellation.handle(signal.SIGTERM, None)
        cancellation.handle(signal.SIGINT, None)
        self.assertEqual(cancellation.signal_number, signal.SIGHUP)

    def test_install_and_restore_preserve_all_previous_handlers(self):
        with ExitStack() as stack:
            previous, registry, calls = self.signal_registry(stack)
            cancellation = supervisor.Cancellation()
            cancellation.install()
            self.assertEqual(set(number for number, _ in calls), set(previous))
            for number in previous:
                self.assertEqual(registry[number], cancellation.handle)
            registry[signal.SIGTERM](signal.SIGTERM, None)
            cancellation.restore()
            self.assertEqual(registry, previous)
            self.assertEqual(cancellation.signal_number, signal.SIGTERM)

    def test_restore_failure_remains_observable_and_does_not_skip_other_handlers(self):
        with ExitStack() as stack:
            previous, registry, _ = self.signal_registry(stack)
            cancellation = supervisor.Cancellation()
            cancellation.install()
            restored = []

            def restore(number, handler):
                restored.append(number)
                self.assertIs(handler, previous[number])
                if number == signal.SIGHUP:
                    raise OSError("FROZEN_HANDLER_RESTORE_FAILURE")
                registry[number] = handler

            with mock.patch.object(signal, "signal", side_effect=restore), self.assertRaises(Exception) as raised:
                cancellation.restore()
            self.assertIn("FROZEN_HANDLER_RESTORE_FAILURE", str(raised.exception))
            self.assertEqual(set(restored), set(previous))
            self.assertIs(registry[signal.SIGTERM], previous[signal.SIGTERM])
            self.assertIs(registry[signal.SIGINT], previous[signal.SIGINT])
            self.assertIn(signal.SIGHUP, cancellation.previous_handlers)
            self.assertNotIn(signal.SIGTERM, cancellation.previous_handlers)
            self.assertNotIn(signal.SIGINT, cancellation.previous_handlers)

    def test_latched_signal_refuses_before_task_terminal_or_process_admission(self):
        fixture = self.fixture("handoff-only")
        profile, client, executable, gate, _ = self.launch_inputs(fixture)
        cancellation = supervisor.Cancellation()
        cancellation.install = lambda: cancellation.handle(signal.SIGHUP, None)
        cancellation.restore = mock.Mock()
        with mock.patch.object(supervisor, "Cancellation", return_value=cancellation), \
                mock.patch.object(phase, "start", side_effect=AssertionError("Cancelled task cannot be admitted")), \
                mock.patch.object(supervisor.native_terminal, "NativeTerminal", side_effect=AssertionError("Cancelled terminal cannot be admitted")), \
                mock.patch.dict(os.environ, {"DEVFORGE_DELIVERY_EXECUTABLE": str(gate)}):
            result = supervisor.run(fixture.session_path, fixture.state, profile, [str(executable)], 30,
                                    client, synthetic=True, completion_mode="managed-session", io_mode="interactive-tty")
        self.assertEqual(result["status"], "COULD_NOT_RUN", result)
        self.assertIn("SIGHUP", " ".join(result["issues"]))
        self.assertFalse(fixture.state.exists())
        self.assertFalse(fixture.receipt_path.exists())
        cancellation.restore.assert_called_once()

    def test_live_latched_signal_cleans_restores_and_withholds_absent_receipt(self):
        for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(signal=number):
                result, captured = self.controlled_run(cause="signal", late_commit=False, signal_number=number)
                self.assertEqual(result["status"], "COULD_NOT_RUN", result)
                self.assertIn(signal.Signals(number).name, " ".join(result["issues"]))
                self.assertNotIn("task_result", result)
                self.assertFalse(captured["fixture"].receipt_path.exists())
                trace = captured["trace"]
                self.assertLess(trace.index("signal_latched"), trace.index("owned_cleanup"))
                self.assertLess(trace.index("owned_cleanup"), trace.index("terminal_restored"))
                self.assertLess(trace.index("broker_quiesced"), trace.index("terminal_restored"))

    def test_live_signal_preserves_already_committed_task_and_receipt(self):
        result, captured = self.controlled_run(cause="signal", late_commit=False,
                                               signal_number=signal.SIGTERM, commit_before_signal=True)
        self.assert_quiescent_failure(result, captured, "SIGTERM")
        self.assertEqual(result["current_applicability"], "VERIFIED")

    def test_signal_still_captures_previously_admitted_stop_finishing_during_join(self):
        result, captured = self.controlled_run(cause="signal", late_commit=True,
                                               signal_number=signal.SIGTERM)
        self.assert_quiescent_failure(result, captured, "SIGTERM")
        self.assertEqual(result["current_applicability"], "VERIFIED")
        trace = captured["trace"]
        self.assertLess(trace.index("stop_completion_already_admitted"), trace.index("signal_latched"))
        self.assertLess(trace.index("signal_latched"), trace.index("inflight_completion_resumed"))

    def test_signal_between_task_admission_and_spawn_prevents_process_launch(self):
        fixture = self.fixture("handoff-only")
        profile, client, executable, gate, _ = self.launch_inputs(fixture)
        cancellation = supervisor.Cancellation()
        real_broker = supervisor.Broker
        captured = {}

        def broker(*args, **kwargs):
            value = real_broker(*args, **kwargs)
            captured["broker"] = value
            self.addCleanup(lambda: value.close() if value.socket_root.exists() else None)
            return value

        def sandbox(*args, **kwargs):
            self.assertTrue(fixture.state.exists(), "This stimulus must follow task admission")
            cancellation.handle(signal.SIGTERM, None)
            return ["must-never-launch"]

        with ExitStack() as stack:
            self.signal_registry(stack)
            stack.enter_context(mock.patch.object(supervisor, "Cancellation", return_value=cancellation))
            stack.enter_context(mock.patch.object(supervisor, "Broker", side_effect=broker))
            stack.enter_context(mock.patch.object(supervisor, "sandbox_command", side_effect=sandbox))
            stack.enter_context(mock.patch.dict(os.environ, {"DEVFORGE_DELIVERY_EXECUTABLE": str(gate)}))
            result = supervisor.run(fixture.session_path, fixture.state, profile, [str(executable)], 30,
                                    client, synthetic=True, completion_mode="managed-session")
        self.assertEqual(result["status"], "COULD_NOT_RUN", result)
        self.assertIn("SIGTERM", " ".join(result["issues"]))
        self.assertFalse(fixture.receipt_path.exists())
        self.assertFalse((captured["broker"].observations / "owned-process.json").exists())

    def test_latched_cancellation_prevents_new_stop_completion(self):
        fixture = self.fixture("handoff-only")
        broker = self.broker(fixture)
        self.to_focus(fixture, broker)
        self.focus_checkpoint(fixture)
        cancellation = supervisor.Cancellation()
        cancellation.handle(signal.SIGTERM, None)
        broker.cancellation = cancellation
        with mock.patch.object(phase, "complete", side_effect=AssertionError("Latched cancellation cannot admit new completion")), \
                self.assertRaisesRegex(ValueError, "SIGTERM"):
            self.callback(fixture, broker, "Stop")
        self.assertFalse(fixture.receipt_path.exists())
        self.assertIsNone(broker.task_result)


if __name__ == "__main__":
    import unittest
    unittest.main()
