# NI11 runtime review 03

## Custody and scope

The corrected authority SHA-256 for the reviewed manifest is
`2de108deba34ac9be0d28f2a594017781e8cbd33704f2c86859d7aa099e1dc92`.
All 64 listed inputs (32 baseline and 32 candidate) matched their manifest
hashes. Six selected paths differ: the native-process contract,
`native_process.py`, `supervisor.py`, and three test modules. The current
integration checkout is at `62e9329e2aa69cf3ae43f269ad3620d1fca2095f`, but
this review uses only the pinned snapshot inputs.

The earlier `ni11-review-02` custody-blocked report remains correct for the
authority string supplied at that time; this report uses the corrected,
separately allocated authority.

No source mutation, test execution, client launch, authentication, native
evaluation, or model call was performed.

## Result

No new actionable static defect found. The previous findings are closed at the
source-and-fixture level only.

### F-01 closure: managed multi-prompt correlation

The adapter records only fully delivered `turn/start` inputs
([native_process.py:574](../candidate/runtime/delivery/native_process.py#L574),
[native_process.py:751](../candidate/runtime/delivery/native_process.py#L751)).
For an interactive managed call, the broker retains that live list
([native_process.py:1082](../candidate/runtime/delivery/native_process.py#L1082)).
Managed completion now requires an exact `UserPromptSubmit` count, exact prompt
hashes, a `Stop` after each prompt, and the `WAITING_USER -> ACTIVE` transition
at the unchanged phase for continuations
([native_process.py:1001](../candidate/runtime/delivery/native_process.py#L1001),
[native_process.py:1021](../candidate/runtime/delivery/native_process.py#L1021)).
The broker persists the required before-state and prompt digest without
elevating callback origin ([supervisor.py:275](../candidate/runtime/delivery/supervisor.py#L275)).

The synthetic multi-phase fixture covers the accepted two-prompt path, a hash
mismatch, and an extra prompt without the required waiting transition
([test_native_process.py:501](../candidate/tests/test_native_process.py#L501),
[test_native_process.py:535](../candidate/tests/test_native_process.py#L535)).

### F-02 closure: accounting, replay, durable-send, and teardown coverage

The changed fixtures cover exact-cap versus over-cap continuation slots before
binding ([test_utility_native_lifecycle.py:218](../candidate/tests/test_utility_native_lifecycle.py#L218));
exact policy/allocation units and duplicate units before preparation
([test_native_process.py:372](../candidate/tests/test_native_process.py#L372));
durable reservation failure before any generation message
([test_native_interactive.py:62](../candidate/tests/test_native_interactive.py#L62));
transcript/claim replay tampering
([test_native_interactive.py:71](../candidate/tests/test_native_interactive.py#L71));
and signal-exit eligibility and incomplete-protocol cleanup paths
([test_native_interactive.py:118](../candidate/tests/test_native_interactive.py#L118),
[test_native_interactive.py:136](../candidate/tests/test_native_interactive.py#L136)).

Static review found the implementation consistent with those cases: unit
reservation precedes a `turn/start` message; the collector records a prompt only
after its complete stdin write; interactive replay requires every durable claim
to match the reconstructed client message; and signal exit remains conditional
on protocol completion, owned cleanup, complete streams, no overflow, and intact
inputs.

## Remaining evidence boundary

This is not evidence of native behavior. Unobserved: real Codex 0.153.4
app-server serialization and ordering, `gpt-6-astra` user-input availability,
authenticated operation, effective isolation, live callback chronology/origin,
real-client process cleanup, semantic grading, and runtime admission.
