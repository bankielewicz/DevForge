"""Owned bounded native processes; authenticated collection is not a grade.

The protected journal calls prepare_request before its exclusive launch claim,
then launch outside its lock. No schedule, campaign clock, login, retries, or
fallback is owned here. Native execution requires frozen effective-runtime
observations; a declaration or a fixture is not such an observation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import time
import tomllib


class NativeProcessError(ValueError):
    """A required binding, boundary, or collection observation is unavailable."""


LIMIT = 8 * 1024 * 1024
OUTPUT_LIMIT = 64 * 1024 * 1024
CLIENT = {"path": "/home/bryan/.codex/packages/standalone/releases/0.153.4-x86_64-unknown-linux-musl/bin/codex",
          "sha256": "56ef98ab4032d317ab26e9b5e5a175650717351edb16ed9cde0cb6d1734d62da", "version": "0.153.4"}
MODEL = "gpt-6-astra"
SYSTEM_MOUNTS = frozenset(("/usr/bin", "/usr/lib", "/usr/lib64", "/bin", "/lib", "/lib64",
                           "/etc/ssl/certs", "/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf"))
OBSERVATIONS = frozenset(("effective_hooks", "tool_profile_deny_read", "tool_network_denied",
                         "fresh_subscription_profile", "process_namespace", "nested_model_routes_disabled"))
DISABLED_FEATURES = frozenset(("apps", "plugins", "remote_plugin", "multi_agent", "multi_agent_v2",
                              "guardian_approval", "goals", "memories", "browser_use", "browser_use_external",
                              "computer_use", "image_generation", "code_mode", "code_mode_host",
                              "skill_mcp_dependency_install", "unbounded_connection_retries"))


def canonical_json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise NativeProcessError("Expected finite JSON values") from error


def digest(value):
    return hashlib.sha256(value).hexdigest()


def _exact(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise NativeProcessError(label + " has missing or unknown fields")


def _json(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise NativeProcessError("Duplicate JSON key")
            result[key] = value
        return result
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(NativeProcessError("Nonfinite JSON")))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise NativeProcessError("Invalid strict JSON") from error


def _number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise NativeProcessError(label + " must be finite and nonnegative")
    return value


def _path(value, *, directory=False):
    path = Path(value)
    if not path.is_absolute() or str(path) != str(value) or path.resolve() != path:
        raise NativeProcessError("Canonical absolute path required: " + str(value))
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise NativeProcessError("Symlink path component: " + str(current))
    if directory and not path.is_dir():
        raise NativeProcessError("Required directory unavailable: " + str(path))
    return path


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _read(path, limit=LIMIT):
    path = _path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise NativeProcessError("Bounded single-link regular file required: " + str(path))
        raw = bytearray()
        while block := os.read(fd, min(65536, limit + 1 - len(raw))):
            raw.extend(block)
            if len(raw) > limit:
                raise NativeProcessError("File exceeds bound")
        if (len(raw) != before.st_size or _identity(before) != _identity(os.fstat(fd))
                or _identity(before) != _identity(path.lstat())):
            raise NativeProcessError("File changed during collection")
        return bytes(raw)
    finally:
        os.close(fd)


def _file_digest(path, limit=512 * 1024 * 1024):
    # Stream the large selected client instead of copying it to evidence.
    path = _path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise NativeProcessError("Invalid pinned file")
        checksum, count = hashlib.sha256(), 0
        while block := os.read(fd, 65536):
            count += len(block)
            if count > before.st_size:
                raise NativeProcessError("Pinned file grew")
            checksum.update(block)
        if (count != before.st_size or _identity(before) != _identity(os.fstat(fd))
                or _identity(before) != _identity(path.lstat())):
            raise NativeProcessError("Pinned file changed")
        return checksum.hexdigest()
    finally:
        os.close(fd)


def _pin(ref, *, read=True):
    _exact(ref, {"path", "sha256"}, "file pin")
    if not isinstance(ref["sha256"], str) or re.fullmatch("[0-9a-f]{64}", ref["sha256"]) is None:
        raise NativeProcessError("Invalid SHA-256")
    value = _read(ref["path"]) if read else None
    actual = digest(value) if read else _file_digest(ref["path"])
    if actual != ref["sha256"]:
        raise NativeProcessError("Pinned bytes changed: " + ref["path"])
    return value


def tree_digest(path):
    """Bound a selected disposable Git common directory; never follow pointers."""
    path = _path(path, directory=True)
    rows = []
    for parent, dirs, files in os.walk(path, followlinks=False):
        for name in sorted(dirs + files):
            item = Path(parent) / name
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise NativeProcessError("Fixture tree contains a symlink or special file")
            rows.append({"path": str(item.relative_to(path)), "sha256": None if item.is_dir()
                         else _file_digest(item, LIMIT), "mode": stat.S_IMODE(info.st_mode)})
            if len(rows) > 10000:
                raise NativeProcessError("Fixture tree exceeds entry bound")
    return digest(canonical_json(sorted(rows, key=lambda row: row["path"])))


def _overlap(left, right):
    return left.is_relative_to(right) or right.is_relative_to(left)


def discovery_surfaces(workspace, profile):
    """Prepared empty stubs preserve absence without writable discovery parents."""
    directories = {workspace / ".agents", workspace / ".codex", profile / ".agents", profile / ".codex",
                   profile / "skills", profile / "rules"}
    files = {root / name for root in (workspace, profile) for name in ("AGENTS.md", "AGENTS.override.md")}
    files |= {profile / "hooks.json", profile / "config.toml"}
    return directories, files


def runtime_inputs_digest(row):
    """Non-cyclic identity for the effective observer's exact selected surfaces."""
    selected = {key: row[key] for key in ("readonly_inputs", "system_mounts", "fixture_git_dirs", "immutable_discovery_dirs", "managed_worker", "interaction")}
    if "answer_policy" in row:
        selected["answer_policy"] = row["answer_policy"]
    return digest(canonical_json(selected))


def _toml(value):
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(key) + "=" + _toml(item) for key, item in sorted(value.items())) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_toml(item) for item in value) + "]"
    if type(value) in (str, int, float, bool):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    raise NativeProcessError("Unsupported TOML configuration value")


def _config(raw, workspace, profile):
    try:
        cfg = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise NativeProcessError("Invalid selected TOML configuration") from error
    required = {"model": MODEL, "model_reasoning_effort": "medium", "approval_policy": "never",
                "default_permissions": "native", "web_search": "disabled", "forced_login_method": "chatgpt",
                "check_for_update_on_startup": False}
    if any(cfg.get(key) != value for key, value in required.items()):
        raise NativeProcessError("Selected config omits required model/auth/approval controls")
    permission = cfg.get("permissions", {}).get("native", {})
    if (permission.get("filesystem") != {"/": "read", str(workspace): "write",
                                           str(profile): "deny", "/proc": "deny"}
            or permission.get("network") != {"enabled": False}
            or set(permission) != {"filesystem", "network"} or set(cfg["permissions"]) != {"native"}):
        raise NativeProcessError("Selected tool sandbox must deny profile/proc reads and network")
    if cfg.get("model_provider", "openai") != "openai":
        raise NativeProcessError("Only the selected subscription provider is supported")
    if cfg.get("model_providers") != {"openai": {"request_max_retries": 0, "stream_max_retries": 0}}:
        raise NativeProcessError("Selected config must disable request and stream retries")
    if cfg.get("shell_environment_policy") != {"inherit": "none", "set": {
            "PATH": "/usr/bin:/bin", "HOME": str(workspace), "LANG": "C.UTF-8"}}:
        raise NativeProcessError("Tool environment must exclude the private subscription profile")
    if any(cfg.get("features", {}).get(name) is not False for name in DISABLED_FEATURES):
        raise NativeProcessError("Nested model/approval/integration/continuation routes must be disabled")
    allowed = set(required) | {"permissions", "model_provider", "model_providers", "shell_environment_policy",
                               "features", "hooks", "projects", "mcp_servers", "apps"}
    if set(cfg) - allowed or cfg.get("mcp_servers", {}) or cfg.get("apps", {}):
        raise NativeProcessError("Unselected provider/tool/configuration extension")
    return cfg


