"""Pure terminal-transport contracts; all descriptor and terminal operations are mocked.

No PTY, native client, authentication, model, signal, or child process is run.
The tests establish local bridge behavior only, not native TUI delivery.
"""

from __future__ import annotations

import copy
import errno
import fcntl
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import termios
import tty
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from delivery import native_terminal as terminal  # noqa: E402


class ExecRequested(BaseException):
    """The mocked exec succeeded, so the entry function must not return."""


class FakeDescriptors:
    master = 31
    slave = 32

    def __init__(self, case):
        self.case = case
        self.flags = {0: os.O_RDONLY, 1: os.O_WRONLY, self.master: os.O_RDWR}
        self.original_flags = dict(self.flags)
        self.original_attrs = [
            termios.BRKINT | termios.ICRNL | termios.IXON,
            termios.OPOST,
            termios.CS8,
            termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG,
            termios.B38400,
            termios.B38400,
            [b"\x00"] * termios.NCCS,
        ]
        self.attrs = {0: copy.deepcopy(self.original_attrs)}
        self.winsize = struct.pack("HHHH", 37, 101, 0, 0)
        self.closed = []
        self.ioctls = []
        self.raw_calls = []
        self.ready = []
        self.reads = {0: [], self.master: []}
        self.read_requests = []
        self.writes = {1: [], self.master: []}
        self.write_requests = []
        self.accepted = {1: bytearray(), self.master: bytearray()}
        self.select_calls = []
        self.mocks = {}
        operations = (
            (os, "isatty", lambda fd: fd in (0, 1, 2)),
            (os, "openpty", lambda: (self.master, self.slave)),
            (os, "set_blocking", self.set_blocking),
            (os, "set_inheritable", self.set_inheritable),
            (os, "close", self.close),
            (os, "read", self.read),
            (os, "write", self.write),
            (fcntl, "fcntl", self.fcntl),
            (fcntl, "ioctl", self.ioctl),
            (termios, "tcgetattr", self.tcgetattr),
            (termios, "tcsetattr", self.tcsetattr),
            (tty, "setraw", self.setraw),
            (select, "select", self.select),
            (os, "setsid", lambda: None),
            (os, "getpid", lambda: 501),
            (os, "getsid", lambda pid: 501),
            (os, "getpgrp", lambda: 501),
            (os, "tcgetpgrp", lambda fd: 501),
            (os, "execv", self.execv),
        )
        for module, name, effect in operations:
            self.mocks[name] = case.enterContext(mock.patch.object(module, name, side_effect=effect))
        case.enterContext(mock.patch.object(sys, "platform", "linux"))
        for module, name in ((os, "kill"), (os, "killpg"), (os, "waitpid"),
                             (os, "fork"), (subprocess, "Popen")):
            self.mocks[name] = case.enterContext(mock.patch.object(
                module, name, side_effect=AssertionError("Process operations are forbidden")))

    def close(self, fd):
        self.case.assertIn(fd, (self.master, self.slave), "Parent stdio must never close")
        self.case.assertNotIn(fd, self.closed, "Owned descriptor closed twice")
        self.closed.append(fd)

    def set_blocking(self, fd, blocking):
        self.case.assertEqual(fd, self.master)
        self.case.assertIs(blocking, False)
        self.flags[fd] |= os.O_NONBLOCK

    def set_inheritable(self, fd, inheritable):
        self.case.assertIn(fd, (self.master, self.slave))
        self.case.assertIs(inheritable, False)

    def fcntl(self, fd, operation, argument=None):
        self.case.assertIn(fd, self.flags)
        if operation == fcntl.F_GETFL:
            return self.flags[fd]
        self.case.assertEqual(operation, fcntl.F_SETFL)
        self.flags[fd] = argument
        return 0

    def ioctl(self, fd, operation, argument=None):
        self.ioctls.append((fd, operation, argument))
        if operation == termios.TIOCGWINSZ:
            self.case.assertEqual(fd, 0)
            return self.winsize
        if operation == termios.TIOCSCTTY:
            self.case.assertEqual(fd, 0)
            return 0
        self.case.assertEqual(operation, termios.TIOCSWINSZ)
        self.case.assertIn(fd, (self.master, self.slave))
        return 0

    def tcgetattr(self, fd):
        self.case.assertEqual(fd, 0)
        return copy.deepcopy(self.attrs[fd])

    def tcsetattr(self, fd, when, attrs):
        self.case.assertIn(fd, (0, self.slave))
        self.case.assertIn(when, (termios.TCSANOW, termios.TCSADRAIN, termios.TCSAFLUSH))
        self.attrs[fd] = copy.deepcopy(attrs)

    def setraw(self, fd, when=termios.TCSAFLUSH):
        self.case.assertEqual(fd, 0)
        self.raw_calls.append(fd)
        self.attrs[0][3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG | termios.IEXTEN)

    def select(self, readers, writers, exceptional, timeout):
        self.select_calls.append((list(readers), list(writers), list(exceptional), timeout))
        self.case.assertFalse(exceptional)
        self.case.assertGreaterEqual(timeout, 0)
        self.case.assertLessEqual(timeout, 0.05)
        value = self.ready.pop(0) if self.ready else ([], [], [])
        if callable(value):
            value = value(readers, writers)
        for actual, offered in zip(value, (readers, writers, exceptional)):
            self.case.assertTrue(set(actual).issubset(offered))
        return value

    def read(self, fd, size):
        self.case.assertIn(fd, self.reads)
        self.case.assertGreater(size, 0)
        self.case.assertLessEqual(size, 16384)
        self.read_requests.append((fd, size))
        self.case.assertTrue(self.reads[fd], "Read must be scripted")
        value = self.reads[fd].pop(0)
        if isinstance(value, BaseException):
            raise value
        if len(value) > size:
            self.reads[fd].insert(0, value[size:])
        return value[:size]

    def write(self, fd, data):
        self.case.assertIn(fd, self.writes)
        self.case.assertGreater(len(data), 0)
        self.case.assertLessEqual(len(data), 16384)
        self.write_requests.append((fd, bytes(data)))
        value = self.writes[fd].pop(0) if self.writes[fd] else len(data)
        if isinstance(value, BaseException):
            raise value
        self.case.assertLessEqual(value, len(data))
        self.accepted[fd].extend(data[:value])
        return value

    @staticmethod
    def execv(path, argv):
        raise ExecRequested()


