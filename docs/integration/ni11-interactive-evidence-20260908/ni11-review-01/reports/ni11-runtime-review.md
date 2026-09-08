# NI11 runtime review 01

## Bound input and method

Read-only static review of the manifest-selected candidate against baseline
`423c69aafe4bf98601e67a6c94e333b26857f310`.  The manifest SHA-256 is
`0d64ade248d26c9bcfc238179e21f03df9a15011c9a64e05f716dfa3cdf52e82`.
All 63 listed baseline/candidate inputs verified their stated SHA-256 values.
The six changed candidate paths are the two native contracts,
`native_process.py`, `utility_evidence.py`, `utility_state.py`, and the new
`test_native_interactive.py`.

No source mutation, test execution, client launch, authentication, native
evaluation, or model call was performed in this review.

## Findings

### F-01 — P1: managed interactive turn continuations are rejected after they run

`native_process.Interactive` deliberately permits an answer-policy `turn` step:
after a completed turn it reserves that unit and sends another `turn/start`
([native_process.py:620](../candidate/runtime/delivery/native_process.py#L620),
[native_process.py:624](../candidate/runtime/delivery/native_process.py#L624)).
The same collector's managed completion check, however, requires exactly one
`UserPromptSubmit` callback ([native_process.py:988](../candidate/runtime/delivery/native_process.py#L988)).
The broker treats `UserPromptSubmit` as a live phase transition and permits it
while waiting for a user ([supervisor.py:243](../candidate/runtime/delivery/supervisor.py#L243)).

Reproduction: select an `awaiting-user` call with `managed_worker_required:
true` and a frozen policy containing a `turn` continuation. Deliver the normal
managed callback sequence with a `UserPromptSubmit` for the initial prompt and
another for the continuation. On collection close, the recorded names contain
two `UserPromptSubmit` entries. The `names.count(...) != 1` predicate forces
the managed result to `COULD_NOT_RUN`, so the signed process receipt cannot be
eligible even if each bounded continuation, cleanup, and transcript replay
succeeds.

Exposure: the advertised multi-turn path cannot complete for managed cases.
This contradicts the candidate's continuation support and the review scope's
requirement to account for managed phases with multiple user-prompt events.

Required correction: bind the permitted `UserPromptSubmit` chronology/count to
the frozen interactive policy and the actual managed phase contract, then retain
the terminal ordering/quiescence checks. Do not replace it with an unbounded
allowlist or infer human authority from callbacks.

### F-02 — P2: the changed cap, replay, and managed-continuation paths lack discriminating coverage

The sole new test module is explicitly an unsigned deterministic fixture
([test_native_interactive.py:1](../candidate/tests/test_native_interactive.py#L1)).
It checks one answer request and a zero-step fake server
([test_native_interactive.py:10](../candidate/tests/test_native_interactive.py#L10),
[test_native_interactive.py:36](../candidate/tests/test_native_interactive.py#L36)).
No candidate test references `continuation_units`, `awaiting-user`, or
`answer_policy` outside that module. The changed accounting is in
`utility_evidence.launch_allocation` ([utility_evidence.py:196](../candidate/runtime/delivery/utility_evidence.py#L196),
[utility_evidence.py:225](../candidate/runtime/delivery/utility_evidence.py#L225)); receipt replay is in
`native_process.observe_interactive` ([native_process.py:1181](../candidate/runtime/delivery/native_process.py#L1181));
and process eligibility now admits owned SIGTERM/SIGKILL after protocol
completion ([utility_state.py:1196](../candidate/runtime/delivery/utility_state.py#L1196)).

Add synthetic, non-native tests for: (1) full 24-call accounting with
preparation and continuation units, including exact-cap and one-over-cap;
(2) policy/allocation unit mismatch before a launch claim; (3) transcript and
claim replay rejection for reordered, duplicated, missing, or unclaimed
continuations; (4) a managed policy with two `UserPromptSubmit` events; and
(5) refusal to make an interactive SIGTERM/SIGKILL receipt grade-eligible when
any protocol, transcript, claim, or owned-cleanup predicate is absent.

## Static observations that are not native evidence

The candidate has fail-closed request correlation, frozen answer equality,
durable pre-send unit files, original-deadline checks, transcript replay, and
owned group teardown in the selected source. The local frozen protocol schema
supports `thread/start`, `turn/start`, and `item/tool/requestUserInput`; the
adapter's basic message shape matches those schema requirements. The cap logic
also adds declared continuation-unit counts before accepting the allocation.

These are static design observations only. They do not establish app-server
behavior with Codex 0.153.4 and `gpt-6-astra`, authenticated user-input
availability, effective isolation/authentication controls, callback origin,
actual managed chronology, cleanup against the real client, semantic grading,
or native admission. No claim of those observations is made here.