def prepare_request(plan, attempt, binding, reserved_at, deadline, campaign_origin_utc, installed_inputs):
    """Validate a frozen native config and derive the only supported exec command.

    Binding is supplied by the locked journal. Effective observations are owner
    gate evidence, never obtained from worker output by this module. Configuration
    and installed-input pins contain no credentials; auth bytes are never read.
    """
    if plan.get("client") != CLIENT or plan.get("model") != MODEL:
        raise NativeProcessError("Client/model differs from the approved native selection")
    _number(reserved_at, "reserved_at")
    _number(deadline, "deadline")
    if not reserved_at < deadline <= min(14400, reserved_at + 600):
        raise NativeProcessError("Reservation deadline exceeds the approved original clock")
    try:
        origin = datetime.fromisoformat(campaign_origin_utc.replace("Z", "+00:00"))
        if origin.utcoffset() is None or origin.utcoffset().total_seconds() != 0:
            raise ValueError()
    except (ValueError, AttributeError) as error:
        raise NativeProcessError("Original campaign origin must be UTC") from error
    if attempt not in plan.get("attempts", []):
        raise NativeProcessError("Attempt is not in the frozen plan")
    cfgraw = _pin(plan["runtime_configuration"])
    runtime = _json(cfgraw)
    _exact(runtime, {"schema_version", "allocation", "attempts"}, "runtime configuration")
    if runtime["schema_version"] != "devforge.native-runtime-configuration/v1":
        raise NativeProcessError("Unsupported runtime configuration")
    _pin(runtime["allocation"])
    rows = runtime["attempts"]
    if (not isinstance(rows, list) or len(rows) != len(plan["attempts"])
            or {row.get("attempt_id") for row in rows} != {row["attempt_id"] for row in plan["attempts"]}):
        raise NativeProcessError("Runtime configuration must cover every allocated attempt exactly")
    row = next(row for row in rows if row["attempt_id"] == attempt["attempt_id"])
    _exact(row, {"attempt_id", "config", "prompt", "readonly_inputs", "system_mounts", "fixture_git_dirs",
                 "effective_runtime", "managed_worker", "interaction", "immutable_discovery_dirs"} | ({"answer_policy"} if row.get("interaction") == "awaiting-user" else set()), "attempt runtime")
    policy = None
    if row["interaction"] == "awaiting-user":
        policy = _json(_pin(row["answer_policy"]))
        units = answer_policy(policy)
        allocation = _json(_pin(runtime["allocation"]))
        selected = [c for c in allocation["required_calls"] if c["attempt_id"] == attempt["attempt_id"]]
        if len(selected) != 1 or selected[0].get("continuation_units") != units:
            raise NativeProcessError("Every continuation requires exact frozen counted allocation")
    elif row["interaction"] != "single-turn":
        raise NativeProcessError("Interactive answer transport does not support this interaction")
    workspace = _path(attempt["workspace"], directory=True)
    profile = _path(attempt["client_state"], directory=True)
    if _overlap(workspace, profile):
        raise NativeProcessError("Workspace and fresh profile overlap")
    discovery_dirs, discovery_files = discovery_surfaces(workspace, profile)
    immutable = row["immutable_discovery_dirs"]
    if (not isinstance(immutable, list) or len(immutable) != len(discovery_dirs)
            or {Path(ref["path"]) for ref in immutable} != discovery_dirs):
        raise NativeProcessError("Every discovery directory needs an explicit immutable inventory, including empty stubs")
    for ref in immutable:
        _exact(ref, {"path", "sha256"}, "immutable discovery directory")
        if tree_digest(ref["path"]) != ref["sha256"]:
            raise NativeProcessError("Immutable discovery inventory changed")
    cfg = _config(_pin(row["config"]), workspace, profile)
    _pin(row["prompt"])
    report = _json(_pin(row["effective_runtime"]))
    expected_report = {"attempt_id": attempt["attempt_id"], "client": CLIENT, "model": MODEL,
                       "config_sha256": row["config"]["sha256"], "prompt_sha256": row["prompt"]["sha256"],
                       "runtime_inputs_sha256": runtime_inputs_digest(row),
                       "workspace": str(workspace), "client_state": str(profile)}
    _exact(report, set(expected_report) | {"schema_version", "observations"}, "effective runtime evidence")
    if (report["schema_version"] != "devforge.native-effective-runtime/v1"
            or any(report[key] != value for key, value in expected_report.items())):
        raise NativeProcessError("Effective runtime evidence binding differs")
    _exact(report["observations"], OBSERVATIONS, "effective runtime observations")
    pins = [plan[name] for name in ("candidate", "baseline", "specification", "cases", "boundary_evidence",
                                   "runtime_configuration")]
    pins += [plan["authentication"]["arrangement_ref"], runtime["allocation"], row["config"],
             row["prompt"], row["effective_runtime"]]
    for observed in report["observations"].values():
        _exact(observed, {"status", "evidence"}, "effective observation")
        if observed["status"] != "OBSERVED":
            raise NativeProcessError("Effective runtime boundary is not observed")
        _pin(observed["evidence"])
        pins.append(observed["evidence"])
    readonly = row["readonly_inputs"]
    if not isinstance(readonly, list) or not readonly or len(readonly) > 10000:
        raise NativeProcessError("Exact bounded readonly input inventory required")
    paths = set()
    for ref in readonly:
        _pin(ref, read=False)
        path = _path(ref["path"])
        profile_metadata = path in discovery_files or any(path.is_relative_to(root) for root in discovery_dirs)
        if path in paths or _overlap(path, profile) and not profile_metadata:
            raise NativeProcessError("Duplicate input or profile-overlapping readonly input")
        paths.add(path)
    if not discovery_files.issubset(paths):
        raise NativeProcessError("Instruction/hook/config files need immutable pins, including absence stubs")
    for directory in discovery_dirs:
        for parent, _, files in os.walk(directory):
            if any(Path(parent) / name not in paths for name in files):
                raise NativeProcessError("Discovery directory contains an unpinned file")
    if any(ref not in readonly for ref in installed_inputs):
        raise NativeProcessError("Every installed input must be mounted immutable")
    if {key: CLIENT[key] for key in ("path", "sha256")} not in readonly:
        raise NativeProcessError("Exact client binary is not in readonly mount inventory")
    _file_digest(CLIENT["path"])
    if not os.access(CLIENT["path"], os.X_OK):
        raise NativeProcessError("Selected client is not executable")
    mounts = row["system_mounts"]
    if not isinstance(mounts, list) or not mounts or len(mounts) != len(set(mounts)):
        raise NativeProcessError("Explicit system runtime mount selection required")
    for item in mounts:
        if item not in SYSTEM_MOUNTS or not Path(item).exists():
            raise NativeProcessError("Unselected system runtime mount")
    fixture_dirs = row["fixture_git_dirs"]
    if not isinstance(fixture_dirs, list) or len(fixture_dirs) > 64:
        raise NativeProcessError("Bounded explicit fixture Git directory list required")
    for fixture in fixture_dirs:
        _exact(fixture, {"path", "sha256"}, "fixture Git directory")
        path = _path(fixture["path"], directory=True)
        if _overlap(path, profile) or path == workspace or path == workspace / ".git":
            raise NativeProcessError("Fixture Git selection overlaps protected or outer metadata")
        if tree_digest(path) != fixture["sha256"]:
            raise NativeProcessError("Selected fixture Git tree changed")
    managed = row["managed_worker"]
    if managed is not None:
        if cfg.get("features", {}).get("hooks") is not True:
            raise NativeProcessError("Managed worker requires explicitly enabled observed hooks")
        _exact(managed, {"session", "state", "gate_executable"}, "managed worker")
        session = _json(_pin(managed["session"]))
        if (session.get("schema_version") != "devforge.utility-session/v1" or session.get("provider") != "codex"
                or session.get("task_id") == plan["task_id"]):
            raise NativeProcessError("Managed worker must have a distinct selected utility session")
        state = _path(managed["state"])
        if state.exists() or not state.parent.is_dir():
            raise NativeProcessError("Managed worker state must be fresh with a prepared parent")
        _pin(managed["gate_executable"], read=False)
        if managed["gate_executable"] not in readonly:
            raise NativeProcessError("Managed gate executable must be an exact immutable input")
        contract_ref = {"path": session["delivery_contract"], "sha256": session["delivery_contract_sha256"]}
        contract = _json(_pin(contract_ref))
        if contract.get("project_root") != str(workspace):
            raise NativeProcessError("Managed worker delivery project differs from its prepared workspace")
        protected = [state, _path(managed["session"]["path"]), _path(session["delivery_contract"]),
                     _path(session["assignment"]["path"]), _path(session["receipt_path"])]
        protected += [_path(item["path"]) for item in contract["gate_inputs"]]
        protected += [_path(item["decision_path"]) for item in contract["questions"] if item["decision_path"] is not None]
        visible = [workspace, profile, *paths, *[_path(item["path"]) for item in fixture_dirs]]
        if any(_overlap(target, mount) for target in protected for mount in visible):
            raise NativeProcessError("Managed authority/state/gate is visible in the worker mount inventory")
        if any(item not in readonly for item in session["installed_inputs"]):
            raise NativeProcessError("Managed session installed resources are not mounted immutable")
        for code in Path(__file__).parent.glob("*.py"):
            if not any(item["path"] == str(code) for item in readonly):
                raise NativeProcessError("Managed broker runtime code is not fully pinned and immutable")
        pins += [managed["session"], contract_ref, session["assignment"]]
    # All authority/gate/source documents stay outside the view; only selected
    # readonly installation/fixtures are exposed, not the original source tree.
    if policy is not None:
        pins.append(row["answer_policy"])
    for ref in pins:
        _pin(ref)
        if any(_overlap(_path(ref["path"]), root) for root in (workspace, profile)):
            raise NativeProcessError("Control evidence overlaps an attempt writable root")
        if any(_overlap(_path(ref["path"]), _path(mount["path"])) for mount in readonly + fixture_dirs):
            raise NativeProcessError("Control evidence must not be visible in a worker mount")
    command = [CLIENT["path"], "--strict-config", "--ask-for-approval", "never", "exec", "--json",
               "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
               "--model", MODEL, "--cd", str(workspace), "--color", "never"]
    if policy is not None:
        command = [CLIENT["path"], "--strict-config", "app-server", "--stdio"]
    for key, value in sorted(cfg.items()):
        command += ["-c", key + "=" + _toml(value)]
    if policy is None:
        command.append("-")
    binding = dict(binding)
    required = {"task_id", "schedule_binding", "challenge", "reservation_sha256", "attempt_id", "plan_sha256"}
    if set(binding) != required or binding["task_id"] != plan["task_id"] or binding["attempt_id"] != attempt["attempt_id"]:
        raise NativeProcessError("Protected reservation binding is incomplete")
    if any(not isinstance(value, str) or not value for value in binding.values()):
        raise NativeProcessError("Empty reservation binding")
    binding.update(command_sha256=digest(canonical_json(command)), client=CLIENT.copy(), model=MODEL,
                   runtime_configuration_sha256=plan["runtime_configuration"]["sha256"],
                   prompt_sha256=row["prompt"]["sha256"], installed_inputs_sha256=digest(canonical_json(installed_inputs)),
                   reserved_at=reserved_at, deadline=deadline, campaign_origin_utc=campaign_origin_utc)
    return {"binding": binding, "command": command, "workspace": str(workspace), "profile": str(profile),
            "prompt": row["prompt"], "pins": pins + readonly, "readonly_inputs": readonly,
            "system_mounts": mounts, "fixture_git_dirs": fixture_dirs, "reserved_at": reserved_at,
            "deadline": deadline, "campaign_origin_utc": campaign_origin_utc, "output_limit": OUTPUT_LIMIT,
            "managed_worker": managed, "immutable_discovery_dirs": immutable,
            **({"answer_policy": policy, "answer_policy_pin": row["answer_policy"]} if policy is not None else {})}


