# DevForge CLI POC

External Rust gates and workflow tooling for the companion DevForgeAI framework.

Repository: https://github.com/bankielewicz/DevForge

The repository is published on GitHub with `main` as its default branch. Hosted execution is configured in `.github/workflows/`: the CLI checks workflow runs on pushes to `main` and on pull requests, and the framework-revision workflow is dispatched manually from `main` against an exact DevForgeAI commit. Nothing here describes branch protection or release readiness.

## Implementation language

**Framework implementation and all phase, gate, validator, mutation and acceptance authority must be compiled Rust in DevForge CLI. Python is mandatory only for the skill-evaluation JSONL runner and deterministic graders, bound as required build artifacts; all other implementation languages are forbidden.** See the [development language policy](docs/development-language-policy.md) for evidence binding and protected, digest-pinned Rust authority.

Existing Python framework runtime, tooling and tests **must be ported into DevForge CLI as Rust**. The Python commands below describe current legacy implementation, not a completed migration or permission to extend Python authority. Unchanged tools remain available for authorized operation; their successful execution does not establish compliance with the new authority boundary.

## Run it

First use: follow the [WSL CLI quickstart](docs/cli-quickstart.md) to build with the CI-selected Rust toolchain (1.94.0), locate `target/debug/devforge` (it is not placed on `PATH`), and run a harmless structural check. Building does not install DevForgeAI skills into a project.

Requirements for the legacy full verification below: Linux/WSL2, Rust/Cargo (tested with 1.94.0), Python 3.12, and bubblewrap with usable filesystem/PID namespaces. No model API key is required.

Full verification and demonstration (not the first-use check), from this directory:

```bash
cargo build --locked
target/debug/devforge verify-poc --framework ../DevForgeAI --repo "$PWD" \
  --cargo "$(rustup which --toolchain 1.94.0 cargo)"
```

The verification command runs the Rust checks, the legacy Python acceptance and installer suites, the framework structure and MVP document checks, and the fixture demonstration, stopping at the first failure. It writes a source manifest and per-stage logs under `docs/validation/`. The demonstration writes candidates, authority state and its runtime copy under the output root it is given; the framework checkout is only read.

For a prepared project to use interactively:

```bash
target/debug/devforge demo --framework ../DevForgeAI --policies policies \
  --output-root .poc --prepare-only
```

Read the printed `demo-report.json` for exact project, policy, state, and prompt values. See the [terminal runbook](docs/POC.md) for the two-terminal workflow and subscription login.

## What is implemented

- External JSON policy with strict fields and declared source/test scope.
- Project-specific expert context, provenance binding, history, and staleness detection.
- Baseline -> RED -> GREEN -> local accepted snapshot -> integrity verification.
- Real isolated Python unittest execution; empty, skipped, broken, and failing baseline suites are rejected.
- SHA-256 and filesystem-mode binding of candidate files, tests, upstream inputs, and policy.
- External state locks, exclusive initialization, snapshot readback, and durable state-file replacement.
- A filesystem/PID-isolated command launcher with private per-project Codex or Claude state.
- Compiled provider-specific project-local installation (`devforge install framework`), preserving local edits, merging hook groups by ownership and excluding authoring evals, and compiled runtime-only plugin export (`devforge install export-plugin`), which builds a new single-provider `devforgeai` directory, never overwrites one, and reports `adoption: NOT_ACCEPTED_STAGING`. See [project-local framework installation](docs/integration/framework-installation.md).
- Exact external hash/mode pins for immutable installed helper files outside editable application roots.
- Compiled runtime probing (`devforge install probe-runtime`): a delivery-aware installation names the probed runtime with `--runtime`, and the Rust CLI alone admits the reported capabilities. The probe is bound to the installation project, which the validating executable must be outside of, and that executable decides its own destination-overlap, alias and pre-write digest protections. `devforge install framework` is the validating executable itself and applies them in process; the unchanged `scripts/install_framework.py` still selects one with `--validator` and invokes `devforge install guard-validator` before writing.
- A compiled Rust manual-only installation path for the promoted Codex expert workflows (`devforge install manual-experts`) that validates owner-selected adoption evidence and verifies the pinned executable and embedded source identity before writing. `devforge install framework` refuses those packages and preserves any `manual_expert_adoption` record already in the project inventory. The legacy script's `--manual-evidence`/`--manual-experts-only` refresh is the only installation mode that remains Python.
- Compiled structural validation of a DevForgeAI checkout (`devforge validate framework --framework <path>`), the Rust port of `scripts/validate_framework.py`: traversal, retained-evidence exemptions, JSON/TOML/`SKILL.md` inspection, plugin manifests, runtime-requirement sidecars, bounded hook sources and authored eval declarations. It reports structure only and never executes a candidate skill, hook or runtime host.
- Compiled fixture demonstration and local verification (`devforge demo`, `devforge verify-poc`), the Rust ports of `scripts/demo.py` and `scripts/verify_poc.py`. The demonstration calls no model and reports `model_calls: 0`; the verification report keeps `hosted_ci: NOT_RUN`. See [demonstration and verification](docs/integration/demo-and-verification.md), which records the `policies/` drift that currently blocks both.
- Compiled deterministic handoff receipt checks (`devforge receipt check`): byte-identity and format verification, plus a separate digest listing that is explicitly a listing and not a verdict. See [handoff receipt checks](docs/integration/receipt-check.md).
- CI definitions in this repository, including manual structural validation of an exact DevForgeAI commit.

## Limits

This POC does not certify an AI as an expert, prove semantic specification compliance, validate arbitrary package-manager graphs, enforce all possible source semantics, or provide production release authority. Its dependency contract is the explicitly declared `dependencies.json`; source-token checks are a demonstration, not a complete ORM detector.

Expert bindings are structural and deliberately report behavior as `NOT_EVALUATED`. The fixed test harness observes the submitted tests but cannot prove that adversarial Python code is honest. Independent review remains necessary.

The launcher protects filesystem writes outside the candidate within its mount/PID namespace. Network isolation is not enabled; network services, host sockets, and administrator actions are outside this guarantee. The test runner mounts only system runtime files, the frozen candidate, and a result directory. It does not receive the authority state or home credentials.

The authority user can edit policy and evidence. Do not give a worker unrestricted access to that user's host shell, privileged sockets, or the external state. A normal terminal started without the launcher does not inherit this POC's filesystem boundary.

Snapshots exclude root `.git`, root `.devforge-runtime`, and `__pycache__` directories. Limits are 1 MiB per file, 16 MiB per candidate, and 1,024 files. Only the Python unittest runner is implemented. The accepted snapshot is local evidence; it is not a Git merge, deployment, or semantic release approval.

## Layout

| Path | Owns |
| --- | --- |
| `src/main.rs` | CLI, policy checks, provenance, phase state, isolation, and snapshots |
| `runners/` | Fixed harness embedded into the executable |
| `policies/` | Synthetic example policies; real projects need their own accepted policy |
| `scripts/` | Legacy Python baselines for the compiled `devforge install framework`, `devforge validate` and `devforge demo`/`verify-poc`, plus the unported `--export-plugin`; retirement is a separate decision |
| `tests/` | Independent black-box gate and installer cases; Rust suites plus the remaining legacy Python ones ([retirement record](docs/integration/legacy-test-retirement.md)) |
| `.github/workflows/` | CLI CI and external framework structure checks |

The [framework repository](../DevForgeAI/README.md) owns the skills and examples. The detailed [POC contract](docs/POC.md) records operational boundaries and recovery behavior.
