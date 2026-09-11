# Mechanical brainstorm delivery runtime

**Required migration:** the Python framework runtime described here must be ported into DevForge CLI as Rust under the [development language policy](../../docs/development-language-policy.md). All phases, gates, validators, mutation brokers and acceptance decisions require protected, digest-pinned Rust binaries and source/revision. Python remains mandatory only for bound skill-evaluation JSONL runners and deterministic graders, which supply metrics and evidence without framework authority. Embedding Python inside Rust does not satisfy this boundary. Existing code/results remain legacy evidence; this documentation does not implement the migration or protection.

This component is an uncommitted implementation candidate. It checks observable
phase evidence and persisted artifact bytes. It does not certify brainstorming
quality, human adoption, actual native hook provenance, or native turn completion.

The `devforge delivery` command embeds the Python modules in the Rust executable.
It verifies a content-addressed, read-only code cache before using the fixed
`/usr/bin/python3 -I -B` interpreter. Linux, Python 3.11 or newer, PyYAML 6.x and
Bubblewrap and Linux process descriptors (pidfds) are required for guarded execution. The hook forwarding command itself
does not start Python. Installation does not install these system dependencies.

## Commands and authority

`delivery init --contract <session.json> --state <new-external-directory>` selects
the strict `devforge.brainstorm-session/v1` contract once. The contract selects a
`devforge.delivery-task/v1` or `devforge.delivery-task/v2` delivery contract, assignment and installed file pins,
fixed deadline, output baselines/archives, checkpoint destination and external
receipt destination. The state must be outside the worker's writable project.
Creating such a file does not supply authoring, model-call or acceptance authority.

`delivery status`, `advance`, `resume` and `complete` use `--state`. They are
external operator/controller operations, not commands for a model to remember.
`delivery check --contract <delivery.json>` inspects actual artifacts;
`delivery verify --contract <delivery.json> --receipt <receipt.json>` rechecks the
same receipt and current artifacts. None of these operations proves native
completion or semantic acceptance. `resume` does not launch another process or
prove that a human answered a pending question.

`delivery run --contract <session.json> --state <new-external-directory>
--profile <private-home> --client-root <intact-client-directory> --timeout <seconds>
--completion-mode <process|managed-session> --io-mode <inherited|interactive-tty>
-- <argv>` currently refuses native admission. The fixed native launcher/authentication
contract and effective hook-source verification are still missing. It does not
copy credentials, inspect a private host profile, grant hook trust, or substitute
another authentication method. The managed-session mechanical boundary is
implemented for synthetic validation; ordinary native support remains unvalidated.

`inherited` is the default IO mode. `interactive-tty` requires actual terminal
descriptors and relays through an owned controlling PTY. It preserves bounded
input/output queues, window size and parent terminal settings. Terminal EOF is
recorded separately from owned process exit. A missing terminal, failed relay,
incomplete drain or failed restoration prevents a new process-mode completion.
The adapter never falls back to another IO mode or certifies that a user saw text.
The client acquires its session and controlling terminal inside the PID namespace,
so foreground process identity remains usable there. SIGTERM, SIGHUP and SIGINT
to the outer CLI are forwarded through an owned process descriptor. The Python
supervisor latches cancellation, cleans up its worker, joins the broker and
restores terminal settings before returning its final result. A managed commit
that finishes during exceptional shutdown remains in that final historical result.

The explicit `--synthetic` option admits deterministic process fixtures under
Bubblewrap with network isolation. It masks the normal HOME and `/tmp`, restores
the selected project/private profile writable, and restores selected code,
inputs, installed resources, archives and an in-project `.git` directory read-only.
External Git worktree pointers are refused until the launcher contract explicitly
selects their metadata roots. The masked HOME and `/tmp` parents are remounted
read-only so a worker cannot create shadow files at external authority paths.
A dedicated scratch tmpfs is writable and selected through `TMPDIR`. The executable
stays at its original path inside the selected intact package directory. The
supervisor owns an unreaped child through cleanup signals and never falls back to
an unconfined process. These fixtures are not native model evaluations.

## Phase and delivery scope

