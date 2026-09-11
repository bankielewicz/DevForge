//! Deterministic receipt checks for a checkpoint/transfer handoff.
//!
//! A compiled port of the contribution-context expert's
//! `scripts/check_receipt.py`, whose observable behavior it reproduces exactly:
//! the same stdout lines in the same order, the same exit codes, the same token
//! grammar and the same scope statements. That script stays in the DevForgeAI
//! package unchanged as the legacy baseline and as the oracle `tests/receipt.rs`
//! compares against.
//!
//! Two modes, kept separate because they establish different things.
//!
//! Receipt verification (`--expected-sha256`) is a guarantee: the claim is 64
//! lowercase hex characters, and recomputing SHA-256 over the file's bytes
//! reproduces it. The comparison is byte-for-byte in full even when the format
//! is wrong, so an abbreviation is never accepted as a prefix match.
//!
//! Self-receipt inspection (`--self-receipt-inspection`) is NOT a guarantee. A
//! document must not carry a receipt for itself, but that is a claim about
//! meaning and this code only sees text, so it reports rather than adjudicates.
//! `literal_self_digest` cannot be made to fail by construction, because a
//! SHA-256 fixed point is not reachable by appending a digest to the text being
//! hashed; it is retained for completeness and is explicitly not a tested
//! guarantee. Every other digest occurrence is listed with its line number and
//! reported COULD_NOT_RUN for separate inspection by the author.
//!
//! Neither mode establishes that the content is correct, complete, authorized or
//! accepted, or that the workflow's no-self-receipt rule was honoured. That rule
//! is upheld by the documented write order, not by this command.
use clap::Subcommand;
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};

const EXIT_PASS: i32 = 0;
const EXIT_FAIL: i32 = 2;
const EXIT_UNREADABLE: i32 = 3;
const EXIT_BAD_INVOCATION: i32 = 4;
const EXIT_UNADJUDICATED: i32 = 5;

const SCOPE: &str = "scope=receipt verification is byte identity and format only; self-receipt inspection is a listing, not a guarantee; neither is semantic acceptance";

#[derive(Subcommand)]
pub enum Action {
    /// Verify a handoff receipt, and separately list digest occurrences for
    /// author inspection. Receipt verification is a guarantee; self-receipt
    /// inspection is a listing, not a guarantee.
    Check {
        /// Path to the file being receipted.
        #[arg(long)]
        file: PathBuf,
        /// The 64-character lowercase digest to verify; omit for inspection-only use.
        #[arg(long)]
        expected_sha256: Option<String>,
        /// List digest occurrences this command cannot adjudicate, for separate inspection.
        #[arg(long)]
        self_receipt_inspection: bool,
    },
}

/// Python's `str.splitlines()` boundaries, so line numbers match the legacy
/// script on every separator it recognised, not only `\n`.
fn line_break(c: char) -> bool {
    matches!(
        c,
        '\n' | '\r'
            | '\u{b}'
            | '\u{c}'
            | '\u{1c}'
            | '\u{1d}'
            | '\u{1e}'
            | '\u{85}'
            | '\u{2028}'
            | '\u{2029}'
    )
}

