# Utility runtime contract

Implementation target: separate skill-builder and skill-validator adapters, preserving the brainstorm contract. Mechanical checks do not certify semantic quality, reviewer honesty, human adoption, native callback origin or release acceptance.

A `devforge.utility-session/v1` session selects a `devforge.utility-delivery/v1` contract, external assignment, installed resources, deadline, correction limit, output preimages, checkpoint and receipt destinations. The delivery contract has explicit ordered phases and task classifications, allowed outputs and typed evidence requirements. Known utility phase order is fixed; the owner explicitly binds classification and applicability. Validator W1/P1-P6/T01-T12 are Enforced and cannot be excluded to obtain a passing result.

Every checkpoint binds task, phase, journal sequence, fresh challenge and selected input digest. The model supplies concrete evidence and references. The runtime reads the selected actual files, checks required format/fields and preserves their bytes in its protected journal. Marker-only submissions, wrong phase/task, stale/replayed nonce, wrong producer or missing evidence cannot advance. Accepted artifacts are immutable for the task. A changed output requires a new affected assignment rather than rewriting an accepted phase.

Finite-choice waiting may use an exact answer rule supplied in the selected contract; unrelated text leaves WAITING_USER. Free-text consequential answers require a separately recorded authoritative interpretation bound to the exact pending question and observed prompt; a model declaration is insufficient. Reopening context, synthetic continuation prompts, elapsed time and arbitrary user-message arrival never mean adoption. Deadlines and correction counts do not reset on waiting/resume.

At READY, runtime verifies all accepted outputs, publishes the receipt exclusively, reads its complete bytes back and rechecks current targets before reporting completion. The handoff excludes its own digest and is not rewritten. Receipt, callback transport, client completion, renderer observation and receiving invocation are independent facts. Receipt collisions and changed final bytes prevent a current completion claim. Failure reports can be prepared even when passing evidence is unavailable.

Validator contracts must select external `deterministic-inspection` (P2), `independent-review` (P3), and `native-prerequisites`, `native-C`, `native-B`, `native-A` (P4) gate files. Omitting or moving these gates rejects admission. Each gate binds its allocated producer, task, phase, contract digest, outcome, reason and pinned underlying evidence. The receipt retains each outcome separately. A producer pin proves which allocated bytes were consumed; it does not prove the producer's judgment or execution authenticity. Required structured fields reject null, blank strings and empty collections; scalar truth values retain their domain meaning.

The supervisor protects runtime code/state, fixed sources and receipt paths from the worker. Its existing synthetic process interface exercises the utility adapter without pretending native execution. A native launcher must independently establish the selected client, authentication, effective hook configuration and one callback per occurrence. Installation/compatibility alone is not admission.

`native-admission --attempt <id>` retains the legacy nonlaunching C-only reservation path. It requires active validator P4 and the selected prerequisite evidence and cannot mix with a bound schedule. Native process execution instead uses the separate protected scheduling and lifecycle interface below. A reservation alone is never execution evidence. The selected client binary must remain outside every attempt-writable root; imported execution and independent quality review have distinct authority.

This is an incomplete integration revision. Native callback coverage, actual builder-to-validator invocation and delivery remain unobserved. For this continuation the user explicitly selected all five builder phases and their required actions as Enforced. Actual managed assignments must bind that decision; the generic adapter does not adopt a test fixture's classifications for unrelated work.

Official source inspected on 2026-09-07: https://learn.chatgpt.com/docs/hooks. The selected synchronous callback interface uses SessionStart, UserPromptSubmit (prompt field), Stop and SessionEnd; matching sources accumulate, trust is definition-specific and tool hooks are not a complete sandbox. The runtime is the authority boundary; hooks transport observations and feedback. Codex CLI observed: 0.153.4. Live native support remains to be measured on a separately frozen allocation.


## Client-independent scheduling checkpoint

