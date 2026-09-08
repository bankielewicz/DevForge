The native process adapter is a bounded collector behind the protected utility
journal. It implements owned launch, bounded byte collection, scoped cancellation,
an optional managed callback broker, and authenticated receipt custody. It includes a frozen app-server answer adapter. It does
not establish effective native isolation,
authenticate callback authors, grade behavior, or permit an incomplete campaign.
The required case allocation currently exceeds the approved 24 attempts. No
native evaluation, model probe, sign-in, credential inspection, or receiving
execution was performed while implementing this adapter.

`prepare_request(plan, attempt, binding, reserved_at, deadline,
campaign_origin_utc, installed_inputs)` is read-only. The caller must already have
validated the full native plan and the complete counted allocation. It returns
the exact request to journal before launch. The six incoming binding fields are
`task_id`, `attempt_id`, `plan_sha256`, `schedule_binding`,
`reservation_sha256`, and `challenge`. Preparation adds command, client, model,
configuration, prompt, installed-input and original-clock bindings. It never
chooses a new reservation, origin, budget, or authentication arrangement.

The pinned `runtime_configuration` has schema
`devforge.native-runtime-configuration/v1` and exactly `schema_version`,
`allocation`, and `attempts`. Allocation is a file pin validated separately by
`utility_evidence.launch_allocation`. Each attempt has these fields:

| Field | Required value |
| --- | --- |
| `attempt_id` | Exactly one declared plan attempt |
| `interaction` | `single-turn` or `awaiting-user` |
| `answer_policy` | Required only for `awaiting-user`: immutable policy file pin |
| `config` | Pin of the selected credential-free TOML configuration |
| `prompt` | Pin of the complete bounded UTF-8/raw prompt input |
| `readonly_inputs` | Exact single-link regular-file pins for installed resources, client package files, and immutable discovery files |
| `system_mounts` | Explicit subset of the adapter's system-runtime allowlist |
| `fixture_git_dirs` | Explicit disposable fixture Git directory paths and `tree_digest` hashes |
| `immutable_discovery_dirs` | Complete directory inventories, including prepared empty directories |
| `effective_runtime` | Pin of separately produced effective-runtime observation evidence |
| `managed_worker` | `null`, or `{session: pin, state: path, gate_executable: pin}` |

The exact approved CLI is 0.153.4 at the frozen standalone executable path and
SHA-256 in `native_process.CLIENT`; model is `gpt-6-astra`, reasoning is medium,
and the configuration requires direct ChatGPT authentication. It cannot select
an API key, alternate model provider, arbitrary command, automatic approval
review, external MCP/app integration, or native subagent execution. Request and
stream retries must both be zero; unbounded connection retries and automatic
goal continuation must be disabled. These are structural admission requirements;
effective enforcement and provider-internal behavior remain unobserved.

