"""Protected schedule replay; process authentication belongs to the collector."""
from datetime import timedelta
from functools import lru_cache

try:
    from . import delivery_core as core, phase_state as store, native_schedule, validation_policy
except ImportError:
    import delivery_core as core
    import phase_state as store
    import native_schedule, validation_policy

SCHEMA = "devforge.utility-native-schedule/v1"


def validate(raw, plan, plan_ref, task_id, *, policy=None, review_ref=None, delivery_ref=None):
    value = core._json(raw, "native schedule binding")
    if value.get("schema_version") == "devforge.utility-native-schedule/v2":
        return _validate_v2(value, plan, plan_ref, task_id, policy, review_ref, delivery_ref)
    core._exact(value, {"schema_version", "task_id", "plan", "required_predecessors"}, "native schedule binding")
    if (value["schema_version"] != SCHEMA or value["task_id"] != task_id
            or plan.get("schema_version") != "devforge.utility-native-plan/v1"):
        core._fail("native schedule task/schema mismatch")
    core._exact(value["plan"], {"path", "sha256"}, "native schedule plan pin")
    if value["plan"] != plan_ref:
        core._fail("native schedule differs from the admitted prerequisite plan")
    # v1 binds ALL earlier-tier observations within each arm/repetition. It does
    # not permit an empty dependency list to silently drop required C/B coverage.
    expected = {}
    for attempt in plan["attempts"]:
        tiers = {"C"} if attempt["tier"] == "B" else {"C", "B"} if attempt["tier"] == "A" else set()
        predecessors = [p for p in plan["attempts"]
                        if p["tier"] in tiers and (p["arm"], p["repetition"]) == (attempt["arm"], attempt["repetition"])]
        if {p["tier"] for p in predecessors} != tiers:
            core._fail("native schedule is missing required preceding-tier coverage in its arm/repetition")
        expected[attempt["attempt_id"]] = [p["attempt_id"] for p in predecessors]
    if value["required_predecessors"] != expected:
        core._fail("native schedule must explicitly cover every required same-arm/repetition predecessor")
    try:
        return native_schedule.Schedule(plan, value["required_predecessors"])
    except native_schedule.ScheduleError as error:
        core._fail(str(error))


def _validate_v2(value, plan, plan_ref, task_id, policy, review_ref, delivery_ref):
    """Selection-only schedule binding; funding admission remains separate."""
    require = validation_policy.require
    core._exact(value, {"schema_version", "task_id", "plan", "required_predecessors",
                       "validation_plan", "review", "allocation"}, "v2 native schedule")
    require(isinstance(policy, validation_policy.Policy), "schedule needs protected external policy context")
    require(plan.get("schema_version") == "devforge.utility-native-plan/v2"
            and value["task_id"] == task_id == plan["task_id"], "schedule/native version or task mismatch")
    require(value["plan"] == plan_ref and value["validation_plan"] == plan["validation_plan"]
            == policy.selection["plan"], "schedule selected plan mismatch")
    require(value["review"] == review_ref and delivery_ref is not None, "schedule needs actual admitted T04")
    review = policy.review(review_ref, delivery_ref)
    require(review["overall"] == "PASS" and review["selection_review"]["outcome"] == "PASS",
            "schedule requires passing independent selection review")
    runtime = core._json(policy.pin(plan["runtime_configuration"], "selected native runtime"), "native runtime")
    core._exact(runtime, {"schema_version", "allocation", "attempts"}, "v2 runtime configuration")
    require(runtime["schema_version"] == "devforge.native-runtime-configuration/v2"
            and runtime["allocation"] == value["allocation"], "schedule allocation version or pin mismatch")
    policy.pin(value["allocation"], "schedule allocation")
    calls = list(policy.calls.values())
    native = [c for c in calls if c["attempt_id"] is not None]
    attempts = plan["attempts"]
    require(native and [c["attempt_id"] for c in native] == [a["attempt_id"] for a in attempts]
            and plan["max_attempts"] == len(attempts), "native projection differs from complete reviewed graph")
    by_attempt = {a["attempt_id"]: a for a in attempts}
    call_for_observation = {}
    call_for_assertion = {}
    for call in calls:
        for oid in call["observation_ids"]:
            call_for_observation.setdefault(oid, set()).add(call["call_id"])
        for aid in call["assertion_ids"]:
            call_for_assertion.setdefault(aid, set()).add(call["call_id"])
    @lru_cache(maxsize=256)
    def ancestors(cid):
        result = set()
        for previous in policy.calls[cid]["depends_on"]:
            result.add(previous)
            result.update(ancestors(previous))
        return frozenset(result)
    for call in native:
        attempt = by_attempt[call["attempt_id"]]
        require(call["max_seconds"] == attempt["max_seconds"], "native per-call bound differs from graph")
        require(call["observation_ids"] and call["assertion_ids"], "native call lacks original assertions/observation")
        for aid in call["assertion_ids"]:
            row, catalog = policy.assertions[aid], policy.catalog[aid]
            require(row["selection"] == "REQUIRED" and row["tier"] == attempt["tier"]
                    and all(catalog[k] == attempt[k] for k in ("case_id", "arm", "repetition")),
                    "native call substitutes an unselected or incompatible original assertion")
            for dep in row["dependency_ids"]:
                if policy.assertions[dep]["selection"] == "REQUIRED":
                    require(call_for_assertion.get(dep, set()) & ancestors(call["call_id"]),
                            "call graph omits selected assertion predecessor")
        for oid in call["observation_ids"]:
            obs = policy.observations[oid]
            require(obs["evidence_kind"] == "N" and set(obs["assertion_ids"]) <= set(call["assertion_ids"]),
                    "native projection weakens observation kind or membership")
            for dep in obs["prerequisite_observation_ids"]:
                require(call_for_observation.get(dep, set()) & ancestors(call["call_id"]),
                        "call graph omits selected observation predecessor")
    for aid, row in policy.assertions.items():
        if row["selection"] == "REQUIRED" and row["tier"] in {"C", "B", "A"} and row["task_id"] != "T05":
            require(any(aid in c["assertion_ids"] for c in native), "native projection omits selected original obligation")
    expected = {}
    for attempt in attempts:
        tiers = {"C"} if attempt["tier"] == "B" else {"C", "B"} if attempt["tier"] == "A" else set()
        previous = [a for a in attempts if a["tier"] in tiers
                    and (a["arm"], a["repetition"]) == (attempt["arm"], attempt["repetition"])]
        observed_tiers = {a["tier"] for a in previous}
        if tiers:
            require("C" in observed_tiers, "selected B/A requires matching C")
        if attempt["tier"] == "A" and "B" not in observed_tiers:
            require(policy.value["mode"] == "Routine" and policy.tasks["T07"]["selection"] == "NOT_SELECTED"
                    and all(a["selection"] != "REQUIRED" for a in policy.assertions.values() if a["tier"] == "B"),
                    "A can omit B only under pre-run reviewed Routine selection")
        expected[attempt["attempt_id"]] = [a["attempt_id"] for a in previous]
    require(value["required_predecessors"] == expected, "schedule omits selected same-arm/repetition predecessors")
    try:
        return native_schedule.Schedule(plan, expected)
    except native_schedule.ScheduleError as error:
        core._fail(str(error))


