"""Bounded native experiment binding, distinct from creating test workspaces.

This checks operator-produced evidence and exact files, not credential contents,
model execution authenticity or semantic grades. Never launches a process.
"""
from pathlib import Path
import hashlib
import os
import stat

try:
    from . import delivery_core as core, phase_state as store
except ImportError:
    import delivery_core as core
    import phase_state as store


def _pin(value, label):
    core._exact(value, {"path", "sha256"}, label)
    path = store._absolute(value["path"], label)
    core._digest(value["sha256"], label)
    raw = store._external(path, 8 * 1024 * 1024, label)
    if store._hash(raw) != value["sha256"]:
        core._fail(f"{label}: selected bytes changed")
    return path, raw


def _positive(value, maximum, label):
    if type(value) is not int or not 1 <= value <= maximum:
        core._fail(f"{label}: expected an integer in [1,{maximum}]")


def native_plan(raw, task_id):
    """Require an entire frozen experiment before any attempt admission."""
    plan = core._json(raw, "utility native experiment")
    core._exact(plan, {"schema_version", "task_id", "candidate", "baseline", "specification", "cases",
                       "runtime_configuration", "client", "model", "authentication", "repetitions",
                       "max_attempts", "max_seconds", "observation_methods", "boundary_evidence", "attempts"},
                "utility native experiment")
    if plan["schema_version"] != "devforge.utility-native-plan/v1" or plan["task_id"] != task_id:
        core._fail("native plan task/schema mismatch")
    fixed = []
    for name in ("candidate", "baseline", "specification", "cases", "runtime_configuration", "boundary_evidence"):
        path, data = _pin(plan[name], f"native plan {name}")
        fixed.append(("native_input", str(path), data))
    core._exact(plan["client"], {"path", "sha256", "version"}, "native client")
    core._text(plan["client"]["version"], "native client version")
    client = {key: plan["client"][key] for key in ("path", "sha256")}
    path = store._absolute(client["path"], "native client binary")
    client_path = path
    core._digest(client["sha256"], "native client digest")
    # Large client binaries are checked by streaming; never copied into evidence.
    with core._directory(path.parent, "native client parent") as parent:
        initial = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
            core._fail("native client must be a canonical regular single-link executable")
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if core._identity(before) != core._identity(initial) or before.st_size > 512 * 1024 * 1024:
                core._fail("native client identity changed or exceeds bounded size")
            checksum = hashlib.sha256()
            total = 0
            while block := os.read(fd, 65536):
                total += len(block)
                if total > before.st_size:
                    core._fail("native client grew during hashing")
                checksum.update(block)
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (total != before.st_size or core._identity(before) != core._identity(os.fstat(fd))
                    or core._identity(before) != core._identity(current) or checksum.hexdigest() != client["sha256"]):
                core._fail("native client selected bytes changed")
        finally:
            os.close(fd)
    core._text(plan["model"], "native model")
    if plan["model"].casefold() in {"unknown", "pending", "default", "tbd"}:
        core._fail("native model must be resolved before measurement")
    core._exact(plan["authentication"], {"kind", "arrangement_ref", "credential_copying"}, "native authentication")
    if plan["authentication"]["kind"] != "subscription" or plan["authentication"]["credential_copying"] is not False:
        core._fail("native authentication must use the selected subscription arrangement without credential copying")
    path, data = _pin(plan["authentication"]["arrangement_ref"], "authentication arrangement evidence")
    fixed.append(("native_auth_arrangement", str(path), data))
    _positive(plan["repetitions"], 100, "native repetitions")
    _positive(plan["max_attempts"], 256, "native attempt count")
    _positive(plan["max_seconds"], 86400, "native total time")
    methods = plan["observation_methods"]
    if not isinstance(methods, list) or not methods or len(methods) > 32:
        core._fail("native observation methods are missing/unbounded")
    for method in methods:
        core._text(method, "native observation method")
    attempts = plan["attempts"]
    if not isinstance(attempts, list) or not attempts or len(attempts) > plan["max_attempts"]:
        core._fail("native attempts exceed or omit their bounded allocation")
    identities, workspaces, clients = set(), [], []
    scopes = set()
    for attempt in attempts:
        core._exact(attempt, {"attempt_id", "case_id", "tier", "arm", "repetition", "workspace", "client_state", "max_seconds"}, "native attempt")
        identity = core._text(attempt["attempt_id"], "attempt ID")
        case = core._text(attempt["case_id"], "case ID")
        if identity in identities or attempt["tier"] not in {"C", "B", "A"} or attempt["arm"] not in {"candidate", "baseline"}:
            core._fail("native attempt identity/tier/arm invalid")
        identities.add(identity)
        _positive(attempt["repetition"], plan["repetitions"], "attempt repetition")
        scoped = (case, attempt["tier"], attempt["arm"], attempt["repetition"])
        if scoped in scopes:
            core._fail("duplicate native attempt scope")
        scopes.add(scoped)
        workspace = store._absolute(attempt["workspace"], "attempt workspace")
        private = store._absolute(attempt["client_state"], "attempt client state")
        with core._directory(workspace, "prepared attempt workspace"):
            pass
        with core._directory(private, "independent client state"):
            pass
        workspaces.append(workspace)
        clients.append(private)
        _positive(attempt["max_seconds"], plan["max_seconds"], "attempt time limit")
    roots = workspaces + clients
    if any(core._within(client_path, root) for root in roots):
        core._fail("native client binary must be outside every attempt-writable workspace/client root")
    for i, path in enumerate(roots):
        if any(store._collide(path, prior) for prior in roots[:i]):
            core._fail("independent native attempts cannot reuse or overlap writable workspace/client state")
    for _, path, _ in fixed:
        if any(core._within(Path(path), root) for root in roots):
            core._fail("frozen native plan inputs cannot be in an attempt-writable root")
    # Actual producer evidence must name every allocated attempt and all required boundaries.
    boundary = core._json(next(data for _, path, data in fixed if path == plan["boundary_evidence"]["path"]), "native boundary evidence")
    core._exact(boundary, {"schema_version", "task_id", "attempts"}, "native boundary evidence")
    if boundary["schema_version"] != "devforge.utility-boundary-observation/v1" or boundary["task_id"] != task_id:
        core._fail("native boundary evidence task/schema mismatch")
    if not isinstance(boundary["attempts"], list) or len(boundary["attempts"]) != len(attempts):
        core._fail("boundary evidence does not cover the native attempt allocation")
    covered = set()
    dimensions = {"filesystem", "source_visibility", "history_memory", "authentication", "process_ownership", "callbacks"}
    for observed in boundary["attempts"]:
        core._exact(observed, {"attempt_id", "workspace", "client_state", "observations"}, "boundary attempt")
        identity = observed["attempt_id"]
        if identity not in identities or identity in covered:
            core._fail("boundary evidence has wrong/duplicate attempt")
        covered.add(identity)
        selected = next(row for row in attempts if row["attempt_id"] == identity)
        if any(observed[key] != selected[key] for key in ("workspace", "client_state")):
            core._fail("observed boundary differs from allocated workspace/client state")
        core._exact(observed["observations"], dimensions, "native boundary dimensions")
        for dimension, observation in observed["observations"].items():
            core._exact(observation, {"outcome", "evidence"}, "native boundary observation")
            if observation["outcome"] != "PASS":
                core._fail(f"native {dimension} prerequisite is unobserved or failed")
            path, data = _pin(observation["evidence"], "raw native boundary evidence")
            if any(core._within(path, root) for root in roots):
                core._fail("boundary raw evidence must be protected from attempt writes")
            fixed.append(("native_boundary", str(path), data))
    return plan, fixed
