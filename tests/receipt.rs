//! Black-box acceptance tests for `devforge receipt check`, the compiled
//! replacement for the contribution-context expert's `scripts/check_receipt.py`.
//!
//! The legacy script is the oracle: `the_legacy_checker_and_the_compiled_command_agree`
//! runs it on every fixture below and compares stdout byte for byte and the exit
//! code. That script is a shipped evaluation-adjacent helper in DevForgeAI and is
//! not modified here.
//!
//! Receipt verification is byte identity and format only; self-receipt inspection
//! is a listing, not a guarantee. Neither is semantic acceptance, and nothing in
//! this suite claims otherwise.
use sha2::{Digest, Sha256};
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};

const BIN: &str = env!("CARGO_BIN_EXE_devforge");
const PYTHON: &str = "/usr/bin/python3";
/// The shipped legacy helper this action replaces; the oracle, never modified.
const LEGACY: &str = "/home/bryan/Projects/DevForge/framework/DevForgeAI/project-experts/claude/devforgeai-contribution-context/scripts/check_receipt.py";
const SCOPE: &str = "scope=receipt verification is byte identity and format only; self-receipt inspection is a listing, not a guarantee; neither is semantic acceptance";

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct Temp(PathBuf);
impl Drop for Temp {
    fn drop(&mut self) {
        // Restore any mode this suite removed so the tree is always removable.
        if let Ok(entries) = fs::read_dir(&self.0) {
            for entry in entries.flatten() {
                let _ = fs::set_permissions(entry.path(), fs::Permissions::from_mode(0o755));
            }
        }
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn temp() -> Temp {
    let path = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!(
        "devforge-receipt-{}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir_all(&path).unwrap();
    Temp(path)
}

fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

struct Run {
    code: i32,
    stdout: String,
    stderr: String,
}

fn invoke(program: &Path, leading: &[&str], args: &[&str]) -> Run {
    let output = Command::new(program)
        .args(leading)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .unwrap();
    Run {
        code: output.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    }
}

/// `devforge receipt check ...`
/// Recorded parity exception 3: Rust's `char::is_alphanumeric()` is a strict
/// superset of Python's `isalnum()` (combining marks such as U+0345 count as
/// word characters here, not there), so a digest abutting one is not a
/// standalone token for the compiled command while the legacy script lists
/// it. The divergence only ever under-reports; this case pins its direction
/// and fails the day the two agree, so the exception can then be retired.
#[test]
fn a_digest_abutting_a_combining_mark_is_the_recorded_word_class_divergence() {
    let dir = temp();
    let digest = sha(b"x");
    let body = format!("{digest}\u{345}\n");
    let file = write(dir.0.as_path(), "combining.md", body.as_bytes());
    let args = [
        "--file",
        file.to_str().unwrap(),
        "--self-receipt-inspection",
    ];
    let compiled = check(&args);
    let python = legacy(&args);
    assert_eq!(
        compiled.code, 0,
        "stdout={} stderr={}",
        compiled.stdout, compiled.stderr
    );
    assert!(
        compiled
            .stdout
            .contains("check=no_digest_present outcome=PASS"),
        "{}",
        compiled.stdout
    );
    assert_eq!(
        python.code, 5,
        "stdout={} stderr={}",
        python.stdout, python.stderr
    );
    assert!(
        python
            .stdout
            .contains("check=no_digest_present outcome=COULD_NOT_RUN"),
        "{}",
        python.stdout
    );
}

fn check(args: &[&str]) -> Run {
    invoke(Path::new(BIN), &["receipt", "check"], args)
}

/// `python3 <legacy script> ...`
fn legacy(args: &[&str]) -> Run {
    invoke(Path::new(PYTHON), &[LEGACY], args)
}

fn write(dir: &Path, name: &str, bytes: &[u8]) -> PathBuf {
    let path = dir.join(name);
    fs::write(&path, bytes).unwrap();
    path
}

fn lines(run: &Run) -> Vec<&str> {
    run.stdout.lines().collect()
}

// ---- exit codes ----------------------------------------------------------

#[test]
fn an_invocation_that_asserts_nothing_is_refused_without_reading_the_file() {
    let dir = temp();
    // The file exists and would pass, so exit 4 is about the invocation alone.
    let file = write(dir.0.as_path(), "receipt.md", b"content\n");
    let run = check(&["--file", file.to_str().unwrap()]);
    assert_eq!(run.code, 4, "stdout={} stderr={}", run.stdout, run.stderr);
    assert_eq!(
        lines(&run),
        vec![
            "check=invocation outcome=COULD_NOT_RUN cause=nothing asserted: pass --expected-sha256, --self-receipt-inspection, or both",
            "overall=COULD_NOT_RUN",
        ]
    );
    // Neither the file line nor the scope line is printed for this outcome.
    assert!(!run.stdout.contains("file="), "{}", run.stdout);
    assert!(!run.stdout.contains("scope="), "{}", run.stdout);
    // A missing file is not even reached.
    let absent = dir.0.join("absent.md");
    let run = check(&["--file", absent.to_str().unwrap()]);
    assert_eq!(run.code, 4, "{}", run.stdout);
}

#[test]
fn a_matching_receipt_passes() {
    let dir = temp();
    let body = b"handoff body\n";
    let file = write(dir.0.as_path(), "handoff.md", body);
    let digest = sha(body);
    let run = check(&[
        "--file",
        file.to_str().unwrap(),
        "--expected-sha256",
        &digest,
    ]);
    assert_eq!(run.code, 0, "stdout={} stderr={}", run.stdout, run.stderr);
    assert_eq!(
        lines(&run),
        vec![
            format!("file={}", file.display()).as_str(),
            format!("computed_sha256={digest}").as_str(),
            "check=digest_format outcome=PASS claimed_length=64 (a receipt is 64 lowercase hex characters)",
            format!("check=digest_matches_bytes outcome=PASS claimed={digest} computed={digest}")
                .as_str(),
            "overall=PASS",
            SCOPE,
        ]
    );
}

#[test]
fn a_wrong_or_malformed_claim_fails_and_is_never_a_prefix_match() {
    let dir = temp();
    let body = b"handoff body\n";
    let file = write(dir.0.as_path(), "handoff.md", body);
    let digest = sha(body);
    let other = sha(b"something else");
    // Right shape, wrong bytes: only digest_matches_bytes fails.
    let run = check(&[
        "--file",
        file.to_str().unwrap(),
        "--expected-sha256",
        &other,
    ]);
    assert_eq!(run.code, 2, "{}", run.stdout);
    assert!(run.stdout.contains("check=digest_format outcome=PASS"));
    assert!(
        run.stdout.contains(
            format!("check=digest_matches_bytes outcome=FAIL claimed={other} computed={digest}")
                .as_str()
        )
    );
    assert!(run.stdout.contains("overall=FAIL"));
    assert!(run.stdout.contains(SCOPE));
    // Uppercase, short, long and an abbreviation are all format failures, and the
    // full comparison still runs so a prefix is never accepted.
    let upper = digest.to_uppercase();
    let short = &digest[..63];
    let prefix = &digest[..8];
    for (claim, length) in [
        (upper.as_str(), 64),
        (short, 63),
        (prefix, 8),
        ("", 0),
        ("zz", 2),
    ] {
        let run = check(&["--file", file.to_str().unwrap(), "--expected-sha256", claim]);
        assert_eq!(run.code, 2, "claim={claim} stdout={}", run.stdout);
        assert!(
            run.stdout.contains(
                format!("check=digest_format outcome=FAIL claimed_length={length} (a receipt is 64 lowercase hex characters)").as_str()
            ),
            "claim={claim} stdout={}",
            run.stdout
        );
        assert!(
            run.stdout
                .contains("check=digest_matches_bytes outcome=FAIL"),
            "claim={claim} stdout={}",
            run.stdout
        );
    }
}

#[test]
fn inspection_of_a_file_with_no_digest_passes_both_predicates() {
    let dir = temp();
    let file = write(
        dir.0.as_path(),
        "clean.md",
        b"# Handoff\n\nNo digests here.\n",
    );
    let run = check(&[
        "--file",
        file.to_str().unwrap(),
        "--self-receipt-inspection",
    ]);
    assert_eq!(run.code, 0, "stdout={} stderr={}", run.stdout, run.stderr);
    assert!(run.stdout.contains(
        "check=no_digest_present outcome=PASS the file contains no 64-hex token, so it records no receipt for anything, itself included"
    ), "{}", run.stdout);
    assert!(
        run.stdout.contains("check=literal_self_digest outcome=PASS the file does not contain its own final digest. NON-DISCRIMINATING: a SHA-256 fixed point is not constructible, so this predicate cannot be made to fail and is not a tested guarantee"),
        "{}",
        run.stdout
    );
    assert!(!run.stdout.contains("unadjudicated"), "{}", run.stdout);
    assert!(run.stdout.contains("overall=PASS"));
}

#[test]
fn a_digest_for_another_file_is_listed_unadjudicated_with_its_line_numbers() {
    let dir = temp();
    // A file cannot carry its own digest (no SHA-256 fixed point is constructible),
    // so the discriminating case is a digest that names something else.
    let other = sha(b"the referenced record\n");
    let body =
        format!("# Handoff\n\nrecord-a.md {other}\n\nprose\n\nrecord-b.md {other}\nlast line\n");
    let file = write(dir.0.as_path(), "handoff.md", body.as_bytes());
    let run = check(&[
        "--file",
        file.to_str().unwrap(),
        "--self-receipt-inspection",
    ]);
    assert_eq!(run.code, 5, "stdout={} stderr={}", run.stdout, run.stderr);
    assert!(
        run.stdout.contains("check=no_digest_present outcome=COULD_NOT_RUN 2 digest occurrence(s) present, so absence of a self-receipt is not established by this check"),
        "{}",
        run.stdout
    );
    assert!(
        run.stdout
            .contains("check=literal_self_digest outcome=PASS"),
        "{}",
        run.stdout
    );
    assert!(
        run.stdout.contains("check=unadjudicated_digest_occurrences outcome=COULD_NOT_RUN lines [3, 7] carry a 64-hex token."),
        "{}",
        run.stdout
    );
    assert!(
        run.stdout
            .contains("confirm none is a receipt for handoff.md"),
        "{}",
        run.stdout
    );
    assert!(run.stdout.contains("overall=COULD_NOT_RUN"));
    assert!(run.stdout.contains(SCOPE));
}

#[test]
fn a_token_is_only_a_digest_when_it_stands_alone_as_sixty_four_hex_characters() {
    let dir = temp();
    let digest = sha(b"x");
    // 63 hex, 65 hex, a longer word-run and non-hex neighbours are all ignored;
    // ordinary punctuation around an exact 64-hex run is not.
    let body = format!(
        "a {}\nb {}z\nc {}\nd word{}\ne (\"{}\")\n",
        &digest[..63],
        digest,
        digest.clone() + "f",
        digest,
        digest
    );
    let file = write(dir.0.as_path(), "mixed.md", body.as_bytes());
    let run = check(&[
        "--file",
        file.to_str().unwrap(),
        "--self-receipt-inspection",
    ]);
    assert_eq!(run.code, 5, "stdout={} stderr={}", run.stdout, run.stderr);
    assert!(
        run.stdout.contains("lines [5] carry a 64-hex token."),
        "only the parenthesised token is a standalone 64-hex run: {}",
        run.stdout
    );
}

/// Line numbers follow Python's `str.splitlines()` boundaries, not `str::lines()`:
/// `\r\n`, a bare `\r`, `\x0b` and `\u{85}` all start a new line, and a blank
/// line is still counted. Numbering the same file with `lines()` would report
/// [2, 4, 5] instead of [2, 5, 7].
#[test]
fn line_numbers_follow_every_separator_the_legacy_scan_recognised() {
    let dir = temp();
    let other = sha(b"referenced\n");
    let body =
        format!("line1\r\nline2 {other}\n\nalpha\u{85}beta {other}\ngamma\u{b}delta {other}\n");
    let file = write(dir.0.as_path(), "separators.md", body.as_bytes());
    let run = check(&[
        "--file",
        file.to_str().unwrap(),
        "--self-receipt-inspection",
    ]);
    assert_eq!(run.code, 5, "stdout={} stderr={}", run.stdout, run.stderr);
    assert!(
        run.stdout.contains("lines [2, 5, 7] carry a 64-hex token."),
        "{}",
        run.stdout
    );
    assert!(
        run.stdout
            .contains("3 digest occurrence(s) present, so absence of a"),
        "{}",
        run.stdout
    );
}

#[test]
fn both_commands_run_together_and_the_failure_outranks_the_listing() {
    let dir = temp();
    let other = sha(b"referenced\n");
    let body = format!("line one\nrecord {other}\n");
    let file = write(dir.0.as_path(), "handoff.md", body.as_bytes());
    let digest = sha(body.as_bytes());
    // Both requested, receipt correct: the unadjudicated listing decides, exit 5.
    let run = check(&[
        "--file",
        file.to_str().unwrap(),
        "--expected-sha256",
        &digest,
        "--self-receipt-inspection",
    ]);
    assert_eq!(run.code, 5, "stdout={} stderr={}", run.stdout, run.stderr);
    let printed = lines(&run);
    assert_eq!(printed[0], format!("file={}", file.display()));
    assert_eq!(printed[1], format!("computed_sha256={digest}"));
    assert!(printed[2].starts_with("check=digest_format outcome=PASS"));
    assert!(printed[3].starts_with("check=digest_matches_bytes outcome=PASS"));
    assert!(printed[4].starts_with("check=no_digest_present outcome=COULD_NOT_RUN"));
    assert!(printed[5].starts_with("check=literal_self_digest outcome=PASS"));
    assert!(printed[6].starts_with("check=unadjudicated_digest_occurrences outcome=COULD_NOT_RUN"));
    assert_eq!(printed[7], "overall=COULD_NOT_RUN");
    assert_eq!(printed[8], SCOPE);
    // A failing receipt outranks the listing: exit 2.
    let run = check(&[
        "--file",
        file.to_str().unwrap(),
        "--expected-sha256",
        &sha(b"wrong"),
        "--self-receipt-inspection",
    ]);
    assert_eq!(run.code, 2, "{}", run.stdout);
    assert!(run.stdout.contains("overall=FAIL"));
}

#[test]
fn an_empty_file_and_non_utf8_bytes_are_read_and_inspected() {
    let dir = temp();
    let empty = write(dir.0.as_path(), "empty.md", b"");
    let run = check(&[
        "--file",
        empty.to_str().unwrap(),
        "--self-receipt-inspection",
    ]);
    assert_eq!(run.code, 0, "stdout={} stderr={}", run.stdout, run.stderr);
    assert!(
        run.stdout
            .contains(&format!("computed_sha256={}", sha(b"")))
    );
    assert!(run.stdout.contains("check=no_digest_present outcome=PASS"));
    // Invalid UTF-8 decodes with replacement for the line scan; the digest is
    // still computed over the raw bytes, and a digest beside the bad bytes is found.
    let other = sha(b"referenced\n");
    let mut body = b"first\n\xff\xfe raw ".to_vec();
    body.extend_from_slice(other.as_bytes());
    body.extend_from_slice(b"\ntail\n");
    let binary = write(dir.0.as_path(), "binary.md", &body);
    let run = check(&[
        "--file",
        binary.to_str().unwrap(),
        "--self-receipt-inspection",
    ]);
    assert_eq!(run.code, 5, "stdout={} stderr={}", run.stdout, run.stderr);
    assert!(
        run.stdout
            .contains(&format!("computed_sha256={}", sha(&body)))
    );
    assert!(
        run.stdout.contains("lines [2] carry a 64-hex token."),
        "{}",
        run.stdout
    );
}

#[test]
fn an_unreadable_file_names_its_cause_and_asserts_nothing() {
    let dir = temp();
    let absent = dir.0.join("absent.md");
    let run = check(&[
        "--file",
        absent.to_str().unwrap(),
        "--self-receipt-inspection",
    ]);
    assert_eq!(run.code, 3, "stdout={} stderr={}", run.stdout, run.stderr);
    assert_eq!(
        lines(&run),
        vec![
            format!(
                "check=read_file outcome=COULD_NOT_RUN cause=FileNotFoundError: [Errno 2] No such file or directory: '{}'",
                absent.display()
            )
            .as_str(),
            "overall=COULD_NOT_RUN",
        ]
    );
    assert!(!run.stdout.contains("scope="), "{}", run.stdout);
    // A directory is read-refused with its own errno class.
    let folder = dir.0.join("folder");
    fs::create_dir(&folder).unwrap();
    let run = check(&[
        "--file",
        folder.to_str().unwrap(),
        "--expected-sha256",
        &sha(b"x"),
    ]);
    assert_eq!(run.code, 3, "{}", run.stdout);
    assert!(
        run.stdout.contains(
            format!(
                "cause=IsADirectoryError: [Errno 21] Is a directory: '{}'",
                folder.display()
            )
            .as_str()
        ),
        "{}",
        run.stdout
    );
    // An unreadable regular file likewise.
    let denied = write(dir.0.as_path(), "denied.md", b"secret\n");
    fs::set_permissions(&denied, fs::Permissions::from_mode(0o000)).unwrap();
    let run = check(&[
        "--file",
        denied.to_str().unwrap(),
        "--self-receipt-inspection",
    ]);
    fs::set_permissions(&denied, fs::Permissions::from_mode(0o644)).unwrap();
    assert_eq!(run.code, 3, "{}", run.stdout);
    assert!(
        run.stdout.contains(
            format!(
                "cause=PermissionError: [Errno 13] Permission denied: '{}'",
                denied.display()
            )
            .as_str()
        ),
        "{}",
        run.stdout
    );
}

#[test]
fn a_relative_path_is_accepted_and_echoed_as_given() {
    let dir = temp();
    let body = b"relative\n";
    write(dir.0.as_path(), "rel.md", body);
    let output = Command::new(BIN)
        .args(["receipt", "check", "--file", "rel.md", "--expected-sha256"])
        .arg(sha(body))
        .current_dir(&dir.0)
        .stdin(Stdio::null())
        .output()
        .unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    assert_eq!(output.status.code(), Some(0), "{stdout}");
    assert!(stdout.contains("file=rel.md\n"), "{stdout}");
}

// ---- legacy oracle -------------------------------------------------------

/// Every fixture this suite exercises, as (name, bytes, argument builder).
fn fixtures(dir: &Path) -> Vec<(String, Vec<String>)> {
    let other = sha(b"the referenced record\n");
    let body = format!("# Handoff\n\nrecord-a.md {other}\n\nprose\n\nrecord-b.md {other}\nlast\n");
    let plain = b"handoff body\n".to_vec();
    let digest = sha(&plain);
    let mut binary = b"first\n\xff\xfe raw ".to_vec();
    binary.extend_from_slice(other.as_bytes());
    binary.extend_from_slice(b"\ntail\n");
    let mixed = format!(
        "a {}\nb {}z\nc {}\nd word{}\ne (\"{}\")\n",
        &other[..63],
        other,
        other.clone() + "f",
        other,
        other
    );

    let plain_path = dir.join("plain.md");
    fs::write(&plain_path, &plain).unwrap();
    let listed_path = dir.join("listed.md");
    fs::write(&listed_path, body.as_bytes()).unwrap();
    let binary_path = dir.join("binary.md");
    fs::write(&binary_path, &binary).unwrap();
    let mixed_path = dir.join("mixed.md");
    fs::write(&mixed_path, mixed.as_bytes()).unwrap();
    // Every separator Python's splitlines() recognises, plus a blank line.
    let separators = format!(
        "line1\r\nline2 {other}\n\nalpha\u{85}beta {other}\ngamma\u{b}delta {other}\rtail {other}\n"
    );
    let separators_path = dir.join("separators.md");
    fs::write(&separators_path, separators.as_bytes()).unwrap();
    let empty_path = dir.join("empty.md");
    fs::write(&empty_path, b"").unwrap();
    let clean_path = dir.join("clean.md");
    fs::write(&clean_path, b"# Handoff\n\nNo digests here.\n").unwrap();
    let folder = dir.join("folder");
    fs::create_dir(&folder).unwrap();
    let absent = dir.join("absent.md");

    let p = |path: &Path| path.to_str().unwrap().to_string();
    let listed_digest = sha(body.as_bytes());
    vec![
        ("nothing asserted", vec!["--file".into(), p(&plain_path)]),
        (
            "receipt passes",
            vec![
                "--file".into(),
                p(&plain_path),
                "--expected-sha256".into(),
                digest.clone(),
            ],
        ),
        (
            "receipt mismatched",
            vec![
                "--file".into(),
                p(&plain_path),
                "--expected-sha256".into(),
                other.clone(),
            ],
        ),
        (
            "receipt uppercase",
            vec![
                "--file".into(),
                p(&plain_path),
                "--expected-sha256".into(),
                digest.to_uppercase(),
            ],
        ),
        (
            "receipt truncated",
            vec![
                "--file".into(),
                p(&plain_path),
                "--expected-sha256".into(),
                digest[..8].to_string(),
            ],
        ),
        (
            "receipt empty claim",
            vec![
                "--file".into(),
                p(&plain_path),
                "--expected-sha256".into(),
                String::new(),
            ],
        ),
        (
            "inspection clean",
            vec![
                "--file".into(),
                p(&clean_path),
                "--self-receipt-inspection".into(),
            ],
        ),
        (
            "inspection empty file",
            vec![
                "--file".into(),
                p(&empty_path),
                "--self-receipt-inspection".into(),
            ],
        ),
        (
            "inspection listed",
            vec![
                "--file".into(),
                p(&listed_path),
                "--self-receipt-inspection".into(),
            ],
        ),
        (
            "inspection mixed tokens",
            vec![
                "--file".into(),
                p(&mixed_path),
                "--self-receipt-inspection".into(),
            ],
        ),
        (
            "inspection non-utf8",
            vec![
                "--file".into(),
                p(&binary_path),
                "--self-receipt-inspection".into(),
            ],
        ),
        (
            "inspection line separators",
            vec![
                "--file".into(),
                p(&separators_path),
                "--self-receipt-inspection".into(),
            ],
        ),
        (
            "both, listing decides",
            vec![
                "--file".into(),
                p(&listed_path),
                "--expected-sha256".into(),
                listed_digest,
                "--self-receipt-inspection".into(),
            ],
        ),
        (
            "both, failure outranks",
            vec![
                "--file".into(),
                p(&listed_path),
                "--expected-sha256".into(),
                other.clone(),
                "--self-receipt-inspection".into(),
            ],
        ),
        (
            "unreadable missing",
            vec![
                "--file".into(),
                p(&absent),
                "--self-receipt-inspection".into(),
            ],
        ),
        (
            "unreadable directory",
            vec![
                "--file".into(),
                p(&folder),
                "--expected-sha256".into(),
                digest,
            ],
        ),
    ]
    .into_iter()
    .map(|(name, args)| (name.to_string(), args))
    .collect()
}

/// The discriminating parity case: the unchanged shipped Python helper and the
/// compiled command produce the same stdout, byte for byte, and the same exit
/// code, on every fixture this suite exercises.
#[test]
fn the_legacy_checker_and_the_compiled_command_agree() {
    assert!(
        Path::new(PYTHON).is_file(),
        "the legacy oracle interpreter must be present: {PYTHON}"
    );
    assert!(
        Path::new(LEGACY).is_file(),
        "the legacy oracle script must be present: {LEGACY}"
    );
    let dir = temp();
    for (name, args) in fixtures(dir.0.as_path()) {
        let refs: Vec<&str> = args.iter().map(String::as_str).collect();
        let rust = check(&refs);
        let python = legacy(&refs);
        assert_eq!(
            rust.code, python.code,
            "{name}: exit codes differ\nrust stdout={}\npython stdout={}",
            rust.stdout, python.stdout
        );
        assert_eq!(
            rust.stdout, python.stdout,
            "{name}: stdout differs (rust stderr={})",
            rust.stderr
        );
    }
}

/// The permission-denied fixture is kept out of the shared table because it must
/// be restored immediately; it is compared the same way.
#[test]
fn the_legacy_checker_agrees_on_an_unreadable_regular_file() {
    assert!(Path::new(LEGACY).is_file(), "missing oracle: {LEGACY}");
    let dir = temp();
    let denied = write(dir.0.as_path(), "denied.md", b"secret\n");
    let path = denied.to_str().unwrap().to_string();
    let args = ["--file", path.as_str(), "--self-receipt-inspection"];
    fs::set_permissions(&denied, fs::Permissions::from_mode(0o000)).unwrap();
    let rust = check(&args);
    let python = legacy(&args);
    fs::set_permissions(&denied, fs::Permissions::from_mode(0o644)).unwrap();
    assert_eq!(rust.code, 3, "{}", rust.stdout);
    assert_eq!(python.code, 3, "{}", python.stdout);
    assert_eq!(rust.stdout, python.stdout);
}
