# DevForge CLI guidance

## Scope and ownership

This local Linux/WSL2 POC owns the Rust CLI, external policy, protected runtime, fixed harness, project-local installer, acceptance tests, and CI. Existing Python runtime and tooling components are legacy implementation; the mandatory language rule below governs development. Companion [DevForgeAI](https://github.com/bankielewicz/DevForgeAI) owns conversational skills, agents, and expertise. Follow `README.md`, `docs/POC.md`, and the assigned contract.

Only authorized CLI/runtime maintenance may change this repository. Candidate/application work cannot alter governing gates, policy, or tests to pass. Enforce filesystem boundaries through the launcher and keep acceptance in the authority terminal; repository separation alone is not isolation.

## Mandatory languages and protected authority

**Framework implementation must be compiled Rust in DevForge CLI. Every DevForgeAI phase, gate, validator, mutation broker and acceptance decision belongs in Rust. Python is mandatory only for the skill-evaluation JSONL runner and deterministic graders, bound as required evaluation build artifacts. All other implementation languages are forbidden.** Read the [development language policy](docs/development-language-policy.md).

Python evaluation supplies observations and metrics, not trusted framework decisions. Protect and digest-pin the Rust binaries, source/revision, build inputs and governing configuration outside the evaluated agent's writable boundary; verify actual identities so a rebuilt weakened gate cannot masquerade as the selected authority. Compilation alone is insufficient.

Existing Python framework logic **must be ported into DevForge CLI as Rust**, including runtime, validators, gates, brokers, acceptance, installers and tooling. Framework tests/examples outside the evaluation exception must also move to Rust. Preserve existing evidence and mandatory checks. Unchanged legacy tools may still run for authorized purposes, but Python authority is not compliant with this rule. Markdown/declarative configuration remain valid. No migration, new evaluation artifact or automatic enforcement is claimed by this documentation change.

## Concurrent work

Identify repository, worktree, branch, HEAD, write scope, and existing changes before implementation. Parallel implementation sessions use separate worktrees and branches with worktree-local build/test outputs. A chat fork is not filesystem isolation. Workers do not mutate another checkout or the shared integration branch; its designated integration owner handles integration. Preserve unrelated changes and assign delegated writers distinct files.

Recheck required pins and owned files before writes and handoff. Unexpected drift blocks the affected action. Do not silently repin frozen evidence or reset, clean, stash, or overwrite concurrent work. General checks below do not override an explicit read-only or no-execution allocation.

## CLI development is TDD

1. Specify observable behavior and compatibility from the accepted contract.
2. Add a discriminating regression/acceptance test. Run it against the current implementation and observe the intended assertion failure. Broken setup, missing binaries, skips, and environment errors are not valid RED evidence.
3. Implement the smallest change that passes the unchanged test; observe GREEN, then refactor and rerun relevant tests.

Existing CLI acceptance tests include legacy Python `unittest` cases launching the real compiled binary; keep running them unchanged. Add new regression tests in Rust. Rebuild with `cargo build --locked` before CLI RED/GREEN runs; the build embeds current runtime sources, so stale binaries do not verify a source change. Some tests hardcode `target/debug/devforge`; a `DEVFORGE_BIN` override does not cover every suite.

Assert exit status, structured stdout, relevant stderr, and expected/forbidden filesystem or state changes. Include malformed input, stale evidence, collisions, and failure atomicity where affected. Keep fixtures deterministic and independently derived. Use isolated temporary directories, bounded subprocesses, and cleanup of owned resources. A synthetic cleanup test is not observed native cleanup. Never weaken expectations merely to accommodate an implementation.

## Rust and verification

Respect `Cargo.toml`'s edition, minimum Rust version, pinned dependencies, and `unsafe_code = "forbid"`; preserve `Cargo.lock`. Follow the toolchain selected by `.github/workflows/ci.yml`. Justify dependency changes. Return contextual errors for user input and environment failures; keep machine-readable stdout separate from diagnostics.

For executable changes, run the focused tests during TDD, then these checks from this worktree:

```bash
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo build --locked
cargo test --locked --all-targets
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

CI and local verification both require `cargo test`. CI rejects empty or skipped Python suites. Validate changed companion artifacts with `devforge validate framework` and `devforge validate mvp`. Exercise `devforge demo` for gate-lifecycle changes. The demo and `devforge verify-poc` create evidence and fixtures; select permitted output roots first. The legacy `scripts/validate_framework.py`, `scripts/demo.py` and `scripts/verify_poc.py` remain unchanged baselines, not the instructed path. Documentation-only edits need content, link, and diff review, not a runtime campaign.

Preserve no-fallback isolation and precise failure statuses. Report exact changes, executed checks, remaining failures, and evidence boundaries. Structural checks, fixtures, native behavior, admission, and human acceptance are separate results; an accepted local snapshot is not a Git merge or release.