The ordinary path is Recover, Explore, Record, Focus. The externally selected
handoff-only path is Recover, Focus; Explore and Record are `NOT_APPLICABLE`.
Each admitted phase issues a fresh unpredictable challenge. Accepted checkpoints
and inspected bytes are preserved in a protected hash-bound journal. A changed
fixed input, missing archive, replay or skipped phase cannot authorize completion.
Unknown facts can remain explicit. A blocking question preserves `WAITING_USER`
without advancing or publishing a receipt.

One repair is permitted per phase. Every Stop occurrence inspects current bytes;
responses are not deduplicated by message or checkpoint content. Native admission
must separately establish that effective hook sources do not duplicate the managed
callback. A Stop callback is not authenticated evidence of a human decision.
Runtime-generated continuation text must retain that provenance.

Process fixtures finalize only after all required phases reach READY and the owned
process exits successfully. Timeout, interruption, nonzero exit, absent callbacks
or broker failure withhold completion. Finalization rechecks actual output bytes,
publishes an exclusive receipt and verifies its complete readback. A durable intent
allows recovery of the same receipt after interruption; it does not permit a new
publication after the deadline. Later output/input changes invalidate current
applicability without rewriting historical receipts.

The explicit `managed-session` completion mode commits the mechanical task during
the qualifying Stop after Focus reaches READY, while the client can remain alive.
It calls the same protected completion/readback operation and returns the observed
receipt locator and full digest through the provider's synchronous `systemMessage`.
Its selected process/mode observation precedes callback admission. It never treats
a worker's PASS text or callback identity as native completion or human authority.

Managed mode writes a protected `task-result.json`, a fresh delivery-attempt record
before each verified response, and a separate transport record after the socket
write. A successful socket write is `SOCKET_WRITE_COMPLETED`; rendered user delivery
remains `NOT_OBSERVED`. Re-emission rechecks and reuses the same receipt with a new
attempt timestamp. Later drift produces an applicability warning and retains the
historical receipt. A late process failure remains a failed process outcome with
the committed `task_result` preserved; it does not erase that receipt. These
point-in-time checks do not promise that writable outputs can never change later.

The CLI reads the external process result and includes its path and SHA-256 in
`result_readback`. The JSON completion surface exposes any actual receipt locator
and digest. A saved result's `user_delivery: NOT_RUN` is a creation-time fact;
merely writing that file does not prove a user received it.

## Versioned reference coverage

The v2 implementation candidate adds deterministic reference enforcement. V1
contracts and receipts retain their original narrower scope. Existing journals
bind their original delivery contract bytes; an upgraded executable cannot turn
a v1 result into v2 evidence. Native admission remains unavailable for either
version until its separate prerequisites are established.

V2 adds exactly `project_id`, `primary_ledger_path`, and `reference_catalog` to the
six v1 task fields. Brainstorm tasks may select multiple distinct idea-ledgers and
one handoff; the primary path selects the ledger named by the unchanged Record
checkpoint. Record checks every selected ledger before Focus. The accepted ledger
bytes and reference report remain fixed through Focus and recovery. Handoff-only
keeps one handoff and a null primary ledger. Every selected output must exist.

The finite catalog explicitly maps a logical `project` or `authority` locator to
selected physical bytes. Each entry declares `store`, `path`, `kind`, `identity`
and `source`. Artifact identity contains `artifact_id`, `artifact_type`,
`project_id` and a positive integer `revision`; raw-file identity is null. A fixed
source declares `kind: fixed`, an absolute `physical_path` and its complete
lowercase `sha256`. An `output-preimage` source additionally names `output_path`;
its physical path and digest must exactly match that output's session baseline
and selected archive. Initial preimage bytes are checked and snapshotted before
archive publication. Later resolution reads the archive and compares its bytes
to protected custody. The same logical path may identify separately selected old
and current revisions. No search, nearest revision or current-file substitution
resolves a missing variant.

Selected catalog files join the protected source snapshots and read-only mounts.
They cannot overlap the checkpoint, receipt, protected state, implementation or
mutable output destinations. Only the explicitly selected preimage archive has
the initialization publication exception. A source reference never grants access
to an unselected file. Empty and binary raw evidence is supported; an artifact
still requires a valid nonempty UTF-8 envelope. Fixed-source drift invalidates
current applicability without rewriting historical evidence.

