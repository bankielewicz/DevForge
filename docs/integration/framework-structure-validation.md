# Framework structure validation (compiled)

`devforge validate framework` is the compiled Rust port of
`scripts/validate_framework.py`. It inspects a selected DevForgeAI checkout and
reports its structure. It never imports, installs or executes a candidate skill,
hook, agent or runtime host, never materializes an authored eval fixture, never
advances a phase and never records acceptance. A structural `PASS` is not native
skill behavior, package readiness, qualification or owner acceptance; the report
says so with `behavior: NOT_EVALUATED` and, when a package declares a delivery
runtime, `runtime_host: NOT_VERIFIED`.

```bash
devforge validate framework --framework <DevForgeAI checkout>
```

## Outcomes

| Result | Stdout | Stderr | Exit |
| --- | --- | --- | --- |
| `PASS` | the legacy JSON object, 2-space indented | empty | 0 |
| `BLOCKED` (first refused defect) | empty | `BLOCKED: <reason>` | 2 |

There is no `FAIL` report: the legacy validator raised on the first defect and
this command refuses the same way. The `PASS` object is

```json
{
  "status": "PASS",
  "skills": 8,
  "scope": "structure only",
  "behavior": "NOT_EVALUATED"
}
```

(`skills` is the count for that tree; the example above is a small synthetic
root with the two provider packages and their four core skills.)

with `runtime_requirements` (provider to sidecar) and `runtime_host`
(`NOT_VERIFIED`) appended, in that order, when at least one provider package
declares a runtime requirement. `skills` counts every inspected `SKILL.md` that
is not a retained evidence entrypoint.

## Checks (unchanged from the legacy validator)

- Traversal of every file under the selected root, pruning `.git`, `.poc` and
  `__pycache__` at any depth and the root-only `.devforge-runtime` directory.
  Any other symlink, at any depth, is refused (`symlink not accepted: <path>`).
  A pruned name is skipped before the symlink check, so an excluded symlink is
  still excluded.
- GitHub workflows belong in DevForge: any path containing both `.github` and
  `workflows` components is refused, except a frozen
  `docs/skill-authoring/<dated container>/runtime-review-<n>/frozen-source/.github/workflows/<file>`
  snapshot.
- Every `.json` file parses under Python's JSON grammar (duplicate keys and the
  `NaN`/`Infinity` constants are accepted at this stage, exactly as
  `json.loads` accepted them).
- Every `.toml` file parses. A file with an `agents` path component must declare
  truthy `name`, `description` and `developer_instructions`.
- Every `.py` file parses (see "Python syntax inspection" below).
- Every `SKILL.md` starts with `---\n`, has a `^name: [a-z0-9-]+$` line matching
  its parent directory and a `^description: .+` line. A directly retained
  original entrypoint inside a dated evidence container keeps its original name:
  `docs/skill-authoring/history/<dated>/SKILL.md`, or
  `docs/skill-authoring/<history/>?<dated>/<source|installed>(-<label>)?-(before|after)(-<n>)?/SKILL.md`.
  Its metadata is still checked; it is not counted in `skills`.
- The retired `plugins/devforgeai` source must not exist.
- For each of `claude` and `codex`: `providers/<provider>/plugins/devforgeai`
  must carry `.<provider>-plugin/plugin.json` naming `devforgeai`, and the four
  core skills `devforge-brainstorm`, `devforge-project-expert-creator`,
  `devforge-develop` and `devforge-review` each need a `SKILL.md`.
- `hooks/runtime-requirements.json`, when present, must be the exact supported
  `devforge.runtime-requirement/v1` document for that provider, and the package
  must then carry a bounded `hooks/hooks.json` selecting exactly the four
  synchronous delivery events. Both readers are `src/plugin.rs`, the shared port
  of `scripts/runtime_requirements.py`; this command adds nothing to them.
- Every `skills/*/evals/evals.json` is checked as either the legacy
  `{"skill_name", "evals"}` shape (contained, existing fixture files) or the
  `devforge.skill-validator-self-evals/v1` declaration (envelope fields, typed
  metadata, fixture-set shapes, inline path safety, base resolution and cycle
  detection, and per-case identity, tier, fixture and requirement references).
  Inline fixture payloads stay opaque strings: nothing is decoded, written or
  executed.

## Python syntax inspection

A `.py` artifact is parsed by running

```
/usr/bin/python3 -I -B -S -c 'import ast,sys; ast.parse(sys.stdin.buffer.read())'
```