fn split_lines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0usize;
    let mut iter = text.char_indices().peekable();
    while let Some((index, character)) = iter.next() {
        if !line_break(character) {
            continue;
        }
        lines.push(&text[start..index]);
        let mut end = index + character.len_utf8();
        if character == '\r' && iter.peek().is_some_and(|(_, next)| *next == '\n') {
            let (next_index, next) = iter.next().expect("peeked newline");
            end = next_index + next.len_utf8();
        }
        start = end;
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// A Python `\w` character: Unicode alphanumeric, or underscore.
fn word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// Count the `\b[0-9a-fA-F]{64}\b` matches in one line.
///
/// Every hex digit is a word character, so the word boundaries on both sides can
/// only be satisfied by a maximal run of word characters that is exactly 64
/// characters long and entirely hexadecimal. A 63- or 65-character run, or one
/// touching any other word character, matches nothing — which is also what the
/// legacy non-overlapping scan produced.
fn digest_tokens(line: &str) -> usize {
    let mut found = 0;
    let mut run: Vec<char> = Vec::new();
    for character in line.chars().chain(std::iter::once(' ')) {
        if word(character) {
            run.push(character);
            continue;
        }
        if run.len() == 64 && run.iter().all(char::is_ascii_hexdigit) {
            found += 1;
        }
        run.clear();
    }
    found
}

/// Python's `os.path.basename`: everything after the last `/`, which is empty
/// when the path ends in a separator. `Path::file_name` differs there.
fn basename(path: &Path) -> String {
    let text = path.to_string_lossy();
    match text.rfind('/') {
        Some(index) => text[index + 1..].to_string(),
        None => text.into_owned(),
    }
}

/// The `OSError` subclass and message Python would have printed for this errno.
fn cause(error: &std::io::Error, path: &Path) -> String {
    let errno = error.raw_os_error();
    let class = match errno {
        Some(1) | Some(13) => "PermissionError",
        Some(2) => "FileNotFoundError",
        Some(4) => "InterruptedError",
        Some(11) => "BlockingIOError",
        Some(17) => "FileExistsError",
        Some(20) => "NotADirectoryError",
        Some(21) => "IsADirectoryError",
        _ => "OSError",
    };
    let rendered = error.to_string();
    // Rust renders an OS error as "<strerror> (os error N)"; Python prints
    // "[Errno N] <strerror>: '<path>'" with the same libc string.
    let strerror = match rendered.rfind(" (os error ") {
        Some(index) => &rendered[..index],
        None => rendered.as_str(),
    };
    match errno {
        Some(number) => format!(
            "{class}: [Errno {number}] {strerror}: '{}'",
            path.to_string_lossy()
        ),
        None => format!("{class}: {rendered}"),
    }
}

fn report(name: &str, outcome: &str, detail: &str) -> bool {
    println!("check={name} outcome={outcome} {detail}");
    outcome == "FAIL"
}

fn check(file: &Path, expected: Option<&str>, inspect: bool) -> i32 {
    if expected.is_none() && !inspect {
        println!(
            "check=invocation outcome=COULD_NOT_RUN cause=nothing asserted: pass --expected-sha256, --self-receipt-inspection, or both"
        );
        println!("overall=COULD_NOT_RUN");
        return EXIT_BAD_INVOCATION;
    }
    let data = match fs::read(file) {
        Ok(data) => data,
        Err(error) => {
            println!(
                "check=read_file outcome=COULD_NOT_RUN cause={}",
                cause(&error, file)
            );
            println!("overall=COULD_NOT_RUN");
            return EXIT_UNREADABLE;
        }
    };
    let actual = format!("{:x}", Sha256::digest(&data));
    println!("file={}", file.to_string_lossy());
    println!("computed_sha256={actual}");

    let mut failed = false;
    let mut unadjudicated = false;

    if let Some(claimed) = expected {
        let well_formed = claimed.len() == 64
            && claimed
                .bytes()
                .all(|b| b.is_ascii_digit() || b"abcdef".contains(&b));
        failed |= report(
            "digest_format",
            if well_formed { "PASS" } else { "FAIL" },
            &format!(
                "claimed_length={} (a receipt is 64 lowercase hex characters)",
                claimed.chars().count()
            ),
        );
        // Compared in full even when the format is wrong, so an abbreviation is
        // never accepted as a prefix match: only byte-for-byte equality passes.
        failed |= report(
            "digest_matches_bytes",
            if claimed == actual { "PASS" } else { "FAIL" },
            &format!("claimed={claimed} computed={actual}"),
        );
    }

    if inspect {
        let text = String::from_utf8_lossy(&data);
        let mut occurrences = 0usize;
        let mut lines: Vec<usize> = Vec::new();
        for (index, line) in split_lines(&text).iter().enumerate() {
            let found = digest_tokens(line);
            if found > 0 {
                occurrences += found;
                lines.push(index + 1);
            }
        }

        if occurrences == 0 {
            report(
                "no_digest_present",
                "PASS",
                "the file contains no 64-hex token, so it records no receipt for anything, itself included",
            );
        } else {
            report(
                "no_digest_present",
                "COULD_NOT_RUN",
                &format!(
                    "{occurrences} digest occurrence(s) present, so absence of a self-receipt is not established by this check"
                ),
            );
        }

        if data
            .windows(actual.len())
            .any(|window| window == actual.as_bytes())
        {
            failed |= report(
                "literal_self_digest",
                "FAIL",
                "the file contains its own final digest",
            );
        } else {
            report(
                "literal_self_digest",
                "PASS",
                "the file does not contain its own final digest. NON-DISCRIMINATING: a SHA-256 fixed point is not constructible, so this predicate cannot be made to fail and is not a tested guarantee",
            );
        }

        if occurrences > 0 {
            let listed: Vec<String> = lines.iter().map(usize::to_string).collect();
            report(
                "unadjudicated_digest_occurrences",
                "COULD_NOT_RUN",
                &format!(
                    "lines [{}] carry a 64-hex token. This script cannot tell a legitimate reference to another file from a receipt for this one, including forms split across lines. Inspect these lines and confirm none is a receipt for {}",
                    listed.join(", "),
                    basename(file)
                ),
            );
            unadjudicated = true;
        }
    }

    if failed {
        println!("overall=FAIL");
        println!("{SCOPE}");
        return EXIT_FAIL;
    }
    if unadjudicated {
        println!("overall=COULD_NOT_RUN");
        println!("{SCOPE}");
        return EXIT_UNADJUDICATED;
    }
    println!("overall=PASS");
    println!("{SCOPE}");
    EXIT_PASS
}

pub fn main(action: &Action) {
    let Action::Check {
        file,
        expected_sha256,
        self_receipt_inspection,
    } = action;
    std::process::exit(check(
        file,
        expected_sha256.as_deref(),
        *self_receipt_inspection,
    ));
}
