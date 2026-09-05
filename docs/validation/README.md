# Local POC validation — 2026-09-04

Latest implementation verification: [report and source hashes](20260904T202156376559Z/report.json).

| Check | Observed result |
| --- | --- |
| Rust format, Clippy with warnings denied, locked build | PASS |
| Black-box gate, installer, and structural-validator cases | 32 tests passed |
| Framework package structure | PASS; four core skills and two example experts |
| SQLite fixture | RED -> GREEN -> ACCEPTED -> VERIFIED; stale/refresh checks passed |
| JSON-file fixture | RED -> GREEN -> ACCEPTED -> VERIFIED; stale/refresh checks passed |
| Source-manifest readback | Every recorded file hash matched after verification |
| Claude native plugin validator | PASS with nonessential traffic disabled |
| Codex plugin schema validator | PASS using the installed plugin-creator validator |
| Skill frontmatter validator | All six source skills passed |
| Filesystem isolation | Candidate write succeeded; external policy and validator-canary writes were denied |
| Codex isolated binary smoke | codex-cli 0.153.2 started and reported version |
| Claude isolated binary smoke | Claude Code 2.1.261 started and reported version |
| Full interactive onboarding | COULD_NOT_RUN here: startup requests to raw.githubusercontent.com and platform.claude.com were denied by this execution environment's network allowlist |
| Subscribed model behavior / independent expert evaluation | NOT_EVALUATED |
| Network namespace isolation | Unavailable in this environment; filesystem-only scope is explicit |
| Hosted GitHub CI / protected-branch configuration | NOT_RUN / NOT_CONFIGURED |
| Git publication | Two local repositories initialized with the requested origins; no commits or pushes |

The source manifest excludes `.git`, build output, `.poc` runtime/evidence, Python caches, and this validation directory. It binds the implementation and framework source files evaluated by that run. Older report directories are prior development checkpoints.

The generated accepted candidates and their state remain in the `.poc/` paths printed in [the fixture log](20260904T202156376559Z/fixture-demo.log). Private client homes in a separate smoke workspace contain no copied host credentials. The user must sign in through the normal subscription flow for the interactive evaluation.

The initial CLI test failed against a compiled placeholder because expert binding was not implemented. Subsequent tests exposed and corrected relative-path handling, initial-suite validation, and mount ordering. Passing fixtures and package checks do not prove semantic skill quality or production tamper resistance.

Next: follow the [terminal runbook](../POC.md), prepare a fresh project, and evaluate the project-expert-creator in a subscribed terminal session.
