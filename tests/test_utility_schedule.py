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
        # Campaign admission requires a complete independent call oracle even
        # though this fixture never launches the inert selected client.
        owner = self.native.owner
        calls = [{**{k: a[k] for k in ("attempt_id", "case_id", "tier", "arm", "repetition")},
                  "purpose": "case", "review_path": str(owner / (a["attempt_id"] + "-review.json")),
                  "reviewer": "Independent fixture reviewer", "interaction": "single-turn",
                  "managed_worker_required": False} for a in attempts]
        self.cases_path = owner / "cases.json"
        self.cases_path.write_bytes(encoded({"schema_version": "devforge.utility-native-cases/v1",
                                            "task_id": plan["task_id"], "required_calls": calls}))
        plan["cases"] = Fixture.pin(self.cases_path)
        self.allocation_path = owner / "allocation.json"
        self.allocation = {"schema_version": "devforge.utility-native-allocation/v1", "task_id": plan["task_id"],
                           "cases_sha256": plan["cases"]["sha256"], "max_total_attempts": 9,
                           "preparation_attempts": 3, "max_seconds": 120, "per_attempt_max_seconds": 20,
                           "required_calls": calls}
        self.allocation_path.write_bytes(encoded(self.allocation))
        self.runtime_path = owner / "runtime_configuration.json"
        self.runtime = {"schema_version": "devforge.native-runtime-configuration/v1",
                        "allocation": Fixture.pin(self.allocation_path),
                        "attempts": [{"attempt_id": a["attempt_id"], "interaction": "single-turn", "managed_worker": None}
                                     for a in attempts]}
        self.runtime_path.write_bytes(encoded(self.runtime))
        plan["runtime_configuration"] = Fixture.pin(self.runtime_path)
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


class V2ScheduleFixture:
    """Frozen G1 selection and original catalog; mechanical fixtures only."""
    def __init__(self, root):
        from test_validation_policy import PolicyFixture
        self.f = PolicyFixture(root)
        f = self.f
        f.plan['runtime'] = {'selection': 'synthetic only'}
        f.plan['budget'] = {'max_attempts': 2, 'max_seconds': 18000, 'repeats_per_case': 1}
        for i in (5, 6, 8):
            f.vp['assertions'][i-1]['selection'] = 'REQUIRED'
            f.vp['task_selection'][i-1]['selection'] = 'REQUIRED'
        f.vp['assertions'][7]['dependency_ids'] = ['A06']
        proof = f.put('conditions.txt', 'Synthetic frozen visibility/freshness/prompt')
        calls = f.vp['call_graph']
        attempts = []
        for tier, aid, task in [('C', 'A06', 'T06'), ('A', 'A08', 'T08')]:
            oid, cid = 'obs-' + tier, 'call-' + tier
            f.vp['assertions'][int(aid[1:])-1]['observation_ids'] = [oid]
            f.vp['observations'].append({'observation_id': oid, 'evidence_kind': 'N', 'assertion_ids': [aid],
                'conditions': {'identity': f.identity, 'input_refs': [f.catalog_ref], 'prompt_ref': proof,
                'arm': 'candidate', 'variant': 'normal', 'repetition': 1, 'invocation': 'explicit',
                'visibility_ref': proof, 'freshness_ref': proof, 'before_task': task},
                'prerequisite_observation_ids': ['obs-C'] if tier == 'A' else [], 'reuse_ref': None})
            calls.append({'call_id': cid, 'kind': 'native_worker', 'parent_call_id': None,
                'attempt_id': tier + '-candidate', 'assertion_ids': [aid], 'observation_ids': [oid],
                'depends_on': ['T04-review', 'call-C'] if tier == 'A' else ['T04-review'],
                'producer': 'worker-' + tier, 'reviewer': 'grader-' + tier,
                'review_path': str(f.owner / ('grade-' + tier + '.json')), 'interaction': 'single-turn',
                'managed_worker_required': False, 'max_seconds': 900})
            attempts.append({'attempt_id': tier + '-candidate', 'case_id': 'CASE-' + str(int(aid[1:])),
                'tier': tier, 'arm': 'candidate', 'repetition': 1, 'workspace': str(root / ('workspace-' + tier)),
                'client_state': str(root / ('client-' + tier)), 'max_seconds': 900})
        f.freeze()
        self.plan = {'schema_version': 'devforge.utility-native-plan/v2', 'task_id': 'UTILITY-001',
            'validation_plan': f.pin(f.plan_path), 'attempts': attempts, 'max_attempts': 2,
            'max_seconds': 18000, 'repetitions': 1}
        self.allocation_ref = f.put('allocation.json', {'synthetic': 'Never native funding'})
        runtime = f.put('native-runtime.json', {'schema_version': 'devforge.native-runtime-configuration/v2',
            'allocation': self.allocation_ref, 'attempts': []})
        self.plan['runtime_configuration'] = runtime
        self.plan_ref = f.put('native-plan.json', self.plan)
        self.binding = {'schema_version': 'devforge.utility-native-schedule/v2', 'task_id': 'UTILITY-001',
            'plan': self.plan_ref, 'validation_plan': f.pin(f.plan_path), 'review': f.pin(f.review_path),
            'allocation': self.allocation_ref, 'required_predecessors': {'C-candidate': [], 'A-candidate': ['C-candidate']}}

    def policy(self):
        import validation_policy
        return validation_policy.load(self.f.delivery['validation_policy'],
            (self.f.owner / 'assignment.md').read_bytes(), self.f.project)

    def kernel(self):
        # Exercise the real schedule consumer; runtime authority is independently supplied.
        import delivery_core
        try:
            return utility_schedule.validate(encoded(self.binding), self.plan, self.plan_ref, 'UTILITY-001',
                policy=self.policy(), review_ref=self.f.pin(self.f.review_path),
                delivery_ref=self.f.pin(self.f.delivery_path))
        except TypeError as error:
            # A missing keyword is not our RED discriminator. Use the existing consumer.
            if 'unexpected keyword argument' not in str(error):
                raise
            return utility_schedule.validate(encoded(self.binding), self.plan, self.plan_ref, 'UTILITY-001')


