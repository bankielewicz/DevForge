# Native utility integration requirements

Authority: the user's 2026-09-07 continuation approval, following the recorded
CLI/model/authentication proposal. These requirements define the remaining
implementation and its independent review. They are not a completed native
experiment allocation, observed readiness, or permission to exceed the budget.

The preserved runtime base is `cd403cfee0b120ada2f9e198d3a15db5d4147252`.
The implementation continues on the allocated worktree and branch; later
commits do not replace that base. Original builder and validator packages remain
frozen from DevForgeAI `a78b9bff83c090153952b31b84b0584ef1650f02`.

| ID | Required property | Discriminating evidence |
| --- | --- | --- |
| NI-01 | A complete frozen coverage allocation includes every required case/arm/repetition, control, model probe, grader and receiving execution. All launched or failed model invocations consume the approved cap. | A missing required execution or an allocation exceeding 24 prevents campaign admission. No silent case removal, free replacement, or auxiliary execution. |
| NI-02 | Use the approved CLI 0.153.4, exact selected executable hash, `gpt-6-astra`, medium reasoning and direct ChatGPT subscription authentication. | Changed client/config/model, inherited credentials, unknown authentication, or an arbitrary command substitution prevents launch. No model probe is free. |
| NI-03 | Each independent attempt has a distinct, unused workspace and client state. Stage one fresh sign-in first and request additional authorizations only as needed. | Reuse, overlap, occupied destinations, source/eval visibility or inherited normal client state prevents launch. Worktree creation and sign-in remain separate observations. |
| NI-04 | Enforce an explicit filesystem and tool boundary. Protect authority, source, expected answers, history and credentials; allow only the assigned outputs and required resources. | Probe permitted reads/writes and denied reads/writes through the actual selected boundary. Mask an outer worktree's source-bearing shared Git pointer; inner fixture Git authority must be explicit. A host-root read-only mount is insufficient source exclusion. |
| NI-05 | Reserve once before launch and retain a durable launch claim before creating the process. One owned process tree may run at a time. | Replayed claims, concurrent launch, restart, resume or a crash cannot relaunch a reservation, refill its cap or reset its clock. Ambiguous prior process ownership prevents another launch. |
| NI-06 | Preserve the original four-hour campaign deadline and ten-minute attempt limit, including pauses. | Clock rollback, late quality outcomes, an expired campaign and an unsettled process prevent progress. Cleanup may settle accounting after expiry, but cannot reopen the workflow. |
| NI-07 | Capture complete bounded stdout/stderr and process lifecycle outside worker writes; stop and reap only the owned process tree. | Timeout, truncated streams, premature EOF, surviving descendants, source drift and collection errors retain their actual incomplete or contaminated outcomes. Exit zero alone cannot produce a native PASS. |
| NI-08 | Authenticate collector receipts against protected per-assignment custody and exact plan/reservation/launch identities. | Unsigned, tampered, replayed, cross-task, wrong-attempt, changed-log and synthetic receipts cannot satisfy native execution. Do not export signing secrets or let a worker select accepted authority files. |
| NI-09 | Keep process authenticity separate from semantic grading, callback origin, instruction consultation and rendered delivery. | A signed process receipt authenticates only its collector claims. A grader needs exact raw evidence and its own allocated producer. Text saying PASS, a keyword, or a claimed callback is insufficient. |
| NI-10 | Managed workers use the selected utility session and protected callback broker for phase context, questions, correction and receipts. | Missing/disabled/duplicate/failed effective hooks, wrong session, transport failure or missing final-byte observation cannot establish managed completion. A generic CLI collector is insufficient by itself. |
| NI-11 | Required interactive cases need an owned answer transport bound to the frozen operator answer policy. | A single-turn invocation cannot silently stand in for Q&A or resume itself. Additional model invocations require explicit counted allocation. Unsupported interaction must remain an explicit prerequisite gap. |
| NI-12 | Import only authentic, current observations into C/B/A ordering and tier gates. Preserve reporting on failures. | Missing C observation blocks dependent work; intact B failure may remain useful observed evidence. P5/P6 reporting with unavailable native outcomes does not imply suitability, acceptance, or an actual receiving transfer. |

The existing builder phase-group decision and validator W1/P1-P6/T01-T12
Enforced classifications remain settled. The recorded validator reservation
release permits bounded alignment against a completed reviewed interface. Later
stale ownership text produced by the integration session does not create a new
reservation. Preserve all original source and evidence when correcting that text.

The baseline-reproduced fixture demo incompatibility is resolved separately by
exact tooling-file pins. The policies retain byte, mode and source-layout checks;
no acceptance expectation is weakened. Record complete required Rust/Python
checks and exercise the demo after the final implementation changes.

Native evaluation cannot begin while complete coverage does not fit the approved
cap. Deterministic tests, a signature, an installed package or a prepared handoff
cannot replace unavailable native observations. Report any remaining unsupported
interaction, isolation, delivery or ownership prerequisite explicitly.
