"""Pure scheduling/accounting for an already admitted utility native plan.

This module neither admits plans/results nor launches or stops clients. Call
utility_evidence.native_plan first. A protected, single-writer caller must freeze
the supplemental required_predecessors map together with that plan, authenticate
observations, atomically retain each returned State BEFORE launch, and never
reuse an old state or relaunch a reservation on resume. Immutable Python values
are not protected custody, an anti-rollback journal, or process isolation.

elapsed_seconds always means authoritative elapsed time since the SAME original
experiment origin, including waiting/resume; it is not a fresh per-call clock.
The caller must preserve that origin across restarts and enforce returned
timeouts on the owned process. Expired in-flight work remains reserved until the
caller stops/reaps it and records its terminal result.

required_predecessors is a supplemental kernel input awaiting versioned runtime
plan integration; it is NOT an additional accepted field in native-plan/v1.
Every attempt needs an explicit entry (possibly empty for non-applicability).
Applicability is exactly the caller's frozen mapping: upstream plan/contract
integration must establish its completeness against the required observations.
An empty list is not evidence that undeclared required coverage is satisfied.
Dependencies name attempts, never inferred case names. Edges stay in the same
arm/repetition; callers must allocate any required cross-scope observation
explicitly in its own scope. Declared attempt order must already be C, then B,
then A. C predecessors require intact PASS; B predecessors allow intact PASS or
FAIL. Completion of the allocation is never an evaluation PASS.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
from collections.abc import Mapping


class ScheduleError(ValueError):
    """Malformed scheduling input, stale result, or invalid state transition."""


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ScheduleError(f"{label} must be nonempty text")
    return value


def _positive(value, maximum, label):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ScheduleError(f"{label} must be an integer in [1, {maximum}]")
    return value


def _elapsed(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ScheduleError("elapsed_seconds must be finite and nonnegative")
    return value


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    case_id: str
    tier: str
    arm: str
    repetition: int
    workspace: str
    client_state: str
    max_seconds: int
    required_predecessors: tuple[str, ...]


@dataclass(frozen=True)
class Observation:
    """Caller-authenticated result; contamination never counts as an intact grade."""

    outcome: str
    integrity: str

    def __post_init__(self):
        grades = {"PASS", "FAIL"}
        unavailable = {"COULD_NOT_RUN", "LAUNCH_FAILED", "TIMED_OUT", "CANCELLED"}
        if self.outcome not in grades | unavailable:
            raise ScheduleError("unknown terminal observation outcome")
        if self.integrity not in {"INTACT", "UNOBTAINABLE", "CONTAMINATED"}:
            raise ScheduleError("unknown observation integrity")
        if self.outcome in grades and self.integrity == "UNOBTAINABLE":
            raise ScheduleError("an unobtainable observation cannot have a quality grade")
        if self.outcome in unavailable and self.integrity == "INTACT":
            raise ScheduleError("an unavailable result cannot be an intact observation")


@dataclass(frozen=True)
class Reservation:
    attempt_id: str
    reserved_at: float
    deadline: float
    observation: Observation | None = None
    finished_at: float | None = None


@dataclass(frozen=True)
class State:
    """Caller-owned current value; reservations count even before successful launch."""

    binding: str
    last_elapsed: float
    reservations: tuple[Reservation, ...] = ()


@dataclass(frozen=True)
class BlockedAttempt:
    attempt_id: str
    predecessor_id: str
    reason: str


@dataclass(frozen=True)
class Decision:
    state: State
    status: str
    reason: str
    attempt: Attempt | None = None
    timeout_seconds: float = 0
    stop_required: bool = False
    blocked: tuple[BlockedAttempt, ...] = ()


@dataclass(frozen=True, init=False)
class Schedule:
    """Bounded deterministic transitions, assuming externally protected custody.

    The constructor checks the scheduling projection and dependencies, not pinned
    files, model/auth selection, source visibility, process lifetime, or evidence
    authenticity. Callers must first pass the full existing native plan validator.
    """

    binding: str
    attempts: tuple[Attempt, ...]
    max_attempts: int
    max_seconds: int

    def __init__(self, plan: Mapping, required_predecessors: Mapping):
        if not isinstance(plan, Mapping):
            raise ScheduleError("expected an already validated native plan")
        if plan.get("schema_version") != "devforge.utility-native-plan/v1":
            raise ScheduleError("native plan schema mismatch")
        _text(plan.get("task_id"), "task ID")
        max_attempts = _positive(plan.get("max_attempts"), 256, "max_attempts")
        max_seconds = _positive(plan.get("max_seconds"), 86400, "max_seconds")
        repetitions = _positive(plan.get("repetitions"), 100, "repetitions")
        rows = plan.get("attempts")
        if not isinstance(rows, list) or not rows or len(rows) > max_attempts:
            raise ScheduleError("attempt allocation is missing or exceeds max_attempts")
        if not isinstance(required_predecessors, Mapping):
            raise ScheduleError("an explicit frozen required_predecessors map is required")
        selected, scopes, ids = [], set(), set()
        prior_tier = 0
        for row in rows:
            if not isinstance(row, Mapping):
                raise ScheduleError("attempt must be a mapping")
            identity = _text(row.get("attempt_id"), "attempt ID")
            case = _text(row.get("case_id"), "case ID")
            tier, arm = row.get("tier"), row.get("arm")
            if tier not in ("C", "B", "A") or arm not in ("candidate", "baseline"):
                raise ScheduleError("invalid attempt tier or arm")
            rank = ("C", "B", "A").index(tier)
            if rank < prior_tier:
                raise ScheduleError("declared attempts must follow C then B then A order")
            prior_tier = rank
            repetition = _positive(row.get("repetition"), repetitions, "repetition")
            scope = (case, tier, arm, repetition)
            if identity in ids or scope in scopes:
                raise ScheduleError("duplicate attempt ID or scope")
            ids.add(identity)
            scopes.add(scope)
            deps = required_predecessors.get(identity)
            if not isinstance(deps, (list, tuple)) or any(not isinstance(dep, str) for dep in deps):
                raise ScheduleError("every attempt needs an explicit predecessor list")
            if len(set(deps)) != len(deps):
                raise ScheduleError("duplicate required predecessor")
            selected.append(Attempt(
                identity, case, tier, arm, repetition,
                _text(row.get("workspace"), "workspace"),
                _text(row.get("client_state"), "client_state"),
                _positive(row.get("max_seconds"), max_seconds, "attempt max_seconds"),
                tuple(deps)))
        if set(required_predecessors) != ids:
            raise ScheduleError("predecessor map must cover exactly the allocated attempts")
        preceding = {}
        for attempt in selected:
            for identity in attempt.required_predecessors:
                predecessor = preceding.get(identity)
                if predecessor is None:
                    raise ScheduleError("predecessor must name an earlier declared attempt")
                allowed = {"C"} if attempt.tier == "B" else {"C", "B"} if attempt.tier == "A" else set()
                if predecessor.tier not in allowed:
                    raise ScheduleError("predecessor tier is not applicable to this attempt")
                if (predecessor.arm, predecessor.repetition) != (attempt.arm, attempt.repetition):
                    raise ScheduleError("predecessor arm/repetition must match its dependent")
            preceding[attempt.attempt_id] = attempt
        try:
            bound = json.dumps(
                {"plan": plan, "required_predecessors": {
                    a.attempt_id: list(a.required_predecessors) for a in selected}},
                sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (ValueError, TypeError) as error:
            raise ScheduleError("frozen plan must contain JSON values") from error
        object.__setattr__(self, "binding", hashlib.sha256(bound).hexdigest())
        object.__setattr__(self, "attempts", tuple(selected))
        object.__setattr__(self, "max_attempts", max_attempts)
        object.__setattr__(self, "max_seconds", max_seconds)

    def initial_state(self) -> State:
        """Create once at the original experiment origin, never to resume a run."""
        return State(self.binding, 0)

    def _next(self, reservations):
        """Find the first eligible unreserved attempt, skipping blocked scopes."""
        recorded = {row.attempt_id: row for row in reservations}
        allocated = {row.attempt_id: row for row in self.attempts}
        blocked, blocked_ids = [], set()
        for attempt in self.attempts:
            if attempt.attempt_id in recorded:
                continue
            for identity in attempt.required_predecessors:
                previous = recorded.get(identity)
                if identity in blocked_ids:
                    reason = "PREDECESSOR_BLOCKED"
                elif previous is None or previous.observation is None:
                    raise ScheduleError("required predecessor is not settled")
                elif previous.observation.integrity != "INTACT":
                    reason = "OBSERVATION_" + previous.observation.integrity
                elif allocated[identity].tier == "C" and previous.observation.outcome != "PASS":
                    reason = "C_NOT_PASS"
                elif previous.observation.outcome not in {"PASS", "FAIL"}:
                    reason = "OBSERVATION_UNOBTAINABLE"
                else:
                    continue
                blocked.append(BlockedAttempt(attempt.attempt_id, identity, reason))
                blocked_ids.add(attempt.attempt_id)
                break
            else:
                return attempt, tuple(blocked)
        return None, tuple(blocked)

    def _checked(self, state, elapsed_seconds):
        elapsed = _elapsed(elapsed_seconds)
        if not isinstance(state, State) or state.binding != self.binding:
            raise ScheduleError("state is not bound to this frozen plan and dependency map")
        _elapsed(state.last_elapsed)
        if elapsed < state.last_elapsed:
            raise ScheduleError("elapsed time cannot decrease or reset on resume")
        if not isinstance(state.reservations, tuple) or len(state.reservations) > self.max_attempts:
            raise ScheduleError("invalid reservation allocation")
        prior, end = [], 0
        for index, row in enumerate(state.reservations):
            if not isinstance(row, Reservation):
                raise ScheduleError("invalid reservation record")
            expected, _ = self._next(prior)
            if expected is None or row.attempt_id != expected.attempt_id:
                raise ScheduleError("reservation is duplicated, blocked, or outside declared order")
            _elapsed(row.reserved_at)
            _elapsed(row.deadline)
            if row.reserved_at < end or row.reserved_at >= self.max_seconds:
                raise ScheduleError("reservation time overlaps or exceeds the global deadline")
            if row.deadline != min(self.max_seconds, row.reserved_at + expected.max_seconds):
                raise ScheduleError("reservation deadline differs from its original budget")
            if row.observation is None:
                if row.finished_at is not None or index != len(state.reservations) - 1:
                    raise ScheduleError("only the last reservation may remain in flight")
                end = row.reserved_at
            else:
                if not isinstance(row.observation, Observation):
                    raise ScheduleError("invalid terminal observation")
                _elapsed(row.finished_at)
                if row.finished_at < row.reserved_at:
                    raise ScheduleError("result predates its reservation")
                if row.finished_at >= row.deadline and row.observation.outcome in {"PASS", "FAIL"}:
                    raise ScheduleError("quality observation was recorded at or after its deadline")
                end = row.finished_at
            prior.append(row)
        if end > state.last_elapsed:
            raise ScheduleError("state elapsed high-water mark predates its reservations")
        return replace(state, last_elapsed=elapsed)

    def reserve(self, state: State, elapsed_seconds: float) -> Decision:
        """Reserve once or report WAITING/EXHAUSTED/BLOCKED/DONE.

        A RESERVED decision consumes allocation before launch. Repeated calls
        while in flight return WAITING, never another launch authorization.
        At either inclusive deadline, stop_required is true and timeout is zero;
        no successor can be reserved until the caller has stopped/reaped and
        recorded that attempt. Settlement precedes budget/dependency decisions.
        With no in-flight work, fully accounted or fully blocked allocations take
        precedence over budgets; budgets constrain only otherwise eligible work.
        """
        current = self._checked(state, elapsed_seconds)
        if current.reservations and current.reservations[-1].observation is None:
            row = current.reservations[-1]
            attempt = next(a for a in self.attempts if a.attempt_id == row.attempt_id)
            remaining = max(0, row.deadline - current.last_elapsed)
            reason = ("GLOBAL_DEADLINE_REACHED" if current.last_elapsed >= self.max_seconds
                      else "ATTEMPT_DEADLINE_REACHED" if not remaining else "ATTEMPT_IN_FLIGHT")
            return Decision(current, "WAITING", reason, attempt, remaining, not remaining)
        attempt, blocked = self._next(current.reservations)
        if attempt is None:
            return Decision(current, "BLOCKED" if blocked else "DONE",
                            "REQUIRED_PREDECESSORS_BLOCKED" if blocked else "ALLOCATION_COMPLETED",
                            blocked=blocked)
        if current.last_elapsed >= self.max_seconds:
            return Decision(current, "EXHAUSTED", "GLOBAL_TIME_BUDGET_EXHAUSTED", blocked=blocked)
        if len(current.reservations) >= self.max_attempts:
            return Decision(current, "EXHAUSTED", "ATTEMPT_BUDGET_EXHAUSTED", blocked=blocked)
        deadline = min(self.max_seconds, current.last_elapsed + attempt.max_seconds)
        reservation = Reservation(attempt.attempt_id, current.last_elapsed, deadline)
        current = replace(current, reservations=(*current.reservations, reservation))
        return Decision(current, "RESERVED", "ATTEMPT_RESERVED", attempt,
                        deadline - current.last_elapsed, blocked=blocked)

    def record(self, state: State, attempt_id: str, observation: Observation,
               elapsed_seconds: float) -> State:
        """Settle the one reservation after caller-confirmed process termination.

        Recording time is authoritative: no result-provided completion backdating.
        PASS/FAIL at or after the deadline is rejected. Record a non-grade terminal
        outcome for late/failed/unobtainable work; it still consumes the reservation.
        This method authenticates neither termination nor observation provenance.
        """
        current = self._checked(state, elapsed_seconds)
        if not isinstance(observation, Observation):
            raise ScheduleError("record requires an explicit Observation")
        if not current.reservations:
            raise ScheduleError("result has no reserved attempt")
        row = current.reservations[-1]
        if row.attempt_id != attempt_id or row.observation is not None:
            raise ScheduleError("result is stale, replayed, or names the wrong in-flight attempt")
        if current.last_elapsed >= row.deadline and observation.outcome in {"PASS", "FAIL"}:
            raise ScheduleError("cannot accept a quality observation at or after its deadline")
        settled = replace(row, observation=observation, finished_at=current.last_elapsed)
        return replace(current, reservations=(*current.reservations[:-1], settled))

