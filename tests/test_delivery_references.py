"""Independent reference-coverage contract fixtures, authored without execution.

Oracle: REFERENCE-COVERAGE-CONTRACT.md, SHA-256
fc7d5901899882c1fb6762e5e4683e978212d35ccfc484df1cd5d5fa61807b90.
Human-readable expectations were frozen before this module was written:
.poc/runtime-repairs/reference-fixtures-20260907T122235Z/.
The root's exact report/research clarifications are preserved there separately.

All artifacts are synthetic. Expected hashes use only hashlib over fixture
bytes, never production helpers. Public file-open spies demonstrate selected
read behavior in logic fixtures; they do not demonstrate a worker sandbox.
Authoring status: UNEXECUTED. A later independent validator owns execution.
"""

from __future__ import annotations

import builtins
from contextlib import contextmanager
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest import mock


IMPLEMENTATION = Path(__file__).resolve().parents[1] / "runtime" / "delivery"
sys.path.insert(0, str(IMPLEMENTATION))
import delivery_core as core  # noqa: E402


PROFILE = "devforge.reference-coverage/v1"
DELIVERY_FIELDS = (
    "final_artifact_check", "receipt_publication", "receipt_readback",
    "target_verification", "user_delivery",
)
COVERAGE_KEYS = {
    "profile", "outputs", "catalog", "occurrences", "exceptions",
    "execution_references", "semantic_review",
}
UNAVAILABLE = ("FAIL", "COULD_NOT_RUN")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def artifact_bytes(envelope: dict, body: str) -> bytes:
    return b"---\n" + json_bytes(envelope) + b"---\n\n" + body.encode("utf-8")


def atom(value: dict, marker: str = "ref") -> str:
    return "@df-" + marker + "(" + json.dumps(value, separators=(",", ":")) + ")"


def reference_atom(reference: dict, field: str = "evidence") -> str:
    return atom({"field": field, **reference})


def scalar_values(value: object) -> list:
    """Inspect unspecified report leaf names without imposing a new schema."""
    if isinstance(value, dict):
        return [item for key, child in value.items()
                for item in [key, *scalar_values(child)]]
    if isinstance(value, list):
        return [item for child in value for item in scalar_values(child)]
    return [value]


class Fixture:
    def __init__(self, root: Path, *, mode: str = "brainstorm"):
        self.root = root
        self.project = root / "project"
        self.operator = root / "operator"
        self.project.mkdir(parents=True)
        self.operator.mkdir()
        (self.operator / "fixed").mkdir()
        (self.project / "docs").mkdir()
        (self.project / "inputs").mkdir()
        self.contract_path = self.operator / "task.json"
        self.receipt_path = self.operator / "receipt.json"
        self.unselected_path = self.project / "unselected-secret.bin"
        self.unselected_path.write_bytes(b"Unselected fixture sentinel; must never be read.\n")
        self.input_path = self.project / "inputs/context.txt"
        self.input_path.write_bytes(b"Synthetic user uncertainty about shift reminders.\n")
        self.contract = {
            "schema_version": "devforge.delivery-task/v2",
            "task_id": "REFERENCE-TEST-001",
            "project_root": str(self.project),
            "mode": mode,
            "inputs": [{"path": "inputs/context.txt", "sha256": digest(self.input_path.read_bytes())}],
            "outputs": [],
            "project_id": "synthetic-project",
            "primary_ledger_path": "docs/ideas.md" if mode == "brainstorm" else None,
            "reference_catalog": [],
        }
        self.source_envelopes = {}
        self.source_bodies = {}
        self.session = self.add_artifact(
            "session", "authority", "sessions/current.md", "SESSION-001", "session-record",
            "## Session [SESSION-SECTION-001]\n\nSynthetic selected session assignment.\n",
            sections=["SESSION-SECTION-001"],
        )
        self.source = self.add_artifact(
            "source", "project", "sources/product.md", "PROD-001", "product-brief",
            "## Observations [SRC-001]\n\nA reminder remains a proposal.\n\n"
            "| ID | Observation |\n| --- | --- |\n| I-001 | One missed shift was mentioned. |\n",
            sections=["SRC-001"],
        )
        self.envelopes = {}
        self.bodies = {}
        self.paths = {}
        if mode == "brainstorm":
            self.add_output("primary", "docs/ideas.md", "IDEAS-001", "idea-ledger", 2, "IDEAS-SECTION-001")
            self.add_output("secondary", "docs/retained.md", "IDEAS-002", "idea-ledger", 1, "RETAINED-001")
            self.envelopes["primary"]["upstream"] = [copy.deepcopy(self.source)]
            self.write("primary", refresh=False)
        self.add_output("handoff", "docs/handoff.md", "HANDOFF-001", "handoff", 1, "NEXT-001")
        self.envelopes["handoff"]["delivery_state"] = dict.fromkeys(DELIVERY_FIELDS, "NOT_RUN")
        if mode == "brainstorm":
            self.envelopes["handoff"]["upstream"] = [self.output_ref("primary")]
        self.write("handoff", refresh=False)
        self.write_contract()

    @staticmethod
    def envelope(artifact_id: str, role: str, revision: int = 1) -> dict:
        return {
            "schema_version": "devforge.artifact/v1", "artifact_id": artifact_id,
            "artifact_type": role, "project_id": "synthetic-project", "revision": revision,
            "status": "draft", "created_at_utc": "2026-09-01T12:00:00Z",
            "producer": {"skill": "devforge-brainstorm", "skill_revision": "synthetic-fixture"},
            "execution_ref": None, "upstream": [], "evidence": [], "supersedes": None,
            "decision_ref": None, "missing_inputs": [],
        }

    def add_artifact(self, name: str, store: str, logical: str, identity: str,
                     role: str, body: str, *, revision: int = 1,
                     sections: list | None = None, project_id: str = "synthetic-project") -> dict:
        env = self.envelope(identity, role, revision)
        env["project_id"] = project_id
        physical = self.operator / "fixed" / (name + ".md")
        physical.write_bytes(artifact_bytes(env, body))
        self.source_envelopes[name] = env
        self.source_bodies[name] = body
        self.contract["reference_catalog"].append({
            "store": store, "path": logical, "kind": "artifact",
            "identity": {"artifact_id": identity, "artifact_type": role,
                         "project_id": project_id, "revision": revision},
            "source": {"kind": "fixed", "physical_path": str(physical),
                       "sha256": digest(physical.read_bytes())},
        })
        return {"artifact_id": identity, "revision": revision, "store": store,
                "path": logical, "sha256": digest(physical.read_bytes()),
                "sections": list(sections or [])}

    def add_raw(self, name: str, raw: bytes, *, logical: str | None = None) -> dict:
        physical = self.operator / "fixed" / (name + ".bin")
        physical.write_bytes(raw)
        logical = logical or ("evidence/" + name + ".bin")
        self.contract["reference_catalog"].append({
            "store": "authority", "path": logical, "kind": "raw-file", "identity": None,
            "source": {"kind": "fixed", "physical_path": str(physical), "sha256": digest(raw)},
        })
        return {"evidence_kind": "raw-file", "store": "authority", "path": logical,
                "sha256": digest(raw)}

    def add_output(self, name: str, logical: str, identity: str, role: str,
                   revision: int, section: str) -> None:
        self.contract["outputs"].append({
            "path": logical, "artifact_id": identity, "artifact_type": role,
            "revision": revision, "sections": [section],
        })
        self.paths[name] = self.project / logical
        self.envelopes[name] = self.envelope(identity, role, revision)
        self.envelopes[name]["execution_ref"] = copy.deepcopy(self.session)
        self.bodies[name] = "## Selected material [" + section + "]\n\nA bounded proposal remains unadopted.\n"
        self.write(name, refresh=False)

    def output_row(self, name: str) -> dict:
        logical = self.paths[name].relative_to(self.project).as_posix()
        return next(row for row in self.contract["outputs"] if row["path"] == logical)

    def output_ref(self, name: str) -> dict:
        row = self.output_row(name)
        return {"artifact_id": row["artifact_id"], "revision": row["revision"],
                "store": "project", "path": row["path"],
                "sha256": digest(self.paths[name].read_bytes()), "sections": list(row["sections"])}

    def link(self, name: str, relation: str = "planned-output") -> dict:
        row = self.output_row(name)
        return {"store": "project", "path": row["path"], "artifact_id": row["artifact_id"],
                "revision": row["revision"], "relation": relation}

    def source_row(self, name: str = "source") -> dict:
        physical = self.operator / "fixed" / (name + ".md")
        return next(row for row in self.contract["reference_catalog"]
                    if row["source"]["physical_path"] == str(physical))

    def rewrite_source(self, body: str, *, name: str = "source") -> None:
        row = self.source_row(name)
        raw = artifact_bytes(self.source_envelopes[name], body)
        Path(row["source"]["physical_path"]).write_bytes(raw)
        row["source"]["sha256"] = digest(raw)
        self.source_bodies[name] = body
        if name == "source":
            self.source["sha256"] = digest(raw)
            if "primary" in self.envelopes:
                self.envelopes["primary"]["upstream"][0]["sha256"] = digest(raw)
                self.write("primary")
        self.write_contract()

    def write(self, name: str, *, refresh: bool = True) -> None:
        self.paths[name].write_bytes(artifact_bytes(self.envelopes[name], self.bodies[name]))
        if name == "primary" and refresh and "handoff" in self.envelopes:
            self.envelopes["handoff"]["upstream"][0] = self.output_ref("primary")
            self.write("handoff", refresh=False)

    def append(self, name: str, text: str) -> None:
        self.bodies[name] += "\n" + text + "\n"
        self.write(name)

    def write_contract(self) -> None:
        self.contract_path.write_bytes(json_bytes(self.contract))

    def expected_outputs(self, *, record: bool = False) -> dict:
        return {row["path"]: digest((self.project / row["path"]).read_bytes())
                for row in self.contract["outputs"]
                if not record or row["artifact_type"] == "idea-ledger"}

    def historical_primary(self, *, preimage: bool = False) -> dict:
        old = self.add_artifact(
            "old-primary", "project", "docs/ideas.md", "IDEAS-001", "idea-ledger",
            "## Prior proposal [IDEAS-SECTION-001]\n\nA preserved earlier proposal.\n",
            sections=["IDEAS-SECTION-001"],
        )
        if preimage:
            row = self.source_row("old-primary")
            archive = self.project / "archive" / "ideas-r1.md"
            archive.parent.mkdir()
            archive.write_bytes(Path(row["source"]["physical_path"]).read_bytes())
            row["source"] = {"kind": "output-preimage", "output_path": "docs/ideas.md",
                             "physical_path": str(archive), "sha256": old["sha256"]}
        self.envelopes["primary"]["supersedes"] = copy.deepcopy(old)
        self.write("primary")
        self.write_contract()
        return old


class DeliveryReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="devforge-reference-contract-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.serial = 0

    def fixture(self, **kwargs) -> Fixture:
        self.serial += 1
        return Fixture(self.root / ("case-" + str(self.serial)), **kwargs)

    @contextmanager
    def selected_reads_only(self, f: Fixture):
        """Trap unselected file reads, including component-walk os.open calls."""
        permitted = {str(f.contract_path), str(f.receipt_path)}
        permitted.update(str(f.project / row["path"]) for row in f.contract["inputs"])
        permitted.update(str(f.project / row["path"]) for row in f.contract["outputs"])
        permitted.update(row["source"]["physical_path"] for row in f.contract.get("reference_catalog", [])
                         if isinstance(row, dict) and isinstance(row.get("source"), dict)
                         and isinstance(row["source"].get("physical_path"), str))
        forbidden = []
        network_attempts = []
        observed = []
        original_os_open, original_open, original_io_open = os.open, builtins.open, io.open

        def inspect(path, *, dir_fd=None):
            if isinstance(path, int):
                return
            name = os.fsdecode(os.fspath(path))
            if not os.path.isabs(name):
                parent = os.readlink("/proc/self/fd/" + str(dir_fd)) if dir_fd is not None else os.getcwd()
                name = os.path.join(parent, name)
            name = os.path.normpath(name)
            if os.path.isdir(name):
                return
            observed.append(name)
            if name not in permitted:
                forbidden.append(name)
                raise AssertionError("Unselected read attempted: " + name)

        def guarded_os_open(path, flags, *args, **kwargs):
            reading = not flags & os.O_WRONLY
            if reading:
                inspect(path, dir_fd=kwargs.get("dir_fd"))
            descriptor = original_os_open(path, flags, *args, **kwargs)
            if reading:
                try:
                    # A successful following open must not hide its real target
                    # behind an otherwise selected symlink spelling.
                    inspect(os.readlink("/proc/self/fd/" + str(descriptor)))
                except BaseException:
                    os.close(descriptor)
                    raise
            return descriptor

        def guarded_open(path, mode="r", *args, **kwargs):
            if "r" in mode or "+" in mode:
                inspect(path)
            handle = original_open(path, mode, *args, **kwargs)
            if "r" in mode or "+" in mode:
                try:
                    inspect(os.readlink("/proc/self/fd/" + str(handle.fileno())))
                except BaseException:
                    handle.close()
                    raise
            return handle

        def guarded_io_open(path, mode="r", *args, **kwargs):
            if "r" in mode or "+" in mode:
                inspect(path)
            handle = original_io_open(path, mode, *args, **kwargs)
            if "r" in mode or "+" in mode:
                try:
                    inspect(os.readlink("/proc/self/fd/" + str(handle.fileno())))
                except BaseException:
                    handle.close()
                    raise
            return handle

        def reject_network(*args, **kwargs):
            network_attempts.append((args, kwargs))
            raise AssertionError("Selected snapshots never authorize runtime network access")

        try:
            with mock.patch.object(os, "open", side_effect=guarded_os_open), \
                    mock.patch.object(builtins, "open", side_effect=guarded_open), \
                    mock.patch.object(io, "open", side_effect=guarded_io_open), \
                    mock.patch.object(socket, "create_connection", side_effect=reject_network), \
                    mock.patch.object(socket, "getaddrinfo", side_effect=reject_network), \
                    mock.patch.object(socket.socket, "connect", side_effect=reject_network), \
                    mock.patch.object(socket.socket, "connect_ex", side_effect=reject_network):
                yield observed
        finally:
            self.assertEqual(forbidden, [], "Reference claims must never authorize unselected reads")
            self.assertEqual(network_attempts, [], "Snapshot references must not make network calls")

    def result(self, actual: dict, expected="PASS") -> dict:
        self.assertIsInstance(actual, dict)
        allowed = (expected,) if isinstance(expected, str) else expected
        self.assertIn(actual.get("result"), allowed, actual)
        self.assertIsInstance(actual.get("issues"), list, actual)
        self.assertTrue(actual.get("scope"), actual)
        if actual["result"] != "PASS":
            self.assertTrue(actual["issues"], actual)
        return actual

    def checked(self, f: Fixture, *, record: bool = False) -> dict:
        with self.selected_reads_only(f):
            actual = self.result((core.check_record if record else core.check)(f.contract_path))
        expected = f.expected_outputs(record=record)
        self.assertEqual(actual["outputs"], expected)
        self.assertEqual(actual["contract_sha256"], digest(f.contract_path.read_bytes()))
        coverage = actual["reference_coverage"]
        self.assertEqual(set(coverage), COVERAGE_KEYS)
        self.assertEqual(coverage["profile"], PROFILE)
        self.assertEqual(coverage["semantic_review"], "NOT_EVALUATED")
        self.assertEqual(set(coverage["outputs"]), set(expected))
        self.assertEqual(len(coverage["outputs"]), len(expected))
        self.assertEqual(set(coverage["execution_references"]), set(expected))
        session = f.source_row("session")
        for output in expected:
            self.assertEqual(coverage["execution_references"][output], {
                "physical_path": session["source"]["physical_path"],
                "artifact_id": "SESSION-001", "revision": 1, "sha256": f.session["sha256"],
            })
        for key in ("catalog", "occurrences", "exceptions"):
            self.assertIsInstance(coverage[key], list)
        self.assertFalse(f.receipt_path.exists())
        return actual

    def rejected(self, f: Fixture, expected="FAIL", *, record: bool = False) -> dict:
        self.assertFalse(os.path.lexists(f.receipt_path))
        with self.selected_reads_only(f):
            actual = self.result((core.check_record if record else core.check)(f.contract_path), expected)
            if not record:
                self.result(core.finalize(f.contract_path, f.receipt_path), expected)
        self.assertFalse(os.path.lexists(f.receipt_path), "Refusal must not publish a receipt")
        return actual

    def finalized(self, f: Fixture) -> tuple[dict, bytes]:
        before = f.expected_outputs()
        with self.selected_reads_only(f):
            actual = self.result(core.finalize(f.contract_path, f.receipt_path))
            self.result(core.verify(f.contract_path, f.receipt_path))
        raw = f.receipt_path.read_bytes()
        receipt = json.loads(raw)
        self.assertEqual(actual["receipt_sha256"], digest(raw))
        self.assertIs(actual["receipt_published"], True)
        self.assertIs(actual["receipt_readback"], True)
        self.assertEqual(receipt["schema_version"], "devforge.delivery-receipt/v2")
        self.assertEqual(receipt["reference_profile"], PROFILE)
        self.assertEqual(receipt["reference_coverage"]["semantic_review"], "NOT_EVALUATED")
        self.assertEqual(receipt["outputs"], before)
        self.assertEqual(receipt["receipt_publication"], "NOT_RUN")
        self.assertEqual(receipt["receipt_readback"], "NOT_RUN")
        self.assertNotIn("receipt_sha256", receipt)
        self.assertNotIn(digest(raw), scalar_values(receipt))
        self.assertEqual(f.expected_outputs(), before, "Finalization must preserve authored output bytes")
        return receipt, raw

    def test_all_selected_ledgers_and_handoff_receive_coverage(self):
        f = self.fixture()
        original_retained = f.paths["secondary"].read_bytes()
        first = self.checked(f)
        second = self.checked(f)
        self.assertEqual(first["reference_coverage"], second["reference_coverage"])
        self.assertEqual(f.paths["secondary"].read_bytes(), original_retained)
        self.assertEqual(len(first["outputs"]), 3)
        self.finalized(f)

    def test_second_selected_ledger_cannot_be_filtered_by_existence_or_file_kind(self):
        for kind in ("missing", "directory", "symlink", "hardlink", "malformed"):
            with self.subTest(kind=kind):
                f = self.fixture()
                target = f.paths["secondary"]
                original = target.read_bytes()
                target.unlink()
                if kind == "directory":
                    target.mkdir()
                elif kind == "symlink":
                    f.unselected_path.write_bytes(original)
                    target.symlink_to(f.unselected_path)
                elif kind == "hardlink":
                    f.unselected_path.write_bytes(original)
                    os.link(f.unselected_path, target)
                elif kind == "malformed":
                    target.write_bytes(original.replace(b'"idea-ledger"', b'"unknown-artifact"', 1))
                self.rejected(f)

    def test_prepare_before_outputs_and_handoff_only_primary_selection(self):
        f = self.fixture()
        for path in f.paths.values():
            path.unlink()
        with self.selected_reads_only(f):
            prepared = self.result(core.prepare(f.contract_path))
        self.assertNotIn("receipt_sha256", prepared)
        self.assertFalse(f.receipt_path.exists())
        self.rejected(f)
        h = self.fixture(mode="handoff-only")
        self.assertIsNone(h.contract["primary_ledger_path"])
        self.checked(h)
        h.contract["primary_ledger_path"] = "docs/ideas.md"
        h.write_contract()
        self.rejected(h)

    def test_v2_exact_top_level_selection_and_output_identity_uniqueness(self):
        variants = {
            "missing-project": lambda c: c.pop("project_id"),
            "missing-primary": lambda c: c.pop("primary_ledger_path"),
            "missing-catalog": lambda c: c.pop("reference_catalog"),
            "unknown": lambda c: c.update(full_enforcement=True),
            "empty-project": lambda c: c.update(project_id=""),
            "bad-primary": lambda c: c.update(primary_ledger_path="docs/other.md"),
            "handoff-primary": lambda c: c.update(primary_ledger_path="docs/handoff.md"),
            "duplicate-path": lambda c: c["outputs"].append(copy.deepcopy(c["outputs"][0])),
            "duplicate-id": lambda c: c["outputs"][1].update(artifact_id="IDEAS-001"),
            "unsupported-output": lambda c: c["outputs"][1].update(artifact_type="product-brief"),
        }
        for name, mutate in variants.items():
            with self.subTest(case=name):
                f = self.fixture()
                mutate(f.contract)
                f.write_contract()
                self.rejected(f)

    def test_preserved_fixed_and_preimage_revisions_share_exact_logical_path(self):
        for preimage in (False, True):
            with self.subTest(preimage=preimage):
                f = self.fixture()
                old = f.historical_primary(preimage=preimage)
                f.append("primary", reference_atom(old, "supersedes"))
                f.append("handoff", reference_atom(f.output_ref("primary"), "output"))
                checked = self.checked(f)
                values = scalar_values(checked["reference_coverage"])
                self.assertIn(old["sha256"], values)
                self.assertIn(digest(f.paths["primary"].read_bytes()), values)
                self.finalized(f)

    def test_missing_preimage_remains_pending_without_standalone_publication(self):
        f = self.fixture()
        f.historical_primary(preimage=True)
        row = f.contract["reference_catalog"][-1]
        archive = Path(row["source"]["physical_path"])
        archive.unlink()
        with self.selected_reads_only(f):
            prepared = self.result(core.prepare(f.contract_path))
        self.assertEqual(prepared["pending_baselines"], [str(archive)])
        self.assertFalse(archive.exists())
        self.rejected(f, UNAVAILABLE)
        self.assertFalse(archive.exists())

    def test_old_revision_never_substitutes_current_bytes_or_stale_archive(self):
        for variant in ("archive-drift", "old-id-current-hash", "current-id-old-hash", "unlisted-alias"):
            with self.subTest(case=variant):
                f = self.fixture()
                old = f.historical_primary(preimage=True)
                ref = f.envelopes["primary"]["supersedes"]
                if variant == "archive-drift":
                    Path(f.contract["reference_catalog"][-1]["source"]["physical_path"]).write_bytes(b"Changed archive\n")
                elif variant == "old-id-current-hash":
                    ref["sha256"] = digest(f.paths["primary"].read_bytes())
                    f.write("primary")
                elif variant == "current-id-old-hash":
                    ref["revision"] = 2
                    f.write("primary")
                else:
                    ref["path"] = "archive/ideas-r1.md"
                    f.write("primary")
                self.rejected(f)

    def test_separately_selected_fixed_alias_is_not_discovered_by_suffix(self):
        f = self.fixture()
        alias = copy.deepcopy(f.source_row())
        alias["path"] = "aliases/product-copy.md"
        f.contract["reference_catalog"].append(alias)
        ref = copy.deepcopy(f.source)
        ref["path"] = alias["path"]
        f.envelopes["primary"]["upstream"] = [ref]
        f.write("primary")
        f.write_contract()
        self.checked(f)
        for path in ("product-copy.md", str(f.root / "unselected" / alias["path"]), "other/product-copy.md"):
            with self.subTest(path=path):
                g = self.fixture()
                g.envelopes["primary"]["upstream"][0]["path"] = path
                g.write("primary")
                self.rejected(g)

    def test_unselected_direct_and_transitive_targets_do_not_authorize_reads(self):
        f = self.fixture()
        ref = copy.deepcopy(f.source)
        ref.update(path="unselected-secret.bin", sha256=digest(b"Unselected fixture sentinel; must never be read.\n"))
        f.envelopes["primary"]["upstream"] = [ref]
        f.write("primary")
        self.rejected(f)
        g = self.fixture()
        g.source_envelopes["source"]["upstream"] = [ref]
        g.rewrite_source(g.source_bodies["source"])
        self.checked(g)

    def test_duplicate_catalog_variant_and_current_slot_substitution_refuse(self):
        for variant in ("duplicate", "current-slot"):
            with self.subTest(case=variant):
                f = self.fixture()
                if variant == "duplicate":
                    f.contract["reference_catalog"].append(copy.deepcopy(f.source_row()))
                else:
                    raw = f.paths["primary"].read_bytes()
                    physical = f.operator / "fixed" / "current-copy.md"
                    physical.write_bytes(raw)
                    f.contract["reference_catalog"].append({
                        "store": "project", "path": "docs/ideas.md", "kind": "artifact",
                        "identity": {"artifact_id": "IDEAS-001", "artifact_type": "idea-ledger",
                                     "project_id": "synthetic-project", "revision": 2},
                        "source": {"kind": "fixed", "physical_path": str(physical), "sha256": digest(raw)},
                    })
                f.write_contract()
                self.rejected(f)

    def test_strict_produced_identity_revision_and_required_envelope_types(self):
        variants = {
            "wrong-project": lambda e: e.update(project_id="unselected-project"),
            "wrong-prefix": lambda e: e.update(artifact_id="PROD-001"),
            "unsupported-prefix": lambda e: e.update(artifact_id="RECEIPT-001"),
            "zero-id": lambda e: e.update(artifact_id="IDEAS-000"),
            "unicode-digits": lambda e: e.update(artifact_id="IDEAS-١"),
            "wrong-type": lambda e: e.update(artifact_type="product-brief"),
            "missing-producer": lambda e: e.pop("producer"),
            "wrong-producer": lambda e: e.update(producer="worker"),
            "missing-project": lambda e: e.pop("project_id"),
            "wrong-upstream": lambda e: e.update(upstream={}),
            "wrong-evidence": lambda e: e.update(evidence="none"),
            "wrong-missing": lambda e: e.update(missing_inputs={}),
            "null-execution": lambda e: e.update(execution_ref=None),
        }
        for value in (True, 0, -1, 1.0, "1", None):
            variants["revision-" + repr(value)] = lambda e, value=value: e.update(revision=value)
        for name, mutate in variants.items():
            with self.subTest(case=name):
                f = self.fixture()
                mutate(f.envelopes["secondary"])
                f.write("secondary")
                self.rejected(f)

    def test_catalog_has_closed_shape_and_exact_canonical_file_selection(self):
        variants = {
            "extra-entry-key": lambda r: r.update(title="Not an identity"),
            "wrong-store": lambda r: r.update(store="latest"),
            "absolute-logical": lambda r: r.update(path="/sources/product.md"),
            "parent-logical": lambda r: r.update(path="../sources/product.md"),
            "dot-logical": lambda r: r.update(path="sources/./product.md"),
            "unknown-kind": lambda r: r.update(kind="native-receipt"),
            "missing-identity": lambda r: r.update(identity=None),
            "identity-extra": lambda r: r["identity"].update(receipt_id="LOCAL-1"),
            "identity-bool": lambda r: r["identity"].update(revision=True),
            "source-extra": lambda r: r["source"].update(root="/tmp"),
            "source-kind": lambda r: r["source"].update(kind="latest"),
            "relative-physical": lambda r: r["source"].update(physical_path="fixed/source.md"),
            "noncanonical-physical": lambda r: r["source"].update(
                physical_path=r["source"]["physical_path"].replace("/fixed/", "/fixed/../fixed/")),
            "source-short-hash": lambda r: r["source"].update(sha256="a" * 63),
            "source-upper-hash": lambda r: r["source"].update(sha256="A" * 64),
        }
        for name, mutate in variants.items():
            with self.subTest(case=name):
                f = self.fixture()
                mutate(f.source_row())
                f.write_contract()
                self.rejected(f)

    def test_all_declared_canonical_source_id_type_pairs_are_supported(self):
        pairs = (
            ("IDEAS", "idea-ledger"), ("HANDOFF", "handoff"), ("SESSION", "session-record"),
            ("PROD", "product-brief"), ("UX", "design-spec"), ("XPLAN", "experiment-plan"),
            ("XREPORT", "prototype-report"), ("ARCH", "architecture-contract"), ("EPIC", "epic"),
            ("STORY", "story"), ("XSPEC", "expert-spec"), ("XPKG", "expert-package"),
            ("EVPLAN", "expert-evaluation-plan"), ("EVREPORT", "expert-evaluation-report"),
            ("DEV", "development-record"), ("QA", "review-report"), ("REL", "release-record"),
            ("CHG", "change-request"), ("SEVAL", "skill-evaluation-report"),
        )
        for prefix, role in pairs:
            with self.subTest(prefix=prefix, role=role):
                f = self.fixture()
                ref = f.add_artifact("typed-source", "authority", "typed/source.md", prefix + "-123", role,
                                     "## SOURCE-001\n\nA selected typed source.\n", sections=["SOURCE-001"])
                f.envelopes["secondary"]["evidence"] = [ref]
                f.write("secondary")
                f.write_contract()
                self.checked(f)

    def test_invalid_source_id_profile_refuses_even_when_all_identity_copies_agree(self):
        for identity in ("RECEIPT-001", "PROD-000", "PROD-١", "prod-001", "PROD-1x", "IDEAS-001"):
            with self.subTest(identity=identity):
                f = self.fixture()
                f.source_envelopes["source"]["artifact_id"] = identity
                f.source_row()["identity"]["artifact_id"] = identity
                f.source["artifact_id"] = identity
                f.envelopes["primary"]["upstream"][0]["artifact_id"] = identity
                f.rewrite_source(f.source_bodies["source"])
                self.rejected(f)

    def test_catalog_limit_128_entries_is_not_silently_truncated(self):
        f = self.fixture()
        for number in range(126):
            f.add_raw("selected-" + str(number), ("Evidence " + str(number)).encode())
        f.write_contract()
        self.assertEqual(len(f.contract["reference_catalog"]), 128)
        self.checked(f)
        f.add_raw("overflow", b"This would be the 129th catalog entry.")
        f.write_contract()
        self.rejected(f)

    def test_fixed_missing_directory_symlink_and_hardlink_never_fall_back(self):
        for variant in ("missing", "directory", "symlink", "hardlink"):
            with self.subTest(case=variant):
                f = self.fixture()
                physical = Path(f.source_row()["source"]["physical_path"])
                original = physical.read_bytes()
                physical.unlink()
                if variant == "directory":
                    physical.mkdir()
                elif variant == "symlink":
                    f.unselected_path.write_bytes(original)
                    physical.symlink_to(f.unselected_path)
                elif variant == "hardlink":
                    f.unselected_path.write_bytes(original)
                    os.link(f.unselected_path, physical)
                # A matching fallback exists at the logical project location;
                # it remains unselected and must not be opened.
                fallback = f.project / f.source["path"]
                fallback.parent.mkdir()
                fallback.write_bytes(original)
                self.rejected(f, UNAVAILABLE if variant == "missing" else "FAIL")

    def test_exact_supported_id_profile_and_disclosed_fixed_cross_project(self):
        f = self.fixture()
        f.envelopes["secondary"]["artifact_id"] = "IDEAS-0002"
        f.output_row("secondary")["artifact_id"] = "IDEAS-0002"
        f.write("secondary")
        f.source_envelopes["source"]["project_id"] = "disclosed-other-project"
        f.source_row()["identity"]["project_id"] = "disclosed-other-project"
        f.rewrite_source(f.source_bodies["source"])
        f.write_contract()
        self.checked(f)

    def test_reference_tuple_types_unknown_keys_and_nested_reserved_objects(self):
        variants = {
            "wrong-id": lambda r: r.update(artifact_id="PROD-002"),
            "wrong-type": lambda r: r.update(artifact_type="session-record"),
            "wrong-project": lambda r: r.update(project_id="elsewhere"),
            "wrong-store": lambda r: r.update(store="authority"),
            "short-hash": lambda r: r.update(sha256="a" * 63),
            "upper-hash": lambda r: r.update(sha256="A" * 64),
            "unknown-key": lambda r: r.update(accepted=True),
            "unknown-kind": lambda r: r.update(evidence_kind="closest-file"),
            "mixed-raw": lambda r: r.update(evidence_kind="raw-file"),
            "sections-string": lambda r: r.update(sections="SRC-001"),
            "sections-duplicate": lambda r: r.update(sections=["SRC-001", "SRC-001"]),
            "sections-null": lambda r: r.update(sections=None),
        }
        for value in (True, 0, -1, 1.0, "1", None):
            variants["revision-" + repr(value)] = lambda r, value=value: r.update(revision=value)
        for name, mutate in variants.items():
            with self.subTest(case=name):
                f = self.fixture()
                mutate(f.envelopes["primary"]["upstream"][0])
                f.write("primary")
                self.rejected(f)
        f = self.fixture()
        f.envelopes["secondary"]["extension"] = {"nested": [{"path": "unselected-secret.bin", "sha256": "0" * 64}]}
        f.write("secondary")
        self.rejected(f)

    def test_fixed_metadata_must_match_selection_even_with_exact_hash(self):
        for field, value in (("artifact_id", "PROD-002"), ("artifact_type", "handoff"),
                             ("project_id", "other"), ("revision", True), ("revision", "1")):
            with self.subTest(field=field, value=value):
                f = self.fixture()
                f.source_envelopes["source"][field] = value
                f.rewrite_source(f.source_bodies["source"])
                self.rejected(f)

    def test_raw_file_cannot_evade_claimed_artifact_envelope_or_promote_receipt(self):
        for variant in ("raw-artifact", "promoted-receipt"):
            with self.subTest(case=variant):
                f = self.fixture()
                if variant == "raw-artifact":
                    raw = artifact_bytes(f.envelope("PROD-010", "product-brief"), "## S-001\n\nClaimed artifact.\n")
                    ref = f.add_raw("evasion", raw)
                else:
                    ref = f.add_raw("receipt", b'{"schema_version":"devforge.skill-run/v1","receipt_id":"NATIVE-1"}\n')
                    row = f.contract["reference_catalog"][-1]
                    row.update(kind="artifact", identity={"artifact_id": "SESSION-009", "artifact_type": "session-record",
                                                          "project_id": "synthetic-project", "revision": 1})
                    ref = {"artifact_id": "SESSION-009", "revision": 1, "store": ref["store"],
                           "path": ref["path"], "sha256": ref["sha256"], "sections": []}
                f.envelopes["secondary"]["evidence"] = [ref]
                f.write("secondary")
                f.write_contract()
                self.rejected(f)

    def test_duplicate_json_and_yaml_envelope_keys_refuse(self):
        for syntax in ("json", "yaml"):
            with self.subTest(syntax=syntax):
                f = self.fixture()
                env = f.envelopes["secondary"]
                if syntax == "json":
                    raw = artifact_bytes(env, f.bodies["secondary"])
                    raw = raw.replace(b'"revision": 1,', b'"revision": 1, "revision": 1,', 1)
                else:
                    # JSON scalar/container values are valid YAML flow values.
                    lines = [key + ": " + json.dumps(value) for key, value in env.items()]
                    lines.append("revision: 1")
                    raw = ("---\n" + "\n".join(lines) + "\n---\n\n" + f.bodies["secondary"]).encode()
                f.paths["secondary"].write_bytes(raw)
                self.rejected(f)

    def test_produced_artifact_utf8_and_yaml_aliases_are_not_silently_repaired(self):
        for variant in ("invalid-utf8", "yaml-alias"):
            with self.subTest(case=variant):
                f = self.fixture()
                if variant == "invalid-utf8":
                    raw = f.paths["secondary"].read_bytes() + b"\n\xff\n"
                else:
                    env = f.envelopes["secondary"]
                    lines = [key + ": " + json.dumps(value) for key, value in env.items()
                             if key not in {"evidence", "upstream"}]
                    lines.extend(["upstream: &empty []", "evidence: *empty"])
                    raw = ("---\n" + "\n".join(lines) + "\n---\n\n" + f.bodies["secondary"]).encode()
                f.paths["secondary"].write_bytes(raw)
                self.rejected(f)

    def test_real_atx_and_setext_populated_source_headings_pass(self):
        bodies = (
            "## Observed source [SRC-001]\n\nOne concrete observation.\n",
            "Observed source [SRC-001]\n-------------------------\n\nOne concrete observation.\n",
            "## SRC-001\r\n\r\nOne concrete observation.\r\n",
        )
        for body in bodies:
            with self.subTest(body=body):
                f = self.fixture()
                f.rewrite_source(body)
                self.checked(f)

    def test_section_lookalikes_empty_duplicate_and_headingless_without_disclosure_refuse(self):
        bodies = {
            "row-only": "| ID | Text |\n| --- | --- |\n| SRC-001 | Not a heading. |\n",
            "fenced": "```markdown\n## SRC-001\nPopulated lookalike.\n```\n",
            "indented": "    ## SRC-001\n    Populated lookalike.\n",
            "comment": "<!--\n## SRC-001\nPopulated lookalike.\n-->\n",
            "bullet": "- SRC-001: This is not a heading.\n",
            "empty": "## SRC-001\n\n## OTHER-001\n\nOnly the other section has content.\n",
            "duplicate": "## SRC-001\n\nFirst.\n\n## Again [SRC-001]\n\nSecond.\n",
            "unstable-heading": "## Observed source\n\nA title without a stable identifier.\n",
            "html-anchor": '<a id="SRC-001"></a>\n\nA raw anchor does not create a supported heading.\n',
            "unclosed-fence": "## SRC-001\n\n```\nStill open.\n",
        }
        for name, body in bodies.items():
            with self.subTest(case=name):
                f = self.fixture()
                f.rewrite_source(body)
                self.rejected(f)
        f = self.fixture()
        f.envelopes["primary"]["upstream"][0]["sections"] = []
        f.write("primary")
        self.rejected(f)

    def test_source_rows_require_exact_real_first_data_cell_and_causal_section(self):
        f = self.fixture()
        f.envelopes["primary"]["upstream"][0]["source_rows"] = ["I-001"]
        f.write("primary")
        self.checked(f)
        for variant in ("row-as-section", "no-section", "wrong-row", "duplicate-claim"):
            with self.subTest(case=variant):
                g = self.fixture()
                ref = g.envelopes["primary"]["upstream"][0]
                ref["source_rows"] = ["I-001"]
                if variant == "row-as-section":
                    ref["sections"] = ["I-001"]
                elif variant == "no-section":
                    ref["sections"] = []
                elif variant == "wrong-row":
                    ref["source_rows"] = ["I-002"]
                else:
                    ref["source_rows"] = ["I-001", "I-001"]
                g.write("primary")
                self.rejected(g)
        tables = {
            "second-cell": "| ID | Text |\n| --- | --- |\n| X-001 | I-001 |\n",
            "substring": "| ID | Text |\n| --- | --- |\n| I-001-extra | Other. |\n",
            "header": "| I-001 | Text |\n| --- | --- |\n| X-001 | Other. |\n",
            "duplicate": "| ID | Text |\n| --- | --- |\n| I-001 | First. |\n| I-001 | Second. |\n",
            "code": "```\n| ID | Text |\n| --- | --- |\n| I-001 | Hidden. |\n```\n",
            "comment": "<!--\n| ID | Text |\n| --- | --- |\n| I-001 | Hidden. |\n-->\n",
        }
        for name, table in tables.items():
            with self.subTest(case=name):
                g = self.fixture()
                g.rewrite_source("## SRC-001\n\nA populated section.\n\n" + table)
                g.envelopes["primary"]["upstream"][0]["source_rows"] = ["I-001"]
                g.write("primary")
                self.rejected(g)

    def unsectioned(self, f: Fixture) -> dict:
        f.rewrite_source("A preserved source with no real Markdown headings.\n")
        ref = f.envelopes["primary"]["upstream"][0]
        ref["sections"] = []
        disclosure = {"kind": "unsectioned-source", **{key: ref[key] for key in
                      ("store", "path", "artifact_id", "revision", "sha256")},
                      "reason": "Selected source has no actual headings."}
        f.envelopes["primary"]["missing_inputs"] = [disclosure]
        f.write("primary")
        return disclosure

    def test_heading_free_source_requires_exact_structured_disclosure(self):
        f = self.fixture()
        disclosure = self.unsectioned(f)
        result = self.checked(f)
        self.assertIn(disclosure["sha256"], scalar_values(result["reference_coverage"]["exceptions"]))
        for variant in ("missing", "free-text", "wrong-hash", "empty-reason", "extra-key", "null-sections", "real-heading"):
            with self.subTest(case=variant):
                g = self.fixture()
                item = self.unsectioned(g)
                if variant == "missing":
                    g.envelopes["primary"]["missing_inputs"] = []
                elif variant == "free-text":
                    g.envelopes["primary"]["missing_inputs"] = ["PROD-001 has no sections; use all."]
                elif variant == "wrong-hash":
                    item["sha256"] = "0" * 64
                elif variant == "empty-reason":
                    item["reason"] = ""
                elif variant == "extra-key":
                    item["approved"] = True
                elif variant == "null-sections":
                    g.envelopes["primary"]["upstream"][0]["sections"] = None
                else:
                    g.rewrite_source("## A real heading without stable ID\n\nSource text.\n")
                    item["sha256"] = g.source["sha256"]
                g.write("primary")
                self.rejected(g)

    def test_raw_empty_and_binary_evidence_are_not_artifacts(self):
        for raw in (b"", b"\x00\xffopaque binary evidence\n"):
            with self.subTest(raw=raw):
                f = self.fixture()
                ref = f.add_raw("opaque", raw)
                f.envelopes["secondary"]["evidence"] = [ref]
                f.append("secondary", reference_atom(ref))
                f.write_contract()
                self.checked(f)
                for field in ("upstream", "supersedes", "execution_ref", "decision_ref"):
                    with self.subTest(field=field):
                        g = self.fixture()
                        other = g.add_raw("opaque", raw)
                        g.envelopes["secondary"][field] = [other] if field == "upstream" else other
                        g.write("secondary")
                        g.write_contract()
                        self.rejected(g)

    def test_execution_session_type_and_unique_shorthand(self):
        f = self.fixture()
        f.envelopes["secondary"]["execution_ref"] = "SESSION-001@1"
        f.write("secondary")
        self.checked(f)
        for variant in ("wrong-type", "wrong-revision", "malformed-shorthand", "null"):
            with self.subTest(case=variant):
                g = self.fixture()
                g.envelopes["secondary"]["execution_ref"] = {
                    "wrong-type": copy.deepcopy(g.source), "wrong-revision": "SESSION-001@2",
                    "malformed-shorthand": "SESSION-001@true", "null": None,
                }[variant]
                g.write("secondary")
                self.rejected(g)

    def test_supersedes_requires_same_id_and_strictly_earlier_selected_revision(self):
        for variant in ("different-id", "same-revision", "later-revision"):
            with self.subTest(case=variant):
                f = self.fixture()
                identity = "IDEAS-002" if variant == "different-id" else "IDEAS-001"
                revision = {"different-id": 1, "same-revision": 2, "later-revision": 3}[variant]
                old = f.add_artifact("bad-prior", "authority", "history/prior.md", identity, "idea-ledger",
                                     "## PRIOR-001\n\nA selected prior-looking source.\n",
                                     revision=revision, sections=["PRIOR-001"])
                f.envelopes["primary"]["supersedes"] = old
                f.write("primary")
                f.write_contract()
                self.rejected(f)

    def test_external_research_requires_selected_snapshot_and_closed_shape(self):
        def selected_research(f):
            snapshot = f.add_raw("research", b"Synthetic provider documentation snapshot.\n")
            return {"evidence_kind": "external-research", "url": "https://example.invalid/reference",
                    "applicable_version": "synthetic-v1", "retrieved_at": "2026-09-01",
                    "claim": "The synthetic documentation describes one example.", "snapshot": snapshot}
        f = self.fixture()
        f.envelopes["secondary"]["evidence"] = [selected_research(f)]
        f.write("secondary")
        f.write_contract()
        self.checked(f)
        for variant in ("bare-url", "missing-snapshot", "unselected-snapshot", "wrong-hash", "wrong-date", "extra-key"):
            with self.subTest(case=variant):
                g = self.fixture()
                evidence = selected_research(g)
                if variant == "bare-url":
                    evidence = "https://example.invalid/reference"
                elif variant == "missing-snapshot":
                    evidence.pop("snapshot")
                elif variant == "unselected-snapshot":
                    evidence["snapshot"]["path"] = "unselected-secret.bin"
                elif variant == "wrong-hash":
                    evidence["snapshot"]["sha256"] = "0" * 64
                elif variant == "wrong-date":
                    evidence["retrieved_at"] = "yesterday"
                else:
                    evidence["approved"] = True
                g.envelopes["secondary"]["evidence"] = [evidence]
                g.write("secondary")
                g.write_contract()
                self.rejected(g)

    def test_every_identical_body_occurrence_is_retained(self):
        f = self.fixture()
        baseline = len(self.checked(f)["reference_coverage"]["occurrences"])
        claim = reference_atom(f.source)
        f.append("handoff", "First " + claim + "\n\nSecond `" + claim + "`.")
        report = self.checked(f)["reference_coverage"]
        self.assertEqual(len(report["occurrences"]), baseline + 2)
        self.assertGreaterEqual(scalar_values(report["occurrences"]).count(f.source["sha256"]), 2)

    def test_body_occurrence_locations_bind_utf8_bytes_and_exact_source_tuple(self):
        f = self.fixture()
        claim = reference_atom(f.source)
        f.append("handoff", "An accented prefix: café. " + claim + "\nRepeated " + claim)
        report = self.checked(f)["reference_coverage"]
        document = f.paths["handoff"].read_bytes()
        # The authored frontmatter delimiter fixes the parser's body origin.
        body = document.split(b"\n---\n", 1)[1]
        encoded = claim.encode("utf-8")
        first = body.index(encoded)
        second = body.index(encoded, first + len(encoded))
        expected_where = {"body:" + str(start) + ":" + str(start + len(encoded))
                          for start in (first, second)}
        occurrences = [row for row in report["occurrences"]
                       if row.get("document") == "docs/handoff.md"
                       and isinstance(row.get("where"), str) and row["where"].startswith("body:")
                       and row.get("field") == "evidence"]
        self.assertEqual(len(occurrences), 2)
        self.assertEqual({row["where"] for row in occurrences}, expected_where)
        source = f.source_row()
        for occurrence in occurrences:
            self.assertEqual(set(occurrence), {"document", "document_sha256", "where", "field",
                                               "target", "sections", "source_rows"})
            self.assertEqual(occurrence["document_sha256"], digest(document))
            self.assertEqual(occurrence["sections"], ["SRC-001"])
            self.assertEqual(occurrence["source_rows"], [])
            self.assertEqual(occurrence["target"], {
                "store": "project", "path": "sources/product.md", "kind": "artifact",
                "identity": source["identity"], "sha256": f.source["sha256"],
                "physical_path": source["source"]["physical_path"], "source_kind": "fixed",
            })

    def test_complete_atoms_remain_valid_in_tables_lists_code_and_comments(self):
        for wrapper in ("| Reference |\n| --- |\n| {} |", "- {}", "```text\n{}\n```",
                        "    {}", "<!-- {} -->"):
            with self.subTest(wrapper=wrapper):
                f = self.fixture()
                f.append("handoff", wrapper.format(reference_atom(f.source)))
                self.checked(f)

    def test_later_bad_repetition_never_hides_behind_first_good(self):
        for wrapper in ("{}", "```text\n{}\n```", "<!-- {} -->"):
            with self.subTest(wrapper=wrapper):
                f = self.fixture()
                good = reference_atom(f.source)
                bad = copy.deepcopy(f.source)
                bad["sha256"] = "0" * 64
                f.append("handoff", good + "\n\n" + wrapper.format(reference_atom(bad)))
                self.rejected(f)

    def test_swapped_and_long_locator_hashes_cannot_use_nearest_digest(self):
        for long in (False, True):
            with self.subTest(long_locator=long):
                f = self.fixture()
                path = "evidence/" + ("segment/" * 50 if long else "") + "first.bin"
                first = f.add_raw("first", b"First exact evidence bytes.\n", logical=path)
                second = f.add_raw("second", b"Second distinct evidence bytes.\n")
                first["sha256"], second["sha256"] = second["sha256"], first["sha256"]
                f.append("handoff", reference_atom(first) + " " + reference_atom(second))
                f.write_contract()
                self.rejected(f)

    def test_malformed_atoms_unknown_fields_and_duplicate_json_keys_refuse(self):
        for variant in ("truncated-hash", "upper-hash", "duplicate-key", "unknown-key", "unknown-field",
                        "raw-output", "newline", "missing-close", "unknown-marker", "trailing-object"):
            with self.subTest(case=variant):
                f = self.fixture()
                ref = copy.deepcopy(f.source)
                claim = reference_atom(ref)
                if variant == "truncated-hash":
                    ref["sha256"] = ref["sha256"][:32]
                    claim = reference_atom(ref)
                elif variant == "upper-hash":
                    ref["sha256"] = ref["sha256"].upper()
                    claim = reference_atom(ref)
                elif variant == "duplicate-key":
                    claim = claim.replace('{"field":', '{"field":"evidence","field":', 1)
                elif variant == "unknown-key":
                    ref["accepted"] = True
                    claim = reference_atom(ref)
                elif variant == "unknown-field":
                    claim = reference_atom(ref, "receipt")
                elif variant == "raw-output":
                    claim = reference_atom(f.add_raw("raw-output", b"Raw bytes.\n"), "output")
                elif variant == "newline":
                    claim = claim.replace(",", ",\n", 1)
                elif variant == "missing-close":
                    claim = claim[:-1]
                elif variant == "unknown-marker":
                    claim = '@df-ref({"path":"unselected-secret.bin",BROKEN})'
                else:
                    claim = claim[:-1] + '{"other":true})'
                f.append("handoff", claim)
                f.write_contract()
                self.rejected(f)

    def test_reserved_body_claims_and_legacy_split_receipt_tables_refuse(self):
        for variant in ("digest", "long-digest", "label", "id-revision", "malformed-revision", "locator",
                        "selected-path", "table", "comment", "code"):
            with self.subTest(case=variant):
                f = self.fixture()
                fragments = {
                    "digest": f.source["sha256"], "long-digest": "a" * 65,
                    "label": "SHA-256: truncated", "id-revision": "PROD-001@1",
                    "malformed-revision": "PROD-001@not-a-revision", "locator": "authority:anywhere",
                    "selected-path": "sources/product.md",
                    "table": "| File | `SHA-256` |\n| --- | --- |\n| unrelated | short |",
                    "comment": "<!-- sha256=bad -->", "code": "```text\nproject:unknown\n```",
                }
                f.append("handoff", fragments[variant])
                self.rejected(f)

    def test_plain_narrative_ids_urls_and_unrelated_examples_are_not_byte_claims(self):
        f = self.fixture()
        f.append("handoff", "PROD-001 is a narrative identifier. See https://example.invalid/guide. "
                 "An unrelated example is sketches/example.txt.")
        self.checked(f)

    def test_body_causal_atoms_must_agree_with_the_corresponding_envelope(self):
        for field in ("upstream", "execution_ref", "decision_ref", "supersedes"):
            with self.subTest(field=field):
                f = self.fixture()
                ref = f.source
                if field == "execution_ref":
                    ref = f.add_artifact("other-session", "authority", "sessions/other.md", "SESSION-002", "session-record",
                                         "## SESSION-SECTION-002\n\nAnother selected session.\n",
                                         sections=["SESSION-SECTION-002"])
                elif field == "supersedes":
                    f.envelopes["secondary"]["revision"] = 2
                    f.output_row("secondary")["revision"] = 2
                    ref = f.add_artifact("other-prior", "authority", "history/retained-r1.md", "IDEAS-002", "idea-ledger",
                                         "## RETAINED-001\n\nSelected older same-ID bytes.\n", sections=["RETAINED-001"])
                # This tuple is selected, complete, and valid for its field,
                # but absent from the corresponding envelope declaration.
                f.append("secondary", reference_atom(ref, field))
                f.write_contract()
                self.rejected(f)
        f = self.fixture()
        f.append("primary", reference_atom(f.source, "upstream"))
        f.append("secondary", reference_atom(f.session, "execution_ref"))
        f.envelopes["secondary"]["decision_ref"] = copy.deepcopy(f.source)
        f.append("secondary", reference_atom(f.source, "decision_ref"))
        self.checked(f)

    def test_handoff_delivery_state_and_noncausal_self_location_pass(self):
        f = self.fixture()
        state = {"task_id": f.contract["task_id"], **dict.fromkeys(DELIVERY_FIELDS, "NOT_RUN")}
        f.append("handoff", atom(f.link("handoff", "noncausal"), "link") + "\n" + atom(state, "delivery") + "\n" + atom(state, "delivery"))
        before = f.paths["handoff"].read_bytes()
        self.checked(f)
        self.finalized(f)
        self.assertEqual(f.paths["handoff"].read_bytes(), before)

    def test_each_premature_handoff_state_and_wrong_body_delivery_claim_refuses(self):
        variants = list(DELIVERY_FIELDS) + ["missing-state", "missing-field", "extra-field", "wrong-body", "wrong-task", "reserved-key"]
        for variant in variants:
            with self.subTest(case=variant):
                f = self.fixture()
                if variant in DELIVERY_FIELDS:
                    f.envelopes["handoff"]["delivery_state"][variant] = "PASS"
                elif variant == "missing-state":
                    f.envelopes["handoff"].pop("delivery_state")
                elif variant == "missing-field":
                    f.envelopes["handoff"]["delivery_state"].pop("user_delivery")
                elif variant == "extra-field":
                    f.envelopes["handoff"]["delivery_state"]["accepted"] = "NOT_RUN"
                elif variant == "reserved-key":
                    f.bodies["handoff"] += "\nreceipt_readback remains pending.\n"
                else:
                    state = {"task_id": f.contract["task_id"], **dict.fromkeys(DELIVERY_FIELDS, "NOT_RUN")}
                    if variant == "wrong-body":
                        state["receipt_readback"] = "PASS"
                    else:
                        state["task_id"] = "OTHER-TASK"
                    f.bodies["handoff"] += "\n" + atom(state, "delivery") + "\n"
                f.write("handoff")
                self.rejected(f)

    def test_current_self_identity_is_rejected_before_any_digest_fixed_point(self):
        for variant in ("body-output", "envelope-evidence", "fixed-alias"):
            with self.subTest(case=variant):
                f = self.fixture()
                ref = f.output_ref("handoff")
                if variant == "fixed-alias":
                    ref = f.add_artifact("self-alias", "authority", "aliases/self.md", "HANDOFF-001", "handoff",
                                         "## NEXT-001\n\nAn aliased current identity.\n", sections=["NEXT-001"])
                if variant == "envelope-evidence":
                    f.envelopes["handoff"]["evidence"] = [ref]
                    f.write("handoff")
                else:
                    f.append("handoff", reference_atom(ref, "output" if variant == "body-output" else "evidence"))
                f.write_contract()
                self.rejected(f)

    def test_historical_raw_receipt_is_permitted_but_future_destination_is_not(self):
        f = self.fixture()
        historic = f.add_raw("historical-receipt", b'{"schema_version":"devforge.skill-run/v1","result":"historical"}\n')
        f.envelopes["handoff"]["evidence"] = [historic]
        f.write("handoff")
        f.write_contract()
        self.checked(f)
        g = self.fixture()
        future = g.add_raw("future-receipt", b"Preexisting bytes cannot occupy this task's receipt destination.\n")
        row = g.contract["reference_catalog"][-1]
        # The requested final receipt collides with a selected fixed source;
        # do not overwrite or repurpose those existing bytes.
        g.receipt_path = Path(row["source"]["physical_path"])
        original = g.receipt_path.read_bytes()
        g.envelopes["handoff"]["evidence"] = [future]
        g.write("handoff")
        g.write_contract()
        with self.selected_reads_only(g):
            self.result(core.finalize(g.contract_path, g.receipt_path), "FAIL")
        self.assertEqual(g.receipt_path.read_bytes(), original)

    def test_handoff_may_supersede_selected_same_identity_older_bytes(self):
        f = self.fixture()
        f.envelopes["handoff"]["revision"] = 2
        f.output_row("handoff")["revision"] = 2
        older = f.add_artifact("old-handoff", "project", "docs/handoff.md", "HANDOFF-001", "handoff",
                               "## NEXT-001\n\nAn earlier preserved continuation.\n", sections=["NEXT-001"])
        f.envelopes["handoff"]["supersedes"] = older
        f.append("handoff", reference_atom(older, "supersedes"))
        f.write_contract()
        self.checked(f)
        self.finalized(f)

    def test_record_checks_all_ledgers_before_future_handoff_is_available(self):
        f = self.fixture()
        f.append("primary", atom(f.link("handoff"), "link"))
        f.paths["handoff"].unlink()
        with self.selected_reads_only(f) as observed:
            actual = self.result(core.check_record(f.contract_path))
        self.assertEqual(actual["outputs"], f.expected_outputs(record=True))
        self.assertEqual(set(actual["reference_coverage"]["outputs"]), {"docs/ideas.md", "docs/retained.md"})
        self.assertNotIn(str(f.paths["handoff"]), observed, "Planned link grants no future handoff read")
        self.rejected(f)

    def test_record_refuses_second_ledger_reference_and_future_causal_handoff(self):
        for variant in ("secondary-reference", "future-causal"):
            with self.subTest(case=variant):
                f = self.fixture()
                if variant == "secondary-reference":
                    bad = copy.deepcopy(f.source)
                    bad["sha256"] = "0" * 64
                    f.envelopes["secondary"]["upstream"] = [bad]
                    f.write("secondary")
                else:
                    f.envelopes["secondary"]["upstream"] = [f.output_ref("handoff")]
                    f.write("secondary")
                f.paths["handoff"].unlink()
                self.rejected(f, record=True)

    def test_planned_link_cannot_replace_required_handoff_upstream(self):
        f = self.fixture()
        f.envelopes["handoff"]["upstream"] = []
        f.append("handoff", atom(f.link("primary", "noncausal"), "link"))
        self.rejected(f)

    def test_noncausal_link_grammar_never_infers_an_unselected_target(self):
        for variant in ("unknown-relation", "hash", "unselected", "bool-revision", "wrong-id", "duplicate-key"):
            with self.subTest(case=variant):
                f = self.fixture()
                link = f.link("handoff")
                if variant == "unknown-relation":
                    link["relation"] = "causal"
                elif variant == "hash":
                    link["sha256"] = "0" * 64
                elif variant == "unselected":
                    link["path"] = "unselected-secret.bin"
                elif variant == "bool-revision":
                    link["revision"] = True
                elif variant == "wrong-id":
                    link["artifact_id"] = "HANDOFF-002"
                text = atom(link, "link")
                if variant == "duplicate-key":
                    text = text.replace('{"store":', '{"store":"project","store":', 1)
                f.append("primary", text)
                self.rejected(f)

    def test_receipt_overflow_refuses_without_dropping_repeated_occurrences(self):
        f = self.fixture()
        claim = reference_atom(f.source)
        f.append("handoff", "\n".join([claim] * 900))
        checked = self.checked(f)
        self.assertGreaterEqual(len(checked["reference_coverage"]["occurrences"]), 900)
        with self.selected_reads_only(f):
            self.result(core.finalize(f.contract_path, f.receipt_path), "FAIL")
        self.assertFalse(f.receipt_path.exists())

    def test_receipt_coverage_drift_and_current_or_fixed_bytes_invalidate_verify(self):
        for variant in ("coverage", "current-output", "fixed-source", "preimage"):
            with self.subTest(case=variant):
                f = self.fixture()
                if variant == "preimage":
                    f.historical_primary(preimage=True)
                receipt, original = self.finalized(f)
                if variant == "coverage":
                    receipt["reference_coverage"]["occurrences"] = []
                    f.receipt_path.write_bytes(json_bytes(receipt))
                elif variant == "current-output":
                    f.paths["secondary"].write_bytes(f.paths["secondary"].read_bytes() + b"\nLater drift.\n")
                else:
                    row = f.contract["reference_catalog"][-1] if variant == "preimage" else f.source_row()
                    target = Path(row["source"]["physical_path"])
                    target.write_bytes(target.read_bytes() + b"\nLater source drift.\n")
                frozen = f.receipt_path.read_bytes()
                with self.selected_reads_only(f):
                    self.result(core.verify(f.contract_path, f.receipt_path), "FAIL")
                self.assertEqual(f.receipt_path.read_bytes(), frozen)
                if variant != "coverage":
                    self.assertEqual(frozen, original)

    def test_legacy_v1_keeps_its_original_receipt_scope(self):
        f = self.fixture()
        f.contract["schema_version"] = "devforge.delivery-task/v1"
        for key in ("project_id", "primary_ledger_path", "reference_catalog"):
            f.contract.pop(key)
        f.contract["outputs"] = [row for row in f.contract["outputs"] if row["path"] != "docs/retained.md"]
        f.envelopes["primary"]["execution_ref"] = None
        f.envelopes["primary"]["upstream"] = []
        f.envelopes["handoff"]["execution_ref"] = None
        f.envelopes["handoff"].pop("delivery_state")
        f.write("primary")
        f.write_contract()
        with self.selected_reads_only(f):
            self.result(core.prepare(f.contract_path))
            checked = self.result(core.check(f.contract_path))
            self.result(core.finalize(f.contract_path, f.receipt_path))
            self.result(core.verify(f.contract_path, f.receipt_path))
        receipt = json.loads(f.receipt_path.read_bytes())
        self.assertEqual(receipt["schema_version"], "devforge.delivery-receipt/v1")
        self.assertNotIn("reference_profile", receipt)
        self.assertNotIn("reference_coverage", receipt)
        self.assertNotIn("reference_coverage", checked)
        self.assertNotIn(PROFILE, scalar_values(receipt))

    def test_v1_additive_catalog_and_v1_receipt_under_v2_refuse(self):
        f = self.fixture()
        f.contract["schema_version"] = "devforge.delivery-task/v1"
        f.contract.pop("project_id")
        f.contract.pop("primary_ledger_path")
        f.contract["outputs"] = [row for row in f.contract["outputs"] if row["path"] != "docs/retained.md"]
        f.write_contract()
        self.rejected(f)
        g = self.fixture()
        receipt, _ = self.finalized(g)
        receipt["schema_version"] = "devforge.delivery-receipt/v1"
        receipt.pop("reference_profile")
        receipt.pop("reference_coverage")
        g.receipt_path.write_bytes(json_bytes(receipt))
        original = g.receipt_path.read_bytes()
        with self.selected_reads_only(g):
            self.result(core.verify(g.contract_path, g.receipt_path), "FAIL")
        self.assertEqual(g.receipt_path.read_bytes(), original)


    # R2 expectations were frozen before these source edits in
    # reference-repair-r2-20260907T145928067385Z/fixture-authoring/EXPECTED-OUTCOMES.md.
    # This author did not read the concurrent production repair or execute tests.
    def test_r2_complete_envelope_references_require_sections(self):
        for field in ("upstream", "execution_ref", "decision_ref", "evidence"):
            with self.subTest(field=field):
                f = self.fixture()
                ref = copy.deepcopy(f.session if field == "execution_ref" else f.source)
                f.envelopes["secondary"][field] = [ref] if field in ("upstream", "evidence") else ref
                f.write("secondary")
                self.checked(f)
                del ref["sections"]
                f.write("secondary")
                self.rejected(f)

    def test_r2_complete_body_references_require_sections(self):
        for field in ("upstream", "execution_ref", "decision_ref", "evidence", "output"):
            with self.subTest(field=field):
                f = self.fixture()
                ref = copy.deepcopy(f.session if field == "execution_ref" else
                                    f.output_ref("primary") if field == "output" else f.source)
                if field != "upstream":
                    # The missing-key negative must not rely on a mismatched
                    # nonempty envelope section list as its reason to refuse.
                    ref["sections"] = []
                if field in ("upstream", "execution_ref", "decision_ref"):
                    f.envelopes["secondary"][field] = [copy.deepcopy(ref)] if field == "upstream" else copy.deepcopy(ref)
                original_body = f.bodies["secondary"]
                f.append("secondary", reference_atom(ref, field))
                self.checked(f)
                del ref["sections"]
                f.bodies["secondary"] = original_body
                f.append("secondary", reference_atom(ref, field))
                self.rejected(f)

    def test_r2_supersedes_omission_and_session_shorthand_remain_valid(self):
        f = self.fixture()
        old = f.historical_primary()
        old.pop("sections")
        f.envelopes["primary"]["supersedes"] = copy.deepcopy(old)
        f.envelopes["primary"]["execution_ref"] = "SESSION-001@1"
        session = {**f.session, "sections": []}
        f.append("primary", reference_atom(old, "supersedes") + "\n" +
                 reference_atom(session, "execution_ref"))
        self.checked(f)

    def test_r2_source_rows_accept_optional_outer_table_pipes(self):
        for leading, trailing in (("", "|"), ("|", ""), ("", ""), ("|", "|")):
            with self.subTest(leading=leading, trailing=trailing):
                f = self.fixture()
                rows = (" ID | Observation ", " --- | --- ", " I-001 | A selected observation. ")
                table = "\n".join(leading + row + trailing for row in rows) + "\n"
                f.rewrite_source("## SRC-001\n\nA populated source.\n\n" + table)
                f.envelopes["primary"]["upstream"][0]["source_rows"] = ["I-001"]
                f.write("primary")
                self.checked(f)

    def test_r2_legacy_sha_tables_refuse_each_border_form_in_code_and_comments(self):
        for leading, trailing in (("", "|"), ("|", ""), ("", ""), ("|", "|")):
            for label in ("SHA-256", "`sHa256`"):
                for wrapper in ("{}", "```text\n{}\n```", "<!--\n{}\n-->"):
                    with self.subTest(leading=leading, trailing=trailing, label=label, wrapper=wrapper):
                        f = self.fixture()
                        rows = (" File | " + label + " ", " --- | --- ", " unrelated | short ")
                        table = "\n".join(leading + row + trailing for row in rows)
                        f.append("handoff", wrapper.format(table))
                        self.rejected(f)

    def test_r2_source_rows_do_not_inherit_headers_across_blocks(self):
        header = "| ID | Observation |\n| --- | --- |\n"
        blocks = {
            "fenced": "```text\nA code block ends the earlier table.\n```\n",
            "indented": "    An indented code block ends the earlier table.\n",
            "comment": "<!--\nA comment block ends the earlier table.\n-->\n",
        }
        for name, block in blocks.items():
            for new_header in (False, True):
                with self.subTest(block=name, new_header=new_header):
                    f = self.fixture()
                    tail = (header if new_header else "") + "| I-001 | A later observation. |\n"
                    f.rewrite_source("## SRC-001\n\nA populated source.\n\n" + header + block + tail)
                    f.envelopes["primary"]["upstream"][0]["source_rows"] = ["I-001"]
                    f.write("primary")
                    if new_header:
                        self.checked(f)
                    else:
                        self.rejected(f)

    def r2_metadata_fixture(self):
        """Build literal free-text slots without using production for expected values."""
        f = self.fixture()
        disclosure = self.unsectioned(f)
        snapshot = f.add_raw("metadata-snapshot", b"Synthetic metadata research snapshot.\n")
        research = {"evidence_kind": "external-research", "url": "https://example.invalid/r2",
                    "applicable_version": "synthetic-r2", "retrieved_at": "2026-09-07",
                    "claim": "A synthetic research observation.", "snapshot": snapshot}
        env = f.envelopes["primary"]
        env["evidence"] = [research]
        env["missing_inputs"].append("One ordinary unknown remains.")
        slots = [
            (env["producer"], "skill", "$.producer.skill"),
            (env["producer"], "skill_revision", "$.producer.skill_revision"),
            (disclosure, "reason", "$.missing_inputs[0].reason"),
            (env["missing_inputs"], 1, "$.missing_inputs[1]"),
            (research, "claim", "$.evidence[0].claim"),
            (research, "applicable_version", "$.evidence[0].applicable_version"),
        ]
        f.write("primary")
        f.write_contract()
        return f, slots

    def test_r2_free_metadata_refuses_malformed_and_wrong_target_atoms(self):
        for position in range(6):
            for malformed_json in (True, False):
                with self.subTest(position=position, malformed_json=malformed_json):
                    f, slots = self.r2_metadata_fixture()
                    self.checked(f)
                    container, key, _ = slots[position]
                    container[key] = ("@df-ref({BROKEN})" if malformed_json else
                                      reference_atom({**f.session, "sha256": "0" * 64}))
                    f.write("primary")
                    self.rejected(f)

    def test_r2_valid_metadata_atoms_are_individually_enumerated(self):
        f, slots = self.r2_metadata_fixture()
        claim = reference_atom(f.session)
        expected_strings = {}
        expected_locations = []
        for position, (container, key, location) in enumerate(slots):
            value = "\u00e9 " + claim
            expected_locations.append(location)
            if position == 1:
                value += " " + claim
                expected_locations.append(location)
            container[key] = value
            expected_strings[location] = value
        f.write("primary")
        checked = self.checked(f)
        occurrences = [item for item in checked["reference_coverage"]["occurrences"]
                       if item["document"] == "docs/ideas.md"
                       and item["where"].rsplit(":", 2)[0] in expected_strings]
        self.assertEqual(len(occurrences), 7, "Every local metadata repetition needs its own observation")
        self.assertCountEqual([item["where"].rsplit(":", 2)[0] for item in occurrences], expected_locations)
        for item in occurrences:
            location, start, end = item["where"].rsplit(":", 2)
            self.assertEqual(expected_strings[location].encode("utf-8")[int(start):int(end)], claim.encode("utf-8"))
            self.assertEqual(item["document_sha256"], digest(f.paths["primary"].read_bytes()))
            self.assertEqual(item["field"], "evidence")
            self.assertEqual(item["target"]["identity"]["artifact_id"], "SESSION-001")
            self.assertEqual(item["target"]["sha256"], f.session["sha256"])
            self.assertEqual(item["target"]["path"], f.session["path"])

    def test_r2_ordinary_metadata_and_structured_tuple_strings_are_not_body_text(self):
        f, _ = self.r2_metadata_fixture()
        f.envelopes["primary"]["producer"]["skill_revision"] = "sha256:" + f.session["sha256"]
        raw = f.add_raw("marker-path", b"Synthetic explicitly selected raw tuple.\n",
                        logical="evidence/@df-ref({BROKEN}).bin")
        f.envelopes["primary"]["evidence"].append(raw)
        f.write("primary")
        f.write_contract()
        checked = self.checked(f)
        matches = [item for item in checked["reference_coverage"]["occurrences"]
                   if item["document"] == "docs/ideas.md" and item["field"] == "evidence"
                   and item.get("target", {}).get("path") == raw["path"]]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["target"]["kind"], "raw-file")
        self.assertEqual(matches[0]["target"]["sha256"], raw["sha256"])


    # Parent-reported adjacent triggers were frozen separately in
    # fixture-authoring/EXPECTED-OUTCOMES-SUPPLEMENT.md before these edits.
    def test_r2_ordinary_missing_input_hash_label_is_metadata_but_atoms_are_checked(self):
        f = self.fixture()
        f.envelopes["secondary"]["missing_inputs"] = ["sha256:" + f.session["sha256"]]
        f.write("secondary")
        self.checked(f)
        f.envelopes["secondary"]["missing_inputs"][0] += " @df-ref({BROKEN})"
        f.write("secondary")
        self.rejected(f)

    def test_r2_atx_heading_with_pipe_is_not_a_source_table_header(self):
        for genuine_table in (False, True):
            with self.subTest(genuine_table=genuine_table):
                f = self.fixture()
                body = "# [SRC-001] Name | Value\n"
                if genuine_table:
                    body += "\nID | Observation\n"
                body += "--- | ---\nI-001 | payload\n"
                f.rewrite_source(body)
                f.envelopes["primary"]["upstream"][0]["source_rows"] = ["I-001"]
                f.write("primary")
                if genuine_table:
                    self.checked(f)
                else:
                    self.rejected(f)


if __name__ == "__main__":
    unittest.main()
