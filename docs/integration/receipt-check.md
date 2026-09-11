# Handoff receipt checks (`devforge receipt check`)

Compiled Rust owns the deterministic receipt checks a checkpoint/transfer handoff
needs. `devforge receipt check` replaces
`project-experts/claude/devforgeai-contribution-context/scripts/check_receipt.py`
in DevForgeAI, which stays in that package unchanged as the legacy baseline and
as the oracle `tests/receipt.rs` compares against. Its retirement is a later
owner decision.

Nothing here is semantic acceptance. Receipt verification is byte identity and
format only; self-receipt inspection is a listing, not a guarantee.

## Command

```bash
devforge receipt check --file <path> [--expected-sha256 <digest>] [--self-receipt-inspection]
```

`--file` is required and may be relative; it is echoed back exactly as given.
At least one of the two commands must be requested, or the invocation asserts
nothing and is refused.

### Command 1 — receipt verification (`--expected-sha256`), a guarantee

- `digest_format` — the claim is exactly 64 lowercase hexadecimal characters.
- `digest_matches_bytes` — recomputing SHA-256 over the file's bytes reproduces
  the claim. Compared in full even when the format is wrong, so an abbreviation
  is never accepted as a prefix match.

### Command 2 — self-receipt inspection (`--self-receipt-inspection`), NOT a guarantee

- `no_digest_present` — PASS only when the file contains no 64-hex token at all.
- `literal_self_digest` — FAIL when the file literally contains its own final
  digest. **Non-discriminating**: a SHA-256 fixed point is not reachable by
  appending a digest to the text being hashed, so this predicate cannot be made
  to fail and is not a tested guarantee.
- `unadjudicated_digest_occurrences` — every other digest occurrence is listed
  with its line number and reported COULD_NOT_RUN, because the command cannot
  tell a legitimate reference to another file from a receipt for this one,
  including a receipt split across lines. Those lines need the author's reading.

A token counts only when it is a whole 64-character hexadecimal word: a 63- or
65-character run, or one touching another word character, is not a digest.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | every requested check passed (PASS) |
| 2 | a requested check failed (FAIL) |
| 3 | the file could not be read (COULD_NOT_RUN) |
| 4 | the invocation asserted nothing (COULD_NOT_RUN) |
| 5 | inspection ran but could not adjudicate one or more occurrences (COULD_NOT_RUN) |

A failure outranks an unadjudicated listing. Output is one
`check=<name> outcome=<label>` line per check plus `overall=<label>`, using only
the vocabulary PASS, FAIL, COULD_NOT_RUN. The `file=` and `computed_sha256=`
lines are printed only once the file has been read, and the `scope=` line only
for outcomes 0, 2 and 5 — exactly as the legacy script printed them.

## What neither command establishes

That the content is correct, complete, authorized or accepted, or that the
workflow's no-self-receipt rule was honoured. That rule is upheld by the
documented write order — finish the referenced records, hash them, write the
handoff, read it back, and deliver its digest outside its bytes — not by this
command.

## Callers

| Caller | Status |
| --- | --- |
| DevForgeAI `project-experts/claude/devforgeai-contribution-context/SKILL.md` | Repointed to `<selected devforge executable> receipt check` |
| DevForgeAI `.../references/checkpoint-transfer.md` | Repointed; both example invocations now name the executable |
| DevForgeAI `.../scripts/check_receipt.py` | Unchanged legacy baseline, and the oracle this suite runs |

The brainstorm packages ship three further Python helpers
(`providers/claude/.../devforge-brainstorm/scripts/check_artifact.py`,
`providers/codex/.../devforge-brainstorm/scripts/artifact_receipt.py`,
`providers/codex/.../devforge-brainstorm/scripts/delivery_check.py`). No live
`SKILL.md`, reference or asset names any of them, so no package instructs a model
to run them. They are neither ported nor deleted here.

## Legacy parity

`tests/receipt.rs` runs `python3 <the shipped script>` on every fixture and
compares **stdout byte for byte and the exit code**: 15 fixtures in
`the_legacy_checker_and_the_compiled_command_agree` plus a permission-denied file
in `the_legacy_checker_agrees_on_an_unreadable_regular_file`. The oracle asserts
the interpreter and script exist rather than skipping.

Deliberate reimplementations, each matched to the legacy behavior rather than to
the nearest Rust idiom:

- **Line numbering** uses Python's `str.splitlines()` boundaries (`\n`, `\r`,
  `\r\n`, `\v`, `\f`, `\x1c`, `\x1d`, `\x1e`, `\x85`, ` `, ` `), not
  Rust's `lines()`, which splits on `\n` only.
- **The token grammar** reimplements `\b[0-9a-fA-F]{64}\b` by hand, with no regex
  crate. Every hex digit is a word character, so the boundaries can only be
  satisfied by a maximal run of Unicode word characters that is exactly 64
  characters long and entirely hexadecimal.
- **`claimed_length`** counts characters, as Python's `len()` does on a `str`.
- **`basename`** takes everything after the last `/`, as `os.path.basename` does;
  `Path::file_name` differs for a path ending in a separator.
- **Decoding** for the line scan is UTF-8 with replacement; the digest is always
  computed over the raw bytes.

## Parity exceptions

1. **`cause=` text for an unreadable file.** The legacy message is Python's
   `f"{error.__class__.__name__}: {error}"`. The compiled command reproduces the
   class name, the `[Errno N]`, the libc string and the quoted path for the
   cases a reader can reach: `FileNotFoundError` (2), `PermissionError` (1, 13),
   `IsADirectoryError` (21), `NotADirectoryError` (20), `FileExistsError` (17),
   `InterruptedError` (4), `BlockingIOError` (11), and `OSError` for every other
   errno. The three cases the tests pin — not found, is-a-directory and
   permission denied — are byte-identical to the legacy output. An errno outside
   that mapping whose Python class is a subclass of `OSError` would be reported
   as `OSError`; none is reachable by reading an ordinary file.
2. **Argument-parsing errors** come from `clap` rather than `argparse`, so a
   missing `--file` or an unknown flag produces different usage text on stderr.
   Both exit 2, and neither is part of the checked vocabulary.
