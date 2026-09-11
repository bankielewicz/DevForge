# Project-local framework installation and plugin export

Compiled Rust owns project-local installation of the DevForgeAI provider
packages and the runtime-only plugin export. `devforge install framework`
replaces the project-installation modes of `scripts/install_framework.py`'s
`install()`, and `devforge install export-plugin` replaces its
`export_plugin()`. That script is unchanged and remains the legacy baseline;
`tests/test_installer.py` still runs against it.

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
  pre-write protections. It must be outside the installation project **and
  outside the framework**, and that is checked on **every** run — before the
  framework is read, whether or not any provider declared a runtime requirement.
  Its self-protections (no destination may name it, no existing destination may
  already alias its inode, its bytes must not change before the writes) also run
  on every install. Only the runtime *capability probe* stays conditional, so no
  `--runtime` is needed when nothing declares a requirement.
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

## Runtime-only plugin export

```bash
devforge install export-plugin \
  --framework <ABS DevForgeAI checkout> \
  --provider codex|claude \
  --output <ABS new directory named devforgeai>
```

This is **unaccepted staging**: the result says so, in `adoption:
"NOT_ACCEPTED_STAGING"`. It builds a fresh runtime-only plugin directory from
one provider's source and never overwrites an existing export. It reads only
declarative files, runs nothing, and probes no runtime even when the package
declares one.

- `--provider` takes exactly one provider; `both` is rejected by the parser.
  There is no `--include-experts` and no adoption input, so a plugin export can
  never carry project experts or adoption evidence.
- `--output` must be named `devforgeai`, must not already exist (a dangling
  symlink counts as existing), must have no symlinked or non-directory parent,
  and must lie outside the source plugin.
- Only `.{provider}-plugin/`, `skills/`, `agents/` and `hooks/` may appear at
  the plugin's top level; anything else refuses the export by name.
  `__pycache__/` and `*.pyc` are skipped wherever they appear, before that
  check. Skill and hook authoring material (`evals/`, `history/`,
  `provenance.json`) is excluded.
- The plugin manifest must declare `"name": "devforgeai"`.
- Every refusal precedes creating anything: after one the output directory does
  not exist and no parent directory was created for it. A parent that already
  existed is left exactly as it was; the export never modifies anything outside
  the new directory it creates.

Success prints, on stdout with exit 0:

```json
{
  "status": "EXPORTED",
  "provider": "codex",
  "output": "...",
  "files_sha256": {"skills/demo/SKILL.md": "..."},
  "behavior": "NOT_EVALUATED",
  "adoption": "NOT_ACCEPTED_STAGING",
  "authority": "compiled Rust CLI; no Python consulted",
  "runtime_requirements": {"codex": {...}},
  "runtime_host": "NOT_VERIFIED"
}
```

`runtime_requirements` and `runtime_host` appear only when the provider's plugin
declares `hooks/runtime-requirements.json`. The sidecar is copied into the export
and reported, never satisfied: `runtime_host: "NOT_VERIFIED"` means no host was
probed or admitted.

## Callers

