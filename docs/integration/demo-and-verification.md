# Fixture demonstration and local verification (compiled)

`devforge demo` and `devforge verify-poc` are the compiled Rust ports of
`scripts/demo.py` and `scripts/verify_poc.py`. Both scripts are unchanged and
remain the legacy baseline.

Neither command evaluates behavior. The demonstration calls no model: its report
records `model_calls: 0` and `model_behavior: NOT_EVALUATED`, and a gate `PASS`
is mechanical evidence that a command exited 0. The verification report keeps
`model_behavior: NOT_EVALUATED` and `hosted_ci: NOT_RUN`. Neither is acceptance,
qualification or release authority.

## `devforge demo`

```bash
devforge demo --framework <DevForgeAI checkout> \
  --policies <dir with notes-sqlite.json and notes-json.json> \
  --output-root <dir outside the framework> \
  [--prepare-only] [--run-id <name>]
```

Everything the run writes lives under `<output-root>/<run-id>/`:

| Path | Contents |
| --- | --- |
| `candidates/<slug>` | the copied `examples/<slug>/seed`, installed and driven |
| `candidates/<slug>-refresh` | the refresh copy, so the accepted original keeps its exact evidence |
| `authority/<slug>` | external gate state for that candidate |
| `runtime/devforge` | a single-hard-link 0755 copy of the running executable |
| `demo-report.json` | the run report |

`--run-id` defaults to `%Y%m%dT%H%M%S-<pid>`; an existing run directory is
refused rather than overwritten. The framework checkout is only read.

For each of `notes-sqlite` and `notes-json` the run copies the seed, installs
with

```
<this executable> --project <candidate> install framework --framework <F> \
  --provider claude --include-experts --runtime <output-root>/<run-id>/runtime/devforge
```

and then drives the gates as subprocesses of the same executable with
`--project/--policy/--state` and a 30-second deadline each:

`expert prepare`, `expert bind --expert experts/<slug>-persistence`, `check`,
`init`; then, unless `--prepare-only`, `green` (expected refusal), copy
`changes/tests/test_store.py`, `red`, copy `changes/src/store.py`, `green`,
`accept`, `verify`; then in the refresh copy, with the story amended,
`expert status`, `check` (expected refusal), `expert bind`, `check`.

Each event records `command`, `exit_code` and the parsed JSON `result`. An exit
status the sequence did not expect is a hard error: the run stops, no report is
written, and the refusal carries the captured output.

Progress lines, `Report:` and `Candidates:` go to stdout. Exit 0 on success;
otherwise `{"status":"BLOCKED","reason":"..."}` on stdout and exit 2, the same
surface `devforge install` uses.

Report fields: `schema`, `run_id`, `runtime`, `runtime_sha256`,
`fixture_execution`, `model_calls`, `scope`, and `projects[]` with `project`,
`policy`, `state`, `events`, `interactive_prompt`, `model_behavior`.

### Three corrections to `scripts/demo.py`

The legacy demonstration had been failing at its installation step before this
migration, for three independent reasons recorded in
[project-local framework installation](framework-installation.md). All three are
corrected here, and each is a deliberate behavioral difference:

1. **Candidates live outside the framework.** The script placed them at
   `<framework>/.poc/<run id>/<slug>` with authority state in the CLI's own
   `.poc/`. `install framework` refuses a project inside the framework, so both
   now live under `--output-root`, which is refused if it is inside
   `--framework`.
2. **The runtime is a single-hard-link copy.** Cargo hard-links
   `target/debug/devforge` (link count 2), which `--runtime` refuses. The run
   copies the running executable to `<run>/runtime/devforge`, sets mode 0755 and
   records the copy's SHA-256 as `runtime_sha256`. The same running executable
   still performs the installation and every gate call.
