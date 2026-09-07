"""Contract-derived mechanical delivery tests; no provider or runtime execution.

Expected results come from IMPLEMENTATION-CONTRACT.md.  These tests deliberately
do not read the implementation or use it to construct expected hashes/fixtures.
The temporary operator/project directories are logic fixtures, not proof of an
actual worker filesystem boundary.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import yaml


IMPLEMENTATION = Path(__file__).resolve().parents[1] / "runtime" / "delivery"
sys.path.insert(0, str(IMPLEMENTATION))
import delivery_core as core  # noqa: E402


OUTPUT_LIMIT = 1024 * 1024
INPUT_LIMIT = 8 * 1024 * 1024
CONTRACT_LIMIT = 64 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def envelope_bytes(envelope: dict, body: str, syntax: str) -> bytes:
    if syntax == "json":
        front = json.dumps(envelope, indent=2, ensure_ascii=False)
    else:
        front = yaml.safe_dump(envelope, sort_keys=False, allow_unicode=True).rstrip()
    return ("---\n" + front + "\n---\n\n" + body).encode("utf-8")


class Fixture:
    """Small independent artifacts, with a selected contract outside the project."""

    def __init__(self, root: Path, mode: str = "brainstorm", syntax: str = "yaml"):
        self.root = root
        self.project = root / "project"
        self.operator = root / "operator"
        self.project.mkdir(parents=True)
        self.operator.mkdir()
        self.syntax = syntax
        self.mode = mode
        self.contract_path = self.operator / "task.json"
        self.receipt_path = self.operator / "receipt.json"
        self.input_path = self.project / "inputs/context.txt"
        self.ledger_path = self.project / "docs/ideas.md"
        self.handoff_path = self.project / "docs/handoff.md"
        self.input_path.parent.mkdir()
        self.ledger_path.parent.mkdir()
        self.input_path.write_bytes(b"The user is exploring volunteer shift reminders.\n")

        self.ledger_envelope = self.envelope("IDEAS-001", "idea-ledger")
        self.ledger_body = (
            "# Ideas\n\n## FOCUS-001\n\nThe user has not selected a product.\n\n"
            "## IDEAS-SECTION-001\n\nAn optional reminder remains a proposal.\n"
        )
        self.handoff_envelope = self.envelope("HANDOFF-001", "handoff")
        self.handoff_body = (
            "# Handoff\n\n## IO-001\n\nThe selected input was considered.\n\n"
            "## NEXT-001\n\nAsk the user which small experiment to consider.\n"
        )
        self.contract = {
            "schema_version": "devforge.delivery-task/v1",
            "task_id": "TASK-DELIVERY-001",
            "project_root": str(self.project),
            "mode": mode,
            "inputs": [{"path": "inputs/context.txt", "sha256": digest(self.input_path.read_bytes())}],
            "outputs": [],
        }
        if mode == "brainstorm":
            self.contract["outputs"].append({
                "path": "docs/ideas.md", "artifact_id": "IDEAS-001",
                "artifact_type": "idea-ledger", "revision": 1,
                "sections": ["FOCUS-001", "IDEAS-SECTION-001"],
            })
            self.ledger_path.write_bytes(envelope_bytes(self.ledger_envelope, self.ledger_body, syntax))
            self.handoff_envelope["upstream"] = [{
                "artifact_id": "IDEAS-001", "revision": 1, "store": "project",
                "path": "docs/ideas.md", "sha256": digest(self.ledger_path.read_bytes()),
                "sections": ["FOCUS-001", "IDEAS-SECTION-001"],
            }]
        self.contract["outputs"].append({
            "path": "docs/handoff.md", "artifact_id": "HANDOFF-001",
            "artifact_type": "handoff", "revision": 1,
            "sections": ["IO-001", "NEXT-001"],
        })
        self.write_handoff()
        self.write_contract()

    @staticmethod
    def envelope(artifact_id: str, artifact_type: str) -> dict:
        return {
            "schema_version": "devforge.artifact/v1",
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "project_id": "synthetic-project",
            "revision": 1,
            "status": "draft",
            "created_at_utc": "2026-09-01T12:00:00Z",
            "producer": {"skill": "devforge-brainstorm", "skill_revision": "synthetic-fixture"},
            "execution_ref": None,
            "upstream": [],
            "evidence": [],
            "supersedes": None,
            "decision_ref": None,
            "missing_inputs": [],
        }

    def contract_bytes(self) -> bytes:
        return (json.dumps(self.contract, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    def write_contract(self) -> None:
        self.contract_path.write_bytes(self.contract_bytes())

    def write_handoff(self) -> None:
        self.handoff_path.write_bytes(envelope_bytes(self.handoff_envelope, self.handoff_body, self.syntax))

    def put_ledger_bytes(self, data: bytes, *, refresh_backlink: bool = True) -> None:
        self.ledger_path.write_bytes(data)
        if refresh_backlink:
            self.handoff_envelope["upstream"][0]["sha256"] = digest(data)
            self.write_handoff()

    def write_ledger(self) -> None:
        self.put_ledger_bytes(envelope_bytes(self.ledger_envelope, self.ledger_body, self.syntax))

    def expected_inputs(self) -> dict:
        return {row["path"]: digest((self.project / row["path"]).read_bytes()) for row in self.contract["inputs"]}

    def expected_outputs(self) -> dict:
        return {row["path"]: digest((self.project / row["path"]).read_bytes()) for row in self.contract["outputs"]}


class DeliveryCoreContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="devforge-delivery-core-tests-")
        self.addCleanup(self.temp.cleanup)
        self.serial = itertools.count(1)

    def fixture(self, mode: str = "brainstorm", syntax: str = "yaml") -> Fixture:
        return Fixture(Path(self.temp.name) / ("case-%d" % next(self.serial)), mode, syntax)

    def assert_result(self, actual: dict, expected="PASS") -> None:
        self.assertIsInstance(actual, dict)
        for key in ("result", "issues", "scope"):
            self.assertIn(key, actual)
        allowed = (expected,) if isinstance(expected, str) else expected
        self.assertIn(actual["result"], allowed, actual)
        self.assertIsInstance(actual["issues"], list)
        self.assertTrue(all(isinstance(issue, str) and issue.strip() for issue in actual["issues"]), actual)
        self.assertTrue(actual["scope"], actual)
        if actual["result"] != "PASS":
            self.assertTrue(actual["issues"], actual)

    def assert_checked(self, f: Fixture) -> dict:
        result = core.check(f.contract_path)
        self.assert_result(result)
        self.assertEqual(result["task_id"], f.contract["task_id"])
        self.assertEqual(result["contract_sha256"], digest(f.contract_path.read_bytes()))
        self.assertEqual(result["project_root"], str(f.project))
        self.assertEqual(result["inputs"], f.expected_inputs())
        self.assertEqual(result["outputs"], f.expected_outputs())
        self.assertFalse(f.receipt_path.exists(), "Read-only check must not publish a receipt")
        return result

    def assert_rejected_without_receipt(self, f: Fixture, expected="FAIL") -> None:
        self.assertFalse(os.path.lexists(f.receipt_path))
        self.assert_result(core.check(f.contract_path), expected)
        self.assert_result(core.finalize(f.contract_path, f.receipt_path), expected)
        self.assertFalse(os.path.lexists(f.receipt_path), "A failed finalization published a receipt")

    def finalized(self, f: Fixture) -> tuple[dict, dict]:
        # Deliberately do not call check first: finalize must check for itself.
        result = core.finalize(f.contract_path, f.receipt_path)
        self.assert_result(result)
        self.assertIs(result["receipt_published"], True)
        self.assertIs(result["receipt_readback"], True)
        self.assertEqual(result["receipt_path"], str(f.receipt_path))
        raw = f.receipt_path.read_bytes()
        self.assertEqual(result["receipt_sha256"], digest(raw))
        receipt = json.loads(raw)
        self.assertEqual(receipt["schema_version"], "devforge.delivery-receipt/v1")
        self.assertEqual(receipt["receipt_path"], str(f.receipt_path))
        self.assertEqual(receipt["receipt_publication"], "NOT_RUN")
        self.assertEqual(receipt["receipt_readback"], "NOT_RUN")
        self.assertNotIn("receipt_sha256", receipt)
        self.assertEqual(receipt["task_id"], f.contract["task_id"])
        self.assertEqual(receipt["contract_sha256"], digest(f.contract_path.read_bytes()))
        self.assertEqual(receipt["project_root"], str(f.project))
        self.assertEqual(receipt["inputs"], f.expected_inputs())
        self.assertEqual(receipt["outputs"], f.expected_outputs())
        self.assertTrue(receipt["scope"])
        self.assertNotIn(result["receipt_sha256"], json.dumps(receipt), "Receipt embeds its own full-byte digest")
        self.assert_result(core.verify(f.contract_path, f.receipt_path))
        return result, receipt

    def test_complete_brainstorm_yaml_and_json_envelopes(self):
        for syntax in ("yaml", "json"):
            with self.subTest(syntax=syntax):
                f = self.fixture(syntax=syntax)
                self.assert_checked(f)
                self.finalized(f)

    def test_complete_handoff_only_does_not_require_a_ledger(self):
        for syntax in ("yaml", "json"):
            with self.subTest(syntax=syntax):
                f = self.fixture(mode="handoff-only", syntax=syntax)
                self.assertFalse(f.ledger_path.exists())
                self.assert_checked(f)
                self.finalized(f)

    def test_prepare_does_not_require_outputs_or_publish_delivery(self):
        f = self.fixture()
        f.ledger_path.unlink()
        f.handoff_path.unlink()
        result = core.prepare(f.contract_path)
        self.assert_result(result)
        self.assertEqual(result["task_id"], f.contract["task_id"])
        self.assertEqual(result["project_root"], str(f.project))
        self.assertEqual(result["contract_sha256"], digest(f.contract_path.read_bytes()))
        self.assertEqual(result["inputs"], f.expected_inputs())
        self.assertNotIn("receipt_sha256", result)
        self.assertNotIn("receipt_path", result)
        self.assertFalse(f.receipt_path.exists())
        self.assert_rejected_without_receipt(f)

    def test_missing_each_required_output_prevents_receipt(self):
        for which in ("ledger_path", "handoff_path"):
            with self.subTest(output=which):
                f = self.fixture()
                getattr(f, which).unlink()
                self.assert_rejected_without_receipt(f)

    def test_missing_contract_is_an_unavailable_operational_prerequisite(self):
        f = self.fixture()
        f.contract_path.unlink()
        self.assert_result(core.prepare(f.contract_path), "COULD_NOT_RUN")
        self.assert_rejected_without_receipt(f, "COULD_NOT_RUN")

    def test_missing_pinned_input_cannot_pass_prepare_or_delivery(self):
        f = self.fixture()
        f.input_path.unlink()
        self.assert_result(core.prepare(f.contract_path), "COULD_NOT_RUN")
        self.assert_rejected_without_receipt(f, "COULD_NOT_RUN")

    def test_changed_input_pin_fails_prepare_and_finalization(self):
        f = self.fixture()
        f.input_path.write_bytes(b"An unselected later input.\n")
        self.assert_result(core.prepare(f.contract_path), "FAIL")
        self.assert_rejected_without_receipt(f)

    def test_contract_rejects_unknown_fields_missing_fields_and_wrong_types(self):
        mutations = [
            ("unknown-field", lambda c: c.update(accept_anything=True)),
            ("missing-inputs", lambda c: c.pop("inputs")),
            ("missing-outputs", lambda c: c.pop("outputs")),
            ("empty-task-id", lambda c: c.update(task_id="")),
            ("numeric-task-id", lambda c: c.update(task_id=7)),
            ("relative-root", lambda c: c.update(project_root="project")),
            ("invalid-mode", lambda c: c.update(mode="other")),
            ("inputs-not-array", lambda c: c.update(inputs={})),
            ("outputs-not-array", lambda c: c.update(outputs={})),
            ("wrong-schema", lambda c: c.update(schema_version="devforge.delivery-task/v0")),
        ]
        for name, mutate in mutations:
            with self.subTest(case=name):
                f = self.fixture()
                mutate(f.contract)
                f.write_contract()
                self.assert_result(core.prepare(f.contract_path), "FAIL")
                self.assert_rejected_without_receipt(f)

    def test_contract_requires_exact_input_and_output_entry_shapes(self):
        mutations = [
            ("input-extra", lambda c: c["inputs"][0].update(note="not selected")),
            ("input-missing-pin", lambda c: c["inputs"][0].pop("sha256")),
            ("input-uppercase-pin", lambda c: c["inputs"][0].update(sha256="A" * 64)),
            ("input-short-pin", lambda c: c["inputs"][0].update(sha256="a" * 63)),
            ("output-extra", lambda c: c["outputs"][0].update(optional=True)),
            ("output-missing-id", lambda c: c["outputs"][0].pop("artifact_id")),
            ("output-empty-id", lambda c: c["outputs"][0].update(artifact_id="")),
            ("output-no-sections", lambda c: c["outputs"][0].update(sections=[])),
            ("output-empty-section", lambda c: c["outputs"][0].update(sections=[""])),
            ("output-duplicate-section", lambda c: c["outputs"][0].update(sections=["FOCUS-001", "FOCUS-001"])),
            ("output-numeric-section", lambda c: c["outputs"][0].update(sections=[1])),
        ]
        for name, mutate in mutations:
            with self.subTest(case=name):
                f = self.fixture()
                mutate(f.contract)
                f.write_contract()
                self.assert_rejected_without_receipt(f)

    def test_contract_revision_is_a_positive_integer_not_a_numeric_equivalent(self):
        for revision in (True, False, 1.0, 0, -1, "1", None):
            with self.subTest(revision=revision, type=type(revision).__name__):
                f = self.fixture()
                f.contract["outputs"][0]["revision"] = revision
                f.write_contract()
                self.assert_rejected_without_receipt(f)

    def test_role_path_id_and_input_output_collisions_are_invalid(self):
        mutations = [
            ("duplicate-role", lambda c: c["outputs"][0].update(artifact_type="handoff")),
            ("duplicate-id", lambda c: c["outputs"][1].update(artifact_id=c["outputs"][0]["artifact_id"])),
            ("duplicate-path", lambda c: c["outputs"][1].update(path=c["outputs"][0]["path"])),
            ("duplicate-input", lambda c: c["inputs"].append(copy.deepcopy(c["inputs"][0]))),
            ("input-output-overlap", lambda c: c["outputs"][0].update(path=c["inputs"][0]["path"])),
            ("missing-brainstorm-role", lambda c: c["outputs"].pop(0)),
            ("extra-output", lambda c: c["outputs"].append(copy.deepcopy(c["outputs"][0]))),
        ]
        for name, mutate in mutations:
            with self.subTest(case=name):
                f = self.fixture()
                mutate(f.contract)
                f.write_contract()
                self.assert_result(core.prepare(f.contract_path), "FAIL")
                self.assert_rejected_without_receipt(f)

    def test_duplicate_json_contract_keys_are_rejected_at_each_level(self):
        for level in ("top", "input", "output"):
            with self.subTest(level=level):
                f = self.fixture()
                raw = f.contract_bytes().decode()
                if level == "top":
                    needle = '"task_id": "TASK-DELIVERY-001"'
                elif level == "input":
                    needle = '"sha256": "' + f.contract["inputs"][0]["sha256"] + '"'
                else:
                    needle = '"revision": 1'
                self.assertIn(needle, raw)
                f.contract_path.write_text(raw.replace(needle, needle + ", " + needle, 1))
                self.assert_rejected_without_receipt(f)

    def test_contract_root_must_be_an_object_and_json_must_parse(self):
        for raw in (b"[]", b"null", b"true", b"{broken", b""):
            with self.subTest(raw=raw):
                f = self.fixture()
                f.contract_path.write_bytes(raw)
                self.assert_rejected_without_receipt(f)

    def test_contract_cannot_live_inside_worker_project(self):
        f = self.fixture()
        f.contract_path = f.project / "task.json"
        f.write_contract()
        self.assert_rejected_without_receipt(f)

    def test_selected_target_paths_reject_noncanonical_or_escaping_forms(self):
        invalid = ["", "/tmp/ideas.md", ".", "..", "./docs/ideas.md", "docs/../ideas.md",
                   "docs/./ideas.md", "docs//ideas.md", "docs/ideas.md/", "docs\\ideas.md", "docs/\x00ideas.md"]
        for role in ("inputs", "outputs"):
            for path in invalid:
                with self.subTest(role=role, path=repr(path)):
                    f = self.fixture()
                    f.contract[role][0]["path"] = path
                    f.write_contract()
                    self.assert_rejected_without_receipt(f)

    def test_envelope_selected_identity_type_revision_schema_and_draft_status(self):
        variants = [("artifact_id", "OTHER-001"), ("artifact_type", "handoff"),
                    ("schema_version", "devforge.artifact/v0"), ("status", "accepted"),
                    ("revision", True), ("revision", 1.0), ("revision", 0), ("revision", "1")]
        for syntax in ("yaml", "json"):
            for field, value in variants:
                with self.subTest(syntax=syntax, field=field, value=value):
                    f = self.fixture(syntax=syntax)
                    f.ledger_envelope[field] = value
                    f.write_ledger()
                    self.assert_rejected_without_receipt(f)

    def test_required_envelope_fields_cannot_be_absent(self):
        for field in ("schema_version", "artifact_id", "artifact_type", "revision", "status"):
            with self.subTest(field=field):
                f = self.fixture()
                del f.ledger_envelope[field]
                f.write_ledger()
                self.assert_rejected_without_receipt(f)

    def test_duplicate_yaml_and_json_envelope_keys_are_rejected(self):
        for syntax in ("yaml", "json"):
            with self.subTest(syntax=syntax):
                f = self.fixture(syntax=syntax)
                raw = f.ledger_path.read_text()
                if syntax == "yaml":
                    raw = raw.replace("revision: 1\n", "revision: 1\nrevision: 1\n", 1)
                else:
                    raw = raw.replace('"revision": 1,', '"revision": 1, "revision": 1,', 1)
                f.put_ledger_bytes(raw.encode())
                self.assert_rejected_without_receipt(f)

    def test_duplicate_yaml_reference_keys_are_rejected(self):
        f = self.fixture()
        raw = f.handoff_path.read_text()
        needle = "  revision: 1\n"
        self.assertIn(needle, raw)
        f.handoff_path.write_text(raw.replace(needle, needle + needle, 1))
        self.assert_rejected_without_receipt(f)

    def test_missing_duplicate_empty_or_fenced_required_sections_fail(self):
        bodies = {
            "missing": "## IDEAS-SECTION-001\n\nAn idea.\n",
            "duplicate": "## FOCUS-001\n\nFirst.\n\n## FOCUS-001\n\nSecond.\n\n## IDEAS-SECTION-001\n\nAn idea.\n",
            "empty": "## FOCUS-001\n   \n\t\n## IDEAS-SECTION-001\n\nAn idea.\n",
            "fenced-only": "```markdown\n## FOCUS-001\nNot a real section.\n```\n\n## IDEAS-SECTION-001\n\nAn idea.\n",
        }
        for name, body in bodies.items():
            with self.subTest(case=name):
                f = self.fixture()
                f.ledger_body = body
                f.write_ledger()
                self.assert_rejected_without_receipt(f)

    def test_fenced_decoy_duplicate_does_not_count_as_a_real_heading(self):
        f = self.fixture()
        f.ledger_body += "\n```markdown\n## FOCUS-001\nA code example only.\n```\n"
        f.write_ledger()
        self.assert_checked(f)

    def test_fence_closing_requires_same_character_and_sufficient_length(self):
        for opener, closer in [("````markdown", "```"), ("~~~~markdown", "~~~"),
                               ("```markdown", "~~~"), ("~~~markdown", "```"),
                               ("```markdown", "``` trailing-text")]:
            with self.subTest(opener=opener, closer=closer):
                f = self.fixture()
                f.ledger_body = opener + "\nExample text.\n" + closer + "\n\n" + f.ledger_body
                f.write_ledger()
                self.assert_rejected_without_receipt(f)

    def test_equal_or_longer_matching_fence_closes_before_real_headings(self):
        for opener, closer in [("````markdown", "````"), ("```markdown", "`````"),
                               ("~~~~markdown", "~~~~"), ("~~~markdown", "~~~~~")]:
            with self.subTest(opener=opener, closer=closer):
                f = self.fixture()
                f.ledger_body = opener + "\n## FOCUS-001\nA fenced decoy.\n" + closer + "\n\n" + f.ledger_body
                f.write_ledger()
                self.assert_checked(f)

    def test_unresolved_template_marker_prevents_receipt(self):
        for which in ("ledger", "handoff"):
            with self.subTest(output=which):
                f = self.fixture()
                if which == "ledger":
                    f.ledger_body += "\n{{UNRESOLVED_VALUE}}\n"
                    f.write_ledger()
                else:
                    f.handoff_body += "\n{{UNRESOLVED_VALUE}}\n"
                    f.write_handoff()
                self.assert_rejected_without_receipt(f)

    def test_bad_ledger_upstream_binding_fails_even_when_artifacts_parse(self):
        variants = [("artifact_id", "WRONG-001"), ("revision", 2), ("revision", True),
                    ("revision", 1.0), ("store", "authority"), ("path", "docs/handoff.md"),
                    ("sha256", "0" * 64), ("sections", ["MISSING-001"])]
        for field, value in variants:
            with self.subTest(field=field, value=value):
                f = self.fixture()
                f.handoff_envelope["upstream"][0][field] = value
                f.write_handoff()
                self.assert_rejected_without_receipt(f)

    def test_handoff_must_actually_bind_ledger_and_required_reference_fields(self):
        for missing in ("entire-reference", "artifact_id", "revision", "store", "path", "sha256", "sections"):
            with self.subTest(missing=missing):
                f = self.fixture()
                if missing == "entire-reference":
                    f.handoff_envelope["upstream"] = []
                else:
                    del f.handoff_envelope["upstream"][0][missing]
                f.write_handoff()
                self.assert_rejected_without_receipt(f)

    def test_output_is_nonempty_utf8_regular_file(self):
        for value in (b"", b"\xff\xfe", b"ordinary text without frontmatter\n"):
            with self.subTest(value=value):
                f = self.fixture()
                f.put_ledger_bytes(value)
                self.assert_rejected_without_receipt(f)
        f = self.fixture()
        f.ledger_path.unlink()
        f.ledger_path.mkdir()
        self.assert_rejected_without_receipt(f)

    def test_output_byte_limit_is_inclusive(self):
        for size, expected in ((OUTPUT_LIMIT, "PASS"), (OUTPUT_LIMIT + 1, "FAIL")):
            with self.subTest(size=size):
                f = self.fixture()
                original = f.ledger_path.read_bytes()
                f.put_ledger_bytes(original + b"x" * (size - len(original)))
                self.assertEqual(f.ledger_path.stat().st_size, size)
                if expected == "PASS":
                    self.assert_checked(f)
                else:
                    self.assert_rejected_without_receipt(f)

    def test_input_byte_limit_and_empty_input(self):
        for size, expected in ((INPUT_LIMIT, "PASS"), (INPUT_LIMIT + 1, "FAIL"), (0, "FAIL")):
            with self.subTest(size=size):
                f = self.fixture()
                data = b"i" * size
                f.input_path.write_bytes(data)
                f.contract["inputs"][0]["sha256"] = digest(data)
                f.write_contract()
                if expected == "PASS":
                    self.assert_checked(f)
                else:
                    self.assert_rejected_without_receipt(f)

    def test_contract_byte_limit_is_inclusive(self):
        for size, expected in ((CONTRACT_LIMIT, "PASS"), (CONTRACT_LIMIT + 1, "FAIL")):
            with self.subTest(size=size):
                f = self.fixture()
                f.contract["task_id"] += "x" * (size - len(f.contract_bytes()))
                f.write_contract()
                self.assertEqual(f.contract_path.stat().st_size, size)
                if expected == "PASS":
                    self.assert_checked(f)
                else:
                    self.assert_rejected_without_receipt(f)

    def test_symlink_target_is_rejected_even_with_identical_selected_bytes(self):
        for which in ("input_path", "ledger_path"):
            with self.subTest(target=which):
                f = self.fixture()
                target = getattr(f, which)
                other = f.operator / "identical-target.txt"
                other.write_bytes(target.read_bytes())
                target.unlink()
                target.symlink_to(other)
                self.assert_rejected_without_receipt(f)

    def test_symlink_directory_component_is_rejected_even_within_project(self):
        f = self.fixture()
        (f.project / "alias").symlink_to(f.project / "docs", target_is_directory=True)
        f.contract["outputs"][0]["path"] = "alias/ideas.md"
        f.handoff_envelope["upstream"][0]["path"] = "alias/ideas.md"
        f.write_contract()
        f.write_handoff()
        self.assert_rejected_without_receipt(f)

    def test_hardlinked_input_and_output_are_rejected_without_mutating_peer(self):
        for which in ("input_path", "ledger_path"):
            with self.subTest(target=which):
                f = self.fixture()
                target = getattr(f, which)
                peer = f.operator / "linked-peer.txt"
                original = target.read_bytes()
                os.link(target, peer)
                self.assert_rejected_without_receipt(f)
                self.assertEqual(peer.read_bytes(), original)

    def test_finalizer_rejects_receipt_inside_project(self):
        f = self.fixture()
        inside = f.project / "receipt.json"
        self.assert_result(core.finalize(f.contract_path, inside), "FAIL")
        self.assertFalse(inside.exists())

    def test_finalizer_rejects_external_alias_into_project(self):
        f = self.fixture()
        alias = f.operator / "alias"
        alias.symlink_to(f.project, target_is_directory=True)
        target = alias / "receipt.json"
        self.assert_result(core.finalize(f.contract_path, target), "FAIL")
        self.assertFalse((f.project / "receipt.json").exists())

    def test_existing_receipt_collision_preserves_exact_bytes(self):
        f = self.fixture()
        original = b"An unrelated pre-existing operator record.\n"
        f.receipt_path.write_bytes(original)
        self.assert_result(core.finalize(f.contract_path, f.receipt_path), "FAIL")
        self.assertEqual(f.receipt_path.read_bytes(), original)

    def test_second_finalization_is_a_collision_not_idempotent_overwrite(self):
        f = self.fixture()
        self.finalized(f)
        original = f.receipt_path.read_bytes()
        self.assert_result(core.finalize(f.contract_path, f.receipt_path), "FAIL")
        self.assertEqual(f.receipt_path.read_bytes(), original)

    def test_verification_rejects_receipt_copied_to_another_task(self):
        original = self.fixture()
        self.finalized(original)
        other = self.fixture()
        other.contract["task_id"] = "TASK-OTHER-002"
        other.write_contract()
        self.assert_checked(other)
        other.receipt_path.write_bytes(original.receipt_path.read_bytes())
        self.assert_result(core.verify(other.contract_path, other.receipt_path), "FAIL")

    def test_verification_rejects_copied_receipt_at_another_path(self):
        f = self.fixture()
        self.finalized(f)
        copied = f.operator / "copied-receipt.json"
        copied.write_bytes(f.receipt_path.read_bytes())
        self.assert_result(core.verify(f.contract_path, copied), "FAIL")

    def test_verification_rejects_stale_outputs_even_if_current_pair_is_valid(self):
        f = self.fixture()
        self.finalized(f)
        original_receipt = f.receipt_path.read_bytes()
        f.ledger_body += "\nA later proposed option, still a draft.\n"
        f.write_ledger()  # Updates the handoff backlink too: mechanical checks remain valid.
        self.assert_result(core.check(f.contract_path))
        self.assert_result(core.verify(f.contract_path, f.receipt_path), "FAIL")
        self.assertEqual(f.receipt_path.read_bytes(), original_receipt)

    def test_verification_rechecks_pinned_inputs(self):
        f = self.fixture()
        self.finalized(f)
        original_receipt = f.receipt_path.read_bytes()
        f.input_path.write_bytes(b"A later unselected source.\n")
        self.assert_result(core.verify(f.contract_path, f.receipt_path), "FAIL")
        self.assertEqual(f.receipt_path.read_bytes(), original_receipt)

    def test_verification_binds_contract_bytes_even_if_semantics_are_unchanged(self):
        f = self.fixture()
        self.finalized(f)
        f.contract_path.write_bytes(f.contract_path.read_bytes() + b" \n")
        self.assert_result(core.check(f.contract_path))
        self.assert_result(core.verify(f.contract_path, f.receipt_path), "FAIL")

    def test_verification_rejects_malformed_or_incomplete_receipts(self):
        for raw in (b"{broken", b"[]", b"{}", b"null"):
            with self.subTest(raw=raw):
                f = self.fixture()
                f.receipt_path.write_bytes(raw)
                self.assert_result(core.verify(f.contract_path, f.receipt_path), "FAIL")

    def test_verification_rejects_forged_checked_hash_maps(self):
        for field in ("inputs", "outputs"):
            with self.subTest(field=field):
                f = self.fixture()
                _, receipt = self.finalized(f)
                first = next(iter(receipt[field]))
                receipt[field][first] = "0" * 64
                f.receipt_path.write_text(json.dumps(receipt))
                self.assert_result(core.verify(f.contract_path, f.receipt_path), "FAIL")

    def test_verification_checks_each_task_contract_and_project_binding(self):
        for field, value in (("task_id", "TASK-WRONG"), ("contract_sha256", "0" * 64),
                             ("project_root", "/unselected-project")):
            with self.subTest(field=field):
                f = self.fixture()
                _, receipt = self.finalized(f)
                receipt[field] = value
                f.receipt_path.write_text(json.dumps(receipt))
                self.assert_result(core.verify(f.contract_path, f.receipt_path), "FAIL")

    def test_verify_repeats_mechanical_checks_even_with_matching_forged_hashes(self):
        f = self.fixture()
        self.finalized(f)
        f.ledger_envelope["status"] = "accepted"
        f.write_ledger()
        receipt = json.loads(f.receipt_path.read_bytes())
        receipt["outputs"] = f.expected_outputs()
        f.receipt_path.write_text(json.dumps(receipt))
        self.assert_result(core.verify(f.contract_path, f.receipt_path), "FAIL")


if __name__ == "__main__":
    unittest.main()
