"""Protected schedule replay; process authentication belongs to the collector."""
from datetime import timedelta

try:
    from . import delivery_core as core, phase_state as store, native_schedule
except ImportError:
    import delivery_core as core
    import phase_state as store
    import native_schedule

SCHEMA = "devforge.utility-native-schedule/v1"


def validate(raw, plan, plan_ref, task_id):
    value = core._json(raw, "native schedule binding")
    core._exact(value, {"schema_version", "task_id", "plan", "required_predecessors"}, "native schedule binding")
    if value["schema_version"] != SCHEMA or value["task_id"] != task_id:
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
