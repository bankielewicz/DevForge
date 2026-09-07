"""Durable mechanical brainstorm checkpoints for a trusted external supervisor.

The worker does not own this directory or choose successful state. HEAD.json is
the atomic commit point for a hash-bound journal. Published records and snapshots
are immutable; files in pending/ are uncommitted publication work and never grant
admission. Protection of these paths, callback provenance, worker lifetime and
semantic acceptance are the supervisor's separate responsibilities.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any

try:
    from . import delivery_core
except ImportError:  # Installed scripts and the independent suite use flat imports.
    import delivery_core


SESSION_LIMIT = CHECKPOINT_LIMIT = 64 * 1024
PREIMAGE_LIMIT = ASSIGNMENT_LIMIT = 8 * 1024 * 1024
INSTALLED_LIMIT = 64 * 1024 * 1024
STATE_JSON_LIMIT = 1024 * 1024
SCOPE = (
    "Mechanical phase evidence and delivery persistence only; no semantic "
    "quality, human adoption, native callback provenance or native completion "
    "certification."
)
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_NONCE = re.compile(r"[0-9a-f]{32}\Z")
_PLACEHOLDER = re.compile(r"\{\{[\s\S]*?\}\}")
_PHASES = {"brainstorm": ("Recover", "Explore", "Record", "Focus"),
           "handoff-only": ("Recover", "Focus")}
_SESSION_KEYS = {
    "schema_version", "task_id", "provider", "delivery_contract",
    "delivery_contract_sha256", "assignment", "installed_inputs",
    "checkpoint_path", "receipt_path", "deadline_utc",
    "max_corrections_per_phase", "output_baselines",
}
_MANIFEST_KEYS = {
    "schema_version", "state_root", "session_path", "session_sha256",
    "task_id", "project_root", "provider", "mode", "started_at_utc",
    "deadline_utc", "snapshots",
}
_RECORD_KEYS = {
    "schema_version", "sequence", "previous", "at_utc", "operation",
    "data", "snapshots",
}
_REF_KEYS = {"kind", "source", "path", "sha256", "bytes"}
_OPERATIONS = {"start", "accepted", "waiting_user", "resume", "correction",
               "completion_intent", "completed"}


def utc_now() -> datetime:
    """The only phase-state clock seam; callers cannot supply a clock value."""
    return datetime.now(timezone.utc)


def _fail(message: str) -> None:
    delivery_core._fail(message)


def _unavailable(message: str) -> None:
    delivery_core._operational(message)


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _dump(value: Any) -> bytes:
    raw = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False,
                      allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > STATE_JSON_LIMIT:
        _fail("protected state: serialized record exceeds its bounded size")
    return raw


def _exact(value: Any, keys: set[str], label: str) -> dict:
    return delivery_core._exact(value, keys, label)


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        _fail(f"{label}: expected a complete lowercase SHA-256")
    return value


def _nonce_value(value: Any, label: str = "challenge") -> str:
    if not isinstance(value, str) or not _NONCE.fullmatch(value):
        _fail(f"{label}: expected 32 lowercase hexadecimal characters")
    return value


def _absolute(value: Any, label: str) -> Path:
    try:
        value = os.fspath(value)
    except TypeError:
        _fail(f"{label}: expected an absolute canonical path")
    return delivery_core._absolute(value, label)


def _collide(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def _stamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(f"{label}: expected an aware ISO UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise ValueError("UTC required")
    except (ValueError, OverflowError):
        _fail(f"{label}: expected an aware ISO UTC timestamp")
    return parsed


def _now() -> datetime:
    try:
        value = utc_now()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("aware datetime required")
        if value.utcoffset() != timedelta(0):
            raise ValueError("UTC required")
        return value
    except (OSError, ValueError, TypeError, OverflowError, RuntimeError) as exc:
        _unavailable(f"UTC clock prerequisite unavailable: {type(exc).__name__}")


def _before_deadline(now: datetime, deadline: datetime) -> None:
    if now >= deadline:
        _unavailable("original deadline expired; no new phase or finalization is admitted")


def _read_at(fd: int, relative: str, limit: int, label: str,
             missing: str = "FAIL", optional: bool = False) -> bytes | None:
    """Bound arbitrary bytes using the core's no-follow directory walkers.

    Preimages and installed resources may be empty or binary. JSON/checkpoint
    and current artifact callers apply their own unchanged parser constraints.
    """
    descriptor = None
    absent = "_PHASE_ABSENT" if optional else missing
    try:
        with delivery_core._parent(fd, relative, label, absent) as (parent, name):
            initial = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
                _fail(f"{label}: expected a regular single-link file")
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW |
                                 os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                    delivery_core._identity(initial) != delivery_core._identity(before)):
                _fail(f"{label}: unsafe file or identity changed while opening")
            if before.st_size < 0 or before.st_size > limit:
                _fail(f"{label}: file exceeds {limit} bytes")
            parts, total = [], 0
            while total <= limit:
                part = os.read(descriptor, min(65536, limit + 1 - total))
                if not part:
                    break
                total += len(part)
                parts.append(part)
            raw = b"".join(parts)
            after = os.fstat(descriptor)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (delivery_core._identity(before) != delivery_core._identity(after) or
                    delivery_core._identity(after) != delivery_core._identity(current) or
                    len(raw) != before.st_size or len(raw) > limit):
                _fail(f"{label}: bytes or identity changed during readback")
            return raw
    except delivery_core._Problem as exc:
        if optional and exc.result == "_PHASE_ABSENT":
            return None
        raise
    except OSError as exc:
        if optional and exc.errno == errno.ENOENT:
            return None
        delivery_core._os_problem(exc, label, missing)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _external(path: Path, limit: int, label: str,
              missing: str = "COULD_NOT_RUN", optional: bool = False) -> bytes | None:
    absent = "_PHASE_ABSENT" if optional else missing
    try:
        with delivery_core._directory(path.parent, label, absent) as fd:
            return _read_at(fd, path.name, limit, label, missing, optional)
    except delivery_core._Problem as exc:
        if optional and exc.result == "_PHASE_ABSENT":
            return None
        raise


def _json_at(fd: int, path: str, label: str) -> tuple[dict, bytes]:
    raw = _read_at(fd, path, STATE_JSON_LIMIT, label)
    return delivery_core._json(raw, label), raw


def _core_result(result: dict) -> dict:
    if result.get("result") != "PASS":
        issues = result.get("issues") or ["required delivery-core check did not pass"]
        raise delivery_core._Problem(
            "COULD_NOT_RUN" if result.get("result") == "COULD_NOT_RUN" else "FAIL",
            "; ".join(str(item) for item in issues),
        )
    return result


def _safe_destination(path: Path) -> None:
    with delivery_core._directory(Path("/"), "destination root") as fd:
        delivery_core._inspect_destination(fd, str(path)[1:])


def _configuration(session_path: Path, state_root: Path, initial: bool) -> dict:
    raw = _external(session_path, SESSION_LIMIT, "session contract")
    session = _exact(delivery_core._json(raw, "session contract"), _SESSION_KEYS,
                     "session contract")
    if session["schema_version"] != "devforge.brainstorm-session/v1":
        _fail("session contract: unsupported schema_version")
    task_id = delivery_core._text(session["task_id"], "task_id")
    if session["provider"] not in ("codex", "claude"):
        _fail("session contract: provider must be codex or claude")
    if type(session["max_corrections_per_phase"]) is not int or session["max_corrections_per_phase"] != 1:
        _fail("max_corrections_per_phase must be integer 1, not boolean")
    deadline = _stamp(session["deadline_utc"], "deadline_utc")
    delivery_path = _absolute(session["delivery_contract"], "delivery_contract")
    delivery_digest = _digest(session["delivery_contract_sha256"], "delivery contract digest")
    delivery, delivery_raw, project = delivery_core._load_contract(delivery_path)
    if _hash(delivery_raw) != delivery_digest:
        _fail("delivery contract: selected SHA-256 mismatch")
    if task_id != delivery["task_id"]:
        _fail("session task_id does not match the selected delivery task")
    if delivery_core._within(session_path, project):
        _fail("session contract must be outside the writable project")

    assignment = _exact(session["assignment"], {"path", "sha256"}, "assignment")
    assignment_path = _absolute(assignment["path"], "assignment path")
    _digest(assignment["sha256"], "assignment digest")
    if delivery_core._within(assignment_path, project):
        _fail("assignment must be outside the writable project")
    assignment_raw = _external(assignment_path, ASSIGNMENT_LIMIT, "assignment")
    if _hash(assignment_raw) != assignment["sha256"]:
        _fail("assignment: selected SHA-256 mismatch")
    sources = [("session", str(session_path), raw),
               ("delivery", str(delivery_path), delivery_raw),
               ("assignment", str(assignment_path), assignment_raw)]
    installed = session["installed_inputs"]
    if not isinstance(installed, list):
        _fail("installed_inputs must be an array")
    installed_paths = set()
    for entry in installed:
        _exact(entry, {"path", "sha256"}, "installed input")
        path = _absolute(entry["path"], "installed input path")
        _digest(entry["sha256"], "installed input digest")
        if path in installed_paths:
            _fail("installed_inputs contains a duplicate path")
        installed_paths.add(path)
        value = _external(path, INSTALLED_LIMIT, "installed input")
        if _hash(value) != entry["sha256"]:
            _fail(f"installed input {path}: selected SHA-256 mismatch")
        sources.append(("installed", str(path), value))

    checkpoint = delivery_core._relative(session["checkpoint_path"], "checkpoint_path")
    receipt = _absolute(session["receipt_path"], "receipt_path")
    if delivery_core._within(receipt, project):
        _fail("receipt_path must be outside the writable project")
    outputs = {entry["path"]: entry for entry in delivery["outputs"]}
    baselines = session["output_baselines"]
    if not isinstance(baselines, list) or len(baselines) != len(outputs):
        _fail("output_baselines must select each delivery output exactly once")
    baseline_map, archives = {}, []
    for entry in baselines:
        _exact(entry, {"path", "sha256", "archive", "allow_unchanged"}, "output baseline")
        relative = delivery_core._relative(entry["path"], "baseline path")
        if relative not in outputs or relative in baseline_map:
            _fail("output_baselines contains an unselected or duplicate output")
        if type(entry["allow_unchanged"]) is not bool:
            _fail("output baseline allow_unchanged must be boolean")
        if entry["sha256"] is None:
            if entry["archive"] is not None or entry["allow_unchanged"]:
                _fail("an absent output needs archive null and allow_unchanged false")
        else:
            _digest(entry["sha256"], "output baseline digest")
            archive = delivery_core._relative(entry["archive"], "baseline archive")
            archives.append(archive)
        baseline_map[relative] = entry
    if set(baseline_map) != set(outputs):
        _fail("output baseline coverage does not match delivery outputs")

    input_paths = [entry["path"] for entry in delivery["inputs"]]
    selected = input_paths + list(outputs) + [checkpoint] + archives
    absolute_selected = [project / item for item in selected]
    for index, path in enumerate(absolute_selected):
        if any(_collide(path, other) for other in absolute_selected[:index]):
            _fail("input/output/checkpoint/archive paths collide")
    mutable = [project / item for item in list(outputs) + [checkpoint] + archives]
    if any(_collide(path, other) for path in installed_paths for other in mutable):
        _fail("installed input overlaps a mutable/checkpoint/archive destination")

    implementation = Path(__file__).absolute().parent
    reference_paths = delivery_core.catalog_paths(delivery)
    if delivery["schema_version"] == "devforge.delivery-task/v2":
        if not any(
                entry["kind"] == "artifact"
                and entry["identity"]["artifact_type"] == "session-record"
                and entry["source"]["kind"] == "fixed"
                and entry["source"]["physical_path"] == str(assignment_path)
                and entry["source"]["sha256"] == assignment["sha256"]
                for entry in delivery["reference_catalog"]):
            _fail("reference catalog must select the exact managed SESSION assignment path and bytes")
    for entry in delivery.get("reference_catalog", []):
        source = entry["source"]
        target = Path(source["physical_path"])
        if source["kind"] == "output-preimage":
            baseline = baseline_map.get(source["output_path"])
            if (baseline is None or baseline["archive"] is None or
                    source["sha256"] != baseline["sha256"] or
                    target != project / baseline["archive"]):
                _fail("reference preimage must match the selected output baseline and archive")
        elif any(_collide(target, other) for other in mutable):
            _fail("fixed reference source overlaps an output, checkpoint or archive destination")
        if _collide(target, implementation):
            _fail("reference source overlaps delivery implementation")
    protected = [project, session_path, delivery_path, assignment_path, implementation,
                 *installed_paths, *reference_paths]
    if any(_collide(state_root, path) for path in protected):
        _fail("state_root overlaps the project, selected code or fixed input")
    if any(_collide(receipt, path) for path in protected if path != project):
        _fail("receipt destination overlaps selected code or fixed input")
    # A receipt may be inside the protected state root, but not in its journal.
    if delivery_core._within(receipt, state_root):
        relative = receipt.relative_to(state_root)
        if not relative.parts or relative.parts[0] in {
                "LOCK", "MANIFEST.json", "HEAD.json", "records", "snapshots", "pending"}:
            _fail("receipt destination collides with protected state storage")
    elif _collide(receipt, state_root):
        _fail("receipt destination is an ancestor of state_root")
    with delivery_core._directory(state_root.parent, "state_root parent"):
        pass
    _safe_destination(receipt)
    if not delivery_core._within(receipt, state_root):
        with delivery_core._directory(receipt.parent, "receipt parent"):
            pass
    if initial and _external(receipt, delivery_core.RECEIPT_LIMIT, "receipt",
                             optional=True) is not None:
        _fail("receipt already exists at initialization")

    _core_result(delivery_core.prepare(delivery_path))

    preimages = {}
    with delivery_core._directory(project, "project_root") as fd:
        delivery_core._inspect_destination(fd, checkpoint)
        for entry in delivery["inputs"]:
            value = delivery_core._read_at(fd, entry["path"], delivery_core.INPUT_LIMIT,
                                           "fixed delivery input", "COULD_NOT_RUN")
            if _hash(value) != entry["sha256"]:
                _fail("fixed delivery input changed during preparation")
            sources.append(("input", str(project / entry["path"]), value))
        for entry in baselines:
            if initial:
                value = _read_at(fd, entry["path"], PREIMAGE_LIMIT,
                                 "output preimage", optional=True)
                if entry["sha256"] is None:
                    if value is not None:
                        _fail(f"output {entry['path']}: expected absent prelaunch destination")
                else:
                    if value is None or _hash(value) != entry["sha256"]:
                        _fail(f"output {entry['path']}: selected prelaunch digest mismatch")
                    preimages[entry["path"]] = value
            if entry["archive"] is not None:
                delivery_core._inspect_destination(fd, entry["archive"])
                value = _read_at(fd, entry["archive"], PREIMAGE_LIMIT,
                                 "preserved output archive", optional=initial)
                if value is not None and _hash(value) != entry["sha256"]:
                    _fail(f"archive {entry['archive']}: collision or changed preserved bytes")
    # Initial preimages are verified before publication; subsequent calls read
    # only the selected immutable archives. Both become protected snapshots.
    sources.extend(delivery_core.catalog_sources(
        delivery, preimages=preimages if initial else None))
    return {"session": session, "raw": raw, "delivery": delivery,
            "delivery_path": delivery_path, "project": project, "deadline": deadline,
            "receipt": receipt, "sources": sources, "preimages": preimages,
            "outputs": outputs, "baselines": baseline_map}


def _write(fd: int, raw: bytes) -> None:
    offset = 0
    while offset < len(raw):
        count = os.write(fd, raw[offset:])
        if count <= 0:
            _unavailable("incomplete protected publication")
        offset += count
    os.fsync(fd)


def _new_at(parent: int, name: str, raw: bytes) -> None:
    descriptor = None
    try:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        _write(descriptor, raw)
        os.fsync(parent)
    except FileExistsError:
        _fail(f"exclusive publication collision: {name}")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _mkdir_chain(root_fd: int, relative: str) -> None:
    fd = os.dup(root_fd)
    try:
        for part in relative.split("/") if relative else []:
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
                os.fsync(fd)
            except FileExistsError:
                pass
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY |
                                os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = following
    finally:
        os.close(fd)


@contextmanager
def _lock(root: Path):
    with delivery_core._directory(root, "protected state_root", "FAIL") as fd:
        descriptor = None
        try:
            before = os.stat("LOCK", dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size:
                _fail("protected state lock is not an empty regular single-link file")
            descriptor = os.open("LOCK", os.O_RDONLY | os.O_NOFOLLOW |
                                 os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
            if delivery_core._identity(before) != delivery_core._identity(os.fstat(descriptor)):
                _fail("protected lock identity changed")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                _unavailable("another protected phase operation owns the exclusive lock")
            if delivery_core._identity(before) != delivery_core._identity(
                    os.stat("LOCK", dir_fd=fd, follow_symlinks=False)):
                _fail("protected lock was replaced")
            yield fd
        except OSError as exc:
            delivery_core._os_problem(exc, "protected state lock", "FAIL")
        finally:
            if descriptor is not None:
                os.close(descriptor)


def _publish(fd: int, relative: str, raw: bytes, replace: bool = False) -> None:
    """Fsync bytes before an atomic rename; preserve interrupted work separately."""
    existing = _read_at(fd, relative, max(INSTALLED_LIMIT, len(raw)),
                        "protected publication target", optional=True)
    if existing is not None and not replace:
        if existing != raw:
            _fail("immutable protected publication collision")
        return
    pending_name = secrets.token_hex(16) + ".pending"
    with delivery_core._parent(fd, "pending/" + pending_name, "pending publication", "FAIL") as (parent, name):
        _new_at(parent, name, raw)
    with delivery_core._parent(fd, relative, "protected publication", "FAIL") as (parent, name):
        with delivery_core._parent(fd, "pending/unused", "pending publication", "FAIL") as (pending, _):
            os.rename(pending_name, name, src_dir_fd=pending, dst_dir_fd=parent)
            os.fsync(parent)
            os.fsync(pending)
        os.fsync(fd)


def _snapshot(fd: int, kind: str, source: str, raw: bytes) -> dict:
    digest = _hash(raw)
    relative = "snapshots/" + digest + ".bin"
    _publish(fd, relative, raw)
    return {"kind": kind, "source": source, "path": relative,
            "sha256": digest, "bytes": len(raw)}


def _validate_ref(ref: Any, blobs: dict[str, bytes]) -> dict:
    _exact(ref, _REF_KEYS, "snapshot reference")
    digest = _digest(ref["sha256"], "snapshot digest")
    if ref["path"] != "snapshots/" + digest + ".bin":
        _fail("snapshot locator does not match its digest")
    if type(ref["bytes"]) is not int or ref["bytes"] < 0:
        _fail("snapshot byte count must be a nonnegative integer")
    if not isinstance(ref["kind"], str) or not isinstance(ref["source"], str):
        _fail("snapshot kind/source must be strings")
    if digest not in blobs or len(blobs[digest]) != ref["bytes"]:
        _fail("protected snapshot is missing or its byte count changed")
    return ref


def _object_directory(fd: int, name: str, suffix: str, limit: int) -> dict[str, bytes]:
    with delivery_core._parent(fd, name + "/unused", name, "FAIL") as (parent, _):
        names = os.listdir(parent)
    result = {}
    for filename in names:
        if not isinstance(filename, str) or not filename.endswith(suffix):
            _fail(f"unexpected protected {name} entry")
        digest = filename[:-len(suffix)]
        _digest(digest, f"protected {name} filename")
        raw = _read_at(fd, name + "/" + filename, limit, f"protected {name} object")
        if _hash(raw) != digest:
            _fail(f"protected {name} object hash mismatch")
        result[digest] = raw
    return result


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 100:
        _fail(f"{label}: expected an array of at most 100 strings")
    return [_string(item, label) for item in value]


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label}: expected a nonempty string")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        _fail(f"{label}: invalid UTF-8 string")
    if size > 8192 or _PLACEHOLDER.search(value):
        _fail(f"{label}: oversized string or unresolved double-brace template marker")
    return value


def _checkpoint(raw: bytes, task: str, phase: str, challenge: str,
                outputs: dict[str, dict], primary_ledger: str | None = None) -> dict:
    if not raw or len(raw) > CHECKPOINT_LIMIT:
        _fail("checkpoint must contain 1 to 65536 bytes")
    value = _exact(delivery_core._json(raw, "checkpoint"), {
        "schema_version", "task_id", "phase", "challenge", "state", "content"
    }, "checkpoint")
    if value["schema_version"] != "devforge.brainstorm-checkpoint/v1":
        _fail("checkpoint: unsupported schema_version")
    if value["task_id"] != task or value["phase"] != phase or value["challenge"] != challenge:
        _fail("checkpoint task/phase/challenge mismatch or replay; current admission is required")
    content = value["content"]
    if value["state"] == "awaiting_user":
        _exact(content, {"question", "blocking_dependency"}, "awaiting_user content")
        for key in content:
            _string(content[key], key)
    elif value["state"] != "ready":
        _fail("checkpoint state must be ready or awaiting_user")
    elif phase == "Recover":
        _exact(content, {"known_ideas", "known_decisions", "missing_inputs"}, "Recover content")
        if not sum(len(_strings(content[key], key)) for key in content):
            _fail("Recover must record at least one known item or missing input")
    elif phase == "Explore":
        _exact(content, {"ideas", "missing_inputs"}, "Explore content")
        _strings(content["missing_inputs"], "missing_inputs")
        if not isinstance(content["ideas"], list) or not 1 <= len(content["ideas"]) <= 100:
            _fail("Explore ideas must contain 1 to 100 ideas")
        ids = set()
        for idea in content["ideas"]:
            _exact(idea, {"idea_id", "people", "problem", "outcome", "alternatives",
                          "open_questions"}, "idea")
            identity = _string(idea["idea_id"], "idea_id")
            if identity in ids:
                _fail("Explore contains a duplicate idea_id")
            ids.add(identity)
            _strings(idea["alternatives"], "alternatives")
            questions = _strings(idea["open_questions"], "open_questions")
            for key in ("people", "problem", "outcome"):
                if idea[key] is None:
                    if not questions:
                        _fail("unknown people/problem/outcome requires an open question")
                else:
                    _string(idea[key], key)
    elif phase == "Record":
        _exact(content, {"ledger_path"}, "Record content")
        expected = primary_ledger or next((path for path, spec in outputs.items()
                                           if spec["artifact_type"] == "idea-ledger"), None)
        if content["ledger_path"] != expected or expected is None:
            _fail("Record ledger_path must equal the selected idea-ledger output")
    elif phase == "Focus":
        _exact(content, {"next_action", "owner", "completion_evidence", "non_goals",
                         "handoff_path"}, "Focus content")
        for key in ("next_action", "owner", "completion_evidence"):
            _string(content[key], key)
        _strings(content["non_goals"], "non_goals")
        expected = next(path for path, spec in outputs.items() if spec["artifact_type"] == "handoff")
        if content["handoff_path"] != expected:
            _fail("Focus handoff_path must equal the selected handoff output")
    else:
        _fail("checkpoint phase is unsupported")
    return value


class _State:
    """A verified view, reconstructed for one locked operation only."""

    def __init__(self, root: Path, fd: int, info: dict):
        self.root, self.fd = root, fd
        manifest, raw = _json_at(fd, "MANIFEST.json", "protected manifest")
        self.manifest = _exact(manifest, _MANIFEST_KEYS, "protected manifest")
        if manifest["schema_version"] != "devforge.brainstorm-state/v1" or manifest["state_root"] != str(root):
            _fail("protected state schema/root binding mismatch")
        self.task = delivery_core._text(manifest["task_id"], "protected task_id")
        self.mode = manifest["mode"]
        if self.mode not in _PHASES or manifest["provider"] not in ("codex", "claude"):
            _fail("protected state mode/provider is invalid")
        self.phases = _PHASES[self.mode]
        self.started = _stamp(manifest["started_at_utc"], "protected start")
        self.deadline = _stamp(manifest["deadline_utc"], "protected deadline")
        if self.started >= self.deadline:
            _fail("protected initialization was not admitted before its deadline")
        _absolute(manifest["session_path"], "protected session path")
        _absolute(manifest["project_root"], "protected project root")
        _digest(manifest["session_sha256"], "protected session digest")
        head, _ = _json_at(fd, "HEAD.json", "protected HEAD")
        self.head = _exact(head, {"schema_version", "manifest_sha256", "records"}, "protected HEAD")
        if head["schema_version"] != "devforge.brainstorm-head/v1" or head["manifest_sha256"] != _hash(raw):
            _fail("protected manifest/HEAD binding mismatch")
        if not isinstance(head["records"], list) or not head["records"]:
            _fail("protected HEAD has no admitted journal")
        if len(head["records"]) != len(set(_digest(item, "journal digest") for item in head["records"])):
            _fail("protected HEAD repeats a record")
        self.blobs = _object_directory(fd, "snapshots", ".bin", INSTALLED_LIMIT)
        if not isinstance(manifest["snapshots"], list):
            _fail("protected manifest snapshots must be an array")
        for ref in manifest["snapshots"]:
            _validate_ref(ref, self.blobs)
        delivery_refs = [r for r in manifest["snapshots"] if r["kind"] == "delivery"]
        if len(delivery_refs) != 1:
            _fail("protected delivery snapshot is missing or ambiguous")
        delivery = delivery_core._json(self.blobs[delivery_refs[0]["sha256"]], "delivery snapshot")
        if not isinstance(delivery, dict) or not isinstance(delivery.get("outputs"), list):
            _fail("protected delivery snapshot has invalid output specifications")
        self.outputs = {item["path"]: item for item in delivery["outputs"]}
        self.reference_v2 = delivery.get("schema_version") == "devforge.delivery-task/v2"
        self.primary_ledger = delivery.get("primary_ledger_path")
        self.objects = _object_directory(fd, "records", ".json", STATE_JSON_LIMIT)
        self.records = {}
        for digest, record_raw in self.objects.items():
            record = _exact(delivery_core._json(record_raw, "protected journal record"),
                            _RECORD_KEYS, "protected journal record")
            if (record["schema_version"] != "devforge.brainstorm-operation/v1" or
                    record["operation"] not in _OPERATIONS or
                    type(record["sequence"]) is not int or record["sequence"] < 0 or
                    not isinstance(record["data"], dict) or not isinstance(record["snapshots"], list)):
                _fail("protected journal record has malformed fields")
            _stamp(record["at_utc"], "journal timestamp")
            if record["previous"] is not None:
                _digest(record["previous"], "prior journal digest")
            for ref in record["snapshots"]:
                _validate_ref(ref, self.blobs)
            self.records[digest] = record
        self.phase, self.status, self.challenge = "Recover", "ACTIVE", None
        self.corrections, self.issues = 0, []
        self.pending, self.focus_outputs, self.intent, self.receipt = None, None, None, None
        self.record_outputs, self.record_references, self.focus_references = None, None, None
        self.nonces = set()
        for index, digest in enumerate(head["records"]):
            if digest not in self.records:
                _fail("protected committed journal record is missing")
            record = self.records[digest]
            previous = None if index == 0 else head["records"][index - 1]
            if record["sequence"] != index or record["previous"] != previous:
                _fail("protected journal sequence/hash chain mismatch")
            self._apply(record, digest)
        info.update(task_id=self.task, phase=self.phase, phase_applicability=_applicability(self.mode))

    def _challenge(self, nonce: str) -> None:
        _nonce_value(nonce)
        if nonce in self.nonces:
            _fail("protected journal reused an admission challenge")
        self.nonces.add(nonce)
        self.challenge = nonce

    def _current(self, data: dict) -> None:
        if data.get("phase") != self.phase or data.get("challenge") != self.challenge:
            _fail("protected journal transition does not bind its current admission")

    def _checkpoint_ref(self, record: dict, expected_state: str) -> dict:
        refs = [item for item in record["snapshots"] if item["kind"] == "checkpoint"]
        if len(refs) != 1 or record["data"].get("checkpoint_sha256") != refs[0]["sha256"]:
            _fail("protected transition checkpoint binding is missing")
        checkpoint = _checkpoint(self.blobs[refs[0]["sha256"]], self.task, self.phase,
                                 self.challenge, self.outputs, self.primary_ledger)
        if checkpoint["state"] != expected_state:
            _fail("protected transition used the wrong checkpoint state")
        return checkpoint

    def _apply(self, record: dict, digest: str) -> None:
        operation, data = record["operation"], record["data"]
        stamp = _stamp(record["at_utc"], "journal timestamp")
        if stamp < self.started:
            _fail("journal timestamp precedes task initialization")
        if operation == "start":
            _exact(data, {"challenge"}, "start operation")
            if record["sequence"] != 0 or stamp >= self.deadline:
                _fail("start must be the first pre-deadline journal operation")
            self._challenge(data["challenge"])
            return
        if record["sequence"] == 0 or self.challenge is None and self.status == "ACTIVE":
            _fail("protected journal lacks initialization")
        if operation in {"accepted", "waiting_user", "correction", "resume"}:
            if self.status not in ({"ACTIVE", "WAITING_USER"} if operation == "resume" else {"ACTIVE"}):
                _fail("protected operation is not admitted in its prior state")
            self._current(data)
            if stamp >= self.deadline:
                _fail("journal admits a phase operation after the fixed deadline")
        if operation == "accepted":
            _exact(data, {"phase", "challenge", "checkpoint_sha256", "next_phase",
                          "next_challenge", "output_hashes"}, "accepted operation")
            self._checkpoint_ref(record, "ready")
            if not isinstance(data["output_hashes"], dict):
                _fail("accepted output hashes must be a mapping")
            expected = (set(self.outputs) if self.phase == "Focus" else
                        {p for p, s in self.outputs.items() if s["artifact_type"] == "idea-ledger"}
                        if self.phase == "Record" else set())
            actual = {ref["source"]: ref["sha256"] for ref in record["snapshots"] if ref["kind"] == "output"}
            if set(data["output_hashes"]) != expected or actual != data["output_hashes"]:
                _fail("accepted artifact snapshots/output hashes do not match phase coverage")
            reports = [ref for ref in record["snapshots"] if ref["kind"] == "reference_coverage"]
            if self.reference_v2 and self.phase in {"Record", "Focus"}:
                if len(reports) != 1 or reports[0]["source"] != self.phase:
                    _fail("accepted phase lacks exactly one selected reference coverage snapshot")
                report = _exact(delivery_core._json(self.blobs[reports[0]["sha256"]],
                                                    "reference coverage snapshot"),
                                {"reference_profile", "reference_coverage"}, "reference coverage snapshot")
                if (report["reference_profile"] != "devforge.reference-coverage/v1" or
                        not isinstance(report["reference_coverage"], dict)):
                    _fail("reference coverage snapshot has invalid profile or report")
                if self.phase == "Record":
                    self.record_outputs, self.record_references = actual, report
                else:
                    self.focus_references = report
                    if self.record_outputs is not None and any(
                            actual.get(path) != digest for path, digest in self.record_outputs.items()):
                        _fail("Focus changed an accepted Record ledger")
            elif reports:
                _fail("unexpected reference coverage snapshot for this contract or phase")
            position = self.phases.index(self.phase)
            self.issues, self.pending = [], None
            if position == len(self.phases) - 1:
                if data["next_phase"] != "Focus" or data["next_challenge"] is not None:
                    _fail("final phase cannot issue another admission")
                self.focus_outputs = data["output_hashes"]
                self.status, self.challenge = "READY", None
            else:
                if data["next_phase"] != self.phases[position + 1]:
                    _fail("protected transition skips a required phase")
                self.phase = data["next_phase"]
                self._challenge(data["next_challenge"])
                self.corrections = 0
        elif operation == "waiting_user":
            _exact(data, {"phase", "challenge", "checkpoint_sha256"}, "waiting_user operation")
            checkpoint = self._checkpoint_ref(record, "awaiting_user")
            self.pending = checkpoint["content"]
            self.status = "WAITING_USER"
        elif operation == "resume":
            _exact(data, {"phase", "challenge", "next_challenge"}, "resume operation")
            self._challenge(data["next_challenge"])
            self.pending, self.status = None, "ACTIVE"
        elif operation == "correction":
            _exact(data, {"phase", "challenge", "corrections", "issues", "terminal"}, "correction operation")
            if (type(data["corrections"]) is not int or data["corrections"] != self.corrections + 1 or
                    data["corrections"] > 2 or type(data["terminal"]) is not bool or
                    data["terminal"] != (data["corrections"] == 2)):
                _fail("protected correction counter/termination is malformed")
            self.issues = data["issues"]
            if (not isinstance(self.issues, list) or not 1 <= len(self.issues) <= 100 or
                    any(not isinstance(item, str) or not item.strip() or len(item.encode("utf-8")) > 32768
                        for item in self.issues)):
                _fail("correction record has no failure evidence")
            self.corrections = data["corrections"]
            if data["terminal"]:
                self.status = "FAIL"
        elif operation == "completion_intent":
            _exact(data, {"receipt_path", "output_hashes", "delivery_contract_sha256"}, "completion intent")
            if self.status != "READY" or self.intent is not None or stamp >= self.deadline:
                _fail("completion intent is not a single pre-deadline READY operation")
            if data["output_hashes"] != self.focus_outputs:
                _fail("completion intent differs from the accepted Focus outputs")
            _absolute(data["receipt_path"], "intent receipt path")
            _digest(data["delivery_contract_sha256"], "intent contract digest")
            self.intent = {**data, "record_sha256": digest, "at_utc": record["at_utc"]}
        elif operation == "completed":
            _exact(data, {"intent_sha256", "receipt_path", "receipt_sha256",
                          "receipt_readback_at_utc"}, "completed operation")
            if self.status != "READY" or self.intent is None:
                _fail("completed record lacks a protected READY completion intent")
            if data["intent_sha256"] != self.intent["record_sha256"] or data["receipt_path"] != self.intent["receipt_path"]:
                _fail("completed record is bound to another intent/receipt")
            _digest(data["receipt_sha256"], "completed receipt digest")
            if _stamp(data["receipt_readback_at_utc"], "receipt readback") != stamp:
                _fail("completed readback timestamp differs from its actual operation record")
            refs = [ref for ref in record["snapshots"] if ref["kind"] == "receipt"]
            if len(refs) != 1 or refs[0]["sha256"] != data["receipt_sha256"]:
                _fail("completed receipt snapshot binding is missing")
            self.receipt, self.status = data, "COMPLETED"
        else:
            _fail("unknown protected operation")

    def commit(self, operation: str, data: dict, snapshots: list[dict], now: datetime) -> None:
        record = {"schema_version": "devforge.brainstorm-operation/v1",
                  "sequence": len(self.head["records"]), "previous": self.head["records"][-1],
                  "at_utc": now.isoformat(), "operation": operation, "data": data,
                  "snapshots": snapshots}
        raw = _dump(record)
        digest = _hash(raw)
        _publish(self.fd, "records/" + digest + ".json", raw)
        head = {**self.head, "records": self.head["records"] + [digest]}
        _publish(self.fd, "HEAD.json", _dump(head), replace=True)


def _verify_current(state: _State) -> dict:
    cfg = _configuration(Path(state.manifest["session_path"]), state.root, False)
    manifest, session = state.manifest, cfg["session"]
    if (manifest["session_sha256"] != _hash(cfg["raw"]) or manifest["task_id"] != session["task_id"] or
            manifest["project_root"] != str(cfg["project"]) or manifest["mode"] != cfg["delivery"]["mode"] or
            manifest["provider"] != session["provider"] or manifest["deadline_utc"] != session["deadline_utc"]):
        _fail("current session contract differs from protected initialization")
    expected = {(kind, source): _hash(raw) for kind, source, raw in cfg["sources"]}
    initial = state.manifest["snapshots"]
    actual = {(ref["kind"], ref["source"]): ref["sha256"] for ref in initial if ref["kind"] != "preimage"}
    if len(actual) != len([ref for ref in initial if ref["kind"] != "preimage"]) or actual != expected:
        _fail("fixed selected source identities differ from their protected snapshots")
    preimages = {ref["source"]: ref["sha256"] for ref in initial if ref["kind"] == "preimage"}
    expected_preimages = {path: item["sha256"] for path, item in cfg["baselines"].items() if item["sha256"] is not None}
    if preimages != expected_preimages:
        _fail("protected preimage coverage differs from the selected output baselines")
    if state.outputs != cfg["outputs"]:
        _fail("protected output selection differs from the fixed delivery contract")
    if state.reference_v2 and state.record_outputs is not None:
        checked = _core_result(delivery_core.check_record(cfg["delivery_path"]))
        delivery_core.validate_assignment_reference(
            cfg["delivery"], Path(session["assignment"]["path"]), checked)
        if (checked["outputs"] != state.record_outputs or
                _reference_report(checked) != state.record_references):
            _fail("current Record ledger or reference coverage differs from its accepted snapshot")
    receipt = _external(cfg["receipt"], delivery_core.RECEIPT_LIMIT, "receipt", optional=True)
    if receipt is not None and state.intent is None:
        _fail("existing receipt has no matching protected completion intent")
    if state.intent is not None:
        if (state.intent["receipt_path"] != str(cfg["receipt"]) or
                state.intent["delivery_contract_sha256"] != session["delivery_contract_sha256"]):
            _fail("protected completion intent differs from the selected receipt/contract")
    return cfg


def _fresh(state: _State | None = None) -> str:
    for _ in range(4):
        nonce = secrets.token_hex(16)
        if _NONCE.fullmatch(nonce) and (state is None or nonce not in state.nonces):
            return nonce
    _unavailable("unpredictable fresh challenge generation unavailable")


def _result(status: str, info: dict | None = None, issues: list | None = None,
            **extra: Any) -> dict:
    return {"status": status, "issues": list(issues or []), "scope": SCOPE,
            "native_completion": "NOT_EVALUATED", **(info or {}), **extra}


def _applicability(mode: str) -> dict:
    return {phase: "NOT_APPLICABLE" if mode == "handoff-only" and phase in {"Explore", "Record"}
            else "REQUIRED" for phase in _PHASES["brainstorm"]}


def _view(state: _State, status: str | None = None) -> dict:
    value = _result(status or state.status, {"task_id": state.task, "phase": state.phase,
                                           "phase_applicability": _applicability(state.mode)},
                    state.issues, corrections=state.corrections,
                    terminal=state.status == "FAIL", deadline_utc=state.manifest["deadline_utc"])
    if state.mode == "handoff-only":
        value["not_applicable"] = {"Explore": "handoff-only", "Record": "handoff-only"}
    if state.status in {"ACTIVE", "WAITING_USER"}:
        value["challenge"] = state.challenge
        value["instructions"] = (
            f"Write the selected JSON checkpoint for {state.phase} using this task and challenge; "
            "record actual evidence and explicit unknowns. Runtime performs mechanical checks."
        )
        if state.issues:
            value["instructions"] += " Correct the reported checkpoint issues; no phase has advanced."
    if state.pending is not None:
        value.update(question=state.pending["question"], blocking_dependency=state.pending["blocking_dependency"])
    if state.receipt is not None:
        value.update(receipt_path=state.receipt["receipt_path"], receipt_sha256=state.receipt["receipt_sha256"],
                     receipt_published=True, receipt_readback=True, receipt_verified=True,
                     receipt_readback_at_utc=state.receipt["receipt_readback_at_utc"])
    return value


def _reference_report(checked: dict) -> dict:
    return {key: checked[key] for key in ("reference_profile", "reference_coverage")}


def _current_final(state: _State, cfg: dict) -> dict:
    checked = _core_result(delivery_core.check(cfg["delivery_path"]))
    if checked["outputs"] != state.focus_outputs:
        _fail("current output bytes differ from the accepted Focus snapshot")
    if state.reference_v2:
        delivery_core.validate_assignment_reference(
            cfg["delivery"], Path(cfg["session"]["assignment"]["path"]), checked)
        if _reference_report(checked) != state.focus_references:
            _fail("current references differ from the accepted Focus coverage snapshot")
    return checked


def _verified_receipt(state: _State, cfg: dict) -> tuple[dict, bytes]:
    _current_final(state, cfg)
    verified = _core_result(delivery_core.verify(cfg["delivery_path"], cfg["receipt"]))
    raw = _external(cfg["receipt"], delivery_core.RECEIPT_LIMIT, "receipt readback", "FAIL")
    if _hash(raw) != verified["receipt_sha256"]:
        _fail("receipt changed during protected readback")
    if state.receipt is not None and verified["receipt_sha256"] != state.receipt["receipt_sha256"]:
        _fail("completed receipt bytes differ from the protected completion record")
    return verified, raw


def _guard(function):
    def call(*args, **kwargs):
        info: dict = {}
        try:
            return function(*args, **kwargs, info=info)
        except delivery_core._Problem as exc:
            return _result(exc.result, info, [exc.issue])
        except OSError as exc:
            return _result("COULD_NOT_RUN", info,
                           [f"Filesystem prerequisite unavailable: {exc.strerror or type(exc).__name__}"])
        except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError, UnicodeError) as exc:
            return _result("FAIL", info, [f"Malformed protected input/state: {type(exc).__name__}"])
    call.__name__, call.__doc__ = function.__name__, function.__doc__
    return call


@_guard
def start(session_contract: Path, state_root: Path, *, info: dict) -> dict:
    """Preflight all selected paths and bytes, preserve preimages, then admit Recover."""
    path = _absolute(session_contract, "session_contract")
    root = _absolute(state_root, "state_root")
    now = _now()
    with delivery_core._directory(root.parent, "state_root parent") as parent:
        try:
            os.stat(root.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            _fail("state_root already exists; initialization is exclusive")
    cfg = _configuration(path, root, True)
    info.update(task_id=cfg["session"]["task_id"], phase="Recover",
                phase_applicability=_applicability(cfg["delivery"]["mode"]))
    if now >= cfg["deadline"]:
        _fail("session deadline_utc must be later than initialization")
    # No destination or parent is created until the complete preflight above.
    with delivery_core._directory(root.parent, "state_root parent") as parent:
        try:
            os.mkdir(root.name, 0o700, dir_fd=parent)
        except FileExistsError:
            _fail("state_root initialization collision")
        os.fsync(parent)
    with delivery_core._directory(root, "new state_root") as fd:
        for directory in ("records", "snapshots", "pending"):
            os.mkdir(directory, 0o700, dir_fd=fd)
        _new_at(fd, "LOCK", b"")
        os.fsync(fd)
    with _lock(root) as fd:
        refs = [_snapshot(fd, kind, source, raw) for kind, source, raw in cfg["sources"]]
        with delivery_core._directory(cfg["project"], "project_root") as project_fd:
            for relative, raw in cfg["preimages"].items():
                archive = cfg["baselines"][relative]["archive"]
                refs.append(_snapshot(fd, "preimage", relative, raw))
                _mkdir_chain(project_fd, "/".join(archive.split("/")[:-1]))
                existing = _read_at(project_fd, archive, PREIMAGE_LIMIT, "archive", optional=True)
                if existing is None:
                    with delivery_core._parent(project_fd, archive, "archive", "FAIL") as (parent, name):
                        _new_at(parent, name, raw)
                elif existing != raw:
                    _fail("archive collision during preimage publication")
                if _read_at(project_fd, archive, PREIMAGE_LIMIT, "archive readback") != raw:
                    _fail("archive readback differs from the preserved preimage")
        if delivery_core._within(cfg["receipt"], root):
            relative = cfg["receipt"].relative_to(root).as_posix()
            _mkdir_chain(fd, "/".join(relative.split("/")[:-1]))
        # Revalidate the same selection and prelaunch bytes after preservation.
        # This is still pre-admission: failure leaves no committed HEAD.
        rechecked = _configuration(path, root, True)
        source_pins = lambda value: {(kind, source): _hash(raw)
                                     for kind, source, raw in value["sources"]}
        if (cfg["raw"] != rechecked["raw"] or source_pins(cfg) != source_pins(rechecked) or
                cfg["preimages"] != rechecked["preimages"]):
            _fail("selected contracts, fixed inputs or prelaunch bytes changed during initialization")
        with delivery_core._directory(cfg["project"], "project_root") as project_fd:
            for relative, raw in cfg["preimages"].items():
                archive = cfg["baselines"][relative]["archive"]
                if _read_at(project_fd, archive, PREIMAGE_LIMIT, "pre-admission archive") != raw:
                    _fail("preserved archive changed before admission")
        now = _now()
        _before_deadline(now, cfg["deadline"])
        manifest = {"schema_version": "devforge.brainstorm-state/v1", "state_root": str(root),
                    "session_path": str(path), "session_sha256": _hash(cfg["raw"]),
                    "task_id": cfg["session"]["task_id"], "project_root": str(cfg["project"]),
                    "provider": cfg["session"]["provider"], "mode": cfg["delivery"]["mode"],
                    "started_at_utc": now.isoformat(), "deadline_utc": cfg["session"]["deadline_utc"],
                    "snapshots": refs}
        manifest_raw = _dump(manifest)
        _publish(fd, "MANIFEST.json", manifest_raw)
        record = {"schema_version": "devforge.brainstorm-operation/v1", "sequence": 0,
                  "previous": None, "at_utc": now.isoformat(), "operation": "start",
                  "data": {"challenge": _fresh()}, "snapshots": []}
        record_raw = _dump(record)
        record_hash = _hash(record_raw)
        _publish(fd, "records/" + record_hash + ".json", record_raw)
        _publish(fd, "HEAD.json", _dump({"schema_version": "devforge.brainstorm-head/v1",
                                        "manifest_sha256": _hash(manifest_raw), "records": [record_hash]}))
        state = _State(root, fd, info)
        return _view(state)


@_guard
def context(state_root: Path, *, info: dict) -> dict:
    """Read verified durable state without creating or modifying any file."""
    root = _absolute(state_root, "state_root")
    with _lock(root) as fd:
        state = _State(root, fd, info)
        cfg = _verify_current(state)
        now = _now()
        if state.status == "COMPLETED":
            _verified_receipt(state, cfg)
        elif state.status == "READY":
            _current_final(state, cfg)
        result = _view(state)
        expired = now >= state.deadline
        result.update(expired=expired, admitted=state.status == "ACTIVE" and not expired,
                      persisted_status=state.status)
        if expired:
            # Read-only history remains inspectable without issuing a worker
            # admission or exposing correction instructions as a new turn.
            result.pop("challenge", None)
            result.pop("instructions", None)
            if state.status == "ACTIVE":
                result["status"] = "COULD_NOT_RUN"
                result["issues"] = state.issues + [
                    "original deadline expired; historical context only, no new admission"]
        return result


@_guard
def advance(state_root: Path, *, info: dict) -> dict:
    """Inspect the current checkpoint; accept one phase or spend one correction."""
    root = _absolute(state_root, "state_root")
    with _lock(root) as fd:
        state = _State(root, fd, info)
        cfg = _verify_current(state)
        if state.status != "ACTIVE":
            if state.status == "COMPLETED":
                _verified_receipt(state, cfg)
            elif state.status == "READY":
                _current_final(state, cfg)
            return _view(state)
        _before_deadline(_now(), state.deadline)
        raw = None
        failure = None
        try:
            with delivery_core._directory(cfg["project"], "project_root") as project_fd:
                raw = delivery_core._read_at(project_fd, cfg["session"]["checkpoint_path"],
                                             CHECKPOINT_LIMIT, "checkpoint", "COULD_NOT_RUN")
            checkpoint = _checkpoint(raw, state.task, state.phase, state.challenge, cfg["outputs"],
                                     state.primary_ledger)
            output_values = {}
            reference_report = None
            if checkpoint["state"] == "ready" and state.phase == "Record":
                if state.reference_v2:
                    checked = _core_result(delivery_core.check_record(cfg["delivery_path"]))
                    delivery_core.validate_assignment_reference(
                        cfg["delivery"], Path(cfg["session"]["assignment"]["path"]), checked)
                    reference_report = _reference_report(checked)
                    with delivery_core._directory(cfg["project"], "project_root") as project_fd:
                        for relative, digest in checked["outputs"].items():
                            value = delivery_core._read_at(project_fd, relative, delivery_core.OUTPUT_LIMIT,
                                                           "Record ledger", "FAIL")
                            if _hash(value) != digest:
                                _fail("Record ledger changed after its reference check")
                            baseline = cfg["baselines"][relative]
                            if baseline["sha256"] == digest and not baseline["allow_unchanged"]:
                                _fail(f"unchanged output {relative} was not selected for reuse")
                            output_values[relative] = value
                else:
                    relative = checkpoint["content"]["ledger_path"]
                    with delivery_core._directory(cfg["project"], "project_root") as project_fd:
                        value = delivery_core._read_at(project_fd, relative, delivery_core.OUTPUT_LIMIT,
                                                       "Record ledger", "FAIL")
                    delivery_core._artifact(value, cfg["outputs"][relative])
                    output_values[relative] = value
            elif checkpoint["state"] == "ready" and state.phase == "Focus":
                checked = _core_result(delivery_core.check(cfg["delivery_path"]))
                if state.reference_v2:
                    delivery_core.validate_assignment_reference(
                        cfg["delivery"], Path(cfg["session"]["assignment"]["path"]), checked)
                    reference_report = _reference_report(checked)
                    if state.record_outputs is not None and any(
                            checked["outputs"].get(path) != digest
                            for path, digest in state.record_outputs.items()):
                        _fail("Focus changed an accepted Record ledger")
                with delivery_core._directory(cfg["project"], "project_root") as project_fd:
                    for relative in cfg["outputs"]:
                        value = delivery_core._read_at(project_fd, relative, delivery_core.OUTPUT_LIMIT,
                                                       "Focus output", "FAIL")
                        if _hash(value) != checked["outputs"][relative]:
                            _fail("Focus output changed after its delivery check")
                        baseline = cfg["baselines"][relative]
                        if baseline["sha256"] == _hash(value) and not baseline["allow_unchanged"]:
                            _fail(f"unchanged output {relative} was not selected for reuse")
                        output_values[relative] = value
        except delivery_core._Problem as exc:
            if exc.result != "FAIL":
                raise
            failure = exc.issue
        # Only checkpoint/artifact validation failures consume corrections.
        # Fixed-pin failures and journal/publication errors cannot be converted
        # into an old-state correction that silently discards a committed step.
        _verify_current(state)
        now = _now()
        _before_deadline(now, state.deadline)
        if failure is not None:
            snapshots = ([] if raw is None else [
                _snapshot(fd, "rejected_checkpoint", cfg["session"]["checkpoint_path"], raw)])
            count = state.corrections + 1
            state.commit("correction", {"phase": state.phase, "challenge": state.challenge,
                                        "corrections": count, "issues": [failure], "terminal": count == 2},
                         snapshots, now)
            return _view(_State(root, fd, info), "FAIL")
        snapshots = [_snapshot(fd, "checkpoint", cfg["session"]["checkpoint_path"], raw)]
        snapshots.extend(_snapshot(fd, "output", relative, value) for relative, value in output_values.items())
        if reference_report is not None:
            snapshots.append(_snapshot(fd, "reference_coverage", state.phase, _dump(reference_report)))
        data = {"phase": state.phase, "challenge": state.challenge, "checkpoint_sha256": _hash(raw)}
        if checkpoint["state"] == "awaiting_user":
            state.commit("waiting_user", data, snapshots, now)
        else:
            position = state.phases.index(state.phase)
            final = position == len(state.phases) - 1
            data.update(next_phase="Focus" if final else state.phases[position + 1],
                        next_challenge=None if final else _fresh(state),
                        output_hashes={path: _hash(value) for path, value in output_values.items()})
            state.commit("accepted", data, snapshots, now)
        updated = _State(root, fd, info)
        return _view(updated, "PROGRESS" if updated.status == "ACTIVE" else None)


@_guard
def resume(state_root: Path, *, info: dict) -> dict:
    """Revalidate a paused/interrupted admission, retaining its budget and phase."""
    root = _absolute(state_root, "state_root")
    with _lock(root) as fd:
        state = _State(root, fd, info)
        cfg = _verify_current(state)
        if state.status not in {"ACTIVE", "WAITING_USER"}:
            if state.status == "COMPLETED":
                _verified_receipt(state, cfg)
            elif state.status == "READY":
                _current_final(state, cfg)
            return _view(state)
        now = _now()
        _before_deadline(now, state.deadline)
        state.commit("resume", {"phase": state.phase, "challenge": state.challenge,
                                "next_challenge": _fresh(state)}, [], now)
        return _view(_State(root, fd, info))


@_guard
def complete(state_root: Path, *, info: dict) -> dict:
    """Finalize once, or verify the same receipt under a durable pending intent."""
    root = _absolute(state_root, "state_root")
    with _lock(root) as fd:
        state = _State(root, fd, info)
        cfg = _verify_current(state)
        if state.status == "COMPLETED":
            _verified_receipt(state, cfg)
            return _view(state)
        if state.status != "READY":
            _fail("complete requires READY; required observable phases are incomplete")
        _current_final(state, cfg)
        now = _now()
        if state.intent is None:
            _before_deadline(now, state.deadline)
            if _external(cfg["receipt"], delivery_core.RECEIPT_LIMIT, "receipt", optional=True) is not None:
                _fail("receipt collision without a protected completion intent")
            state.commit("completion_intent", {"receipt_path": str(cfg["receipt"]),
                                               "output_hashes": state.focus_outputs,
                                               "delivery_contract_sha256": cfg["session"]["delivery_contract_sha256"]}, [], now)
            state = _State(root, fd, info)
        existing = _external(cfg["receipt"], delivery_core.RECEIPT_LIMIT, "receipt", optional=True)
        if existing is None:
            _before_deadline(_now(), state.deadline)
            _core_result(delivery_core.finalize(cfg["delivery_path"], cfg["receipt"]))
        # An existing receipt is accepted only under the exact protected intent.
        # OSError after finalize leaves that intent intact for this same path.
        verified, raw = _verified_receipt(state, cfg)
        with delivery_core._directory(cfg["receipt"].parent, "receipt parent") as parent:
            os.fsync(parent)
        _verify_current(state)
        now = _now()
        ref = _snapshot(fd, "receipt", str(cfg["receipt"]), raw)
        final_verified, final_raw = _verified_receipt(state, cfg)
        if final_raw != raw or final_verified["receipt_sha256"] != verified["receipt_sha256"]:
            _fail("receipt bytes changed between readback and completion commit")
        state.commit("completed", {"intent_sha256": state.intent["record_sha256"],
                                   "receipt_path": str(cfg["receipt"]),
                                   "receipt_sha256": verified["receipt_sha256"],
                                   "receipt_readback_at_utc": now.isoformat()}, [ref], now)
        completed = _State(root, fd, info)
        _verified_receipt(completed, cfg)
        return _view(completed)
