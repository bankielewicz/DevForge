# MVP document validation (compiled)

`devforge validate mvp` is the compiled Rust port of `scripts/validate_mvp.py`.
It inspects a selected DevForgeAI `docs/mvp` tree and reports structural
findings. It never runs model evaluations, executes candidate code, advances a
phase, installs a package or records acceptance. A structural `PASS` is not MVP
completion, native skill behavior, qualification or owner acceptance.

```bash
devforge validate mvp --mvp <framework>/docs/mvp [--report <file>]
```

The framework root is the directory two levels above `--mvp`; provider skill
sources are resolved against it. The optional `--report` file receives the full
JSON report, including `files_sha256`, and its parent directories are created.
Stdout always prints the same report without `files_sha256`.

## Outcomes

| Result | Stdout | Stderr | Exit | Report file |
| --- | --- | --- | --- | --- |
| `PASS` | JSON report | empty | 0 | written when requested |
| `FAIL` (collected errors) | JSON report | empty | 2 | written when requested |
| `BLOCKED` (malformed or unreadable input) | empty | `BLOCKED: <reason>` | 2 | never written |

Missing templates, indexed documents, provider `SKILL.md` files, link targets and
research snapshots accumulate as `errors`. A missing or malformed
`package-index.json`, specification file or `research/sources.json`, any
unparsable `.json` document, non-UTF-8 text, an invalid indexed skill name, an
absolute or escaping path, or a symlinked indexed document refuses execution.

## Checks (unchanged from the legacy validator)

- Exactly twelve uniquely named indexed skills; each specification contains the
  seven required section headings (substring matches).
- Template existence, declared consumers, shared contracts, shared templates and
  authoring templates from `package-index.json`.
- Provider `source` mappings equal
  `providers/<provider>/plugins/devforgeai/skills/<name>` and that skill's
  `SKILL.md` exists under the framework root.
- Local-path containment and symlink refusal for indexed paths; symlinks found
  during traversal are errors before the `validation/` and `validation.json`
  inventory exclusions apply. Containment compares resolved paths on both
  sides, so a symlinked directory such as `research` is collected as
  `symlink document: research` rather than refusing its own entries.
- Every `.json` document parses; every `.md` document has balanced code fences,
  no trailing whitespace and no broken local links (`://`, `#...` and `{{`
  targets are skipped; fragments are stripped).
- Cached research snapshots listed in `research/sources.json` exist under
  `research/` and match their recorded SHA-256.
- Per-file SHA-256 inventory of the tree in the legacy traversal order.

Report fields: `schema_version` 2, `created_at_utc`, `status`, `errors`,
`specifications`, `skill_output_templates`, `shared_templates`,
`authoring_templates`, `scope`, `not_checked`, `native_skill_behavior`
(`NOT_EVALUATED`) and, in the report file only, `files_sha256`.

## Known differences from `scripts/validate_mvp.py`

- Diagnostic wording after `BLOCKED:` differs for JSON decoding, UTF-8 decoding
  and wrong-type index values (contextual text instead of Python exception
  text). Path, symlink, skill-name and OS-error reasons are identical.
- JSON dialect: `NaN`, `Infinity`, lone surrogate escapes and out-of-range
  floats are refused (`BLOCKED`) rather than accepted.
- Conditions the legacy script left as uncaught tracebacks (exit 1), such as a
  `--mvp` directory with fewer than two parents, a non-object `implementations`
  value or a report write failure, are reported as `BLOCKED` with exit 2.
- Index arrays must be JSON arrays; the legacy script iterated other iterables.

## Status

Partial migration. The manual `.github/workflows/validate-framework.yml`
workflow (`workflow_dispatch`, main-only authority guard, exact 40-character
candidate SHA) now builds DevForge from its trusted `authority` checkout with
Rust 1.94.0 and `cargo build --locked`, then runs
`authority/target/debug/devforge validate mvp --mvp candidate/docs/mvp` for
the MVP-document check. The build finishes before the candidate is checked out,
only authority source is compiled, and candidate code, scripts, Cargo files,
tests and hooks are never executed. A `FAIL` or `BLOCKED` exit fails that
workflow step; there is no Python fallback. The separate framework-structure
step runs the compiled `devforge validate framework` (see
[framework structure validation](framework-structure-validation.md)).

`scripts/verify_poc.py` now calls the compiled command for its `mvp-documents`
stage, and `devforge verify-poc` is its compiled port (see
[demonstration and verification](demo-and-verification.md)).
The Python script itself is unchanged and stays the regression baseline:
`tests/validate_mvp.rs` compares the compiled command with it on synthetic
trees. Installers and operational skills are unchanged.

This is structural checking only. It is not native skill acceptance, MVP
completion, qualification or owner acceptance. The workflow change was
verified locally with equivalent commands and normal PR CI; hosted execution of
the changed manual workflow is `NOT_RUN` until an owner dispatches it from
`main` after merge.