def validate_allocation_v2(plan, *, frozen=None):
    """No grant is admitted without its selected one-use authority/ledger contract.

    G1 freezes the allocation envelope, but not the externally authenticated
    prior-grant terminal records, already charged non-native completions, or
    one-use grant claim/replay consumer. A graph inventory alone is not funding
    evidence. Keep this dependent action unavailable, including collector entry.
    """
    core._fail("v2 funding authority admission unavailable: external grant ledger and "
               "charged non-native completion contracts must be frozen before reservation")


class JournalSchedule:
    """Rebuilt from committed transitions, never from a caller's state value."""

    def __init__(self, kernel, origin, session_deadline):
        if origin + timedelta(seconds=kernel.max_seconds) > session_deadline:
            core._fail("native campaign budget exceeds the remaining session deadline")
        self.kernel = kernel
        self.origin = origin
        self.state = kernel.initial_state()
        self.decision = None

    def reserve(self, stamp):
        try:
            self.decision = self.kernel.reserve(self.state, (stamp - self.origin).total_seconds())
            self.state = self.decision.state
        except native_schedule.ScheduleError as error:
            core._fail(str(error))

    def cancel_unlaunched(self, attempt_id, stamp):
        self.record(attempt_id, "CANCELLED", "UNOBTAINABLE", stamp)

    def record(self, attempt_id, outcome, integrity, stamp):
        try:
            self.state = self.kernel.record(self.state, attempt_id,
                                           native_schedule.Observation(outcome, integrity),
                                           (stamp - self.origin).total_seconds())
            self.decision = None
        except native_schedule.ScheduleError as error:
            core._fail(str(error))

    def inflight(self):
        return bool(self.state.reservations and self.state.reservations[-1].observation is None)

    def view(self, now):
        # This read-only view may notice expiry but cannot reserve or settle work.
        row = self.state.reservations[-1] if self.inflight() else None
        elapsed = (now - self.origin).total_seconds()
        if elapsed < self.state.last_elapsed:
            core._fail("native campaign clock moved behind its journal high-water mark")
        return {"binding": self.kernel.binding, "origin_utc": self.origin.isoformat(),
                "campaign_deadline_utc": (self.origin + timedelta(seconds=self.kernel.max_seconds)).isoformat(),
                "last_recorded_elapsed_seconds": self.state.last_elapsed,
                "reservations_consumed": len(self.state.reservations),
                "inflight_attempt": row.attempt_id if row else None,
                "inflight_deadline_utc": (self.origin + timedelta(seconds=row.deadline)).isoformat() if row else None,
                "stop_required": bool(row and elapsed >= row.deadline),
                "campaign_expired": elapsed >= self.kernel.max_seconds,
                "decision": self.decision.status if self.decision else None,
                "reason": self.decision.reason if self.decision else None,
                "blocked": [{"attempt_id": b.attempt_id, "predecessor_id": b.predecessor_id, "reason": b.reason}
                            for b in self.decision.blocked] if self.decision else [],
                "native_launch_admitted": False, "execution": "NOT_RUN"}
