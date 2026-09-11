# `devforge delivery status`: the compiled phase-state reader

Status of this document: implementation record for the first Rust slice of the
phase/gate runtime. It describes what `devforge delivery status` now decides in
compiled Rust, what the unchanged legacy Python runtime still decides, and the
exact dependencies that block the remainder. Nothing here claims that the
migration of `runtime/delivery/` is complete.

## The command

```bash
target/debug/devforge delivery status --state <absolute-state-directory>
```

`status` is read-only. It reports the persisted phase view of one hash-bound
journal (`MANIFEST.json`, `HEAD.json`, `records/`, `snapshots/`, `pending/`) and
grants nothing: it issues no admission, publishes no receipt and never mutates
the state root. Its stdout is `serde_json::to_string_pretty` of the result
object; the exit code is `2` when `status` is `FAIL`, `COULD_NOT_RUN`, `BLOCKED`
or `STALE`, otherwise `0`. Neither the rendering nor the exit rule changed.

## What is ported

`src/phase_state.rs` ports the read path of `runtime/delivery/phase_state.py`:

| Legacy Python | Rust |
| --- | --- |
| `phase_state.context` | `phase_state::context` |
| `phase_state._State.__init__` / `_apply` | `State::load` / `State::apply` |
| `phase_state._checkpoint` | `State::parse_checkpoint` |
| `phase_state._verify_current` | `verify_current` |
| `phase_state._view` / `_result` / `_applicability` | `view` / `result_value` / `applicability` |
| `phase_state._configuration` (v1, non-initial) | `configuration` |
| `phase_state._read_at` / `_external` / `_object_directory` | `read_at` / `external` / `object_directory` |
| `phase_state._lock` | `lock` / `acquire` |
| `delivery_core._directory` / `_parent` / `_read_at` / `_read_external` | `directory` / `parent` / `core_read_at` / `core_external` |
| `delivery_core._inspect_destination` | `inspect_destination` |
| `delivery_core._load_contract` (`devforge.delivery-task/v1`) | `load_contract` |
| `delivery_core.prepare` (v1 outcome) | `prepare` |
| `delivery_core._text` / `_relative` / `_absolute` / `_exact` / `_digest` / `_revision` / `_sections` | `text` / `relative` / `absolute` / `exact` / `hex_digest` / `revision` / `sections` |
| `str.isspace()` / `str.strip()` | `python_isspace` / `python_strip` |
| `workflow_runtime.engine_for_state` (phase engine only) | `phase_engine` |

The compiled reader answers a `status` call when all of the following hold:

* `MANIFEST.json` declares `devforge.brainstorm-state/v1`;
* the journal's bound delivery contract is `devforge.delivery-task/v1`;
* the replayed status is `ACTIVE`, `WAITING_USER` or `FAIL`;
* every refusal it reaches has wording this port reproduces exactly.

It holds the journal's exclusive `flock` for the whole read, so a contended
journal is now refused in Rust rather than handed to Python.

Otherwise `phase_state::status` returns `None` and `src/delivery.rs` runs the
unchanged `runtime/delivery/controller.py` exactly as before. No refusal is
weakened, skipped or reworded: an unported condition is answered by the legacy
authority, not by a Rust approximation.

## What stays Python, and why

| Unported decision | Concrete dependency |
| --- | --- |
| `advance`, `resume`, `complete`, `init` and every `native-*` action | Mutation broker slices; not in this slice's scope. |
| `status` on a `READY` or `COMPLETED` journal (`_current_final`, `_verified_receipt`) | Needs `delivery_core.check` / `verify` / `finalize`: the Markdown heading parser, the YAML frontmatter envelope, `_ledger_binding` and the receipt schema. Roughly 900 further lines of `delivery_core.py`. |
| `status` on a `devforge.delivery-task/v2` journal | Needs `_catalog_contract`, `catalog_sources`, `_reference_coverage`, `_v2_markdown`, `validate_assignment_reference` — the whole v2 reference-coverage engine. |
| `status` on a `devforge.utility-state/...` journal | Needs `runtime/delivery/utility_state.py` (1318 lines). |
| CPython JSON decoder diagnostics quoted by `delivery_core._json`, and `strerror` text quoted by `delivery_core._os_problem` | Byte-identical wording would require reimplementing CPython's `json` error positions and the libc message table. The reader delegates instead. |

