# Implementation languages and protected authority

Owner decision, 2026-09-09: **DevForge and DevForgeAI framework implementation must use compiled Rust in the DevForge CLI. Python is mandatory only for skill-evaluation JSONL runners and deterministic graders. All other programming languages are forbidden for project-authored executable implementation.** This explicit evaluation boundary supersedes both earlier general Python use and an absolute ban that would remove skill evaluation.

## Required boundary

| Responsibility | Required implementation |
| --- | --- |
| Framework phases, gates, validators, mutation brokers, policy checks and acceptance decisions | Compiled Rust in DevForge CLI |
| Framework runtime, installers, hooks, scheduling, development tooling, framework tests and executable examples | Rust; scheduling remains deferred post-MVP |
| Skill-evaluation JSONL runner and deterministic graders | Python, required evaluation build artifacts; observations and metrics only |
| Skill instructions, specifications, manifests and configuration | Markdown and declarative JSON/YAML/TOML; no hidden executable implementation |

A Python grader may calculate a score or per-case assertion outcome. It must not advance phases, bypass gates, approve mutations, issue trusted validator/admission results, or decide framework acceptance. Rust validates the evidence and owns every authority decision. AI reviews may supply findings and rationale, not override that boundary. Provider convenience, legacy code, temporary helpers, generated scripts, or embedding an interpreter inside a Rust binary does not expand the Python exception.

Existing third-party tools and external source may be used or inspected within the task's permissions. A provider's standalone Python skill checker is supporting evidence, not a replacement for the Rust framework validator. The mandatory Python evaluation design is this framework's owner requirement, not a claim that every Claude/Codex skill intrinsically requires Python. Ordinary command invocations remain valid operational instructions; new shell/PowerShell implementation is not an alternative framework language.

## Mandatory evaluation artifacts and evidence

A skill-evaluation build must include a Python JSONL runner and deterministic graders. Bind their exact files, dependency/runtime selection, case inputs and grading criteria in a manifest alongside the candidate and specification identities. Keep these selected artifacts protected from the evaluated agent. Do not omit evaluation to satisfy the Rust rule, replace executed benchmarks with prose, or treat a missing metric as zero or success.

The runner produces machine-readable case results and metrics, with case identity and execution status sufficient to distinguish measured outcomes from errors, timeouts and unrun cases. Record the actual outputs and diagnostics. The Rust validator checks the selected identities, result format and required coverage before applying the accepted criteria. Missing, changed, malformed or incomplete evidence cannot authorize acceptance. Python results alone are not framework PASS.

## Protect and pin the Rust authority

The evaluated agent must not be able to modify the selected Rust executable, its source/revision and build inputs, governing policy, expected digests, or acceptance records. Keep them outside its writable candidate boundary; bind the actual executable digest and source revision/content identity in protected authority configuration. Compilation by itself does not create this protection.

The trusted launch/verification path must check the selected executable and governing identities before authority-bearing execution and acceptance. A locally rebuilt or modified gate is not equivalent merely because it has the same filename, version or claimed revision. Reject an identity mismatch; candidate work cannot change the expected digest or approve a replacement. Any replacement requires the authorized integration owner to review and select the new identity while preserving earlier evidence.

## Required migration and current limitations

Existing Python framework runtime, phases, gates, validators, mutation/acceptance logic, installers and other framework tooling **must be ported into the DevForge CLI as Rust**. Framework-owned tests and executable examples outside the evaluation exception must also move to Rust. Retain Python only for the defined skill-evaluation runner and graders. This is a required migration, not an optional language preference.

Existing nonconforming source, checks and historical results remain evidence; do not delete them, disable tests, or rewrite history to claim compliance. Unchanged legacy commands may run for authorized observation and continuity, but their Python authority decisions do not satisfy this policy. New or extended framework behavior and its tests must use Rust. If a current delivery depends on nonconforming authority, identify the exact dependency and report the affected acceptance as unmet rather than silently waiving the requirement.

This documentation change does not implement that migration, add the required evaluation artifacts, or establish protected-binary enforcement. Those requirements must be verified against actual implementation before claiming compliance. Preserve frozen skill packages/specifications and active evidence; apply any needed implementation/specification changes within an explicitly assigned scope. Do not turn a documentation task into a migration campaign.
