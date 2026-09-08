"""Protected schedule replay; process authentication belongs to the collector."""
from datetime import timedelta
from pathlib import Path
import os
import stat
import time
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


FUNDING_KEYS = set('grant_id authority_ref owner purpose origin_utc deadline_utc clock_id origin_monotonic_ns deadline_monotonic_ns prior_ledgers'.split())
ALLOCATION_KEYS = set('schema_version task_id cases_sha256 validation_plan funding max_total_attempts preparation_attempts max_seconds per_attempt_max_seconds required_calls'.split())
AUTHORITY_KEYS = (FUNDING_KEYS - {'authority_ref', 'owner'}) | set('schema_version producer task_id validation_plan max_total_attempts max_seconds per_attempt_max_seconds claim_root preparation completed_calls call_records'.split())
CALL_RECORD_KEYS = set('schema_version grant_id call_id producer charged status started_utc completed_utc started_monotonic_ns completed_monotonic_ns evidence'.split())


def clock_id():
    return 'linux-boot:' + Path('/proc/sys/kernel/random/boot_id').read_text().strip()


class Funding:
    """External-assignment-selected funding with shared, exclusive host custody.

    Files are authority only under the existing protected launcher boundary.
    Producer strings from an unselected candidate are never a funding source.
    """
    def __init__(self, allocation, plan, policy):
        require = validation_policy.require
        self.allocation, self.plan, self.policy = allocation, plan, policy
        self.funding = f = allocation['funding']
        core._exact(f, FUNDING_KEYS, 'v2 funding authority')
        require(isinstance(policy, validation_policy.Policy), 'funding authority needs external assignment context')
        selected = policy.authorization.get('funding')
        core._exact(selected, {'authority_ref', 'owner', 'claim_root'}, 'external funding selection')
        require(selected['authority_ref'] == f['authority_ref'] and selected['owner'] == f['owner']
                == policy.owner, 'funding authority is not selected by the external owner')
        self.authority = a = core._json(policy.pin(f['authority_ref'], 'funding authority'), 'funding authority')
        core._exact(a, AUTHORITY_KEYS, 'funding authority record')
        require(a['schema_version'] == 'devforge.funding-authority/v1' and a['producer'] == f['owner']
                and a['task_id'] == plan['task_id'] and a['validation_plan'] == plan['validation_plan'],
                'funding authority producer/task/plan mismatch')
        require(all(a[k] == f[k] for k in FUNDING_KEYS - {'authority_ref', 'owner'}), 'funding authority original clocks or grant changed')
        require(f['purpose'] == 'native campaign ' + plan['task_id'], 'grant purpose does not authorize this native campaign')
        core._text(f['grant_id'], 'grant identity')
        self.origin = store._stamp(f['origin_utc'], 'funding origin')
        self.deadline = store._stamp(f['deadline_utc'], 'funding deadline')
        for k in ('origin_monotonic_ns', 'deadline_monotonic_ns'):
            require(type(f[k]) is int and f[k] > 0, 'funding monotonic timestamp must be positive integer')
        require(f['clock_id'] == clock_id(), 'funding monotonic clock source changed')
        for k in ('max_total_attempts', 'max_seconds', 'per_attempt_max_seconds'):
            validation_policy.positive(a[k], 'grant ' + k)
            validation_policy.positive(allocation[k], 'allocation ' + k)
            require(allocation[k] <= a[k], 'allocation exceeds external grant limit')
        seconds = allocation['max_seconds']
        require(0 < (self.deadline-self.origin).total_seconds() <= seconds
                and 0 < (f['deadline_monotonic_ns']-f['origin_monotonic_ns']) / 1e9 <= seconds,
                'funding original time ceilings exceed campaign allocation')
        self.root = store._absolute(a['claim_root'], 'shared grant custody')
        require(str(self.root) == selected['claim_root'], 'shared funding custody differs from external assignment')
        info = self.root.lstat()
        require(not self.root.is_symlink() and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o700, 'shared grant custody must be host-owned private directory')
        require(not store._collide(self.root, policy.project), 'shared grant custody overlaps candidate')
        prior_ids, prior_digests = set(), set()
        for ref in validation_policy.rows(f['prior_ledgers'], 'prior terminal ledgers'):
            ledger = core._json(policy.pin(ref, 'prior terminal ledger'), 'prior terminal ledger')
            core._exact(ledger, {'schema_version','grant_id','producer','approved','charged','remaining','status','evidence'}, 'prior terminal ledger')
            require(ledger['schema_version'] == 'devforge.funding-terminal/v1' and ledger['producer'] == f['owner']
                    and ledger['status'] in {'EXHAUSTED','CLOSED'} and ledger['remaining'] == 0,
                    'prior funding ledger must be externally selected and terminal')
            require(type(ledger['approved']) is int and type(ledger['charged']) is int
                    and 0 <= ledger['charged'] <= ledger['approved']
                    and (ledger['status'] != 'EXHAUSTED' or ledger['charged'] == ledger['approved']),
                    'prior terminal ledger accounting differs')
            require(ledger['grant_id'] not in prior_ids and ref['sha256'] not in prior_digests,
                    'duplicate prior terminal ledger')
            prior_ids.add(ledger['grant_id']); prior_digests.add(ref['sha256'])
            for evidence in validation_policy.rows(ledger['evidence'], 'terminal evidence', True):
                policy.pin(evidence, 'terminal evidence')
        require(f['grant_id'] not in prior_ids and f['authority_ref']['sha256'] not in prior_digests,
                'exhausted funding authority cannot reopen')
        self.calls = policy.calls
        require(allocation['required_calls'] == list(self.calls.values()), 'complete funding call graph differs from reviewed inventory')
        require(type(allocation['preparation_attempts']) is int and allocation['preparation_attempts'] >= 0,
                'preparation count must be nonnegative integer')
        require(allocation['preparation_attempts'] + len(self.calls) <= allocation['max_total_attempts'],
                'complete graph and charged preparation exceed grant cap')
        require(all(c['max_seconds'] <= min(allocation['per_attempt_max_seconds'], seconds) for c in self.calls.values()),
                'call bound exceeds complete grant limit')
        native = [c for c in self.calls.values() if c['attempt_id'] is not None]
        require([c['attempt_id'] for c in native] == [x['attempt_id'] for x in plan['attempts']]
                and plan['max_attempts'] == len(native) and plan['max_seconds'] == seconds,
                'native projection omits or changes complete funding inventory')
        require(all(c['max_seconds'] == x['max_seconds'] for c,x in zip(native,plan['attempts'])), 'native call clock differs')
        require(isinstance(a['call_records'], dict) and set(a['call_records']) == set(self.calls), 'funding lacks complete external call record locators')
        paths = [store._absolute(p, 'funding completion locator') for p in a['call_records'].values()]
        require(len(set(paths)) == len(paths) and all(core._within(p,self.root) for p in paths), 'completion locators require distinct shared protected custody')
        self.completed = {}
        setup = validation_policy.rows(a['preparation'], 'charged setup')
        require(len(setup) == allocation['preparation_attempts'], 'funding lacks actual charged preparation evidence')
        for ref in setup:
            row = self.completion(ref, setup=True)
            require(row['call_id'] not in self.completed, 'duplicate charged setup')
            self.completed[row['call_id']] = row
        for ref in validation_policy.rows(a['completed_calls'], 'completed graph calls'):
            row = self.completion(ref)
            require(row['call_id'] not in self.completed, 'graph completion counted twice')
            self.completed[row['call_id']] = row
        # The already admitted semantic T04 generation must have its actual
        # completion, not merely a row declaring that a reviewer was selected.
        reviews = [c for c in self.calls.values() if c['kind'] == 'static_review' and c['producer'] == policy.value['selection_reviewer']]
        require(reviews and all(c['call_id'] in self.completed for c in reviews), 'funding lacks charged T04 completion evidence')

    def completion(self, ref, *, setup=False):
        require = validation_policy.require
        row = core._json(self.policy.pin(ref, 'charged call completion'), 'charged call completion')
        core._exact(row, CALL_RECORD_KEYS, 'charged call completion')
        cid = row['call_id']; call = self.calls.get(cid)
        require(row['schema_version'] == 'devforge.funding-call/v1' and row['grant_id'] == self.funding['grant_id']
                and row['charged'] is True and row['status'] in {'COMPLETED','FAILED','TIMED_OUT'}, 'call completion is not an actual charged terminal record')
        require((setup and call is None and row['producer'] == self.funding['owner'])
                or (not setup and call is not None and row['producer'] == call['producer']), 'call completion producer or graph membership differs')
        start = store._stamp(row['started_utc'], 'call start'); end = store._stamp(row['completed_utc'], 'call completion')
        bound = self.allocation['per_attempt_max_seconds'] if setup else call['max_seconds']
        require(self.origin <= start <= end <= self.deadline and (end-start).total_seconds() <= bound,
                'completed call exceeds original UTC allocation')
        require(type(row['started_monotonic_ns']) is int and type(row['completed_monotonic_ns']) is int
                and self.funding['origin_monotonic_ns'] <= row['started_monotonic_ns'] <= row['completed_monotonic_ns'] <= self.funding['deadline_monotonic_ns']
                and (row['completed_monotonic_ns']-row['started_monotonic_ns'])/1e9 <= bound, 'completed call exceeds original monotonic allocation')
        for evidence in validation_policy.rows(row['evidence'], 'actual completed output', True):
            self.policy.pin(evidence, 'actual completed output')
        if call and call['kind'] == 'static_review' and call['producer'] == self.policy.value['selection_reviewer']:
            require(any(core._json(self.policy.pin(e, 'T04 completed output'), 'T04 completed output').get('schema_version') == validation_policy.REVIEW_SCHEMA for e in row['evidence']), 'T04 completion lacks actual review output')
        return row

    def consume(self, call_id, stamp, mono):
        """Consume an externally completed semantic call at its frozen locator.

        This records externally dispatched work; it never launches a model or
        substitutes a completion file for authenticated native process evidence.
        """
        require = validation_policy.require
        call = self.calls[call_id]
        require(call['attempt_id'] is None, 'external completion cannot impersonate native launch')
        if call_id not in self.completed:
            path = Path(self.authority['call_records'][call_id])
            if self.policy.frozen is not None and str(path) in self.policy.frozen:
                raw = self.policy.frozen[str(path)]
            else:
                raw = store._external(path, 1024*1024, 'selected external call completion')
            ref = {'path':str(path),'sha256':store._hash(raw)}
            self.completed[call_id] = self.completion(ref)
        row = self.completed[call_id]
        require(store._stamp(row['completed_utc'], 'call completion') <= stamp
                and row['completed_monotonic_ns'] <= mono, 'dependent work precedes external call completion')
        require(row['status'] == 'COMPLETED', 'required semantic call did not complete')
        return row

    def dependencies(self, call_id, stamp, mono):
        for dep in self.calls[call_id]['depends_on']:
            self.dependencies(dep, stamp, mono)
            if self.calls[dep]['attempt_id'] is None:
                self.consume(dep, stamp, mono)

    def elapsed(self, stamp, mono):
        validation_policy.require(mono >= self.funding['origin_monotonic_ns'] and stamp >= self.origin,
                                  'funding original clock reset or moved backward')
        return max((stamp-self.origin).total_seconds(), (mono-self.funding['origin_monotonic_ns'])/1e9)

    def live(self, stamp, session_deadline):
        mono = time.monotonic_ns()
        self.elapsed(stamp, mono)
        validation_policy.require(clock_id() == self.funding['clock_id'] and stamp < min(self.deadline,session_deadline)
            and mono < self.funding['deadline_monotonic_ns'], 'funding original deadline expired')
        return mono

    def claim(self, state, *, create=False):
        identity = {'schema_version':'devforge.funding-claim/v1', 'grant_id':self.funding['grant_id'],
            'authority_ref':self.funding['authority_ref'], 'allocation_sha256':store._hash(store._dump(self.allocation)),
            'state_root':str(state.root), 'session_sha256':state.manifest['session_sha256']}
        path = self.root / ('grant-' + store._hash(self.funding['grant_id'].encode()) + '.json')
        raw = store._dump(identity)
        if create:
            try:
                with core._directory(self.root, 'shared funding custody') as fd:
                    try:
                        handle=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                    except FileExistsError:
                        core._fail('funding grant is already claimed; another session cannot reuse authority')
                    with os.fdopen(handle,'wb') as stream: stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                    os.fsync(fd)
            except FileExistsError:
                core._fail('funding grant is already claimed; another session cannot reuse authority')
        else:
            validation_policy.require(store._external(path, 1024*1024, 'shared funding claim') == raw,
                                      'funding grant claim differs from original session')
        return path


