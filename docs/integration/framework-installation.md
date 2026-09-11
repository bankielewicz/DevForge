# Project-local framework installation (`devforge install framework`)

Compiled Rust owns project-local installation of the DevForgeAI provider
packages. `devforge install framework` replaces the project-installation modes
of `scripts/install_framework.py`'s `install()`. That script is unchanged and
remains the legacy baseline; `tests/test_installer.py` still runs against it.

Nothing here is behavioral acceptance. A successful install proves which bytes
were placed and which mechanical predicates passed. Native activation stays
`NOT_VERIFIED` and skill behavior stays `NOT_EVALUATED`.

## Command

```bash
devforge --project <ABS project dir> install framework \
  --framework <ABS DevForgeAI checkout> \
  [--provider codex|claude|both] \
  [--include-experts] \
  [--runtime <ABS devforge executable>]
```

- `--provider` defaults to `both`.
- `--include-experts` also installs the project's own `experts/*/SKILL.md`
  packages into every selected provider's skill root.
- `--runtime` is required, and only required, when a selected provider's plugin
  declares `hooks/runtime-requirements.json`. It must be absolute, canonical,
  a regular executable file and have exactly one hard link.
- There is no `--validator`. The executable you invoke **is** the validating
  authority: it probes the selected runtime in process and applies its own
  pre-write protections. It must be outside the installation project.
- `--manual-evidence` and `--manual-experts-only` are not offered. Promoted
  Codex expert packages (`devforge-project-expert-creator`,
  `devforge-evaluate-expert`) are refused here and installed only by
  [`devforge install manual-experts`](manual-expert-adoption.md). An existing
  inventory's `manual_expert_adoption` record is preserved untouched.

Success prints a JSON result on stdout and exits 0:

```json
{
  "status": "INSTALLED",
  "project": "...",
  "providers": ["claude"],
  "files": 108,
  "removed_authoring_files": [],
  "scope": "project-local; no global configuration changed",
  "authority": "compiled Rust CLI; no Python consulted",
  "behavior": "NOT_EVALUATED",
  "runtime_requirements": {"claude": {...}},
  "runtime_compatibility": "VERIFIED",
  "native_activation": "NOT_VERIFIED"
}
```

A refusal prints `{"status":"BLOCKED","reason":"..."}` on stdout and exits 2,
the same surface every other `devforge install` action uses. Refusals precede
every write: the project tree is unchanged after one.

### Worked example

```bash
DEVFORGE_SRC=/path/to/DevForge
FRAMEWORK=/path/to/DevForgeAI
PROJECT=/path/to/project          # must be outside $FRAMEWORK
# Cargo hard-links target/debug/devforge, so --runtime needs a single-link copy.
install -m 755 "$DEVFORGE_SRC/target/debug/devforge" /tmp/devforge-runtime
"$DEVFORGE_SRC/target/debug/devforge" --project "$PROJECT" install framework \
  --framework "$FRAMEWORK" --provider claude --runtime /tmp/devforge-runtime
```

## What it writes

| Destination | Contents |
| --- | --- |
| `.agents/skills/<name>/` | Codex runtime skill files |
| `.claude/skills/<name>/` | Claude runtime skill files |
| `.codex/agents/` | `providers/codex/agents` |
| `.claude/agents/` | `<claude plugin>/agents` |
| `.codex/hooks.json` | Codex hook groups merged into the existing document |
| `.claude/settings.local.json` | Claude hook groups merged into the existing document |
| `.devforge-install.json` | Schema-1 inventory: `files`, `managed_hooks`, `runtime_evidence` |

`evals/`, `history/`, `__pycache__/`, `*.pyc` and `provenance.json` are excluded
from every installed package. Previously managed authoring files in the selected
skill scopes are retired, and a locally edited one refuses the removal instead.

Hook ownership is per group, never per file. A group the installer added is
`owned`; an identical group the user already had is `reused` and is never
removed. Editing, deleting or duplicating an owned group refuses the whole
installation. A settings document whose content is already semantically
identical is not rewritten at all.

## Callers

