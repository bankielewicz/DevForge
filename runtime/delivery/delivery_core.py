"""Mechanical delivery checks for an externally selected, protected task contract.

This module has no worker/model invocation.  The caller must stop the worker and
protect the contract, implementation and receipt directory using its supervisor.
PASS establishes the checks below, never adoption or cognitive phase completion.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

import yaml


CONTRACT_LIMIT = 64 * 1024
OUTPUT_LIMIT = 1024 * 1024
INPUT_LIMIT = 8 * 1024 * 1024
RECEIPT_LIMIT = 128 * 1024
MECHANICAL_SCOPE = (
    "Mechanical delivery only: selected input bytes, required artifact identity, "
    "draft status, real populated section IDs, template-marker absence and "
    "brainstorm handoff-to-ledger binding. No semantic quality, adoption, "
    "native behavior or cognitive phase certification."
)
PREPARATION_SCOPE = (
    "Preparation only: fixed task contract, confined paths and selected input "
    "bytes validated. Required outputs and delivery completion NOT_EVALUATED."
)
REFERENCE_PROFILE = "devforge.reference-coverage/v1"
REFERENCE_SCOPE = (
    "Mechanical delivery and complete selected v2 reference-language coverage: "
    "exact catalog bytes, all selected outputs, artifact identity, causal sections, "
    "permitted evidence fields, every declared body receipt occurrence and "
    "creation-time pending handoff operations. Historical source lineage outside "
    "the selected graph, semantic attribution, adoption, authority, usefulness, "
    "native behavior and rendered user delivery are NOT_EVALUATED."
)
_V1 = "devforge.delivery-task/v1"
_V2 = "devforge.delivery-task/v2"
_ARTIFACT_TYPES = {
    "IDEAS": "idea-ledger", "HANDOFF": "handoff", "SESSION": "session-record",
    "PROD": "product-brief", "UX": "design-spec", "XPLAN": "experiment-plan",
    "XREPORT": "prototype-report", "ARCH": "architecture-contract", "EPIC": "epic",
    "STORY": "story", "XSPEC": "expert-spec", "XPKG": "expert-package",
    "EVPLAN": "expert-evaluation-plan", "EVREPORT": "expert-evaluation-report",
    "DEV": "development-record", "QA": "review-report", "REL": "release-record",
    "CHG": "change-request", "SEVAL": "skill-evaluation-report",
}
_STABLE_ID = re.compile(r"([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*)-([0-9]+)\Z")
_DELIVERY_FIELDS = {
    "final_artifact_check", "receipt_publication", "receipt_readback",
    "target_verification", "user_delivery",
}
_ENVELOPE_FIELDS = {
    "schema_version", "artifact_id", "artifact_type", "project_id", "revision",
    "status", "created_at_utc", "producer", "execution_ref", "upstream",
    "evidence", "supersedes", "decision_ref", "missing_inputs",
}
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_MARKER = re.compile(
    r"\{\{[\s\S]*?\}\}|\$\{[\s\S]*?\}|"
    r"\[(?:TODO|TBD|PLACEHOLDER)(?:\s*:[^\]\n]*)?\]|"
    r"<(?:TODO|TBD|PLACEHOLDER)(?:\s*:[^>\n]*)?>",
    re.IGNORECASE,
)


class _Problem(Exception):
    def __init__(self, result: str, issue: str):
        super().__init__(issue)
        self.result = result
        self.issue = issue


def _fail(issue: str) -> None:
    raise _Problem("FAIL", issue)


def _operational(issue: str) -> None:
    raise _Problem("COULD_NOT_RUN", issue)


def _os_problem(exc: OSError, label: str, missing: str = "COULD_NOT_RUN") -> None:
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        _fail(f"{label}: a path component is a symlink or is not a directory")
    if exc.errno == errno.ENOENT:
        raise _Problem(missing, f"{label}: required path does not exist")
    _operational(f"{label}: filesystem operation unavailable ({exc.strerror or type(exc).__name__})")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _fail(f"{label}: expected a nonempty string without surrounding whitespace")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        _fail(f"{label}: control characters are forbidden")
    try:
        value.encode("utf-8")
    except UnicodeError:
        _fail(f"{label}: value is not a valid UTF-8 string")
    return value


def _revision(value: Any, label: str) -> int:
    if type(value) is not int or value < 1:
        _fail(f"{label}: revision must be a positive integer, not a boolean or float")
    return value


def _relative(value: Any, label: str) -> str:
    value = _text(value, label)
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value) or "\\" in value or "\x00" in value:
        _fail(f"{label}: expected a project-relative path without backslashes or NUL")
    if any(part in ("", ".", "..") for part in value.split("/")):
        _fail(f"{label}: empty, dot and parent path components are forbidden")
    return value


def _absolute(value: Any, label: str) -> Path:
    value = _text(value, label)
    if not value.startswith("/") or "\\" in value or "\x00" in value:
        _fail(f"{label}: expected an absolute path without backslashes or NUL")
    if value != "/" and any(x in ("", ".", "..") for x in value[1:].split("/")):
        _fail(f"{label}: empty, dot and parent path components are forbidden")
    return Path(value)


def _argument_path(value: Path, label: str) -> Path:
    # pathlib arguments may be relative; the resolved spelling is not obtained
    # through Path.resolve(), which would silently follow forbidden symlinks.
    try:
        raw = os.fspath(value)
    except TypeError:
        _fail(f"{label}: expected a filesystem path")
    if not isinstance(raw, str) or "\x00" in raw or "\\" in raw:
        _fail(f"{label}: invalid filesystem path")
    return _absolute(os.path.abspath(raw), label)


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _exact(obj: Any, keys: set[str], label: str) -> dict:
    if not isinstance(obj, dict) or set(obj) != keys:
        _fail(f"{label}: expected exactly the fields {', '.join(sorted(keys))}")
    return obj


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json(raw: bytes, label: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=_json_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid JSON number: {value}")),
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        _fail(f"{label}: invalid strict UTF-8 JSON ({str(exc)[:160]})")


@contextmanager
def _directory(path: Path, label: str, missing: str = "COULD_NOT_RUN"):
    """Walk from / with O_NOFOLLOW, never resolving a symlink component."""
    fd = None
    try:
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        for component in path.parts[1:]:
            new = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = new
        yield fd
    except OSError as exc:
        _os_problem(exc, label, missing)
    finally:
        if fd is not None:
            os.close(fd)


@contextmanager
def _parent(root_fd: int, relative: str, label: str, missing: str):
    fd = os.dup(root_fd)
    try:
        for component in relative.split("/")[:-1]:
            new = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = new
        yield fd, relative.split("/")[-1]
    except OSError as exc:
        _os_problem(exc, label, missing)
    finally:
        os.close(fd)


def _identity(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def _read_at(root_fd: int, relative: str, limit: int, label: str, missing: str,
             *, raw_bytes: bool = False) -> bytes:
    with _parent(root_fd, relative, label, missing) as (parent, name):
        fd = None
        try:
            initial = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
                _fail(f"{label}: expected a regular file with exactly one link")
            # O_NONBLOCK avoids blocking on a substituted FIFO before fstat.
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                _fail(f"{label}: expected a regular file with exactly one link")
            if _identity(initial) != _identity(before):
                _fail(f"{label}: file changed while opening")
            if (before.st_size == 0 and not raw_bytes) or before.st_size > limit:
                _fail(f"{label}: file must contain 1 to {limit} bytes")
            pieces = []
            total = 0
            while total <= limit:
                part = os.read(fd, min(65536, limit + 1 - total))
                if not part:
                    break
                pieces.append(part)
                total += len(part)
            raw = b"".join(pieces)
            after = os.fstat(fd)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if _identity(before) != _identity(after) or _identity(after) != _identity(current):
                _fail(f"{label}: file changed during readback")
            if len(raw) != before.st_size or (not raw and not raw_bytes) or len(raw) > limit:
                _fail(f"{label}: incomplete, empty or oversized readback")
            if not raw_bytes:
                try:
                    raw.decode("utf-8")
                except UnicodeError:
                    _fail(f"{label}: bytes are not valid UTF-8")
            return raw
        except OSError as exc:
            _os_problem(exc, label, missing)
        finally:
            if fd is not None:
                os.close(fd)


def _read_external(path: Path, limit: int, label: str, *, raw_bytes: bool = False,
                   missing: str = "COULD_NOT_RUN") -> bytes:
    with _directory(path.parent, label, missing) as fd:
        if raw_bytes:
            return _read_at(fd, path.name, limit, label, missing, raw_bytes=True)
        return _read_at(fd, path.name, limit, label, missing)


def _inspect_destination(root_fd: int, relative: str) -> None:
    """Reject existing unsafe components without requiring future output bytes."""
    fd = os.dup(root_fd)
    try:
        parts = relative.split("/")
        for component in parts[:-1]:
            try:
                new = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            except FileNotFoundError:
                return
            os.close(fd)
            fd = new
        try:
            info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _fail(f"output {relative}: existing destination is not a regular single-link file")
    except OSError as exc:
        _os_problem(exc, f"output {relative}")
    finally:
        os.close(fd)


def _sections(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        _fail(f"{label}: expected a nonempty section-ID array")
    values = [_text(v, label) for v in value]
    if len(set(values)) != len(values):
        _fail(f"{label}: duplicate section IDs")
    if any("[" in v or "]" in v for v in values):
        _fail(f"{label}: section IDs must not include marker brackets")
    return values


def _load_contract(path: Path) -> tuple[dict, bytes, Path]:
    path = _argument_path(path, "contract path")
    raw = _read_external(path, CONTRACT_LIMIT, "contract")
    data = _json(raw, "contract")
    version = data.get("schema_version") if isinstance(data, dict) else None
    if version not in (_V1, _V2):
        _fail("contract: unsupported schema_version")
    fields = {"schema_version", "task_id", "project_root", "mode", "inputs", "outputs"}
    if version == _V2:
        fields |= {"project_id", "primary_ledger_path", "reference_catalog"}
    _exact(data, fields, "contract")
    _text(data["task_id"], "task_id")
    root = _absolute(data["project_root"], "project_root")
    if _within(path, root):
        _fail("contract: contract file must be outside the writable project")
    if data["mode"] not in ("brainstorm", "handoff-only"):
        _fail("contract: unsupported mode")
    if not isinstance(data["inputs"], list) or not isinstance(data["outputs"], list):
        _fail("contract: inputs and outputs must be arrays")
    paths: set[str] = set()
    for entry in data["inputs"]:
        _exact(entry, {"path", "sha256"}, "input")
        rel = _relative(entry["path"], "input path")
        if not isinstance(entry["sha256"], str) or not _HEX.fullmatch(entry["sha256"]):
            _fail(f"input {rel}: expected a lowercase complete SHA-256")
        if rel in paths:
            _fail(f"contract: duplicate/colliding path {rel}")
        paths.add(rel)
    ids: set[str] = set()
    roles: set[str] = set()
    for entry in data["outputs"]:
        _exact(entry, {"path", "artifact_id", "artifact_type", "revision", "sections"}, "output")
        rel = _relative(entry["path"], "output path")
        aid = _text(entry["artifact_id"], "output artifact_id")
        role = entry["artifact_type"]
        if not isinstance(role, str) or role not in ("idea-ledger", "handoff"):
            _fail(f"output {rel}: unsupported artifact_type")
        _revision(entry["revision"], "output revision")
        _sections(entry["sections"], "output sections")
        if version == _V2:
            _artifact_identity({**entry, "project_id": data["project_id"]}, f"output {rel}")
            _stable_sections(entry["sections"], "output sections", nonempty=True)
        if rel in paths or aid in ids or (role in roles and (version == _V1 or role == "handoff")):
            _fail(f"contract: colliding output path, artifact ID or role for {rel}")
        paths.add(rel)
        ids.add(aid)
        roles.add(role)
    wanted = {"idea-ledger", "handoff"} if data["mode"] == "brainstorm" else {"handoff"}
    if roles != wanted:
        _fail(f"contract: {data['mode']} requires exactly {', '.join(sorted(wanted))}")
    # A file cannot also be another target's parent directory.
    for rel in paths:
        if any("/".join(rel.split("/")[:n]) in paths for n in range(1, len(rel.split("/")))):
            _fail(f"contract: file/directory path collision for {rel}")
    if version == _V2:
        _text(data["project_id"], "project_id")
        primary = data["primary_ledger_path"]
        if data["mode"] == "handoff-only":
            if primary is not None:
                _fail("handoff-only primary_ledger_path must be null")
        elif primary not in {s["path"] for s in data["outputs"] if s["artifact_type"] == "idea-ledger"}:
            _fail("primary_ledger_path must select a required idea-ledger output")
        _catalog_contract(data)
    return data, raw, root


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        _fail(f"{label}: expected exactly 64 lowercase SHA-256 characters")
    return value


def _stable_id(value: Any, label: str) -> str:
    value = _text(value, label)
    match = _STABLE_ID.fullmatch(value)
    if match is None or not any(c != "0" for c in match[2]):
        _fail(f"{label}: expected an uppercase stable ID with a positive decimal suffix")
    return value


def _artifact_identity(value: dict, label: str) -> dict:
    aid = _stable_id(value.get("artifact_id"), f"{label} artifact_id")
    prefix = aid.rsplit("-", 1)[0]
    if prefix not in _ARTIFACT_TYPES or value.get("artifact_type") != _ARTIFACT_TYPES[prefix]:
        _fail(f"{label}: unsupported or mismatched artifact ID/type pair")
    return {"artifact_id": aid, "artifact_type": value["artifact_type"],
            "project_id": _text(value.get("project_id"), f"{label} project_id"),
            "revision": _revision(value.get("revision"), f"{label} revision")}


def _stable_sections(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        _fail(f"{label}: expected {'a nonempty' if nonempty else 'an'} array of stable IDs")
    ids = [_stable_id(item, label) for item in value]
    if len(set(ids)) != len(ids):
        _fail(f"{label}: duplicate stable IDs")
    return ids


def _overlaps(a: Path, b: Path) -> bool:
    return _within(a, b) or _within(b, a)


def _catalog_contract(contract: dict) -> None:
    entries = contract["reference_catalog"]
    if not isinstance(entries, list) or len(entries) > 128:
        _fail("reference_catalog must be an array with at most 128 selected variants")
    root = Path(contract["project_root"])
    output_map = {entry["path"]: entry for entry in contract["outputs"]}
    variants: set[tuple] = set()
    physical: dict[Path, tuple] = {}
    for entry in entries:
        _exact(entry, {"store", "path", "kind", "identity", "source"}, "catalog entry")
        if entry["store"] not in ("project", "authority"):
            _fail("catalog entry: unselected/unsupported logical store")
        rel = _relative(entry["path"], "catalog logical path")
        if entry["kind"] == "artifact":
            _exact(entry["identity"], {"artifact_id", "artifact_type", "project_id", "revision"},
                   "catalog artifact identity")
            identity = _artifact_identity(entry["identity"], "catalog artifact")
            aid, rev = identity["artifact_id"], identity["revision"]
        elif entry["kind"] == "raw-file" and entry["identity"] is None:
            aid, rev = None, None
        else:
            _fail("catalog entry: expected artifact identity or raw-file with null identity")
        source = entry["source"]
        if not isinstance(source, dict):
            _fail("catalog source must be an object")
        kind = source.get("kind")
        fields = {"kind", "physical_path", "sha256"}
        if kind == "output-preimage":
            fields.add("output_path")
        elif kind != "fixed":
            _fail("catalog source: unsupported kind")
        _exact(source, fields, "catalog source")
        path = _absolute(source["physical_path"], "catalog physical_path")
        digest = _digest(source["sha256"], "catalog SHA-256")
        if any(_overlaps(path, root / output) for output in output_map):
            _fail("catalog physical source overlaps a writable output")
        if kind == "output-preimage":
            original = _relative(source["output_path"], "catalog output_path")
            if original not in output_map or not _within(path, root) or path == root:
                _fail("catalog preimage must select a required output and an in-project archive")
        variant = (entry["store"], rel, entry["kind"], aid, rev, digest)
        if variant in variants:
            _fail("reference_catalog contains a duplicate logical variant")
        variants.add(variant)
        if entry["store"] == "project" and rel in output_map:
            output = output_map[rel]
            if aid == output["artifact_id"] and rev == output["revision"]:
                _fail("catalog cannot replace the selected current output variant with fixed bytes")
        binding = (kind, source.get("output_path"), digest, entry["kind"], aid, rev)
        if path in physical and physical[path] != binding:
            _fail("catalog physical aliases disagree about the selected source bytes or kind")
        physical[path] = binding
    unique = list(physical)
    for index, path in enumerate(unique):
        if any(_overlaps(path, previous) for previous in unique[:index]):
            _fail("catalog physical file/directory paths collide")


def catalog_paths(contract: dict) -> list[Path]:
    """Return exact selected physical paths, without reading any target bytes."""
    if contract.get("schema_version") != _V2:
        return []
    return sorted({Path(entry["source"]["physical_path"])
                   for entry in contract["reference_catalog"]}, key=str)


def _source_document(raw: bytes, label: str) -> tuple[dict, str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        _fail(f"{label}: declared artifact is not UTF-8")
    if text.startswith("---\n") or text.startswith("---\r"):
        return _envelope(raw, label)
    if text.lstrip().startswith("{"):
        value = _json(raw, label)
        if not isinstance(value, dict):
            _fail(f"{label}: artifact JSON must be an object")
        return value, ""
    _fail(f"{label}: declared artifact has no own frontmatter/JSON envelope")


def _catalog_identity(raw: bytes, entry: dict) -> tuple[dict | None, str | None]:
    label = f"catalog {entry['store']}:{entry['path']}"
    if entry["kind"] == "raw-file":
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            return None, None
        # Native JSON/frontmatter formats remain native. A declared framework
        # artifact cannot bypass its identity checks by selecting raw-file.
        if text.startswith(("---\n", "---\r")) or text.lstrip().startswith("{"):
            try:
                env, _ = _source_document(raw, label)
            except _Problem:
                if re.search(r'''(?:["']?schema_version["']?\s*:\s*["']?)devforge\.artifact/v1''', text):
                    _fail(f"{label}: malformed claimed framework artifact is not raw-file evidence")
                return None, None
            if env.get("schema_version") == "devforge.artifact/v1":
                _fail(f"{label}: declared artifact cannot be promoted/demoted to raw-file evidence")
        return None, None
    env, body = _source_document(raw, label)
    if env.get("schema_version") != "devforge.artifact/v1":
        _fail(f"{label}: target does not declare devforge.artifact/v1")
    if _artifact_identity(env, label) != entry["identity"]:
        _fail(f"{label}: target's own artifact identity differs from the selected catalog")
    if "status" in env and env["status"] not in ("draft", "in_review", "accepted", "superseded", "retired"):
        _fail(f"{label}: invalid target artifact status")
    return env, body


def catalog_sources(contract: dict, *, preimages: dict[str, bytes] | None = None) -> list[tuple[str, str, bytes]]:
    """Verify selected bytes; an initial caller may supply immutable preimages.

    The reported location for a supplied preimage remains its exact archive
    destination. Session baseline cross-binding and archive publication belong
    to the protected phase runtime, not to this read-only helper.
    """
    if contract.get("schema_version") != _V2:
        return []
    values: dict[str, bytes] = {}
    for entry in contract["reference_catalog"]:
        source = entry["source"]
        path = source["physical_path"]
        if path not in values:
            if source["kind"] == "output-preimage" and preimages is not None:
                original = source["output_path"]
                if original not in preimages or not isinstance(preimages[original], bytes):
                    _operational(f"catalog preimage {original}: selected initial bytes unavailable")
                raw = preimages[original]
                if len(raw) > INPUT_LIMIT:
                    _fail("catalog preimage exceeds the bounded input size")
            else:
                raw = _read_external(Path(path), INPUT_LIMIT, "catalog source", raw_bytes=True)
            values[path] = raw
        raw = values[path]
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            _fail(f"catalog source {path}: selected SHA-256 differs from complete target bytes")
        _catalog_identity(raw, entry)
    return [("reference", path, values[path]) for path in sorted(values)]


class _UniqueLoader(yaml.SafeLoader):
    """Safe YAML with bounded nesting, no expansion aliases and unique keys."""
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("YAML aliases are not supported by this bounded contract")
        self._delivery_depth = getattr(self, "_delivery_depth", 0) + 1
        try:
            if self._delivery_depth > 64:
                raise yaml.YAMLError("YAML nesting exceeds 64 levels")
            return super().compose_node(parent, index)
        finally:
            self._delivery_depth -= 1

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise yaml.YAMLError(f"duplicate YAML key: {key}")
                seen.add(key)
            except TypeError as exc:
                raise yaml.YAMLError("unhashable YAML mapping key") from exc
        return super().construct_mapping(node, deep=deep)


def _markdown_lines(text: str) -> list[str]:
    # str.splitlines() incorrectly promotes VT, NEL and Unicode separators to
    # Markdown line endings, allowing non-headings to appear as real headings.
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _envelope(raw: bytes, label: str) -> tuple[dict, str]:
    text = raw.decode("utf-8")
    lines = _markdown_lines(text)
    if not lines or lines[0] != "---":
        _fail(f"{label}: artifact must begin with --- frontmatter")
    closing = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
    if closing is None:
        _fail(f"{label}: frontmatter closing delimiter is absent")
    try:
        value = yaml.load("\n".join(lines[1:closing]), Loader=_UniqueLoader)
    except (yaml.YAMLError, ValueError, TypeError, RecursionError) as exc:
        _fail(f"{label}: invalid unique-key YAML/JSON frontmatter ({str(exc)[:160]})")
    if not isinstance(value, dict):
        _fail(f"{label}: artifact envelope must be an object")
    if _MARKER.search(text):
        _fail(f"{label}: unresolved template marker")
    return value, "\n".join(lines[closing + 1:])


def _heading_sections(body: str, selected: list[str], label: str) -> dict[str, int]:
    lines = _markdown_lines(body)
    headings: list[tuple[int, int, str, int]] = []
    content: set[int] = set()
    fence: tuple[str, int] | None = None
    comment = False
    previous_text: int | None = None
    for index, line in enumerate(lines):
        if fence is not None:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(fence[1]) + r",}[ \t]*", line):
                fence = None
            elif line.strip():
                content.add(index)
            previous_text = None
            continue
        visible = ""
        rest = line
        while rest:
            if comment:
                end_comment = rest.find("-->")
                if end_comment < 0:
                    rest = ""
                    break
                rest = rest[end_comment + 3:]
                comment = False
            else:
                start_comment = rest.find("<!--")
                if start_comment < 0:
                    visible += rest
                    break
                visible += rest[:start_comment]
                rest = rest[start_comment + 4:]
                comment = True
        line = visible
        # This bounded checker does not implement CommonMark raw HTML blocks.
        # Refuse them explicitly instead of promoting their apparent headings.
        # Literal HTML inside a fenced code block took the earlier fence branch.
        if re.match(r"^ {0,3}<(?:(?:/?[A-Za-z][A-Za-z0-9-]*)(?=[ \t/>]|$)|[!?])", line):
            _fail(f"{label}: unsupported raw HTML block; real section headings cannot be established")
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if opening and not (opening[1][0] == "`" and "`" in opening[2]):
            fence = (opening[1][0], len(opening[1]))
            previous_text = None
            continue
        atx = re.match(r"^ {0,3}(#{1,6})[ \t]+(.*)$", line)
        if atx:
            title = re.sub(r"[ \t]+#+[ \t]*$", "", atx[2]).strip()
            headings.append((index, len(atx[1]), title, index))
            previous_text = None
            continue
        underline = re.fullmatch(r" {0,3}(=+|-+)[ \t]*", line)
        if underline and previous_text is not None:
            headings.append((previous_text, 1 if underline[1][0] == "=" else 2, lines[previous_text].strip(), index))
            content.discard(previous_text)
            previous_text = None
            continue
        if line.strip() and not re.fullmatch(r"\s*(?:<!--[\s\S]*?-->|[-*_][ \t]*)+\s*", line):
            content.add(index)
        previous_text = index if line.strip() and len(line) - len(line.lstrip(" ")) <= 3 else None
    if fence is not None:
        _fail(f"{label}: unclosed Markdown code fence")
    found: dict[str, int] = {}
    for section in selected:
        matches = [h for h in headings if h[2] == section or re.search(r"(?:^|\s)\[" + re.escape(section) + r"\](?:\s|$)", h[2])]
        if len(matches) != 1:
            _fail(f"{label}: section {section} must resolve to exactly one real heading")
        start, level, _, end_heading = matches[0]
        end = next((h[0] for h in headings if h[0] > start and h[1] <= level), len(lines))
        if not any(end_heading < line < end for line in content):
            _fail(f"{label}: section {section} has no nonempty content")
        found[section] = start + 1
    return found


def _reference_revision_types(value: Any, label: str, context: str = "") -> None:
    if isinstance(value, list):
        for item in value:
            _reference_revision_types(item, label, context)
    elif isinstance(value, dict):
        if ("artifact_id" in value or context in ("upstream", "supersedes", "evidence")) and value.get("revision") is not None:
            _revision(value["revision"], f"{label} reference revision")
        for key, child in value.items():
            _reference_revision_types(child, label, str(key))


def _artifact(raw: bytes, spec: dict) -> tuple[dict, str]:
    label = spec["path"]
    env, body = _envelope(raw, label)
    if env.get("schema_version") != "devforge.artifact/v1":
        _fail(f"{label}: invalid artifact schema_version")
    for key in ("artifact_id", "artifact_type"):
        if env.get(key) != spec[key]:
            _fail(f"{label}: artifact {key} does not match the selected contract")
    if _revision(env.get("revision"), f"{label} own revision") != spec["revision"]:
        _fail(f"{label}: own revision does not match the contract")
    if env.get("status") != "draft":
        _fail(f"{label}: only draft artifacts are permitted")
    _reference_revision_types(env, label)
    _heading_sections(body, spec["sections"], label)
    return env, body


def _ledger_binding(artifacts: dict[str, tuple[dict, str]], specs: list[dict], hashes: dict[str, str]) -> None:
    ledger = next(s for s in specs if s["artifact_type"] == "idea-ledger")
    handoff = next(s for s in specs if s["artifact_type"] == "handoff")
    env, _ = artifacts[handoff["path"]]
    refs = env.get("upstream")
    if not isinstance(refs, list):
        _fail("handoff upstream: an array binding the selected ledger is required")
    matches = []
    for ref in refs:
        if not isinstance(ref, dict):
            _fail("handoff upstream: every reference must be an object")
        # Type checking is not optional even for another upstream artifact.
        _revision(ref.get("revision"), "handoff upstream revision")
        if ref.get("artifact_id") == ledger["artifact_id"] or ref.get("path") == ledger["path"]:
            matches.append(ref)
    if len(matches) != 1:
        _fail("handoff upstream: exactly one unambiguous selected-ledger binding is required")
    ref = matches[0]
    expected = {"artifact_id": ledger["artifact_id"], "revision": ledger["revision"], "store": "project", "path": ledger["path"], "sha256": hashes[ledger["path"]]}
    if any(ref.get(k) != v for k, v in expected.items()):
        _fail("handoff upstream: selected ledger identity, revision, store, path or digest does not match")
    _heading_sections(artifacts[ledger["path"]][1], _sections(ref.get("sections"), "handoff upstream sections"), "handoff upstream ledger sections")


def _v2_markdown(body: str, label: str) -> dict:
    """Bounded real-heading/pipe-row inventory; never promote code or comments."""
    lines = _markdown_lines(body)
    headings: list[tuple[int, int, str, int]] = []
    visible_lines: list[tuple[int, str]] = []
    content: set[int] = set()
    fence: tuple[str, int] | None = None
    comment = False
    previous: tuple[int, str] | None = None
    for index, original in enumerate(lines):
        line = original
        if fence is not None:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(fence[1]) + r",}[ \t]*", line):
                fence = None
            elif line.strip():
                content.add(index)
            previous = None
            continue
        visible, rest = "", line
        while rest:
            if comment:
                end = rest.find("-->")
                if end < 0:
                    rest = ""
                else:
                    rest, comment = rest[end + 3:], False
            else:
                start = rest.find("<!--")
                if start < 0:
                    visible += rest
                    rest = ""
                else:
                    visible += rest[:start]
                    rest, comment = rest[start + 4:], True
        line = visible
        if re.match(r"^ {0,3}<(?:(?:/?[A-Za-z][A-Za-z0-9-]*)(?=[ \t/>]|$)|[!?])", line):
            _fail(f"{label}: unsupported raw HTML or anchor block")
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if opening and not (opening[1][0] == "`" and "`" in opening[2]):
            fence, previous = (opening[1][0], len(opening[1])), None
            continue
        if line.startswith("\t") or line.startswith("    "):
            if line.strip():
                content.add(index)
            previous = None
            continue
        visible_lines.append((index, line))
        heading = re.match(r"^ {0,3}(#{1,6})[ \t]+(.*)$", line)
        if heading:
            title = re.sub(r"[ \t]+#+[ \t]*$", "", heading[2]).strip()
            headings.append((index, len(heading[1]), title, index))
            previous = None
            continue
        underline = re.fullmatch(r" {0,3}(=+|-+)[ \t]*", line)
        if underline and previous is not None:
            headings.append((previous[0], 1 if underline[1][0] == "=" else 2,
                             previous[1].strip(), index))
            content.discard(previous[0])
            previous = None
            continue
        if line.strip() and not re.fullmatch(r"\s*(?:[-*_][ \t]*){3,}\s*", line):
            content.add(index)
        previous = (index, line) if line.strip() and not re.match(r"^ {0,3}(?:[>|]|[-+*][ \t]|[0-9]+[.)][ \t])", line) else None
    if fence is not None or comment:
        _fail(f"{label}: unclosed code fence or HTML comment")
    sections: dict[str, dict] = {}
    for start, level, title, end_heading in headings:
        ids = re.findall(r"\[([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-[0-9]+)\]", title)
        if _STABLE_ID.fullmatch(title):
            ids.append(title)
        end = next((h[0] for h in headings if h[0] > start and h[1] <= level), len(lines))
        for section in ids:
            _stable_id(section, "heading ID")
            if section in sections:
                _fail(f"{label}: duplicate stable heading ID {section}")
            sections[section] = {"line": start + 1, "end_line": end + 1,
                                 "populated": any(end_heading < row < end for row in content)}
    rows: dict[str, int] = {}
    header: list[str] | None = None
    active_columns: int | None = None
    for number, line in visible_lines:
        if not line.strip().startswith("|") or not line.strip().endswith("|"):
            header, active_columns = None, None
            continue
        cells = [cell.strip() for cell in line.strip()[1:-1].split("|")]
        separator = all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)
        if separator:
            active_columns = len(cells) if header is not None and len(header) == len(cells) else None
            continue
        if active_columns is not None:
            if len(cells) != active_columns:
                _fail(f"{label}: malformed pipe-table row")
            first = cells[0]
            if first.startswith("`") and first.endswith("`"):
                first = first[1:-1]
            if _STABLE_ID.fullmatch(first):
                _stable_id(first, "source row ID")
                if first in rows:
                    _fail(f"{label}: duplicate source row ID {first}")
                rows[first] = number + 1
        else:
            header = cells
    return {"sections": sections, "rows": rows, "heading_count": len(headings)}


def _v2_artifact(raw: bytes, spec: dict, project: str) -> tuple[dict, str]:
    env, body = _artifact(raw, spec)
    label = spec["path"]
    if not _ENVELOPE_FIELDS <= set(env):
        _fail(f"{label}: missing required standard envelope fields")
    own = _artifact_identity(env, label)
    if own["project_id"] != project:
        _fail(f"{label}: output project_id differs from the selected project")
    try:
        timestamp = env["created_at_utc"]
        if not isinstance(timestamp, str):
            raise ValueError("timestamp must be a string")
        stamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
            raise ValueError("UTC required")
    except (TypeError, ValueError, OverflowError):
        _fail(f"{label}: created_at_utc must be a valid UTC timestamp string")
    producer = _exact(env["producer"], {"skill", "skill_revision"}, f"{label} producer")
    for key, value in producer.items():
        _text(value, f"{label} producer {key}")
    for field in ("upstream", "evidence", "missing_inputs"):
        if not isinstance(env[field], list):
            _fail(f"{label}: {field} must be an array")
    if env["execution_ref"] is None:
        _fail(f"{label}: managed v2 output requires a selected execution_ref")
    if env["artifact_type"] == "handoff":
        state = _exact(env.get("delivery_state"), _DELIVERY_FIELDS, f"{label} delivery_state")
        if any(value != "NOT_RUN" for value in state.values()):
            _fail(f"{label}: current-task delivery operations must remain creation-time NOT_RUN")
    elif "delivery_state" in env:
        _fail(f"{label}: delivery_state is reserved for the selected handoff")
    return env, body


def _target_key(target: dict) -> tuple:
    identity = target["identity"] or {}
    return (target["kind"], identity.get("artifact_id"), identity.get("revision"), target["sha256"])


def _reference_targets(contract: dict, sources: list[tuple[str, str, bytes]],
                       output_bytes: dict[str, bytes], artifacts: dict[str, tuple[dict, str]]) -> list[dict]:
    source_bytes = {path: raw for _, path, raw in sources}
    targets = []
    for entry in contract["reference_catalog"]:
        raw = source_bytes[entry["source"]["physical_path"]]
        env, body = _catalog_identity(raw, entry)
        targets.append({"store": entry["store"], "path": entry["path"], "kind": entry["kind"],
                        "identity": entry["identity"], "sha256": entry["source"]["sha256"],
                        "physical_path": entry["source"]["physical_path"],
                        "source_kind": entry["source"]["kind"], "env": env,
                        "markdown": _v2_markdown(body, entry["path"]) if body is not None else None})
    for path, raw in output_bytes.items():
        env, body = artifacts[path]
        targets.append({"store": "project", "path": path, "kind": "artifact",
                        "identity": _artifact_identity(env, path), "sha256": hashlib.sha256(raw).hexdigest(),
                        "physical_path": str(Path(contract["project_root"]) / path),
                        "source_kind": "current-output", "env": env,
                        "markdown": _v2_markdown(body, path)})
    return targets


def _target_public(target: dict) -> dict:
    return {key: target[key] for key in ("store", "path", "kind", "identity", "sha256", "physical_path", "source_kind")}


def _reference_coverage(contract: dict, sources: list[tuple[str, str, bytes]],
                        output_bytes: dict[str, bytes], artifacts: dict[str, tuple[dict, str]]) -> dict:
    targets = _reference_targets(contract, sources, output_bytes, artifacts)
    report = {"profile": REFERENCE_PROFILE, "outputs": sorted(output_bytes),
              "catalog": [_target_public(target) for target in targets], "occurrences": [],
              "exceptions": [], "execution_references": {}, "semantic_review": "NOT_EVALUATED"}
    edges: dict[tuple, set[tuple]] = {}
    output_specs = {entry["path"]: entry for entry in contract["outputs"]}
    required = {"artifact_id", "revision", "store", "path", "sha256"}
    optional = {"sections", "source_rows", "artifact_type", "project_id", "evidence_kind"}

    def resolve(ref: dict, field: str, where: str, own: dict, missing: list) -> dict:
        if not isinstance(ref, dict):
            _fail(f"{where}: reference must be an object")
        kind = ref.get("evidence_kind", "artifact")
        if kind == "raw-file":
            if field != "evidence":
                _fail(f"{where}: raw-file is permitted only in evidence")
            _exact(ref, {"evidence_kind", "store", "path", "sha256"}, where)
            key = ("raw-file", None, None, _digest(ref["sha256"], where))
        elif kind == "artifact":
            if not required <= set(ref) or not set(ref) <= required | optional:
                _fail(f"{where}: malformed or unknown artifact reference fields")
            aid = _stable_id(ref["artifact_id"], where)
            if aid.rsplit("-", 1)[0] not in _ARTIFACT_TYPES:
                _fail(f"{where}: unsupported artifact ID")
            key = ("artifact", aid, _revision(ref["revision"], where), _digest(ref["sha256"], where))
        else:
            _fail(f"{where}: unsupported reference evidence_kind")
        if ref["store"] not in ("project", "authority"):
            _fail(f"{where}: unsupported or unselected store")
        path = _relative(ref["path"], where)
        matches = [target for target in targets if target["store"] == ref["store"]
                   and target["path"] == path and _target_key(target) == key]
        if len(matches) != 1:
            _fail(f"{where}: reference has no unique exact selected identity/revision/hash target")
        target = matches[0]
        identity = target["identity"] or {}
        if ((key[0] == "artifact" and key[1:3] == (own["artifact_id"], own["revision"]))
                or target["sha256"] == own["sha256"]):
            _fail(f"{where}: a current artifact cannot contain its own complete-byte reference")
        for field_name in ("artifact_type", "project_id"):
            if field_name in ref and ref[field_name] != identity.get(field_name):
                _fail(f"{where}: repeated {field_name} differs from the target")
        sections = _stable_sections(ref.get("sections", []), f"{where} sections")
        rows = _stable_sections(ref.get("source_rows", []), f"{where} source_rows")
        if key[0] == "artifact":
            markdown = target["markdown"]
            for section in sections:
                if section not in markdown["sections"] or not markdown["sections"][section]["populated"]:
                    _fail(f"{where}: section {section} is not one populated real stable heading")
            for row in rows:
                if row not in markdown["rows"]:
                    _fail(f"{where}: source row {row} does not resolve uniquely")
                if field == "upstream" and sections and not any(
                    markdown["sections"][section]["line"] < markdown["rows"][row]
                    < markdown["sections"][section]["end_line"] for section in sections
                ):
                    _fail(f"{where}: source row {row} lies outside the claimed causal sections")
            if field == "upstream":
                if "sections" not in ref:
                    _fail(f"{where}: causal upstream requires explicit sections")
                if not sections:
                    if markdown["heading_count"]:
                        _fail(f"{where}: sectioned upstream requires actual stable section IDs")
                    disclosure = {"kind": "unsectioned-source", "store": ref["store"], "path": path,
                                  "artifact_id": ref["artifact_id"], "revision": ref["revision"],
                                  "sha256": ref["sha256"]}
                    found = [item for item in missing if isinstance(item, dict)
                             and all(item.get(k) == v for k, v in disclosure.items())]
                    if len(found) != 1:
                        _fail(f"{where}: truly unsectioned upstream needs one exact missing_inputs disclosure")
                    report["exceptions"].append({"document": own["path"], "where": where, "disclosure": found[0]})
                edges.setdefault(own["node"], set()).add(_target_key(target))
            if field == "supersedes" and (identity["artifact_id"] != own["artifact_id"]
                                           or identity["revision"] >= own["revision"]):
                _fail(f"{where}: supersedes must reference the same artifact at an earlier preserved revision")
            if field == "execution_ref" and identity["artifact_type"] != "session-record":
                _fail(f"{where}: execution_ref must select an actual SESSION/session-record")
            if field == "output" and target["source_kind"] != "current-output":
                _fail(f"{where}: output receipt must select a completed current task output")
        report["occurrences"].append({"document": own["path"], "document_sha256": own["sha256"],
                                      "where": where, "field": field, "target": _target_public(target),
                                      "sections": sections, "source_rows": rows})
        return target

    def research(ref: dict, where: str, own: dict, missing: list) -> None:
        _exact(ref, {"evidence_kind", "url", "applicable_version", "retrieved_at", "claim", "snapshot"}, where)
        url = _text(ref["url"], where)
        if not re.fullmatch(r"https?://[^\s/]+(?:/[^\s]*)?", url):
            _fail(f"{where}: external research URL must be an explicit HTTP(S) locator")
        for field in ("applicable_version", "claim"):
            _text(ref[field], where)
        try:
            value = ref["retrieved_at"]
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError("date")
            date.fromisoformat(value)
        except (ValueError, TypeError):
            _fail(f"{where}: retrieved_at must be a valid YYYY-MM-DD date")
        if not isinstance(ref["snapshot"], dict) or ref["snapshot"].get("evidence_kind") != "raw-file":
            _fail(f"{where}: external research requires an explicit selected raw-file snapshot")
        resolve(ref["snapshot"], "evidence", where + ".snapshot", own, missing)

    def same_reference(left: dict, right: dict) -> bool:
        return (all(left.get(key) == right.get(key) for key in required)
                and left.get("sections", []) == right.get("sections", [])
                and left.get("source_rows", []) == right.get("source_rows", []))

    def body_claims(body: str, own: dict, env: dict, declared: dict[str, list[dict]],
                    location: str = "body") -> None:
        marker = re.compile(r"@df-[A-Za-z-]*")
        decoder = json.JSONDecoder(object_pairs_hook=_json_pairs,
                                   parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        spans: list[tuple[int, int]] = []
        position = 0
        while match := marker.search(body, position):
            name = match[0]
            start = match.end()
            if name not in ("@df-ref", "@df-link", "@df-delivery") or body[start:start + 2] != "({":
                _fail(f"{own['path']}: malformed or unsupported body claim marker")
            try:
                value, end = decoder.raw_decode(body, start + 1)
            except (ValueError, TypeError, RecursionError) as exc:
                _fail(f"{own['path']}: malformed body claim JSON ({str(exc)[:100]})")
            if body[end:end + 1] != ")" or "\n" in body[start:end] or "\r" in body[start:end]:
                _fail(f"{own['path']}: body claim must be one complete JSON object on one physical line")
            stop = end + 1
            # Offsets are UTF-8 bytes within the named parsed body/string;
            # document_sha256 separately binds the complete original file.
            where = location + ":" + str(len(body[:match.start()].encode("utf-8"))) + ":" + str(len(body[:stop].encode("utf-8")))
            if name == "@df-ref":
                if not isinstance(value, dict) or "field" not in value:
                    _fail(f"{where}: body reference requires field")
                field = value["field"]
                if field not in ("upstream", "supersedes", "execution_ref", "decision_ref", "evidence", "output"):
                    _fail(f"{where}: unsupported body reference field")
                ref = {key: val for key, val in value.items() if key != "field"}
                if ref.get("evidence_kind") == "external-research":
                    if field != "evidence":
                        _fail(f"{where}: research belongs only in evidence")
                    research(ref, where, own, env["missing_inputs"])
                else:
                    resolve(ref, field, where, own, env["missing_inputs"])
                if field in ("upstream", "supersedes", "execution_ref", "decision_ref"):
                    if not any(same_reference(ref, existing) for existing in declared[field]):
                        _fail(f"{where}: repeated body reference disagrees with its declared envelope field")
            elif name == "@df-link":
                _exact(value, {"store", "path", "artifact_id", "revision", "relation"}, where)
                if value["store"] not in ("project", "authority") or value["relation"] not in ("planned-output", "noncausal"):
                    _fail(f"{where}: unsupported noncausal link")
                path = _relative(value["path"], where)
                aid, revision = _stable_id(value["artifact_id"], where), _revision(value["revision"], where)
                selected_output = output_specs.get(path) if value["store"] == "project" else None
                output_match = selected_output is not None and (aid, revision) == (selected_output["artifact_id"], selected_output["revision"])
                catalog_match = any(target["store"] == value["store"] and target["path"] == path
                                    and target["identity"] is not None
                                    and (aid, revision) == (target["identity"]["artifact_id"], target["identity"]["revision"])
                                    for target in targets)
                if not output_match and (value["relation"] == "planned-output" or not catalog_match):
                    _fail(f"{where}: link does not match an externally selected output/catalog identity")
                report["occurrences"].append({"document": own["path"], "document_sha256": own["sha256"],
                                              "where": where, "field": "link", "link": value})
            else:
                _exact(value, {"task_id", *_DELIVERY_FIELDS}, where)
                if (env["artifact_type"] != "handoff" or value["task_id"] != contract["task_id"]
                        or {key: value[key] for key in _DELIVERY_FIELDS} != env["delivery_state"]):
                    _fail(f"{where}: repeated delivery state differs from creation-time pending fields")
                report["occurrences"].append({"document": own["path"], "document_sha256": own["sha256"],
                                              "where": where, "field": "delivery_state", "delivery_state": value})
            spans.append((match.start(), stop))
            position = stop
        remaining = list(body)
        for start, end in spans:
            remaining[start:end] = " " * (end - start)
        residual = "".join(remaining)
        reserved = [r"[0-9A-Fa-f]{64,}", r"(?i:sha-?256)[ \t]*[:=][ \t]*\S+",
                    r"(?<![A-Za-z0-9._-])[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-[0-9]+@\S+",
                    r"(?<![A-Za-z0-9._/-])(?:project|authority):[^\s]+",
                    r"\b(?:" + "|".join(sorted(_DELIVERY_FIELDS)) + r")\b"]
        if any(re.search(pattern, residual) for pattern in reserved):
            _fail(f"{own['path']}: reserved receipt/identity/delivery claim outside the deterministic body grammar")
        locators = {target["path"] for target in targets} | set(output_specs)
        locators |= {target["physical_path"] for target in targets}
        locators |= {str(Path(contract["project_root"]) / path) for path in output_specs}
        for locator in locators:
            if re.search(r"(?<![A-Za-z0-9._/-])" + re.escape(locator) + r"(?![A-Za-z0-9._/-])", residual):
                _fail(f"{own['path']}: selected locator outside a complete reference/link atom")
        previous_cells = None
        for line in _markdown_lines(residual):
            if not line.strip().startswith("|") or not line.strip().endswith("|"):
                previous_cells = None
                continue
            cells = [cell.strip().strip("`").casefold() for cell in line.strip()[1:-1].split("|")]
            if previous_cells is not None and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                if any(cell in ("sha-256", "sha256") for cell in previous_cells):
                    _fail(f"{own['path']}: split-column receipt tables are unsupported; use complete reference atoms")
            previous_cells = cells

    for path, (env, body) in artifacts.items():
        own_target = next(target for target in targets if target["source_kind"] == "current-output" and target["path"] == path)
        own = {**own_target["identity"], "path": path, "sha256": own_target["sha256"], "node": _target_key(own_target)}
        for section in output_specs[path]["sections"]:
            observed = own_target["markdown"]["sections"].get(section)
            if observed is None or not observed["populated"]:
                _fail(f"{path}: selected output section {section} is not one populated stable heading")
        for item in env["missing_inputs"]:
            if isinstance(item, str):
                _text(item, f"{path} missing_inputs")
                continue
            _exact(item, {"kind", "store", "path", "artifact_id", "revision", "sha256", "reason"}, "missing_inputs disclosure")
            if item["kind"] != "unsectioned-source" or item["store"] not in ("project", "authority"):
                _fail(f"{path}: unsupported missing_inputs mechanical exception")
            _relative(item["path"], "disclosure path")
            _stable_id(item["artifact_id"], "disclosure artifact_id")
            _revision(item["revision"], "disclosure revision")
            _digest(item["sha256"], "disclosure SHA-256")
            _text(item["reason"], "disclosure reason")
        declared: dict[str, list[dict]] = {key: [] for key in ("upstream", "supersedes", "execution_ref", "decision_ref")}
        for field in ("upstream", "evidence", "supersedes", "execution_ref", "decision_ref"):
            value = env[field]
            values = value if field in ("upstream", "evidence") else ([] if value is None else [value])
            for index, ref in enumerate(values):
                where = f"$.{field}[{index}]" if field in ("upstream", "evidence") else f"$.{field}"
                if field == "execution_ref" and isinstance(ref, str):
                    match = re.fullmatch(r"(SESSION-[0-9]+)@([0-9]+)", ref)
                    if match is None:
                        _fail(f"{path}: malformed SESSION execution_ref shorthand")
                    try:
                        revision = int(match[2])
                    except ValueError:
                        _fail(f"{path}: oversized execution_ref revision")
                    _revision(revision, where)
                    matches = [target for target in targets if target["identity"] is not None
                               and (target["identity"]["artifact_id"], target["identity"]["revision"]) == (match[1], revision)]
                    if len(matches) != 1:
                        _fail(f"{path}: SESSION shorthand has no unique selected catalog variant")
                    target = matches[0]
                    ref = {"artifact_id": match[1], "revision": revision, "store": target["store"],
                           "path": target["path"], "sha256": target["sha256"], "sections": []}
                if field == "evidence" and isinstance(ref, dict) and ref.get("evidence_kind") == "external-research":
                    research(ref, where, own, env["missing_inputs"])
                    continue
                target = resolve(ref, field, where, own, env["missing_inputs"])
                if field in declared:
                    declared[field].append(ref)
                if field == "execution_ref":
                    report["execution_references"][path] = {
                        "physical_path": target["physical_path"], "artifact_id": target["identity"]["artifact_id"],
                        "revision": target["identity"]["revision"], "sha256": target["sha256"]}
        if len(declared["upstream"]) != len({json.dumps(ref, sort_keys=True) for ref in declared["upstream"]}):
            _fail(f"{path}: duplicate declared causal upstream reference")
        if env["artifact_type"] == "handoff" and contract["mode"] == "brainstorm":
            primary = output_specs[contract["primary_ledger_path"]]
            matches = [ref for ref in declared["upstream"] if ref["artifact_id"] == primary["artifact_id"]
                       or (ref["store"] == "project" and ref["path"] == primary["path"])]
            expected = {"artifact_id": primary["artifact_id"], "revision": primary["revision"],
                        "store": "project", "path": primary["path"],
                        "sha256": hashlib.sha256(output_bytes[primary["path"]]).hexdigest()}
            if len(matches) != 1 or any(matches[0].get(key) != value for key, value in expected.items()):
                _fail("handoff upstream must bind exactly the selected current primary ledger")
        def extension(value: Any, location: str) -> None:
            if isinstance(value, dict):
                if any(key in value for key in ("artifact_id", "revision", "store", "path", "sha256")):
                    _fail(f"{path}: reference-shaped mapping in an unsupported envelope field")
                for key, child in value.items():
                    extension(child, location + "." + str(key))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    extension(child, location + "[" + str(index) + "]")
            elif isinstance(value, str):
                body_claims(value, own, env, declared, location)
        for key, value in env.items():
            if key not in _ENVELOPE_FIELDS and key != "delivery_state":
                if key in {"store", "path", "sha256", *_DELIVERY_FIELDS}:
                    _fail(f"{path}: reserved reference/delivery key in an unsupported envelope field")
                extension(value, "$." + str(key))
        for index, value in enumerate(env["missing_inputs"]):
            if isinstance(value, str):
                body_claims(value, own, env, declared, f"$.missing_inputs[{index}]")
        body_claims(body, own, env, declared)
        for disclosure in (item for item in env["missing_inputs"] if isinstance(item, dict)):
            if not any(exception["document"] == path and exception["disclosure"] == disclosure
                       for exception in report["exceptions"]):
                _fail(f"{path}: unsectioned disclosure does not correspond to a checked causal reference")

    # Include only exact already-selected historical causal edges. This never
    # follows a source reference to an unselected path or certifies that lineage.
    for target in targets:
        env = target["env"] or {}
        if target["source_kind"] == "current-output" or not isinstance(env.get("upstream"), list):
            continue
        for ref in env["upstream"]:
            if not isinstance(ref, dict):
                continue
            for other in targets:
                identity = other["identity"] or {}
                if all(ref.get(key) == expected for key, expected in (
                    ("store", other["store"]), ("path", other["path"]),
                    ("artifact_id", identity.get("artifact_id")), ("revision", identity.get("revision")),
                    ("sha256", other["sha256"]))) and type(ref.get("revision")) is int:
                    edges.setdefault(_target_key(target), set()).add(_target_key(other))
    visiting, visited = set(), set()
    def visit(node: tuple) -> None:
        if node in visiting:
            _fail("selected immutable causal reference graph contains a cycle")
        if node in visited:
            return
        visiting.add(node)
        for child in edges.get(node, set()):
            visit(child)
        visiting.remove(node)
        visited.add(node)
    for node in edges:
        visit(node)
    return report


def validate_assignment_reference(contract: dict, assignment_path: Path, checked: dict) -> None:
    """Bind already checked execution references without rereading outputs."""
    if contract.get("schema_version") != _V2:
        return
    if checked.get("result") != "PASS":
        _fail("assignment binding requires a successful selected output check")
    if (checked.get("task_id") != contract["task_id"]
            or checked.get("project_root") != contract["project_root"]
            or checked.get("reference_profile") != REFERENCE_PROFILE):
        _fail("assignment binding: checked task/project/reference profile mismatch")
    path = str(_argument_path(assignment_path, "selected assignment path"))
    coverage = checked.get("reference_coverage", {})
    refs = coverage.get("execution_references", {})
    if (not checked.get("outputs") or set(refs) != set(checked["outputs"])
            or coverage.get("outputs") != sorted(checked["outputs"])):
        _fail("assignment binding: produced execution_ref coverage is incomplete")
    for ref in refs.values():
        if ref.get("physical_path") != path or not ref.get("artifact_id", "").startswith("SESSION-"):
            _fail("output execution_ref does not resolve to the selected assignment bytes")


def _evaluate(contract_path: Path, require_outputs: bool, *, record_only: bool = False) -> dict:
    scope = MECHANICAL_SCOPE if require_outputs else PREPARATION_SCOPE
    result: dict = {"result": "COULD_NOT_RUN", "issues": [], "scope": scope}
    try:
        contract, raw, root = _load_contract(contract_path)
        result.update(task_id=contract["task_id"], contract_sha256=hashlib.sha256(raw).hexdigest(), project_root=str(root), inputs={})
        v2 = contract["schema_version"] == _V2
        if record_only and (not v2 or contract["mode"] != "brainstorm"):
            _fail("Record reference checking requires a v2 brainstorm task")
        if v2 and require_outputs:
            result["scope"] = ("Record admission only; handoff/final delivery NOT_EVALUATED. " if record_only else "") + REFERENCE_SCOPE
        sources = []
        if v2:
            if require_outputs:
                sources = catalog_sources(contract)
            else:
                fixed = {**contract, "reference_catalog": [entry for entry in contract["reference_catalog"]
                                                           if entry["source"]["kind"] == "fixed"]}
                sources = catalog_sources(fixed)
                pending = set()
                archives: dict[str, bytes] = {}
                for entry in contract["reference_catalog"]:
                    source = entry["source"]
                    if source["kind"] != "output-preimage":
                        continue
                    path = source["physical_path"]
                    if path in pending:
                        continue
                    if path not in archives:
                        try:
                            archives[path] = _read_external(Path(path), INPUT_LIMIT, "catalog archive",
                                                            raw_bytes=True, missing="_PENDING_BASELINE")
                        except _Problem as exc:
                            if exc.result != "_PENDING_BASELINE":
                                raise
                            pending.add(path)
                            continue
                    value = archives[path]
                    if hashlib.sha256(value).hexdigest() != source["sha256"]:
                        _fail("catalog archive: selected preimage SHA-256 mismatch")
                    _catalog_identity(value, entry)
                result["pending_baselines"] = sorted(pending)
        artifacts = {}
        output_bytes = {}
        with _directory(root, "project_root") as fd:
            for spec in contract["outputs"]:
                _inspect_destination(fd, spec["path"])
            for spec in contract["inputs"]:
                value = _read_at(fd, spec["path"], INPUT_LIMIT, f"input {spec['path']}", "COULD_NOT_RUN")
                digest = hashlib.sha256(value).hexdigest()
                result["inputs"][spec["path"]] = digest
                if digest != spec["sha256"]:
                    _fail(f"input {spec['path']}: selected SHA-256 does not match current bytes")
            if require_outputs:
                result["outputs"] = {}
                for spec in contract["outputs"]:
                    if record_only and spec["artifact_type"] != "idea-ledger":
                        continue
                    value = _read_at(fd, spec["path"], OUTPUT_LIMIT, f"output {spec['path']}", "FAIL")
                    result["outputs"][spec["path"]] = hashlib.sha256(value).hexdigest()
                    output_bytes[spec["path"]] = value
                    artifacts[spec["path"]] = (_v2_artifact(value, spec, contract["project_id"])
                                               if v2 else _artifact(value, spec))
                if v2:
                    result["reference_profile"] = REFERENCE_PROFILE
                    result["reference_coverage"] = _reference_coverage(contract, sources, output_bytes, artifacts)
                elif contract["mode"] == "brainstorm":
                    _ledger_binding(artifacts, contract["outputs"], result["outputs"])
        result["result"] = "PASS"
    except _Problem as exc:
        result.update(result=exc.result, issues=[exc.issue])
    except OSError as exc:
        result.update(result="COULD_NOT_RUN", issues=[f"Filesystem prerequisite unavailable: {exc.strerror or type(exc).__name__}"])
    except (TypeError, ValueError, KeyError, RecursionError, OverflowError) as exc:
        result.update(result="FAIL", issues=[f"Malformed or excessive selected contract/reference data: {type(exc).__name__}"])
    return result


def prepare(contract_path: Path) -> dict:
    """Validate the fixed contract and inputs; delivery remains unevaluated."""
    return _evaluate(contract_path, False)


def check(contract_path: Path) -> dict:
    """Read and mechanically check the selected current input/output bytes."""
    return _evaluate(contract_path, True)


def check_record(contract_path: Path) -> dict:
    """Check ALL selected v2 ledgers without reading the future handoff bytes."""
    return _evaluate(contract_path, True, record_only=True)


_RECEIPT_KEYS = {"schema_version", "result", "task_id", "contract_sha256", "project_root", "inputs", "outputs", "scope", "completed_checks_at_utc", "receipt_path", "receipt_publication", "receipt_readback"}


def _receipt_path(path: Path, checked: dict) -> Path:
    path = _argument_path(path, "receipt path")
    if _within(path, Path(checked["project_root"])):
        _fail("receipt: publication must be outside the writable project")
    return path


def _validate_receipt(record: Any, path: Path, checked: dict) -> None:
    v2 = "reference_coverage" in checked
    _exact(record, _RECEIPT_KEYS | ({"reference_profile", "reference_coverage"} if v2 else set()), "receipt")
    schema = "devforge.delivery-receipt/v2" if v2 else "devforge.delivery-receipt/v1"
    if record["schema_version"] != schema or record["result"] != "PASS":
        _fail("receipt: unsupported schema or non-PASS mechanical result")
    for field in ("task_id", "contract_sha256", "project_root", "inputs", "outputs", "scope"):
        if record[field] != checked[field]:
            _fail(f"receipt: current {field} binding does not match")
    if v2 and any(record[field] != checked[field] for field in ("reference_profile", "reference_coverage")):
        _fail("receipt: current reference coverage/profile differs from the published evidence")
    if record["receipt_path"] != str(path):
        _fail("receipt: copied or mismatched publication path")
    if record["receipt_publication"] != "NOT_RUN" or record["receipt_readback"] != "NOT_RUN":
        _fail("receipt: its own publication/readback must remain pending at creation")
    try:
        stamp = datetime.fromisoformat(record["completed_checks_at_utc"])
        if stamp.utcoffset() is None:
            raise ValueError("timezone required")
    except (TypeError, ValueError):
        _fail("receipt: invalid completed-check timestamp")


def finalize(contract_path: Path, receipt_path: Path) -> dict:
    """Check itself, exclusively publish externally, then read back its receipt."""
    checked = check(contract_path)
    if checked["result"] != "PASS":
        return checked
    observed: dict = {}
    try:
        path = _receipt_path(receipt_path, checked)
        record = {"schema_version": "devforge.delivery-receipt/v1", **{k: checked[k] for k in ("result", "task_id", "contract_sha256", "project_root", "inputs", "outputs", "scope")}, "completed_checks_at_utc": datetime.now(timezone.utc).isoformat(), "receipt_path": str(path), "receipt_publication": "NOT_RUN", "receipt_readback": "NOT_RUN"}
        if "reference_coverage" in checked:
            record.update(schema_version="devforge.delivery-receipt/v2",
                          reference_profile=checked["reference_profile"], reference_coverage=checked["reference_coverage"])
        raw = (json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        if len(raw) > RECEIPT_LIMIT:
            _fail("receipt: serialized receipt exceeds the bounded size")
        with _directory(path.parent, "receipt parent") as parent:
            fd = None
            try:
                fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
                offset = 0
                while offset < len(raw):
                    written = os.write(fd, raw[offset:])
                    if written <= 0:
                        _operational("receipt: incomplete publication")
                    offset += written
                os.fsync(fd)
            except FileExistsError:
                _fail("receipt: destination already exists; existing receipt preserved")
            except OSError as exc:
                _os_problem(exc, "receipt publication")
            finally:
                if fd is not None:
                    os.close(fd)
            if "reference_coverage" in checked:
                observed.update(receipt_path=str(path), receipt_published=True, receipt_readback=False)
            readback = _read_at(parent, path.name, RECEIPT_LIMIT, "receipt readback", "COULD_NOT_RUN")
            if readback != raw:
                _fail("receipt: complete-byte readback differs from published bytes")
            if "reference_coverage" in checked:
                observed.update(receipt_readback=True, receipt_sha256=hashlib.sha256(readback).hexdigest())
        _validate_receipt(_json(readback, "receipt"), path, checked)
        if "reference_coverage" in checked:
            current = check(contract_path)
            if current["result"] != "PASS":
                return {**current, "receipt_path": str(path), "receipt_sha256": hashlib.sha256(readback).hexdigest(),
                        "receipt_published": True, "receipt_readback": True}
            _validate_receipt(_json(readback, "receipt"), path, current)
        return {**checked, "receipt_path": str(path), "receipt_sha256": hashlib.sha256(readback).hexdigest(), "receipt_published": True, "receipt_readback": True}
    except _Problem as exc:
        return {**checked, **observed, "result": exc.result, "issues": [exc.issue]}
    except OSError as exc:
        return {**checked, **observed, "result": "COULD_NOT_RUN", "issues": [f"Receipt filesystem operation unavailable: {exc.strerror or type(exc).__name__}"]}


def verify(contract_path: Path, receipt_path: Path) -> dict:
    """Repeat current byte/mechanical checks and validate the external receipt."""
    checked = check(contract_path)
    if checked["result"] != "PASS":
        return checked
    try:
        path = _receipt_path(receipt_path, checked)
        raw = _read_external(path, RECEIPT_LIMIT, "receipt")
        _validate_receipt(_json(raw, "receipt"), path, checked)
        return {**checked, "receipt_path": str(path), "receipt_sha256": hashlib.sha256(raw).hexdigest(), "receipt_verified": True}
    except _Problem as exc:
        return {**checked, "result": exc.result, "issues": [exc.issue]}
    except OSError as exc:
        return {**checked, "result": "COULD_NOT_RUN", "issues": [f"Receipt filesystem operation unavailable: {exc.strerror or type(exc).__name__}"]}
