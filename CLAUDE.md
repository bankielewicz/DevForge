# DevForge CLI guidance for Claude

Read [AGENTS.md](AGENTS.md) and the [development language policy](docs/development-language-policy.md) before editing. Follow their ownership, TDD, evidence and bounded-delivery requirements.

**Framework implementation must be compiled Rust in DevForge CLI. Every DevForgeAI phase, gate, validator, mutation broker and acceptance decision belongs in Rust. Python is mandatory only for the skill-evaluation JSONL runner and deterministic graders; these must be bound as required evaluation build artifacts. All other implementation languages are forbidden.** Evaluation outputs and AI review are evidence, not authority to advance or accept.

Protect and digest-pin the actual Rust executable and its source/revision/build inputs, plus policy and expected identities, outside the evaluated agent's writable boundary. Reject a rebuilt or changed gate even when it claims the same version. Compilation alone is not tamper protection.

Existing Python framework logic **must be ported into DevForge CLI as Rust**. Preserve legacy source, tests and evidence; unchanged tools may still run when authorized, but do not claim their Python authority meets this policy. Framework code/tests outside the evaluation exception must use Rust. Markdown and declarative data/configuration remain valid. This documentation task neither performs the migration nor implements missing evaluation artifacts or automatic enforcement.
