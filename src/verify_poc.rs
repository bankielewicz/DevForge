//! Compiled port of `scripts/verify_poc.py`: the bounded local check run.
//!
//! It runs a fixed stage list in order, stops at the first failure, and saves
//! each stage's combined output plus a report and a source inventory. A stage
//! `PASS` records that a command exited 0. It is not behavioral acceptance, and
//! the report keeps `model_behavior: NOT_EVALUATED` and `hosted_ci: NOT_RUN`.
use crate::demo::{Finished, bounded, civil, parts, resolve};
use anyhow::{Context, Result, ensure};
use clap::Args;
use serde::Serialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, SystemTime};

const STAGE_DEADLINE: Duration = Duration::from_secs(180);
const EXCLUDED: [&str; 4] = [".git", "target", ".poc", "__pycache__"];
const SCOPE: &str = "Local structural checks, isolated-runner gate tests, installer tests, and scripted project fixtures.";

#[derive(Args)]
pub struct Options {
    /// DevForgeAI checkout the structural and fixture stages inspect.
    #[arg(long)]
    framework: PathBuf,
    /// DevForge checkout the stages run in.
    #[arg(long)]
    repo: PathBuf,
    /// Evidence root; defaults to <repo>/docs/validation.
    #[arg(long)]
    evidence_root: Option<PathBuf>,
    /// cargo executable for the format, clippy and build stages. Defaults to
    /// $CARGO, then `cargo` on PATH. Select the CI toolchain explicitly with
    /// --cargo "$(rustup which --toolchain 1.94.0 cargo)".
    #[arg(long)]
    cargo: Option<PathBuf>,
}

#[derive(Serialize)]
struct Check {
    check: &'static str,
    command: Vec<String>,
    exit_code: i32,
    status: &'static str,
}

#[derive(Serialize)]
struct Report {
    schema: u32,
    created_at: String,
    checks: Vec<Check>,
    sources_sha256: BTreeMap<String, String>,
    model_behavior: &'static str,
    hosted_ci: &'static str,
    scope: &'static str,
}

pub(crate) fn main(options: &Options) {
    match run(options) {
        Ok(status) => std::process::exit(status),
        Err(error) => {
            println!(
                "{}",
                json!({"status":"BLOCKED","reason":format!("{error:#}")})
            );
            std::process::exit(2);
        }
    }
}

fn run(options: &Options) -> Result<i32> {
    let framework = resolve(&options.framework)?;
    let repo = resolve(&options.repo)?;
    // Both selections are checked before any evidence directory is created.
    ensure!(
        framework.is_dir(),
        "--framework must be an existing directory: {}",
        framework.display()
    );
    ensure!(
        repo.is_dir(),
        "--repo must be an existing directory: {}",
        repo.display()
    );
    let cargo = options
        .cargo
        .clone()
        .or_else(|| std::env::var_os("CARGO").map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("cargo"));
    let executable = std::env::current_exe().context("this executable's own path")?;

    let (date, time, micros) = civil(SystemTime::now());
    let created_at = format!("{date}T{time}{micros:06}Z");
    let evidence = match &options.evidence_root {
        Some(root) => resolve(root)?,
        None => repo.join("docs/validation"),
    }
    .join(&created_at);
    fs::create_dir_all(&evidence).with_context(|| evidence.display().to_string())?;

    let stages = stages(&cargo, &executable, &framework, &repo);
    let total = stages.len();
    let mut checks = Vec::new();
    for (name, argv) in stages {
        let mut command = Command::new(&argv[0]);
        command.args(&argv[1..]).current_dir(&repo);
        let finished: Finished = bounded(&mut command, STAGE_DEADLINE, &repo)?;
        let log = format!("{}{}", finished.stdout, finished.stderr);
        let path = evidence.join(format!("{name}.log"));
        fs::write(&path, &log).with_context(|| path.display().to_string())?;
        let exit_code = finished.code.unwrap_or(-1);
        let status = if exit_code == 0 { "PASS" } else { "FAIL" };
        checks.push(Check {
            check: name,
            command: argv
                .iter()
                .map(|part| part.to_string_lossy().into_owned())
                .collect(),
            exit_code,
            status,
        });
        println!("{name}: {status}");
        if exit_code != 0 {
            eprintln!("{log}");
            break;
        }
    }

    let failed = checks.len() != total || checks.iter().any(|check| check.exit_code != 0);
    let mut sources = BTreeMap::new();
    for (label, root) in [("DevForge", &repo), ("DevForgeAI", &framework)] {
        inventory(root, label, &mut sources);
    }
    let report = Report {
        schema: 1,
        created_at,
        checks,
        sources_sha256: sources,
        model_behavior: "NOT_EVALUATED",
        hosted_ci: "NOT_RUN",
        scope: SCOPE,
    };
    let path = evidence.join("report.json");
    let mut document = serde_json::to_string_pretty(&report)?;
    document.push('\n');
    fs::write(&path, document).with_context(|| path.display().to_string())?;
    println!("Evidence: {}", path.display());
    Ok(if failed { 2 } else { 0 })
}

fn stages(
    cargo: &Path,
    executable: &Path,
    framework: &Path,
    repo: &Path,
) -> Vec<(&'static str, Vec<OsString>)> {
    let argv = |parts: &[&dyn AsRef<std::ffi::OsStr>]| -> Vec<OsString> {
        parts
            .iter()
            .map(|part| part.as_ref().to_os_string())
            .collect()
    };
    vec![
        ("format", argv(&[&cargo, &"fmt", &"--check"])),
        (
            "clippy",
            argv(&[
                &cargo,
                &"clippy",
                &"--locked",
                &"--all-targets",
                &"--",
                &"-D",
                &"warnings",
            ]),
        ),
        ("build", argv(&[&cargo, &"build", &"--locked"])),
        (
            "tests",
            argv(&[
                &"python3",
                &"-m",
                &"unittest",
                &"discover",
                &"-s",
                &"tests",
                &"-p",
                &"test_*.py",
                &"-v",
            ]),
        ),
        (
            "framework-structure",
            argv(&[
                &executable,
                &"validate",
                &"framework",
                &"--framework",
                &framework,
            ]),
        ),
        (
            "mvp-documents",
            argv(&[
                &executable,
                &"validate",
                &"mvp",
                &"--mvp",
                &framework.join("docs/mvp"),
            ]),
        ),
        (
            "fixture-demo",
            argv(&[
                &executable,
                &"demo",
                &"--framework",
                &framework,
                &"--policies",
                &repo.join("policies"),
                &"--output-root",
                &repo.join(".poc"),
            ]),
        ),
    ]
}

/// SHA-256 of every file under `root`, keyed `<label>/<relative>`, excluding the
/// legacy component names and the `docs/validation` evidence prefix.
fn inventory(root: &Path, label: &str, sources: &mut BTreeMap<String, String>) {
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        let Ok(entries) = fs::read_dir(&directory) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let Ok(relative) = path.strip_prefix(root) else {
                continue;
            };
            let components = parts(relative);
            if components
                .iter()
                .any(|part| EXCLUDED.contains(&part.as_str()))
                || components.starts_with(&["docs".to_owned(), "validation".to_owned()])
            {
                continue;
            }
            let Ok(metadata) = fs::symlink_metadata(&path) else {
                continue;
            };
            if metadata.is_dir() {
                pending.push(path);
            } else if metadata.is_file()
                && let Ok(bytes) = fs::read(&path)
            {
                sources.insert(
                    format!("{label}/{}", components.join("/")),
                    format!("{:x}", Sha256::digest(&bytes)),
                );
            }
        }
    }
}