The Python module `runtime/delivery/native_schedule.py` supplies an in-memory scheduling and budget policy component for a previously validated frozen experiment. Its supplemental `required_predecessors` map is an explicit caller-owned input, not a new accepted field in `devforge.utility-native-plan/v1`. The protected caller must establish that this frozen map covers the actual case requirements; an empty list cannot self-exclude required coverage.

The component preserves declared C/B/A order, independent attempt identities and explicit prerequisite relationships. It reserves each attempt once, keeps at most one reservation in flight, consumes an allocation even when launch fails, and bounds each attempt by its own limit and the experiment's original elapsed-time budget. Intact B quality FAIL observations may satisfy a declared observation dependency; required C dependencies need intact PASS. Missing or contaminated predecessor observations block dependent attempts while declared independent work may continue.

Elapsed time is supplied from one authoritative experiment origin including waiting/resume. An expired reservation returns a stop-required decision and remains in flight until its protected caller stops/reaps the owned process and records a terminal observation. The component does not stop processes itself. It rejects late quality grades under its recording-time policy; selecting authenticated capture-time semantics belongs to result-import integration.

The pure component alone supplies no persistent custody or authenticated result importer. The following journal adapter now owns its state. Neither immutable Python values nor a completed allocation establishes native execution or evaluation PASS.

## Protected scheduling interface

`devforge.utility-native-schedule/v1` is a separate frozen document with exactly `schema_version`, `task_id`, `plan` (absolute path and SHA-256), and `required_predecessors`. The plan pin must match the protected native-prerequisites producer's selected native-plan/v1 bytes. The map has one entry per allocated attempt. This version requires every B attempt to depend on all C attempts in the same arm/repetition, and every A attempt to depend on all C and B attempts in that scope. Missing preceding-tier coverage, extra/missing dependencies, wrong scope or a substituted plan rejects binding. This conservative version supports no per-case dependency exemptions.

Before committing a schedule binding, the controller validates the complete independent cases/allocation oracle, exact runtime attempt coverage, and the total cap including preparation calls. It snapshots the allocation and runtime configuration alongside the cases and plan, and retains the allocation digest in reconstructed state. Legacy, missing, mismatched, or over-cap coverage cannot bind or reserve, and rejected binding preflight consumes no attempt or campaign clock. Reservation requires successful replay of that complete binding and unchanged current source bytes. Cleanup uses the frozen allocation snapshots and original clock; it grants no renewed eligibility.

The operator interface is:

```text
devforge delivery --state <protected-state> native-schedule-bind --schedule <external-binding.json>
devforge delivery --state <protected-state> native-schedule-reserve
devforge delivery --state <protected-state> native-schedule-cancel --attempt <id> --reason <reason>
```

These operations are external controller actions, not worker fallbacks. Binding requires active validator P4 after passing independent P2/P3 gates and complete native prerequisites. It is exclusive, pins actual bytes in the locked journal and records its original clock from the operator's transition timestamp. Its entire campaign budget must fit within the enclosing session deadline. Every reload replays the plan-bound transitions; callers cannot submit replacement scheduler state, elapsed time, grades or termination claims. Legacy native-admission and scheduled reservations cannot mix.

The reserve operation consumes one eligible allocation and issues no launch permission. Repeated calls while an attempt is in flight retain that attempt and its original deadline. Expiry marks stop-required without silently settling it. P4 cannot advance with an unsettled reservation. Cancellation of an **unlaunched** reservation remains a fixed CANCELLED/UNOBTAINABLE observation. Claimed process reservations require authenticated lifecycle settlement; cancellation cannot stand in for proven termination. Such cancellation may be recorded after session expiry; it cannot reopen the session or authorize further work. Clock rollback before any journal write is rejected. Current dependency, evidence and source drift remain fail-closed.

The scheduling modules and process collector are embedded in the Rust delivery package and routed through the protected controller. The older generic supervisor entrypoint retains its refusal of arbitrary native commands. The separate native lifecycle accepts only an exact pinned runtime configuration and complete call allocation; it does not reuse the synthetic launch path.

## Native process custody and remaining scope