with the file bytes on standard input, a cleared environment (only `PATH` and
`LANG`), no filesystem path given to the interpreter, discarded output and a
10-second wall deadline. The candidate file is never imported or executed and is
never named to the interpreter. A non-zero exit, a timeout or a spawn failure is
`BLOCKED` with the relative path named:

- `<path>: invalid Python syntax`
- `<path>: Python syntax inspection exceeded 10 seconds`
- `<path>: Python syntax inspection unavailable: <error>`

Rust owns the decision; the interpreter only inspects a Python artifact, which
the development language policy permits. This is the only subprocess the command
starts.

## Callers

| Caller | Command |
| --- | --- |
| `scripts/verify_poc.py`, `framework-structure` stage | `target/debug/devforge validate framework --framework <framework>` (the `build` stage runs first) |
| `.github/workflows/validate-framework.yml`, "Validate without executing candidate code" | `authority/target/debug/devforge validate framework --framework candidate` |
| [`README.md`](../../README.md) capability list | names the subcommand |
| [`docs/cli-quickstart.md`](../cli-quickstart.md) | the structural framework check example |

`scripts/validate_framework.py` and `scripts/runtime_requirements.py` are
unchanged and remain the legacy regression baseline (`tests/test_validation.py`
was retired once this mapping was complete; see
[legacy test retirement](legacy-test-retirement.md)); `tests/validate_framework.rs` compares the compiled command with the
Python script on synthetic trees and on the companion checkout. Retiring the
Python script is a separate later decision.

## Test mapping

Every case in `tests/test_validation.py` has a black-box counterpart in
`tests/validate_framework.rs`, which drives the compiled binary instead of
importing the module.

