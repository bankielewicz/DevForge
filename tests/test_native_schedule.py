"""Synthetic policy tests only: no admission, client, model, authentication or launch."""
import copy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime" / "delivery"))
from native_schedule import Observation, Reservation, Schedule, ScheduleError


PASS = Observation("PASS", "INTACT")
FAIL = Observation("FAIL", "INTACT")
TIMEOUT = Observation("TIMED_OUT", "UNOBTAINABLE")


def attempt(identity, tier, *, arm="candidate", repetition=1, case=None, seconds=20):
    return {"attempt_id": identity, "case_id": case or identity, "tier": tier,
            "arm": arm, "repetition": repetition, "workspace": "/allocated/" + identity,
            "client_state": "/client-state/" + identity, "max_seconds": seconds}


def plan(rows, *, seconds=100, repetitions=1, budget=None):
    # Only scheduler projection is exercised. Production callers must validate
    # the full file-backed plan through utility_evidence.native_plan first.
    return {"schema_version": "devforge.utility-native-plan/v1", "task_id": "SYNTHETIC",
            "model": "explicit-synthetic-model", "attempts": rows,
            "max_attempts": len(rows) if budget is None else budget,
            "max_seconds": seconds, "repetitions": repetitions}


def chain():
    return Schedule(plan([attempt("resource", "C"), attempt("behavior", "B"),
                          attempt("activation", "A")]),
                    {"resource": [], "behavior": ["resource"],
                     "activation": ["resource", "behavior"]})