class NativeTerminalTests(unittest.TestCase):
    def setUp(self):
        self.fd = FakeDescriptors(self)

    def bridge(self, *, entered=True):
        bridge = terminal.NativeTerminal()
        self.addCleanup(bridge.close)
        if entered:
            bridge.enter()
            bridge.child_started()
        return bridge

    def test_constructor_copies_terminal_state_and_only_changes_owned_master_flags(self):
        bridge = self.bridge(entered=False)
        self.assertEqual(bridge.child_stdio(), self.fd.slave)
        self.assertEqual(self.fd.attrs[self.fd.slave], self.fd.original_attrs)
        self.assertEqual(self.fd.flags[0], self.fd.original_flags[0])
        self.assertEqual(self.fd.flags[1], self.fd.original_flags[1])
        self.assertTrue(self.fd.flags[self.fd.master] & os.O_NONBLOCK)
        for fd in (0, 1, 2):
            self.fd.mocks["isatty"].assert_any_call(fd)
        self.assertIn((self.fd.master, termios.TIOCSWINSZ, self.fd.winsize), self.fd.ioctls)

    def test_each_non_tty_stream_refuses_before_allocating_a_pty(self):
        for missing in (0, 1, 2):
            with self.subTest(fd=missing):
                self.fd.mocks["isatty"].side_effect = lambda fd: fd != missing
                with self.assertRaises(OSError):
                    terminal.NativeTerminal()
        self.fd.mocks["openpty"].assert_not_called()
        self.assertEqual(self.fd.closed, [])

    def test_non_linux_refuses_before_allocating_a_pty(self):
        with mock.patch.object(sys, "platform", "darwin"), self.assertRaises(OSError):
            terminal.NativeTerminal()
        self.fd.mocks["openpty"].assert_not_called()

    def test_terminal_snapshot_failure_refuses_before_pty_allocation(self):
        self.fd.mocks["tcgetattr"].side_effect = termios.error(errno.ENOTTY, "snapshot failed")
        with self.assertRaises(OSError) as caught:
            terminal.NativeTerminal()
        self.assertEqual(caught.exception.errno, errno.ENOTTY)
        self.fd.mocks["openpty"].assert_not_called()
        self.assertEqual(self.fd.closed, [])

    def test_slave_configuration_failure_closes_both_owned_descriptors(self):
        self.fd.mocks["tcsetattr"].side_effect = OSError(errno.EIO, "slave setup failed")
        with self.assertRaises(OSError):
            terminal.NativeTerminal()
        self.assertCountEqual(self.fd.closed, [self.fd.master, self.fd.slave])
        self.assertEqual(self.fd.flags[0], self.fd.original_flags[0])
        self.assertEqual(self.fd.flags[1], self.fd.original_flags[1])

    def test_real_termios_error_during_raw_mode_rolls_back_and_is_normalized(self):
        bridge = self.bridge(entered=False)

        def partial_raw(fd, when):
            self.fd.setraw(fd, when)
            raise termios.error(errno.EIO, "raw-mode setup failed")

        self.fd.mocks["setraw"].side_effect = partial_raw
        with self.assertRaises(OSError) as caught:
            bridge.enter()
        self.assertEqual(caught.exception.errno, errno.EIO)
        self.assertEqual(self.fd.attrs[0], self.fd.original_attrs)
        self.assertEqual(self.fd.flags[0], self.fd.original_flags[0])
        self.assertEqual(self.fd.flags[1], self.fd.original_flags[1])
        self.assertCountEqual(self.fd.closed, [self.fd.master, self.fd.slave])

    def test_failed_terminal_restoration_still_restores_flags_and_can_be_retried(self):
        bridge = self.bridge()
        failed = False

        def fail_restore_once(fd, when, attrs):
            nonlocal failed
            if fd == 0 and not failed:
                failed = True
                raise termios.error(errno.EIO, "restore failed")
            return self.fd.tcsetattr(fd, when, attrs)

        self.fd.mocks["tcsetattr"].side_effect = fail_restore_once
        with self.assertRaises(OSError) as caught:
            bridge.close()
        self.assertEqual(caught.exception.errno, errno.EIO)
        self.assertEqual(self.fd.flags[0], self.fd.original_flags[0])
        self.assertEqual(self.fd.flags[1], self.fd.original_flags[1])
        self.assertCountEqual(self.fd.closed, [self.fd.master, self.fd.slave])
        bridge.close()
        self.assertEqual(self.fd.attrs[0], self.fd.original_attrs)
        self.assertCountEqual(self.fd.closed, [self.fd.master, self.fd.slave])

    def test_child_started_closes_slave_once_and_close_restores_parent_state(self):
        bridge = self.bridge(entered=False)
        bridge.enter()
        self.assertFalse(self.fd.attrs[0][3] & (termios.ICANON | termios.ECHO | termios.ISIG))
        self.assertTrue(self.fd.flags[0] & os.O_NONBLOCK)
        self.assertTrue(self.fd.flags[1] & os.O_NONBLOCK)
        bridge.child_started()
        bridge.child_started()
        self.assertEqual(self.fd.closed, [self.fd.slave])
        bridge.close()
        bridge.close()
        self.assertEqual(self.fd.attrs[0], self.fd.original_attrs)
        self.assertEqual(self.fd.flags[0], self.fd.original_flags[0])
        self.assertEqual(self.fd.flags[1], self.fd.original_flags[1])
        self.assertCountEqual(self.fd.closed, [self.fd.slave, self.fd.master])
        for name in ("kill", "killpg", "waitpid", "fork", "Popen"):
            self.fd.mocks[name].assert_not_called()

    def test_partial_enter_failure_rolls_back_already_changed_parent_flags(self):
        bridge = self.bridge(entered=False)
        failed = False

        def fail_stdout_once(fd, operation, argument=None):
            nonlocal failed
            if fd == 1 and operation == fcntl.F_SETFL and argument & os.O_NONBLOCK and not failed:
                failed = True
                raise OSError(errno.EIO, "stdout setup failed")
            return self.fd.fcntl(fd, operation, argument)

        self.fd.mocks["fcntl"].side_effect = fail_stdout_once
        with self.assertRaises(OSError):
            bridge.enter()
        self.assertEqual(self.fd.attrs[0], self.fd.original_attrs)
        self.assertEqual(self.fd.flags[0], self.fd.original_flags[0])
        self.assertEqual(self.fd.flags[1], self.fd.original_flags[1])

    def test_resize_uses_current_parent_size_and_does_not_touch_closed_slave(self):
        bridge = self.bridge(entered=False)
        self.fd.ioctls.clear()
        self.fd.winsize = struct.pack("HHHH", 52, 144, 0, 0)
        bridge.resize()
        self.assertIn((self.fd.master, termios.TIOCSWINSZ, self.fd.winsize), self.fd.ioctls)
        bridge.child_started()
        self.fd.ioctls.clear()
        bridge.resize()
        self.assertIn((self.fd.master, termios.TIOCSWINSZ, self.fd.winsize), self.fd.ioctls)
        self.assertFalse(any(fd == self.fd.slave for fd, _, _ in self.fd.ioctls))

    def test_input_is_retained_across_blocking_and_partial_writes(self):
        bridge = self.bridge()
        self.fd.reads[0] = [b"abcde"]
        self.fd.ready = [([0], [], []), ([], [self.fd.master], []),
                         ([], [self.fd.master], []), ([], [self.fd.master], [])]
        self.fd.writes[self.fd.master] = [BlockingIOError(errno.EAGAIN, "busy"), 2, 3]
        states = [bridge.pump() for _ in range(4)]
        self.assertEqual([state["pending_input"] for state in states], [5, 5, 3, 0])
        self.assertEqual(bytes(self.fd.accepted[self.fd.master]), b"abcde")

    def test_output_is_retained_across_blocking_and_partial_writes(self):
        bridge = self.bridge()
        self.fd.reads[self.fd.master] = [b"native"]
        self.fd.ready = [([self.fd.master], [], []), ([], [1], []), ([], [1], []), ([], [1], [])]
        self.fd.writes[1] = [BlockingIOError(errno.EAGAIN, "busy"), 2, 4]
        states = [bridge.pump() for _ in range(4)]
        self.assertEqual([state["pending_output"] for state in states], [6, 6, 4, 0])
        self.assertEqual(bytes(self.fd.accepted[1]), b"native")

    def test_each_direction_stops_reading_at_65536_buffered_bytes(self):
        bridge = self.bridge()
        self.fd.reads[0] = [b"a" * 16384 for _ in range(4)]
        self.fd.reads[self.fd.master] = [b"b" * 16384 for _ in range(4)]
        self.fd.ready = [lambda readers, writers: (list(readers), [], []) for _ in range(5)]
        states = [bridge.pump() for _ in range(5)]
        for state in states:
            self.assertLessEqual(state["pending_input"], 65536)
            self.assertLessEqual(state["pending_output"], 65536)
        self.assertEqual(states[-1]["pending_input"], 65536)
        self.assertEqual(states[-1]["pending_output"], 65536)
        self.assertEqual(len(self.fd.read_requests), 8)
        self.assertEqual(self.fd.select_calls[-1][0], [])

    def test_read_would_block_preserves_non_eof_state(self):
        bridge = self.bridge()
        self.fd.ready = [([0, self.fd.master], [], [])]
        self.fd.reads[0] = [BlockingIOError(errno.EAGAIN, "busy")]
        self.fd.reads[self.fd.master] = [BlockingIOError(errno.EAGAIN, "busy")]
        state = bridge.pump()
        self.assertFalse(state["stdin_eof"])
        self.assertFalse(state["pty_eof"])
        self.assertEqual(state["pending_input"], 0)
        self.assertEqual(state["pending_output"], 0)

    def test_stdin_eof_is_distinct_from_pty_eof_and_stops_input_reads(self):
        bridge = self.bridge()
        self.fd.ready = [([0], [], []), ([], [], [])]
        self.fd.reads[0] = [b""]
        state = bridge.pump()
        self.assertTrue(state["stdin_eof"])
        self.assertFalse(state["pty_eof"])
        bridge.pump()
        self.assertNotIn(0, self.fd.select_calls[-1][0])

    def test_master_eio_is_pty_eof_and_preserves_queued_output(self):
        bridge = self.bridge()
        self.fd.ready = [([self.fd.master], [], []), ([self.fd.master], [], []), ([], [1], [])]
        self.fd.reads[self.fd.master] = [b"last bytes", OSError(errno.EIO, "slave closed")]
        bridge.pump()
        state = bridge.pump()
        self.assertTrue(state["pty_eof"])
        self.assertFalse(state["stdin_eof"])
        self.assertEqual(state["pending_output"], 10)
        self.assertEqual(bridge.pump()["pending_output"], 0)
        self.assertEqual(bytes(self.fd.accepted[1]), b"last bytes")

    def test_other_master_read_errors_propagate(self):
        bridge = self.bridge()
        self.fd.ready = [([self.fd.master], [], [])]
        self.fd.reads[self.fd.master] = [OSError(errno.EBADF, "bad descriptor")]
        with self.assertRaises(OSError) as caught:
            bridge.pump()
        self.assertEqual(caught.exception.errno, errno.EBADF)

    def test_timeout_rejects_bool_nonfinite_out_of_bounds_and_non_numbers(self):
        bridge = self.bridge()
        for timeout in (True, False, float("nan"), float("inf"), -float("inf"),
                        -0.001, 0.050001, 10 ** 1000, None, "0.01"):
            with self.subTest(timeout=timeout), self.assertRaises(OSError):
                bridge.pump(timeout=timeout)
        self.fd.mocks["select"].assert_not_called()
        self.fd.mocks["read"].assert_not_called()
        self.fd.mocks["write"].assert_not_called()

    def test_timeout_boundaries_remain_bounded(self):
        bridge = self.bridge()
        for timeout in (0, 0.05):
            bridge.pump(timeout=timeout)
        self.assertEqual([call[3] for call in self.fd.select_calls], [0, 0.05])