class V2ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = V2ScheduleFixture(Path(self.tmp.name))

    def accepted(self):
        import delivery_core
        try:
            result = self.f.kernel()
        except delivery_core._Problem as error:
            self.fail('Reviewed v2 C/A schedule must be supported by actual consumer: ' + str(error))
        return result

    def test_reviewed_C_pass_unselected_B_permits_A(self):
        import native_schedule
        kernel = self.accepted()
        first = kernel.reserve(kernel.initial_state(), 10)
        self.assertEqual(first.attempt.attempt_id, 'C-candidate')
        settled = kernel.record(first.state, 'C-candidate', native_schedule.Observation('PASS', 'INTACT'), 20)
        self.assertEqual(kernel.reserve(settled, 21).attempt.attempt_id, 'A-candidate')

    def test_selected_C_fail_missing_and_stale_block_A(self):
        import native_schedule
        kernel = self.accepted()
        first = kernel.reserve(kernel.initial_state(), 10)
        self.assertEqual(kernel.reserve(first.state, 11).status, 'WAITING')
        settled = kernel.record(first.state, 'C-candidate', native_schedule.Observation('FAIL', 'INTACT'), 20)
        self.assertEqual(kernel.reserve(settled, 21).status, 'BLOCKED')
        with self.assertRaises(native_schedule.ScheduleError):
            kernel.record(first.state, 'C-candidate', native_schedule.Observation('PASS', 'INTACT'), first.state.reservations[0].deadline)

    def test_failed_launch_stays_charged_and_replay_does_not_reset(self):
        import native_schedule
        kernel = self.accepted()
        first = kernel.reserve(kernel.initial_state(), 10)
        closed = kernel.record(first.state, 'C-candidate', native_schedule.Observation('LAUNCH_FAILED', 'UNOBTAINABLE'), 20)
        self.assertEqual(len(closed.reservations), 1)
        self.assertEqual(kernel.reserve(closed, 21).status, 'BLOCKED')
        with self.assertRaises(native_schedule.ScheduleError):
            kernel.reserve(closed, 0)
        with self.assertRaises(native_schedule.ScheduleError):
            kernel.record(closed, 'C-candidate', native_schedule.Observation('PASS', 'INTACT'), 21)

    def test_posthoc_exclusion_missing_C_and_wrong_projection_rejected(self):
        import delivery_core
        for mutation in ('exclude', 'missing', 'projection', 'review'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                f = V2ScheduleFixture(Path(tmp))
                if mutation == 'exclude':
                    f.f.vp['task_selection'][6]['selection'] = 'REQUIRED'
                    f.f.freeze()  # old reviewed schedule is deliberately stale
                elif mutation == 'missing':
                    f.binding['required_predecessors']['A-candidate'] = []
                elif mutation == 'projection':
                    f.plan['attempts'].pop(0)
                else:
                    f.f.review_path.write_text('{}')
                with self.assertRaises(delivery_core._Problem):
                    f.kernel()

    def test_router_selects_v2_and_protects_policy_inputs(self):
        import workflow_runtime
        try:
            contract, _, _ = workflow_runtime.load_delivery(self.f.f.delivery_path)
        except Exception as error:
            self.fail('Actual router must select valid v2 utility delivery: ' + str(error))
        protected = workflow_runtime.protected_paths(contract)
        for key in ('policy_ref', 'acceptance_ref', 'plan'):
            self.assertIn(Path(contract['validation_policy'][key]['path']), protected)

    def test_router_protects_nested_policy_pins_and_review_destinations(self):
        import workflow_runtime
        protected = workflow_runtime.protected_paths(self.f.f.delivery)
        for path in self.f.policy().sources:
            self.assertIn(Path(path), protected, 'Managed profile/result must not overlap nested policy inputs')
        for call in self.f.f.vp['call_graph']:
            if call['review_path'] is not None:
                self.assertIn(Path(call['review_path']), protected)

    def test_cli_v2_capability_is_explicit(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([str(root / 'target/debug/devforge'), 'delivery', 'capabilities'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['schema_version'], 'devforge.delivery-capabilities/v2')
        self.assertIn('devforge.utility-native-schedule/v2', value.get('supported_utility_native_schedule_schemas', []))
        self.assertFalse(value['native_execution_enabled'])


class V2PrerequisiteTests(unittest.TestCase):
    def test_actual_selected_native_prerequisite_consumes_one_plan_and_retains_review_pins(self):
        import delivery_core
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frozen = V2ScheduleFixture(root)
            f = frozen.f
            native_root = root / 'prepared-native'
            native_root.mkdir()
            n = admitted.NativePlanFixture(native_root)
            for a in frozen.plan['attempts']:
                Path(a['workspace']).mkdir()
                Path(a['client_state']).mkdir()
            n.plan.update(frozen.plan)
            boundary = json.loads(n.boundary_path.read_bytes())
            raw = boundary['attempts'][0]['observations']
            boundary['attempts'] = [{'attempt_id': a['attempt_id'], 'workspace': a['workspace'],
                'client_state': a['client_state'], 'observations': raw} for a in n.plan['attempts']]
            n.boundary_path.write_bytes(encoded(boundary))
            n.plan['boundary_evidence'] = f.pin(n.boundary_path)
            n.save()
            spec = next(g for g in f.delivery['gate_inputs'] if g['id'] == 'native-prerequisites')
            value = json.loads(Path(spec['path']).read_bytes())
            value.update(outcome='PASS', selection='REQUIRED', disposition='SATISFIED', evidence=[f.pin(n.plan_path)])
            Path(spec['path']).write_bytes(encoded(value))
            with mock.patch.object(store, 'utc_now', return_value=NOW):
                self.assertEqual(f.start()['status'], 'ACTIVE')
                for phase in ('P1', 'P2', 'P3'):
                    f.checkpoint()
                    self.assertEqual(utility.advance(f.state)['status'], 'PROGRESS', phase)
                with store._lock(f.state) as fd:
                    state = utility.State(f.state, fd)
                    try:
                        _, _, sources, ref, plan = utility._native_prerequisites(state)
                    except delivery_core._Problem as error:
                        self.fail('Selected v2 readiness must identify its one native plan: ' + str(error))
                    self.assertEqual(ref, f.pin(n.plan_path))
                    self.assertEqual(plan, n.plan)
                    self.assertTrue(any(path == str(f.review_path) for _, path, _ in sources))

                    frozen.binding['plan'] = f.pin(n.plan_path)
                    schedule_path = f.owner / 'selected-schedule.json'
                    schedule_path.write_bytes(encoded(frozen.binding))
                before = (f.state / 'HEAD.json').read_bytes()
                result = utility.native_schedule_bind(f.state, schedule_path)
                self.assertEqual(result['status'], 'FAIL', result)
                self.assertIn('funding authority', str(result))
                self.assertEqual((f.state / 'HEAD.json').read_bytes(), before)


class V2FundingFixture(V2ScheduleFixture):
    """Synthetic owner documents: never a live grant or qualification claim."""
    def __init__(self, root):
        import time
        super().__init__(root)
        f = self.f
        self.now = datetime.now(timezone.utc)
        self.mono = time.monotonic_ns()
        prepared = root / 'prepared'; prepared.mkdir()
        self.native = admitted.NativePlanFixture(prepared)
        n = self.native
        for a in self.plan['attempts']:
            Path(a['workspace']).mkdir(); Path(a['client_state']).mkdir()
        n.plan.update(self.plan)
        self.plan = n.plan
        self.plan['max_seconds'] = 3600
        self.claim_root = f.owner / 'shared-grant-custody'; self.claim_root.mkdir(mode=0o700)
        proof = f.put('synthetic-dispatch-output.txt', 'Synthetic actual-output fixture bytes; not a model observation')
        self.prior = {'schema_version': 'devforge.funding-terminal/v1', 'grant_id': 'old-exhausted',
            'producer': 'external-owner', 'approved': 24, 'charged': 24, 'remaining': 0,
            'status': 'EXHAUSTED', 'evidence': [proof]}
        prior_ref = f.put('prior-ledger.json', self.prior)
        self.funding = {'grant_id': 'new-synthetic-grant', 'authority_ref': None, 'owner': 'external-owner',
            'purpose': 'native campaign UTILITY-001', 'origin_utc': (self.now-timedelta(seconds=30)).isoformat(),
            'deadline_utc': (self.now+timedelta(seconds=3570)).isoformat(),
            'clock_id': 'linux-boot:' + Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'origin_monotonic_ns': self.mono-30_000_000_000, 'deadline_monotonic_ns': self.mono+3570_000_000_000,
            'prior_ledgers': [prior_ref]}
        def completion(cid, producer, evidence):
            return {'schema_version': 'devforge.funding-call/v1', 'grant_id': self.funding['grant_id'],
                'call_id': cid, 'producer': producer, 'charged': True, 'status': 'COMPLETED',
                'started_utc': (self.now-timedelta(seconds=20)).isoformat(),
                'completed_utc': (self.now-timedelta(seconds=10)).isoformat(),
                'started_monotonic_ns': self.mono-20_000_000_000,
                'completed_monotonic_ns': self.mono-10_000_000_000, 'evidence': evidence}
        setup = f.put('setup-completion.json', completion('setup-1', 'external-owner', [proof]))
        review = f.put('review-completion.json', completion('T04-review', 'independent-reviewer', [f.pin(f.review_path)]))
        self.authority = {'schema_version': 'devforge.funding-authority/v1', 'producer': 'external-owner',
            'task_id': 'UTILITY-001', 'validation_plan': f.pin(f.plan_path),
            **{k:v for k,v in self.funding.items() if k not in ('authority_ref', 'owner')},
            'max_total_attempts': 4, 'max_seconds': 3600, 'per_attempt_max_seconds': 900,
            'claim_root': str(self.claim_root), 'preparation': [setup], 'completed_calls': [review],
            'call_records': {c['call_id']: str(self.claim_root / (c['call_id'] + '.completion.json')) for c in f.vp['call_graph']}}
        self.allocation = {'schema_version': 'devforge.utility-native-allocation/v2', 'task_id': 'UTILITY-001',
            'cases_sha256': self.plan['cases']['sha256'], 'validation_plan': f.pin(f.plan_path),
            'funding': self.funding, 'max_total_attempts': 4, 'preparation_attempts': 1,
            'max_seconds': 3600, 'per_attempt_max_seconds': 900, 'required_calls': copy.deepcopy(f.vp['call_graph'])}
        boundary = json.loads(n.boundary_path.read_bytes()); obs = boundary['attempts'][0]['observations']
        boundary['attempts'] = [{'attempt_id': a['attempt_id'], 'workspace': a['workspace'],
            'client_state': a['client_state'], 'observations': obs} for a in self.plan['attempts']]
        n.boundary_path.write_bytes(encoded(boundary)); self.plan['boundary_evidence'] = f.pin(n.boundary_path)
        self.save_funding()

    def save_funding(self):
        f = self.f
        self.funding['authority_ref'] = f.put('funding-authority.json', self.authority)
        assignment_path = f.owner / 'assignment.md'; assignment = json.loads(assignment_path.read_bytes())
        assignment['authorization']['funding'] = {'authority_ref': self.funding['authority_ref'],
            'owner': self.funding['owner'], 'claim_root': str(self.claim_root)}
        assignment_path.write_bytes(encoded(assignment)); f.session['assignment'] = f.pin(assignment_path); f.write_contracts()
        self.allocation_ref = f.put('allocation.json', self.allocation)
        self.plan['runtime_configuration'] = f.put('native-runtime.json', {
            'schema_version': 'devforge.native-runtime-configuration/v2', 'allocation': self.allocation_ref,
            'attempts': [{'attempt_id': a['attempt_id'], 'interaction': 'single-turn', 'managed_worker': None} for a in self.plan['attempts']]})
        self.native.save(); self.plan_ref = f.pin(self.native.plan_path)
        self.binding.update(plan=self.plan_ref, allocation=self.allocation_ref)
        self.schedule_path = f.owner / 'funded-schedule.json'; self.schedule_path.write_bytes(encoded(self.binding))
        spec = next(g for g in f.delivery['gate_inputs'] if g['id'] == 'native-prerequisites')
        value = json.loads(Path(spec['path']).read_bytes())
        value.update(outcome='PASS', selection='REQUIRED', disposition='SATISFIED', evidence=[self.plan_ref])
        Path(spec['path']).write_bytes(encoded(value))

    def p4(self):
        result = self.f.start()
        if result['status'] != 'ACTIVE': raise AssertionError(result)
        for phase in ('P1', 'P2', 'P3'):
            self.f.checkpoint(); result = utility.advance(self.f.state)
            if result['status'] != 'PROGRESS': raise AssertionError((phase,result))


class V2FundingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.fixture = V2FundingFixture(Path(self.tmp.name)); self.f = self.fixture.f
        clock = mock.patch.object(store, 'utc_now', return_value=self.fixture.now)
        self.now = clock.start(); self.addCleanup(clock.stop)

    def bind(self):
        return utility.native_schedule_bind(self.f.state, self.fixture.schedule_path)

    def test_distinct_external_grant_admits_real_cli_binding_and_failed_reservation_stays_charged(self):
        self.fixture.p4()
        binary = Path(__file__).resolve().parents[1] / 'target/debug/devforge'
        run = subprocess.run([str(binary), 'delivery', '--state', str(self.f.state), 'native-schedule-bind',
            '--schedule', str(self.fixture.schedule_path)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        value = json.loads(run.stdout); self.assertEqual(value['status'], 'NATIVE_SCHEDULE_BOUND', value)
        head = json.loads((self.f.state / 'HEAD.json').read_bytes())
        bound_record = json.loads((self.f.state / 'records' / (head['records'][-1] + '.json')).read_bytes())
        self.now.return_value = datetime.fromisoformat(bound_record['at_utc']) + timedelta(seconds=1)
        self.assertEqual(value['native_schedule']['origin_utc'], self.fixture.funding['origin_utc'])
        first = utility.native_schedule_reserve(self.f.state)
        self.assertEqual(first['native_schedule']['inflight_attempt'], 'C-candidate', first)
        closed = utility.native_schedule_cancel(self.f.state, 'C-candidate', 'Synthetic failed allocated launch')
        self.assertEqual(closed['native_schedule']['reservations_consumed'], 1, closed)
        before = (self.f.state/'HEAD.json').read_bytes()
        self.assertEqual(self.bind()['status'], 'FAIL')
        self.assertEqual(before, (self.f.state/'HEAD.json').read_bytes())
        self.assertEqual(utility.native_schedule_reserve(self.f.state)['native_schedule']['decision'], 'BLOCKED')
        self.assertTrue(list(self.fixture.claim_root.glob('grant-*.json')))

    def test_grant_claim_cannot_be_reused_in_another_session(self):
        self.fixture.p4(); self.assertEqual(self.bind()['status'], 'NATIVE_SCHEDULE_BOUND')
        other_state = self.f.state.parent / 'another-session'
        original = self.f.state; self.f.state = other_state
        # Fresh fixture outputs must match the declared absent preimages.
        for output in self.f.session['output_baselines']:
            if output['sha256'] is None:
                (self.f.project / output['path']).unlink(missing_ok=True)
        self.fixture.p4()
        before = (other_state/'HEAD.json').read_bytes()
        result = utility.native_schedule_bind(other_state, self.fixture.schedule_path)
        self.assertEqual(result['status'], 'FAIL', result)
        self.assertIn('grant', str(result)); self.assertEqual(before, (other_state/'HEAD.json').read_bytes())
        self.f.state = original

    def test_incomplete_graph_setup_completion_and_exhausted_grant_refuse_before_head_or_claim(self):
        for mutation in ('graph', 'setup', 'completion', 'owner', 'exhausted', 'cap', 'reset-clock'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                x = V2FundingFixture(Path(tmp))
                if mutation == 'graph': x.allocation['required_calls'].pop(0)
                elif mutation == 'setup': x.authority['preparation'] = []
                elif mutation == 'completion': x.authority['completed_calls'] = []
                elif mutation == 'owner': x.authority['producer'] = 'candidate-author'
                elif mutation == 'exhausted': x.funding['grant_id'] = x.authority['grant_id'] = 'old-exhausted'
                elif mutation == 'cap': x.allocation['max_total_attempts'] = 3
                else: x.funding['origin_monotonic_ns'] += 60_000_000_000
                x.save_funding(); x.p4(); before = (x.f.state/'HEAD.json').read_bytes()
                result = utility.native_schedule_bind(x.f.state, x.schedule_path)
                self.assertEqual(result['status'], 'FAIL', result)
                self.assertEqual(before, (x.f.state/'HEAD.json').read_bytes())
                self.assertEqual(list(x.claim_root.glob('grant-*.json')), [])

    def test_original_monotonic_deadline_refuses_without_a_new_charge(self):
        self.fixture.p4(); self.assertEqual(self.bind()['status'], 'NATIVE_SCHEDULE_BOUND')
        before = (self.f.state/'HEAD.json').read_bytes()
        with mock.patch('time.monotonic_ns', return_value=self.fixture.funding['deadline_monotonic_ns']):
            result = utility.native_schedule_reserve(self.f.state)
        self.assertIn(result['status'], ('FAIL','COULD_NOT_RUN'), result)
        self.assertEqual(before, (self.f.state/'HEAD.json').read_bytes())