def validate_allocation_v2(plan, *, frozen=None, policy=None):
    if not isinstance(policy, validation_policy.Policy):
        core._fail('v2 funding authority admission requires protected external assignment context')
    runtime_raw = policy.pin(plan['runtime_configuration'], 'native runtime configuration')
    runtime = core._json(runtime_raw, 'native runtime configuration')
    core._exact(runtime, {'schema_version','allocation','attempts'}, 'native runtime configuration')
    validation_policy.require(runtime['schema_version'] == 'devforge.native-runtime-configuration/v2', 'funding runtime version differs')
    raw = policy.pin(runtime['allocation'], 'native complete allocation')
    allocation = core._json(raw, 'native complete allocation')
    core._exact(allocation, ALLOCATION_KEYS, 'v2 funding authority allocation')
    validation_policy.require(allocation['schema_version'] == 'devforge.utility-native-allocation/v2'
        and allocation['task_id'] == plan['task_id'] and allocation['cases_sha256'] == plan['cases']['sha256']
        and allocation['validation_plan'] == plan['validation_plan'] == policy.selection['plan'], 'funding authority task/cases/validation plan differs')
    policy.funding_context = Funding(allocation, plan, policy)
    sources = [('native_allocation',runtime['allocation']['path'],raw),
               ('native_runtime_configuration',plan['runtime_configuration']['path'],runtime_raw)]
    sources.extend(policy.fixed())
    return allocation, sources