3. **The installation selects `--provider claude --include-experts`.** The Codex
   plugin ships the promoted `devforge-project-expert-creator` and
   `devforge-evaluate-expert` packages, which need owner-selected adoption
   evidence that this demonstration does not carry; `--provider both` and
   `--provider codex` are both refused for that reason. Only the Claude package
   is installed, so nothing in this run demonstrates the Codex runtime.

## `devforge verify-poc`

```bash
devforge verify-poc --framework <DevForgeAI checkout> --repo <DevForge checkout> \
  [--evidence-root <dir>] [--cargo <path>]
```

Stages run in this order with `cwd = --repo`, a 180-second deadline each,
stopping at the first failure:

| Stage | Command |
| --- | --- |
| `format` | `<cargo> fmt --check` |
| `clippy` | `<cargo> clippy --locked --all-targets -- -D warnings` |
| `build` | `<cargo> build --locked` |
| `tests` | `python3 -m unittest discover -s tests -p 'test_*.py' -v` (the legacy suite, run as a baseline) |
| `framework-structure` | `<this executable> validate framework --framework <F>` |
| `mvp-documents` | `<this executable> validate mvp --mvp <F>/docs/mvp` |
| `fixture-demo` | `<this executable> demo --framework <F> --policies <repo>/policies --output-root <repo>/.poc` |

`--cargo` selects the toolchain; it defaults to `$CARGO` and then to `cargo` on
`PATH`. Select the CI pin explicitly with
`--cargo "$(rustup which --toolchain 1.94.0 cargo)"`.

Evidence goes to `<--evidence-root or <repo>/docs/validation>/<UTC stamp>/` with
one `<stage>.log` per stage that ran (stdout then stderr) and `report.json`:
`schema`, `created_at`, `checks[]` (`check`, `command`, `exit_code`, `status`),
`sources_sha256`, `model_behavior`, `hosted_ci`, `scope`. The inventory covers
both checkouts, keyed `DevForge/<rel>` and `DevForgeAI/<rel>`, excluding any path
with a `.git`, `target`, `.poc` or `__pycache__` component and anything under
`docs/validation`. Exit 2 when any stage fails or a stage did not run.

## Callers

| Caller | Status |
| --- | --- |
| `README.md`, `docs/POC.md`, `docs/cli-quickstart.md` | name the compiled commands |
| `scripts/demo.py`, `scripts/verify_poc.py` | unchanged legacy baseline; not retired |
| `.github/workflows/ci.yml` | unchanged; it does not invoke either script |

## Known blocker: `policies/` has drifted from the framework's Claude package

With the three corrections above applied, the demonstration still refuses
against this repository's own `policies/notes-sqlite.json` and
`policies/notes-json.json`. Observed on 2026-09-11:

```
{"reason":"unexpected gate result: check exited 2 where success=true:
 {\"reason\":\"source outside approved layout:
  .claude/skills/devforge-evaluate-expert/scripts/graders.py\",\"status\":\"BLOCKED\"}",
 "status":"BLOCKED"}
```

Two further independent causes, both in `policies/`, which this change does not
own:

4. **Two installed scripts are unpinned.** A current Claude installation writes
   `.claude/skills/devforge-evaluate-expert/scripts/graders.py` and
   `.../run_cases.py`. `check` requires every code file to be under
   `source_roots`, under `test_root`, or pinned in `tooling_files`, and neither
   policy pins them.
5. **Two pinned files no longer exist anywhere.** Both policies pin
   `.agents/skills/skill-validator/scripts/assess_evidence.py` and
   `.../inspect_skill.py`. The `skill-validator` package was folded into
   `devforge-evaluate-expert`; no provider package installs those paths any
   more, and a Claude-only installation never wrote `.agents/` at all. With
   cause 4 repaired, `check` then refuses with
   `missing tooling file: .agents/skills/skill-validator/scripts/assess_evidence.py`.

Repairing `policies/*.json` is a coordinator decision, so `--policies` is a
parameter of the command rather than a hard-coded path. With a policy directory
whose `tooling_files` pins the three scripts a Claude installation actually
writes, the full run completes: both fixtures reach `VERIFIED + STALE/REFRESH
CHECKED`, exercised by `tests/demo.rs` and by the recorded run in
`tmp/rust-migration-20260911/w2d/`.