def sandbox_command(request, broker=None):
    """Empty root with selected mounts; never mount host / or follow outer .git."""
    workspace, profile = (_path(request[key], directory=True) for key in ("workspace", "profile"))
    binary = Path("/usr/bin/bwrap")
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise NativeProcessError("Bubblewrap unavailable; no unconfined fallback")
    argv = [str(binary), "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc",
            "--unshare-uts", "--cap-drop", "ALL", "--tmpfs", "/", "--proc", "/proc", "--dev", "/dev"]
    for name in request["system_mounts"]:
        if name not in SYSTEM_MOUNTS:
            raise NativeProcessError("Unselected system mount")
        # System aliases are explicit; no project-controlled link is followed.
        argv += ["--ro-bind", str(Path(name).resolve(strict=True)), name]
    argv += ["--bind", str(workspace), str(workspace), "--bind", str(profile), str(profile)]
    for ref in sorted(request["readonly_inputs"], key=lambda ref: (len(Path(ref["path"]).parts), ref["path"])):
        argv += ["--ro-bind", ref["path"], ref["path"]]
    for ref in request["fixture_git_dirs"]:
        argv += ["--ro-bind", ref["path"], ref["path"]]
    for ref in request.get("immutable_discovery_dirs", []):
        argv += ["--ro-bind", ref["path"], ref["path"]]
    for path in (workspace / ".git", workspace / ".codex" / "config.toml"):
        if path.is_symlink():
            raise NativeProcessError("Symlink outer metadata/config cannot be safely masked")
        if path.is_dir():
            argv += ["--tmpfs", str(path), "--remount-ro", str(path)]
        elif path.exists():
            argv += ["--ro-bind", "/dev/null", str(path)]
    environment = {"HOME": str(profile), "CODEX_HOME": str(profile), "LANG": "C.UTF-8",
                   "PATH": "/usr/bin:/bin", "TMPDIR": str(workspace), "PYTHONDONTWRITEBYTECODE": "1"}
    if broker is not None:
        argv += ["--ro-bind", str(broker.socket_root), str(broker.socket_root)]
        environment.update(DEVFORGE_DELIVERY_SOCKET=str(broker.socket_path),
                           DEVFORGE_DELIVERY_CONTRACT_SHA256=broker.contract_digest,
                           DEVFORGE_DELIVERY_EXECUTABLE=request["managed_worker"]["gate_executable"]["path"])
    argv.append("--clearenv")
    for key, value in sorted(environment.items()):
        argv += ["--setenv", key, value]
    return argv + ["--remount-ro", "/", "--chdir", str(workspace), "--", *request["command"]]


def observe_jsonl(raw, *, complete=True):
    """Strict stream observations only. Agent text and nested JSON confer nothing."""
    result = {"status": "UNOBTAINABLE", "thread_started": False, "turn_started": False,
              "turn_completed": False, "turn_failed": False, "errors": 0, "issues": []}
    if not complete or not raw or not raw.endswith(b"\n"):
        result["issues"].append("Empty, truncated, or incomplete JSONL stream")
        return result
    state, count = "initial", 0
    try:
        for line in raw.splitlines():
            count += 1
            if count > 100000 or len(line) > LIMIT:
                raise NativeProcessError("JSONL event bound exceeded")
            event = _json(line)
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise NativeProcessError("JSONL event requires a type")
            kind = event["type"]
            if kind == "thread.started":
                if state != "initial" or not isinstance(event.get("thread_id"), str) or not event["thread_id"]:
                    raise NativeProcessError("Repeated/malformed/out-of-order thread.started")
                result["thread_started"], state = True, "thread"
            elif kind == "turn.started":
                if state != "thread":
                    raise NativeProcessError("Repeated/out-of-order turn.started")
                result["turn_started"], state = True, "turn"
            elif kind in {"turn.completed", "turn.failed"}:
                if state != "turn":
                    raise NativeProcessError("Repeated/out-of-order turn terminal event")
                if kind == "turn.completed":
                    usage = event.get("usage")
                    if (not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0
                            for key in ("input_tokens", "cached_input_tokens", "output_tokens"))):
                        raise NativeProcessError("Malformed native completion usage")
                result[kind.replace(".", "_")], state = True, "terminal"
            elif kind == "error":
                result["errors"] += 1
            elif kind in {"item.started", "item.updated", "item.completed"}:
                if state != "turn" or not isinstance(event.get("item"), dict):
                    raise NativeProcessError("Malformed/out-of-turn item event")
            else:
                raise NativeProcessError("Unrecognized native event type")
        if state != "terminal" or not result["turn_completed"] or result["errors"]:
            raise NativeProcessError("Successful native turn terminal event unavailable")
        result["status"] = "OBSERVED"
    except NativeProcessError as error:
        result["issues"].append(str(error))
    return result


def _exited(process):
    # WNOWAIT reserves the leader PID until every owned-group signal is finished.
    return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def _group_exists(pid):
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


