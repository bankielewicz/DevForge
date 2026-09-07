"""Linux controlling-PTY transport; process admission and lifetime stay external.

The parent must call child_started() immediately after successful Popen with
child_stdio() for all three streams and start_new_session=True. For a sandbox
with PID isolation, namespace_entry_command() must wrap the final command inside
that namespace; its helper creates the session and acquires the inherited slave
there. entry_command() supports direct launches into an already created session.
No preexec_fn, signal-handler changes, authentication, signals or reaping occurs.
"""
from __future__ import annotations

import copy
import errno
import fcntl
import os
from pathlib import Path
import select
import sys
import termios
import tty


BUFFER_LIMIT = 65536
IO_CHUNK = 16384
MAX_PUMP_TIMEOUT = 0.05
SYSTEM_PYTHON = "/usr/bin/python3"


def _terminal_call(operation, *args):
    # CPython termios.error inherits Exception directly, not OSError.
    try:
        return operation(*args)
    except termios.error as error:
        number = error.args[0] if error.args and isinstance(error.args[0], int) else errno.EIO
        message = str(error.args[1]) if len(error.args) > 1 else str(error)
        raise OSError(number, message) from error


def _linux_ttys() -> None:
    if sys.platform != "linux":
        raise OSError(errno.ENOSYS, "Native controlling-terminal transport requires Linux")
    if not all(os.isatty(fd) for fd in (0, 1, 2)):
        raise OSError(errno.ENOTTY, "Native terminal requires actual stdin/stdout/stderr TTYs")


def _window_size() -> bytes:
    value = fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8)
    if not isinstance(value, bytes) or len(value) != 8:
        raise OSError(errno.EIO, "Terminal returned an invalid window-size record")
    return value


def _argv(argv) -> list[str]:
    if not isinstance(argv, (list, tuple)) or not argv:
        raise OSError(errno.EINVAL, "A nonempty selected argv array is required")
    if any(not isinstance(value, str) or "\0" in value for value in argv):
        raise OSError(errno.EINVAL, "Terminal argv must contain strings without NUL bytes")
    if not os.path.isabs(argv[0]):
        raise OSError(errno.EINVAL, "Terminal entry requires an absolute selected executable")
    return list(argv)


