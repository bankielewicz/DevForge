# Manual Codex expert adoption

This implements the F06 adoption prerequisite for `devforge-project-expert-creator` and `devforge-evaluate-expert`. The trusted installer requires current evidence before its operational writes. It does not intercept authoring phase order, arbitrary shell/editor writes, user invocation, or native execution. The user initiates skills, transfers their real artifacts, and initiates commands. Automated orchestration remains deferred; managed v1 workflow IDs and funded-launch refusals are unchanged.

## Scope and commands

The integration owner refreshes only the promoted Codex packages:

```text
python3 scripts/install_framework.py --framework FRAMEWORK --project PROJECT --provider codex --manual-experts-only --manual-evidence ADOPTION_RECORD
```

The same manual-only path exists as compiled Rust in the DevForge CLI, which owns the evidence validation, acceptance predicates and installation writes without consulting Python:

```text
devforge install manual-experts --project PROJECT --framework FRAMEWORK --evidence ADOPTION_RECORD --authority AUTHORITY_RECORD
```

`devforge install manual-experts` implements only this mode: the recognized Codex packages present in the framework, `.agents/skills` destinations, authoring-only retirement, local-edit/collision refusal, the record shapes and refusal reasons below, and the `.devforge-install.json` inventory (`files`, `managed_hooks`, `runtime_evidence` and other existing fields are preserved; `manual_expert_adoption` is updated exactly as the legacy installer records it). Whole-framework installation, Claude, `--include-experts`, delivery runtime probes, hook merging and plugin export are not implemented in Rust; the CLI does not accept those options and makes no claim about them. Refusals print the JSON `BLOCKED` object with exit 2 before any installation write; success prints `INSTALLED` with `authority: "compiled Rust CLI; no Python consulted"`, `behavior: NOT_EVALUATED` and the verified `protected_identity`.

The additional `--authority` record is the owner-controlled binding required by the [development language policy](../development-language-policy.md). It must be outside the project and the framework and has exactly:

```json
{
  "schema_version": "devforge.manual-install-authority/v1",
  "owner": "ACTUAL_INTEGRATION_OWNER",
  "executable": {"path": "/protected/devforge", "sha256": "ACTUAL_SHA256"},
  "source_sha256": "ACTUAL_SOURCE_IDENTITY"
}
```

`devforge install identity` prints the running executable's canonical path and SHA-256 together with `source_sha256`, the content identity of `Cargo.toml`, `Cargo.lock`, `build.rs`, `src/`, `runners/` and `runtime/delivery/` that `build.rs` embeds at compile time. Before any evidence is read, and again immediately before installation writes, the CLI refuses unless its own canonical path and digest equal the pinned executable and its embedded source identity equals `source_sha256`. A rebuilt binary, a copy at another path, or a build from a changed source tree cannot satisfy an unchanged authority record even when it reports the same version. This self-check is not tamper protection on its own: the owner must launch the pinned binary from a location the evaluated agent cannot write (for example under `devforge isolate`, where only the project is writable) and keep the authority record, framework and evidence records outside that boundary. The identity binds file content only; it does not cover the Rust toolchain or a Git revision.

The preflight also inspects destination aliases. Every existing destination inode is collected with the payload it would receive; an inode reachable through two destinations with differing payloads is refused (`destination alias would receive conflicting replacement payloads`), an inode that is the selected authority record or the running executable is refused whatever the payload, and an inode that is pinned evidence is refused unless the payload equals the pinned digest. After the evidence recheck, the authority record and executable are verified again and must match the values checked before evidence was read. A symlinked promoted skill source is refused (`missing or symlink source directory`), as the legacy installer refuses it, rather than being omitted from the selection. Timestamps keep the contract the legacy guard accepted through `datetime.fromisoformat`, including basic forms such as `20260908T120001+0000`, ISO week dates (week 53 only in a long ISO year, so `2021-W53-1` is refused as the legacy guard refused it), a fraction after the last time component, and offsets down to fractional seconds such as `+00:00:01.5`, which shift the compared instant. The offset uses the same `HH[[:]MM[[:]SS]][.frac]` grammar as the time (`+00:01.5` is 60.5 s, `+2359.5` is 23 h 59 min 0.5 s); minute or second fields above 59 are summed as the legacy parser summed them, and the total must stay under 24 hours. As in the legacy parser, an offset fraction counts only when the whole offset is non-zero: `+00:00:00.5` and `+00:00.5` are UTC, so a set frozen at `12:00:00.2+00:00:00.5` is still after a `12:00:00.1+00:00` start and is refused as not predefined. A missing offset remains `invalid timestamp`.