| Caller | Status |
| --- | --- |
| `scripts/demo.py` | Switched to `subprocess.run` of the compiled command; fails loudly on a nonzero exit. See "Known defect" below. |
| `README.md`, `docs/cli-quickstart.md` | Name the compiled command. |
| `scripts/install_framework.py` | Unchanged legacy baseline and the legacy oracle both Rust commands are tested against. Its remaining unported modes are `--manual-evidence` / `--manual-experts-only`. |
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
| `test_hook_default_and_exact_declarations_export_runtime_only` | `hook_default_and_exact_declarations_are_both_read` (install half) and `hook_default_and_exact_declarations_export_runtime_only` (export half) |
| `test_unsupported_hook_declarations_fail_install_and_export_before_writes` | `unsupported_hook_declarations_fail_install_before_writes` (install half) and `unsupported_hook_declarations_fail_export_before_writes` (export half) |
| `test_missing_malformed_duplicate_or_symlink_hook_source_is_rejected` | `missing_malformed_duplicate_or_symlink_hook_source_is_rejected` (install half) and `malformed_hook_sources_and_sidecars_fail_export_before_writes` (export half) |
| `test_empty_default_hook_directory_and_duplicate_manifest_are_rejected` | `empty_default_hook_directory_and_duplicate_manifest_are_rejected` |
| `test_legacy_absence_never_probes_a_runtime` | `legacy_absence_never_probes_a_runtime` |
| `test_delivery_install_requires_explicit_runtime_even_with_path_and_environment` | `delivery_install_requires_explicit_runtime_even_with_path_and_environment` (the `--validator` half is not applicable) |
| `test_delivery_sidecar_rejects_malformed_duplicate_unknown_and_unsupported_values` | `delivery_sidecar_rejects_malformed_duplicate_unknown_and_unsupported_values` (install half) and `malformed_hook_sources_and_sidecars_fail_export_before_writes` (export half) |
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
| `test_delivery_binary_mutation_after_probe_blocks_all_installation_writes` | NOT_RUN, and the rule is **not covered** by the Rust suite. The rule is implemented: `install_framework` recomputes `runtime_digest(selected)` and compares it to the probe report's `sha256_after` immediately before the writes, refusing with `selected runtime binary changed before installation writes` (`src/install.rs`, in `install_framework`, just after the runtime-overlap loop). That is verified by inspection only; deleting it leaves the whole suite GREEN. The legacy case mutated the runtime from inside a mocked `plan_hook_merge`, and no black-box reproduction can land in that window deterministically, so no timing test is added. `delivery_binary_mutation_during_probe_blocks_all_installation_writes` exercises the different, in-probe `before == after` check. The validator half of the same rule is covered directly, through the shared `guard_writes`, by `tests/probe_runtime.rs::a_validator_whose_bytes_changed_since_the_probe_is_refused`. |
| `test_delivery_validator_mutation_after_probe_blocks_all_installation_writes` | NOT_RUN: same reason. `tests/probe_runtime.rs::a_validator_whose_bytes_changed_since_the_probe_is_refused` covers the compiled decision directly. |
| `test_delivery_selected_validator_cannot_be_overwritten_by_installation` | NOT_APPLICABLE: guard check 1 (a destination naming the validator) is still unreachable from `install framework`, and now for a stronger reason — `install_framework` refuses a validating executable inside the project before it reads the framework, on every run, not only when a probe happens. Those refusals are `a_validating_executable_inside_the_project_is_refused_before_execution` (delivery-aware) and `the_validating_executable_placement_is_checked_without_any_requirement` (no requirement); `tests/probe_runtime.rs::a_destination_that_names_the_validating_executable_is_refused` covers check 1 itself, through the same shared `protect_validator`. |
| `test_export_preserves_runtime_and_excludes_authoring_material` | `export_preserves_runtime_and_excludes_authoring_material` |
| `test_delivery_export_retains_dependency_without_executing_runtime` | `delivery_export_retains_the_dependency_without_executing_a_runtime`. The copied sidecar, `runtime_requirements`, `runtime_host`, the `files_sha256` entry and `behavior` are asserted. The **no-probe half is verified by inspection, not by execution**: the legacy case mocked `runtime_requirements.probe_runtime` to raise if called, which has no black-box equivalent, and the Rust case's execution-marker assertion cannot fail because the export takes no `--runtime` and performs no discovery. `export_plugin` contains no `Command::new` and no `probe_runtime` call; the CLI's only spawn is `capability_output`, inside `probe_runtime`, unreachable from this action. |

Added beyond the legacy suite:

- `project_and_framework_must_be_separate` — the new path-hygiene predicate.
- `project_experts_are_added_only_when_selected_and_collide_by_name`.
- `a_recorded_manual_expert_adoption_is_preserved_and_its_flags_are_not_offered` —
  an existing `manual_expert_adoption` record survives an install unchanged, and
  `--manual-evidence` / `--manual-experts-only` are rejected by the parser.
- Export cases: `export_output_selection_is_refused_before_anything_is_created`
  (name, existing entry, dangling symlink, symlinked parent, non-directory
  parent, output inside the source plugin),
  `unsupported_components_and_manifest_names_refuse_before_writes`,
  `a_symlinked_plugin_source_refuses_the_export`,
  `export_accepts_one_provider_and_no_adoption_inputs`, and the export oracle
  `the_legacy_exporter_and_the_compiled_command_agree` (byte-identical trees and
  identical `files_sha256`, `runtime_requirements` and `runtime_host` against
  `python3 scripts/install_framework.py --export-plugin`, for both providers).