| Caller | Status |
| --- | --- |
| `scripts/demo.py` | Switched to `subprocess.run` of the compiled command; fails loudly on a nonzero exit. See "Known defect" below. |
| `README.md`, `docs/cli-quickstart.md` | Name the compiled command. |
| `scripts/install_framework.py` | Unchanged legacy baseline; still the only implementation of `--export-plugin`. |
| `scripts/verify_poc.py` | Unchanged; it invokes `demo.py`, so it inherits the defect below. |

## Legacy test mapping

`tests/install_framework.rs` is the Rust replacement for the project-installation
half of `tests/test_installer.py`, which stays in place and passing.

| `tests/test_installer.py` | `tests/install_framework.rs` |
| --- | --- |
| `test_promoted_codex_expert_requires_evidence_before_any_install_write` | `promoted_codex_expert_requires_evidence_before_any_install_write` |
| `test_installs_both_providers_and_repeats` | `installs_both_providers_and_repeats` |
| `test_authoring_material_excluded_and_runtime_resources_preserved` | `authoring_material_excluded_and_runtime_resources_preserved` |
| `test_missing_provider_source_cannot_fall_back_to_shared_tree` | `missing_provider_source_cannot_fall_back_to_shared_tree` |
| `test_managed_old_eval_file_is_removed_but_edited_one_blocks` | `managed_old_eval_file_is_removed_but_edited_one_blocks` |
| `test_local_edit_collision_is_preserved` | `local_edit_collision_is_preserved` |
| `test_managed_refresh_updates_unmodified_copy` | `managed_refresh_updates_unmodified_copy` |
| `test_symlink_destination_is_rejected` | `symlink_destination_is_rejected` |
| `test_hook_sources_install_for_both_providers_and_repeat_without_duplicates` | `hook_sources_install_for_both_providers_and_repeat_without_duplicates` |
| `test_hook_merge_preserves_settings_and_noncommand_user_groups` | `hook_merge_preserves_settings_and_noncommand_user_groups` |
| `test_owned_group_updates_and_retires_without_losing_unrelated_group` | `owned_group_updates_and_retires_without_losing_unrelated_group` |
| `test_identical_unowned_group_is_reused_and_never_removed_on_source_update` | `identical_unowned_group_is_reused_and_never_removed_on_source_update` |
| `test_removed_edited_or_duplicated_owned_group_blocks_all_writes` | `removed_edited_or_duplicated_owned_group_blocks_all_writes` |
| `test_owned_definition_digest_distinguishes_json_boolean_from_integer` | `owned_definition_digest_distinguishes_json_boolean_from_integer` |
| `test_missing_settings_rebuilds_current_groups_only` | `missing_settings_rebuilds_current_groups_only` |
| `test_selected_provider_preserves_unselected_hook_inventory_and_settings` | `selected_provider_preserves_unselected_hook_inventory_and_settings` |
| `test_malformed_or_duplicate_settings_preserve_all_existing_bytes` | `malformed_or_duplicate_settings_preserve_all_existing_bytes` |
| `test_symlink_settings_preserves_target_and_does_not_install_skills` | `symlink_settings_preserves_target_and_does_not_install_skills` |
| `test_skill_collision_does_not_update_hooks` | `skill_collision_does_not_update_hooks` |
| `test_parent_file_collision_preflights_before_other_writes` | `parent_file_collision_preflights_before_other_writes` |
| `test_hook_default_and_exact_declarations_export_runtime_only` | `hook_default_and_exact_declarations_are_both_read` (install half only; the export half moves with export) |
| `test_unsupported_hook_declarations_fail_install_and_export_before_writes` | `unsupported_hook_declarations_fail_install_before_writes` (install half only) |
| `test_missing_malformed_duplicate_or_symlink_hook_source_is_rejected` | `missing_malformed_duplicate_or_symlink_hook_source_is_rejected` (install half only) |
| `test_empty_default_hook_directory_and_duplicate_manifest_are_rejected` | `empty_default_hook_directory_and_duplicate_manifest_are_rejected` |
| `test_legacy_absence_never_probes_a_runtime` | `legacy_absence_never_probes_a_runtime` |
| `test_delivery_install_requires_explicit_runtime_even_with_path_and_environment` | `delivery_install_requires_explicit_runtime_even_with_path_and_environment` (the `--validator` half is not applicable) |
| `test_delivery_sidecar_rejects_malformed_duplicate_unknown_and_unsupported_values` | `delivery_sidecar_rejects_malformed_duplicate_unknown_and_unsupported_values` (install half only) |
| `test_delivery_requires_complete_unique_synchronous_hook_selection` | `delivery_requires_complete_unique_synchronous_hook_selection` |
| `test_delivery_runtime_must_be_absolute_canonical_regular_and_executable` | `delivery_runtime_must_be_absolute_canonical_regular_and_executable` |
| `test_delivery_incompatible_capabilities_block_all_installation_writes` | `delivery_incompatible_capabilities_block_all_installation_writes` |
| `test_delivery_compatible_explicit_runtime_records_exact_evidence_and_preserves_user_hooks` | `delivery_compatible_runtime_records_exact_evidence_and_preserves_user_hooks` |
| `test_delivery_guard_receives_the_resolved_project_and_every_sorted_destination` | `delivery_compatible_runtime_records_exact_evidence_and_preserves_user_hooks` (the guard is in process; the recorded evidence and the admitted write set are asserted instead of a recorded call) |
| `test_delivery_binary_mutation_during_probe_blocks_all_installation_writes` | `delivery_binary_mutation_during_probe_blocks_all_installation_writes` |
| `test_delivery_selected_runtime_cannot_be_overwritten_by_installation` | `delivery_selected_runtime_cannot_be_overwritten_by_installation` |
| `test_delivery_runtime_hardlink_to_managed_destination_blocks_before_probe` | `delivery_runtime_hardlink_to_managed_destination_blocks_before_probe` |
| `test_delivery_capability_probe_limits_time_output_and_exit_status` | `delivery_capability_probe_limits_time_output_and_exit_status` |
| `test_delivery_selected_validator_admits_the_real_extended_runtime_contract` | `the_real_extended_runtime_contract_is_admitted_and_recorded` |
| `test_delivery_validator_refusal_blocks_every_installation_write` | `a_validator_refusal_blocks_every_installation_write` |
| `test_delivery_validator_inside_the_project_is_refused_by_the_compiled_authority` | `a_validating_executable_inside_the_project_is_refused_before_execution` |
| `test_delivery_validator_aliased_by_a_destination_blocks_all_installation_writes` | `a_destination_aliasing_the_validating_executable_blocks_all_installation_writes` |
| `test_delivery_guard_refusal_blocks_every_installation_write` | `a_destination_aliasing_the_validating_executable_blocks_all_installation_writes` (a real guard refusal, not an injected one) |
| `test_delivery_validator_must_be_an_absolute_canonical_single_link_executable` | NOT_APPLICABLE: there is no `--validator` to select |
| `test_delivery_binary_mutation_after_probe_blocks_all_installation_writes` | NOT_RUN: the legacy case mutated the runtime from inside a mocked `plan_hook_merge`. A black-box reproduction cannot land inside that window deterministically. The rule itself is covered by the `sha256_after` comparison before writes, which `delivery_binary_mutation_during_probe_blocks_all_installation_writes` and the guard's digest check exercise. |
| `test_delivery_validator_mutation_after_probe_blocks_all_installation_writes` | NOT_RUN: same reason. `tests/probe_runtime.rs::a_validator_whose_bytes_changed_since_the_probe_is_refused` covers the compiled decision directly. |
| `test_delivery_selected_validator_cannot_be_overwritten_by_installation` | NOT_APPLICABLE: guard check 1 (a destination naming the validator) is unreachable from `install framework`, because the probe refuses a validating executable inside the project first. That refusal is `a_validating_executable_inside_the_project_is_refused_before_execution`; `tests/probe_runtime.rs::a_destination_that_names_the_validating_executable_is_refused` covers check 1 itself. |
| `test_export_preserves_runtime_and_excludes_authoring_material` | NOT_RUN: export is unported |
| `test_delivery_export_retains_dependency_without_executing_runtime` | NOT_RUN: export is unported |