The [native lifecycle contract](native-lifecycle-contract.md) defines the external `native-process-launch`, `native-process-import`, `native-result-review` and `native-result-close` operations. Each process launch has a durable one-use claim tied to the existing reservation and original clock. Collector receipts use protected authentication and bind complete raw streams, process termination and input freshness; a selected independent review remains necessary for quality grades. Native tier gates require the corresponding imported observations and review evidence for every allocated attempt. Missing evidence continues to support honest P5/P6 reporting, without a passing evaluation claim.

The collector uses a separate explicit filesystem view and a selected managed worker broker when required. It does not infer effective hook, authentication, tool or credential isolation from configuration text. Those observations are mandatory before launch. Callback origin, rendered delivery and actual receiving invocation remain separate claims.

This interface currently admits only supported single-turn requests. Required Q&A and later-answer cases need an additional controlled interaction transport and counted allocation; unsupported interaction fails preflight. No actual native campaign has run on this implementation, and deterministic fixture receipts cannot substitute for one. The approved 24-attempt cap does not fit the frozen required coverage, so no campaign clock has been bound. See [the integration requirements](native-integration-requirements-20260908.md) for the unfulfilled prerequisites and completion criteria.

## Opt-in VPR-2 policy records

Canonical schema source: DevForgeAI G1 commit
`75bcba915fd1d5f88477318d4b27db6e6961ca81`,
`docs/mvp/execution-contract.md`, VPR-2 record contract. This implementation
adds validator-only `devforge.utility-delivery/v2` and typed v2 gates/results;
all v1 session/checkpoint/journal/receipt fields and legacy semantics remain.
The existing assignment's JSON authorization payload selects the exact
`validation_policy` object and `selection_reviewer`; the assignment owner must
match the plan owner. Its author cannot be its selection reviewer. No new
approval artifact type or helper-issued acceptance is introduced.

`validation_policy.load(selection, assignment_raw, project, frozen=None)`
returns a protected Policy with the exact plan, catalog/assertion/task/observation
and complete typed call maps. Its `fixed()` list supplies immutable snapshot
sources. Policy, acceptance, scope, lineage, diffs, original catalogs and selected
identity bytes must be external, current and pinned. Routine retains the owner's
qualified or explicitly unqualified cumulative anchor independently of current
Routine acceptance. Original assertion projection completeness and semantic
sufficiency require actual independently selected T04 review; a digest does not
prove those judgments. The existing JSON owner acceptance payload carries its
candidate_identity, accepted_scope_ref and lineage when continuing a Routine
chain; no result reducer writes a successor owner record.

At P3, the exact actual `skill-ai-review/v2` binds the frozen plan and delivery
without a circular plan-to-review pin, and retains R01–R10. At P4, a reviewed
NOT_SELECTED native gate keeps outcome NOT_RUN and disposition
SATISFIED_BY_REVIEWED_SELECTION. Its exact plan/review evidence permits reporting;
it does not create a native plan, reservation or PASS. Wrong producer, stale
review, changed selection and invalid v2 admission leave the protected HEAD
unchanged. Legacy bounded correction behavior remains on the v1 path.

`utility_evidence.validation_results` and `validation_decision` call the protected
reducers with the selected Policy, actual review, exact delivery pin and the
runtime's authenticated native import mapping. Results retain one judgment per
original assertion. An intact baseline FAIL under expectation=observation can
complete a comparison; a required candidate FAIL still produces FAIL. Complete
honest reporting with unavailable observations remains INSUFFICIENT_EVIDENCE.
Unselected native groups remain NOT_RUN. The reducer does not update qualification
or grant owner acceptance. Actual receiving evidence is distinct from T12's
prepared handoff. Evidence consumed in final reduction is snapshotted and checked
again during replay and receipt publication.

The G2 native-plan bridge accepts the explicit v2 validation_plan binding and
native projection. V2 complete-allocation validation delegates to
`utility_schedule.validate_allocation_v2(plan, frozen=None)`, which must return
`(allocation, snapshot_sources)` using the existing `(kind, absolute_path, raw)`
source tuple format. G3 supplies conditional selection validation, but its
allocation entrypoint explicitly refuses v2 funding authority admission pending
the external grant/terminal-ledger and charged non-native completion contract
below. No v2 campaign can bind, reserve or launch. G6 owns installed/exported
capability and helper-policy pins.
Neither this bridge nor synthetic development tests establish native activation,
qualification, receiving execution, installation readiness or human acceptance.

