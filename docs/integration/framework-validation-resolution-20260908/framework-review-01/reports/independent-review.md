# Independent maintenance review

## Scope and custody

- Review type: read-only consequential maintenance review.
- Candidate: existing concurrent maintenance bytes; no new authorship claimed.
- Base commit: `423c69aafe4bf98601e67a6c94e333b26857f310`.
- Companion source head supplied: `9970e92db7d824dea0c49e1a61a122480f2aa8b5`.
- Manifest: `manifest.json`, SHA-256 `37cda241d792a72e1a010d51cd4274a0b72bc3002720bb7f0b5a9a352afffcfb`.
- Pin verification: every listed input hash matched both before review and immediately before this report was written. The unchanged `runtime_requirements.py` has SHA-256 `08d8f1761e9c89298010903979509cd45c301c089e55446030055e343e9b7ab2` in both arms.

## Finding

### P1 — The archive exception is broader than its stated dated, precise scope

`candidate/scripts/validate_framework.py:200-205` treats every direct `SKILL.md` below `docs/skill-authoring/history/<arbitrary-directory>/` as an archived entrypoint. It then accepts any syntactically valid lower-case/hyphenated `name` and excludes it from the skill count. Neither the directory name nor an archive-date/evidence marker is checked.

Reproduction, without running it: add `docs/skill-authoring/history/unreviewed/SKILL.md` with front matter `name: devforge-review` and a nonempty description. Its name differs from its parent (`unreviewed`), but lines 200-205 accept it and omit it from `skills`. The normal `SKILL.md` identity rule would reject the same file anywhere outside that broad location.

This weakens the structural gate for a location intended to hold only dated retained evidence. Restrict the exception to the approved dated evidence-container form (or an explicit immutable allowlist) and add a negative test for an undated/arbitrary direct child. The present test at `candidate/tests/test_validation.py:36-42` uses `previous-builder-revision`, so it demonstrates the overly broad behavior rather than enforcing the stated boundary.

## Review conclusions

I found no additional actionable defect in the requested areas by static inspection. The root `.devforge-runtime` handling prunes only that real root directory before descent and rejects it when it is a symlink; nested authored directories remain traversed. The candidate retains JSON/Python/front-matter inspection of history descendants, validates the versioned self-evaluation document structurally without materializing inline bytes, preserves legacy evaluation checks, and structurally checks optional runtime requirements and hook source data.

## Input exposure and evidence boundary

Only the six frozen source inputs, manifest, and repository guidance were read. No candidate code, fixtures, hooks, runtime, native tool, authentication flow, or test suite was executed. No source or expected-test file was edited. This is static-review evidence only; it does not establish validation execution, hook discovery/activation, runtime admission, or native behavior.

## Frozen input pins

| Arm | Path | SHA-256 |
| --- | --- | --- |
| baseline | `scripts/validate_framework.py` | `5ef17e17d78535b520a0a1e6d8166b7fbb92bb44b7a8804dfb1131b8dce392a2` |
| baseline | `tests/test_validation.py` | `9f965dc1949f5a94e763f2deadd361743104651a92f5d46c6925bb085ec03f46` |
| baseline | `scripts/runtime_requirements.py` | `08d8f1761e9c89298010903979509cd45c301c089e55446030055e343e9b7ab2` |
| candidate | `scripts/validate_framework.py` | `b9fdcf2530cb87a3602b36b8c42fa52e42c5a816935c004511bd5177364a08a6` |
| candidate | `tests/test_validation.py` | `4ac36b653ed072acf3d342998c3a27c350dab61bcead0731be0748b95eb0f9c8` |
| candidate | `scripts/runtime_requirements.py` | `08d8f1761e9c89298010903979509cd45c301c089e55446030055e343e9b7ab2` |