- `only_the_providers_that_declared_a_requirement_are_probed` — `--provider both`
  with a requirement on one provider only admits a runtime that supports just
  that provider, records `providers` as only the declaring one, and records no
  evidence for the other; the unchanged legacy installer is asserted to accept
  the same selection identically.
- `legacy_float_hook_inventory_can_be_refreshed` — the PR #14 review
  reproduction of finding P2, copied unchanged from
  `tests/review_install.rs::review_legacy_float_hook_inventory_can_be_refreshed`:
  the legacy installer records a hook whose `timeout` is `0.000001`, and the
  compiled refresh of that unchanged installation must not answer
  `BLOCKED: managed hook definition digest mismatch`.
- `a_float_hook_identity_survives_a_cross_refresh_and_an_edit_is_still_refused` —
  the identity itself: the recorded `sha256` is the digest of the Python
  encoding `{"hooks":[{"command":"true","timeout":1e-06,"type":"command"}]}`, the
  compiled refresh records it unchanged and does not rewrite a semantically
  identical settings document, the legacy installer then accepts what the
  compiled command wrote back, and a genuine edit of the owned group is still
  refused with `local edit/collision in owned claude hook: Stop` before any write.
- `installer_inside_managed_destination_without_requirement_refuses_before_writes`
  — the PR #14 review reproduction of finding P1, copied unchanged from
  `tests/review_install.rs::review_installer_inside_managed_destination_without_requirement_refuses_before_writes`:
  with no runtime requirement declared, an installer sitting at one of the
  installation's own managed destinations refuses with exit 2 and the project
  byte-identical. See parity exception 10 (numbered 8 on the #14 branch).
- `the_validating_executable_placement_is_checked_without_any_requirement` — the
  same check, named: `validating executable must be outside the installation
  project` and `validating executable must be outside the framework`, both on a
  run where nothing declares a runtime requirement.
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
   plus the refusal of a validating executable inside the project or inside the
   framework. A practical consequence: `target/debug/devforge`, which Cargo
   hard-links, can run the installation, though it still cannot be passed as
   `--runtime`. The framework half of that placement check is new: the legacy
   installer never compared `--validator` to `--framework`.
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
5. **Float formatting in a hook group digest — fixed; two residuals remain.**
   `group_digest` no longer hashes `serde_json`'s compact encoding. It hashes the
   bytes `json.dumps(group, sort_keys=True, separators=(",", ":"),
   ensure_ascii=False, allow_nan=False)` produces, floats included: `python_float`
   reproduces CPython's `float.__repr__` (positional, always with a fractional
   part, when the scientific exponent is in `-4..16`; otherwise
   `d[.ddd]e<sign><at least two exponent digits>`). A valid floating `timeout`
   that the legacy installer recorded — `0.000001`, which Python writes `1e-06`
   and ryu wrote `1e-6` — no longer makes the installation unrefreshable. Both
   directions are covered by
   `a_float_hook_identity_survives_a_cross_refresh_and_an_edit_is_still_refused`
   and by the unit test `install::tests::hook_identities_reproduce_the_python_encoding`,
   whose expectations are the captured output of
   `/usr/bin/python3 -c 'import json; print(json.dumps([0.000001,1e16,1.5e-7,0.0001,123456789012345.0,12345678901234567.0,1e100,-0.0,1.0,5],separators=(",",":")))'`
   → `[1e-06,1e+16,1.5e-07,0.0001,123456789012345.0,1.2345678901234568e+16,1e+100,-0.0,1.0,5]`
   (Python 3.12.3). Two differences are introduced by the *decoder*, before any
   encoding, and no encoder can recover them from a `serde_json::Value`:
   - the integer literal `-0` decodes to the float `-0.0` and encodes as `-0.0`,
     where Python keeps an `int` and writes `0`;
   - an integer literal outside `i64`/`u64` range decodes to an `f64` and encodes
     in float form (`12345678901234567890123` → `1.2345678901234568e+22`), where
     Python's arbitrary-precision `int` writes every digit.

   Both are unchanged by this repair and are pinned by the same unit test.
   Neither appears in any provider hook definition; a package that introduced one
   would need this checked.