The companion frozen helper discriminator is independently selectable:

```text
VPI_VALIDATOR_PACKAGE=/absolute/selected/skill-validator PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests python3 -m unittest test_validation_policy.HelperDiscriminator -v
```

It requires an honest unattempted v2 input to emit a v2 decision with NOT_RUN,
INSUFFICIENT_EVIDENCE, COMPLETE reporting, no adoption/acceptance and native groups
NOT_RUN. Run it only as the allocated external runtime/operator role. An unchanged
v1 helper is expected to fail that assertion until the G4 canonical update.


## VPR-2 conditional selection and consumer boundary

`utility_schedule.validate` accepts an explicit v2 schedule only with the
already externally authorized `Policy`, actual admitted T04 review pin and
selected delivery pin. It checks the exact v2 native-plan/runtime/allocation
pin chain, the complete reviewed call graph's native projection, original
assertion/observation membership and prerequisite graph edges. A reviewed
Routine T07 NOT_SELECTED disposition permits C then A; every selected matching C
must have intact PASS. Selected B predecessors still require intact PASS/FAIL.
Cross-arm/repetition substitutions, missing C, stale review/plan, omitted native
obligations and post-hoc exclusions reject selection. Kernel reservation
accounting still charges failed launches and rejects replayed/late grades.
This selection-only API does not authenticate a grant or issue launch authority.

The real utility router recognizes v2 delivery. Managed-worker profile, result
and state collision checks include nested policy pins and selected graph review
destinations. The synthetic supervisor preserves the utility boundary that keeps
external gate and policy evidence out of worker mounts. The native prerequisite
consumer identifies the one explicitly selected native plan and separately
retains the full policy/review snapshot inventory. No-native Routine reporting
continues without a native plan or campaign.

Capabilities now use explicit `devforge.delivery-capabilities/v2`, retaining the
existing capability values and adding `supported_utility_native_schedule_schemas`
(v1 and v2), `validation_policy` (VPR-2), and `native_funding_v2`
(BLOCKED_EXTERNAL_GRANT_LEDGER_CONTRACT). `native_execution_enabled` remains
false. G6 must reconcile the strict installer capability consumer with this
explicit version; this source change does not authorize installation.

The remaining funding gap is an implementation contract selection, not a
relaxation of G1. G1 execution-contract lines 211-219 require actual distinct
owner authority, earlier terminal-ledger references, evidence for charged setup
and non-native graph completions, exclusive grant use, and both original clocks.
It freezes allocation/funding/Call envelope keys, but does not select the
machine-verifiable payload and producer-binding contract for those evidence
records, their completion locators, or an externally protected shared grant
claim/replay authority across sessions. A local scheduler counter cannot prove
that a grant is unused elsewhere or that a static reviewer completed. G3 leaves
that admission path blocked instead of creating fields or treating graph rows
as completed calls. After those external contracts are selected, the same
validated grant and graph must reach reservation/replay, collector, managed
consumers and both clocks. The v1 24/600/14400 ceilings remain unchanged.

### G8 supporting funding records and shared custody

The v2 allocation, funding, and Call envelopes above are unchanged. The external assignment's existing `authorization` object selects `funding` exactly as `{authority_ref, owner, claim_root}`. `authority_ref` equals the allocation funding Pin; owner equals the assignment owner and authority producer. This is the existing externally protected assignment boundary, not a new authentication method. A worker-supplied owner name or funding document has no authority.