Added beyond the legacy suite:

- `project_and_framework_must_be_separate` — the new path-hygiene predicate.
- `project_experts_are_added_only_when_selected_and_collide_by_name`.
- `the_legacy_installer_and_the_compiled_command_agree_and_cross_refresh` — the
  legacy oracle: `/usr/bin/python3 scripts/install_framework.py` and the compiled
  command install the same delivery-aware fixture into two projects with one
  shared single-link executable as runtime and validator, agree on every
  installed byte and (after normalization) on the inventory, and then refresh
  each other's tree idempotently, with no local-edit refusal and no rewrite of a
  semantically identical settings document.

## Parity exceptions

These are the only known observable differences from
`scripts/install_framework.py`'s `install()`.

1. **`--validator` is gone.** The running executable is the validator. The
   legacy selection hygiene for `--validator` (absolute, canonical, regular,
   executable, exactly one hard link) is replaced by `executable_identity()`
   plus the existing refusal of a validating executable inside the project. A
   practical consequence: `target/debug/devforge`, which Cargo hard-links, can
   run the installation, though it still cannot be passed as `--runtime`.
2. **`--project` and `--framework` must be separate directories.** The legacy
   installer never compared them. This is new, and it is what stops
   `scripts/demo.py` (see below).
3. **JSON object key order.** Python wrote `.devforge-install.json` and the
   settings documents in insertion order; `serde_json` maps are key-sorted, so
   a document this command rewrites comes back with sorted keys. Content is
   unchanged and both tools read either order. No Cargo feature is used to
   change this.
