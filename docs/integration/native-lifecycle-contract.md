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