The selected `devforge.funding-authority/v1` supporting record has exactly `schema_version`, `producer`, `task_id`, `validation_plan`, `grant_id`, `purpose`, `origin_utc`, `deadline_utc`, `clock_id`, `origin_monotonic_ns`, `deadline_monotonic_ns`, `prior_ledgers`, `max_total_attempts`, `max_seconds`, `per_attempt_max_seconds`, `claim_root`, `preparation`, `completed_calls`, and `call_records`. The grant and clock values equal the funding envelope; purpose is `native campaign ` followed by the exact task ID. The three allocation limits must fit the selected owner limits. `clock_id` is `linux-boot:` plus the exact Linux boot ID; a different boot or decreasing clock refuses admission. `preparation` and `completed_calls` are ordered Pins to actual charged terminal call records. Setup IDs are outside the reviewed graph; completed graph IDs are inside it. Both are unique and counted once. The admitted T04 review Pin must occur in its actual selected producer's completion evidence.

`devforge.funding-terminal/v1` has exactly `{schema_version, grant_id, producer, approved, charged, remaining, status, evidence}`. Producer equals the selected owner; counts are nonnegative integers, charged cannot exceed approved, remaining is zero, and status is EXHAUSTED or CLOSED. EXHAUSTED additionally requires charged equal approved; CLOSED means the owner retired unused authority. Evidence is nonempty Pins to actual terminal records. Prior IDs and document digests are unique; neither a prior grant ID nor a prior ledger digest can become new authority.

`devforge.funding-call/v1` has exactly `{schema_version, grant_id, call_id, producer, charged, status, started_utc, completed_utc, started_monotonic_ns, completed_monotonic_ns, evidence}`. Charged is true, status is COMPLETED, FAILED, or TIMED_OUT, and evidence is nonempty Pins to actual complete outputs. Producer matches the selected graph call, or the owner for setup outside the graph. Both observed intervals fit the original grant and per-call ceilings. Future-dated evidence cannot admit dependent work. Native receipt authenticity remains separate: an external call record cannot impersonate a native launch or grade.

`call_records` maps every frozen graph call ID to a distinct absolute completion-record destination inside `claim_root`. Externally hosted semantic producers publish immutable actual completion records there; the protected consumer checks exact producer, graph membership, clocks, output Pins and required dependency completion. A declared graph row is not a completion record. These supporting records do not supply a new model launcher, supported authentication lifecycle, or native transport. Unsupported externally selected dispatch methods remain unavailable.

The owner selects one shared host-private mode-0700 `claim_root` for the grant across all sessions. It must remain outside every worker-visible workspace, client state, selected mount, runtime, receipt and journal root. Grant claims are exclusively created with O_EXCL, O_NOFOLLOW, mode 0600 and file/directory fsync, at `grant-<sha256(grant_id UTF-8)>.json`. `devforge.funding-claim/v1` has exactly `{schema_version, grant_id, authority_ref, allocation_sha256, state_root, session_sha256}`; allocation digest uses the runtime canonical JSON encoding. Replay requires identical claim bytes. A second session, changed authority, or repeated bind cannot reclaim it. A crash after claim publication burns that claim; no automatic deletion, replay or refund is permitted.

The campaign starts at the selected funding origin, including already charged preparation and review. Reservation elapsed time uses the greater original UTC or monotonic elapsed observation. Protected journal operations retain monotonic observations and refuse decreasing values; dispatch and semantic acceptance use the earliest session, grant, campaign and unit cutoff. Cleanup remains possible after expiry without accepting a late quality result. Failed or cancelled allocated launches remain charged. Collector preparation requires the validated protected funding context and exact origin, and the actual collector must observe the original monotonic clock throughout process collection. In this partial G8 candidate, v2 funded launch remains explicitly refused before collector authority or process creation because complete later semantic-call accounting is unresolved. Preparation is implemented; funded collection is not admitted. Continuation answer-policy units must equal the ordered graph continuation IDs for that parent; they do not create uncounted allowance.

These are supporting encoding selections and mechanical implementation requirements. Synthetic fixtures are not funding for a real native campaign and establish no model observation, qualification, owner acceptance or operational readiness. Complete lifecycle/semantic-accounting coverage and actual execution evidence must be reported separately; a passing binding fixture alone is not completion of VPI-13.