class NativeEntryTests(unittest.TestCase):
    def setUp(self):
        self.fd = FakeDescriptors(self)

    def test_entry_command_is_absolute_isolated_and_preserves_literal_arguments(self):
        argv = ["/opt/client/bin/client", "literal $(no-shell); ' argument", ""]
        command = terminal.NativeTerminal.entry_command(argv)
        self.assertEqual(command[:3], ["/usr/bin/python3", "-I", "-B"])
        self.assertTrue(Path(command[3]).is_absolute())
        self.assertEqual(Path(command[3]), Path(terminal.__file__).resolve())
        self.assertEqual(command[4:], ["--entry", "--", *argv])
        self.fd.mocks["execv"].assert_not_called()
        self.fd.mocks["openpty"].assert_not_called()

    def test_entry_claims_controlling_terminal_and_executes_exact_argv(self):
        argv = ["/opt/client/bin/client", "--literal", "x; y"]
        with self.assertRaises(ExecRequested):
            terminal._entry(argv)
        self.fd.mocks["execv"].assert_called_once_with(argv[0], argv)
        self.fd.mocks["getsid"].assert_called()
        self.fd.mocks["tcgetpgrp"].assert_called_once_with(0)
        self.assertTrue(any(fd == 0 and operation == termios.TIOCSCTTY
                            for fd, operation, _ in self.fd.ioctls))
        self.fd.mocks["openpty"].assert_not_called()
        self.fd.mocks["setsid"].assert_not_called()

    def test_entry_refuses_non_absolute_or_empty_argv(self):
        for argv in ([], ["client"], [""], ["./client"]):
            with self.subTest(argv=argv), self.assertRaises((OSError, ValueError)):
                terminal._entry(argv)
        self.fd.mocks["execv"].assert_not_called()

    def test_entry_refuses_each_non_tty_stream_before_exec(self):
        for missing in (0, 1, 2):
            self.fd.mocks["isatty"].side_effect = lambda fd: fd != missing
            with self.subTest(fd=missing), self.assertRaises(OSError):
                terminal._entry(["/opt/client"])
        self.fd.mocks["execv"].assert_not_called()

    def test_entry_requires_existing_session_leadership(self):
        self.fd.mocks["getsid"].return_value = 900
        self.fd.mocks["getsid"].side_effect = None
        with self.assertRaises(OSError):
            terminal._entry(["/opt/client"])
        self.fd.mocks["execv"].assert_not_called()
        self.assertFalse(any(operation == termios.TIOCSCTTY for _, operation, _ in self.fd.ioctls))

    def test_entry_refuses_matching_but_nonpositive_process_ids(self):
        for identity in (0, -1):
            with self.subTest(identity=identity):
                self.fd.mocks["getpid"].side_effect = lambda: identity
                self.fd.mocks["getsid"].side_effect = lambda pid: identity
                self.fd.mocks["getpgrp"].side_effect = lambda: identity
                self.fd.mocks["tcgetpgrp"].side_effect = lambda fd: identity
                with self.assertRaises(OSError):
                    terminal._entry(["/opt/client"])
        self.fd.mocks["ioctl"].assert_not_called()
        self.fd.mocks["execv"].assert_not_called()
        self.fd.mocks["setsid"].assert_not_called()

    def test_entry_refuses_controlling_terminal_failure(self):
        self.fd.mocks["ioctl"].side_effect = OSError(errno.EPERM, "not controlling terminal")
        with self.assertRaises(OSError):
            terminal._entry(["/opt/client"])
        self.fd.mocks["execv"].assert_not_called()

    def test_entry_refuses_foreground_group_mismatch(self):
        self.fd.mocks["tcgetpgrp"].side_effect = lambda fd: 900
        with self.assertRaises(OSError):
            terminal._entry(["/opt/client"])
        self.fd.mocks["execv"].assert_not_called()