### The exclusive lock

`phase_state._lock` opens `<state>/LOCK` through the state-root directory
descriptor with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC`, checks that it
is an empty regular single-link file whose identity did not change across the
open, takes `fcntl.flock(LOCK_EX | LOCK_NB)`, and re-checks the identity
afterwards. `phase_state::lock` performs exactly that sequence in the same
order, and `phase_state::acquire` takes the lock with
`rustix::fs::flock(&file, FlockOperation::NonBlockingLockExclusive)`:

* `EWOULDBLOCK` (`EAGAIN`) reproduces the legacy `BlockingIOError` branch:
  `COULD_NOT_RUN` with `another protected phase operation owns the exclusive
  lock`, exit code `2`. Contention is now decided in Rust and no longer
  delegates to Python.
* `EINTR` is retried, matching CPython's PEP 475 behaviour.
* Every other `errno` falls through to the reader's `os_problem` mapping, which
  is `delivery_core._os_problem(exc, "protected state lock", "FAIL")`: the
  `ELOOP`/`ENOTDIR`/`ENOENT` wordings are reproduced exactly and anything else
  takes the unexpected-errno branch and delegates so that the `strerror` text
  stays exact.

The acquired lock is held by the `std::fs::File` returned from `lock` for the
whole of `context`, so mutual exclusion covers the entire read, and it is
released when that file is dropped on **every** exit path. That includes the
paths that hand the call back to the legacy controller (a `READY`/`COMPLETED`
journal, a v2 contract): `context` returns, its lock file drops, and only then
does `src/delivery.rs` spawn `controller.py`, which takes the same lock itself.
`tests/phase_state.rs::the_exclusive_lock_is_released_after_every_read` pins
both the successful and the delegating case by re-acquiring the lock from the
test process afterwards.

This uses the `fs` feature of the already pinned `rustix = "=1.1.4"`, enabled
in `Cargo.toml` by commit `ea22280`; `Cargo.lock` is unchanged. It needs no
`unsafe` and no `rust-version` above the declared `1.85` floor
(`std::fs::File::try_lock` would have required 1.89 and fails
`clippy::incompatible_msrv`).

### CPython whitespace is wider than Rust's

Rust's `char::is_whitespace` is the Unicode `White_Space` property. CPython's
`str.isspace()` additionally treats U+001C, U+001D, U+001E and U+001F as space,
and `str.strip()` removes them. Enumerating both sets over all of Unicode
(`/usr/bin/python3 -c 'print([hex(i) for i in range(0x110000) if chr(i).isspace()])'`)
gives exactly those four as the difference, with nothing in the reverse
direction.

That matters wherever the legacy runtime calls `.strip()` on attacker-supplied
journal content. `phase_state._string` treats a value of only such characters as
empty and refuses it; so does the `correction` record's issues check; and
`delivery_core._text` rejects it before its control-character branch can produce
a different message. A reader using `str::trim` would **accept** a pending
`question` made of U+001C and answer `WAITING_USER` with exit 0 where the legacy
authority answers `FAIL` with exit 2 — the defect review K found and this slice
now fixes. `python_isspace`/`python_strip` are used at every such call site
(`text`, `bounded_string`, `stamp`, and the correction-issues check); no
`str::trim` call remains in `src/phase_state.rs`. The predicate is pinned
against the enumerated CPython set by the unit test
`phase_state::tests::python_isspace_matches_the_enumerated_cpython_set`, and the
behaviour by `a_checkpoint_question_of_python_only_whitespace_is_refused`,
`a_correction_issue_of_python_only_whitespace_is_refused` and
`a_contract_field_of_python_only_whitespace_is_refused_with_the_legacy_wording`,
each of which tampers with a committed journal object and re-chains it.

## Reading through held directory descriptors

The Python runtime never resolves a path by name twice: it opens `/`, walks each
component with `O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC`, keeps the
resulting descriptor, and performs every subsequent `stat`/`open`/`listdir`
with `dir_fd=`. A symlink or directory swapped in mid-operation therefore cannot
redirect the read.

Rust `std` exposes no `openat`, and the pinned dependency set offers no
alternative without `unsafe`. `src/phase_state.rs` keeps the same property by
addressing the held descriptor through procfs:

* each directory is opened into a `Dir(std::fs::File)` with
  `OpenOptionsExt::custom_flags(O_DIRECTORY | O_NOFOLLOW)`;
* a child is addressed as `/proc/self/fd/<fd>/<name>`, which the kernel resolves
  through the descriptor rather than by re-walking the original path, with
  `O_NOFOLLOW` still applying to the final component;
* `fs::read_dir("/proc/self/fd/<fd>")` replaces `os.listdir(dir_fd)`;
* file reads additionally re-check the
  `(dev, ino, size, mtime, mtime_nsec, ctime, ctime_nsec)` identity before the
  open, after the `fstat`, and after the readback, at the same three points as
  `phase_state._read_at`. `delivery_core._identity` also carries `st_nlink`;
  the Rust tuple does not, because the link count is asserted separately on both
  the `lstat` and the `fstat` (`nlink() != 1` is its own refusal) and any change
  to it also moves `ctime`.

This requires a mounted `/proc`, which the runtime already requires (the
supervisor uses pidfds). `tests/phase_state.rs::a_symlinked_journal_component_is_refused_exactly_as_before`
and `::a_state_root_reached_through_a_symlink_component_is_refused` pin the
refusals.

## Parity exceptions

Every row states its pinning test, or `NOT_PINNED` with the reason no
deterministic fixture exists.

| Condition | Behaviour | Pinned by |
| --- | --- | --- |
| Absent state root | The CLI boundary (`checked_path`/`project_for` in `src/delivery.rs`) refuses before any engine is selected, with its own wording. Pre-existing; unchanged by this slice. | `a_missing_or_unreadable_state_root_is_refused_exactly_as_before` |
| State root without a readable `MANIFEST.json`, or a manifest above 1 MiB | Same CLI boundary refusal; the legacy engine's own wording (`workflow manifest: ...`) is recorded in the test but is never reached through the CLI. Pre-existing. | `a_missing_or_unreadable_state_root_is_refused_exactly_as_before`, `a_malformed_manifest_or_head_is_refused_exactly_as_before` |
| A state root that is not absolute | `NOT_APPLICABLE` at the CLI: `--state` is absolutised by `crate::resolved` before either implementation sees it. `phase_state::absolute` still applies the `delivery_core._absolute` rules to the resolved value. | `NOT_PINNED` — the condition cannot be reached through the CLI, so no fixture can exhibit it. |
| Unparsable JSON in any read object | Delegated, so the CPython decoder message is emitted verbatim. | `a_malformed_manifest_or_head_is_refused_exactly_as_before` |
| An unexpected `errno` (anything but `ENOENT`, `ELOOP`, `ENOTDIR`) | Delegated, so `strerror` wording stays exact. | `NOT_PINNED` — provoking a specific `errno` (`EACCES`, `EMFILE`, `ENOMEM`) deterministically from a test needs privileges or resource exhaustion this suite does not take. |
| A snapshot `bytes` value that is a JSON number outside `i64` | Delegated. CPython's `type(x) is int` accepts an arbitrarily large integer and fails later at the blob length comparison; without serde_json's `arbitrary_precision` (which would move `Cargo.lock`) a huge integer and a float are the same `Number`, so both are delegated rather than reworded. A float is therefore delegated too, trading a little more delegation for exact stdout. | `a_snapshot_byte_count_outside_i64_is_answered_exactly_as_before` |
| `_configuration`'s `implementation` collision check | Python binds `Path(__file__).parent`, the materialised module cache; Rust binds the same `package` directory computed by `src/delivery.rs`. Identical under the CLI. When the legacy controller is run directly from the source tree (as the test oracle does), its `implementation` is `runtime/delivery` instead, so a state root or receipt placed inside either directory would diverge. | `NOT_PINNED` — the oracle harness itself is what diverges, so a test using that oracle cannot express the expectation. No fixture places a state root or receipt inside either directory. |

## Legacy-to-Rust test mapping

`tests/test_phase_state.py` (59 cases) and `tests/test_phase_references.py`
(26 cases) are unchanged and still required. 33 of the 85 exercise the read path
(`context`, `_verify_current`, `_view`, reference validation on read); the other
52 exercise mutation or initialization paths only.

| Legacy case | This slice |
| --- | --- |
| `test_phase_state.test_context_is_read_only_and_keeps_durable_challenge` | `the_reader_changes_no_byte_mode_or_timestamp_under_the_state_root` |
| `test_phase_state.test_handoff_only_admits_recover_then_focus_and_records_skips` | `the_legacy_controller_and_the_compiled_status_agree` (handoff-only view) |
| `test_phase_state.test_one_content_repair_then_success_is_permitted` | `the_legacy_controller_and_the_compiled_status_agree` (one spent correction) |
| `test_phase_state.test_second_content_failure_durably_terminates_admission` | `the_legacy_controller_and_the_compiled_status_agree` (terminal FAIL view) |
| `test_phase_state.test_waiting_user_preserves_question_phase_and_no_receipt` | `the_legacy_controller_and_the_compiled_status_agree` (WAITING_USER view) |
| `test_phase_state.test_waiting_checkpoint_requires_question_and_blocking_dependency` | `the_legacy_controller_and_the_compiled_status_agree` (pending question replay) |
| `test_phase_state.test_checkpoint_strict_shapes_types_and_bounds` | `the_legacy_controller_and_the_compiled_status_agree`, `every_intermediate_phase_view_matches_the_legacy_controller` (checkpoint replay on read) |
| `test_phase_state.test_record_uses_actual_selected_ledger_not_claimed_path_or_hash` | `every_intermediate_phase_view_matches_the_legacy_controller` (Record view) |
| `test_phase_state.test_future_phase_cannot_skip_even_with_valid_final_outputs` | `every_intermediate_phase_view_matches_the_legacy_controller` |
| `test_phase_state.test_previous_valid_checkpoint_is_replay_and_cannot_advance_twice` | `other_delivery_actions_still_reach_the_python_controller` (advance stays Python), view parity via `the_legacy_controller_and_the_compiled_status_agree` |
| `test_phase_state.test_resume_active_recovers_same_phase_with_fresh_challenge` | view ported; `resume` itself `NOT_APPLICABLE` (mutation path, next slice) |
| `test_phase_state.test_complete_before_ready_does_not_publish_or_skip` | view ported; `complete` itself `NOT_APPLICABLE` (mutation path, next slice) |
| `test_phase_state.test_session_strict_schema_and_baseline_selection` | `a_changed_contract_or_installed_pin_is_refused_exactly_as_before` |
| `test_phase_state.test_session_duplicate_json_keys_and_size_limit` | `a_malformed_manifest_or_head_is_refused_exactly_as_before` (strict JSON, bounded read) |
| `test_phase_state.test_accepted_checkpoint_snapshot_corruption_or_deletion_fails_reopen` | `an_object_whose_digest_does_not_match_its_name_is_refused` |
| `test_phase_state.test_record_artifact_snapshot_corruption_fails_despite_valid_live_files` | `an_object_whose_digest_does_not_match_its_name_is_refused` |
| `test_phase_state.test_missing_durable_state_records_cannot_reset_task` | `a_committed_record_removed_from_the_journal_cannot_reset_the_task` |
| `test_phase_state.test_expiry_prevents_new_transition_and_context_does_not_reset_deadline` | `an_expired_deadline_reports_historical_context_only` |
| `test_phase_state.test_four_phase_path_binds_independent_output_hashes_and_completes` | intermediate views: `every_intermediate_phase_view_matches_the_legacy_controller`; READY/COMPLETED: `a_ready_journal_is_still_answered_by_the_legacy_delivery_checks` (delegated) |
| `test_phase_state.test_repeated_completion_verifies_and_retains_receipt_identity` | delegated (COMPLETED); `a_ready_journal_is_still_answered_by_the_legacy_delivery_checks` |
| `test_phase_state.test_receipt_changed_after_readback_cannot_commit_completion` | delegated (COMPLETED) |
| `test_phase_state.test_selected_preimage_missing_at_admission_never_creates_archives` | `NOT_APPLICABLE` (initialization path, next slice) |
| `test_phase_state.test_archive_collision_is_preserved_and_initialization_not_admitted` | `NOT_APPLICABLE` (initialization path, next slice) |
| `test_phase_state.test_full_preflight_precedes_archive_directory_creation` | `NOT_APPLICABLE` (initialization path, next slice) |
| `test_phase_references.test_active_v1_journal_cannot_upgrade_to_v2_by_rebinding_contract` | `a_v2_reference_contract_is_still_answered_by_the_legacy_runtime` |
| `test_phase_references.test_legacy_v1_full_phase_stays_v1_and_record_bytes_can_evolve` | intermediate views ported (`every_intermediate_phase_view_matches_the_legacy_controller`); READY/COMPLETED delegated |
| `test_phase_references.test_full_v2_phase_uses_second_listed_primary_and_binds_receipt_report` | delegated (v2 reference coverage) |
| `test_phase_references.test_bad_reference_at_record_cannot_advance_and_one_correction_can_repair` | delegated (v2) |
| `test_phase_references.test_record_cannot_filter_missing_directory_or_bad_nonprimary_ledger` | delegated (v2) |
| `test_phase_references.test_null_execution_ref_is_not_managed_record_authority` | delegated (v2) |
| `test_phase_references.test_accepted_reference_report_corruption_cannot_be_rederived_on_reopen` | delegated (v2) |
| `test_phase_references.test_same_byte_catalog_alias_reselection_cannot_change_accepted_record_binding` | delegated (v2) |
| `test_phase_references.test_active_v2_resume_preserves_accepted_record_and_original_deadline` | delegated (v2) |

Counts: 33 read-path cases — 16 ported and pinned by a Rust test, 5 partially
ported (the read view is ported, the mutation the case also drives is not),
9 delegated and pinned by a delegation test, 3 `NOT_APPLICABLE`
(initialization path, next slice). The remaining
52 cases are `NOT_APPLICABLE` to this slice with the reason
"mutation path, next slice": every `test_phase_state` and `test_phase_references`
case that does not call `context`. No Python test was retired, skipped or
changed.

Neither `tests/test_phase_state.py` nor `tests/test_phase_references.py`
exercises lock contention, so porting the acquisition does not move any row of
the table above and the 16/5/9/3 split over the 33 legacy read-path cases is
unchanged. What changed is the reader's own delegation set. A held exclusive
lock left it for the ported set and is now pinned by a parity test rather than a
delegation test; review K's F2 added a snapshot `bytes` number outside `i64`.
The set now holds five conditions, each pinned:

| Delegated condition | Pinned by |
| --- | --- |
| A `READY` or `COMPLETED` journal | `a_ready_journal_is_still_answered_by_the_legacy_delivery_checks` (second half: an accepted Focus output drifts, so the legacy authority answers `FAIL`/2 where a reader skipping the delegation would answer `READY`/0) |
| A `devforge.delivery-task/v2` contract | `a_v2_reference_contract_is_still_answered_by_the_legacy_runtime` |
| A state schema that is not `devforge.brainstorm-state/v1` | `an_unsupported_workflow_state_schema_is_still_answered_by_the_controller` |
| CPython JSON decoder diagnostics | `a_malformed_manifest_or_head_is_refused_exactly_as_before` |
| A snapshot `bytes` number outside `i64`, and `strerror` diagnostics | `a_snapshot_byte_count_outside_i64_is_answered_exactly_as_before`; the `errno` half is `NOT_PINNED` (see Parity exceptions) |

Additional Rust coverage with no single legacy counterpart:
`a_symlinked_journal_component_is_refused_exactly_as_before`,
`a_state_root_reached_through_a_symlink_component_is_refused`,
`an_unexpected_or_oversized_journal_object_is_refused`,
`a_replaced_or_unusable_lock_is_refused_exactly_as_before`,
`a_held_exclusive_lock_is_refused_exactly_as_before`,
`the_exclusive_lock_is_released_after_every_read`,
`a_checkpoint_question_of_python_only_whitespace_is_refused`,
`a_correction_issue_of_python_only_whitespace_is_refused`,
`a_contract_field_of_python_only_whitespace_is_refused_with_the_legacy_wording`,
`a_snapshot_byte_count_outside_i64_is_answered_exactly_as_before`,
`an_existing_receipt_without_a_completion_intent_is_refused`,
`a_leftover_partial_publication_is_inspected_without_recovery`,
`an_unsupported_workflow_state_schema_is_still_answered_by_the_controller`,
`the_ported_status_path_does_not_start_the_python_controller`.

## Evidence that Python is not started

`the_ported_status_path_does_not_start_the_python_controller` uses two
observations, both recorded here because neither is a syscall trace (`strace` is
not installed in this environment):

1. **Timing, binary against itself.** Two fixtures differ only in whether the
   reader answers: an ACTIVE v1 journal (ported) and the same journal rebound to
   a `devforge.delivery-task/v2` contract (delegated). Both pay the identical
   CLI boundary and runtime-cache verification, so the only difference is the
   `/usr/bin/python3` spawn. `fastest_status` takes the minimum of **seven**
   complete runs on each side, and the assertion is an **absolute** margin:
   `ported + PYTHON_START_UP < delegated`, with `PYTHON_START_UP` at 20 ms. A
   margin rather than a ratio, because load inflates the delegated side more, so
   the margin only widens where a ratio would narrow.
2. **Source audit.** The test reads `src/delivery.rs` and asserts that the
   `crate::phase_state::status` call site appears before the
   `python(&package, "controller.py")` command is constructed, so the reader is
   consulted first and returns without building the controller command.

`a_held_exclusive_lock_is_refused_exactly_as_before` repeats observation 1 for
the contended refusal: with a real `flock(1)` holder in place, the minimum of
seven compiled runs against the locked journal plus the same 20 ms margin must
still be under the minimum of seven compiled runs against a delegating fixture. Before the lock was ported this
assertion failed at a ratio of about 1.15 (`w3a/lock-red.log`), because the
contended path was itself spawning Python.

`other_delivery_actions_still_reach_the_python_controller` asserts that
`delivery check` still produces the legacy `delivery_core` result and that
`delivery advance` still commits through the Python controller.

Note that `src/delivery.rs` still materialises and verifies the read-only
runtime code cache before the reader runs, because `_configuration` binds that
directory as the protected implementation path. Cache materialisation is not a
Python process.

## Reproducing

From this worktree:

```bash
cargo +1.94.0 fmt --check
cargo +1.94.0 clippy --locked --all-targets -- -D warnings
cargo +1.94.0 build --locked
cargo +1.94.0 test --locked --all-targets
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

