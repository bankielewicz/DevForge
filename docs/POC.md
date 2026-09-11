# POC contract and terminal runbook

## Ownership

DevForgeAI owns the project-facing skills, agent definitions, and examples. DevForge owns this executable, policy, fixed test harness, installer, and GitHub workflows. During application/framework candidate work, the external authority terminal selects the DevForge executable, policy, and run state. A worker may propose a policy amendment; it cannot make one effective by editing its candidate.

Use absolute paths from the demo report in the commands below. Bracketed values are parameters to replace with those actual paths, not installed defaults.

## Implementation language and legacy commands

The [development language policy](development-language-policy.md) requires compiled Rust in DevForge CLI for framework implementation and every phase, gate, validator, mutation broker and acceptance decision. Python JSONL skill-evaluation runners and deterministic graders are mandatory, bound evaluation artifacts; they produce evidence and metrics, while protected, digest-pinned Rust validates and decides acceptance. All other implementation languages are forbidden.

Existing Python framework logic, tooling and tests **must be ported into DevForge CLI as Rust**. The commands and examples below describe current legacy behavior and may run unchanged when authorized; Python authority results do not establish compliance with the Rust requirement. Preserve those results and gates while the migration remains outstanding. Do not substitute Python for new Rust framework behavior or silently waive an affected acceptance prerequisite. This documentation does not implement a replacement runner, evaluation artifacts, or binary protection.

## First run

1. In DevForge, run `cargo build --locked` and `python3 scripts/verify_poc.py --framework ../DevForgeAI`.
2. Run `python3 scripts/demo.py --framework ../DevForgeAI --prepare-only`. This creates initialized copies of the SQLite and JSON-file examples, with local skills and agents for both providers.
3. Select one generated project and read its external policy, story, expert specification, and current expert skill. Keep its report available in the authority terminal.
4. Start one protected interactive client from DevForge:

```bash
target/debug/devforge isolate --project <absolute-project> --runtime codex -- codex
```

or:

```bash
target/debug/devforge isolate --project <absolute-project> --runtime claude -- claude
```

The runtime option mounts a private client home backed by `<project>/.devforge-runtime/`. It does not copy your existing login. Sign in through the client's supported subscription flow. Codex uses ChatGPT account sign-in; Claude Code uses the Claude subscription account. API-key environment variables are removed by this launcher. Do not add API credentials when your intended mode is subscription-only. Credential storage under `.devforge-runtime/` is private, Git-ignored, and excluded from gate snapshots.

Run the runtime setup before initializing a new custom project: the launcher adds its ignore rule if missing. The supplied demo seeds already include that rule. Custom `CLAUDE_CONFIG_DIR` is rejected by this POC; Codex's configured client-home location is respected through a mount rather than changing the host configuration.

A plain `codex` or `claude` launched outside this wrapper has different permissions. The wrapper is a filesystem boundary, not a network/security guarantee for every integration. Do not expose Docker/admin sockets or host-side mutation tools to the worker.

## Exercise project expertise

In the selected terminal:

```text
Use devforge-project-expert-creator for this project. Inspect docs/expert-spec.md,
the project architecture, and its external policy. Improve the declared project
expert skill, verify its sources, and record structural and behavioral status
separately. Do not edit external DevForge controls or claim an evaluation ran
unless you actually performed it.
```

For a completely new skill, remove nothing from a live project: use a fresh copy and specify the missing expert directory in its externally approved policy. The skill creator authors the expert specification and SKILL.md in the current subscribed conversation. The CLI does not call a model or generate a generic expert persona.

`expert prepare` returns the task context and required expert directories. `expert bind` stores exact project/policy/upstream/expert hashes. `expert status` returns MISSING, CURRENT, or STALE, always with the separate behavioral status. Rebinding preserves prior bindings under the expert's `history/` directory. CURRENT means its recorded inputs still match; it does not mean the skill is effective.

Any expertise change after initialization requires a new external run directory. This intentionally prevents reusing test evidence across changed governing context.

## Exercise TDD and local acceptance

The agent edits the candidate, while the authority terminal runs these commands with the fixed report paths:

```bash
target/debug/devforge init --project <project> --policy <policy> --state <new-external-state>
target/debug/devforge red --project <project> --policy <policy> --state <state>
target/debug/devforge green --project <project> --policy <policy> --state <state>
target/debug/devforge accept --project <project> --policy <policy> --state <state>
target/debug/devforge verify --project <project> --policy <policy> --state <state>
```

The prepared demo has already run init; do not initialize that same state again. Add the new behavioral tests, obtain RED, then implement production changes and obtain GREEN. Do not edit tests between RED and GREEN. The demo's `changes/` files are reference fixtures for the scripted proof, not an independent model evaluation.

The authority runner checks any preexisting baseline tests before initialization. RED permits only test changes against that baseline. GREEN permits only the declared source changes and the exact RED test bytes/modes. Changed upstream documents, policy, expert files, installed instructions, or unrelated files invalidate the run's applicable checks.

A test error, skipped case, empty suite, unavailable isolation, or timeout does not become valid RED. The test process has a 10-second wall deadline and CPU/memory/file-size limits. The fixed harness is embedded in the Rust binary. Executable test/code honesty and whether a failure expresses the intended requirement still require independent review.

