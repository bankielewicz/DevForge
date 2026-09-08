"""Guarded task supervisor with explicitly selected mechanical completion policy.

Process mode requires owned successful exit. Managed-session mode commits the
selected mechanical task at its final qualifying Stop. Neither authenticates
human acceptance, native turn completion, or rendered user delivery.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from delivery import delivery_core, hook_protocol, native_terminal, phase_state, workflow_runtime

MAX_WIRE = 65536
MAX_EVENTS = 1000
SCOPE = "Protected mechanical phases and process-scoped delivery; native behavior NOT_EVALUATED"
MANAGED_SCOPE = ("Coordinated mechanical task completion; native event/EOF completion "
                 "NOT_OBSERVED; semantic acceptance NOT_EVALUATED; user delivery NOT_OBSERVED")
COMPLETION_MODES = {"process", "managed-session"}
IO_MODES = {"inherited", "interactive-tty"}
PTY_DRAIN_SECONDS = 2.0
CANCELLATION_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


class Cancellation:
    """Scoped main-thread signal latch; never interrupt a protected operation."""

    def __init__(self):
        self.signal_number = None
        self.previous_handlers = {}

    def handle(self, signum, frame):
        if self.signal_number is None:
            self.signal_number = signum

    def install(self):
        for signum in CANCELLATION_SIGNALS:
            self.previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, self.handle)

    def restore(self):
        errors = []
        for signum, previous in list(self.previous_handlers.items()):
            try:
                signal.signal(signum, previous)
                del self.previous_handlers[signum]
            except (OSError, ValueError) as error:
                errors.append(error)
        if errors:
            raise errors[0]

    def reason(self):
        if self.signal_number is None:
            return None
        return "Owner cancellation received " + signal.Signals(self.signal_number).name


def failed(issue, status="FAIL", **details):
    return {"status": status, "issues": [str(issue)], "scope": SCOPE,
            "native_completion": "NOT_EVALUATED", **details}


def separate(left, right):
    return not (left.is_relative_to(right) or right.is_relative_to(left))


def canonical(path, directory=False):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("A canonical absolute path is required: " + str(path))
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError("Symlink component: " + str(cursor))
    if directory and not path.is_dir():
        raise ValueError("Required directory unavailable: " + str(path))
    return path


def write_new(path, value):
    raw = (json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n").encode()
    with path.open("xb") as handle:
        os.chmod(path, 0o600)
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return hashlib.sha256(raw).hexdigest()


class Broker:
    """Serial bounded callback requests; worker cannot publish authoritative state."""

    def __init__(self, state, session, contract, contract_digest, observations, *,
                 completion_mode="process"):
        if not isinstance(completion_mode, str) or completion_mode not in COMPLETION_MODES:
            raise ValueError("Unsupported completion_mode")
        self.state = state
        self.session = session
        self.engine = workflow_runtime.engine(session)
        self.contract = contract
        self.contract_digest = contract_digest
        self.observations = observations
        self.completion_mode = completion_mode
        self.task_result = None
        self.current_applicability = "NOT_VERIFIED"
        self.current_issues = []
        self._task_result_stored = False
        self._last_delivery = None
        self.session_id = None
        self.count = 0
        self.previous = None
        self.error = None
        self.interrupted = False
        self.cancellation = None
        self.stopping = threading.Event()
        self.socket_root = Path(tempfile.mkdtemp(prefix="devforge-broker-", dir="/tmp"))
        (self.socket_root / "scratch").mkdir(mode=0o700)
        self.socket_path = self.socket_root / "hook.sock"
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.socket_path))
        self.server.listen(4)
        self.server.settimeout(0.1)
        self.thread = threading.Thread(target=self.serve_guarded, daemon=True)

    def _remember_completed(self, result):
        """Persist actual completed evidence before preparing its first response."""
        if (result.get("status") != "COMPLETED" or result.get("task_id") != self.session["task_id"]
                or result.get("receipt_path") != self.session["receipt_path"]
                or any(result.get(key) is not True for key in
                       ("receipt_published", "receipt_readback", "receipt_verified"))
                or not isinstance(result.get("receipt_sha256"), str)
                or len(result["receipt_sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in result["receipt_sha256"])):
            raise ValueError("Completion result has no verified selected receipt binding")
        if self.task_result is not None:
            if any(result[key] != self.task_result[key] for key in
                   ("task_id", "receipt_path", "receipt_sha256")):
                raise ValueError("Completed receipt differs from the original task result")
        else:
            # Keep this result even if subsequent evidence publication fails.
            self.task_result = json.loads(json.dumps(result))
        if not self._task_result_stored:
            path = self.observations / "task-result.json"
            record = {"schema_version": "devforge.managed-task-result/v1",
                      "task_id": self.session["task_id"], "contract_sha256": self.contract_digest,
                      "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                      "result": self.task_result}
            try:
                write_new(path, record)
            except FileExistsError:
                raw = delivery_core._read_external(path, MAX_WIRE, "managed task result")
                prior = delivery_core._json(raw, "managed task result")
                if (not isinstance(prior, dict) or set(prior) != set(record)
                        or prior["schema_version"] != record["schema_version"]
                        or prior["task_id"] != record["task_id"]
                        or prior["contract_sha256"] != self.contract_digest
                        or not isinstance(prior["recorded_at_utc"], str)
                        or not isinstance(prior["result"], dict)
                        or prior["result"] != self.task_result):
                    raise ValueError("Existing managed task result differs from verified selected task")
                recorded = datetime.fromisoformat(prior["recorded_at_utc"].replace("Z", "+00:00"))
                if recorded.utcoffset() is None or recorded.utcoffset().total_seconds() != 0:
                    raise ValueError("Existing managed task result timestamp is not UTC")
            self._task_result_stored = True
        self.current_applicability = "VERIFIED"
        self.current_issues = []

    def _complete_managed(self):
        if self.cancellation is not None and self.cancellation.signal_number is not None:
            self.interrupted = True
        if self.interrupted or self.error or self.stopping.is_set():
            self.current_applicability = "NOT_VERIFIED"
            self.current_issues = ["Managed completion unavailable after broker interruption, error or shutdown"]
            return failed(self.current_issues[0], "COULD_NOT_RUN")
        result = self.engine.complete(self.state)
        if result.get("status") == "COMPLETED":
            try:
                self._remember_completed(result)
            except (OSError, ValueError, TypeError, KeyError, delivery_core._Problem) as error:
                self.current_applicability = "NOT_VERIFIED"
                self.current_issues = [str(error)]
                raise
        else:
            self.current_applicability = "NOT_VERIFIED"
            self.current_issues = list(result.get("issues", []))
        return result

    def handle(self, request):
        self._last_delivery = None
        if self.cancellation is not None and self.cancellation.signal_number is not None:
            self.interrupted = True
            raise ValueError(self.cancellation.reason())
        if not isinstance(request, dict) or set(request) != {"provider", "contract_sha256", "event"}:
            raise ValueError("Malformed callback request")
        if request["provider"] != self.session["provider"] or request["contract_sha256"] != self.contract_digest:
            raise ValueError("Callback provider or selected contract binding differs")
        event = request["event"]
        hook_protocol.validate_event(request["provider"], event, self.contract["project_root"])
        name = event["hook_event_name"]
        if name == "Interrupt":
            self.interrupted = True
        if self.session_id is None:
            if name != "SessionStart":
                raise ValueError("SessionStart callback was not observed before this callback")
            self.session_id = event["session_id"]
        elif event["session_id"] != self.session_id:
            raise ValueError("Callback session differs from this owned process")
        if self.count >= MAX_EVENTS:
            raise ValueError("Callback observation limit exceeded")
        before = self.engine.context(self.state)
        if (self.completion_mode == "managed-session"
                and name in {"SessionStart", "UserPromptSubmit", "Stop"}
                and (self.task_result is not None or before["status"] == "COMPLETED")):
            result = self._complete_managed()
        elif name == "Stop" and before["status"] in {"ACTIVE", "READY"}:
            # Providers do not expose an authenticated per-Stop delivery ID.
            # Reinspect every occurrence, including repaired artifacts with the
            # same checkpoint bytes. Configuration duplicates must be refused at
            # native admission rather than guessed away by content caching.
            result = self.engine.advance(self.state) if before["status"] == "ACTIVE" else before
            if self.completion_mode == "managed-session" and result["status"] == "READY":
                result = self._complete_managed()
        else:
            if name == "UserPromptSubmit" and before["status"] == "WAITING_USER":
                # Reopening inspection is not evidence of a human answer/approval.
                result = (self.engine.resume(self.state, event.get("prompt"))
                          if self.session["schema_version"] == "devforge.utility-session/v1"
                          else self.engine.resume(self.state))
            else:
                result = before
        try:
            if self.completion_mode == "process":
                reply = hook_protocol.response(name, result, self.session, self.contract)
            else:
                reply = hook_protocol.response(name, result, self.session, self.contract,
                                               completion_mode=self.completion_mode, task_result=self.task_result)
        except ValueError as error:
            if self.completion_mode != "managed-session" or result.get("status") != "COMPLETED":
                raise
            result = failed("Receipt response unavailable: " + str(error), "COULD_NOT_RUN")
            reply = {"systemMessage": "Mechanical receipt delivery is unavailable: " + str(error)
                     + ". The committed task result is preserved. Native turn completion NOT_OBSERVED; "
                     "semantic acceptance NOT_EVALUATED."}
        self.count += 1
        response_sha256 = hashlib.sha256(json.dumps(reply, sort_keys=True).encode()).hexdigest()
        attempt_sha256 = None
        if (self.completion_mode == "managed-session" and result["status"] == "COMPLETED"
                and name in {"SessionStart", "UserPromptSubmit", "Stop"}):
            attempt = {"schema_version": "devforge.managed-delivery-attempt/v1",
                       "sequence": self.count, "task_id": self.session["task_id"],
                       "contract_sha256": self.contract_digest, "completion_mode": self.completion_mode,
                       "attempted_at_utc": datetime.now(timezone.utc).isoformat(),
                       "receipt_path": result["receipt_path"], "receipt_sha256": result["receipt_sha256"],
                       "response_sha256": response_sha256, "user_delivery": "NOT_OBSERVED"}
            attempt_sha256 = write_new(self.observations / f"delivery-attempt-{self.count:06d}.json", attempt)
        observation = {"sequence": self.count, "previous_sha256": self.previous,
                       "at_utc": datetime.now(timezone.utc).isoformat(), "event": name,
                       "session_id_sha256": hashlib.sha256(self.session_id.encode()).hexdigest(),
                       "phase": result.get("phase"), "status": result["status"],
                       "before_phase": before.get("phase"), "before_status": before["status"],
                       "authority": "CALLBACK_NOT_HUMAN_AUTHORITY",
                       "response_sha256": response_sha256}
        if name == "UserPromptSubmit" and isinstance(event.get("prompt"), str):
            observation["prompt_sha256"] = hashlib.sha256(event["prompt"].encode("utf-8")).hexdigest()
        if self.completion_mode == "managed-session":
            observation["completion_mode"] = self.completion_mode
            if attempt_sha256 is not None:
                observation["delivery_attempt_sha256"] = attempt_sha256
        self.previous = write_new(self.observations / f"event-{self.count:06d}.json", observation)
        if attempt_sha256 is not None:
            self._last_delivery = {"sequence": self.count, "delivery_attempt_sha256": attempt_sha256,
                                   "event_sha256": self.previous, "response_sha256": response_sha256}
        return reply

    def serve(self):
        while not self.stopping.is_set():
            try:
                connection, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError as error:
                if not self.stopping.is_set():
                    self.error = "Broker listener failed: " + str(error)
                break
            with connection:
                connection.settimeout(3)
                self._last_delivery = None
                try:
                    data = bytearray()
                    while b"\n" not in data and len(data) <= MAX_WIRE:
                        chunk = connection.recv(min(4096, MAX_WIRE + 1 - len(data)))
                        if not chunk:
                            break
                        data.extend(chunk)
                    if len(data) > MAX_WIRE or not data.endswith(b"\n") or data.count(b"\n") != 1:
                        raise ValueError("Callback must be one bounded JSON line")
                    request = delivery_core._json(bytes(data), "callback")
                    reply = self.handle(request)
                except (OSError, ValueError, TypeError, KeyError, RecursionError, delivery_core._Problem) as error:
                    self.error = str(error)
                    reply = {"continue": False, "stopReason": "Runtime callback refused: " + self.error[:500]}
                wire = (json.dumps(reply) + "\n").encode()
                transport_issues = []
                try:
                    connection.sendall(wire)
                except OSError as error:
                    self.error = "Callback response delivery failed: " + str(error)
                    transport_issues = [self.error[:1200]]
                if self._last_delivery is not None:
                    transport = {"schema_version": "devforge.managed-delivery-transport/v1",
                                 **self._last_delivery, "wire_sha256": hashlib.sha256(wire).hexdigest(),
                                 "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                                 "transport_status": "SOCKET_WRITE_FAILED" if transport_issues
                                 else "SOCKET_WRITE_COMPLETED", "user_delivery": "NOT_OBSERVED",
                                 "issues": transport_issues}
                    try:
                        write_new(self.observations / f"transport-{self._last_delivery['sequence']:06d}.json",
                                  transport)
                    except (OSError, ValueError, TypeError) as error:
                        self.error = (self.error + "; " if self.error else "") + \
                            "Callback transport observation failed: " + str(error)

    def serve_guarded(self):
        try:
            self.serve()
        except Exception as error:
            # An unexpected service failure must never become a missing callback
            # observation that can be mistaken for successful quiescence.
            self.error = "Runtime callback service failed: " + type(error).__name__
        finally:
            if not self.stopping.is_set() and self.error is None:
                self.error = "Runtime callback service stopped unexpectedly"

    def close(self):
        if self.thread.ident is not None and not self.thread.is_alive() and not self.stopping.is_set():
            self.error = self.error or "Runtime callback service stopped unexpectedly"
        self.stopping.set()
        self.server.close()
        if self.thread.is_alive():
            self.thread.join(timeout=4)
        quiescent = not self.thread.is_alive()
        if not quiescent:
            self.error = "Runtime callback service did not become quiescent"
        self.socket_path.unlink(missing_ok=True)
        (self.socket_root / "scratch").rmdir()
        self.socket_root.rmdir()
        return quiescent


def group_exists(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def owned_exited(process):
    """Observe this unreaped child; keep its PID reserved until all signals end."""
    return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def stop_owned(process):
    signals = []
    try:
        owned_exited(process)  # Refuse signals if another caller already reaped it.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if group_exists(process.pid):
                try:
                    os.killpg(process.pid, sig)
                    signals.append(sig.name)
                except ProcessLookupError:
                    pass
            if sig == signal.SIGTERM:
                end = time.monotonic() + 0.3
                while time.monotonic() < end and not owned_exited(process):
                    time.sleep(0.01)
        # No subsequent signal may use the numeric group identifier after this.
        process.wait(timeout=3)
        # Reaping the namespace leader can precede the kernel's final removal
        # of its dying group. Observe that tail without ever signaling a reused
        # numeric identifier after wait().
        end = time.monotonic() + 0.3
        while group_exists(process.pid) and time.monotonic() < end:
            time.sleep(0.01)
        return {"signals": signals, "leader_reaped": process.returncode is not None,
                "group_absent": not group_exists(process.pid), "issues": []}
    except (OSError, subprocess.SubprocessError) as error:
        return {"signals": signals, "leader_reaped": process.returncode is not None,
                "group_absent": False, "issues": ["Owned cleanup could not be verified: " + str(error)]}


def sandbox_command(project, profile, client_root, command, session_path, session, contract, broker,
                    synthetic=False, io_mode="inherited"):
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise OSError("Bubblewrap is unavailable; no unconfined fallback")
    executable = Path(shutil.which(command[0]) or command[0]).resolve(strict=True)
    if not executable.is_relative_to(client_root):
        raise ValueError("Selected intact client root does not contain the executable")
    home = canonical(Path.home(), directory=True)
    runtime = Path(__file__).resolve().parent.parent
    gate = canonical(Path(os.environ["DEVFORGE_DELIVERY_EXECUTABLE"]))
    protected = [session_path, Path(session["delivery_contract"]), Path(session["assignment"]["path"]),
                 runtime, gate, client_root]
    protected += [Path(row["path"]) for row in session["installed_inputs"]]
    protected += [project / row["path"] for row in contract["inputs"]]
    protected += [project / row["archive"] for row in session["output_baselines"] if row["archive"]]
    # Utility gate/answer evidence is consumed outside the worker sandbox. Do
    # not expose an entire parent directory merely because a future file is absent.
    if contract.get("schema_version") != "devforge.utility-delivery/v1":
        protected += workflow_runtime.protected_paths(contract)
    git_entry = project / ".git"
    if git_entry.exists() or git_entry.is_symlink():
        canonical(git_entry)
        protected.append(git_entry)
        if git_entry.is_file():
            # A project-controlled pointer cannot select extra external read
            # mounts. Worktree/common-dir roots need the future fixed launcher
            # contract's explicit owner selection; never infer it from .git text.
            raise ValueError("External Git worktree metadata needs a selected launcher contract")
    # Mask private host home and temporary files, then restore only selected paths.
    argv = [bwrap, "--die-with-parent"]
    if io_mode == "inherited":
        argv.append("--new-session")
    argv += ["--unshare-pid", "--cap-drop", "ALL",
            "--ro-bind", "/", "/", "--tmpfs", str(home), "--tmpfs", "/tmp",
            "--proc", "/proc", "--dev", "/dev", "--bind", str(project), str(project),
            "--bind", str(profile), str(profile)]
    if synthetic:
        argv.append("--unshare-net")
    for path in sorted(set(protected), key=lambda entry: (len(entry.parts), str(entry))):
        argv += ["--ro-bind", str(path), str(path)]
    argv += ["--ro-bind", str(broker.socket_root), str(broker.socket_root)]
    # Masked ancestor tmpfs mounts must not offer writable shadow authority
    # paths. Keep only the selected project/profile mounts and a dedicated
    # scratch tmpfs writable; remount-ro deliberately does not recurse.
    scratch = broker.socket_root / "scratch"
    argv += ["--tmpfs", str(scratch), "--remount-ro", str(home), "--remount-ro", "/tmp"]
    environment = {"HOME": str(profile), "CODEX_HOME": str(profile / ".codex"),
                   "CLAUDE_CONFIG_DIR": str(profile / ".claude"),
                   "XDG_CONFIG_HOME": str(profile / ".config"), "XDG_CACHE_HOME": str(profile / ".cache"),
                   "XDG_DATA_HOME": str(profile / ".local/share"), "LANG": "C.UTF-8",
                   "PATH": str(executable.parent) + ":/usr/local/bin:/usr/bin:/bin",
                   "PYTHONDONTWRITEBYTECODE": "1", "DEVFORGE_DELIVERY_EXECUTABLE": str(gate),
                   "TMPDIR": str(scratch),
                   "DEVFORGE_DELIVERY_SOCKET": str(broker.socket_path),
                   "DEVFORGE_DELIVERY_CONTRACT_SHA256": broker.contract_digest}
    if os.environ.get("TERM"):
        environment["TERM"] = os.environ["TERM"]
    argv += ["--clearenv"]
    for name, value in environment.items():
        argv += ["--setenv", name, value]
    selected_command = [str(executable), *command[1:]]
    if io_mode == "interactive-tty":
        selected_command = native_terminal.NativeTerminal.namespace_entry_command(selected_command)
    return argv + ["--chdir", str(project), "--", *selected_command]


def run(session_path, state, profile, command, timeout, client_root, result_path=None, synthetic=False, *,
        completion_mode="process", io_mode="inherited"):
    if not isinstance(completion_mode, str) or completion_mode not in COMPLETION_MODES:
        return failed("Unsupported completion_mode")
    if not isinstance(io_mode, str) or io_mode not in IO_MODES:
        return failed("Unsupported io_mode")
    broker = None
    process = None
    cleanup = None
    outcome = None
    observations = None
    observation = None
    began = None
    terminal = None
    terminal_closed = False
    terminal_state = {"stdin_eof": False, "pty_eof": False, "pending_input": 0, "pending_output": 0}
    drain_complete = False
    cancellation = Cancellation()
    publish_observation_result = False

    def finish(result, *, final=False):
        nonlocal outcome
        if completion_mode == "process" and io_mode == "inherited":
            outcome = result
            return result
        # Finally refreshes this same object: Python has already selected its
        # return value before running finally, so replacing it would lose edits.
        if result is not outcome:
            result = dict(result)
        if observation is not None:
            result.setdefault("worker", observation)
        elif process is not None:
            result.setdefault("worker", {
                "exit_code": process.returncode,
                "duration_seconds": None if began is None else time.monotonic() - began,
                "cleanup": cleanup, "event_count": broker.count if broker is not None else 0,
                "observations": str(observations), "completion_boundary": "MECHANICAL_TASK_COMMIT"
                if completion_mode == "managed-session" else "OWNED_PROCESS_EXIT",
                "completion_mode": completion_mode, "callback_origin": "NOT_AUTHENTICATED"})
        if io_mode == "interactive-tty":
            result["io_mode"] = io_mode
            if "worker" in result:
                result["worker"].update(io_mode=io_mode, **terminal_state, drain_complete=drain_complete)
        if final and process is not None and "worker" in result:
            result["worker"].update(exit_code=process.returncode, cleanup=cleanup,
                                    event_count=broker.count if broker is not None else 0)
        if completion_mode == "process":
            outcome = result
            return result
        applicability = "NOT_VERIFIED"
        verification_issues = []
        if final and broker is not None and broker.task_result is not None:
            # This path cannot create an absent task receipt: a completed result
            # has already been obtained. It only rechecks that historical commit.
            try:
                if broker.thread.is_alive():
                    result.update(status="COULD_NOT_RUN")
                    verification_issues = ["Runtime callback service did not become quiescent"]
                else:
                    current = engine.complete(state)
                    if current.get("status") == "COMPLETED":
                        broker._remember_completed(current)
                        applicability = "VERIFIED"
                        if result.get("status") == "COMPLETED":
                            result.update(current)
                    else:
                        verification_issues = list(current.get("issues", []))
            except Exception as error:
                verification_issues = ["Current receipt verification unavailable: " + str(error)]
            if applicability != "VERIFIED":
                if result.get("status") == "COMPLETED":
                    result.update(status="FAIL", issues=["Current applicability could not be verified",
                                                          *verification_issues])
                # Only the nested task_result carries historical verified flags.
                for key in ("receipt_published", "receipt_readback", "receipt_verified",
                            "receipt_readback_at_utc", "receipt_path", "receipt_sha256"):
                    result.pop(key, None)
            result["task_result"] = json.loads(json.dumps(broker.task_result))
        result.update(completion_mode=completion_mode, scope=MANAGED_SCOPE,
                      native_completion="NOT_EVALUATED",
                      long_lived_task_completion="MECHANICAL_TASK_BOUNDARY_IMPLEMENTED",
                      execution_kind="SYNTHETIC", native_launch_admitted=False,
                      native_provider_admission="NOT_VALIDATED", user_delivery="NOT_OBSERVED",
                      current_applicability=applicability)
        if verification_issues:
            result["current_verification_issues"] = verification_issues
        outcome = result
        return result

    if not synthetic:
        return finish(failed("Native launcher/authentication contract and effective hook-source verification are unavailable; "
                      "no native process or task was admitted", "COULD_NOT_RUN", native_launch_admitted=False))
    if not command or not 0 < timeout <= 86400 or client_root is None:
        return finish(failed("Command, timeout in (0, 86400], and intact --client-root are required"))
    try:
        cancellation.install()
        session_path, state = canonical(session_path), canonical(state)
        profile, client_root = canonical(profile, True), canonical(client_root, True)
        session_raw = delivery_core._read_external(session_path, MAX_WIRE, "session")
        session = delivery_core._json(session_raw, "session")
        if not isinstance(session, dict) or not isinstance(session.get("delivery_contract"), str):
            raise ValueError("Session must select a delivery contract path")
        engine = workflow_runtime.engine(session)
        contract, _, project = workflow_runtime.load_delivery(Path(session["delivery_contract"]))
        reference_paths = workflow_runtime.protected_paths(contract)
        if result_path is not None:
            result_path = canonical(result_path)
            if not result_path.parent.is_dir() or result_path.exists():
                raise ValueError("External result destination is unavailable or occupied")
            for selected in (project, profile, state, session_path, Path(session["delivery_contract"]),
                             Path(session["assignment"]["path"]), Path(session["receipt_path"]),
                             Path(__file__).resolve().parent.parent, client_root, *reference_paths):
                if not separate(result_path, selected):
                    raise ValueError("Result destination overlaps mutable or protected task paths")
        for selected in (state, session_path, Path(session["delivery_contract"]),
                         Path(session["assignment"]["path"]), Path(session["receipt_path"]),
                         project, Path(__file__).resolve().parent.parent, client_root, *reference_paths):
            if not separate(profile, selected):
                raise ValueError("Private profile overlaps protected code/state/input or project")
        for selected in (Path(__file__).resolve().parent.parent, client_root,
                         Path(sys.executable).resolve(), Path(os.environ["DEVFORGE_DELIVERY_EXECUTABLE"])):
            if not separate(project, selected):
                raise ValueError("Writable project overlaps launcher or client code")
        # One launch per state; resuming a process requires a prospective owner action.
        if state.exists():
            return finish(failed("State already exists; inspect its owned process and use explicit recovery"))
        if not state.parent.is_dir():
            raise OSError("External state parent is unavailable")
        if cancellation.signal_number is not None:
            return finish(failed(cancellation.reason(), "COULD_NOT_RUN"))
        if io_mode == "interactive-tty":
            # This preflight allocates transport only; no task or worker has
            # been admitted and the parent terminal remains in its original mode.
            terminal = native_terminal.NativeTerminal()
        observations = Path(tempfile.mkdtemp(prefix=state.name + ".process-", dir=state.parent))
        if cancellation.signal_number is not None:
            return finish(failed(cancellation.reason(), "COULD_NOT_RUN"))
        started = engine.start(session_path, state)
        if started["status"] != "ACTIVE":
            return finish(started)
        deadline = datetime.fromisoformat(session["deadline_utc"].replace("Z", "+00:00"))
        timeout = min(timeout, (deadline - datetime.now(timezone.utc)).total_seconds())
        if timeout <= 0:
            return finish(failed("Task deadline expired", "COULD_NOT_RUN"))
        broker_args = (state, session, contract, hashlib.sha256(session_raw).hexdigest(), observations)
        broker = (Broker(*broker_args) if completion_mode == "process"
                  else Broker(*broker_args, completion_mode=completion_mode))
        broker.cancellation = cancellation
        sandbox_args = (project, profile, client_root, command, session_path, session, contract, broker)
        argv = (sandbox_command(*sandbox_args, synthetic=synthetic) if io_mode == "inherited"
                else sandbox_command(*sandbox_args, synthetic=synthetic, io_mode=io_mode))
        if completion_mode == "process":
            broker.thread.start()
        timeout = min(timeout, (deadline - datetime.now(timezone.utc)).total_seconds())
        if timeout <= 0:
            return finish(failed("Original deadline expired before process admission", "COULD_NOT_RUN"))
        if cancellation.signal_number is not None:
            broker.interrupted = True
            return finish(failed(cancellation.reason(), "COULD_NOT_RUN"))
        began = time.monotonic()
        if terminal is None:
            process = subprocess.Popen(argv, start_new_session=True)
        else:
            child_stdio = terminal.child_stdio()
            process = subprocess.Popen(argv, stdin=child_stdio,
                                       stdout=child_stdio, stderr=child_stdio, start_new_session=True)
            terminal.child_started()
            terminal.enter()
        ownership = {"pid": process.pid,
                  "start_time_ticks": Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()[19],
                  "task_id": session["task_id"], "contract_sha256": broker.contract_digest,
                  "deadline_utc": session["deadline_utc"], "mode": "PROCESS_SCOPED",
                  "started_at_utc": datetime.now(timezone.utc).isoformat()}
        if completion_mode == "managed-session":
            ownership.update(mode="MANAGED_SESSION", completion_mode=completion_mode)
        if io_mode == "interactive-tty":
            ownership["io_mode"] = io_mode
        write_new(observations / "owned-process.json", ownership)
        if completion_mode == "managed-session":
            # Connections may queue on the bound socket, but no callback can
            # commit before the external process/mode observation is durable.
            broker.thread.start()
        cause = None
        try:
            while not owned_exited(process):
                if cancellation.signal_number is not None:
                    broker.interrupted = True
                    cause = cancellation.reason()
                    break
                if broker.interrupted:
                    cause = "Native client reported interruption; task completion is not admitted"
                    break
                if broker.error:
                    cause = broker.error
                    break
                if not broker.thread.is_alive():
                    cause = "Runtime callback service stopped unexpectedly"
                    break
                if time.monotonic() - began >= timeout:
                    cause = "Owned process deadline exceeded"
                    break
                if terminal is None:
                    time.sleep(0.05)
                else:
                    terminal.resize()
                    terminal_state = terminal.pump(0.05)
                    if terminal_state["stdin_eof"] and not owned_exited(process):
                        cause = "Parent terminal input closed before owned process exit"
                        break
        except KeyboardInterrupt:
            cause = "Operator interrupted the owned process"
        if cancellation.signal_number is not None:
            broker.interrupted = True
            cause = cancellation.reason()
        cleanup = stop_owned(process)
        broker.close()
        if terminal is not None:
            # This fixed allowance drains transport after owned cleanup; it
            # cannot extend the task's phase deadline or admit more worker work.
            drain_deadline = time.monotonic() + PTY_DRAIN_SECONDS
            while True:
                remaining = drain_deadline - time.monotonic()
                if remaining <= 0:
                    break
                terminal.resize()
                terminal_state = terminal.pump(min(0.05, remaining))
                if terminal_state["pty_eof"] and terminal_state["pending_output"] == 0:
                    drain_complete = True
                    break
            if not drain_complete:
                cause = cause or "Owned PTY output did not drain within the cleanup allowance"
            # Restoration is also a prerequisite to a new process-mode receipt.
            terminal.close()
            terminal_closed = True
        observation = {"exit_code": process.returncode, "duration_seconds": time.monotonic() - began,
                       "cleanup": cleanup, "event_count": broker.count, "observations": str(observations),
                       "completion_boundary": "OWNED_PROCESS_EXIT", "callback_origin": "NOT_AUTHENTICATED"}
        if completion_mode == "managed-session":
            observation.update(completion_boundary="MECHANICAL_TASK_COMMIT", completion_mode=completion_mode)
        managed_cleanup_failure = (completion_mode == "managed-session"
                                   and (not cleanup.get("leader_reaped") or cleanup.get("issues")))
        if cancellation.signal_number is not None:
            broker.interrupted = True
            cause = cancellation.reason()
        if cause or broker.error or broker.interrupted or not cleanup["group_absent"] or managed_cleanup_failure:
            result = failed(cause or broker.error or ("Native client reported interruption" if broker.interrupted
                            else "Owned cleanup could not be verified" if managed_cleanup_failure
                            else "Owned process group remains"), "COULD_NOT_RUN")
        elif process.returncode != 0:
            result = failed("Owned process exited unsuccessfully")
        elif completion_mode == "managed-session" and broker.task_result is not None:
            result = dict(broker.task_result)
        else:
            current = engine.context(state)
            if current["status"] == "WAITING_USER":
                result = current
            elif current["status"] != "READY" or completion_mode == "managed-session":
                result = failed("Owned process ended without all required observed phases", phase=current.get("phase"))
            elif cancellation.signal_number is not None:
                result = failed(cancellation.reason(), "COULD_NOT_RUN")
            else:
                result = engine.complete(state)
        result = {**result, "worker": observation, "scope": SCOPE,
                  "long_lived_task_completion": "NOT_IMPLEMENTED",
                  "native_provider_admission": "NOT_VALIDATED", "native_launch_admitted": False,
                  "execution_kind": "SYNTHETIC", "user_delivery": "NOT_RUN"}
        result = finish(result)
        publish_observation_result = True
        return result
    except (ValueError, TypeError, KeyError, IndexError, RecursionError) as error:
        return finish(failed(error))
    except delivery_core._Problem as error:
        return finish(failed(error.issue, error.result))
    except (OSError, subprocess.SubprocessError) as error:
        return finish(failed(error, "COULD_NOT_RUN"))
    except KeyboardInterrupt:
        if io_mode == "inherited":
            raise
        return finish(failed("Operator interrupted the owned terminal run", "COULD_NOT_RUN"))
    except Exception as error:
        if completion_mode == "process" and io_mode == "inherited":
            raise
        return finish(failed("Managed supervisor failed: " + str(error), "COULD_NOT_RUN"))
    finally:
        try:
            try:
                if process is not None and cleanup is None:
                    cleanup = stop_owned(process)
                    if (completion_mode == "managed-session" or io_mode == "interactive-tty") and outcome is not None:
                        outcome["worker"]["cleanup"] = cleanup
                        outcome["worker"]["exit_code"] = process.returncode
                        if (not cleanup.get("group_absent")
                                or not cleanup.get("leader_reaped")
                                or cleanup.get("issues")):
                            outcome.update(status="COULD_NOT_RUN")
                            outcome.setdefault("issues", []).append("Owned cleanup could not be verified")
            finally:
                if broker is not None and broker.socket_root.exists():
                    broker.close()
        except (Exception, KeyboardInterrupt) as error:
            if isinstance(error, KeyboardInterrupt) and io_mode == "inherited":
                raise
            if completion_mode == "process" and io_mode == "inherited":
                raise
            if outcome is not None:
                outcome.update(status="COULD_NOT_RUN")
                outcome.setdefault("issues", []).append("Owned cleanup unavailable: " + str(error))
        finally:
            try:
                if terminal is not None and not terminal_closed:
                    for restoration_attempt in range(2):
                        try:
                            terminal.close()
                            break
                        except (Exception, KeyboardInterrupt) as error:
                            if outcome is not None:
                                outcome.update(status="COULD_NOT_RUN")
                                outcome.setdefault("issues", []).append(
                                    "Parent terminal restoration failed: " + (str(error) or type(error).__name__))
                            # A signal may interrupt restoration between its steps.
                            # Retry it once while retaining the observable failure;
                            # close() tracks pending restoration and released fds.
                            if not isinstance(error, KeyboardInterrupt):
                                break
                if outcome is not None:
                    # The broker has been stopped/joined before the final view,
                    # including commits that finished during exception cleanup.
                    finish(outcome, final=True)
                    if cancellation.signal_number is not None:
                        outcome.update(status="COULD_NOT_RUN")
                        issues = outcome.setdefault("issues", [])
                        if cancellation.reason() not in issues:
                            issues.append(cancellation.reason())
                    if publish_observation_result:
                        try:
                            write_new(observations / "result.json", outcome)
                        except (OSError, ValueError, TypeError) as error:
                            outcome.update(status="COULD_NOT_RUN")
                            outcome.setdefault("issues", []).append("Process result observation failed: " + str(error))
            finally:
                try:
                    cancellation.restore()
                except (OSError, ValueError) as error:
                    if outcome is not None:
                        outcome.update(status="COULD_NOT_RUN")
                        outcome.setdefault("issues", []).append("Signal handler restoration failed: " + str(error))


def result_destination(args):
    """Validate the output channel before any admission or write to that channel."""
    path = canonical(args.result)
    if path.exists() or not path.parent.is_dir():
        raise ValueError("External result destination is occupied or unavailable")
    raw = delivery_core._read_external(canonical(args.contract), MAX_WIRE, "session")
    session = delivery_core._json(raw, "session")
    if not isinstance(session, dict):
        raise ValueError("Session must be an object")
    workflow_runtime.engine(session)
    contract, _, project = workflow_runtime.load_delivery(Path(session["delivery_contract"]))
    protected = [project, canonical(args.profile), canonical(args.state), canonical(args.contract),
                 Path(session["delivery_contract"]), Path(session["assignment"]["path"]),
                 Path(session["receipt_path"]), Path(__file__).resolve().parent.parent]
    protected += [Path(row["path"]) for row in session["installed_inputs"]]
    protected += workflow_runtime.protected_paths(contract)
    if args.client_root is not None:
        protected.append(canonical(args.client_root))
    if any(not separate(path, target) for target in protected):
        raise ValueError("External result destination overlaps a selected task path")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    run_parser = parser.add_subparsers(dest="action", required=True).add_parser("run")
    for flag in ("contract", "state", "profile", "result"):
        run_parser.add_argument("--" + flag, type=Path, required=True)
    run_parser.add_argument("--client-root", type=Path)
    run_parser.add_argument("--timeout", type=float, default=600)
    run_parser.add_argument("--synthetic", action="store_true", help="Isolated deterministic fixture execution only")
    run_parser.add_argument("--completion-mode", choices=sorted(COMPLETION_MODES), default="process")
    run_parser.add_argument("--io-mode", choices=sorted(IO_MODES), default="inherited")
    run_parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        destination = result_destination(args)
    except (OSError, ValueError, TypeError, KeyError, delivery_core._Problem) as error:
        print(json.dumps(failed("Result destination preflight failed: " + str(error))), file=sys.stderr)
        return 2
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    result = run(args.contract, args.state, args.profile, command, args.timeout, args.client_root, args.result,
                 synthetic=args.synthetic, completion_mode=args.completion_mode, io_mode=args.io_mode)
    try:
        write_new(destination, result)
    except OSError as error:
        print("Cannot deliver runtime result: " + str(error), file=sys.stderr)
        return 2
    return 2 if result["status"] in {"FAIL", "COULD_NOT_RUN"} else 0


if __name__ == "__main__":
    sys.exit(main())
