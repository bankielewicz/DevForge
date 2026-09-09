# DevForge CLI POC

External Rust gates and workflow tooling for the companion DevForgeAI framework.

Intended repository: https://github.com/bankielewicz/DevForge

This is a new local POC, not a clone or a replacement of remote history. GitHub publication, branch protections, and hosted execution are not configured by this scaffold.

## Implementation language

**Framework implementation and all phase, gate, validator, mutation and acceptance authority must be compiled Rust in DevForge CLI. Python is mandatory only for the skill-evaluation JSONL runner and deterministic graders, bound as required build artifacts; all other implementation languages are forbidden.** See the [development language policy](docs/development-language-policy.md) for evidence binding and protected, digest-pinned Rust authority.

Existing Python framework runtime, tooling and tests **must be ported into DevForge CLI as Rust**. The Python commands below describe current legacy implementation, not a completed migration or permission to extend Python authority. Unchanged tools remain available for authorized operation; their successful execution does not establish compliance with the new authority boundary.

## Run it

Requirements: Linux/WSL2, Rust/Cargo (tested with 1.94.0), Python 3.12, and bubblewrap with usable filesystem/PID namespaces. No model API key is required.

From this directory:

```bash
cargo build --locked
python3 scripts/verify_poc.py --framework ../DevForgeAI
```

The verification command runs Rust checks, black-box acceptance and installer tests, framework structure checks, and both example projects. It writes a source manifest and logs under `docs/validation/`. The demonstration creates collision-safe working copies under the sibling framework's `.poc/`, with external state here in `.poc/`.

For a prepared project to use interactively:

```bash
python3 scripts/demo.py --framework ../DevForgeAI --prepare-only
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
- Provider-specific project-local installation and runtime-only plugin export, preserving local edits and excluding authoring evals.
- Exact external hash/mode pins for immutable installed helper files outside editable application roots.
- A compiled Rust manual-only installation path for the promoted Codex expert workflows (`devforge install manual-experts`) that validates owner-selected adoption evidence and verifies the pinned executable and embedded source identity before writing; other installation modes remain legacy Python.
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
| `scripts/` | Project-local installation, demo, structural validation, and verification |
| `tests/` | Independent black-box gate and installer cases |
| `.github/workflows/` | CLI CI and external framework structure checks |

The [framework repository](../DevForgeAI/README.md) owns the skills and examples. The detailed [POC contract](docs/POC.md) records operational boundaries and recovery behavior.