Produced artifact references use strict identity, revision, store, path, digest
and section fields. Upstream requires real populated causal section IDs; table
row IDs cannot substitute for headings. A truly heading-free source requires an
exact structured `unsectioned-source` disclosure matching that reference in
`missing_inputs`. Raw files are permitted only as evidence. Managed artifacts
require an execution reference resolving to the physically selected SESSION
assignment; null is unsupported in the two v2 delivery modes. Existence of that
reference does not establish human adoption.

V2 is also a prospective artifact-format change. Every body byte/identity claim
uses a complete single-line `@df-ref(<strict JSON object>)` atom, carrying `field`
and its complete reference tuple. Every occurrence is checked, including repeats
inside prose, tables and code. Malformed atoms, ambiguous split-column hash
receipts and reserved claims outside the grammar fail. Noncausal or future output
links use `@df-link(...)` with store, path, artifact_id, revision and relation
(`planned-output` or `noncausal`), and no digest. These links grant no byte-read
authority. This grammar does not classify every possible misleading English
sentence; attribution, relevance and truthful adoption remain separate judgments.

Handoffs declare `delivery_state` with exactly `final_artifact_check`,
`receipt_publication`, `receipt_readback`, `target_verification` and `user_delivery`,
all `NOT_RUN`. Repeated body outcomes use `@df-delivery(...)` with those fields
plus the selected task ID. A current self digest is forbidden, including aliases;
an exactly preserved prior revision can still be superseded. Historical receipts
remain separate evidence and cannot supply current-task future outcomes.

Record, Focus, finalization and verification share the selected reference engine.
Protected phase snapshots retain its deterministic report. The v2 external
receipt adds `reference_profile: devforge.reference-coverage/v1` and complete
`reference_coverage`; verification reproduces and compares them. Receipt overflow
fails explicitly instead of truncating observations. Publication and readback
remain pending inside the receipt's own creation-time bytes. Separate later
observations bind the complete saved receipt. These checks establish mechanical
conformance to this profile, not semantic quality, native activation or acceptance.

Existing provider instructions must receive a separately bound compatibility
revision before v2 behavioral evaluation. The issued revision 7 authoring packet
is immutable and does not already promise this new artifact grammar. Source
implementation, synthetic checks, provider evaluation and human acceptance keep
their separate identities and statuses.

## Hook registration

Prospective provider packages use `hooks/hooks.json`. A thin command invokes
`"${DEVFORGE_DELIVERY_EXECUTABLE:-devforge}" delivery hook --provider codex` (or
`claude`). The selected supervisor supplies the socket and session-contract digest.
Without an active socket, the command returns `{}` and makes no enforcement claim.
With an active socket, malformed or unavailable transport fails explicitly.

The project installer merges Codex hooks into `.codex/hooks.json` and Claude hooks
into `.claude/settings.local.json`. It records ownership per group, preserves
unrelated settings and identical preexisting groups, and rejects local edits to
owned groups. Plugin export retains the selected hook component. Neither path
grants native hook trust or demonstrates actual callback activation.

A delivery-aware package declares `hooks/runtime-requirements.json` with the
versioned `devforge.runtime-requirement/v1` contract and its provider. Installation
then requires an explicit `--runtime` executable to probe and an explicit
`--validator` DevForge executable to probe it with; both must be absolute,
canonical, regular executables with exactly one hard link, and the validator is
never taken from `--runtime`'s value by default, an environment variable or PATH.
The installer delegates to `devforge install probe-runtime`, which runs
`delivery capabilities` under a five-second deadline and a 1 MiB output budget,
admits either the eight-field `devforge.delivery-capabilities/v1` base contract or
that base plus all seven declared extensions, and reports `base` or `extended`.
A missing base field, an unknown field, a partial extension set or any malformed
value is refused, and the installer writes nothing on refusal; Python performs no
capability validation. Compatibility is mechanical only: native activation stays
unverified. Export preserves the requirement; export and structural validation
report the eventual host runtime as unverified.

Before integrated release, validate runtime compatibility, finish native launcher
admission and observe native task completion and message rendering, update the retained provider packages and shared
execution contract under fresh author allocations, run the required independent
native evaluations, and obtain the applicable human acceptance evidence.
