# Native utility lifecycle contract

This interface supplies protected accounting and host process receipt custody.
It does not certify semantic review quality, native callback origin, rendered
delivery, receiving execution, or human acceptance. A successful deterministic
test is fixture evidence only. No native campaign is admitted by this document.

## Operator interface

All commands run through `devforge delivery --state <protected-state>`.
The worker cannot write this state, the collector authority, or the selected
external review files. The native entrypoint accepts no command or grade string.

1. `native-schedule-bind --schedule <frozen-binding>` validates the complete
   independent call inventory and total cap before retaining any campaign
   origin or reservation. It snapshots the allocation and runtime configuration
   alongside the frozen cases, plan and C/B/A dependency map.
2. `native-schedule-reserve` consumes the next eligible attempt before launch.
3. `native-process-launch --attempt <id>` rechecks the bound call allocation
   and validates process prerequisites, records one exclusive launch claim under the journal
   lock, releases the lock, and invokes the host collector. It imports the
   returned process receipt automatically. A repeated launch is rejected.
4. `native-process-import --attempt <id> --receipt <collector-receipt>` supports
   recovery of a completed collector operation. The path must be the exact
   attempt receipt in `<protected-state>/native-collector`; the importer checks
   its HMAC, request identity, exact reservation binding and raw stream bytes.
   Repeated imports are rejected.
5. `native-result-review --attempt <id> --review <selected-review>` consumes the
   separately selected operator/independent review. A healthy process receipt
   alone leaves the reservation pending. `native-result-close --attempt <id>
   --reason <reason>` settles an authenticated completed but ungraded attempt
   as unavailable; it cannot produce a quality grade.

`native-schedule-cancel` remains limited to unclaimed reservations. A launch
claim with no authentic process receipt is unresolved. Neither resume, a failed
launch, nor a new command restores the attempt budget or original clock.

## Supplemental allocation

Existing `devforge.utility-native-plan/v1` retains its inspection meaning.
Protected schedule binding and reservation additionally require the plan's pinned
`runtime_configuration` to use `devforge.native-runtime-configuration/v1` and
contain a pinned `allocation` plus an exact `attempts` runtime inventory.
Opaque legacy, incomplete, mismatched or over-cap documents cannot bind or
reserve a campaign. Rejected binding preflight creates no campaign clock and
consumes no attempt. Replay requires the exact allocation/runtime snapshots;
old journals lacking that complete binding cannot silently gain reserve access.
Current source changes block reservation, while cleanup replays the frozen
original allocation and retains the original clock.

The allocation uses `devforge.utility-native-allocation/v1` with exactly:

- `task_id`, `cases_sha256`, `max_total_attempts`, `preparation_attempts`;
- `max_seconds`, `per_attempt_max_seconds`, `required_calls`.

`max_total_attempts` cannot exceed 24, `max_seconds` cannot exceed 14400, and
`per_attempt_max_seconds` cannot exceed 600. Both the declared plan budget and
the full call list plus preparation calls must fit the total cap. The plan and
allocation must select the same original campaign duration.

The independent pinned cases document must use
`devforge.utility-native-cases/v1` with exactly `task_id` and `required_calls`
in addition to its schema identifier. Its call list must equal the allocation's
call list, whose identity/order must equal all plan attempts. Every call has:

- `attempt_id`, `case_id`, `tier`, `arm`, `repetition`;
- `purpose`: `case`, `control`, `probe`, `grader`, or `receiving`;
- `interaction`: `single-turn` or `awaiting-user`;
- `managed_worker_required`: an explicit boolean;
- `review_path` and `reviewer`: the frozen external review authority selection.

All required probes, failed launches, graders and receiving invocations consume
the same allocation. The independently frozen inventory is the coverage oracle;
the implementation cannot infer missing requirements from an opaque document.
Awaiting-user calls also require ordered `continuation_units` matching the
frozen answer policy. Initial calls plus every allocated continuation unit plus
preparation calls must fit the total cap. All units are charged upfront; unused
units cannot be recycled. The collector refuses unsupported interactions during
preflight. Durable per-unit collector claims precede generation-resuming sends,
under the original journal launch claim and original clocks. Managed case execution requires
the separately selected worker session/state and broker configuration described
by the collector, distinct from the outer validator's evaluation state.