| `tests/test_validation.py` | `tests/validate_framework.rs` |
| --- | --- |
| `FrameworkTraversalTest.test_source_only_passes` | `source_only_passes` |
| `test_retained_entrypoint_keeps_original_name_in_evidence_container` | `retained_entrypoint_keeps_original_name_in_evidence_container` |
| `test_retained_entrypoint_still_requires_valid_name_and_description` | `retained_entrypoint_still_requires_valid_name_and_description` |
| `test_named_snapshot_entrypoints_preserve_original_metadata` | `named_snapshot_entrypoints_preserve_original_metadata` |
| `test_snapshot_name_exception_is_bounded_and_metadata_still_checked` | `snapshot_name_exception_is_bounded_and_metadata_still_checked` |
| `test_archive_does_not_exempt_deeper_or_lookalike_skill_directories` | `archive_does_not_exempt_deeper_or_lookalike_skill_directories` |
| `test_retained_tree_json_and_python_are_still_inspected` | `retained_tree_json_and_python_are_still_inspected` |
| `test_frozen_runtime_review_workflows_are_inert_evidence` | `frozen_runtime_review_workflows_are_inert_evidence` |
| `test_workflow_ownership_is_still_enforced_outside_frozen_runtime_review` | `workflow_ownership_is_still_enforced_outside_frozen_runtime_review` |
| `test_retained_entrypoint_symlink_is_rejected` | `retained_entrypoint_symlink_is_rejected` |
| `test_root_runtime_is_pruned_before_enumeration` | `root_runtime_is_pruned_before_enumeration` (black box: a malformed document and a dangling symlink inside the private runtime, which any enumeration would refuse, still `PASS`) |
| `test_root_runtime_symlinks_are_rejected` | `root_runtime_symlinks_are_rejected` |
| `test_authored_symlinks_are_rejected_including_nested_runtime` | `authored_symlinks_are_rejected_including_nested_runtime` |
| `test_authored_json_is_checked_including_nested_runtime` | `authored_json_is_checked_including_nested_runtime` |
| `test_existing_exclusions_retain_root_and_nested_scope` | `existing_exclusions_retain_root_and_nested_scope` |
| `test_existing_excluded_symlink_entries_remain_excluded` | `existing_excluded_symlink_entries_remain_excluded` |
| `test_required_structure_checks_remain_active` | `required_structure_checks_remain_active` |
| `RuntimeRequirementsTest.test_legacy_result_omits_runtime_claims` | `legacy_result_omits_runtime_claims` |
| `test_valid_sidecars_report_only_declared_providers_without_executing_host` | `valid_sidecars_report_only_declared_providers_without_executing_host` |
| `test_invalid_json_and_duplicate_requirement_keys_are_rejected` | `invalid_json_and_duplicate_requirement_keys_are_rejected` |
| `test_requirement_must_be_an_object_with_exact_keys` | `requirement_must_be_an_object_with_exact_keys` |
| `test_requirement_literals_and_scalar_types_are_strict` | `requirement_literals_and_scalar_types_are_strict` |
| `test_requirement_provider_must_match_its_plugin` | `requirement_provider_must_match_its_plugin` |
| `test_required_events_are_exact_ordered_literals` | `required_events_are_exact_ordered_literals` |
| `test_runtime_requirement_needs_a_hook_source` | `runtime_requirement_needs_a_hook_source` |
| `test_hook_source_requires_each_event_once_and_no_extra_events` | `hook_source_requires_each_event_once_and_no_extra_events` |
| `test_hook_source_requires_one_correct_command_handler_per_event` | `hook_source_requires_one_correct_command_handler_per_event` |
| `test_duplicate_hook_event_keys_are_rejected` | `duplicate_hook_event_keys_are_rejected` |
| `EvalDeclarationTest.test_explicit_schema_checks_all_tiers_without_executing_or_materializing_payloads` | `explicit_schema_checks_all_tiers_without_executing_or_materializing_payloads` |
| `test_unknown_schema_and_unversioned_self_evals_do_not_fall_back` | `unknown_schema_and_unversioned_self_evals_do_not_fall_back` |
| `test_optional_workspace_refinement_is_typed_declarative_metadata` | `optional_workspace_refinement_is_typed_declarative_metadata` |
| `test_optional_case_requirement_ids_are_nonempty_unique_text` | `optional_case_requirement_ids_are_nonempty_unique_text` |
| `test_case_requirement_references_resolve_when_refinement_is_declared` | `case_requirement_references_resolve_when_refinement_is_declared` |
| `test_identity_and_provider_must_match_with_other_fields_valid` | `identity_and_provider_must_match_with_other_fields_valid` |
| `test_envelope_and_metadata_shapes_are_required` | `envelope_and_metadata_shapes_are_required` |
| `test_cases_must_be_a_nonempty_array_and_ids_unique` | `cases_must_be_a_nonempty_array_and_ids_unique` |
| `test_case_fields_and_optional_boolean_are_checked` | `case_fields_and_optional_boolean_are_checked` |
| `test_case_fixture_and_base_references_must_resolve` | `case_fixture_and_base_references_must_resolve` |
| `test_self_and_multi_node_fixture_cycles_are_rejected` | `self_and_multi_node_fixture_cycles_are_rejected` |
| `test_every_inline_path_operation_rejects_escaping_or_nonportable_paths` | `every_inline_path_operation_rejects_escaping_or_nonportable_paths` |
| `test_inline_content_must_be_utf8_text_in_every_map` | `inline_content_must_be_utf8_text_in_every_map` |
| `test_fixture_objects_and_operations_have_declared_shapes` | `fixture_objects_and_operations_have_declared_shapes` |
| `test_patch_targets_resolve_and_declared_absence_stays_absent` | `patch_targets_resolve_and_declared_absence_stays_absent` |
| `test_duplicate_keys_and_nonfinite_json_are_rejected` | `duplicate_keys_and_nonfinite_json_are_rejected` |
| `test_legacy_nonfinite_values_require_strict_json_parsing` | `legacy_nonfinite_values_require_strict_json_parsing` |
| `test_legacy_eval_format_keeps_existing_file_checks` | `legacy_eval_format_keeps_existing_file_checks` |
| `StructuralGateTest.test_optimized_python_cannot_disable_validation` | `NOT_APPLICABLE`: the compiled command has no `assert` statements and no `python3 -O` equivalent. The property it protected (checks cannot be optimized away) holds by construction. |

Cases with no legacy counterpart:

| `tests/validate_framework.rs` | Why |
| --- | --- |
| `agent_toml_requires_truthy_identity_metadata` | The `.toml` branch and the Codex `agents` key requirement had no legacy unit test. |
| `valid_python_and_json_in_retained_evidence_stay_inspectable_and_pass` | A valid `.py` artifact must pass the bounded inspection, not only fail it. |
| `python_syntax_inspection_refuses_what_the_legacy_parser_refuses` | Records the exit-status difference for a `SyntaxError` (below). |
| `matches_the_legacy_validator_on_synthetic_trees` | Eighteen synthetic trees compared with the unchanged Python script: exit status and stdout bytes always, stderr bytes except the JSON-decoder wording case. |
| `refuses_the_real_framework_checkout_like_the_legacy_script` | Compares the refusal contract of both validators on the companion checkout: equal exit status, empty stdout and a single `BLOCKED` line each. It is deliberately not byte parity, because the checkout holds several independent defects and neither validator promises which one is named; the byte evidence is the two cases above. |
| `the_real_authored_hook_packages_validate_byte_for_byte_like_the_legacy_script` | Copies the two real `hooks/` and `.{provider}-plugin/` directories into a synthetic root and compares byte for byte, including the emitted `runtime_requirements`. |
| `a_missing_framework_root_is_refused_like_the_legacy_script` | The `[Errno 2]` refusal text. |

