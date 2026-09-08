# Manual Codex expert adoption

This implements the F06 adoption prerequisite for `devforge-project-expert-creator` and `devforge-evaluate-expert`. The trusted installer requires current evidence before its operational writes. It does not intercept authoring phase order, arbitrary shell/editor writes, user invocation, or native execution. The user initiates skills, transfers their real artifacts, and initiates commands. Automated orchestration remains deferred; managed v1 workflow IDs and funded-launch refusals are unchanged.

## Scope and commands

The integration owner refreshes only the promoted Codex packages:

```text
python3 scripts/install_framework.py --framework FRAMEWORK --project PROJECT --provider codex --manual-experts-only --manual-evidence ADOPTION_RECORD
```

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
