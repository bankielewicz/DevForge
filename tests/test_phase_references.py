"""Independent v2 phase-reference fixtures, authored but not executed by author.

Expectations were frozen in reference-phase-fixtures-20260907T122923Z before
this module was written, from contract fc7d5901...07b90 and the v1 baseline.
No new phase implementation was read to derive expected outcomes. Fixture
hashes bind independently authored bytes; no native client/worker is involved.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
import test_phase_state as legacy  # noqa: E402

phase = legacy.phase
NOW = legacy.NOW
PROJECT = "synthetic-phase-reference-project"
PROFILE = "devforge.reference-coverage/v1"
PENDING = {key: "NOT_RUN" for key in (
    "final_artifact_check", "receipt_publication", "receipt_readback",
    "target_verification", "user_delivery")}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def packed(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def artifact(identity, role, body, *, revision=1, execution=None, upstream=None,
             supersedes=None, handoff=False) -> bytes:
    envelope = {
        "schema_version": "devforge.artifact/v1", "artifact_id": identity,
        "artifact_type": role, "project_id": PROJECT, "revision": revision,
        "status": "draft", "created_at_utc": "2026-09-07T12:00:00Z",
        "producer": {"skill": "synthetic-phase-fixture", "skill_revision": "independent-r1"},
        "execution_ref": execution, "upstream": list(upstream or []), "evidence": [],
        "supersedes": supersedes, "decision_ref": None,
        "missing_inputs": ["Synthetic fixture is not human acceptance."],
    }
    if handoff:
        envelope["delivery_state"] = dict(PENDING)
    return b"---\n" + packed(envelope) + b"---\n\n" + body.encode()


def reference(identity, revision, store, path, raw, sections) -> dict:
    return {"artifact_id": identity, "revision": revision, "store": store,
            "path": path, "sha256": digest(raw), "sections": list(sections)}


def atom(tag, value) -> str:
    return "@df-" + tag + "(" + json.dumps(value, sort_keys=True, separators=(",", ":")) + ")"


class Fixture(legacy.Fixture):
    PRIMARY = "docs/ideas.md"
    SECONDARY = "docs/retained-ideas.txt"  # Required despite its extension/order.
    HANDOFF = "docs/handoff.md"

    def __init__(self, root):
        super().__init__(root)
        self.assignment_raw = artifact("SESSION-101", "session-record",
            "## Assignment [SESSION-SECTION-001]\n\nSynthetic owned phase fixture.\n")
        self.assignment_path.write_bytes(self.assignment_raw)
        self.source_path = self.operator / "product-source.md"
        self.source_raw = artifact("PROD-101", "product-brief",
            "## Source [PRODUCT-SECTION-001]\n\nA handoff uncertainty needs an observation.\n")
        self.source_path.write_bytes(self.source_raw)
        self.session_ref = reference("SESSION-101", 1, "authority", "sessions/assignment.md",
                                     self.assignment_raw, ["SESSION-SECTION-001"])
        self.product_ref = reference("PROD-101", 1, "authority", "sources/product.md",
                                     self.source_raw, ["PRODUCT-SECTION-001"])
        self.outputs = [
            {"path": self.SECONDARY, "artifact_id": "IDEAS-102", "artifact_type": "idea-ledger",
             "revision": 1, "sections": ["IDEAS-SECTION-002"]},
            {"path": self.PRIMARY, "artifact_id": "IDEAS-101", "artifact_type": "idea-ledger",
             "revision": 2, "sections": ["IDEAS-SECTION-001"]},
            {"path": self.HANDOFF, "artifact_id": "HANDOFF-101", "artifact_type": "handoff",
             "revision": 1, "sections": ["NEXT-001"]},
        ]
        self.delivery.update(schema_version="devforge.delivery-task/v2", project_id=PROJECT,
                             primary_ledger_path=self.PRIMARY, outputs=self.outputs,
                             reference_catalog=[
                                 self.fixed("authority", "sessions/assignment.md", "SESSION-101",
                                            "session-record", self.assignment_path, self.assignment_raw),
                                 self.fixed("authority", "sources/product.md", "PROD-101",
                                            "product-brief", self.source_path, self.source_raw)])
        self.session["assignment"] = {"path": str(self.assignment_path), "sha256": digest(self.assignment_raw)}
        self.session["output_baselines"] = [
            {"path": row["path"], "sha256": None, "archive": None, "allow_unchanged": False}
            for row in self.outputs]
        self.old_raw = None
        self.preimage_row = None
        self.refresh_delivery()

    @staticmethod
    def fixed(store, logical, identity, role, physical, raw):
        return {"store": store, "path": logical, "kind": "artifact",
                "identity": {"artifact_id": identity, "artifact_type": role,
                             "project_id": PROJECT, "revision": 1},
                "source": {"kind": "fixed", "physical_path": str(physical), "sha256": digest(raw)}}

    def spec(self, path):
        return next(row for row in self.outputs if row["path"] == path)

    def baseline(self, path):
        return next(row for row in self.session["output_baselines"] if row["path"] == path)

    def ledger_bytes(self, path, *, suffix="", execution=True, bad_reference=False):
        spec = self.spec(path)
        upstream = copy.deepcopy(self.product_ref)
        if bad_reference:
            upstream["sections"] = ["PRODUCT-SECTION-999"]
        old = None
        if path == self.PRIMARY and self.old_raw is not None:
            old = reference("IDEAS-101", 1, "project", self.PRIMARY,
                            self.old_raw, ["IDEAS-SECTION-001"])
        planned = {"store": "project", "path": self.HANDOFF, "artifact_id": "HANDOFF-101",
                   "revision": 1, "relation": "planned-output"}
        body = ("## Ideas [" + spec["sections"][0] + "]\n\n"
                "A reminder is a proposal whose usefulness remains unknown.\n\n" +
                atom("link", planned) + "\n" + suffix)
        return artifact(spec["artifact_id"], "idea-ledger", body, revision=spec["revision"],
                        execution=self.session_ref if execution else None,
                        upstream=[upstream], supersedes=old)

    def write_ledgers(self, *, bad_path=None, execution=True):
        result = {}
        for path in (self.SECONDARY, self.PRIMARY):
            raw = self.ledger_bytes(path, execution=execution, bad_reference=path == bad_path)
            target = self.project / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            result[path] = raw
        return result

    def handoff_bytes(self, *, bad_reference=False, execution=True):
        refs = [reference(self.spec(path)["artifact_id"], self.spec(path)["revision"], "project", path,
                          (self.project / path).read_bytes(), self.spec(path)["sections"])
                for path in (self.SECONDARY, self.PRIMARY)]
        if bad_reference:
            refs[0]["sha256"] = "0" * 64
        body = "## Next [NEXT-001]\n\nAsk a volunteer for one concrete observation.\n\n"
        body += "\n".join(atom("ref", {"field": "output", **row}) for row in refs) + "\n\n"
        body += atom("delivery", {"task_id": self.delivery["task_id"], **PENDING}) + "\n"
        return artifact("HANDOFF-101", "handoff", body, execution=self.session_ref if execution else None,
                        upstream=refs, handoff=True)

    def write_current_handoff(self, **kwargs):
        raw = self.handoff_bytes(**kwargs)
        self.handoff_path.write_bytes(raw)
        return raw

    def seed_preimage(self):
        self.old_raw = artifact("IDEAS-101", "idea-ledger",
            "## Earlier ideas [IDEAS-SECTION-001]\n\nAn earlier unadopted proposal.\n",
            execution=self.session_ref, upstream=[self.product_ref])
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_bytes(self.old_raw)
        archive = "archive/primary-r1.md"
        self.baseline(self.PRIMARY).update(sha256=digest(self.old_raw), archive=archive,
                                           allow_unchanged=False)
        self.preimage_row = {
            "store": "project", "path": self.PRIMARY, "kind": "artifact",
            "identity": {"artifact_id": "IDEAS-101", "artifact_type": "idea-ledger",
                         "project_id": PROJECT, "revision": 1},
            "source": {"kind": "output-preimage", "output_path": self.PRIMARY,
                       "physical_path": str(self.project / archive), "sha256": digest(self.old_raw)},
        }
        self.delivery["reference_catalog"].append(self.preimage_row)
        self.refresh_delivery()
        return self.project / archive

    def add_raw_source(self, physical, raw, logical="evidence/collision.bin"):
        self.delivery["reference_catalog"].append({
            "store": "authority", "path": logical, "kind": "raw-file", "identity": None,
            "source": {"kind": "fixed", "physical_path": str(physical), "sha256": digest(raw)}})
        self.refresh_delivery()


class PhaseReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="devforge-phase-references-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.counter = 0
        timer = mock.patch.object(phase, "utc_now", return_value=NOW)
        timer.start()
        self.addCleanup(timer.stop)

    def fixture(self, *, v1=False):
        self.counter += 1
        return (legacy.Fixture if v1 else Fixture)(self.root / str(self.counter))

    def result(self, actual, expected, phase_name=None):
        self.assertEqual(actual.get("status"), expected, actual)
        self.assertEqual(actual.get("native_completion"), "NOT_EVALUATED", actual)
        if phase_name is not None:
            self.assertEqual(actual.get("phase"), phase_name, actual)
        return actual

    def refuse(self, actual):
        self.assertIn(actual.get("status"), {"FAIL", "COULD_NOT_RUN"}, actual)
        self.assertEqual(actual.get("native_completion"), "NOT_EVALUATED", actual)
        return actual

    def checkpoint(self, f, name, content, *, challenge=None):
        if challenge is None:
            challenge = phase.context(f.state)["challenge"]
        raw = packed({"schema_version": "devforge.brainstorm-checkpoint/v1",
                      "task_id": f.session["task_id"], "phase": name, "challenge": challenge,
                      "state": "ready", "content": content})
        f.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        f.checkpoint_path.write_bytes(raw)
        return raw

    def to_record(self, f):
        self.result(phase.start(f.session_path, f.state), "ACTIVE", "Recover")
        self.checkpoint(f, "Recover", {"known_ideas": [], "known_decisions": [],
                                      "missing_inputs": ["Human adoption remains unknown."]})
        self.result(phase.advance(f.state), "PROGRESS", "Explore")
        self.checkpoint(f, "Explore", {"ideas": [{"idea_id": "IDEA-101", "people": None,
            "problem": "One handoff may be missed.", "outcome": None,
            "alternatives": ["Ask a volunteer for a recent example."],
            "open_questions": ["Who experiences the problem?"]}], "missing_inputs": []})
        return self.result(phase.advance(f.state), "PROGRESS", "Record")

    def accept_record(self, f):
        self.checkpoint(f, "Record", {"ledger_path": f.PRIMARY})
        return self.result(phase.advance(f.state), "PROGRESS", "Focus")

    def focus_checkpoint(self, f):
        return self.checkpoint(f, "Focus", {"next_action": "Ask a volunteer for one example.",
            "owner": "Human project owner", "completion_evidence": "A written observation.",
            "non_goals": ["No automatic adoption or release."], "handoff_path": "docs/handoff.md"})

    def to_focus(self, f):
        self.to_record(f)
        ledgers = f.write_ledgers()
        self.accept_record(f)
        return ledgers

    def to_ready(self, f):
        outputs = self.to_focus(f)
        outputs[f.HANDOFF] = f.write_current_handoff()
        self.focus_checkpoint(f)
        self.result(phase.advance(f.state), "READY", "Focus")
        return outputs

    @staticmethod
    def tree(root):
        result = {}
        for path in sorted(root.rglob("*")):
            name = path.relative_to(root).as_posix()
            if path.is_symlink():
                result[name] = ("symlink", os.readlink(path))
            elif path.is_file():
                result[name] = ("file", path.read_bytes())
            else:
                result[name] = ("directory",)
        return result

    def exact_snapshot(self, f, raw):
        target = f.state / "snapshots" / (digest(raw) + ".bin")
        self.assertEqual(target.read_bytes(), raw)
        return target

    def report_snapshot(self, f, name):
        matches = []
        head = json.loads((f.state / "HEAD.json").read_bytes())
        for record_id in head["records"]:
            record = json.loads((f.state / "records" / (record_id + ".json")).read_bytes())
            for ref in record["snapshots"]:
                if ref["kind"] == "reference_coverage" and ref["source"] == name:
                    raw = (f.state / ref["path"]).read_bytes()
                    self.assertEqual(digest(raw), ref["sha256"])
                    self.assertEqual(len(raw), ref["bytes"])
                    matches.append((f.state / ref["path"], raw, json.loads(raw)))
        self.assertEqual(len(matches), 1, "Exactly one accepted normalized phase report must be durable")
        path, raw, report = matches[0]
        self.assertEqual(set(report), {"reference_profile", "reference_coverage"})
        self.assertEqual(report["reference_profile"], PROFILE)
        coverage = report["reference_coverage"]
        self.assertIsInstance(coverage, dict)
        self.assertEqual(coverage["profile"], PROFILE)
        self.assertEqual(coverage["semantic_review"], "NOT_EVALUATED")
        expected_paths = [row["path"] for row in f.outputs
                          if name == "Focus" or row["artifact_type"] == "idea-ledger"]
        self.assertCountEqual(coverage["outputs"], expected_paths)
        self.assertEqual(coverage["execution_references"], {
            path: {"physical_path": str(f.assignment_path), "artifact_id": "SESSION-101",
                   "revision": 1, "sha256": digest(f.assignment_raw)} for path in expected_paths})
        return path, raw, report

    def assert_coverage_mentions(self, report, path, raw):
        encoded = json.dumps(report, sort_keys=True)
        self.assertIn(json.dumps(path), encoded)
        self.assertIn(digest(raw), encoded)

    def test_full_v2_phase_uses_second_listed_primary_and_binds_receipt_report(self):
        f = self.fixture()
        self.assertNotEqual(f.delivery["outputs"][0]["path"], f.delivery["primary_ledger_path"])
        self.to_record(f)
        ledgers = f.write_ledgers()
        self.accept_record(f)
        self.assertFalse(f.handoff_path.exists(), "Record may not require future handoff bytes")
        _, record_raw, record_report = self.report_snapshot(f, "Record")
        for path, raw in ledgers.items():
            self.exact_snapshot(f, raw)
            self.assert_coverage_mentions(record_report, path, raw)
        before = self.tree(f.state)
        self.result(phase.context(f.state), "ACTIVE", "Focus")
        self.assertEqual(self.tree(f.state), before, "Status cannot regenerate accepted coverage")
        handoff = f.write_current_handoff()
        self.focus_checkpoint(f)
        self.result(phase.advance(f.state), "READY", "Focus")
        _, _, focus_report = self.report_snapshot(f, "Focus")
        checked = phase.delivery_core.check(f.delivery_path)
        self.assertEqual(checked["result"], "PASS", checked)
        self.assertEqual(focus_report["reference_coverage"], checked["reference_coverage"])
        self.assertEqual(self.report_snapshot(f, "Record")[1], record_raw)
        completed = self.result(phase.complete(f.state), "COMPLETED")
        receipt_raw = f.receipt_path.read_bytes()
        receipt = json.loads(receipt_raw)
        self.assertEqual(receipt["schema_version"], "devforge.delivery-receipt/v2")
        self.assertEqual(receipt["reference_profile"], PROFILE)
        self.assertEqual(receipt["reference_coverage"], focus_report["reference_coverage"])
        self.assertEqual(receipt["outputs"], {**{p: digest(r) for p, r in ledgers.items()},
                                              f.HANDOFF: digest(handoff)})
        self.assertEqual(completed["receipt_sha256"], digest(receipt_raw))
        self.assertEqual(f.handoff_path.read_bytes(), handoff)
        envelope = json.loads(handoff.split(b"---\n", 2)[1])
        self.assertEqual(envelope["delivery_state"], PENDING)
        for key in ("receipt_publication", "receipt_readback"):
            self.assertEqual(receipt[key], "NOT_RUN")
        before = self.tree(f.root)
        self.result(phase.context(f.state), "COMPLETED")
        self.result(phase.complete(f.state), "COMPLETED")
        self.assertEqual(self.tree(f.root), before)

    def test_bad_reference_at_record_cannot_advance_and_one_correction_can_repair(self):
        f = self.fixture()
        self.to_record(f)
        f.write_ledgers(bad_path=f.PRIMARY)
        self.checkpoint(f, "Record", {"ledger_path": f.PRIMARY})
        rejected = self.result(phase.advance(f.state), "FAIL", "Record")
        self.assertFalse(rejected["terminal"])
        self.assertEqual(phase.context(f.state)["phase"], "Record")
        self.assertFalse(f.receipt_path.exists())
        f.write_ledgers()
        self.accept_record(f)

    def test_record_cannot_filter_missing_directory_or_bad_nonprimary_ledger(self):
        for mutation in ("missing", "directory", "bad-reference"):
            with self.subTest(mutation=mutation):
                f = self.fixture()
                self.to_record(f)
                f.write_ledgers(bad_path=f.SECONDARY if mutation == "bad-reference" else None)
                self.checkpoint(f, "Record", {"ledger_path": f.PRIMARY})
                extra = f.project / f.SECONDARY
                saved = extra.read_bytes()
                head = (f.state / "HEAD.json").read_bytes()
                if mutation != "bad-reference":
                    extra.unlink()
                    if mutation == "directory":
                        extra.mkdir()
                self.refuse(phase.advance(f.state))
                if mutation == "directory":
                    self.assertEqual((f.state / "HEAD.json").read_bytes(), head)
                    extra.rmdir()
                    extra.write_bytes(saved)
                self.assertEqual(phase.context(f.state)["phase"], "Record")
                self.assertFalse(f.receipt_path.exists())

    def test_record_checkpoint_cannot_select_first_nonprimary_ledger(self):
        f = self.fixture()
        self.to_record(f)
        f.write_ledgers()
        self.checkpoint(f, "Record", {"ledger_path": f.SECONDARY})
        self.result(phase.advance(f.state), "FAIL", "Record")
        self.accept_record(f)

    def test_explicitly_retained_unchanged_second_ledger_remains_selected_output(self):
        f = self.fixture()
        retained = f.ledger_bytes(f.SECONDARY)
        retained_path = f.project / f.SECONDARY
        retained_path.parent.mkdir(parents=True, exist_ok=True)
        retained_path.write_bytes(retained)
        f.baseline(f.SECONDARY).update(sha256=digest(retained), archive="archive/retained-secondary.md",
                                      allow_unchanged=True)
        f.write_session()
        outputs = self.to_ready(f)
        self.result(phase.complete(f.state), "COMPLETED")
        self.assertEqual(outputs[f.SECONDARY], retained)
        self.assertEqual((f.project / "archive/retained-secondary.md").read_bytes(), retained)
        receipt = json.loads(f.receipt_path.read_bytes())
        self.assertEqual(receipt["outputs"][f.SECONDARY], digest(retained))
        self.assert_coverage_mentions(receipt["reference_coverage"], f.SECONDARY, retained)

    def test_null_execution_ref_is_not_managed_record_authority(self):
        f = self.fixture()
        self.to_record(f)
        f.write_ledgers(execution=False)
        self.checkpoint(f, "Record", {"ledger_path": f.PRIMARY})
        self.refuse(phase.advance(f.state))
        self.assertEqual(phase.context(f.state)["phase"], "Record")

    def test_identical_session_copy_at_wrong_physical_assignment_path_cannot_start(self):
        f = self.fixture()
        copy_path = f.operator / "equally-hashed-unselected-assignment.md"
        copy_path.write_bytes(f.assignment_raw)
        f.delivery["reference_catalog"][0]["source"]["physical_path"] = str(copy_path)
        f.refresh_delivery()
        before = self.tree(f.root)
        self.refuse(phase.start(f.session_path, f.state))
        self.assertEqual(self.tree(f.root), before)
        self.assertFalse(f.state.exists())

    def test_old_revision_is_materialized_before_recover_and_resolves_after_live_r2(self):
        f = self.fixture()
        archive = f.seed_preimage()
        self.assertFalse(archive.exists())
        outputs = self.to_ready(f)
        self.assertEqual(archive.read_bytes(), f.old_raw)
        self.exact_snapshot(f, f.old_raw)
        self.assertNotEqual(f.ledger_path.read_bytes(), f.old_raw)
        self.result(phase.complete(f.state), "COMPLETED")
        receipt = json.loads(f.receipt_path.read_bytes())
        self.assertEqual(receipt["outputs"][f.PRIMARY], digest(outputs[f.PRIMARY]))
        self.assert_coverage_mentions(receipt["reference_coverage"], f.PRIMARY, f.old_raw)
        self.assert_coverage_mentions(receipt["reference_coverage"], f.PRIMARY, outputs[f.PRIMARY])
        self.assertEqual(archive.read_bytes(), f.old_raw)

    def test_preimage_cross_binding_mismatch_is_refused_before_any_publication(self):
        for mismatch in ("output_path", "sha256", "archive_path"):
            with self.subTest(mismatch=mismatch):
                f = self.fixture()
                f.seed_preimage()
                if mismatch == "output_path":
                    f.preimage_row["source"]["output_path"] = f.SECONDARY
                elif mismatch == "sha256":
                    f.preimage_row["source"]["sha256"] = "0" * 64
                else:
                    wrong = f.project / "wrong-archive/same-old-bytes.md"
                    wrong.parent.mkdir()
                    wrong.write_bytes(f.old_raw)
                    f.preimage_row["source"]["physical_path"] = str(wrong)
                f.refresh_delivery()
                before = self.tree(f.root)
                self.refuse(phase.start(f.session_path, f.state))
                self.assertEqual(self.tree(f.root), before)
                self.assertFalse(f.state.exists())
                self.assertFalse((f.project / "archive").exists())

    def test_missing_changed_or_unlisted_archive_alias_cannot_resolve_old_revision(self):
        for mutation in ("missing", "changed", "unlisted-alias", "protected-snapshot"):
            with self.subTest(mutation=mutation):
                f = self.fixture()
                archive = f.seed_preimage()
                self.to_record(f)
                f.write_ledgers()
                self.checkpoint(f, "Record", {"ledger_path": f.PRIMARY})
                if mutation == "missing":
                    archive.unlink()
                elif mutation == "changed":
                    archive.write_bytes(f.old_raw + b"Changed archived bytes.\n")
                elif mutation == "protected-snapshot":
                    self.exact_snapshot(f, f.old_raw).write_bytes(f.old_raw + b"Changed protected bytes.\n")
                else:
                    archive.rename(archive.with_name("same-bytes-unselected-alias.md"))
                before = self.tree(f.root)
                self.refuse(phase.advance(f.state))
                self.assertEqual(self.tree(f.root), before, "Immutable binding errors cannot publish or commit corrections")
                self.refuse(phase.resume(f.state))
                self.assertEqual(self.tree(f.root), before)
                self.assertFalse(f.receipt_path.exists())

    def test_start_preserves_actual_preimage_snapshot_before_archive_publication(self):
        f = self.fixture()
        archive = f.seed_preimage()
        publish = phase._new_at  # Existing v1 publication seam, not new parsing logic.
        seen = []
        def observe(parent, name, raw):
            if name == archive.name:
                seen.append(True)
                self.assertEqual(raw, f.old_raw)
                self.exact_snapshot(f, f.old_raw)
            return publish(parent, name, raw)
        with mock.patch.object(phase, "_new_at", side_effect=observe):
            self.result(phase.start(f.session_path, f.state), "ACTIVE", "Recover")
        self.assertEqual(seen, [True])
        self.assertEqual(archive.read_bytes(), f.old_raw)

    def test_preimage_or_archive_changed_during_publication_never_commits_admission(self):
        for mutation in ("preimage", "archive"):
            with self.subTest(mutation=mutation):
                f = self.fixture()
                archive = f.seed_preimage()
                publish = phase._new_at
                changed = []
                def race(parent, name, raw):
                    value = publish(parent, name, raw)
                    if name == archive.name and not changed:
                        changed.append(True)
                        target = f.ledger_path if mutation == "preimage" else archive
                        target.write_bytes(f.old_raw + b"Raced selected bytes.\n")
                    return value
                with mock.patch.object(phase, "_new_at", side_effect=race):
                    self.refuse(phase.start(f.session_path, f.state))
                self.assertEqual(changed, [True])
                self.assertFalse((f.state / "HEAD.json").exists())
                self.assertFalse(f.receipt_path.exists())
                self.exact_snapshot(f, f.old_raw)

    def test_fixed_catalog_drift_blocks_record_status_resume_and_completion(self):
        for stage in ("Record", "Focus-status", "Focus-resume", "READY-complete", "protected-snapshot"):
            with self.subTest(stage=stage):
                f = self.fixture()
                if stage == "Record":
                    self.to_record(f)
                    f.write_ledgers()
                    self.checkpoint(f, "Record", {"ledger_path": f.PRIMARY})
                    operation = phase.advance
                elif stage == "READY-complete":
                    self.to_ready(f)
                    operation = phase.complete
                else:
                    self.to_focus(f)
                    operation = phase.resume if stage == "Focus-resume" else phase.context
                if stage == "protected-snapshot":
                    self.exact_snapshot(f, f.source_raw).write_bytes(f.source_raw + b"Protected source drift.\n")
                else:
                    f.source_path.write_bytes(f.source_raw + b"Original source drift.\n")
                before = self.tree(f.state)
                self.refuse(operation(f.state))
                self.assertEqual(self.tree(f.state), before)
                self.assertFalse(f.receipt_path.exists())

    def test_accepted_record_bytes_cannot_change_at_focus_status_resume_or_complete(self):
        for operation_name in ("context", "resume", "advance", "complete"):
            with self.subTest(operation=operation_name):
                f = self.fixture()
                accepted = self.to_focus(f)
                report_path, report_raw, _ = self.report_snapshot(f, "Record")
                f.write_current_handoff()
                self.focus_checkpoint(f)
                changed = f.ledger_bytes(f.SECONDARY, suffix="\nAnother still-unadopted proposal.\n")
                (f.project / f.SECONDARY).write_bytes(changed)
                # Rebind handoff to changed bytes so stale handoff hashes cannot be the reason.
                f.write_current_handoff()
                before = self.tree(f.state)
                self.refuse(getattr(phase, operation_name)(f.state))
                self.assertEqual(self.tree(f.state), before)
                self.assertEqual(report_path.read_bytes(), report_raw)
                self.exact_snapshot(f, accepted[f.SECONDARY])
                self.assertFalse(f.receipt_path.exists())

    def test_accepted_reference_report_corruption_cannot_be_rederived_on_reopen(self):
        f = self.fixture()
        self.to_focus(f)
        path, raw, _ = self.report_snapshot(f, "Record")
        path.write_bytes(raw + b" ")
        before = self.tree(f.state)
        self.refuse(phase.context(f.state))
        self.refuse(phase.resume(f.state))
        self.assertEqual(self.tree(f.state), before)
        self.assertFalse(f.receipt_path.exists())

    def test_same_byte_catalog_alias_reselection_cannot_change_accepted_record_binding(self):
        f = self.fixture()
        self.to_focus(f)
        report_path, report_raw, _ = self.report_snapshot(f, "Record")
        alias = f.operator / "same-bytes-new-catalog-alias.md"
        alias.write_bytes(f.source_raw)
        f.delivery["reference_catalog"][1]["source"]["physical_path"] = str(alias)
        f.refresh_delivery()
        before = self.tree(f.state)
        self.refuse(phase.context(f.state))
        self.refuse(phase.resume(f.state))
        self.assertEqual(self.tree(f.state), before)
        self.assertEqual(report_path.read_bytes(), report_raw)

    def test_focus_references_are_checked_before_ready(self):
        f = self.fixture()
        self.to_focus(f)
        f.write_current_handoff(bad_reference=True)
        self.focus_checkpoint(f)
        self.result(phase.advance(f.state), "FAIL", "Focus")
        self.assertFalse(f.receipt_path.exists())
        f.write_current_handoff()
        self.focus_checkpoint(f)
        self.result(phase.advance(f.state), "READY", "Focus")

    def test_handoff_without_assignment_reference_cannot_reach_ready(self):
        f = self.fixture()
        self.to_focus(f)
        f.write_current_handoff(execution=False)
        self.focus_checkpoint(f)
        self.refuse(phase.advance(f.state))
        self.assertFalse(f.receipt_path.exists())

    def publish_then_interrupt(self, f):
        finalize = phase.delivery_core.finalize
        def interrupted(contract, receipt):
            result = finalize(contract, receipt)
            self.assertEqual(result["result"], "PASS", result)
            raise OSError("Synthetic interruption after exclusive receipt publication")
        with mock.patch.object(phase.delivery_core, "finalize", side_effect=interrupted):
            self.result(phase.complete(f.state), "COULD_NOT_RUN")
        return f.receipt_path.read_bytes()

    def test_interrupted_v2_completion_recovers_same_receipt_without_republication(self):
        f = self.fixture()
        archive = f.seed_preimage()
        outputs = self.to_ready(f)
        receipt = self.publish_then_interrupt(f)
        with mock.patch.object(phase.delivery_core, "finalize", side_effect=AssertionError("No second publication")):
            completed = self.result(phase.complete(f.state), "COMPLETED")
        self.assertEqual(f.receipt_path.read_bytes(), receipt)
        self.assertEqual(completed["receipt_sha256"], digest(receipt))
        self.assertEqual(archive.read_bytes(), f.old_raw)
        self.assertEqual(json.loads(receipt)["outputs"], {p: digest(r) for p, r in outputs.items()})

    def test_active_v2_resume_preserves_accepted_record_and_original_deadline(self):
        f = self.fixture()
        archive = f.seed_preimage()
        self.to_focus(f)
        report_path, report_raw, _ = self.report_snapshot(f, "Record")
        before = phase.context(f.state)
        resumed = self.result(phase.resume(f.state), "ACTIVE", "Focus")
        self.assertNotEqual(resumed["challenge"], before["challenge"])
        self.assertEqual(resumed["deadline_utc"], before["deadline_utc"])
        self.assertEqual(report_path.read_bytes(), report_raw)
        self.assertEqual(archive.read_bytes(), f.old_raw)
        f.write_current_handoff()
        self.focus_checkpoint(f)
        self.result(phase.advance(f.state), "READY", "Focus")
        self.result(phase.complete(f.state), "COMPLETED")

    def test_interrupted_completion_cannot_ignore_catalog_archive_or_output_drift(self):
        for mutation in ("fixed", "archive", "output"):
            with self.subTest(mutation=mutation):
                f = self.fixture()
                archive = f.seed_preimage()
                self.to_ready(f)
                receipt = self.publish_then_interrupt(f)
                target = {"fixed": f.source_path, "archive": archive, "output": f.handoff_path}[mutation]
                target.write_bytes(target.read_bytes() + b"Late drift.\n")
                before = self.tree(f.state)
                with mock.patch.object(phase.delivery_core, "finalize", side_effect=AssertionError("No overwrite")):
                    self.refuse(phase.complete(f.state))
                self.assertEqual(f.receipt_path.read_bytes(), receipt)
                self.assertEqual(self.tree(f.state), before)

    def test_reference_report_overflow_cannot_publish_truncated_receipt(self):
        f = self.fixture()
        self.to_focus(f)
        raw = f.handoff_bytes()
        row = reference("IDEAS-101", 2, "project", f.PRIMARY, f.ledger_path.read_bytes(), ["IDEAS-SECTION-001"])
        # A bounded artifact with many valid repeats produces a report much larger
        # than the fixed 128 KiB receipt budget. No repetition may be discarded.
        repeated = (atom("ref", {"field": "output", **row}) + "\n").encode() * 900
        self.assertLess(len(raw + repeated), 1024 * 1024, "Fixture must fit the established artifact bound")
        f.handoff_path.write_bytes(raw + repeated)
        self.focus_checkpoint(f)
        focus = phase.advance(f.state)
        if focus["status"] == "READY":
            self.refuse(phase.complete(f.state))
        else:
            self.refuse(focus)
        self.assertFalse(f.receipt_path.exists(), "Overflow must not publish a truncated success receipt")

    def test_catalog_collisions_preserve_inputs_and_refuse_before_state_writes(self):
        for target_kind in ("checkpoint", "receipt", "implementation"):
            with self.subTest(target=target_kind):
                f = self.fixture()
                if target_kind == "checkpoint":
                    target = f.checkpoint_path
                    raw = b"Synthetic checkpoint collision sentinel.\n"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(raw)
                elif target_kind == "receipt":
                    target = f.receipt_path
                    raw = b"Synthetic existing external receipt.\n"
                    target.write_bytes(raw)
                else:
                    # Executor selects public implementation bytes for a collision
                    # fixture only; author never read them to infer behavior.
                    target = Path(phase.__file__).resolve()
                    raw = target.read_bytes()
                f.add_raw_source(target, raw)
                before = self.tree(f.root)
                self.refuse(phase.start(f.session_path, f.state))
                self.assertEqual(self.tree(f.root), before)
                self.assertFalse(f.state.exists())

    def test_fixed_catalog_source_cannot_live_under_selected_state_root(self):
        f = self.fixture()
        f.state.mkdir()
        source = f.state / "unrelated-protected-source.bin"
        source.write_bytes(b"Existing protected state sentinel.\n")
        f.add_raw_source(source, source.read_bytes())
        before = self.tree(f.root)
        self.refuse(phase.start(f.session_path, f.state))
        self.assertEqual(self.tree(f.root), before)

    def test_legacy_v1_full_phase_stays_v1_and_record_bytes_can_evolve(self):
        f = self.fixture(v1=True)
        self.to_record(f)
        earlier = f.write_ledger()
        self.checkpoint(f, "Record", {"ledger_path": "docs/ideas.md"})
        self.result(phase.advance(f.state), "PROGRESS", "Focus")
        later = f.write_ledger("\nLegacy Focus adds an observation.\n")
        handoff = f.write_handoff()
        self.focus_checkpoint(f)
        self.result(phase.advance(f.state), "READY", "Focus")
        self.result(phase.complete(f.state), "COMPLETED")
        receipt = json.loads(f.receipt_path.read_bytes())
        self.assertEqual(receipt["schema_version"], "devforge.delivery-receipt/v1")
        self.assertNotIn("reference_profile", receipt)
        self.assertNotIn("reference_coverage", receipt)
        self.assertEqual(receipt["outputs"], {"docs/ideas.md": digest(later), "docs/handoff.md": digest(handoff)})
        self.exact_snapshot(f, earlier)
        before = self.tree(f.root)
        self.result(phase.context(f.state), "COMPLETED")
        self.assertEqual(self.tree(f.root), before)

    def test_active_v1_journal_cannot_upgrade_to_v2_by_rebinding_contract(self):
        f = self.fixture(v1=True)
        self.result(phase.start(f.session_path, f.state), "ACTIVE", "Recover")
        protected = self.tree(f.state)
        f.delivery.update(schema_version="devforge.delivery-task/v2", project_id=PROJECT,
                          primary_ledger_path="docs/ideas.md", reference_catalog=[])
        f.refresh_delivery()
        self.refuse(phase.context(f.state))
        self.refuse(phase.resume(f.state))
        self.assertEqual(self.tree(f.state), protected)
        self.assertFalse(f.receipt_path.exists())


if __name__ == "__main__":
    unittest.main()