def answer_policy(value):
    _exact(value, {"schema_version", "steps"}, "answer policy")
    if value["schema_version"] not in {"devforge.native-answer-policy/v1", "devforge.native-answer-policy/v2"} or not isinstance(value["steps"], list) or len(value["steps"]) > 23:
        raise NativeProcessError("Invalid bounded answer policy")
    units = []
    for step in value["steps"]:
        action = step.get("action")
        fields = {"unit_id", "action", "questions", "answers"} if action == "answer" else {"unit_id", "action", "text"}
        if action in {"operator_answer", "operator_turn"} and value["schema_version"] == "devforge.native-answer-policy/v2":
            fields = {"unit_id", "action", "allowed_answers"}
        _exact(step, fields, "answer step")
        unit = step["unit_id"]
        if not isinstance(unit, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", unit) is None or unit == "initial" or unit in units:
            raise NativeProcessError("Invalid or duplicate continuation unit")
        units.append(unit)
        if action == "answer":
            questions, answers = step["questions"], step["answers"]
            if not isinstance(questions, list) or not questions or not isinstance(answers, dict) or {q.get("id") for q in questions} != set(answers) or len(questions) != len(answers):
                raise NativeProcessError("Answer policy must cover exact question IDs")
            for answer in answers.values():
                _exact(answer, {"answers"}, "operator answer")
                if not isinstance(answer["answers"], list) or not answer["answers"] or any(not isinstance(a, str) for a in answer["answers"]):
                    raise NativeProcessError("Operator answer is empty or invalid")
        elif action in {"operator_answer", "operator_turn"}:
            answers = step["allowed_answers"]
            if not isinstance(answers, dict) or not 1 <= len(answers) <= 64 or any(not isinstance(k, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", k) is None or not isinstance(v, str) or not 1 <= len(v) <= 4096 for k, v in answers.items()):
                raise NativeProcessError("Operator choices must be finite frozen literal answers")
        elif action != "turn" or not isinstance(step["text"], str) or not step["text"]:
            raise NativeProcessError("Unsupported continuation action")
    return units


def _operator_questions(message):
    if message.get("method") == "devforge/operatorTurn":
        if not message["params"].get("assistantMessages"):
            raise NativeProcessError("Completed assistant text is unavailable")
        return ["next_turn"]
    questions = message.get("params", {}).get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 16:
        raise NativeProcessError("Bounded actual operator questions required")
    ids = []
    for question in questions:
        if not isinstance(question, dict) or not isinstance(question.get("id"), str) or not question["id"] or not isinstance(question.get("question"), str) or not question["question"]:
            raise NativeProcessError("Actual operator question is malformed")
        ids.append(question["id"])
    if len(set(ids)) != len(ids):
        raise NativeProcessError("Duplicate actual question ID")
    return ids


def _operator_pending(binding, policy, step, message):
    return {"schema_version": "devforge.native-operator-request/v1", "binding": binding,
            "unit_id": step["unit_id"], "policy_sha256": digest(canonical_json(policy)),
            "request_sha256": digest(canonical_json(message)), "request": message,
            "question_ids": _operator_questions(message), "allowed_answers": step["allowed_answers"]}


def _operator_selection(pending, value):
    _exact(value, {"schema_version", "binding", "unit_id", "policy_sha256", "request_sha256", "relevance_confirmed", "selections"}, "operator decision")
    if value["schema_version"] != "devforge.native-operator-decision/v1" or value["relevance_confirmed"] is not True or any(value[k] != pending[k] for k in ("binding", "unit_id", "policy_sha256", "request_sha256")):
        raise NativeProcessError("Operator decision does not bind this request and frozen policy")
    choices = value["selections"]
    if not isinstance(choices, dict) or set(choices) != set(pending["question_ids"]) or any(not isinstance(k, str) or k not in pending["allowed_answers"] for k in choices.values()):
        raise NativeProcessError("Operator decision must cover every actual question with frozen keys")
    return {question: {"answers": [pending["allowed_answers"][key]]} for question, key in choices.items()}


def write_operator_decision(pending_path, selections, *, relevance_confirmed):
    """Operator-only helper: exclusive publication, no inferred answer or authority."""
    path = _path(pending_path)
    if re.fullmatch(r"operator-[A-Za-z0-9_-]{1,80}-request\.json", path.name) is None:
        raise NativeProcessError("Exact protected pending request filename required")
    info = path.parent.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise NativeProcessError("Operator inbox must be host-private")
    pending = _json(_read(path))
    if pending.get("schema_version") != "devforge.native-operator-request/v1" or not isinstance(pending.get("unit_id"), str) or path.name != "operator-" + pending["unit_id"] + "-request.json" or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", pending["unit_id"]) is None:
        raise NativeProcessError("Pending request unit binding is invalid")
    value = {"schema_version": "devforge.native-operator-decision/v1", **{k: pending[k] for k in ("binding", "unit_id", "policy_sha256", "request_sha256")},
             "relevance_confirmed": relevance_confirmed, "selections": selections}
    _operator_selection(pending, value)
    target = path.parent / ("operator-" + pending["unit_id"] + "-decision.json")
    if target.name != path.name.replace("-request.json", "-decision.json"):
        raise NativeProcessError("Pending unit filename differs")
    _new(target, canonical_json(value))
    return target


class OperatorInbox:
    """Protected collector-owned request/decision custody; no worker-selected path."""
    def __init__(self, root, binding, policy, *, replay=None):
        self.root, self.binding, self.policy = _path(root, directory=True), binding, policy
        info = self.root.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise NativeProcessError("Operator inbox must be an owned private directory")
        self.records = [] if replay is None else replay
        self.replay = replay is not None
        self.current = None
        self.used = set()

    def publish(self, step, message):
        unit = step["unit_id"]
        if unit in self.used or self.current is not None:
            raise NativeProcessError("Operator unit cannot be reused")
        self.used.add(unit)
        raw = canonical_json(_operator_pending(self.binding, self.policy, step, message))
        if len(raw) > LIMIT:
            raise NativeProcessError("Operator request document exceeds bound")
        pending = _json(raw)
        path = self.root / ("operator-" + unit + "-request.json")
        if self.replay:
            rows = [r for r in self.records if r["unit_id"] == unit]
            if len(rows) != 1 or rows[0]["request"]["path"] != str(path) or _json(_pin(rows[0]["request"])) != pending:
                raise NativeProcessError("Operator request replay differs")
            row = rows[0]
        else:
            _new(path, canonical_json(pending))
            row = {"unit_id": unit, "request": {"path": str(path), "sha256": digest(canonical_json(pending))}, "decision": None, "accepted": None}
            self.records.append(row)
        self.current = (pending, row)

    def poll(self):
        if self.current is None:
            return None
        pending, row = self.current
        path = self.root / ("operator-" + pending["unit_id"] + "-decision.json")
        if self.replay:
            if row["decision"] is None or row["decision"]["path"] != str(path):
                raise NativeProcessError("Operator decision is missing")
            raw = _pin(row["decision"])
        else:
            try:
                raw = _read(path)
            except FileNotFoundError:
                return None
            row["decision"] = {"path": str(path), "sha256": digest(raw)}
        answers = _operator_selection(pending, _json(raw))
        accepted = self.root / ("operator-" + pending["unit_id"] + "-accepted.json")
        if self.replay:
            if row["accepted"] is None or row["accepted"]["path"] != str(accepted) or _pin(row["accepted"]) != raw:
                raise NativeProcessError("Accepted operator bytes differ")
        else:
            _new(accepted, raw)
            row["accepted"] = {"path": str(accepted), "sha256": digest(raw)}
        self.current = None
        return answers


class Interactive:
    """Frozen app-server protocol. Reservation callback must fsync before returning."""
    def __init__(self, policy, prompt, workspace, reserve, transcript, operator=None):
        answer_policy(policy)
        self.steps = list(policy["steps"])
        self.prompt, self.workspace = prompt.decode("utf-8"), workspace
        self.reserve, self.transcript = reserve, transcript
        self.state, self.thread, self.turn = "new", None, None
        self.done, self.index, self.sequence = False, 0, 0
        self.requests = set()
        self.buffer = b""
        self.sent_prompts = []
        self.operator, self.pending_operator = operator, None
        self.agent_messages = []

    def record(self, direction, message):
        self.transcript.write(canonical_json({"direction": direction, "message": message}) + b"\n")
        self.transcript.flush()
        try:
            os.fsync(self.transcript.fileno())
        except (AttributeError, OSError):
            # BytesIO is used only by unsigned deterministic fixtures.
            import io
            if not isinstance(self.transcript, io.BytesIO):
                raise

    def sent(self, raw):
        for line in raw.splitlines():
            message = _json(line)
            if message.get("method") == "turn/start":
                self.sent_prompts.append(message["params"]["input"][0]["text"])

    def start(self):
        self.state = "init"
        return {"id": "init", "method": "initialize", "params": {"clientInfo": {"name": "devforge", "version": "1"}, "capabilities": {"experimentalApi": True}}}

    def poll(self):
        if self.pending_operator is None:
            return []
        answers = self.operator.poll()
        if answers is None:
            return []
        step, message = self.pending_operator
        self.pending_operator = None
        self.index += 1
        if step["action"] == "operator_turn":
            self.sequence += 1
            return self.begin_turn(answers["next_turn"]["answers"][0], step["unit_id"])
        answer = {"id": message["id"], "result": {"answers": answers}}
        self.reserve(step["unit_id"], answer)
        self.requests.add(message["id"])
        self.state = "running"
        return [answer]

    def await_operator(self, step, message):
        if self.operator is None:
            raise NativeProcessError("Protected operator inbox is unavailable")
        self.operator.publish(step, message)
        self.pending_operator = (step, message)
        self.state = "operator-wait"
        return []

    def begin_turn(self, text, unit):
        message = {"id": "turn-" + str(self.sequence), "method": "turn/start", "params": {"threadId": self.thread, "input": [{"type": "text", "text": text, "text_elements": []}]}}
        self.reserve(unit, message)
        self.state = "turn-response"
        self.agent_messages = []
        return [message]

    def receive(self, message):
        if self.done or not isinstance(message, dict) or "error" in message:
            raise NativeProcessError("Unexpected or failed app-server message")
        method = message.get("method")
        if method is None:
            identity, result = message.get("id"), message.get("result")
            if not isinstance(result, dict):
                raise NativeProcessError("Invalid app-server response")
            if self.state == "init" and identity == "init":
                self.state = "thread-response"
                return [{"method": "initialized"}, {"id": "thread", "method": "thread/start", "params": {"cwd": self.workspace, "model": MODEL, "ephemeral": True}}]
            if self.state == "thread-response" and identity == "thread":
                self.thread = result.get("thread", {}).get("id")
                if not isinstance(self.thread, str) or not self.thread:
                    raise NativeProcessError("Missing thread correlation")
                return self.begin_turn(self.prompt, "initial")
            if self.state == "turn-response" and identity == "turn-" + str(self.sequence):
                self.turn = result.get("turn", {}).get("id")
                if not isinstance(self.turn, str) or not self.turn:
                    raise NativeProcessError("Missing turn correlation")
                self.state = "turn-start"
                return []
            raise NativeProcessError("Uncorrelated app-server response")
        params = message.get("params", {})
        if method == "thread/started":
            if params.get("thread", {}).get("id") != self.thread:
                raise NativeProcessError("Thread notification mismatch")
            return []
        if params.get("threadId") != self.thread:
            raise NativeProcessError("Thread correlation mismatch")
        if method in {"turn/started", "turn/completed"}:
            turn = params.get("turn", {})
            if turn.get("id") != self.turn:
                raise NativeProcessError("Turn correlation mismatch")
            if method == "turn/started" and self.state == "turn-start":
                self.state = "running"
                return []
            if method == "turn/completed" and self.state == "running" and turn.get("status") == "completed":
                if self.index == len(self.steps):
                    self.done = True
                    return []
                step = self.steps[self.index]
                if step["action"] == "operator_turn":
                    pending = {"method": "devforge/operatorTurn", "params": {"threadId": self.thread, "turnId": self.turn, "completion": message, "assistantMessages": self.agent_messages}}
                    return self.await_operator(step, pending)
                if step["action"] != "turn":
                    raise NativeProcessError("Required operator question was not observed")
                self.index += 1
                self.sequence += 1
                return self.begin_turn(step["text"], step["unit_id"])
            raise NativeProcessError("Out-of-order or failed turn lifecycle")
        if method == "item/tool/requestUserInput":
            identity = message.get("id")
            if self.state != "running" or params.get("turnId") != self.turn or params.get("isBlocking") is not True or params.get("autoResolutionMs") is not None or not params.get("itemId") or type(identity) not in (str, int) or identity in self.requests or self.index >= len(self.steps):
                raise NativeProcessError("Unanswered or uncorrelated operator request")
            step = self.steps[self.index]
            if step["action"] == "operator_answer":
                return self.await_operator(step, message)
            if step["action"] != "answer" or params.get("questions") != step["questions"]:
                raise NativeProcessError("Question differs from frozen operator policy")
            answer = {"id": identity, "result": {"answers": step["answers"]}}
            self.reserve(step["unit_id"], answer)
            self.requests.add(identity)
            self.index += 1
            return [answer]
        if "id" in message:
            raise NativeProcessError("Unapproved server request")
        if self.state in {"running", "operator-wait"} and method in {"item/started", "item/completed", "item/agentMessage/delta", "item/reasoning/textDelta", "item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded", "thread/tokenUsage/updated", "item/commandExecution/outputDelta"} and params.get("turnId", self.turn) == self.turn:
            if method == "item/completed" and params.get("item", {}).get("type") == "agentMessage":
                item = params["item"]
                if not isinstance(item.get("text"), str) or not item["text"]:
                    raise NativeProcessError("Completed agent message is malformed")
                self.agent_messages.append(item)
            return []
        raise NativeProcessError("Unsupported app-server notification")

    def feed(self, raw):
        self.buffer += raw
        outgoing = []
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            if len(line) > LIMIT:
                raise NativeProcessError("App-server line exceeds bound")
            message = _json(line)
            self.record("server", message)
            outgoing.extend(self.receive(message))
        if len(self.buffer) > LIMIT:
            raise NativeProcessError("App-server line exceeds bound")
        return outgoing


def _collect(command, prompt, deadline, elapsed_seconds, output_limit, stdout_file, stderr_file, interactive=None):
    """Owned one-shot collection. Native callers additionally require PID isolation."""
    _number(deadline, "deadline")
    if type(output_limit) is not int or not 1 <= output_limit <= OUTPUT_LIMIT:
        raise NativeProcessError("Invalid output bound")
    started = _number(elapsed_seconds(), "elapsed_seconds")
    result = {"status": "LAUNCH_FAILED", "exit_code": None, "leader_reaped": False, "group_absent": False,
              "pid": None, "pid_start_time_ticks": None, "session_id": None,
              "stdout_complete": False, "stderr_complete": False, "output_limit_exceeded": False,
              "started_elapsed_seconds": started, "finished_elapsed_seconds": started,
              "deadline_elapsed_seconds": deadline, "issues": []}
    if started >= deadline:
        result.update(status="TIMED_OUT", leader_reaped=True, group_absent=True)
        result["issues"].append("Original reservation deadline reached before spawn")
        return result
    if interactive is not None:
        first = interactive.start()
        interactive.record("client", first)
        prompt = canonical_json(first) + b"\n"
    process, last_elapsed = None, started
    cancellation, handlers = [], {}
    def cancelled(signum, frame):
        if not cancellation:
            cancellation.append(signum)
    buffers = {"stdout": stdout_file, "stderr": stderr_file}
    overflowed = set()
    counts = {"stdout": 0, "stderr": 0}
    local_deadline = time.monotonic() + deadline - started
    try:
        for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, cancelled)
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, cwd="/",
                                   start_new_session=True, close_fds=True)
        result["status"] = "COULD_NOT_RUN"
        result.update(pid=process.pid, session_id=os.getsid(process.pid),
                      pid_start_time_ticks=int(Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()[19]))
        if result["session_id"] != process.pid:
            raise NativeProcessError("Spawned process is not the owned session leader")
        with selectors.DefaultSelector() as selector:
            for name in ("stdout", "stderr"):
                stream = getattr(process, name)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            os.set_blocking(process.stdin.fileno(), False)
            if prompt:
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                process.stdin.close()
            offset, terminal_at, stop_reason = 0, None, None
            while selector.get_map() or not _exited(process):
                if cancellation:
                    stop_reason = "CANCELLED"
                    break
                now = _number(elapsed_seconds(), "elapsed_seconds")
                if now < last_elapsed:
                    raise NativeProcessError("Original campaign clock moved backwards")
                last_elapsed = now
                if now >= deadline or time.monotonic() >= local_deadline:
                    stop_reason = "TIMED_OUT"
                    break
                if interactive is not None:
                    for message in interactive.poll():
                        interactive.record("client", message)
                        prompt += canonical_json(message) + b"\n"
                    if prompt:
                        try:
                            selector.get_key(process.stdin)
                        except KeyError:
                            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
                if _exited(process):
                    terminal_at = terminal_at or time.monotonic()
                    if time.monotonic() - terminal_at >= 0.5:
                        stop_reason = "COULD_NOT_RUN"
                        result["issues"].append("A child retained output after leader termination")
                        break
                for key, _ in selector.select(0.02):
                    if key.data == "stdin":
                        try:
                            written = os.write(key.fd, prompt[offset:offset + 65536])
                            offset += written
                        except BrokenPipeError:
                            if interactive is not None:
                                raise NativeProcessError("Client closed stdin before complete interactive delivery")
                            offset = len(prompt)
                            result["issues"].append("Client closed stdin before complete prompt delivery")
                        except BlockingIOError:
                            continue
                        if offset == len(prompt):
                            if interactive is not None:
                                interactive.sent(prompt)
                            selector.unregister(key.fileobj)
                            if interactive is None:
                                key.fileobj.close()
                            prompt, offset = b"", 0
                        continue
                    try:
                        data = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    if not data:
                        result[key.data + "_complete"] = True
                        selector.unregister(key.fileobj)
                        continue
                    room = output_limit - counts[key.data]
                    buffers[key.data].write(data[:room])
                    counts[key.data] += min(room, len(data))
                    if interactive is not None and key.data == "stdout" and len(data) <= room:
                        for message in interactive.feed(data):
                            interactive.record("client", message)
                            prompt += canonical_json(message) + b"\n"
                        if prompt and process.stdin not in selector.get_map():
                            try:
                                selector.get_key(process.stdin)
                            except KeyError:
                                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
                        if interactive.done:
                            stop_reason = "INTERACTIVE_COMPLETED"
                            break
                    if len(data) > room:
                        result["output_limit_exceeded"] = True
                        overflowed.add(key.data)
                        stop_reason = "OUTPUT_LIMIT"
                        break
                if stop_reason:
                    break
            result["status"] = stop_reason or "EXITED"
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        result["issues"].append(str(error))
    except (KeyboardInterrupt, SystemExit):
        result["status"] = "CANCELLED"
        result["issues"].append("Collector was interrupted")
    finally:
        if process is not None:
            try:
                _exited(process)
                # Always terminate the owned namespace/group tail, including a
                # leader that already exited. Never signal after reaping it.
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    if _group_exists(process.pid):
                        try:
                            os.killpg(process.pid, sig)
                        except ProcessLookupError:
                            pass
                    if sig == signal.SIGTERM:
                        time.sleep(0.05)
                process.wait(timeout=3)
                result.update(exit_code=process.returncode, leader_reaped=True,
                              group_absent=not _group_exists(process.pid))
                # Drain only to the finite capture bounds, after scoped teardown.
                for name in ("stdout", "stderr"):
                    stream = getattr(process, name)
                    end = time.monotonic() + 0.3
                    while not result[name + "_complete"] and time.monotonic() < end:
                        try:
                            data = os.read(stream.fileno(), 65536)
                        except BlockingIOError:
                            time.sleep(0.005)
                            continue
                        if not data:
                            result[name + "_complete"] = True
                            break
                        room = output_limit - counts[name]
                        buffers[name].write(data[:room])
                        counts[name] += min(room, len(data))
                        if len(data) > room:
                            result["output_limit_exceeded"] = True
                            overflowed.add(name)
                            break
            except (OSError, subprocess.SubprocessError) as error:
                result["issues"].append("Owned teardown unverified: " + str(error))
            finally:
                for name in ("stdin", "stdout", "stderr"):
                    getattr(process, name).close()
        else:
            result.update(leader_reaped=True, group_absent=True)
        try:
            finished = _number(elapsed_seconds(), "elapsed_seconds")
            if finished < last_elapsed:
                raise NativeProcessError("Original campaign clock moved backwards")
            result["finished_elapsed_seconds"] = finished
        except (ValueError, OSError) as error:
            result["issues"].append(str(error))
            result["status"] = "COULD_NOT_RUN"
        if result["output_limit_exceeded"]:
            result["status"] = "OUTPUT_LIMIT"
            for name in overflowed:
                result[name + "_complete"] = False
        elif result["status"] == "EXITED" and (result["exit_code"] != 0 or result["issues"]):
            result["status"] = "COULD_NOT_RUN"
        if result["status"] == "EXITED" and not all(result[key] for key in
                ("leader_reaped", "group_absent", "stdout_complete", "stderr_complete")):
            result["status"] = "COULD_NOT_RUN"
        if interactive is not None and result["status"] == "INTERACTIVE_COMPLETED":
            result["protocol_completed_before_owned_shutdown"] = True
            result["status"] = "EXITED" if all(result[k] for k in ("leader_reaped", "group_absent", "stdout_complete", "stderr_complete")) and not result["issues"] else "COULD_NOT_RUN"
        if cancellation:
            result["status"] = "CANCELLED"
            result["issues"].append("Collector cancellation: " + signal.Signals(cancellation[0]).name)
        for signum, previous in handlers.items():
            signal.signal(signum, previous)
    return result


def _new(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def _authority(root, *, create=False):
    root = _path(root)
    if create:
        try:
            root.mkdir(mode=0o700)
        except FileExistsError:
            pass
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise NativeProcessError("Collector authority must be an owned private directory")
    key_path = root / "collector.key"
    if create and not key_path.exists():
        try:
            _new(key_path, os.urandom(32))
        except FileExistsError:
            pass
    info = key_path.lstat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise NativeProcessError("Collector key must be host-private")
    key = _read(key_path, 32)
    if len(key) != 32:
        raise NativeProcessError("Collector key is invalid")
    return root, key


def _fresh(request):
    identities = []
    for ref in request["pins"] + ([request["answer_policy_pin"]] if "answer_policy_pin" in request else []):
        _pin(ref, read=False)
        identities.append({"path": ref["path"], "identity": list(_identity(Path(ref["path"]).lstat()))})
    for ref in request["fixture_git_dirs"]:
        if tree_digest(ref["path"]) != ref["sha256"]:
            raise NativeProcessError("Fixture Git source changed")
    for ref in request.get("immutable_discovery_dirs", []):
        if tree_digest(ref["path"]) != ref["sha256"]:
            raise NativeProcessError("Immutable discovery inventory changed")
    return {"status": "INTACT", "inputs_sha256": digest(canonical_json(request["pins"])),
            "identities_sha256": digest(canonical_json(identities))}


def _managed_start(request, evidence_dir):
    selected = request["managed_worker"]
    if selected is None:
        return None
    # Import lazily; deterministic collection does not instantiate a workflow.
    try:
        from . import supervisor, workflow_runtime
    except ImportError:
        import supervisor
        import workflow_runtime
    session_raw = _pin(selected["session"])
    session = _json(session_raw)
    engine = workflow_runtime.engine(session)
    contract, _, project = workflow_runtime.load_delivery(Path(session["delivery_contract"]))
    if str(project) != request["workspace"]:
        raise NativeProcessError("Managed worker project changed")
    state = _path(selected["state"])
    started = engine.start(Path(selected["session"]["path"]), state)
    if started.get("status") != "ACTIVE":
        raise NativeProcessError("Managed worker initialization failed: " + str(started.get("issues", [])))
    evidence_dir.mkdir(mode=0o700)
    broker = supervisor.Broker.__new__(supervisor.Broker)
    try:
        supervisor.Broker.__init__(broker, state, session, contract, digest(session_raw), evidence_dir,
                                   completion_mode="managed-session")
        broker.thread.start()
    except BaseException:
        if hasattr(broker, "thread"):
            broker.close()
        else:
            if hasattr(broker, "server"):
                broker.server.close()
            if hasattr(broker, "socket_root"):
                broker.socket_path.unlink(missing_ok=True)
                (broker.socket_root / "scratch").rmdir()
                broker.socket_root.rmdir()
        raise
    return broker


def _managed_finish(broker, evidence_dir):
    result = {"required": broker is not None, "status": "NOT_APPLICABLE", "task_id": None,
              "broker_quiescent": True, "callbacks": 0, "callback_origin": "NOT_AUTHENTICATED",
              "task_result": None, "evidence": []}
    if broker is None:
        return result
    result.update(status="COULD_NOT_RUN", task_id=broker.session["task_id"], callbacks=broker.count)
    try:
        result["broker_quiescent"] = broker.close()
        if not result["broker_quiescent"] or broker.error or broker.interrupted:
            return result
        context = broker.engine.context(broker.state)
        result["status"] = context.get("status", "COULD_NOT_RUN")
        # Completion must already have been committed by a qualifying callback.
        # Merely READY does not create a terminal receipt here.
        if result["status"] == "COMPLETED":
            if broker.task_result is None:
                result["status"] = "COULD_NOT_RUN"
            else:
                current = broker.engine.complete(broker.state)
                keys = ("receipt_path", "receipt_sha256", "task_id")
                if (current.get("status") != "COMPLETED" or current.get("receipt_verified") is not True
                        or any(current.get(key) != broker.task_result.get(key) for key in keys)):
                    result["status"] = "COULD_NOT_RUN"
                else:
                    result["task_result"] = current
    except Exception:
        result["status"] = "COULD_NOT_RUN"
        result["broker_quiescent"] = not broker.thread.is_alive()
    finally:
        # Bound exact broker-created observations, including callback chronology
        # and socket-write records, while preserving their weaker provenance.
        try:
            names, observations = [], []
            for path in sorted(evidence_dir.iterdir()):
                raw = _read(path)
                result["evidence"].append({"path": str(path), "sha256": digest(raw)})
                if re.fullmatch(r"event-\d{6}\.json", path.name):
                    observation = _json(raw)
                    observations.append(observation)
                    names.append(observation["event"])
            expected_prompts = getattr(broker, "native_sent_prompts", None)
            expected_count = 1 if expected_prompts is None else len(expected_prompts)
            if result["status"] == "COMPLETED" and (
                    expected_count < 1 or names.count("UserPromptSubmit") != expected_count
                    or any(names.count(name) != 1 for name in ("SessionStart", "SessionEnd"))
                    or not names or names[0] != "SessionStart" or names[-1] != "SessionEnd" or "Stop" not in names
                    or names.index("UserPromptSubmit") > names.index("Stop")):
                result["status"] = "COULD_NOT_RUN"
            if result["status"] == "COMPLETED" and expected_prompts is not None:
                prompt_indices = [i for i, name in enumerate(names) if name == "UserPromptSubmit"]
                for ordinal, (index, prompt) in enumerate(zip(prompt_indices, expected_prompts)):
                    event = observations[index]
                    stop = next((j for j in range(index + 1, len(names)) if names[j] in {"Stop", "UserPromptSubmit", "SessionEnd"}), None)
                    if (event.get("prompt_sha256") != digest(prompt.encode("utf-8"))
                            or stop is None or names[stop] != "Stop"):
                        result["status"] = "COULD_NOT_RUN"
                        break
                    if ordinal == 0:
                        valid = index == 1 and event.get("before_status") == "ACTIVE" and event["status"] == "ACTIVE"
                    else:
                        previous = observations[index - 1]
                        valid = (previous["event"] == "Stop" and previous["status"] == "WAITING_USER"
                                 and event.get("before_status") == "WAITING_USER" and event["status"] == "ACTIVE"
                                 and previous["phase"] == event.get("before_phase") == event["phase"])
                    if not valid:
                        result["status"] = "COULD_NOT_RUN"
                        break
        except (OSError, ValueError):
            result["status"] = "COULD_NOT_RUN"
    return result


def launch(authority_root, request, *, check_reservation, elapsed_seconds):
    """Launch once after protected reservation validation; return a signed receipt.

    A durable exclusive request file prevents retries, including after a crash.
    A crash without a receipt remains unresolved; never rerun its reservation.
    """
    request = _json(canonical_json(request))
    check_reservation(request)
    if (request["binding"]["client"] != CLIENT or request["binding"]["model"] != MODEL
            or digest(canonical_json(request["command"])) != request["binding"]["command_sha256"]):
        raise NativeProcessError("Native launch request does not match selected command/client")
    root = _path(authority_root)
    mounts = [Path(request[key]) for key in ("workspace", "profile")]
    mounts += [Path(ref["path"]) for ref in request["readonly_inputs"] + request["fixture_git_dirs"]]
    mounts += [Path(ref["path"]) for ref in request.get("immutable_discovery_dirs", [])]
    mounts += [Path(name).resolve() for name in request["system_mounts"]]
    if any(_overlap(root, path) for path in mounts):
        raise NativeProcessError("Collector authority overlaps worker-visible mounts")
    initial_freshness = _fresh(request)
    root, key = _authority(root, create=True)
    attempt_dir = root / digest(canonical_json(request["binding"]))
    attempt_dir.mkdir(mode=0o700)  # Exclusive reservation; no retries after partial writes.
    _new(attempt_dir / "request.json", canonical_json(request))
    broker = None
    interactive = None
    operator = None
    transcript = None
    command_digest = None
    managed_evidence = attempt_dir / "managed"
    stdout_path, stderr_path = attempt_dir / "stdout.bin", attempt_dir / "stderr.bin"
    with stdout_path.open("xb") as out, stderr_path.open("xb") as err:
        os.chmod(stdout_path, 0o600)
        os.chmod(stderr_path, 0o600)
        try:
            broker = _managed_start(request, managed_evidence)
            command = sandbox_command(request, broker)
            command_digest = digest(canonical_json(command))
            check_reservation(request)  # Current protected claim immediately before spawn.
            if "answer_policy" in request:
                transcript = (attempt_dir / "transcript.jsonl").open("xb")
                os.chmod(attempt_dir / "transcript.jsonl", 0o600)
                def reserve_unit(unit, message):
                    check_reservation(request)
                    now = _number(elapsed_seconds(), "continuation elapsed")
                    if now >= request["deadline"]:
                        raise NativeProcessError("Original continuation deadline expired")
                    allowed = ["initial"] + answer_policy(request["answer_policy"])
                    if unit not in allowed:
                        raise NativeProcessError("Unallocated continuation")
                    _new(attempt_dir / ("unit-" + unit + ".json"), canonical_json({"unit_id": unit, "binding": request["binding"], "elapsed_seconds": now, "message": message}))
                operator = OperatorInbox(attempt_dir, request["binding"], request["answer_policy"])
                interactive = Interactive(request["answer_policy"], _pin(request["prompt"]), request["workspace"], reserve_unit, transcript, operator=operator)
                if broker is not None:
                    broker.native_sent_prompts = interactive.sent_prompts
            process = _collect(command, _pin(request["prompt"]), request["deadline"], elapsed_seconds,
                               request["output_limit"], out, err, interactive=interactive)
        except Exception as error:
            now = _number(elapsed_seconds(), "elapsed_seconds")
            process = {"status": "LAUNCH_FAILED", "exit_code": None, "leader_reaped": True,
                       "pid": None, "pid_start_time_ticks": None, "session_id": None,
                       "group_absent": True, "stdout_complete": False, "stderr_complete": False,
                       "output_limit_exceeded": False, "started_elapsed_seconds": now,
                       "finished_elapsed_seconds": now, "deadline_elapsed_seconds": request["deadline"],
                       "issues": ["Managed/sandbox/prelaunch setup failed: " + str(error)]}
        finally:
            if transcript is not None:
                transcript.close()
            managed = _managed_finish(broker, managed_evidence)
            if request["managed_worker"] is not None and broker is None:
                managed.update(required=True, status="COULD_NOT_RUN")
        for stream in (out, err):
            stream.flush()
            os.fsync(stream.fileno())
    if not managed["broker_quiescent"]:
        process["status"] = "COULD_NOT_RUN"
        process["issues"].append("Managed callback broker did not become quiescent")
    try:
        freshness = _fresh(request)
        if freshness != initial_freshness:
            raise NativeProcessError("Selected input identity changed during the owned process")
        check_reservation(request)
    except Exception as error:
        freshness = {"status": "CONTAMINATED", "inputs_sha256": None, "identities_sha256": None}
        process["issues"].append("Postrun freshness/reservation check failed: " + str(error))
        process["status"] = "COULD_NOT_RUN"
    raw_out, raw_err = _read(stdout_path, OUTPUT_LIMIT), _read(stderr_path, OUTPUT_LIMIT)
    body = {"schema_version": "devforge.native-process-receipt/v1", "provenance": "OWNED_NATIVE_COLLECTOR",
            "binding": request["binding"], "process": process, "freshness": freshness,
            "events": observe_jsonl(raw_out, complete=process["stdout_complete"] and not process["output_limit_exceeded"]),
            "stdout": {"path": str(stdout_path), "sha256": digest(raw_out), "bytes": len(raw_out)},
            "stderr": {"path": str(stderr_path), "sha256": digest(raw_err), "bytes": len(raw_err)},
            "request_sha256": digest(canonical_json(request)), "campaign_origin_utc": request["campaign_origin_utc"],
            "launch_command_sha256": command_digest, "collector_sha256": _file_digest(Path(__file__).resolve()),
            "managed_worker": managed,
            "semantic_grade": "NOT_EVALUATED", "native_callback_authentication": "NOT_EVALUATED"}
    if "answer_policy" in request:
        path = attempt_dir / "transcript.jsonl"
        if not path.exists():
            _new(path, b"")
        raw = _read(path, OUTPUT_LIMIT)
        body["interactive"] = {"transcript": {"path": str(path), "sha256": digest(raw)},
            "units": [{"path": str(path), "sha256": digest(_read(path))} for path in sorted(attempt_dir.glob("unit-*.json"))]}
        body["interactive"]["operators"] = operator.records if operator is not None else []
        body["events"] = observe_interactive(request, raw, raw_out, body["interactive"]["units"], process, operators=operator.records if operator is not None else [])
    receipt = {"body": body, "hmac_sha256": hmac.new(key, canonical_json(body), hashlib.sha256).hexdigest()}
    path = attempt_dir / "receipt.json"
    _new(path, canonical_json(receipt))
    verify_receipt(root, path, request["binding"])
    return path


def verify_receipt(authority_root, receipt_path, expected):
    """Authenticate host process provenance and exact raw bytes, never semantics."""
    root, key = _authority(authority_root)
    path = _path(receipt_path)
    expected_parent = root / digest(canonical_json(expected))
    if path != expected_parent / "receipt.json":
        raise NativeProcessError("Receipt is outside its exact reservation authority")
    envelope = _json(_read(path))
    _exact(envelope, {"body", "hmac_sha256"}, "receipt envelope")
    body = envelope["body"]
    if (not isinstance(envelope["hmac_sha256"], str)
            or not hmac.compare_digest(envelope["hmac_sha256"], hmac.new(key, canonical_json(body), hashlib.sha256).hexdigest())):
        raise NativeProcessError("Receipt authentication failed")
    if (body.get("schema_version") != "devforge.native-process-receipt/v1"
            or body.get("provenance") != "OWNED_NATIVE_COLLECTOR" or body.get("binding") != expected
            or body.get("semantic_grade") != "NOT_EVALUATED"
            or body.get("native_callback_authentication") != "NOT_EVALUATED"):
        raise NativeProcessError("Receipt authority/schema/binding differs")
    raw_request = _read(expected_parent / "request.json")
    if digest(raw_request) != body["request_sha256"]:
        raise NativeProcessError("Request custody differs")
    request = _json(raw_request)
    if request["binding"] != expected:
        raise NativeProcessError("Request reservation differs")
    for name in ("stdout", "stderr"):
        ref = body[name]
        if ref["path"] != str(expected_parent / (name + ".bin")):
            raise NativeProcessError("Stream evidence path differs")
        raw = _read(ref["path"], OUTPUT_LIMIT)
        if len(raw) != ref["bytes"] or digest(raw) != ref["sha256"]:
            raise NativeProcessError("Stream evidence changed")
        if name == "stdout" and "answer_policy" not in request:
            events = observe_jsonl(raw, complete=body["process"]["stdout_complete"]
                                  and not body["process"]["output_limit_exceeded"])
            if body["events"] != events:
                raise NativeProcessError("Event observation differs from raw stream")
    if "answer_policy" in request:
        evidence = body.get("interactive", {})
        ref = evidence.get("transcript", {})
        if ref.get("path") != str(expected_parent / "transcript.jsonl"):
            raise NativeProcessError("Interactive transcript authority mismatch")
        transcript_raw = _read(ref["path"], OUTPUT_LIMIT)
        if digest(transcript_raw) != ref.get("sha256"):
            raise NativeProcessError("Interactive transcript digest mismatch")
        for unit in evidence.get("units", []):
            if Path(unit["path"]).parent != expected_parent:
                raise NativeProcessError("Continuation authority mismatch")
            _pin(unit)
        for record in evidence.get("operators", []):
            for name in ("request", "decision", "accepted"):
                ref = record[name]
                if ref is not None:
                    if Path(ref["path"]).parent != expected_parent:
                        raise NativeProcessError("Operator evidence authority mismatch")
                    _pin(ref)
        events = observe_interactive(request, transcript_raw, _read(body["stdout"]["path"], OUTPUT_LIMIT), evidence["units"], body["process"], operators=evidence.get("operators", []))
        if events != body["events"]:
            raise NativeProcessError("Interactive replay differs")
    for ref in body["managed_worker"]["evidence"]:
        if Path(ref["path"]).parent != expected_parent / "managed":
            raise NativeProcessError("Managed observation lies outside collector authority")
        _pin(ref)
    return body


def run_fixture(command, *, prompt=b"", timeout=1.0, output_limit=LIMIT):
    """Trusted deterministic subprocess test only; unsigned, never native-valid.

    Fixture commands must not escape their owned process group. Native execution
    uses the additional mandatory PID namespace. This helper accepts no authority
    root, receipt key, model configuration, or protected reservation.
    """
    import io
    _number(timeout, "fixture timeout")
    if not 0 < timeout <= 10:
        raise NativeProcessError("Fixture timeout must be in (0,10]")
    origin, out, err = time.monotonic(), io.BytesIO(), io.BytesIO()
    process = _collect(command, prompt, timeout, lambda: time.monotonic() - origin, output_limit, out, err)
    return {"provenance": "FIXTURE_ONLY", "native_valid": False, "process": process,
            "stdout": out.getvalue(), "stderr": err.getvalue(),
            "events": observe_jsonl(out.getvalue(), complete=process["stdout_complete"]
                                    and not process["output_limit_exceeded"])}


def observe_interactive(request, transcript_raw, stdout_raw, units, process, *, operators=None):
    """Replay exact client/server bytes and durable generation claims."""
    import io
    result = {"status": "UNOBTAINABLE", "thread_started": False, "turn_started": False, "turn_completed": False, "turn_failed": False, "errors": 0, "issues": []}
    claims = {}
    try:
        for pin in units:
            claim = _json(_pin(pin))
            unit = claim["unit_id"]
            if unit in claims or claim["binding"] != request["binding"] or not request["reserved_at"] <= claim["elapsed_seconds"] < request["deadline"]:
                raise NativeProcessError("Invalid continuation claim")
            claims[unit] = claim
        def reserved(unit, message):
            claim = claims.pop(unit, None)
            if claim is None or claim["message"] != message:
                raise NativeProcessError("Missing authentic pre-send claim")
        operator = None
        if operators:
            parent = Path(operators[0]["request"]["path"]).parent
            operator = OperatorInbox(parent, request["binding"], request["answer_policy"], replay=operators)
        adapter = Interactive(request["answer_policy"], _pin(request["prompt"]), request["workspace"], reserved, io.BytesIO(), operator=operator)
        pending = [adapter.start()]
        server = []
        if not transcript_raw.endswith(b"\n"):
            raise NativeProcessError("Incomplete interactive transcript")
        for line in transcript_raw.splitlines():
            row = _json(line)
            _exact(row, {"direction", "message"}, "transcript row")
            if row["direction"] == "client":
                if not pending:
                    pending.extend(adapter.poll())
                if not pending or pending.pop(0) != row["message"]:
                    raise NativeProcessError("Interactive client sequence differs")
            elif row["direction"] == "server" and not pending:
                server.append(row["message"])
                pending.extend(adapter.receive(row["message"]))
            else:
                raise NativeProcessError("Interactive direction/order differs")
        if server != [_json(line) for line in stdout_raw.splitlines()] or not stdout_raw.endswith(b"\n"):
            raise NativeProcessError("Transcript differs from authentic stdout")
        if operator is not None and len(operator.used) != len(operators):
            raise NativeProcessError("Unconsumed operator evidence")
        if pending or claims or not adapter.done or not process["stdout_complete"] or process["output_limit_exceeded"]:
            raise NativeProcessError("Interactive completion unavailable")
        result.update(status="OBSERVED", thread_started=True, turn_started=True, turn_completed=True)
    except (ValueError, OSError, KeyError, TypeError) as error:
        result["issues"].append(str(error))
    return result