class JournalSchedule:
    """Rebuilt from committed transitions, never from a caller's state value."""

    def __init__(self, kernel, origin, session_deadline, funding=None):
        if origin + timedelta(seconds=kernel.max_seconds) > session_deadline:
            core._fail("native campaign budget exceeds the remaining session deadline")
        self.kernel = kernel
        self.funding = funding
        self.observed_elapsed = 0
        self.last_monotonic_ns = funding.funding["origin_monotonic_ns"] if funding else None
        self.origin = origin
        self.state = kernel.initial_state()
        self.decision = None

    def reserve(self, stamp, monotonic_ns=None):
        elapsed = (stamp - self.origin).total_seconds()
        if self.funding:
            validation_policy.require(type(monotonic_ns) is int and monotonic_ns >= self.last_monotonic_ns,
                                      "funding journal monotonic clock moved backward")
            elapsed = self.funding.elapsed(stamp, monotonic_ns)
            self.last_monotonic_ns = monotonic_ns
        try:
            self.decision = self.kernel.reserve(self.state, elapsed)
            self.state = self.decision.state
        except native_schedule.ScheduleError as error:
            core._fail(str(error))

    def cancel_unlaunched(self, attempt_id, stamp):
        self.record(attempt_id, "CANCELLED", "UNOBTAINABLE", stamp)

    def record(self, attempt_id, outcome, integrity, stamp):
        try:
            self.state = self.kernel.record(self.state, attempt_id,
                                           native_schedule.Observation(outcome, integrity),
                                           max((stamp - self.origin).total_seconds(), self.state.last_elapsed, self.observed_elapsed)
                                           if self.funding else (stamp - self.origin).total_seconds())
            self.decision = None
        except native_schedule.ScheduleError as error:
            core._fail(str(error))

    def inflight(self):
        return bool(self.state.reservations and self.state.reservations[-1].observation is None)

    def view(self, now):
        # This read-only view may notice expiry but cannot reserve or settle work.
        row = self.state.reservations[-1] if self.inflight() else None
        elapsed = (now - self.origin).total_seconds()
        if self.funding:
            elapsed = max(elapsed, self.funding.elapsed(now, time.monotonic_ns()))
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
