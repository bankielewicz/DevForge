# Legacy Python test retirement

Retirement is authorized only where compiled Rust coverage passes first and no
boundary, malformed-input, refusal or file-preservation assertion is lost or
becomes a silent skip. This records what was checked, what was retired, and what
was deliberately kept.

**Outcome: one of the four candidate suites was retired.** `tests/test_validation.py`
is gone. `tests/test_installer.py`, `tests/test_manual_expert_adoption.py` and
`tests/test_manual_local_baseline.py` stay, for the two reasons in "Kept back".

`scripts/*.py` are untouched and keep running as the oracles the Rust suites
compare against.

## Counts

| Measure | Before (`db4b281`) | After |
| --- | --- | --- |
| `python3 -m unittest discover -s tests -p 'test_*.py'` | 630 | 583 |
| Python suites discovered | 21 | 20 |
| Skipped Python cases | 0 | 0 |
| `cargo test --locked --all-targets` | 263 across 8 binaries, 0 failed | unchanged |

583 = 630 − 47, the exact case count of the retired suite.

## Retired

### `tests/test_validation.py` (47 cases)

Replaced by `tests/validate_framework.rs` (53 cases). The case-by-case mapping is
the table in
[framework structure validation](framework-structure-validation.md#test-mapping),
which covers all 47: 46 mapped to a named Rust case and
`StructuralGateTest.test_optimized_python_cannot_disable_validation` recorded as
`NOT_APPLICABLE` (the compiled command has no `assert` statements and no
`python3 -O` equivalent, so the property it protected holds by construction).

No assertion is lost. `tests/validate_framework.rs` additionally runs the
unchanged `scripts/validate_framework.py` as a compatibility oracle over 18
synthetic trees, the companion checkout and its real authored hook packages, so
the Python validator is still executed on every `cargo test` run — it simply no
longer carries the authority.

## Kept back

### `tests/test_installer.py` (47 cases) — kept for an import dependency, not a coverage gap

Its own coverage is complete: all 47 cases are mapped in
[project-local framework installation](framework-installation.md#legacy-test-mapping),
to `tests/install_framework.rs` (54 cases) and `tests/probe_runtime.rs`, with
four rows recorded there as `NOT_RUN`/`NOT_APPLICABLE` and justified before this
slice.

It cannot be deleted on its own because **both manual suites import it**:

```
tests/test_manual_expert_adoption.py:9: import test_installer
tests/test_manual_local_baseline.py:9:  import test_installer
```

Deleting it produced `ModuleNotFoundError: No module named 'test_installer'`,
two loader errors and a discovered count of 508 instead of 583 — 28 live cases
silently stopped running, which is exactly the failure mode this retirement is
supposed to prevent (`w2g/after-deletion.log`, `w2g/after-python.log`). It
therefore retires only together with the two suites below, or after their shared
fixtures are moved.

### `tests/test_manual_expert_adoption.py` (18) and `tests/test_manual_local_baseline.py` (12)

`docs/integration/manual-expert-adoption.md` has no mapping table, so the
mapping below was built by reading all 30 legacy cases against the 84 cases of
`tests/manual_install.rs` plus `tests/install_framework.rs`. **28 of 30 are
covered. Two are not, and neither has a pre-existing documented exclusion**, so
per the retirement rule both suites stay.

### `tests/test_manual_expert_adoption.py` (18 cases)

| Legacy case | Rust coverage |
| --- | --- |
| `test_exact_full_evidence_installs_and_records_custody` | `manual_install.rs::exact_full_evidence_installs_and_records_custody` |
| `test_bounded_routine_can_be_adopted_without_qualification` | `manual_install.rs::bounded_routine_can_be_adopted_without_qualification` |
| `test_creator_phase_and_evaluator_task_cannot_be_omitted` | `manual_install.rs::creator_phase_and_evaluator_task_cannot_be_omitted` |
| `test_self_review_and_optional_classification_are_refused` | `manual_install.rs::self_review_and_optional_classification_are_refused` |
| `test_missing_evidence_does_not_become_pass_from_summary` | `manual_install.rs::missing_evidence_does_not_become_pass_from_summary` |
| `test_stale_candidate_or_leaf_evidence_refuses_before_writes` | `manual_install.rs::stale_candidate_or_leaf_evidence_refuses_before_writes` |
| `test_full_trigger_cannot_be_relabelled_routine` | `manual_install.rs::full_trigger_cannot_be_relabelled_routine` |
| `test_full_requires_actual_transfer_references` | `manual_install.rs::full_requires_actual_transfer_references` |
| `test_duplicate_record_and_wrong_destination_are_refused` | `manual_install.rs::duplicate_record_and_wrong_destination_are_refused` |
| `test_last_moment_evidence_drift_preserves_existing_installation` | **NONE — blocker.** Mock-injected mid-window mutation (`mock.patch.object(installer, 'plan_hook_merge')` rewrites the evidence file after preflight). No black-box reproduction lands in that window. Nearest: `manual_install.rs::stale_candidate_or_leaf_evidence_refuses_before_writes`, `changed_source_identity_is_refused_before_writes` |
| `test_export_is_unaccepted_staging_without_circular_adoption_requirement` | `install_framework.rs::export_preserves_runtime_and_excludes_authoring_material` (asserts `adoption: NOT_ACCEPTED_STAGING` on the compiled `install export-plugin`) |
| `test_case_catalog_cannot_be_dropped_from_mutually_consistent_summaries` | `manual_install.rs::case_catalog_cannot_be_dropped_from_mutually_consistent_summaries` |
| `test_install_cannot_invalidate_its_own_accepted_evidence` | `manual_install.rs::install_cannot_invalidate_its_own_accepted_evidence` |
| `test_malformed_nested_record_is_structured_refusal` | `manual_install.rs::malformed_nested_record_is_structured_refusal` |
| `test_routine_cannot_invent_an_accepted_baseline` | `manual_install.rs::routine_cannot_invent_an_accepted_baseline` |
| `test_targeted_install_preserves_other_skills_agents_and_hook_inventory` | `manual_install.rs::targeted_install_preserves_other_skills_agents_and_hook_inventory` |
| `test_install_cannot_invalidate_evidence_through_hardlink_alias` | `manual_install.rs::install_cannot_invalidate_evidence_through_hardlink_alias` |
| `test_native_catalog_assertion_cannot_be_relabelled_deterministic` | `manual_install.rs::native_catalog_assertion_cannot_be_relabelled_deterministic` |

### `tests/test_manual_local_baseline.py` (12 cases)

| Legacy case | Rust coverage |
| --- | --- |
| `test_owner_approved_local_set_installs_exact_unqualified_baseline` | `manual_install.rs::owner_approved_local_set_installs_exact_unqualified_baseline` |
| `test_remaining_cases_cannot_be_omitted_or_relabelled_pass` | `manual_install.rs::remaining_cases_cannot_be_omitted_or_relabelled_pass` |
| `test_failed_selected_check_blocks_even_with_passing_summary` | `manual_install.rs::failed_selected_check_blocks_even_with_passing_summary` |
| `test_plan_must_precede_observations` | `manual_install.rs::plan_must_precede_observations` |
| `test_manual_handoff_cannot_be_replaced_with_prepared_text` | `manual_install.rs::manual_handoff_cannot_be_replaced_with_prepared_text` |
| `test_source_only_addition_refuses_before_writes` | `manual_install.rs::source_only_addition_refuses_before_writes` |
| `test_historical_failure_changes_refuse_before_writes` | `manual_install.rs::historical_failure_changes_refuse_before_writes` |
| `test_exact_owner_acceptance_cannot_be_downgraded_to_generic_install` | `manual_install.rs::exact_owner_acceptance_cannot_be_downgraded_to_generic_install` |
| `test_author_cannot_supply_independent_review` | `manual_install.rs::author_or_native_actor_cannot_supply_independent_review` |
| `test_last_moment_source_only_addition_is_rechecked` | **NONE — blocker.** Mock-injected mid-window mutation (`mock.patch.object(Evidence, 'recheck')` adds a source file between the first and second recheck). Nearest: `manual_install.rs::source_only_addition_refuses_before_writes`, `changed_source_identity_is_refused_before_writes` |
| `test_native_actor_cannot_grade_its_own_observation` | `manual_install.rs::author_or_native_actor_cannot_supply_independent_review` (one Rust case carries both legacy cases) |
| `test_local_baseline_cannot_refresh_unrelated_framework_components` | `install_framework.rs::a_recorded_manual_expert_adoption_is_preserved_and_its_flags_are_not_offered` — NOT_APPLICABLE in form: the compiled command offers no `--manual-evidence`/`--manual-experts-only`, so the refused combination cannot be expressed; the preservation half is asserted |

### The two blockers

Both are mock-injected mid-window mutations: the legacy case patches an
installer internal so the evidence changes *between* the preflight and the
writes. No black-box test can land in that window deterministically. This is the
identical argument already accepted for
`test_delivery_binary_mutation_after_probe_blocks_all_installation_writes` in
[framework installation](framework-installation.md#legacy-test-mapping), but
that acceptance was recorded there in advance; these two were not. Creating the
justification and acting on it in the same change is what the retirement rule
forbids, so the decision is referred rather than taken.

Both rules are implemented and partially covered at the normal entry point
(`stale_candidate_or_leaf_evidence_refuses_before_writes`,
`source_only_addition_refuses_before_writes`,
`changed_source_identity_is_refused_before_writes`). What is lost on retirement
is specifically the *timing* assertion, not the predicate.

The coordinator has two ways forward: accept the `NOT_RUN` exclusions in
`manual-expert-adoption.md` with the reasoning above and retire all three
remaining suites together, or commission a Rust port that can reach the window.

## Evidence

| What | Log |
| --- | --- |
| Base GREEN before any deletion (Rust 263, Python 630) | `w2g/baseline.log`, `w2g/baseline-python-full.log` |
| The four-suite deletion attempt that broke discovery | `w2g/after-deletion.log`, `w2g/after-python.log` |
| Final state, 583 OK, 0 skipped | `w2g/after-deletion-final.log`, `w2g/final-python.log` |
| `test_utility_schedule.py` base flake probe | `w2g/base-utility-schedule-probe.log` |

`git diff --stat db4b281 -- scripts` is empty, and the oracle invocations are
still present: `tests/install_framework.rs` names `install_framework.py` 5 times
and `tests/validate_framework.rs` names `validate_framework.py` twice.

## A pre-existing base defect, unrelated to this change

At `db4b281`, before any deletion,
`test_utility_schedule.UtilityScheduleTests.test_compiled_delivery_embeds_and_routes_schedule_modules`
failed once in the full discover run with

```
utility operation clock moved behind its journal high-water mark
```

It passed 3 of 3 isolated runs of its own suite and did not recur in the final
full run, so it is order- or timing-dependent rather than deterministic. That
suite is not part of this retirement and was not touched. Flagged because CI
runs the full discover and would fail on it.