class NativeNamespaceEntryTests(unittest.TestCase):
    def setUp(self):
        self.fd = FakeDescriptors(self)

    def assert_no_session_or_acquisition(self):
        for name in ("setsid", "ioctl", "execv", "openpty", "fork", "Popen"):
            self.fd.mocks[name].assert_not_called()

    def test_namespace_command_is_absolute_isolated_and_keeps_literal_argv(self):
        argv = ["/opt/client/bin/client", "$(literal); ' no shell", ""]
        command = terminal.NativeTerminal.namespace_entry_command(argv)
        self.assertEqual(command[:3], ["/usr/bin/python3", "-I", "-B"])
        self.assertTrue(Path(command[3]).is_absolute())
        self.assertEqual(Path(command[3]), Path(terminal.__file__).resolve())
        self.assertEqual(command[4:], ["--namespace-entry", "--", *argv])
        self.assert_no_session_or_acquisition()

    def test_namespace_entry_preflights_then_creates_session_then_acquires_and_execs(self):
        trace = mock.Mock()
        for name in ("isatty", "setsid", "getpid", "getsid", "getpgrp", "ioctl", "tcgetpgrp", "execv"):
            trace.attach_mock(self.fd.mocks[name], name)
        argv = ["/opt/client/bin/client", "literal; argv", ""]
        with self.assertRaises(ExecRequested):
            terminal._namespace_entry(argv)
        names = [call[0] for call in trace.mock_calls]
        self.assertEqual(trace.mock_calls[:3], [mock.call.isatty(0), mock.call.isatty(1), mock.call.isatty(2)])
        self.assertLess(names.index("isatty"), names.index("setsid"))
        for name in ("getpid", "getsid", "getpgrp"):
            self.assertLess(names.index("setsid"), names.index(name))
            self.assertLess(names.index(name), names.index("ioctl"))
        self.assertLess(names.index("ioctl"), names.index("tcgetpgrp"))
        self.assertLess(names.index("tcgetpgrp"), names.index("execv"))
        self.fd.mocks["setsid"].assert_called_once_with()
        self.fd.mocks["ioctl"].assert_called_once_with(0, termios.TIOCSCTTY, 0)
        self.fd.mocks["tcgetpgrp"].assert_called_once_with(0)
        self.fd.mocks["execv"].assert_called_once_with(argv[0], argv)
        self.fd.mocks["openpty"].assert_not_called()

    def test_namespace_entry_rejects_invalid_argv_before_session_creation(self):
        invalid = (None, "/opt/client", [], ["client"], [""], ["./client"],
                   ["/opt/client", 7], ["/opt/client", "nul\0argument"])
        for argv in invalid:
            with self.subTest(argv=argv), self.assertRaises(OSError):
                terminal._namespace_entry(argv)
        self.assert_no_session_or_acquisition()

    def test_namespace_command_rejects_invalid_argv_without_process_side_effects(self):
        for argv in ([], ["client"], ["/opt/client", "nul\0argument"], ["/opt/client", None]):
            with self.subTest(argv=argv), self.assertRaises(OSError):
                terminal.NativeTerminal.namespace_entry_command(argv)
        self.assert_no_session_or_acquisition()

    def test_namespace_entry_requires_each_tty_before_session_creation(self):
        for missing in (0, 1, 2):
            self.fd.mocks["isatty"].side_effect = lambda fd: fd != missing
            with self.subTest(fd=missing), self.assertRaises(OSError):
                terminal._namespace_entry(["/opt/client"])
        self.assert_no_session_or_acquisition()

    def test_namespace_entry_refuses_non_linux_before_session_creation(self):
        with mock.patch.object(sys, "platform", "darwin"), self.assertRaises(OSError):
            terminal._namespace_entry(["/opt/client"])
        self.assert_no_session_or_acquisition()

    def test_setsid_failure_prevents_terminal_acquisition_and_execution(self):
        self.fd.mocks["setsid"].side_effect = OSError(errno.EPERM, "session creation failed")
        with self.assertRaises(OSError) as caught:
            terminal._namespace_entry(["/opt/client"])
        self.assertEqual(caught.exception.errno, errno.EPERM)
        self.fd.mocks["setsid"].assert_called_once_with()
        for name in ("getpid", "getsid", "getpgrp", "ioctl", "execv"):
            self.fd.mocks[name].assert_not_called()

    def test_invalid_post_setsid_identities_refuse_before_terminal_acquisition(self):
        for pid, sid, group in ((0, 0, 0), (-1, -1, -1), (501, 0, 501),
                                (501, 501, 0), (501, 900, 501), (501, 501, 900)):
            with self.subTest(pid=pid, sid=sid, group=group):
                self.fd.mocks["setsid"].reset_mock()
                self.fd.mocks["getpid"].side_effect = lambda: pid
                self.fd.mocks["getsid"].side_effect = lambda target: sid
                self.fd.mocks["getpgrp"].side_effect = lambda: group
                with self.assertRaises(OSError):
                    terminal._namespace_entry(["/opt/client"])
                self.fd.mocks["setsid"].assert_called_once_with()
        self.fd.mocks["ioctl"].assert_not_called()
        self.fd.mocks["tcgetpgrp"].assert_not_called()
        self.fd.mocks["execv"].assert_not_called()

    def test_namespace_terminal_acquisition_failure_never_execs(self):
        self.fd.mocks["ioctl"].side_effect = OSError(errno.EPERM, "cannot acquire terminal")
        with self.assertRaises(OSError) as caught:
            terminal._namespace_entry(["/opt/client"])
        self.assertEqual(caught.exception.errno, errno.EPERM)
        self.fd.mocks["setsid"].assert_called_once_with()
        self.fd.mocks["tcgetpgrp"].assert_not_called()
        self.fd.mocks["execv"].assert_not_called()

    def test_namespace_entry_requires_positive_matching_foreground_group(self):
        for foreground in (0, -1, 900):
            with self.subTest(foreground=foreground):
                self.fd.mocks["setsid"].reset_mock()
                self.fd.mocks["ioctl"].reset_mock()
                self.fd.mocks["tcgetpgrp"].side_effect = lambda fd: foreground
                with self.assertRaises(OSError):
                    terminal._namespace_entry(["/opt/client"])
                self.fd.mocks["setsid"].assert_called_once_with()
                self.fd.mocks["ioctl"].assert_called_once_with(0, termios.TIOCSCTTY, 0)
        self.fd.mocks["execv"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