class NativeTerminal:
    """One parent's PTY transport, with at most 64 KiB queued per direction.

    Call enter() before pump(); call close() from the supervisor's finally block.
    Methods are for one parent event-loop thread. stdin_eof reports terminal input
    closure; it never injects an EOF character or makes a process-lifetime choice.
    pty_eof reports the slave's output closure after all kernel-buffered reads.
    Queued output may remain after pty_eof and can be drained with further pumps.
    """

    def __init__(self):
        _linux_ttys()
        self._original_termios = copy.deepcopy(_terminal_call(termios.tcgetattr, 0))
        self._original_flags = {fd: fcntl.fcntl(fd, fcntl.F_GETFL) for fd in (0, 1)}
        self._initial_window_size = _window_size()
        self._master = None
        self._slave = None
        self._closed = False
        self._entered = False
        self._terminal_modified = False
        self._changed_flags = set()
        self._stdin_open = True
        self._master_read_open = True
        self._master_write_open = True
        self._to_master = bytearray()
        self._to_stdout = bytearray()
        try:
            self._master, self._slave = os.openpty()
            os.set_inheritable(self._master, False)
            os.set_inheritable(self._slave, False)
            _terminal_call(termios.tcsetattr, self._slave, termios.TCSANOW,
                           copy.deepcopy(self._original_termios))
            fcntl.ioctl(self._master, termios.TIOCSWINSZ, self._initial_window_size)
            os.set_blocking(self._master, False)
        except BaseException as error:
            try:
                self.close()
            except OSError as cleanup_error:
                error.add_note("PTY allocation cleanup failed: " + str(cleanup_error))
            raise

    @staticmethod
    def entry_command(argv) -> list[str]:
        """Use only after external package/argv admission; this grants no authority."""
        return [SYSTEM_PYTHON, "-I", "-B", str(Path(__file__).resolve()),
                "--entry", "--", *_argv(argv)]

    @staticmethod
    def namespace_entry_command(argv) -> list[str]:
        """Wrap the admitted command inside its selected PID namespace.

        The outer sandbox process must inherit the slave without first claiming
        it. Acquiring its controlling terminal here gives the command positive
        namespace-visible session and foreground process-group identifiers.
        """
        return [SYSTEM_PYTHON, "-I", "-B", str(Path(__file__).resolve()),
                "--namespace-entry", "--", *_argv(argv)]

    def child_stdio(self) -> int:
        """Owned slave fd for Popen stdin, stdout and stderr; caller must not close it."""
        if self._closed or self._slave is None:
            raise OSError(errno.EBADF, "PTY slave is no longer available for child launch")
        return self._slave

    def child_started(self) -> None:
        """Release the parent slave after Popen; retaining it would hide child EOF."""
        if self._slave is not None:
            descriptor, self._slave = self._slave, None
            os.close(descriptor)

    def enter(self) -> None:
        if self._closed:
            raise OSError(errno.EBADF, "Native terminal is closed")
        if self._entered:
            return
        try:
            # Mark before changing state so even a partly failing call restores.
            self._terminal_modified = True
            _terminal_call(tty.setraw, 0, termios.TCSANOW)
            for fd, flags in self._original_flags.items():
                self._changed_flags.add(fd)
                fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            self._entered = True
        except BaseException as error:
            try:
                self.close()
            except OSError as cleanup_error:
                error.add_note("Parent terminal restoration failed: " + str(cleanup_error))
            raise

    def resize(self) -> None:
        if self._closed or self._master is None:
            raise OSError(errno.EBADF, "Native terminal is closed")
        # Linux applies the same PTY winsize to both ends. The master remains
        # owned after child_started() releases the parent's slave descriptor.
        fcntl.ioctl(self._master, termios.TIOCSWINSZ, _window_size())

    def _state(self) -> dict:
        return {"stdin_eof": not self._stdin_open,
                "pty_eof": not self._master_read_open,
                "pending_input": len(self._to_master),
                "pending_output": len(self._to_stdout)}

    def _write(self, fd: int, pending: bytearray, *, master: bool = False) -> None:
        try:
            written = os.write(fd, pending[:IO_CHUNK])
        except (BlockingIOError, InterruptedError):
            return
        except OSError as error:
            if master and error.errno in (errno.EIO, errno.EPIPE):
                # Keep undelivered input visible in pending_input, and continue
                # reading buffered slave output until its separate EOF.
                self._master_write_open = False
                return
            raise
        if written <= 0 or written > min(len(pending), IO_CHUNK):
            raise OSError(errno.EIO, "Terminal write made invalid progress")
        del pending[:written]

    def pump(self, timeout: float = MAX_PUMP_TIMEOUT) -> dict:
        """Perform one bounded nonblocking relay step; never wait over 50 ms."""
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or
                not 0 <= timeout <= MAX_PUMP_TIMEOUT):
            raise OSError(errno.EINVAL, "PTY pump timeout must be finite and within 0..0.05 seconds")
        if self._closed or not self._entered or self._master is None:
            raise OSError(errno.EBADF, "Native terminal must be entered and open before pumping")
        readable = []
        writable = []
        if self._stdin_open and self._master_write_open and len(self._to_master) < BUFFER_LIMIT:
            readable.append(0)
        if self._master_read_open and len(self._to_stdout) < BUFFER_LIMIT:
            readable.append(self._master)
        if self._to_master and self._master_write_open:
            writable.append(self._master)
        if self._to_stdout:
            writable.append(1)
        try:
            ready_read, ready_write, _ = select.select(readable, writable, [], timeout)
        except InterruptedError:
            return self._state()
        if 1 in ready_write:
            self._write(1, self._to_stdout)
        if self._master in ready_write:
            self._write(self._master, self._to_master, master=True)
        for descriptor, pending, is_master in ((0, self._to_master, False),
                                                (self._master, self._to_stdout, True)):
            if descriptor not in ready_read:
                continue
            maximum = min(IO_CHUNK, BUFFER_LIMIT - len(pending))
            if maximum <= 0:
                continue
            try:
                value = os.read(descriptor, maximum)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError as error:
                if is_master and error.errno == errno.EIO:
                    value = b""  # Linux PTY read convention after slave closure.
                else:
                    raise
            if not value:
                if is_master:
                    self._master_read_open = False
                    self._master_write_open = False
                else:
                    self._stdin_open = False
            else:
                if len(value) > maximum:
                    raise OSError(errno.EIO, "Terminal read exceeded its requested bound")
                pending.extend(value)
        return self._state()

    def close(self) -> None:
        """Restore parent state and release owned fds; never stop or reap a child.

        All restoration steps are attempted if one fails. Failed parent-state
        restoration remains pending for a later close() call; a closed owned fd
        is never retried because its integer may already have been reused.
        """
        self._closed = True
        self._entered = False
        errors = []
        if self._terminal_modified:
            try:
                _terminal_call(termios.tcsetattr, 0, termios.TCSANOW,
                               copy.deepcopy(self._original_termios))
                self._terminal_modified = False
            except OSError as error:
                errors.append(error)
        for fd in sorted(self._changed_flags):
            try:
                fcntl.fcntl(fd, fcntl.F_SETFL, self._original_flags[fd])
                self._changed_flags.remove(fd)
            except OSError as error:
                errors.append(error)
        for name in ("_slave", "_master"):
            descriptor = getattr(self, name)
            setattr(self, name, None)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as error:
                    errors.append(error)
        if errors:
            for following in errors[1:]:
                errors[0].add_note("Additional terminal cleanup failure: " + str(following))
            raise errors[0]


def _claim_and_exec(selected) -> None:
    pid, session, group = os.getpid(), os.getsid(0), os.getpgrp()
    if pid <= 0 or session <= 0 or group <= 0 or session != pid or group != pid:
        raise OSError(errno.EPERM, "Terminal entry requires a new session/group leader")
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    foreground = os.tcgetpgrp(0)
    if foreground <= 0 or foreground != group:
        raise OSError(errno.ENOTTY, "Child did not acquire the foreground controlling terminal")
    os.execv(selected[0], selected)
    raise OSError(errno.EIO, "Selected terminal executable unexpectedly returned")


def _entry(argv) -> None:
    """Exec-only helper entered after Popen creates an independent session."""
    selected = _argv(argv)
    _linux_ttys()
    _claim_and_exec(selected)


def _namespace_entry(argv) -> None:
    """Create and claim the command's session inside the admitted PID namespace."""
    selected = _argv(argv)
    _linux_ttys()
    os.setsid()
    _claim_and_exec(selected)


if __name__ == "__main__":
    try:
        if sys.argv[1:3] == ["--entry", "--"]:
            _entry(sys.argv[3:])
        elif sys.argv[1:3] == ["--namespace-entry", "--"]:
            _namespace_entry(sys.argv[3:])
        else:
            raise OSError(errno.EINVAL, "Expected --entry -- or --namespace-entry -- followed by selected absolute argv")
    except OSError as error:
        print("DevForge native terminal entry failed: " + str(error), file=sys.stderr)
        sys.exit(2)