Rust acceptance tests in `tests/manual_install.rs` drive the compiled binary with synthetic Full, Routine and local-baseline fixtures equivalent to the legacy Python cases, identity refusals (changed executable digest, changed source identity, a copied executable at another path, an authority record inside the project or framework), and the alias, symlink and timestamp regressions above; two of those invoke the unchanged legacy installer only as a compatibility baseline. The unchanged Python suites still exercise the legacy Python installer and are not evidence for the Rust path. The Rust tests cannot inject the mid-run evidence drift that the Python mock-based cases simulate; the final rechecks exist but that race is not black-box tested.

Destination checks and writes are path based (check-then-use), in Rust as in the legacy installer. A process that can write to the project concurrently could replace a checked directory with a symlink between the preflight and a write, and nothing here defends against that. Operational precondition: run `install manual-experts` from the authority terminal only while the project is quiescent, meaning no evaluated agent, `devforge isolate` session or other writer holds write access to that project for the duration of the command. No existing DevForge mechanism establishes or verifies this: the gate lock in `<state>/.lock` covers gate runs, not installation, and `isolate` confines a client to the project rather than stopping it writing there. Until the owner meets that precondition procedurally or a separately assigned bounded Rust write-containment change (directory-descriptor-relative, no-follow opens) is delivered, treat concurrent project mutation during installation as an unresolved prerequisite, not a protected case.

`--manual-experts-only` selects recognized promoted packages present in the chosen framework and preserves other skills, agents, hook settings and runtime inventories. It cannot combine with `--include-experts` or a different provider. Without this flag, ordinary installation retains its existing provider/runtime checks and also requires adoption evidence when either promoted Codex identity is included. Recognized names cannot opt out by omitting a candidate-owned profile. Claude is unchanged.

The record must be selected by the actual integration owner. Its named producer strings and digests cannot authenticate people or prove model behavior. The independent reviewer and operator must examine actual retained outputs, source/history/output boundaries, native observations and real user-mediated transfer before selecting acceptance. A synthetic fixture, advisory reducer PASS, or the package author's report cannot provide that authority. Conditional installation authority already supplied by the user need not be requested again after its conditions pass.

For pre-adoption native resource testing, `--export-plugin NEW/devforgeai --provider codex` remains available. It reports `adoption: NOT_ACCEPTED_STAGING` and omits source-only evals. It does not enable a package in this project's operational discovery directory. Treat manually loading an exported plugin as an allocated test action, not operational acceptance. No managed advance/resume/complete fallback is used.

All evidence refusals return the existing JSON `BLOCKED` with exit 2 before installation writes. Missing/extra files, unsupported or duplicate record data, stale pins, incomplete case/phase/task evidence, changed classifications, self-review, unsupported Routine eligibility and missing Full receiving references refuse. Existing local-edit/hook/runtime collision checks remain. The final preflight rechecks evidence and refuses writes that would invalidate its own selected evidence. Installed bytes come from the validated in-memory plan. The installation inventory records the selected adoption record's identity and owner.

## Record shapes

All `Pin` values are exactly `{path, sha256}`, with canonical absolute paths and lowercase SHA-256 of preserved files. Pin bytes must exist and remain current. Authoritative review, acceptance and top-level adoption records must be outside the candidate framework and installation skill/settings directories. A selected evidence outbox elsewhere in the consuming project is supported. The installer does not modify evidence.