4. **`managed_hooks` row order.** The legacy `desired` list followed the hook
   source's document order; the compiled one follows sorted event names. The
   set of rows, their digests and their `owned`/`reused` classification are
   identical.
5. **Float formatting in a hook group digest.** `group_digest` matches Python
   byte for byte for strings, objects, arrays, booleans, integers and ordinary
   decimals (`2.5`). Exponent-form floats can print differently
   (`1e+300` versus `1e300`), which would change that group's digest. No hook
   definition in either provider package contains a float; a package that
   introduced one would need this checked.
6. **Path resolution.** `crate::resolved` refuses any symlinked component of
   `--project` or `--framework`; Python's `Path.resolve()` followed them. This
   matches `install manual-experts`.
7. **Refusal wording for malformed JSON.** The exact decoder message differs
   (`serde_json` versus Python's `json`); the status, the exit code and the
   absence of writes do not.

## Still Python

- `--export-plugin` (runtime-only plugin export) is unported. Use
  `python3 scripts/install_framework.py --framework <dir> --provider <one>
  --export-plugin <parent>/devforgeai`. Its planned compiled form is
  `devforge install export-plugin`.
- `--manual-evidence` / `--manual-experts-only` remain legacy Python; the
  compiled replacement for that path is `devforge install manual-experts`.
- `scripts/install_framework.py`, `scripts/runtime_requirements.py` and
  `tests/test_installer.py` are unchanged and still run. Retirement is a
  separate decision.

## Known defect: `scripts/demo.py` installation is COULD_NOT_RUN

`scripts/demo.py` places each candidate at
`<framework>/.poc/<run id>/<slug>` and installs with
`--provider both --include-experts` and `target/debug/devforge` as both runtime
and validator. That call has been failing before this migration, and it still
fails after it. Observed on 2026-09-11 against
`framework/DevForgeAI` with the demo's own layout and arguments:

| Tool | Observed refusal |
| --- | --- |
| Compiled `install framework` | `{"reason":"project and framework must be separate directories","status":"BLOCKED"}` (exit 2) |
| Legacy `scripts/install_framework.py` | `{"status": "BLOCKED", "reason": "manual expert evidence: manual adoption evidence is required for promoted Codex packages"}` (exit 2) |
| Legacy, `--provider claude`, `--validator target/debug/devforge` | `{"status": "BLOCKED", "reason": "--validator must have exactly one hard link"}` (exit 2) |

Three independent causes, none of them fixed by a one-line change to the demo:

1. The Codex plugin ships `devforge-project-expert-creator` and
   `devforge-evaluate-expert`, so `--provider both` needs owner-selected
   adoption evidence that neither installer accepts on this path.
2. Cargo hard-links `target/debug/devforge` (link count 2), which
   `--runtime` and the legacy `--validator` both refuse.
3. The candidate project lives inside the framework, which the compiled
   command refuses.

The demo's `install(...)` call is switched to the compiled command and fails
loudly, so the defect is visible rather than silent. Repairing it is a
coordinator decision: it needs a candidate root outside the framework, a
single-link runtime copy, and a provider selection (or adoption evidence) that
does not include the promoted Codex packages. `scripts/verify_poc.py` invokes
`demo.py` and inherits this.
