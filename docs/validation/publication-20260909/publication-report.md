# CLI source publication

The five pending CLI files are preserved unchanged on a draft publication branch from baseline `4859a279784f902d5f9dabcb0664907b24b297a5`. Original shared and author checkouts remain untouched. This is source preservation, not runtime qualification or operational installation.

## Changes

- Preserve repository instructions, including TDD, separate worktrees and required Rust tests.
- Preserve reference-parser corrections and discriminating tests for explicit section keys, table boundaries and metadata reference atoms.
- Pass the built runtime to the fixture installer and document the helpers pinned by fixture policies.

## Verification of selected bytes

`source-identities.json` binds the five source files actually checked. Formatting, Clippy, locked build, Rust tests and all **621 Python tests passed**. Actual commands and outputs are under `checks/`; tests ran in this owned publication worktree. Independent review by `timeout_evidence` found no new correctness blocker in the five-file diff and performed no source edits.

The scripted demo **FAILED** against an owned archive of DevForgeAI `d29430beace67a141d4d35c2beb066981060cda4`: `manual adoption evidence is required for promoted Codex packages`. No adoption evidence was manufactured and the installer gate was not changed. No native model runs or operational installation occurred. The PR remains draft.

## Preserved history

`historical-reference-repair/` contains the frozen expected outcomes, supplement, review and original closeout/result. Current parser bytes match the historically reviewed candidate (`f01eda3d5e4964a527e617316df52be63f2d67c9b68a28e52f65807753b2c3ae`). The historical whole run remained FAIL (232 tests; five failures and nineteen errors attributed in that report to AF_UNIX restrictions). Its results are not relabeled by today's successful run. Preimplementation RED was not verified; frozen expectations alone do not establish it.

`detached-validator-preserved.patch` preserves the two-file dirty delta at detached HEAD `6983f1de3e06f9b422546fd6944888dcd34e56a1`. The source traversal implementation and added test cases are already in main. Main also has newer validation behavior, so the old whole files were not imported. The original detached checkout remains dirty and unchanged.

The exact baseline and three historical VPI tips have been read back from GitHub; see `cli-archive-results.json`. Historical branches are preservation refs, not merges or endorsements of deferred orchestration. `cli-public-suitability.json` records the screened committed history and independent publication review.

## Recovery and limits

`evidence-map.json` maps original paths to retained bytes. These are selected public-safe records, not an archive of every file transitively mentioned in historical logs. The original private evidence, ignored runtime/client state and raw transcripts remain local-only; no private off-machine backup destination was supplied. No original evidence or worktree was deleted. Readiness, native qualification and acceptance remain separate from this draft publication.

Whole-diff whitespace checking reports blank/context-line whitespace in the preserved patch and Rust log. Those evidence bytes remain unchanged; the five source files and authored publication report pass the scoped whitespace check.
