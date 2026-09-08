# Independent final successor maintenance review

## Scope and custody

- Review type: read-only consequential maintenance review.
- Baseline: `framework-review-02/candidate`.
- Candidate: existing successor maintenance bytes; no new candidate authorship claimed.
- Manifest SHA-256: `a9555816c70fc3dbc6f26f7e0668b300324385493caac8f9b994a1eb0520e3ca`.
- All six frozen input hashes matched before review and immediately before this report was written.

## Closure of the prior finding

The candidate closes framework-review-02 finding F-02. At `candidate/scripts/validate_framework.py:147-153`, each case still accepts an independently typed, nonempty, unique `requirement_ids` list. When the optional `workspace_allocation_refinement` is present, the new subset check rejects every case ID absent from its declared ID set. The condition preserves the intended behavior for self-contained cases without a refinement.

`candidate/tests/test_validation.py:608-618` adds both discriminating arms: a declared ID passes and an undeclared ID raises `unknown case requirement ID`. The preceding tests still cover the independent list-shape and duplicate-ID failures.

## Findings

No new actionable defect found by static inspection of the frozen successor candidate. The prior archive scope, root-runtime traversal, symlink, legacy-evaluation, optional-runtime-requirement, and inline-fixture inertness behavior is unchanged by this focused patch.

## Input exposure and evidence boundary

Only the manifest, six frozen input files, and workspace/repository guidance were read. No candidate code, test, runtime, hook, fixture, authentication flow, or native tool was executed. No source or expected-test file was edited. This is static-review evidence only; it does not establish validation execution, hook discovery or activation, runtime admission, or native behavior.

## Frozen input pins

| Arm | Path | SHA-256 |
| --- | --- | --- |
| baseline | `scripts/validate_framework.py` | `c50dd60c61384a165c794fd0fd140a761d4ac1b6d39d0aa3778b9273322f8636` |
| baseline | `tests/test_validation.py` | `2d69246a081e70d2491f8c23f3bf88e0360b85284e10a8c2435c9b7ff3c324ca` |
| baseline | `scripts/runtime_requirements.py` | `08d8f1761e9c89298010903979509cd45c301c089e55446030055e343e9b7ab2` |
| candidate | `scripts/validate_framework.py` | `cca69d937cb88f4c5a10055a4c95210a51b6c353908cb4c8322579f2f70778c6` |
| candidate | `tests/test_validation.py` | `17411b058636e49038248f833998c9c1c8976db8ac7b278e436ef14c150ba9a2` |
| candidate | `scripts/runtime_requirements.py` | `08d8f1761e9c89298010903979509cd45c301c089e55446030055e343e9b7ab2` |