`src/validate_framework.rs` also carries unit tests for the three retained
evidence name patterns, the front-matter helpers and the Python JSON dialect.

## Known differences from `scripts/validate_framework.py`

- **First-defect selection.** Neither validator promises which defect is
  reported when a tree has several: the legacy script pops a LIFO stack of
  directories and iterates `iterdir()` in readdir order, while this command
  visits a directory's files and then its subdirectories in sorted component
  order. Both refuse with exit 2. The companion checkout currently holds 38
  independent retained-evidence `SKILL.md` defects, so the two commands name
  different ones there. A repaired copy of that same checkout produces byte
  identical output from both.
- **`.py` syntax errors.** The legacy script let `SyntaxError` escape its
  `except (ValueError, OSError, AssertionError, KeyError)` clause, so a
  malformed `.py` file exited 1 with a traceback. This command refuses it as
  `BLOCKED: <path>: invalid Python syntax` with exit 2, and names the file the
  legacy traceback did not.
- **Non-object plugin manifest.** `json.loads(manifest)["name"]` on a JSON array
  raised an uncaught `TypeError` (exit 1). This command refuses with
  `BLOCKED: plugin manifest must be an object: <path>` and exit 2.
- **Strict plugin manifest decoding.** `.{provider}-plugin/plugin.json` is decoded
  with `serde_json`, which refuses the `NaN` and `Infinity` constants that the
  legacy `json.loads(manifest.read_text())` accepted. A manifest carrying one is
  `BLOCKED` with exit 2 where the legacy script returned `PASS`. This is stricter
  and so cannot weaken a refusal. Duplicate keys (last wins) and very large
  integers still behave as they did.
- **Diagnostic wording.** Malformed `.json` and `.toml` documents keep Python's
  positional message shape but are prefixed with the relative path, which the
  legacy `BLOCKED:` line omitted. TOML parse errors use the `toml` crate's text.
  JSON column numbers count bytes rather than characters, which differ only for
  non-ASCII documents.
- **Lone surrogate escapes.** `"\ud800"` decoded to a Python string and was
  refused later as `inline content must encode as UTF-8`. Rust refuses the
  document at decoding time with the serde wording. Both are `BLOCKED`, exit 2.
- **Very large integers.** Python decoded a 400-digit integer exactly; this
  command refuses a JSON integer outside `i64`/`u64` inside a strictly decoded
  eval declaration. The lenient traversal parse still accepts it.
- **`runtime_requirements` key order.** The legacy report echoed the sidecar's
  own key order. This command emits the declared order
  (`schema_version`, `runtime`, `protocol`, `provider`, `completion_mode`,
  `required_events`), which is what the authored packages use, so real inputs
  are byte identical. A sidecar written with shuffled keys would differ in
  order only; `load_requirement` already requires the exact same key/value set.
- **Eval declaration order.** Where the legacy code iterated a set or a
  document-order dictionary (the per-object text fields, `fixture_sets.items()`,
  `skills/*/evals/evals.json` discovery), this command uses a fixed or sorted
  order. That changes only which of several simultaneous defects is named.

## Status

Partial migration. `scripts/validate_framework.py` and
`scripts/runtime_requirements.py` are unchanged and still run; the compiled
command is the authority for the two callers listed above. Their Python
authority is not claimed to satisfy the development language policy, and
retiring them is a separate later decision.

Verified locally with `cargo fmt --check`, `cargo clippy --locked --all-targets
-- -D warnings`, `cargo build --locked`, `cargo test --locked --all-targets` and
`python3 -m unittest discover -s tests -p 'test_*.py' -v`, plus the real-tree
comparisons above. Hosted execution of the changed manual
`validate-framework.yml` workflow is `NOT_RUN` until an owner dispatches it from
`main` after merge. Structural checking is not native skill acceptance, package
readiness, qualification or owner acceptance.
