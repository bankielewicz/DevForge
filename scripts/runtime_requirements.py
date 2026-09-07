"""Framework-owned delivery compatibility checks, separate from native activation."""
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import stat
import subprocess
import time


REQUIRED_EVENTS = ["SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"]
REQUIREMENT_PATH = "hooks/runtime-requirements.json"
PROBE_TIMEOUT = 5
PROBE_OUTPUT_LIMIT = 1024 * 1024


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


def capability_output(runtime):
    """Bound elapsed time and combined output without a shell or PATH lookup."""
    deadline = time.monotonic() + PROBE_TIMEOUT
    output = bytearray()
    size = 0
    with subprocess.Popen([str(runtime), "delivery", "capabilities"], stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                selector.register(process.stderr, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError("runtime capabilities timed out after 5 seconds")
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fileobj.fileno(), min(65536, PROBE_OUTPUT_LIMIT + 1 - size))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        size += len(chunk)
                        if size > PROBE_OUTPUT_LIMIT:
                            raise ValueError("runtime capabilities output exceeds 1 MiB")
                        if key.fileobj is process.stdout:
                            output.extend(chunk)
            process.wait(timeout=max(0, deadline - time.monotonic()))
            if process.returncode != 0:
                raise ValueError(f"runtime capabilities exited with status {process.returncode}")
        except subprocess.TimeoutExpired as error:
            process.kill()
            raise ValueError("runtime capabilities timed out after 5 seconds") from error
        except BaseException:
            if process.poll() is None:
                process.kill()
            raise
    return bytes(output)


def validate_capabilities(data, providers):
    fields = {"schema_version", "protocol", "supported_providers", "completion_modes",
              "io_modes", "hook_events", "native_admission", "mechanical_scope"}
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError("malformed runtime capabilities fields")
    if (data["schema_version"] != "devforge.delivery-capabilities/v1"
            or data["protocol"] != "devforge.delivery-runtime/v1"
            or data["native_admission"] not in ("NOT_VALIDATED", "CONTRACT_REQUIRED")
            or not isinstance(data["mechanical_scope"], str) or not data["mechanical_scope"].strip()):
        raise ValueError("unsupported runtime capabilities contract")
    for field in ("supported_providers", "completion_modes", "io_modes", "hook_events"):
        values = data[field]
        if (not isinstance(values, list) or not values
                or not all(isinstance(value, str) and value for value in values)
                or len(values) != len(set(values))):
            raise ValueError(f"malformed runtime capability list: {field}")
    if (not set(providers) <= set(data["supported_providers"])
            or "managed-session" not in data["completion_modes"]
            or not set(REQUIRED_EVENTS) <= set(data["hook_events"])):
        raise ValueError("runtime capabilities do not satisfy the package requirement")


def probe_runtime(runtime, providers):
    if runtime is None:
        raise ValueError("delivery-aware project installation requires --runtime ABSOLUTE_PATH")
    before = runtime_digest(runtime)
    capabilities = strict_json(capability_output(runtime))
    validate_capabilities(capabilities, providers)
    after = runtime_digest(runtime)
    if before != after:
        raise ValueError("selected runtime binary changed during capability verification")
    return {"path": str(runtime), "sha256_before": before, "sha256_after": after,
            "capabilities": capabilities, "native_activation": "NOT_VERIFIED"}