`verify-poc` inherits this: its `fixture-demo` stage names `<repo>/policies`. It
does not reach that stage today, because `framework-structure` fails first on
the companion checkout's own pre-existing defects (see
[framework structure validation](framework-structure-validation.md)).

## Parity exceptions

Beyond the three corrections and the policy blocker above:

1. **`--framework`, `--policies` and `--output-root` are explicit.** The script
   defaulted `--framework` to a sibling `DevForgeAI` and hard-coded the policy
   and output roots relative to the script's own location. Nothing is inferred
   from the executable's path now.
2. **`--run-id` is new.** It makes an evidence directory reproducible; the
   default format is unchanged.
3. **Refusal surface.** The script raised `RuntimeError` and exited 1 with a
   traceback. Both commands print
   `{"status":"BLOCKED","reason":"..."}` on stdout and exit 2.
4. **Report additions.** `runtime` and `runtime_sha256` are new fields recording
   the copy the installation selected. Every legacy field is unchanged.
5. **`verify-poc` toolchain selection.** The script always used `cargo` from
   `PATH`. `--cargo`/`$CARGO` now allow the CI pin to be named explicitly; the
   default is unchanged.
6. **JSON key order.** `serde_json` maps are key-sorted, so `sources_sha256`
   and each parsed gate `result` come back sorted rather than in Python's
   insertion order. The report's own fields keep the legacy order.
7. **Copy semantics.** `copy_tree` refuses a symlink inside a fixture instead of
   following it, and preserves file modes as `shutil.copytree` did.

## Test mapping

Neither script had a test suite; `tests/demo.rs` and `tests/verify_poc.rs` are
new coverage rather than a port of existing cases.

| Case | What it proves |
| --- | --- |
| `demo::prepare_only_initializes_both_fixtures_outside_the_framework` | report shape, both fixtures INITIALIZED, state under `authority/`, the runtime copy's link count 1, mode 0755 and recorded digest, and `examples/` byte-identical afterwards |
| `demo::a_full_run_reaches_verify_and_the_refresh_rebind` | the whole thirteen-event sequence per fixture, including the two deliberate refusals (`green` without RED, the stale expert) and the preserved accepted original |
| `demo::an_output_root_inside_the_framework_is_refused_before_any_write` | correction 1, with nothing created |
| `demo::a_missing_policy_is_refused_before_any_candidate_is_written` | every policy is read before the first copy |
| `demo::an_existing_run_directory_is_refused` | prior evidence is never overwritten |
| `demo::an_unexpected_gate_status_stops_the_run_and_writes_no_report` | a refusing `check` stops the run instead of being recorded as an event |
| `verify_poc::the_first_failing_stage_stops_the_run_and_exits_two` | stop-at-first-failure, one log, the exact `cargo fmt --check` argv and `cwd` |
| `verify_poc::the_cheap_stages_run_in_order_before_the_structural_ones` | stage order, the real `framework-structure` failure recorded rather than swallowed, the legacy unittest baseline running in the selected repository |
| `verify_poc::the_report_records_scope_and_a_source_inventory_with_the_legacy_exclusions` | report fields, the UTC stamp shape, and the `.git`/`target`/`.poc`/`__pycache__`/`docs/validation` exclusions at any depth |
| `verify_poc::evidence_defaults_into_the_selected_repository_when_no_root_is_given` | the default evidence root |
| `verify_poc::a_missing_repository_or_framework_is_refused_without_evidence` | selections are checked before an evidence run is created |

## Status

Partial migration. `scripts/demo.py` and `scripts/verify_poc.py` are unchanged
and still run; their Python authority is not claimed to satisfy the development
language policy, and retiring them is a separate decision. `policies/*.json`
needs the repair described above before either the compiled or the legacy
demonstration can complete against the current framework checkout.
