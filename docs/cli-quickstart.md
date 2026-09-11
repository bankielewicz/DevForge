# WSL CLI quickstart

Build the DevForge CLI from a selected checkout, find the executable, and run a
harmless structural check. This guide installs nothing into any project, adds
nothing to `PATH`, and edits no shell profile. It is not the legacy full POC
verification; see [Legacy verification](#legacy-verification).

Verified on WSL2 Ubuntu with rustup 1.29.0 and Rust 1.94.0 against DevForge
`f175fd967162a89c4166769334744f7918a3f2bc` and DevForgeAI
`6c4db8f566cb2394b2734414256e8cca45bfeaf1`.

## 1. Select the checkouts

Set two absolute paths for the current shell session only. Both variables are
used by every later command, so each later block runs from any directory.

```bash
cd /path/to/DevForge            # the DevForge checkout you selected
DEVFORGE_SRC="$(pwd)"
FRAMEWORK=/path/to/DevForgeAI   # the companion checkout (../DevForgeAI in the workspace layout)
git -C "$DEVFORGE_SRC" rev-parse HEAD
git -C "$FRAMEWORK" rev-parse HEAD
```

Record both revisions. A dirty framework tree is a moving target, so
`git -C "$FRAMEWORK" status --short` should print nothing before the check in
step 4.

## 2. Prerequisites

CI selects Rust 1.94.0 (`.github/workflows/ci.yml`). Check before building:

```bash
rustup toolchain list
rustup run 1.94.0 cargo --version
```

Expected: a line starting `1.94.0-` and `cargo 1.94.0 (...)`. If `rustup` prints
`command not found`, it is unavailable on your current `PATH`; stop and arrange
that prerequisite separately. If toolchain `1.94.0` is absent, `rustup run`
without `--install` reports that it is not installed and exits without downloading
it. Stop before building; toolchain installation is a separate operator decision.
Repeat these checks after that prerequisite is satisfied. CI installs its own
toolchain explicitly; this guide does not.

Python 3.12 and bubblewrap are not needed for anything below. They belong to
the legacy tests, gates, `devforge isolate`, and `scripts/verify_poc.py`.

## 3. Build and locate the executable

```bash
cd "$DEVFORGE_SRC"
rustup run 1.94.0 cargo build --locked
ls -l "$DEVFORGE_SRC/target/debug/devforge"
```

The executable is `$DEVFORGE_SRC/target/debug/devforge`. Nothing puts it on
`PATH`: a bare `devforge` prints `command not found`. Every command below uses
the full path, which works from any directory.

Building the CLI does not install DevForgeAI skills, agents or hooks into any
project. Installation is a separate, explicit action, and nothing in this guide
performs it. For reference only, that action is now the compiled command:

```bash
# Not part of this guide: it writes into $PROJECT.
# The project must already exist and be outside $FRAMEWORK, and Cargo
# hard-links the built binary, so --runtime needs a single-link copy.
install -m 755 "$DEVFORGE_SRC/target/debug/devforge" /tmp/devforge-runtime
"$DEVFORGE_SRC/target/debug/devforge" --project "$PROJECT" install framework \
  --framework "$FRAMEWORK" --provider claude --runtime /tmp/devforge-runtime
```

`--runtime` is needed only when the selected provider package declares
`hooks/runtime-requirements.json`; the executable you invoke is the validating
authority that probes it, so there is no `--validator`. Promoted Codex expert
packages are refused by this command and installed only through
[manual expert adoption](integration/manual-expert-adoption.md).
[Project-local framework installation](integration/framework-installation.md)
documents the destinations, refusals and parity exceptions. The unchanged
`scripts/install_framework.py` remains the legacy baseline and is still the
only implementation of runtime-only plugin export (`--export-plugin`).

## 4. Harmless checks

```bash
"$DEVFORGE_SRC/target/debug/devforge" --version
"$DEVFORGE_SRC/target/debug/devforge" --help
"$DEVFORGE_SRC/target/debug/devforge" install --help
"$DEVFORGE_SRC/target/debug/devforge" validate --help
"$DEVFORGE_SRC/target/debug/devforge" install identity
```

`--version` prints `devforge 0.1.0`; it does not identify the source revision.
`--help` lists commands at the selected level. Top-level help lists `install`
and `validate`; `install --help` and `validate --help` list their nested commands.
Check the appropriate level before deciding whether a command is available.
An unavailable command produces `error: unrecognized subcommand '<name>'`
with exit 2 and does not execute the requested operation.

`install identity` prints JSON with the executable's canonical path and
SHA-256 plus the `source_sha256` embedded at build time. Its own
`protection` field says `NOT_ESTABLISHED_BY_SELF_REPORT`: the printout pins
nothing and is not installer authority. Digests differ per build.

The structural document check, from any directory:

```bash
"$DEVFORGE_SRC/target/debug/devforge" validate mvp --mvp "$FRAMEWORK/docs/mvp"
```

`--mvp` must be a real `docs/mvp` inside a full framework tree, because provider
skill sources are resolved two levels above it. Outcomes:

- `PASS`: JSON report on stdout, exit 0.
- `FAIL`: JSON report with collected `errors`, exit 2.
- `BLOCKED: <reason>` on stderr, empty stdout, exit 2 (malformed or unreadable input).

The report, `--report`, and the checks are documented in
[MVP document validation](integration/mvp-document-validation.md).

## What this does and does not establish

A `PASS` means the selected `docs/mvp` tree is structurally consistent. It
reports `native_skill_behavior: NOT_EVALUATED`. It is not native skill
behavior, acceptance, qualification, MVP completion or installation, and it
does not satisfy the adoption prerequisites in
[manual expert adoption](integration/manual-expert-adoption.md).

## Legacy verification

`scripts/verify_poc.py` and `scripts/demo.py` are the legacy Python full
verification and demonstration. They require Python 3.12 and bubblewrap and
write evidence under `docs/validation/` and `.poc/`. Nothing above launches
them. The [terminal runbook](POC.md) covers the gate and isolation workflow.