The supported switches were checked with the exact local binary's `exec --help`,
`features list`, and `app-server --help`, with an empty allowlisted environment
and no normal profile. Official documentation identifies the JSONL lifecycle,
`--skip-git-repo-check`, named filesystem deny-read permissions and network
settings, feature controls, and retry configuration. See [non-interactive
mode](https://learn.chatgpt.com/docs/non-interactive-mode) and [configuration
reference](https://learn.chatgpt.com/docs/config-file/config-reference).

The effective-runtime evidence schema is `devforge.native-effective-runtime/v1`.
It binds `attempt_id`, `client`, `model`, `config_sha256`, `prompt_sha256`,
`workspace`, `client_state`, and `runtime_inputs_sha256`. The last field is
`runtime_inputs_digest(row)`, a non-cyclic binding to the complete readonly,
system, fixture-Git, discovery, managed-session and interaction selections.
It has exactly these observation names:
`effective_hooks`, `tool_profile_deny_read`, `tool_network_denied`,
`fresh_subscription_profile`, `process_namespace`, and
`nested_model_routes_disabled`. Each value is `{status: "OBSERVED", evidence:
pin}`. A trusted external prerequisite producer must supply real observations;
this adapter does not turn declarations or worker text into those observations.
The plan's separate filesystem/source/history/authentication/callback evidence
remains required. Native readiness stays unavailable when those observations
cannot be obtained within the complete counted allocation.

The outer filesystem starts as an empty root. Only selected system runtimes,
the prepared workspace, the fresh private profile, exact readonly inputs, and
explicit fixture Git directories are mounted. The host root, ordinary home,
source/evaluation trees, original Git common directory, authority, outer journal,
and receipts are not mounted. The outer worktree `.git` entry is masked without
reading a pointer. Only the workspace and private profile have writable backing
mounts. The CLI may access its subscription network; model tools must run under
the selected named permission profile with network disabled and deny-read rules
for the private profile and `/proc`. The latter closes ancestor environment/fd
access routes. Native effective enforcement of those rules is still a gate.

Prepared immutable discovery directories are workspace `.agents` and `.codex`,
and private-profile `.agents`, `.codex`, `skills`, and `rules`. Every contained
file must be pinned; their complete inventories are mounted readonly. Both
workspace and private profile also require immutable `AGENTS.md` and
`AGENTS.override.md` files, and the profile requires immutable `hooks.json` and
`config.toml`. Absence is represented by explicit empty instruction/config stubs
or an empty valid hooks document, prepared before freezing. The adapter never
creates those stubs during admission. Project config is additionally masked and
user config is ignored in favor of the exact selected overrides. A worker cannot
add sibling skills, hook configuration, or instruction overrides after admission.
Ordinary workspace outputs remain writable. Profile metadata pins must never
include authentication files. Put model-readable installed skills in the
workspace because the entire private profile is denied to model tools.

For managed workers, the selected utility session and state must be separate
from the outer validator state. `supervisor.Broker` drives the actual protected
phase engine around the owned process; its readonly socket endpoint and hook
environment reach the worker, while session authority, gate inputs, state and
receipt remain outside the view. Runtime Python files and the gate executable
must be exact readonly inputs. The broker is closed before final readback.
Reported managed completion requires its previously committed task receipt to
reverify and actual ordered SessionStart, UserPromptSubmit, Stop and SessionEnd
observations, with no duplicate singleton events. Interactive managed completion
requires exactly the prompts from fully written counted `turn/start` messages.
Broker evidence binds prompt digests and before/after phase state. Each extra
prompt must follow an observed Stop in WAITING_USER and resume that same phase
to ACTIVE; queued or merely reserved turns do not authorize extra callbacks. Callback records retain
`NOT_AUTHENTICATED` origin. A generic completed turn cannot replace managed
completion. Failure to initialize leaves its consumed attempt and any created
managed state available for custody inspection; it does not retry.

`launch(authority_root, request, check_reservation=..., elapsed_seconds=...)`
checks the exact protected one-use claim before creating the process and after
teardown. It uses the caller's original elapsed clock and a conservative local
monotonic deadline, never a resettable campaign clock. The request directory is
created exclusively. A crash or incomplete collection cannot relaunch it.
The collector records PID, start-time ticks and session, keeps the leader PID
unreaped until all scoped signals finish, tears down the owned group/PID
namespace, and reaps the leader. It never signals a numeric PID/group after
reaping. Cancellation handlers are scoped and restored. Run collection in the
controller's main thread.

Each stream is capped at 64 MiB and retained byte-for-byte up to that bound. On
overflow the process is stopped and the stream is explicitly incomplete. Normal
EOF, timeout, failure, cancellation, output overflow, retained-child output,
clock rollback and incomplete cleanup remain distinct observations. Valid JSONL
requires complete newline-delimited strict JSON, one ordered thread/turn start,
a completed turn with valid usage, and no error/unknown/malformed events.
Nested agent text cannot assert a lifecycle event. Source pins, file identities,
fixture trees and immutable discovery inventories are checked after execution.

`verify_receipt(authority_root, receipt_path, expected_binding)` authenticates
the canonical body HMAC, the exact request directory, original request bytes,
both stream files, broker evidence, and event derivation. The host-only 32-byte
key is in the protected collector root with mode 0600; the root is owned mode
0700 and cannot overlap a worker-visible mount. The key is never put in request,
receipt, worker environment or evidence snapshots. Receipt authority is
`OWNED_NATIVE_COLLECTOR`; semantic grade and native callback authentication are
always `NOT_EVALUATED`. The importer must separately qualify process completion,
freshness, original deadline, required managed completion, and the selected
independent/operator review before recording a native PASS/FAIL.

`run_fixture` is an unsigned deterministic test helper. It accepts neither an
authority root nor a signing key and always reports `FIXTURE_ONLY` and
`native_valid: false`, including when its fake JSONL contains a completed turn.
Its direct fixture commands must remain in the owned process group; the native
path additionally requires Bubblewrap PID isolation. Tests cover binary stream
tails, malformed/truncated/order failures, stdin delivery, nonzero exit, timeout,
cancellation, held child pipes, output overflow, source drift, forged receipts,
immutable discovery additions, and actual local broker callbacks. Empty-root
mount and immutable-directory checks use ordinary Python fixtures and do not
establish native client behavior.

Interactive NI-11 implementation uses the pinned client's app-server stdio
JSONL protocol. The policy schema is `devforge.native-answer-policy/v1` with
exactly `schema_version` and `steps`. Each ordered step contains `unit_id` and
`action`. An `answer` step additionally contains exact `questions` and `answers`
(the request-user-input response map); a `turn` step contains nonempty `text`.
The corresponding allocated call's `continuation_units` must equal the ordered
policy unit IDs. Each initial generation and every answer or additional turn
requires an exclusive fsynced collector unit claim before send. The protected
journal's initial launch claim binds the full immutable request and policy.
All allocated continuation slots are charged against the cap up front, including
unused slots and failed transport; slots are never refunded or reused.

The adapter correlates response IDs, thread IDs, turn IDs and blocking user-input
requests, rejects automatic answer resolution, and sends only frozen answers.
Unexpected or unanswered requests stop collection. All pauses and continuations
retain the original attempt and campaign deadline and the same case workspace.
Terminal completion triggers owned shutdown; its signal exit is distinct from
unexpected process death. Existing cleanup handles timeout, errors, cancellation,
held child pipes and overflow. The app-server adapter never enables model routes.

The receipt authenticates raw stdout, stderr, the bidirectional JSONL transcript
and per-unit durable claims. Import replays protocol messages against the policy
and authentic stdout. A completed protocol followed by verified owned shutdown
may qualify as process evidence even when shutdown used SIGTERM/SIGKILL; it is
not a semantic grade. Unknown protocol notifications fail closed.

Validation remains synthetic. The notification allowlist/order, actual native
Q&A availability, effective runtime controls, native cleanup and managed callback
chronology have not been observed against an authenticated client. The generated
local 0.153.4 schema and credential-free help inspection are protocol references,
not those observations. This candidate needs independent review and complete
required checks before any native readiness decision.

Version 2 policy (`devforge.native-answer-policy/v2`) retains the exact-answer
and literal-turn steps and adds `operator_answer` and `operator_turn`. Each has
exactly `unit_id`, `action`, and `allowed_answers`, a finite map of at most 64
named keys to nonempty literal strings of at most 4096 characters. No answer
text comes from the worker or from a freeform operator decision.

`operator_answer` waits for an actual correlated blocking user-input request.
`operator_turn` waits for an actual completed turn with captured completed
`agentMessage` items, then asks the operator to select the next prompt. Missing
completed assistant text is unavailable. Both actions consume their declared
continuation unit; operator turns use a new counted `turn/start` and operator
answers use a counted server-request response. Waiting does not reset any clock.

The live operator interface is inside the host-private attempt directory:
`<state>/native-collector/<SHA256(canonical launch binding)>/`. Its exact name is
also the parent of the collector's `request.json` and final `receipt.json`.
The collector exclusively publishes `operator-<unit_id>-request.json`. It binds
the full actual protocol request (or completed turn plus captured messages),
its SHA-256, the original launch binding, policy SHA-256, allowed literal facts,
and actual question IDs. The worker cannot access this directory.
The complete request document, including its metadata, must fit in 8 MiB.
An oversized document stops collection before publication; exactly 8 MiB remains
readable by the operator helper and receipt replay. This document bound is
separate from the protocol's per-line bound.

The operator reads that file, judges whether the frozen facts answer the actual
questions, and exclusively publishes `operator-<unit_id>-decision.json`. The
schema is `devforge.native-operator-decision/v1`, with exactly `schema_version`,
`binding`, `unit_id`, `policy_sha256`, `request_sha256`, `relevance_confirmed`
(boolean true), and `selections`. The latter maps every actual question ID to
one allowed fact key; for `operator_turn` its sole ID is `next_turn`. It contains
no freeform answer. Relevance is the operator's assertion, not a runtime judgment
or proof of human origin, and grants no additional authority.

From this repository's root, the operator can use the exclusive-write helper:

```python
from pathlib import Path
from runtime.delivery.native_process import write_operator_decision

pending = Path("/exact/protected/attempt/operator-facts-request.json")
write_operator_decision(pending, {"actual-question-id": "selected-frozen-key"},
                        relevance_confirmed=True)
```

Replace the path and selection with the exact pending request and deliberate
operator choices. For a completed assistant turn, select `{"next_turn": "key"}`.
The helper refuses collisions and malformed selections. The collector polls
without blocking its owned process loop, validates exact coverage/bindings,
saves exact accepted bytes as `operator-<unit_id>-accepted.json`, then reserves
before sending. A malformed/stale decision stops collection; absence waits only
until the original deadline. It does not retry an occupied decision filename.

Signed receipt custody includes request, original decision and accepted-byte
pins. Replay reconstructs the actual request from authentic stdout and derives
answers only from the bound decision and frozen facts. Symlinks, multiple hard
links, tampered files, cross-unit/cross-attempt choices and extra/missing question
IDs fail closed. Synthetic tests establish these mechanics only; native hooks,
real-client notification order, operator identity and semantic relevance remain
unobserved or externally judged.
