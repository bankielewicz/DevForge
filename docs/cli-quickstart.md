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
cargo +1.94.0 --version
```

Expected: a line starting `1.94.0-` and `cargo 1.94.0 (...)`. If `rustup` prints
`command not found`, Rust is not installed; installing rustup is your own
action and is outside this guide. If the listing lacks `1.94.0`, rustup
downloads that toolchain the first time `cargo +1.94.0` runs (CI uses
`rustup toolchain install 1.94.0 --profile minimal` explicitly); treat that
download as your own install decision, not a step this guide performs.

Python 3.12 and bubblewrap are not needed for anything below. They belong to
the legacy tests, gates, `devforge isolate`, and `scripts/verify_poc.py`.

## 3. Build and locate the executable

```bash
cd "$DEVFORGE_SRC"
cargo +1.94.0 build --locked
ls -l "$DEVFORGE_SRC/target/debug/devforge"
```

The executable is `$DEVFORGE_SRC/target/debug/devforge`. Nothing puts it on
`PATH`: a bare `devforge` prints `command not found`. Every command below uses
the full path, which works from any directory.

Building the CLI does not install DevForgeAI skills, agents or hooks into any
project. Installation is a separate, evidence-gated action; see
[manual expert adoption](integration/manual-expert-adoption.md).

## 4. Harmless checks

```bash
"$DEVFORGE_SRC/target/debug/devforge" --version
"$DEVFORGE_SRC/target/debug/devforge" --help
"$DEVFORGE_SRC/target/debug/devforge" install identity
```

`--version` prints `devforge 0.1.0`; it does not identify the source revision.
`--help` lists the subcommands present in this build. A name that is not
listed is not available: the CLI answers
`error: unrecognized subcommand '<name>'` with exit 2 and runs nothing.

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