class NativeScheduleTests(unittest.TestCase):
    def settle(self, schedule, state, expected, result, start, end):
        selected = schedule.reserve(state, start)
        self.assertEqual(selected.status, "RESERVED")
        self.assertEqual(selected.attempt.attempt_id, expected)
        return schedule.record(selected.state, expected, result, end)

    def test_declared_order_all_required_c_then_b_then_a(self):
        schedule = Schedule(plan([attempt("C-z", "C"), attempt("C-a", "C"),
                                  attempt("B-z", "B"), attempt("B-a", "B"),
                                  attempt("A", "A")]),
                            {"C-z": [], "C-a": [], "B-z": ["C-z", "C-a"],
                             "B-a": ["C-z", "C-a"], "A": ["C-z", "C-a", "B-z", "B-a"]})
        state = schedule.initial_state()
        for index, identity in enumerate(("C-z", "C-a", "B-z", "B-a", "A")):
            state = self.settle(schedule, state, identity, PASS, index * 2, index * 2 + 1)
        decision = schedule.reserve(state, 10)
        self.assertEqual((decision.status, decision.reason), ("DONE", "ALLOCATION_COMPLETED"))
        self.assertIsNone(decision.attempt)
        self.assertFalse(decision.stop_required)

    def test_missing_c_result_waits_and_failed_c_blocks_dependents(self):
        schedule = chain()
        reserved = schedule.reserve(schedule.initial_state(), 0)
        waiting = schedule.reserve(reserved.state, 1)
        self.assertEqual(waiting.status, "WAITING")
        self.assertEqual(len(waiting.state.reservations), 1)
        state = schedule.record(waiting.state, "resource", FAIL, 2)
        decision = schedule.reserve(state, 3)
        self.assertEqual(decision.status, "BLOCKED")
        self.assertEqual([(b.attempt_id, b.predecessor_id, b.reason) for b in decision.blocked],
                         [("behavior", "resource", "C_NOT_PASS"),
                          ("activation", "resource", "C_NOT_PASS")])
        self.assertEqual(len(decision.state.reservations), 1)

    def test_every_declared_c_is_required_even_when_another_c_passed(self):
        schedule = Schedule(plan([attempt("C1", "C"), attempt("C2", "C"),
                                  attempt("B", "B"), attempt("A", "A")]),
                            {"C1": [], "C2": [], "B": ["C1", "C2"], "A": ["B"]})
        state = self.settle(schedule, schedule.initial_state(), "C1", PASS, 0, 1)
        state = self.settle(schedule, state, "C2", FAIL, 2, 3)
        decision = schedule.reserve(state, 4)
        self.assertEqual(decision.status, "BLOCKED")
        self.assertEqual([(b.attempt_id, b.reason) for b in decision.blocked],
                         [("B", "C_NOT_PASS"), ("A", "PREDECESSOR_BLOCKED")])

    def test_intact_b_quality_fail_permits_a_without_claiming_evaluation_pass(self):
        schedule = chain()
        state = self.settle(schedule, schedule.initial_state(), "resource", PASS, 0, 1)
        state = self.settle(schedule, state, "behavior", FAIL, 2, 3)
        state = self.settle(schedule, state, "activation", FAIL, 4, 5)
        decision = schedule.reserve(state, 6)
        self.assertEqual(decision.status, "DONE")
        self.assertEqual([r.observation.outcome for r in state.reservations], ["PASS", "FAIL", "FAIL"])

    def test_unobtainable_or_contaminated_b_blocks_a(self):
        results = [Observation("COULD_NOT_RUN", "UNOBTAINABLE"),
                   Observation("LAUNCH_FAILED", "UNOBTAINABLE"), TIMEOUT,
                   Observation("CANCELLED", "UNOBTAINABLE"),
                   Observation("FAIL", "CONTAMINATED"), Observation("PASS", "CONTAMINATED")]
        for result in results:
            with self.subTest(result=result):
                schedule = chain()
                state = self.settle(schedule, schedule.initial_state(), "resource", PASS, 0, 1)
                state = self.settle(schedule, state, "behavior", result, 2, 3)
                decision = schedule.reserve(state, 4)
                self.assertEqual(decision.status, "BLOCKED")
                self.assertEqual(decision.blocked[0].attempt_id, "activation")
                self.assertEqual(len(state.reservations), 2)

    def test_unobtainable_or_contaminated_c_blocks_even_a_passing_grade(self):
        for result in (Observation("PASS", "CONTAMINATED"),
                       Observation("LAUNCH_FAILED", "UNOBTAINABLE"), TIMEOUT):
            with self.subTest(result=result):
                schedule = chain()
                state = self.settle(schedule, schedule.initial_state(), "resource", result, 0, 1)
                decision = schedule.reserve(state, 2)
                self.assertEqual(decision.status, "BLOCKED")
                self.assertEqual({b.attempt_id for b in decision.blocked}, {"behavior", "activation"})

    def test_baseline_without_c_is_independent_of_failed_candidate_c(self):
        rows = [attempt("candidate-C", "C"), attempt("candidate-B", "B"),
                attempt("baseline-B", "B", arm="baseline"),
                attempt("candidate-A", "A"), attempt("baseline-A", "A", arm="baseline")]
        schedule = Schedule(plan(rows), {"candidate-C": [], "candidate-B": ["candidate-C"],
                                        "baseline-B": [], "candidate-A": ["candidate-B"],
                                        "baseline-A": ["baseline-B"]})
        state = self.settle(schedule, schedule.initial_state(), "candidate-C", FAIL, 0, 1)
        selected = schedule.reserve(state, 2)
        self.assertEqual(selected.attempt, schedule.attempts[2])
        self.assertEqual(selected.blocked[0].attempt_id, "candidate-B")
        state = schedule.record(selected.state, "baseline-B", FAIL, 3)
        state = self.settle(schedule, state, "baseline-A", PASS, 4, 5)
        self.assertEqual(schedule.reserve(state, 6).status, "BLOCKED")
        self.assertEqual([r.attempt_id for r in state.reservations],
                         ["candidate-C", "baseline-B", "baseline-A"])

    def test_repetitions_and_scopes_do_not_substitute_for_one_another(self):
        rows = [attempt("C1", "C", case="resource", repetition=1),
                attempt("C2", "C", case="resource", repetition=2),
                attempt("B1", "B", case="behavior", repetition=1),
                attempt("B2", "B", case="behavior", repetition=2),
                attempt("A1", "A", case="activation", repetition=1),
                attempt("A2", "A", case="activation", repetition=2)]
        schedule = Schedule(plan(rows, repetitions=2),
                            {"C1": [], "C2": [], "B1": ["C1"], "B2": ["C2"],
                             "A1": ["B1"], "A2": ["B2"]})
        state = self.settle(schedule, schedule.initial_state(), "C1", PASS, 0, 1)
        state = self.settle(schedule, state, "C2", FAIL, 2, 3)
        state = self.settle(schedule, state, "B1", FAIL, 4, 5)
        state = self.settle(schedule, state, "A1", PASS, 6, 7)
        decision = schedule.reserve(state, 8)
        self.assertEqual(decision.status, "BLOCKED")
        self.assertEqual({b.attempt_id for b in decision.blocked}, {"B2", "A2"})
        self.assertEqual([a.repetition for a in schedule.attempts], [1, 2, 1, 2, 1, 2])

    def test_reservation_consumes_attempt_before_launch_and_never_retries(self):
        schedule = Schedule(plan([attempt("C1", "C"), attempt("C2", "C")]),
                            {"C1": [], "C2": []})
        state = schedule.initial_state()
        selected = schedule.reserve(state, 0)
        self.assertEqual(len(selected.state.reservations), 1)
        self.assertIsNone(selected.state.reservations[0].observation)
        state = schedule.record(selected.state, "C1", Observation("LAUNCH_FAILED", "UNOBTAINABLE"), 1)
        state = self.settle(schedule, state, "C2", Observation("LAUNCH_FAILED", "UNOBTAINABLE"), 2, 3)
        terminal = schedule.reserve(state, 4)
        self.assertEqual(terminal.status, "DONE")
        self.assertEqual(len(terminal.state.reservations), schedule.max_attempts)
        self.assertEqual([r.attempt_id for r in state.reservations], ["C1", "C2"])

    def test_repeated_reserve_does_not_reset_timeout_or_create_second_inflight(self):
        schedule = chain()
        selected = schedule.reserve(schedule.initial_state(), 10)
        self.assertEqual(selected.timeout_seconds, 20)
        waiting = schedule.reserve(selected.state, 17)
        self.assertEqual((waiting.status, waiting.timeout_seconds), ("WAITING", 13))
        self.assertEqual(waiting.state.reservations, selected.state.reservations)
        self.assertEqual(waiting.state.reservations[0].deadline, 30)
        self.assertEqual(selected.state.last_elapsed, 10)

    def test_resume_uses_same_binding_and_original_deadlines(self):
        schedule = chain()
        selected = schedule.reserve(schedule.initial_state(), 10)
        resumed = chain()
        waiting = resumed.reserve(selected.state, 25)
        self.assertEqual(waiting.timeout_seconds, 5)
        self.assertEqual(waiting.state.reservations[0].reserved_at, 10)
        expired = resumed.reserve(waiting.state, 30)
        self.assertEqual((expired.status, expired.reason), ("WAITING", "ATTEMPT_DEADLINE_REACHED"))
        self.assertTrue(expired.stop_required)
        self.assertEqual(expired.timeout_seconds, 0)
        with self.assertRaises(ScheduleError):
            resumed.reserve(expired.state, 0)

    def test_expired_attempt_requires_settlement_before_next_independent_attempt(self):
        schedule = Schedule(plan([attempt("C1", "C", seconds=5), attempt("C2", "C")]),
                            {"C1": [], "C2": []})
        selected = schedule.reserve(schedule.initial_state(), 0)
        expired = schedule.reserve(selected.state, 5)
        self.assertTrue(expired.stop_required)
        self.assertEqual(expired.attempt.attempt_id, "C1")
        still_waiting = schedule.reserve(expired.state, 9)
        self.assertEqual(len(still_waiting.state.reservations), 1)
        with self.assertRaises(ScheduleError):
            schedule.record(still_waiting.state, "C1", PASS, 9)
        settled = schedule.record(still_waiting.state, "C1", TIMEOUT, 9)
        second = schedule.reserve(settled, 9)
        self.assertEqual(second.attempt.attempt_id, "C2")
        with self.assertRaises(ScheduleError):
            schedule.record(second.state, "C1", PASS, 10)

    def test_global_budget_bounds_timeout_and_never_resets_on_resume(self):
        schedule = Schedule(plan([attempt("C1", "C", seconds=20),
                                  attempt("C2", "C", seconds=20)], seconds=30),
                            {"C1": [], "C2": []})
        selected = schedule.reserve(schedule.initial_state(), 25)
        self.assertEqual(selected.timeout_seconds, 5)
        self.assertEqual(selected.state.reservations[0].deadline, 30)
        expired = schedule.reserve(selected.state, 30)
        self.assertEqual(expired.reason, "GLOBAL_DEADLINE_REACHED")
        self.assertTrue(expired.stop_required)
        settled = schedule.record(expired.state, "C1", TIMEOUT, 31)
        exhausted = schedule.reserve(settled, 31)
        self.assertEqual((exhausted.status, exhausted.reason),
                         ("EXHAUSTED", "GLOBAL_TIME_BUDGET_EXHAUSTED"))
        self.assertEqual(len(exhausted.state.reservations), 1)
        self.assertEqual(schedule.reserve(exhausted.state, 40).status, "EXHAUSTED")

    def test_first_reservation_at_global_deadline_is_exhausted_without_consumption(self):
        schedule = chain()
        decision = schedule.reserve(schedule.initial_state(), schedule.max_seconds)
        self.assertEqual(decision.status, "EXHAUSTED")
        self.assertEqual(decision.state.reservations, ())

    def test_result_deadline_is_inclusive_and_success_cannot_be_backdated(self):
        schedule = chain()
        selected = schedule.reserve(schedule.initial_state(), 0)
        for result in (PASS, FAIL):
            with self.subTest(result=result), self.assertRaises(ScheduleError):
                schedule.record(selected.state, "resource", result, 20)
        waiting = schedule.reserve(selected.state, 19)
        with self.assertRaises(ScheduleError):
            schedule.record(waiting.state, "resource", PASS, 18)
        state = schedule.record(waiting.state, "resource", PASS, 19)
        self.assertEqual(state.reservations[-1].finished_at, 19)

    def test_result_must_match_one_unsettled_reservation_and_cannot_replay(self):
        schedule = chain()
        initial = schedule.initial_state()
        with self.assertRaises(ScheduleError):
            schedule.record(initial, "resource", PASS, 0)
        selected = schedule.reserve(initial, 0)
        for wrong in ("unknown", "behavior"):
            with self.subTest(wrong=wrong), self.assertRaises(ScheduleError):
                schedule.record(selected.state, wrong, PASS, 1)
        settled = schedule.record(selected.state, "resource", PASS, 1)
        with self.assertRaises(ScheduleError):
            schedule.record(settled, "resource", PASS, 2)

    def test_clock_rejects_bad_values_and_decreasing_high_water_mark(self):
        schedule = chain()
        for value in (-1, float("inf"), float("-inf"), float("nan"), True, "1", None):
            with self.subTest(value=value), self.assertRaises(ScheduleError):
                schedule.reserve(schedule.initial_state(), value)
        waiting = schedule.reserve(schedule.reserve(schedule.initial_state(), 4).state, 8)
        with self.assertRaises(ScheduleError):
            schedule.reserve(waiting.state, 7)

    def test_terminal_and_blocked_statuses_take_precedence_when_nothing_can_run(self):
        schedule = Schedule(plan([attempt("C", "C")], seconds=20), {"C": []})
        settled = self.settle(schedule, schedule.initial_state(), "C", FAIL, 0, 1)
        self.assertEqual(schedule.reserve(settled, 100).status, "DONE")
        schedule = chain()
        blocked = self.settle(schedule, schedule.initial_state(), "resource", FAIL, 0, 1)
        self.assertEqual(schedule.reserve(blocked, 100).status, "BLOCKED")

    def test_plan_and_dependency_copy_isolation_and_binding(self):
        selected = plan([attempt("C", "C"), attempt("B", "B")])
        dependencies = {"C": [], "B": ["C"]}
        schedule = Schedule(selected, dependencies)
        initial = schedule.initial_state()
        selected["attempts"][0]["workspace"] = "/changed"
        dependencies["B"].clear()
        self.assertEqual(schedule.attempts[0].workspace, "/allocated/C")
        self.assertEqual(schedule.attempts[1].required_predecessors, ("C",))
        with self.assertRaises(FrozenInstanceError):
            initial.last_elapsed = 99
        with self.assertRaises(ScheduleError):
            Schedule(selected, dependencies).reserve(initial, 0)
        another = plan([attempt("C", "C"), attempt("B", "B")])
        another["model"] = "another-explicit-model"
        with self.assertRaises(ScheduleError):
            Schedule(another, {"C": [], "B": ["C"]}).reserve(initial, 0)
        self.assertEqual(initial.reservations, ())

    def test_constructor_requires_explicit_exact_dependency_mapping(self):
        selected = plan([attempt("C", "C"), attempt("B", "B")])
        for dependencies in (None, {}, {"C": []}, {"C": [], "B": [], "extra": []},
                             {"C": [], "B": "C"}, {"C": [], "B": ["C", "C"]},
                             {"C": [], "B": ["missing"]}, {"C": ["B"], "B": ["C"]},
                             {"C": ["C"], "B": []}):
            with self.subTest(dependencies=dependencies), self.assertRaises(ScheduleError):
                Schedule(selected, dependencies)

    def test_constructor_rejects_cross_arm_or_repetition_dependencies(self):
        for change in ({"arm": "baseline"}, {"repetition": 2}):
            with self.subTest(change=change), self.assertRaises(ScheduleError):
                Schedule(plan([attempt("C", "C"), attempt("B", "B", **change)], repetitions=2),
                         {"C": [], "B": ["C"]})

    def test_constructor_rejects_tier_regression_same_tier_edges_and_duplicate_scope(self):
        for rows, dependencies in (
                ([attempt("B", "B"), attempt("C", "C")], {"B": [], "C": []}),
                ([attempt("C1", "C"), attempt("C2", "C")], {"C1": [], "C2": ["C1"]}),
                ([attempt("B1", "B"), attempt("B2", "B")], {"B1": [], "B2": ["B1"]}),
                ([attempt("C1", "C", case="same"), attempt("C2", "C", case="same")],
                 {"C1": [], "C2": []})):
            with self.subTest(rows=rows), self.assertRaises(ScheduleError):
                Schedule(plan(rows), dependencies)

    def test_constructor_rejects_unbounded_or_invalid_scheduling_projection(self):
        original = plan([attempt("C", "C")])
        for key, value in (("max_attempts", 0), ("max_attempts", True), ("max_attempts", 257),
                           ("max_seconds", 0), ("max_seconds", 86401), ("repetitions", 101),
                           ("attempts", []), ("schema_version", "unknown")):
            with self.subTest(key=key, value=value), self.assertRaises(ScheduleError):
                Schedule({**original, key: value}, {"C": []})
        with self.assertRaises(ScheduleError):
            Schedule(plan([attempt("C1", "C"), attempt("C2", "C")], budget=1),
                     {"C1": [], "C2": []})
        for key, value in (("max_seconds", 101), ("repetition", 2), ("arm", "inferred"),
                           ("attempt_id", ""), ("workspace", "")):
            changed = copy.deepcopy(original)
            changed["attempts"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ScheduleError):
                Schedule(changed, {"C": []})

    def test_invalid_observation_combinations_are_rejected(self):
        for outcome, integrity in (("UNKNOWN", "INTACT"), ("PASS", "UNOBTAINABLE"),
                                   ("COULD_NOT_RUN", "INTACT"), ("FAIL", "unknown")):
            with self.subTest(outcome=outcome, integrity=integrity), self.assertRaises(ScheduleError):
                Observation(outcome, integrity)

    def test_impossible_current_states_are_rejected(self):
        schedule = chain()
        selected = schedule.reserve(schedule.initial_state(), 0).state
        row = selected.reservations[0]
        for state in (
                replace(selected, binding="wrong"),
                replace(selected, reservations=(row, row)),
                replace(selected, reservations=(replace(row, deadline=21),)),
                replace(selected, reservations=(replace(row, reserved_at=-1),)),
                replace(selected, reservations=(replace(row, finished_at=1),)),
                replace(selected, reservations=(replace(row, observation=PASS, finished_at=20),),
                        last_elapsed=20),
                replace(selected, reservations=(replace(row, reserved_at=2, deadline=22),)),
                replace(selected, reservations=(Reservation("behavior", 0, 20),))):
            with self.subTest(state=state), self.assertRaises(ScheduleError):
                schedule.reserve(state, 30)


if __name__ == "__main__":
    unittest.main()

