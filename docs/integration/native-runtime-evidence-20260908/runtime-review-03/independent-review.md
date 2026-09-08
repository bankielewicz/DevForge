# Independent native-runtime review

**Disposition:** Changes required.

I reviewed only the supplied frozen packet against `docs/integration/native-integration-requirements-20260908.md` (NI-01 through NI-12). The review was read-only: no candidate code, tests, native client, model, authentication flow, filesystem probe, or hook was run. I did not read author conclusions, other reviews, private authorization roots, ordinary credentials, source-skill history, or unrelated repository content.

The supplied manifest was verified before and after review. Its SHA-256 remained `1d6d5d8db70c2ab0ff16141e135848c3676ac54e8c595d101d5e1ed5a3eb8cfc`; all 198 declared byte hashes matched (96 baseline files, 102 candidate files). Candidate modes are present in the manifest; baseline modes are not represented.

## Finding R03-001 — HIGH

**Incomplete or over-cap coverage can be bound and reserved before the 24-call allocation gate runs.**

The schedule-binding path calls `_native_prerequisites` and then creates the schedule ([utility_state.py](candidate/runtime/delivery/utility_state.py):1124), while reservation immediately consumes its next attempt ([utility_state.py](candidate/runtime/delivery/utility_state.py):1140). That prerequisite path validates the legacy plan and prerequisite gate but does not call `launch_allocation` ([utility_state.py](candidate/runtime/delivery/utility_state.py):375). The complete independent call inventory, 24-call cap, and runtime-configuration checks live in `launch_allocation` ([utility_evidence.py](candidate/runtime/delivery/utility_evidence.py):165) and are first invoked only by `native_process_launch`, after reservation ([utility_state.py](candidate/runtime/delivery/utility_state.py):1212).

A reproducing fixture needs only a native-plan/v1 that passes the legacy plan/prerequisite validation while omitting the supplemental runtime configuration, or whose supplemental allocation is over cap. `native-schedule-bind` and `native-schedule-reserve` can commit their transitions; rejection arrives only at `native-process-launch`. The new lifecycle test intentionally encodes that sequence by reserving first and expecting the legacy-plan failure at launch ([test_utility_native_lifecycle.py](candidate/tests/test_utility_native_lifecycle.py):171).

This violates NI-01's requirement that incomplete coverage and allocations over 24 prevent campaign admission. It also undermines NI-12 ordering because an invalid campaign can acquire a durable in-flight reservation before its coverage oracle is accepted.

Validate `launch_allocation` during `native-schedule-bind`, retain/replay its runtime and allocation snapshots in the schedule identity, and make reserve unavailable unless that complete validation succeeded. Add tests that legacy, missing, and over-cap allocations cannot bind or reserve.

## Requirement assessment

| Requirement | Assessment |
| --- | --- |
| NI-01 | Not met: cap and complete-coverage checks occur too late. |
| NI-02 | Structural selection is encoded; no client/auth observation was performed. |
| NI-03 | Fresh workspace and sign-in behavior remains unobserved. |
| NI-04 | Boundary construction is present; actual permitted/denied probes remain unobserved. |
| NI-05 to NI-08 | Structural controls exist; no native launch, cleanup, or authenticated receipt observation was performed. |
| NI-09 | Process receipt and review are separated structurally; no grader/delivery observation exists. |
| NI-10 | Broker lifecycle is structural only; effective hooks and callback origin remain unobserved. |
| NI-11 | Explicit open prerequisite: awaiting-user interaction is refused. |
| NI-12 | Gate wiring is structural, but NI-01 leaves invalid coverage admissible before launch. |

The newly added native process and lifecycle tests were reviewed as deterministic fixture coverage only. They do not establish native execution. There is no test that requires schedule binding or reservation to reject a legacy, incomplete, or over-cap allocation.

This review has no merge or acceptance authority.