RED/GREEN evidence for the slice is in
`tmp/rust-migration-20260911/w3a/` (`red.log`/`green.log` for the reader,
`lock-red.log`/`lock-green.log` for the exclusive lock).

`tests/phase_state.rs` builds every fixture with the legacy runtime only
(`devforge delivery init`, then `devforge delivery advance` against authored
checkpoints) under `$TMPDIR`, and compares each `devforge delivery status` run
against

```bash
python3 -I -B runtime/delivery/controller.py status --state <state>
```

re-serialised exactly as the CLI prints its own result. Both the stdout bytes
and the exit code must match. Run the slice alone with:

```bash
cargo +1.94.0 test --locked --test phase_state
```

### A host clock artifact this suite can surface

On this WSL2 host the wall clock steps **backwards** after sustained load. A
45-second sample taken while `cargo build` ran recorded one backwards step of
2.09 s (`2026-09-11T13:47:28.086569+00:00` followed by
`2026-09-11T13:47:25.996217+00:00`). The legacy runtime reads that clock through
`phase_state.utc_now`, writes `started_at_utc` at `delivery init` and an
`at_utc` at each `delivery advance`, and then refuses its own commit on replay
with `journal timestamp precedes task initialization` when the second stamp
precedes the first. This was observed on the first suite run after a rebuild.

It is an environment defect in the legacy mutation path, not a phase-state
regression, and it is not masked: `Fixture::advance` fails with a message naming
the cause so a reviewer is not misled. `an_expired_deadline_reports_historical_context_only`
polls the legacy engine's own `expired` flag instead of sleeping a fixed span,
so a backwards step cannot flip that fixture. The compiled reader is unaffected:
it reads the clock once, only to compare `now >= deadline`.
