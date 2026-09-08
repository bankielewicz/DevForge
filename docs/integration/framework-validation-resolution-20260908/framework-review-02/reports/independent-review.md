# Independent successor maintenance review

## Scope and custody

- Review type: read-only consequential maintenance review.
- Baseline: `framework-review-01/candidate`.
- Candidate: existing successor maintenance bytes; no new candidate authorship claimed.
- Source reference inspected read-only: DevForgeAI commit `9970e92db7d824dea0c49e1a61a122480f2aa8b5`.
- Manifest SHA-256: `3cfaf2dee9aa7bdba0557e1d12e6e2e477dbeea998307e5d6fae30cfc0fe9463`.
- All six manifest pins matched before review and immediately before this report was written.

## Finding

### P2 — Case requirement IDs are not tied to the declared refinement requirements

The optional `workspace_allocation_refinement` declares a unique requirement-ID set at `candidate/scripts/validate_framework.py:56-65`. Cases independently accept any nonempty, unique text list at lines 147-150. The validator never checks that a case ID belongs to the refinement’s `requirement_ids` when that refinement is present.

Reproduction, without running it: retain the current refinement IDs `SV-WA-01` through `SV-WA-06`, then replace a case's `requirement_ids` with `["SV-WA-999"]`. Both lists are well-typed and internally unique, so the candidate accepts the ungrounded case reference. The committed self-evaluation uses the refinement IDs for its workspace cases, which shows the intended relationship, but static validation does not preserve it.

This weakens the new requirement-to-case traceability contract: a typo or invented ID can make a case appear covered by the refinement without a declared requirement. When `workspace_allocation_refinement` is present, reject case IDs outside its requirement-ID set and add a negative test for that exact mismatch. The type-only tests at `candidate/tests/test_validation.py:573-606` do not cover referential integrity.

## Review conclusions

The prior P1 archive bypass is remediated. `retained_entrypoint` now limits the name/count exception to dated direct history snapshots and dated source/installed before/after snapshots; ordinary and lookalike documentation paths retain parent-name validation. The workflow exception is limited to a dated `runtime-review-N/frozen-source/.github/workflows/<file>` custody shape, so the committed frozen workflow copies can remain inert evidence without admitting operational workflow locations. The root runtime traversal and symlink rules, legacy evaluation checks, and runtime-requirement structural checks are unchanged.

## Input exposure and evidence boundary

Only the frozen manifest, its six pinned inputs, repository guidance, and read-only committed source paths used to compare the exact archival shapes and current self-evaluation declarations were read. No candidate code, test, runtime, hook, fixture, authentication flow, or native tool was executed. No source or expected-test file was edited. This report establishes static-review observations only; validation execution, activation/discovery, runtime admission, and native behavior remain unproven.

## Frozen input pins

| Arm | Path | SHA-256 |
| --- | --- | --- |
| baseline | `scripts/validate_framework.py` | `b9fdcf2530cb87a3602b36b8c42fa52e42c5a816935c004511bd5177364a08a6` |
| baseline | `tests/test_validation.py` | `4ac36b653ed072acf3d342998c3a27c350dab61bcead0731be0748b95eb0f9c8` |
| baseline | `scripts/runtime_requirements.py` | `08d8f1761e9c89298010903979509cd45c301c089e55446030055e343e9b7ab2` |
| candidate | `scripts/validate_framework.py` | `c50dd60c61384a165c794fd0fd140a761d4ac1b6d39d0aa3778b9273322f8636` |
| candidate | `tests/test_validation.py` | `2d69246a081e70d2491f8c23f3bf88e0360b85284e10a8c2435c9b7ff3c324ca` |
| candidate | `scripts/runtime_requirements.py` | `08d8f1761e9c89298010903979509cd45c301c089e55446030055e343e9b7ab2` |