Acceptance preserves the exact GREEN tree in external state. Verify compares the archive, recorded GREEN manifest, and current candidate. It checks file bytes and Unix permission modes. A changed candidate, changed policy, or archive tampering prevents verification. The CLI does not merge, push, deploy, or post a GitHub status.

## Installation

To install core skills and provider agent definitions into an existing project before starting a run:

```bash
python3 scripts/install_framework.py --framework ../DevForgeAI --project <project> --provider both --include-experts
```

Codex skills go to `.agents/skills`; Claude skills go to `.claude/skills`. Agents go to `.codex/agents` or `.claude/agents`. The installer records managed file hashes, permits repeat installation and refresh of unmodified managed copies, and blocks collisions/local edits. It removes previously managed authoring-only files only when their recorded hashes still match; local edits block removal. Other retired files are retained, and global marketplaces are unchanged. Installation is preflighted but is not a transactional deployment system; interrupted writes should be inspected before retry.

Sources are selected from `<framework>/providers/<provider>/plugins/devforgeai`; the old shared source is retired. Authored evals and fixtures stay in source and are excluded from normal installations. Runtime templates and helpers still ship.

A delivery-aware package additionally requires `--runtime <absolute devforge>` and `--validator <absolute devforge>`, two independently selected executables (the same file is permitted). The installer runs `<validator> install probe-runtime --runtime <runtime> --provider <provider>`; that compiled path owns the runtime hygiene checks, the bounded `delivery capabilities` execution and the capability contract, admitting only the eight-field `devforge.delivery-capabilities/v1` base set or that base plus all seven declared extensions. The Python installer validates nothing itself, records the probe report as the runtime evidence, and writes nothing when the Rust CLI refuses. Note that `cargo` hard-links `target/debug/devforge`, so select a single-link copy for either role.

For a Claude native plugin test, first export to a new directory whose basename is devforgeai:

```bash
python3 scripts/install_framework.py --framework <framework> --provider claude --export-plugin <new-export-parent>/devforgeai
claude --plugin-dir <new-export-parent>/devforgeai
```

Launch from the intended disposable consuming project. Do not also install the same skill project-locally in that test environment. Exporting bytes is not native installation/discovery evidence; record the actual loaded paths and client behavior. Use a fresh export after source changes. Codex project-local installation remains the MVP baseline; the exporter can also create a Codex plugin, but no global marketplace is registered here. Helpers are invoked through their interpreter because the POC copy/export preserves content rather than executable mode bits.

The explicit --include-experts option retains the portable `experts/<skill>` fixture convention and does not synthesize separate provider-specific experts. See the sibling skill-authoring contract for new expert assignments.

The compiled Rust installer covers only the manual adoption of the promoted Codex expert workflows; see [manual expert adoption](integration/manual-expert-adoption.md) for the record shapes and the owner-controlled `--authority` pin:

```bash
target/debug/devforge install identity
target/debug/devforge install manual-experts --project <project> --framework ../DevForgeAI --evidence <adoption-record> --authority <authority-record>
```

Every other installation mode above remains legacy Python until it is ported.

## Recovery and acceptance scope

State-changing commands take an exclusive `.lock`. A stale lock requires the owner to confirm the recorded process is no longer running before removing it. A leftover `state.next.json` indicates an interrupted transition; retain and inspect it, then choose whether to recover or initialize a new run. No automatic force-unlock or gate-skip option exists.

Initialization does not overwrite an existing run. Current POC transitions are explicit and do not automatically retry after interrupted writes. An accepted directory from an interrupted acceptance can be reused only if its exact manifest matches the still-GREEN candidate.

The optional external policy field `tooling_files` maps exact installed helper paths to SHA-256 and mode. A declared helper is permitted outside application source roots only when those fingerprints match. It cannot overlap editable source/test scope. Changed bytes/mode, missing helpers, and undeclared executable files are rejected; tooling is never made an editable source root. The two fixture policies pin the selected Claude `.claude/skills/devforge-brainstorm/scripts/check_artifact.py` helper and Codex `.agents/skills/skill-validator/scripts/assess_evidence.py` and `.agents/skills/skill-validator/scripts/inspect_skill.py` helpers. A later skill refresh requires the external policy owner to review any helper change and initialize new evidence; the worker cannot update these pins to pass.

The synthetic policies use a small `dependencies.json` contract and literal prohibited source tokens. They do not parse real NuGet/npm/Cargo dependency graphs. Production adoption requires runner and stack adapters, stronger semantic evaluation, an authority deployment/identity model, and tested handling of all enabled external tool surfaces.

## You are here

| Work | Owner | Status / completion check |
| --- | --- | --- |
| Local Rust gates, installation, two project fixtures | POC builder | Implemented; rerun `scripts/verify_poc.py` for exact evidence. |
| Native package structure and isolated binary smoke checks | POC builder | Checked locally; see validation notes. |
| Subscribed interactive expert creation and behavioral evaluation | User + terminal AI | Next: sign in within a prepared project and run the expert-creator task above. |
| Production enforcement and hosted governance | Framework owner | Not established by this POC. |

Ordered continuation: verify the local POC, prepare a fresh candidate, sign in through one terminal, exercise the expert creator, evaluate its result in a separate session, then decide the next capability or enforcement adapter to implement.