6. **Path resolution.** `crate::resolved` refuses any symlinked component of
   `--project` or `--framework`; Python's `Path.resolve()` followed them. This
   matches `install manual-experts`.
7. **Refusal wording for malformed or unreadable input.** Both the decoder
   message (`serde_json` versus Python's `json`) and the reader/io message
   differ. The one reachable io case is a missing plugin manifest during an
   export: the legacy script reports `[Errno 2] No such file or directory:
   '<path>'`, and the compiled command reports `cannot read the plugin manifest
   <path>: No such file or directory (os error 2)` — the path is attached
   deliberately so the operator still learns which file was missing, but the
   wording is not the legacy wording. The status, the exit code and the absence
   of writes are identical in every such case.
8. **Export and the global `--project`.** The legacy parser put `--project` and
   `--export-plugin` in one mutually exclusive group, so passing both was an
   argparse error. `--project` is a global flag of this CLI, so
   `install export-plugin` accepts and ignores it, exactly as `install identity`
   does. Nothing is written under the named project; the export goes only to
   `--output`. `export_accepts_one_provider_and_no_adoption_inputs` pins this.
9. **The legacy combination refusal is unreachable.** `export is unaccepted
   staging; requires one provider and excludes project experts/adoption
   evidence` guarded `--export-plugin` combined with `--provider both`,
   `--include-experts`, `--manual-evidence` or `--manual-experts-only`. The
   compiled action offers none of those: the parser rejects each with exit 2 and
   empty stdout, so no input can reach that message.
10. **The validating executable is checked on every run, not only the
   delivery-aware ones.** This is an intentional divergence, requested by the
   owner after the PR #14 review. `scripts/install_framework.py` selects and
   checks its `--validator` only under `if requirements:` (line 257), and calls
   `runtime_requirements.guard_validator` (line 330) only under
   `if runtime_evidence is not None:` (line 319), which nothing but that first
   block can reach. A package with no `hooks/runtime-requirements.json`
   therefore reaches the legacy write loop with no placement or self-protection
   check at all — established by inspection of those three lines, not by running
   it. The compiled command refuses first, before any write and before the
   framework is read, with exit 2 and the project byte-identical.

   What was observed, and is now pinned by
   `installer_inside_managed_destination_without_requirement_refuses_before_writes`:
   with no requirement declared and the running `devforge` sitting at the managed
   destination `<project>/.claude/skills/demo/SKILL.md`, the compiled command
   **at 1cd38a1** wrote `.claude/agents/first.md` and only then failed, with
   `{"reason":"Text file busy (os error 26)","status":"BLOCKED"}` — a refusal
   that did not precede its writes. It now answers
   `{"reason":"validating executable must be outside the installation
   project","status":"BLOCKED"}` with the project unchanged. Runtime capability
   probing itself is unchanged and still conditional.

## Still Python

- `--manual-evidence` / `--manual-experts-only` remain legacy Python; the
  compiled replacement for that path is `devforge install manual-experts`.
- `scripts/install_framework.py`, `scripts/runtime_requirements.py` and
  `tests/test_installer.py` are unchanged and still run. Retirement is a
  separate decision.

## Known defect: `scripts/demo.py` installation is COULD_NOT_RUN

`scripts/demo.py` places each candidate at
`<framework>/.poc/<run id>/<slug>` and installs with
`--provider both --include-experts` and `target/debug/devforge` as both runtime
and validator. That call was already failing before this migration, and it still
fails after it, but the three causes below are not all of the same age: causes 1
and 2 predate this slice and refuse the legacy script today, while **cause 3 is
introduced by this slice** — the `--project`/`--framework` separation check is
parity exception 2, a predicate the legacy installer never applied. Observed on
2026-09-11 against `framework/DevForgeAI` with the demo's own layout and
arguments:

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
   command refuses. This cause is new with this slice; the legacy script does
   not compare the two paths.

The demo's `install(...)` call is switched to the compiled command and fails
loudly, so the defect is visible rather than silent. Repairing it is a
coordinator decision: it needs a candidate root outside the framework, a
single-link runtime copy, and a provider selection (or adoption evidence) that
does not include the promoted Codex packages. `scripts/verify_poc.py` invokes
`demo.py` and inherits this.