`devforge.manual-expert-adoption/v1` has exactly:

- `schema_version`, `project_root` (exact absolute destination), `owner` (actual integration owner), `authorization` (Pin to existing actual user/owner authorization), `packages` (one row per recognized package being installed, no extras or duplicates).
- Each package row has exactly `name`, `manifest`, `specification`, `plan`, `cases`, `creator`, `results`, `decision`, `review`, `acceptance`. All except `name` are Pins.

The runtime manifest has exactly `schema_version: devforge.expert-runtime-manifest/v1`, `name`, and `files_sha256` (complete package-relative runtime file/digest map). It must match precisely the bytes the installer plans to write; evals/history/caches remain source-only. The evaluation's candidate identity binds this runtime manifest. Source structural checks retain their separate full source manifest.

Creator completion has exactly `schema_version: devforge.expert-creator-completion/v1`, `author`, `candidate` (the runtime manifest Pin), `specification` (the same Pin as the adoption row), and `phases`. The latter has exactly Intake, Selection, Design, Authoring, PreparedTransfer. Each has exactly `classification: Enforced` and a nonempty `evidence` Pin array. These records preserve required actions and their actual outputs; a checker does not judge their semantic adequacy.

The evaluator uses the existing generic `devforge.skill-validation-plan/v2`, `devforge.skill-validation-results/v2`, `devforge.skill-validation-decision/v2` and `devforge.skill-ai-review/v2` records. The promoted evaluator documents their full shapes and advisory reducer. This installer consumes their adoption-relevant evidence and requires matching candidate/specification/plan/cases/review/results/decision identities and run ID. Do not use this guard's subset of inspected fields as a replacement schema for producing those records.

The original case catalog uses the existing `cases` or legacy `evals` array. Each case has an `id` and its existing `required_observations`, `expectations`, or single `frozen_discriminator`. Every assertion has a row in the plan's `catalog_assertions`, with matching source Pin, case ID and exact JSON pointer, including every unselected/excluded assertion. Catalog rows and selections must have identical ID inventories. Original C/B/A cases retain N evidence requirements. Additional non-JSON specification anchors must resolve their actual text. The independent selection reviewer assesses complete meaning, variants, arms, applicability and requirement dependencies; mechanical projection does not replace that judgment.

The installer checks fixed P1/T01–T02, P2/T03, P3/T04, P4/T05–T08, P5/T09, P6/T10–T12 coverage and Enforced classification. Required tasks need satisfied evidence; reviewed native exclusions retain NOT_RUN or NOT_APPLICABLE, never native PASS. Required assertions need intact observations and appropriate grade references. Full retains D/S/C/B/A obligations and real target-output/receiver-contract/receiver-observation/completed-action references. The actual operator checks the truth of those observations, including actual user-mediated receiving; the installer does not implement collector authentication.

Routine requires the selected accepted baseline and scope, fixed qualified or accepted unqualified cumulative anchor, linked prior acceptance, immediate/cumulative diff references, CP-01–CP-04 compatibility dispositions, independent selection review and a matching eligible Routine decision. Consequential CI-05/06, unbounded CI-09, explicit Full triggers or a Full claim cannot be relabeled Routine. Routine never moves the qualified anchor. All R01–R10 review records remain present with outcomes and evidence. Author, evaluator and reviewer identities are distinct; actual context independence remains observed evidence, not a name-string guarantee.

Install acceptance is an external owner record with exactly:

```json
{
  "schema_version": "devforge.expert-install-acceptance/v1",
  "owner": "ACTUAL_INTEGRATION_OWNER",
  "project_root": "/absolute/project",
  "package": "devforge-evaluate-expert",
  "action": "install",
  "inputs": {
    "manifest": {"path": "/preserved/runtime-manifest.json", "sha256": "ACTUAL_SHA256"},
    "specification": {"path": "/preserved/spec.md", "sha256": "ACTUAL_SHA256"},
    "plan": {"path": "/preserved/plan.json", "sha256": "ACTUAL_SHA256"},
    "cases": {"path": "/preserved/cases.json", "sha256": "ACTUAL_SHA256"},
    "creator": {"path": "/preserved/creator.json", "sha256": "ACTUAL_SHA256"},
    "results": {"path": "/preserved/results.json", "sha256": "ACTUAL_SHA256"},
    "decision": {"path": "/preserved/decision.json", "sha256": "ACTUAL_SHA256"},
    "review": {"path": "/preserved/review.json", "sha256": "ACTUAL_SHA256"}
  },
  "observation_basis": "operator-reviewed actual evidence"
}
```

This is an illustrative unpopulated shape, not approval or evidence. The record cannot hash itself. Its exact Pin enters the adoption record after actual acceptance. Neither JSON generation nor a matched owner name establishes an actual owner decision.

## Verification and limitations

Focused tests exercise the real installer with synthetic records: successful exact-byte Full/Routine paths; missing phases/tasks, candidate/evidence drift, missing/omitted assertions, invalid producer/classification/selection, absent transfer, duplicate/malformed input, refusal atomicity and preservation of unrelated configuration. They prove those mechanical predicates only. Native qualification and actual user-mediated skill transfers require separately retained real observations under the accepted Full policy.

Before retiring old operational prototype directories, preserve their complete bytes and inventories, recheck for user edits and compare the selected source mapping. Retirement is limited to identified obsolete skill-builder/skill-validator copies and older creator-only files; no generic cleanup occurs here. Refresh and verify the new installed manifest through the supported installer, then retire verified obsolete copies as an integration-owner action. Do not leave duplicate discoverable workflows and call migration complete.

## Owner-approved unqualified local baseline

The owner may select a separate bounded local acceptance set for the exact pair of manual-mode packages. This is an additional installation claim, not a change to the existing Routine/Full decision rules. It requires the same `--manual-experts-only --manual-evidence RECORD` command and produces `acceptance_status: LOCAL_ACCEPTANCE_SET_PASS`, `qualification_status: UNQUALIFIED` in the result and installation inventory. It cannot authorize an ordinary whole-framework refresh. No `FULL_PASS`, qualification anchor, automatic orchestration or native hook claim follows from it.

The acceptance set freezes before measured observations and requires nine named checks: `package_integrity` (D), `installed_resources` (D), `independent_semantics` (S), and `grounded_creation`, `reuse`, `bounded_enhancement`, `missing_evidence_refusal`, `creator_to_evaluator`, `evaluator_to_creator` (N). Its owner selects concrete expectations, exact package/specification/catalog identities and positive time/native-turn limits before execution. The semantic reviewer checks the meaning and actual evidence; a weak expectation cannot be made sufficient by its hash. Compatible observations may support more than one check, with separate judgments. Native checks require actual Codex observations, complete transcripts/artifacts and observed state isolation. Both receiving checks require actual user initiation, real producer artifacts, observed receiver consumption and completed action. Prepared transfer text is insufficient.

The original five creator phases and six evaluator phases/twelve tasks keep their Enforced classification. This local installation prerequisite does not assert universal phase interception or full qualification of those workflows. The creator remains authoring-only; the evaluator remains read-only toward its target. Full qualification is separate. The local result leaves every original source-catalog case `NOT_RUN` for qualification, even where a related local observation exists. A later Full evaluator can assess compatible raw evidence without rewriting this local result. Historical failures are pinned and retained.

### Local record shapes

All references use the same canonical `Pin` and final freshness/overwrite checks described above. No record hashes itself. The integration owner selects genuine authority and acceptance; fixture data cannot establish either. The installer verifies declared identities and bindings, not the authenticity of people, timestamps or native observations. Actual evidence review remains necessary.