## Process and review evidence

The collector owns its private 32-byte HMAC key. The key is never a journal
snapshot, report field, runtime mount, or worker input. A receipt binds the task,
plan, schedule, reservation record, challenge, attempt, original clock, client,
model, command, prompt, runtime configuration, and installed input identity.
Authentication establishes collector execution custody; it does not grade the
worker's output. Worker grade strings and unsigned or `FIXTURE_ONLY` output
cannot become native result evidence.

An eligible process observation needs an exited and reaped leader, no surviving
owned group, complete stdout/stderr, no output truncation, a valid completed
native event stream, intact frozen inputs, and time remaining on the original
reservation. If managed execution is required, the nested worker must also be
completed with a verified receipt and a quiescent broker. Callback origin remains
a distinct claim.

The selected external review uses `devforge.utility-native-review/v1` with
`task_id`, `attempt_id`, `process_receipt_sha256`, the exact receipt `binding`,
`reviewer`, `outcome` (`PASS` or `FAIL`), `reason`, and nonempty pinned `evidence`.
Each evidence file must be outside worker/state/code/receipt roots. Review
acceptance binds an independently supplied judgment to raw process evidence;
it does not establish that the reviewer was correct.

C successors require intact reviewed C PASS. B PASS or FAIL can authorize A
when its other required observations are intact. A tier gate's PASS/FAIL must
cover every allocated attempt in that tier, with an ordered evidence pair of
the imported process receipt and selected review for each attempt. A worker
cannot substitute a JSON gate declaration for that coverage.

## Expiry, drift, and recovery

Process import and ungraded closure may record cleanup after the original
session deadline. They never accept late PASS/FAIL. Source drift blocks normal
progress; a cleanup replay uses the immutable original snapshots and records
the process result as unavailable/contaminated. It does not restore eligibility.
Unproven process termination is rejected and leaves the reservation unsettled.

The test suite covers deterministic failure boundaries, custody checks, and
successful C/B/A import/review transitions using manually constructed receipts
signed under an explicitly named, disposable host-owned test key. Those fixture
states are test machinery; they establish no actual native/model behavior.
It does not run an authenticated native campaign, obtain subscription login,
validate a real effective hook arrangement, or prove native semantic behavior.


## Opt-in v2 funding remains unavailable

G3 implements the reviewed v2 conditional selection scheduler, independently
of native funding. The v2 complete-allocation entrypoint fails before campaign
binding or reservation because the selected external grant/terminal-ledger,
already charged non-native completion and cross-session one-use authority
contracts are unresolved. The collector explicitly refuses v2 and unknown
native-plan versions; neither can enter its legacy v1 configuration path.
All legacy collector limits, continuation accounting, callback-origin checks,
one-use claims and settlement rules retain their existing meaning.

