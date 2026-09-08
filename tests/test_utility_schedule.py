"""Protected scheduling integration using inert fixtures, never native clients."""
from datetime import datetime, timedelta, timezone
import copy
import json
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime" / "delivery"))
import test_utility_native_admission as admitted
from test_utility_state import NOW, Fixture, encoded
import utility_state as utility
import utility_schedule
import phase_state as store
import controller


class ScheduleFixture:
    def __init__(self, root):
        self.native = admitted.NativePlanFixture(root)
        plan = self.native.plan
        original = plan["attempts"][0]
        boundary = json.loads(self.native.boundary_path.read_bytes())
        observation = boundary["attempts"][0]["observations"]
        attempts, observations = [], []
        for tier in ("C", "B", "A"):
            for arm in ("candidate", "baseline"):
                identity = f"{tier}-{arm}"
                workspace, client = root / (identity + "-workspace"), root / (identity + "-client")
                workspace.mkdir()
                client.mkdir()
                attempts.append({**original, "attempt_id": identity, "case_id": tier + "-case", "tier": tier,
                                 "arm": arm, "workspace": str(workspace), "client_state": str(client), "max_seconds": 20})
                observations.append({"attempt_id": identity, "workspace": str(workspace), "client_state": str(client),
                                     "observations": observation})
        plan.update(attempts=attempts, max_attempts=6, max_seconds=120)
        boundary["attempts"] = observations
        self.native.boundary_path.write_bytes(encoded(boundary))
        plan["boundary_evidence"] = Fixture.pin(self.native.boundary_path)
        self.native.save()
        self.validator = admitted.NativeAdmissionTests.validator(SimpleNamespace(root=root, f=self.native))
        self.binding_path = self.validator.owner / "schedule.json"
        self.binding = {"schema_version": utility_schedule.SCHEMA, "task_id": "UTILITY-001",
                        "plan": Fixture.pin(self.native.plan_path), "required_predecessors": {
                            "C-candidate": [], "C-baseline": [],
                            "B-candidate": ["C-candidate"], "B-baseline": ["C-baseline"],
                            "A-candidate": ["C-candidate", "B-candidate"],
                            "A-baseline": ["C-baseline", "B-baseline"]}}
        self.binding_path.write_bytes(encoded(self.binding))

    def p4(self):
        self.validator.start()
        for phase in ("P1", "P2", "P3"):
            self.validator.checkpoint()
            result = utility.advance(self.validator.state)
            if result["status"] != "PROGRESS":
                raise AssertionError((phase, result))

    def bind(self):
        return utility.native_schedule_bind(self.validator.state, self.binding_path)

    def head(self):
        return (self.validator.state / "HEAD.json").read_bytes()


class UtilityScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = mock.patch.object(store, "utc_now", return_value=NOW)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        package_clock = mock.patch.object(controller.workflow_runtime.phase_state, "utc_now", self.now)
        package_clock.start()
        self.addCleanup(package_clock.stop)
        self.f = ScheduleFixture(Path(self.tmp.name))
        self.state = self.f.validator.state

    def bound(self):
        self.f.p4()
        result = self.f.bind()
        self.assertEqual(result["status"], "NATIVE_SCHEDULE_BOUND", result)
        return result

    def reserve(self):
        result = utility.native_schedule_reserve(self.state)
        self.assertEqual(result["status"], "NATIVE_SCHEDULE_RECORDED", result)
        return result["native_schedule"]

    def test_requires_prior_phases_and_passing_prerequisites(self):
        self.f.validator.start()
        prior = self.f.head()
        self.assertEqual(self.f.bind()["status"], "FAIL")
        self.assertEqual(self.f.head(), prior)
        self.assertTrue(self.f.native.workspace.is_dir())

    def test_binding_is_once_and_read_only_context_does_not_reset_clock(self):
        result = self.bound()
        origin = result["native_schedule"]["origin_utc"]
        before = self.f.head()
        self.now.return_value = NOW + timedelta(seconds=30)
        current = utility.context(self.state)
        self.assertEqual(current["native_schedule"]["origin_utc"], origin)
        self.assertEqual(current["native_schedule"]["reservations_consumed"], 0)
        self.assertEqual(self.f.head(), before)
        self.assertEqual(self.f.bind()["status"], "FAIL")
        self.assertEqual(self.f.head(), before)

    def test_reservation_reload_repeated_call_and_deadline_keep_one_inflight(self):
        self.bound()
        self.now.return_value = NOW + timedelta(seconds=5)
        first = self.reserve()
        self.assertEqual(first["decision"], "RESERVED")
        self.assertEqual(first["inflight_attempt"], "C-candidate")
        self.assertFalse(first["native_launch_admitted"])
        self.assertEqual(first["execution"], "NOT_RUN")
        self.now.return_value = NOW + timedelta(seconds=10)
        second = self.reserve()
        self.assertEqual(second["decision"], "WAITING")
        self.assertEqual(second["reservations_consumed"], 1)
        self.assertEqual(second["inflight_deadline_utc"], first["inflight_deadline_utc"])
        self.now.return_value = NOW + timedelta(seconds=25)
        third = self.reserve()
        self.assertTrue(third["stop_required"])
        self.assertEqual(third["inflight_attempt"], "C-candidate")
        self.assertEqual(third["reservations_consumed"], 1)

    def test_unlaunched_cancellation_consumes_allocation_and_preserves_independent_arm(self):
        self.bound()
        self.reserve()
        result = utility.native_schedule_cancel(self.state, "C-candidate", "Cannot establish native launch")
        self.assertEqual(result["status"], "NATIVE_UNLAUNCHED_CANCELLED", result)
        self.assertEqual(result["native_schedule"]["reservations_consumed"], 1)
        next_attempt = self.reserve()
        self.assertEqual(next_attempt["inflight_attempt"], "C-baseline")
        self.assertEqual(next_attempt["reservations_consumed"], 2)
        utility.native_schedule_cancel(self.state, "C-baseline", "No native launcher")
        final = self.reserve()
        self.assertEqual(final["decision"], "BLOCKED")
        self.assertEqual({r["attempt_id"] for r in final["blocked"]},
                         {"B-candidate", "B-baseline", "A-candidate", "A-baseline"})
        self.assertEqual(final["reservations_consumed"], 2)

    def test_cancel_wrong_or_replayed_attempt_does_not_publish(self):
        self.bound()
        self.reserve()
        before = self.f.head()
        self.assertEqual(utility.native_schedule_cancel(self.state, "C-baseline", "Wrong attempt")["status"], "FAIL")
        self.assertEqual(before, self.f.head())
        utility.native_schedule_cancel(self.state, "C-candidate", "Cancelled before launch")
        before = self.f.head()
        self.assertEqual(utility.native_schedule_cancel(self.state, "C-candidate", "Replay")["status"], "FAIL")
        self.assertEqual(before, self.f.head())

    def test_missing_required_dependencies_cannot_be_bound(self):
        self.f.p4()
        for identity in ("B-candidate", "A-baseline"):
            with self.subTest(identity=identity):
                value = copy.deepcopy(self.f.binding)
                value["required_predecessors"][identity] = []
                self.f.binding_path.write_bytes(encoded(value))
                before = self.f.head()
                self.assertEqual(self.f.bind()["status"], "FAIL")
                self.assertEqual(self.f.head(), before)

    def test_schedule_cannot_substitute_another_plan_pin(self):
        self.f.p4()
        self.f.binding["plan"] = Fixture.pin(self.f.validator.owner / "assignment.md")
        self.f.binding_path.write_bytes(encoded(self.f.binding))
        before = self.f.head()
        self.assertEqual(self.f.bind()["status"], "FAIL")
        self.assertEqual(before, self.f.head())

    def test_binding_in_worker_writable_scope_is_refused(self):
        self.f.p4()
        for root in (self.f.validator.project, Path(self.f.native.plan["attempts"][0]["workspace"])):
            with self.subTest(root=root):
                path = root / "schedule.json"
                path.write_bytes(self.f.binding_path.read_bytes())
                before = self.f.head()
                self.assertEqual(utility.native_schedule_bind(self.state, path)["status"], "FAIL")
                self.assertEqual(self.f.head(), before)

    def test_changed_bound_schedule_invalidates_current_context(self):
        self.bound()
        self.f.binding_path.write_bytes(self.f.binding_path.read_bytes() + b"\n")
        self.assertEqual(utility.context(self.state)["status"], "FAIL")
        self.assertEqual(utility.native_schedule_reserve(self.state)["status"], "FAIL")

    def test_legacy_and_scheduled_reservations_cannot_mix(self):
        self.bound()
        before = self.f.head()
        self.assertEqual(utility.native_admission(self.state, "C-candidate")["status"], "FAIL")
        self.assertEqual(self.f.head(), before)
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ScheduleFixture(Path(tmp))
            fixture.p4()
            self.assertEqual(utility.native_admission(fixture.validator.state, "C-candidate")["status"],
                             "NATIVE_ADMISSION_RECORDED")
            self.assertEqual(fixture.bind()["status"], "FAIL")

    def test_elapsed_campaign_budget_is_not_reset_on_reservation(self):
        self.bound()
        self.now.return_value = NOW + timedelta(seconds=120)
        result = self.reserve()
        self.assertEqual(result["decision"], "EXHAUSTED")
        self.assertEqual(result["reservations_consumed"], 0)
        self.assertTrue(result["campaign_expired"])

    def test_unlaunched_cancellation_after_session_expiry_cannot_resume_work(self):
        self.bound()
        self.reserve()
        self.now.return_value = NOW + timedelta(hours=1)
        self.assertEqual(utility.context(self.state)["status"], "COULD_NOT_RUN")
        result = utility.native_schedule_cancel(self.state, "C-candidate", "Original session deadline expired")
        self.assertEqual(result["status"], "NATIVE_UNLAUNCHED_CANCELLED", result)
        self.assertIsNone(result["native_schedule"]["inflight_attempt"])
        self.assertEqual(utility.context(self.state)["status"], "COULD_NOT_RUN")
        self.assertEqual(utility.native_schedule_reserve(self.state)["status"], "COULD_NOT_RUN")
        self.assertFalse(self.f.validator.receipt.exists())

    def test_clock_rollback_does_not_commit_a_bad_journal_record(self):
        self.bound()
        self.now.return_value = NOW + timedelta(seconds=10)
        self.reserve()
        before = self.f.head()
        self.now.return_value = NOW + timedelta(seconds=9)
        self.assertEqual(utility.native_schedule_reserve(self.state)["status"], "FAIL")
        self.assertEqual(before, self.f.head())
        self.now.return_value = NOW + timedelta(seconds=10)
        self.assertEqual(utility.context(self.state)["status"], "ACTIVE")

    def test_campaign_budget_must_fit_remaining_session_time(self):
        self.f.p4()
        self.now.return_value = NOW + timedelta(seconds=3500)
        before = self.f.head()
        self.assertEqual(self.f.bind()["status"], "FAIL")
        self.assertEqual(before, self.f.head())

    def test_phase_completion_waits_for_cancellation_and_reports_unavailable_native(self):
        self.bound()
        self.reserve()
        self.f.validator.checkpoint()
        self.assertEqual(utility.advance(self.state)["status"], "FAIL")
        self.assertEqual(utility.context(self.state)["phase"], "P4")
        utility.native_schedule_cancel(self.state, "C-candidate", "Unlaunched, no native runtime")
        for phase in ("P4", "P5", "P6"):
            self.f.validator.checkpoint()
            self.assertIn(utility.advance(self.state)["status"], {"PROGRESS", "READY"}, phase)
        completed = utility.complete(self.state)
        self.assertEqual(completed["status"], "COMPLETED", completed)
        self.assertEqual(completed["gate_outcomes"]["native-C"], "COULD_NOT_RUN")
        self.assertEqual(completed["native_schedule"]["execution"], "NOT_RUN")

    def test_controller_routes_binding_and_nonlaunching_reservation(self):
        self.f.p4()
        result = controller.operate("native-schedule-bind", self.state, schedule=self.f.binding_path)
        self.assertEqual(result["status"], "NATIVE_SCHEDULE_BOUND", result)
        result = controller.operate("native-schedule-reserve", self.state)
        self.assertEqual(result["native_schedule"]["inflight_attempt"], "C-candidate")
        result = controller.operate("native-schedule-cancel", self.state, attempt="C-candidate", reason="No launch")
        self.assertEqual(result["status"], "NATIVE_UNLAUNCHED_CANCELLED")

    def test_compiled_delivery_embeds_and_routes_schedule_modules(self):
        binary = Path(__file__).resolve().parents[1] / "target/debug/devforge"
        self.assertTrue(binary.is_file(), "Repository checks require cargo build --locked before this suite")
        self.now.return_value = datetime.now(timezone.utc)
        self.f.validator.session["deadline_utc"] = (self.now.return_value + timedelta(hours=1)).isoformat()
        self.f.validator.write_contracts()
        self.f.p4()

        def call(*args):
            result = subprocess.run([str(binary), "delivery", "--state", str(self.state), *args],
                                    text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)

        bound = call("native-schedule-bind", "--schedule", str(self.f.binding_path))
        self.assertEqual(bound["status"], "NATIVE_SCHEDULE_BOUND")
        reserved = call("native-schedule-reserve")
        self.assertEqual(reserved["native_schedule"]["inflight_attempt"], "C-candidate")
        self.assertFalse(reserved["native_schedule"]["native_launch_admitted"])
        cancelled = call("native-schedule-cancel", "--attempt", "C-candidate", "--reason", "Synthetic cancellation")
        self.assertEqual(cancelled["status"], "NATIVE_UNLAUNCHED_CANCELLED")


if __name__ == "__main__":
    unittest.main()
