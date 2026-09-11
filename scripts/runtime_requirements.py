"""Framework-owned package requirement readers; capability validation is the Rust CLI's.

Runtime probing and delivery-capability validation belong to the explicitly
selected DevForge executable (`devforge install probe-runtime`). Nothing here
re-checks or second-guesses that decision: there is no Python fallback.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import subprocess


REQUIRED_EVENTS = ["SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"]
REQUIREMENT_PATH = "hooks/runtime-requirements.json"
PROBE_SCHEMA = "devforge.runtime-probe/v1"
GUARD_REQUEST_SCHEMA = "devforge.validator-guard-request/v1"
GUARD_SCHEMA = "devforge.validator-guard/v1"
PROBE_DEADLINE = 30


def json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def strict_json(data):
    def nonfinite(value):
        raise ValueError(f"non-finite JSON value: {value}")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            nonfinite(value)
        return number

    return json.loads(data, object_pairs_hook=json_object, parse_constant=nonfinite, parse_float=finite_float)


def read_json(path):
    return strict_json(path.read_bytes())


def load_requirement(plugin, provider):
    path = plugin / REQUIREMENT_PATH
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError(f"symlink runtime requirement: {path}")
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"runtime requirement must be a regular file: {path}")
    expected = {
        "schema_version": "devforge.runtime-requirement/v1",
        "runtime": "devforge.delivery",
        "protocol": "devforge.delivery-runtime/v1",
        "provider": provider,
        "completion_mode": "managed-session",
        "required_events": REQUIRED_EVENTS,
    }
    data = read_json(path)
    if provider not in ("codex", "claude") or not isinstance(data, dict) or data != expected:
        raise ValueError(f"unsupported or malformed runtime requirement: {path}")
    return data


def validate_hook_groups(hooks, *, command_only):
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be an event-to-group-list object")
    for event, groups in hooks.items():
        if not isinstance(event, str) or not event.strip() or not isinstance(groups, list):
            raise ValueError("hook event needs a nonempty name and group list")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list) or not group["hooks"]:
                raise ValueError("hook group needs a nonempty hooks list")
            if "matcher" in group and not isinstance(group["matcher"], str):
                raise ValueError("hook matcher must be a string")
            for handler in group["hooks"]:
                if not isinstance(handler, dict) or not isinstance(handler.get("type"), str) or not handler["type"].strip():
                    raise ValueError("hook handler needs a nonempty type")
                if command_only and handler["type"] != "command":
                    raise ValueError("framework hook handlers must be command handlers")
                if handler["type"] == "command" and (not isinstance(handler.get("command"), str) or not handler["command"].strip()):
                    raise ValueError("command hook needs a nonempty command string")


def validate_delivery_hooks(source, provider):
    hooks = source["hooks"]
    if set(hooks) != set(REQUIRED_EVENTS):
        raise ValueError("delivery requirement needs exactly its four required hook events")
    expected_command = f'"${{DEVFORGE_DELIVERY_EXECUTABLE:-devforge}}" delivery hook --provider {provider}'
    for event in REQUIRED_EVENTS:
        groups = hooks[event]
        if len(groups) != 1 or len(groups[0]["hooks"]) != 1:
            raise ValueError(f"delivery requirement needs one command group and handler: {event}")
        group = groups[0]
        handler = group["hooks"][0]
        if group.get("matcher", "") != "":
            raise ValueError(f"delivery hook must select every {event} event")
        if handler["type"] != "command" or handler["command"] != expected_command:
            raise ValueError(f"incompatible delivery hook command: {event}")
        # Async and conditional handlers cannot supply synchronous completion evidence.
        if not set(group) <= {"matcher", "hooks"} or not set(handler) <= {"type", "command", "timeout"}:
            raise ValueError(f"unsupported delivery hook options: {event}")
        if "timeout" in handler and (type(handler["timeout"]) not in (int, float) or handler["timeout"] <= 0):
            raise ValueError(f"delivery hook timeout must be a positive number: {event}")


def load_plugin_hooks(plugin, provider):
    """Read the bounded framework hook source without running any handler."""
    if provider not in ("codex", "claude"):
        raise ValueError(f"unknown provider: {provider}")
    requirement = load_requirement(plugin, provider)
    manifest = plugin / f".{provider}-plugin/plugin.json"
    if manifest.is_symlink() or manifest.parent.is_symlink():
        raise ValueError(f"symlink hook manifest: {manifest}")
    data = read_json(manifest) if manifest.exists() else {}
    if not isinstance(data, dict):
        raise ValueError("plugin manifest must be an object")
    declared = "hooks" in data
    if declared and data["hooks"] not in ("hooks/hooks.json", "./hooks/hooks.json"):
        raise ValueError("framework hooks must select hooks/hooks.json")
    directory = plugin / "hooks"
    source = directory / "hooks.json"
    if directory.is_symlink() or source.is_symlink():
        raise ValueError(f"symlink hook source: {source}")
    if not declared and not directory.exists():
        return None
    if not directory.is_dir() or not source.is_file():
        raise ValueError(f"missing framework hook component: {source}")
    hooks = read_json(source)
    if not isinstance(hooks, dict) or not {"hooks"} <= set(hooks) <= {"hooks", "description"}:
        raise ValueError("hook source needs hooks and optional description only")
    if "description" in hooks and not isinstance(hooks["description"], str):
        raise ValueError("hook description must be a string")
    validate_hook_groups(hooks["hooks"], command_only=True)
    if requirement is not None:
        validate_delivery_hooks(hooks, provider)
    return hooks


def runtime_digest(runtime):
    path = Path(runtime)
    if not path.is_absolute():
        raise ValueError("--runtime must be an absolute executable path")
    if any(p.is_symlink() for p in (path, *path.parents)) or path.resolve(strict=True) != path:
        raise ValueError("--runtime must be canonical and have no symlink components")
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or not os.access(path, os.X_OK):
        raise ValueError("--runtime must select a regular executable file")
    if metadata.st_nlink != 1:
        raise ValueError("--runtime must have exactly one hard link")
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def probe_runtime(validator, runtime, providers, project):
    """Delegate probing and validation to the explicitly selected DevForge executable.

    The validator is chosen by the operator, never discovered from PATH, an
    environment default or the runtime under test. Its refusal is final: this
    module neither re-validates the reported capabilities nor installs anything
    when the probe fails. The already-resolved installation project is named so
    the compiled CLI can refuse a validating executable inside it; the returned
    report must carry that same project back.
    """
    if runtime is None:
        raise ValueError("delivery-aware project installation requires --runtime ABSOLUTE_PATH")
    command = [str(validator), "--project", str(project), "install", "probe-runtime", "--runtime", str(runtime)]
    command += [argument for provider in providers for argument in ("--provider", provider)]
    try:
        completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                   timeout=PROBE_DEADLINE)
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"runtime validation timed out after {PROBE_DEADLINE} seconds") from error
    if completed.returncode != 0:
        reason = f"runtime validation exited with status {completed.returncode}"
        try:
            report = strict_json(completed.stdout)
        except ValueError:
            report = None
        if isinstance(report, dict) and isinstance(report.get("reason"), str):
            reason = report["reason"]
        raise ValueError(reason)
    report = strict_json(completed.stdout)
    if not isinstance(report, dict) or report.get("schema_version") != PROBE_SCHEMA:
        raise ValueError(f"runtime validation did not report {PROBE_SCHEMA}")
    if report.get("project") != str(project):
        raise ValueError("runtime validation bound a different project")
    return report


def guard_validator(validator, project, report, write_paths):
    """Delegate the selected validator's pre-write protections to that executable.

    Whether an installation destination names the validating authority, aliases its
    inode, or whether its bytes still are the ones its probe report bound, is decided
    by the compiled CLI about itself. This module only carries the installation inputs
    in and the refusal out; it re-checks nothing and has no fallback.
    """
    request = {"schema_version": GUARD_REQUEST_SCHEMA, "report": report,
               "write_paths": list(write_paths)}
    command = [str(validator), "--project", str(project), "install", "guard-validator"]
    try:
        completed = subprocess.run(command, input=json.dumps(request, allow_nan=False).encode("utf-8"),
                                   capture_output=True, timeout=PROBE_DEADLINE)
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"runtime validation timed out after {PROBE_DEADLINE} seconds") from error
    if completed.returncode != 0:
        reason = f"runtime validation exited with status {completed.returncode}"
        try:
            refusal = strict_json(completed.stdout)
        except ValueError:
            refusal = None
        if isinstance(refusal, dict) and isinstance(refusal.get("reason"), str):
            reason = refusal["reason"]
        raise ValueError(reason)
    decision = strict_json(completed.stdout)
    if not isinstance(decision, dict) or decision.get("schema_version") != GUARD_SCHEMA:
        raise ValueError(f"validator guard did not report {GUARD_SCHEMA}")
    return decision
