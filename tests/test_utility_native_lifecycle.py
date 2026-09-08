"""Deterministic native lifecycle tests; none establishes native execution.

No native process, model, authentication profile, or global client state is used.
The fake collector stops at a protected claim. Host-owned test keys and manually
constructed signed receipts exercise successful importer/reviewer transitions
only inside disposable fixture state. Production run_fixture output and signed
FIXTURE_ONLY receipts remain rejected. Passing these tests is not native evidence.
"""
import copy
from datetime import timedelta
import hashlib
import hmac
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime" / "delivery"))
from test_utility_schedule import ScheduleFixture
from test_utility_state import Fixture, NOW, encoded
import delivery_core as core
import native_process
import phase_state as store
import utility_evidence
import utility_state as utility


class NativeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = mock.patch.object(store, "utc_now", return_value=NOW)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.f = ScheduleFixture(self.root)
        self.state = self.f.validator.state

    def freeze_allocation(self):
        plan = self.f.native.plan
        owner = self.f.native.owner
        calls = [{**{k: a[k] for k in ("attempt_id", "case_id", "tier", "arm", "repetition")},
                  "purpose": "case", "review_path": str(owner / (a["attempt_id"] + "-review.json")),
                  "reviewer": "Independent fixture reviewer", "interaction": "single-turn",
                  "managed_worker_required": False} for a in plan["attempts"]]
        self.cases_path = owner / "cases.json"
        self.cases_path.write_bytes(encoded({"schema_version": "devforge.utility-native-cases/v1",
                                            "task_id": plan["task_id"], "required_calls": calls}))
        plan["cases"] = Fixture.pin(self.cases_path)
        self.allocation = {"schema_version": "devforge.utility-native-allocation/v1", "task_id": plan["task_id"],
                           "cases_sha256": plan["cases"]["sha256"], "max_total_attempts": 9, "preparation_attempts": 3,
                           "max_seconds": 120, "per_attempt_max_seconds": 20, "required_calls": calls}
        self.allocation_path = owner / "allocation.json"
        self.runtime_path = owner / "runtime_configuration.json"
        self.runtime = {"schema_version": "devforge.native-runtime-configuration/v1", "allocation": {},
                        "attempts": [{"attempt_id": c["attempt_id"], "interaction": "single-turn", "managed_worker": None}
                                     for c in calls]}
        self.refresh_allocation()

    def refresh_allocation(self):
        self.allocation_path.write_bytes(encoded(self.allocation))
        self.runtime["allocation"] = Fixture.pin(self.allocation_path)
        self.runtime_path.write_bytes(encoded(self.runtime))
        self.f.native.plan["runtime_configuration"] = Fixture.pin(self.runtime_path)
        self.f.native.save()
        self.f.binding["plan"] = Fixture.pin(self.f.native.plan_path)
        self.f.binding_path.write_bytes(encoded(self.f.binding))
        gate = Path(next(g["path"] for g in self.f.validator.delivery["gate_inputs"] if g["id"] == "native-prerequisites"))
        value = json.loads(gate.read_bytes())
        value["evidence"] = [Fixture.pin(self.f.native.plan_path)]
        gate.write_bytes(encoded(value))

    def reserve(self):
        self.f.p4()
        self.assertEqual(self.f.bind()["status"], "NATIVE_SCHEDULE_BOUND")
        self.assertEqual(utility.native_schedule_reserve(self.state)["status"], "NATIVE_SCHEDULE_RECORDED")

    def claim_fixture(self):
        self.freeze_allocation()
        self.reserve()
        return self.claim_reserved("C-candidate")

    def claim_reserved(self, identity):
        def prepare(plan, attempt, binding, reserved_at, deadline, campaign_origin_utc, installed_inputs):
            return {"binding": binding, "provenance": "FIXTURE_ONLY", "reserved_at": reserved_at,
                    "deadline": deadline, "campaign_origin_utc": campaign_origin_utc}
        def stopped(authority, request, *, check_reservation, elapsed_seconds):
            # The public operation must have released its journal lock before
            # invoking the collector; this lock must therefore be acquirable.
            with store._lock(self.state):
                pass
            check_reservation(request)
            raise native_process.NativeProcessError("FIXTURE_ONLY: no process is launched")
        fake = SimpleNamespace(prepare_request=prepare, launch=stopped, verify_receipt=native_process.verify_receipt,
                               digest=native_process.digest, canonical_json=native_process.canonical_json)
        with mock.patch.object(utility, "_native_collector", return_value=fake):
            result = utility.native_process_launch(self.state, identity)
        self.assertEqual(result["status"], "COULD_NOT_RUN", result)
        self.assertIn("FIXTURE_ONLY", result["issues"][0])
        with store._lock(self.state) as fd:
            state = utility.State(self.state, fd)
            self.request = state.native_claims[identity]["request"]
            self.binding = self.request["binding"]
        return result

    def host_signed_test_receipt(self, process_overrides=None, freshness="INTACT"):
        """Manual host-owned TEST ONLY receipt; no fixture process is relabeled."""
        process = {"status": "EXITED", "exit_code": 0, "leader_reaped": True, "group_absent": True,
                   "stdout_complete": True, "stderr_complete": True, "output_limit_exceeded": False,
                   "started_elapsed_seconds": self.request["reserved_at"],
                   "finished_elapsed_seconds": self.request["reserved_at"],
                   "deadline_elapsed_seconds": self.request["deadline"], "issues": []}
        process.update(process_overrides or {})
        stdout = (b'{"type":"thread.started","thread_id":"HOST-OWNED-TEST-FIXTURE"}\n'
                  b'{"type":"turn.started"}\n{"type":"turn.completed",'
                  b'"usage":{"input_tokens":1,"cached_input_tokens":0,"output_tokens":1}}\n')
        body = {"schema_version": "devforge.native-process-receipt/v1", "provenance": "OWNED_NATIVE_COLLECTOR",
                "binding": self.binding, "process": process,
                "freshness": {"status": freshness, "inputs_sha256": "1" * 64},
                "events": {"status": "OBSERVED", "thread_started": True, "turn_started": True,
                           "turn_completed": True, "turn_failed": False, "errors": 0, "issues": []},
                "campaign_origin_utc": self.request["campaign_origin_utc"],
                "request_sha256": native_process.digest(native_process.canonical_json(self.request)),
                "semantic_grade": "NOT_EVALUATED", "native_callback_authentication": "NOT_EVALUATED",
                "managed_worker": {"required": False, "status": "NOT_APPLICABLE", "task_id": None,
                                   "broker_quiescent": True, "callbacks": 0, "callback_origin": "NOT_AUTHENTICATED",
                                   "task_result": None, "evidence": []},
                "test_scope": "HOST-OWNED DETERMINISTIC FIXTURE ONLY; NO NATIVE EXECUTION"}
        if not process["stdout_complete"] or process["output_limit_exceeded"]:
            body["events"] = {"status": "UNOBTAINABLE", "thread_started": False, "turn_started": False,
                              "turn_completed": False, "turn_failed": False, "errors": 0,
                              "issues": ["Empty, truncated, or incomplete JSONL stream"]}
        path = self.fake_receipt(body, signed=True)
        (path.parent / "request.json").write_bytes(native_process.canonical_json(self.request))
        for name, raw in (("stdout", stdout), ("stderr", b"")):
            stream = path.parent / (name + ".bin")
            stream.write_bytes(raw)
            body[name] = {"path": str(stream), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        return self.fake_receipt(body, signed=True)

    def write_review(self, identity, receipt, outcome):
        call = next(c for c in self.allocation["required_calls"] if c["attempt_id"] == identity)
        path = Path(call["review_path"])
        value = {"schema_version": "devforge.utility-native-review/v1", "task_id": "UTILITY-001", "attempt_id": identity,
                 "process_receipt_sha256": Fixture.pin(receipt)["sha256"], "binding": self.binding,
                 "reviewer": call["reviewer"], "outcome": outcome,
                 "reason": "HOST-OWNED TEST FIXTURE ONLY; no semantic/native quality claim",
                 "evidence": [self.f.native.plan["specification"]]}
        path.write_bytes(encoded(value))
        return path

    def fake_receipt(self, body, *, signed=False):
        authority = self.state / "native-collector"
        authority.mkdir(mode=0o700, exist_ok=True)
        key = b"deterministic-fixture-key-only!!"
        self.assertEqual(len(key), 32)
        keypath = authority / "collector.key"
        keypath.write_bytes(key)
        keypath.chmod(0o600)
        parent = authority / native_process.digest(native_process.canonical_json(self.binding))
        parent.mkdir(mode=0o700, exist_ok=True)
        signature = hmac.new(key, native_process.canonical_json(body), hashlib.sha256).hexdigest() if signed else "0" * 64
        path = parent / "receipt.json"
        path.write_bytes(encoded({"body": body, "hmac_sha256": signature}))
        return path

    def test_legacy_planning_plan_cannot_bind_reserve_or_launch(self):
        self.freeze_allocation()
        self.runtime["schema_version"] = "fixture/legacy-runtime"
        self.refresh_allocation()
        self.f.p4()
        before = self.f.head()
        result = self.f.bind()
        self.assertEqual(result["status"], "FAIL", result)
        self.assertIn("versioned runtime configuration", result["issues"][0])
        self.assertEqual(utility.native_schedule_reserve(self.state)["status"], "FAIL")
        self.assertEqual(utility.native_process_launch(self.state, "C-candidate")["status"], "FAIL")
        self.assertEqual(self.f.head(), before)
        self.assertNotIn("native_schedule", utility.context(self.state))
        self.assertFalse((self.state / "native-collector").exists())

    def test_incomplete_mismatched_and_over_cap_preflight_never_binds_or_consumes(self):
        for defect in ("missing-allocation", "missing-case-oracle", "mismatched-coverage", "cap-above-24",
                       "preparation-over-cap", "malformed-runtime-row"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as scratch:
                self.f = ScheduleFixture(Path(scratch))
                self.state = self.f.validator.state
                self.freeze_allocation()
                if defect == "mismatched-coverage":
                    self.allocation["required_calls"].pop()
                elif defect == "cap-above-24":
                    self.allocation["max_total_attempts"] = 25
                elif defect == "preparation-over-cap":
                    self.allocation.update(max_total_attempts=24, preparation_attempts=19)
                elif defect == "malformed-runtime-row":
                    self.runtime["attempts"][0] = None
                self.refresh_allocation()
                if defect == "missing-allocation":
                    self.allocation_path.unlink()
                elif defect == "missing-case-oracle":
                    self.cases_path.unlink()
                self.f.p4()
                before = self.f.head()
                snapshots = sorted(p.name for p in (self.state / "snapshots").iterdir())
                with mock.patch.object(utility, "_native_collector", side_effect=AssertionError("Preflight must not invoke a collector")):
                    result = self.f.bind()
                    self.assertIn(result["status"], {"FAIL", "COULD_NOT_RUN"}, result)
                    self.assertEqual(utility.native_schedule_reserve(self.state)["status"], "FAIL")
                self.assertEqual(self.f.head(), before)
                self.assertEqual(sorted(p.name for p in (self.state / "snapshots").iterdir()), snapshots)
                self.assertNotIn("native_schedule", utility.context(self.state))
                self.assertFalse((self.state / "native-collector").exists())

    def test_complete_binding_snapshots_and_drift_block_reserve_without_resetting_cleanup_clock(self):
        self.freeze_allocation()
        self.f.p4()
        bound = self.f.bind()
        self.assertEqual(bound["status"], "NATIVE_SCHEDULE_BOUND", bound)
        self.assertEqual(bound["native_schedule"]["complete_allocation"], Fixture.pin(self.allocation_path))
        before = self.f.head()
        head = json.loads(before)
        record = json.loads((self.state / "records" / (head["records"][-1] + ".json")).read_bytes())
        refs = {(r["kind"], r["source"]): r["sha256"] for r in record["snapshots"]}
        self.assertEqual(refs[("native_allocation", str(self.allocation_path))], Fixture.pin(self.allocation_path)["sha256"])
        self.assertEqual(refs[("native_runtime", str(self.runtime_path))], Fixture.pin(self.runtime_path)["sha256"])
        self.assertEqual(refs[("native_input", str(self.cases_path))], Fixture.pin(self.cases_path)["sha256"])
        self.allocation_path.write_bytes(self.allocation_path.read_bytes() + b"\n")
        self.assertEqual(utility.native_schedule_reserve(self.state)["status"], "FAIL")
        self.assertEqual(self.f.head(), before)
        self.now.return_value = NOW + timedelta(hours=1)
        with store._lock(self.state) as fd:
            cleanup = utility.State(self.state, fd, cleanup=True)
            self.assertEqual(cleanup.schedule.origin, NOW)
            self.assertEqual(len(cleanup.schedule.state.reservations), 0)
            self.assertEqual(cleanup.schedule_allocation, bound["native_schedule"]["complete_allocation"])

    def test_replayed_binding_without_complete_snapshot_identity_cannot_reserve(self):
        self.freeze_allocation()
        self.f.p4()
        self.assertEqual(self.f.bind()["status"], "NATIVE_SCHEDULE_BOUND")
        # Simulate the historical incomplete binding record inside disposable
        # host-owned fixture state, retaining a valid journal hash chain.
        head = json.loads(self.f.head())
        record = json.loads((self.state / "records" / (head["records"][-1] + ".json")).read_bytes())
        record["snapshots"] = [r for r in record["snapshots"] if r["kind"] not in {"native_allocation", "native_runtime"}]
        raw = store._dump(record)
        digest = store._hash(raw)
        with store._lock(self.state) as fd:
            store._publish(fd, "records/" + digest + ".json", raw)
            head["records"][-1] = digest
            store._publish(fd, "HEAD.json", store._dump(head), replace=True)
        before = self.f.head()
        result = utility.native_schedule_reserve(self.state)
        self.assertEqual(result["status"], "FAIL", result)
        self.assertIn("snapshots differ", result["issues"][0])
        self.assertEqual(self.f.head(), before)

    def test_claim_persists_outside_lock_and_resume_cannot_relaunch(self):
        self.claim_fixture()
        before = self.f.head()
        result = utility.native_process_launch(self.state, "C-candidate")
        self.assertEqual(result["status"], "FAIL", result)
        self.assertIn("already claimed", result["issues"][0])
        self.assertEqual(self.f.head(), before)
        current = utility.context(self.state)
        self.assertEqual(current["native_schedule"]["reservations_consumed"], 1)
        self.assertEqual(current["native_schedule"]["execution"], "LAUNCH_CLAIMED")
        self.assertEqual(current["native_schedule"]["imported_attempts"], [])

    def test_claim_cannot_be_disguised_as_unlaunched_cancellation(self):
        self.claim_fixture()
        before = self.f.head()
        result = utility.native_schedule_cancel(self.state, "C-candidate", "Pretend no launch claim exists")
        self.assertEqual(result["status"], "FAIL", result)
        self.assertIn("claimed launch", result["issues"][0])
        self.assertEqual(self.f.head(), before)

    def test_unsigned_forged_and_signed_fixture_receipts_never_import(self):
        self.claim_fixture()
        body = {"schema_version": "devforge.native-process-receipt/v1", "provenance": "FIXTURE_ONLY", "binding": self.binding,
                "semantic_grade": "NOT_EVALUATED", "native_callback_authentication": "NOT_EVALUATED"}
        for signed in (False, True):
            with self.subTest(signed=signed):
                path = self.fake_receipt(body, signed=signed)
                before = self.f.head()
                result = utility.native_process_import(self.state, "C-candidate", path)
                self.assertEqual(result["status"], "COULD_NOT_RUN", result)
                self.assertEqual(self.f.head(), before)
        self.assertEqual(utility.context(self.state)["native_schedule"]["imported_attempts"], [])

    def test_importer_independently_rejects_fixture_provenance(self):
        self.claim_fixture()
        path = self.fake_receipt({"provenance": "FIXTURE_ONLY"})
        before = self.f.head()
        fake = SimpleNamespace(verify_receipt=lambda *args: {"provenance": "FIXTURE_ONLY"})
        with mock.patch.object(utility, "_native_collector", return_value=fake):
            result = utility.native_process_import(self.state, "C-candidate", path)
        self.assertEqual(result["status"], "FAIL", result)
        self.assertIn("fixture or foreign receipt", result["issues"][0])
        self.assertEqual(self.f.head(), before)

    def test_foreign_receipt_path_and_wrong_attempt_fail_before_read(self):
        self.claim_fixture()
        before = self.f.head()
        result = utility.native_process_import(self.state, "C-candidate", self.root / "foreign.json")
        self.assertEqual(result["status"], "FAIL", result)
        result = utility.native_process_import(self.state, "C-baseline", self.state / "native-collector" / "missing.json")
        self.assertEqual(result["status"], "FAIL", result)
        self.assertEqual(self.f.head(), before)

    def test_clock_expiry_never_refills_or_relaunches_claim(self):
        self.claim_fixture()
        self.now.return_value = NOW + timedelta(hours=1)
        before = self.f.head()
        self.assertEqual(utility.native_process_launch(self.state, "C-candidate")["status"], "FAIL")
        self.assertEqual(utility.native_schedule_reserve(self.state)["status"], "COULD_NOT_RUN")
        self.assertEqual(self.f.head(), before)
        with store._lock(self.state) as fd:
            cleanup = utility.State(self.state, fd, cleanup=True)
            self.assertEqual(cleanup.schedule.origin, NOW)
            self.assertEqual(len(cleanup.schedule.state.reservations), 1)
            self.assertTrue(cleanup.schedule.inflight())

    def test_source_drift_blocks_progress_but_cleanup_replays_original_snapshots(self):
        self.claim_fixture()
        self.cases_path.write_text("Source changed after launch claim")
        self.assertEqual(utility.context(self.state)["status"], "FAIL")
        with store._lock(self.state) as fd:
            cleanup = utility.State(self.state, fd, cleanup=True)
            self.assertIn("C-candidate", cleanup.native_claims)
            self.assertTrue(cleanup.schedule.inflight())
            self.assertNotEqual(cleanup.source(self.cases_path, "original cases"), self.cases_path.read_bytes())
        path = self.fake_receipt({"provenance": "FIXTURE_ONLY"})
        before = self.f.head()
        self.assertEqual(utility.native_process_import(self.state, "C-candidate", path)["status"], "COULD_NOT_RUN")
        self.assertEqual(self.f.head(), before)

    def test_review_and_ungraded_close_require_actual_authenticated_completion(self):
        self.claim_fixture()
        review_path = Path(self.allocation["required_calls"][0]["review_path"])
        review_path.write_bytes(encoded({"outcome": "PASS", "evidence": [Fixture.pin(self.cases_path)]}))
        before = self.f.head()
        self.assertEqual(utility.native_result_review(self.state, "C-candidate", review_path)["status"], "FAIL")
        self.assertEqual(utility.native_result_close(self.state, "C-candidate", "Unavailable grade")["status"], "FAIL")
        self.assertEqual(self.f.head(), before)

    def test_host_signed_fixture_import_waits_for_review_and_rejects_replay(self):
        self.claim_fixture()
        receipt = self.host_signed_test_receipt()
        result = utility.native_process_import(self.state, "C-candidate", receipt)
        self.assertEqual(result["status"], "NATIVE_PROCESS_IMPORTED", result)
        self.assertEqual(result["native_schedule"]["inflight_attempt"], "C-candidate")
        before = self.f.head()
        self.assertEqual(utility.native_process_import(self.state, "C-candidate", receipt)["status"], "FAIL")
        self.assertEqual(self.f.head(), before)
        review = self.write_review("C-candidate", receipt, "PASS")
        result = utility.native_result_review(self.state, "C-candidate", review)
        self.assertEqual(result["status"], "NATIVE_REVIEW_RECORDED", result)
        self.assertIsNone(result["native_schedule"]["inflight_attempt"])
        self.assertEqual(result["native_schedule"]["native_callback_authentication"], "NOT_EVALUATED")
        for snapshot in (self.state / "snapshots").glob("*.bin"):
            self.assertNotIn(b"deterministic-fixture-key-only!!", snapshot.read_bytes())
        before = self.f.head()
        self.assertEqual(utility.native_result_review(self.state, "C-candidate", review)["status"], "FAIL")
        self.assertEqual(self.f.head(), before)

    def test_host_signed_fixture_c_b_a_grades_and_exact_tier_coverage(self):
        self.freeze_allocation()
        self.reserve()
        for index, identity in enumerate(("C-candidate", "C-baseline", "B-candidate", "B-baseline", "A-candidate", "A-baseline")):
            if index:
                result = utility.native_schedule_reserve(self.state)
                self.assertEqual(result["native_schedule"]["inflight_attempt"], identity, result)
            self.claim_reserved(identity)
            receipt = self.host_signed_test_receipt()
            self.assertEqual(utility.native_process_import(self.state, identity, receipt)["status"], "NATIVE_PROCESS_IMPORTED")
            # An intact B FAIL still permits A, whereas C needs PASS.
            outcome = "FAIL" if identity == "B-candidate" else "PASS"
            review = self.write_review(identity, receipt, outcome)
            self.assertEqual(utility.native_result_review(self.state, identity, review)["status"], "NATIVE_REVIEW_RECORDED")
        with store._lock(self.state) as fd:
            state = utility.State(self.state, fd)
            for tier, outcome in (("C", "PASS"), ("B", "FAIL"), ("A", "PASS")):
                spec = next(g for g in state.cfg["contract"]["gate_inputs"] if g["id"] == "native-" + tier)
                evidence = [{"path": r["path"], "sha256": r["sha256"]}
                            for arm in ("candidate", "baseline")
                            for r in (state.native_imports[tier + "-" + arm], state.native_reviews[tier + "-" + arm])]
                state.check_native_gate(spec, {"outcome": outcome, "evidence": evidence})
                with self.assertRaises(core._Problem):
                    state.check_native_gate(spec, {"outcome": outcome, "evidence": evidence[:-1]})
                gate_path = Path(spec["path"])
                gate = json.loads(gate_path.read_bytes())
                gate.update(outcome=outcome, evidence=evidence,
                            reason="Signed host-owned fixture coverage only; no native evaluation")
                gate_path.write_bytes(encoded(gate))
        done = utility.native_schedule_reserve(self.state)
        self.assertEqual(done["native_schedule"]["decision"], "DONE")
        self.assertEqual(done["native_schedule"]["reservations_consumed"], 6)
        self.f.validator.checkpoint()
        advanced = utility.advance(self.state)
        self.assertEqual(advanced["status"], "PROGRESS", advanced)
        self.assertEqual(advanced["phase"], "P5")

    def test_host_signed_fixture_late_import_and_late_review_cleanup_only(self):
        self.claim_fixture()
        receipt = self.host_signed_test_receipt()
        self.now.return_value = NOW + timedelta(hours=1)
        result = utility.native_process_import(self.state, "C-candidate", receipt)
        self.assertEqual(result["status"], "NATIVE_CLEANUP_RECORDED", result)
        self.assertIsNone(result["native_schedule"]["inflight_attempt"])
        self.assertEqual(result["native_schedule"]["reviewed_attempts"], [])
        self.assertEqual(utility.native_schedule_reserve(self.state)["status"], "COULD_NOT_RUN")

    def test_host_signed_fixture_late_review_cannot_backdate_process_completion(self):
        self.claim_fixture()
        receipt = self.host_signed_test_receipt()
        self.assertEqual(utility.native_process_import(self.state, "C-candidate", receipt)["status"], "NATIVE_PROCESS_IMPORTED")
        review = self.write_review("C-candidate", receipt, "PASS")
        self.now.return_value = NOW + timedelta(seconds=20)
        before = self.f.head()
        result = utility.native_result_review(self.state, "C-candidate", review)
        self.assertEqual(result["status"], "FAIL", result)
        self.assertEqual(self.f.head(), before)
        self.now.return_value = NOW + timedelta(hours=1)
        result = utility.native_result_close(self.state, "C-candidate", "Original clock expired without review")
        self.assertEqual(result["status"], "NATIVE_UNGRADED_CLOSED", result)
        self.assertIsNone(result["native_schedule"]["inflight_attempt"])

    def test_host_signed_fixture_source_drift_preserves_authenticated_cleanup(self):
        self.claim_fixture()
        receipt = self.host_signed_test_receipt()
        self.cases_path.write_text("Changed after claim")
        Path(self.f.validator.session["installed_inputs"][0]["path"]).write_text("Installed input changed after claim")
        result = utility.native_process_import(self.state, "C-candidate", receipt)
        self.assertEqual(result["status"], "NATIVE_CLEANUP_RECORDED", result)
        self.assertIsNone(result["native_schedule"]["inflight_attempt"])
        self.assertEqual(utility.context(self.state)["status"], "FAIL")

    def test_host_signed_fixture_unproven_children_reject_and_missing_eof_cleans_up(self):
        self.claim_fixture()
        receipt = self.host_signed_test_receipt({"group_absent": False})
        before = self.f.head()
        result = utility.native_process_import(self.state, "C-candidate", receipt)
        self.assertEqual(result["status"], "FAIL", result)
        self.assertIn("cleanup is unproven", result["issues"][0])
        self.assertEqual(self.f.head(), before)
        receipt = self.host_signed_test_receipt({"stdout_complete": False})
        result = utility.native_process_import(self.state, "C-candidate", receipt)
        self.assertEqual(result["status"], "NATIVE_CLEANUP_RECORDED", result)
        self.assertIsNone(result["native_schedule"]["inflight_attempt"])

    def test_host_signed_fixture_cannot_substitute_original_clock_or_backdate_import(self):
        self.claim_fixture()
        for changes in ({"finished_elapsed_seconds": 1}, {"started_elapsed_seconds": -1},
                        {"deadline_elapsed_seconds": 600}):
            with self.subTest(changes=changes):
                receipt = self.host_signed_test_receipt(changes)
                before = self.f.head()
                result = utility.native_process_import(self.state, "C-candidate", receipt)
                self.assertEqual(result["status"], "FAIL", result)
                self.assertIn("original reservation and import clock", result["issues"][0])
                self.assertEqual(self.f.head(), before)

    def test_worker_json_cannot_supply_native_tier_coverage(self):
        self.claim_fixture()
        gate = next(g for g in self.f.validator.delivery["gate_inputs"] if g["id"] == "native-C")
        with store._lock(self.state) as fd:
            state = utility.State(self.state, fd)
            with self.assertRaisesRegex(core._Problem, "authenticated result importer coverage"):
                state.check_native_gate(gate, {"outcome": "PASS", "evidence": []})

    def test_preparation_calls_and_full_inventory_count_toward_total_cap(self):
        self.freeze_allocation()
        self.allocation["max_total_attempts"] = 8
        self.refresh_allocation()
        with self.assertRaisesRegex(core._Problem, "exceeds total calls"):
            utility_evidence.launch_allocation(self.f.native.plan)

    def test_dropped_required_calls_and_managed_worker_omission_fail(self):
        self.freeze_allocation()
        self.allocation["required_calls"].pop()
        self.refresh_allocation()
        with self.assertRaisesRegex(core._Problem, "exact independent required-call inventory"):
            utility_evidence.launch_allocation(self.f.native.plan)
        self.freeze_allocation()
        calls = self.allocation["required_calls"]
        calls[0]["managed_worker_required"] = True
        cases = {"schema_version": "devforge.utility-native-cases/v1", "task_id": "UTILITY-001", "required_calls": calls}
        self.cases_path.write_bytes(encoded(cases))
        self.f.native.plan["cases"] = Fixture.pin(self.cases_path)
        self.allocation["cases_sha256"] = self.f.native.plan["cases"]["sha256"]
        self.refresh_allocation()
        with self.assertRaisesRegex(core._Problem, "required interaction/managed worker"):
            utility_evidence.launch_allocation(self.f.native.plan)


class PureCompletionShapeTests(unittest.TestCase):
    """Shape-only checks; no body here is signed, imported, or native evidence."""
    def test_complete_shape_and_each_missing_observation(self):
        body = {"process": {"status": "EXITED", "exit_code": 0, "stdout_complete": True,
                            "stderr_complete": True, "output_limit_exceeded": False,
                            "leader_reaped": True, "group_absent": True},
                "events": {"status": "OBSERVED"}, "freshness": {"status": "INTACT"}}
        self.assertTrue(utility._native_grade_eligible(body))
        for group, key, value in (("process", "status", "TIMED_OUT"), ("process", "exit_code", 1),
                                  ("process", "stdout_complete", False), ("process", "stderr_complete", False),
                                  ("process", "leader_reaped", False), ("process", "group_absent", False),
                                  ("process", "output_limit_exceeded", True), ("events", "status", "UNOBTAINABLE"),
                                  ("freshness", "status", "CONTAMINATED")):
            with self.subTest(group=group, key=key):
                invalid = copy.deepcopy(body)
                invalid[group][key] = value
                self.assertFalse(utility._native_grade_eligible(invalid))
        self.assertFalse(utility._native_grade_eligible(body, True))
        body["managed_worker"] = {"required": True, "status": "COMPLETED", "broker_quiescent": True,
                                  "task_result": {"receipt_verified": True}}
        self.assertTrue(utility._native_grade_eligible(body, True))
        for key, value in (("status", "WAITING_USER"), ("broker_quiescent", False), ("task_result", None)):
            invalid = copy.deepcopy(body)
            invalid["managed_worker"][key] = value
            self.assertFalse(utility._native_grade_eligible(invalid, True))


if __name__ == "__main__":
    unittest.main()