See the [utility consumer boundary](utility-runtime-contract.md#vpr-2-conditional-selection-and-consumer-boundary)
for the exact G1 contract gap and capability version. Neither synthetic
selection tests nor retained funding envelope keys establish a new grant,
clock start, non-native dispatch, native reservation or native observation.

### G8 supporting funding records and shared custody

The v2 allocation, funding, and Call envelopes above are unchanged. The external assignment's existing `authorization` object selects `funding` exactly as `{authority_ref, owner, claim_root}`. `authority_ref` equals the allocation funding Pin; owner equals the assignment owner and authority producer. This is the existing externally protected assignment boundary, not a new authentication method. A worker-supplied owner name or funding document has no authority.

The selected `devforge.funding-authority/v1` supporting record has exactly `schema_version`, `producer`, `task_id`, `validation_plan`, `grant_id`, `purpose`, `origin_utc`, `deadline_utc`, `clock_id`, `origin_monotonic_ns`, `deadline_monotonic_ns`, `prior_ledgers`, `max_total_attempts`, `max_seconds`, `per_attempt_max_seconds`, `claim_root`, `preparation`, `completed_calls`, and `call_records`. The grant and clock values equal the funding envelope; purpose is `native campaign ` followed by the exact task ID. The three allocation limits must fit the selected owner limits. `clock_id` is `linux-boot:` plus the exact Linux boot ID; a different boot or decreasing clock refuses admission. `preparation` and `completed_calls` are ordered Pins to actual charged terminal call records. Setup IDs are outside the reviewed graph; completed graph IDs are inside it. Both are unique and counted once. The admitted T04 review Pin must occur in its actual selected producer's completion evidence.

`devforge.funding-terminal/v1` has exactly `{schema_version, grant_id, producer, approved, charged, remaining, status, evidence}`. Producer equals the selected owner; counts are nonnegative integers, charged cannot exceed approved, remaining is zero, and status is EXHAUSTED or CLOSED. EXHAUSTED additionally requires charged equal approved; CLOSED means the owner retired unused authority. Evidence is nonempty Pins to actual terminal records. Prior IDs and document digests are unique; neither a prior grant ID nor a prior ledger digest can become new authority.

`devforge.funding-call/v1` has exactly `{schema_version, grant_id, call_id, producer, charged, status, started_utc, completed_utc, started_monotonic_ns, completed_monotonic_ns, evidence}`. Charged is true, status is COMPLETED, FAILED, or TIMED_OUT, and evidence is nonempty Pins to actual complete outputs. Producer matches the selected graph call, or the owner for setup outside the graph. Both observed intervals fit the original grant and per-call ceilings. Future-dated evidence cannot admit dependent work. Native receipt authenticity remains separate: an external call record cannot impersonate a native launch or grade.

`call_records` maps every frozen graph call ID to a distinct absolute completion-record destination inside `claim_root`. Externally hosted semantic producers publish immutable actual completion records there; the protected consumer checks exact producer, graph membership, clocks, output Pins and required dependency completion. A declared graph row is not a completion record. These supporting records do not supply a new model launcher, supported authentication lifecycle, or native transport. Unsupported externally selected dispatch methods remain unavailable.

The owner selects one shared host-private mode-0700 `claim_root` for the grant across all sessions. It must remain outside every worker-visible workspace, client state, selected mount, runtime, receipt and journal root. Grant claims are exclusively created with O_EXCL, O_NOFOLLOW, mode 0600 and file/directory fsync, at `grant-<sha256(grant_id UTF-8)>.json`. `devforge.funding-claim/v1` has exactly `{schema_version, grant_id, authority_ref, allocation_sha256, state_root, session_sha256}`; allocation digest uses the runtime canonical JSON encoding. Replay requires identical claim bytes. A second session, changed authority, or repeated bind cannot reclaim it. A crash after claim publication burns that claim; no automatic deletion, replay or refund is permitted.

The campaign starts at the selected funding origin, including already charged preparation and review. Reservation elapsed time uses the greater original UTC or monotonic elapsed observation. Protected journal operations retain monotonic observations and refuse decreasing values; dispatch and semantic acceptance use the earliest session, grant, campaign and unit cutoff. Cleanup remains possible after expiry without accepting a late quality result. Failed or cancelled allocated launches remain charged. Collector preparation requires the validated protected funding context and exact origin, and the actual collector must observe the original monotonic clock throughout process collection. In this partial G8 candidate, v2 funded launch remains explicitly refused before collector authority or process creation because complete later semantic-call accounting is unresolved. Preparation is implemented; funded collection is not admitted. Continuation answer-policy units must equal the ordered graph continuation IDs for that parent; they do not create uncounted allowance.

These are supporting encoding selections and mechanical implementation requirements. Synthetic fixtures are not funding for a real native campaign and establish no model observation, qualification, owner acceptance or operational readiness. Complete lifecycle/semantic-accounting coverage and actual execution evidence must be reported separately; a passing binding fixture alone is not completion of VPI-13.