- `devforge.manual-expert-local-baseline/v1`: exactly `schema_version`, `project_root`, `owner`, `authorization`, `packages`, `acceptance_set`, `results`, `review`, `acceptance`, `historical_evidence`. The last field is a nonempty list of Pins to preserved prior evidence; other record references are Pins. Both recognized package names must be present exactly once.
- Each package row: exactly `name`, `manifest`, `source_manifest`, `specification`, `cases`, `author`. The runtime manifest keeps the existing shape and must match installation bytes. The source manifest is exactly `{source_root, files_sha256}`, covering every current canonical package file except Python caches. Source-only additions/deletions also invalidate it. `cases` binds that source's `evals/evals.json`. The guard rechecks the full source inventory immediately before installation.
- `devforge.manual-local-acceptance-set/v1`: exactly `schema_version`, `project_root`, `owner`, `authorization`, `packages`, `checks`, `frozen_at_utc`, `max_seconds`, `max_native_turns`, `historical_evidence`. Identity/authority/history fields equal the adoption record. `checks` maps the nine fixed IDs to `{kind, expectations}`; expectations is a nonempty list of concrete strings. Limits are positive integers. Time is timezone-aware ISO 8601. The set's frozen time precedes the results interval, and every native observation lies within that interval.
- `devforge.manual-local-acceptance-results/v1`: exactly `schema_version`, `acceptance_set`, `qualification_status: UNQUALIFIED`, `checks`, `qualification_cases`, `started_at_utc`, `finished_at_utc`, `native_turns`. `checks` maps each fixed ID to `{outcome: PASS, evidence: [Pin], native_observation: Pin|null}`. D/S use null; N binds its observation among its evidence. `qualification_cases` maps each package name to its complete original case-ID map, with every value `NOT_RUN`. Report actual native turns, including continuations; elapsed time and turns cannot exceed the predefined limits. Never reset the interval or discard a failed attempt to fit.
- `devforge.manual-local-observation/v1`: exactly `schema_version`, `acceptance_set`, `outcome: PASS`, `packages` (name-to-runtime-manifest Pin map), `actor`, `native_client: codex`, `model`, `reasoning_effort`, `state_isolation` (Pin), `transcript` (Pin), `artifacts` (nonempty Pin list), `started_at_utc`, `finished_at_utc`, `manual_transfer`. A receiving observation uses `{direction, user, user_request, producer_output, receiver_observation, completed_action}`; direction is the corresponding check ID, user names the actual initiating user, and the remaining fields are Pins to real evidence. Non-receiving observations may use null.
- `devforge.manual-local-review/v1`: exactly `schema_version`, `reviewer`, `independence_evidence` (Pin), `overall: PASS`, `criteria`, `acceptance_set`, `packages`, `check_judgments`. Reviewer differs from the authors, native actors and integration/test owner; actual context independence must be observed. `criteria` contains R01–R10 and `check_judgments` contains all nine check IDs. Each value is `{outcome: PASS, reason, evidence: [Pin]}`. Each check judgment includes the actual result evidence. Review inputs are the frozen set, packages and raw observations, so no result/review self-digest cycle is needed.
- `devforge.manual-local-owner-acceptance/v1`: exactly `schema_version`, `owner`, `action: install_unqualified_local_baseline`, `qualification_status: UNQUALIFIED`, `inputs`, `observation_basis: operator-reviewed actual evidence`. `inputs` is the complete adoption object before its `acceptance` field is added. Its owner matches the selected record. Conditional user installation authority remains usable after its conditions have actually passed; do not fabricate the owner's decision or ask the same authorization again.

These local records are not inputs to `assess_evidence.py`'s VPR-2 qualification reducer. The installer consumes them only for the explicitly unqualified local claim. Keep any separately produced Full/Routine plan, results and decision unchanged. A failed local check or unavailable required observation blocks local installation, while reporting and historical preservation remain possible. Native execution still needs its own selected bounded allocation; local adoption permission does not launch an old campaign.
