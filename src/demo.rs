//! Compiled port of `scripts/demo.py`: a repeatable fixture demonstration.
//!
//! It copies the two example seeds out of the selected framework, installs the
//! provider package into each copy with the compiled installer, and drives the
//! real gate lifecycle over them. It calls no model and claims no expert
//! behavioral evaluation: the report records `model_calls: 0` and
//! `model_behavior: NOT_EVALUATED`, and a gate `PASS` is mechanical evidence
//! only.
//!
//! The framework checkout is read, never written: candidates, authority state
//! and the runtime copy all live under `--output-root`, which must be outside
//! the framework.
use anyhow::{Context, Result, anyhow, bail, ensure};
use clap::Args;
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const SLUGS: [&str; 2] = ["notes-sqlite", "notes-json"];
const GATE_DEADLINE: Duration = Duration::from_secs(30);
const INSTALL_DEADLINE: Duration = Duration::from_secs(120);
const SCOPE: &str = "Scripted fixtures exercise real gates; no terminal-model behavior is claimed.";
const AMENDMENT: &str =
    "\nFixture-only amendment: review storage lifetime before a future multi-request story.\n";

#[derive(Args)]
pub struct Options {
    /// DevForgeAI checkout holding examples/<slug>/seed and the provider packages.
    #[arg(long)]
    framework: PathBuf,
    /// Directory holding notes-sqlite.json and notes-json.json.
    #[arg(long)]
    policies: PathBuf,
    /// Evidence root for candidates, authority state and the runtime copy.
    /// Must be outside --framework; the fixtures are never written in place.
    #[arg(long)]
    output_root: PathBuf,
    /// Stop after init, leaving initialized candidates for interactive work.
    #[arg(long)]
    prepare_only: bool,
    /// Reproducible run directory name instead of the timestamp-and-pid default.
    #[arg(long)]
    run_id: Option<String>,
}

#[derive(Serialize)]
struct Event {
    command: Vec<String>,
    exit_code: i32,
    result: Value,
}

#[derive(Serialize)]
struct Project {
    project: String,
    policy: String,
    state: String,
    events: Vec<Event>,
    interactive_prompt: String,
    model_behavior: &'static str,
}

#[derive(Serialize)]
struct Report {
    schema: u32,
    run_id: String,
    runtime: String,
    runtime_sha256: String,
    fixture_execution: &'static str,
    model_calls: u32,
    scope: &'static str,
    projects: Vec<Project>,
}

pub(crate) fn main(options: &Options) {
    if let Err(error) = run(options) {
        println!(
            "{}",
            json!({"status":"BLOCKED","reason":format!("{error:#}")})
        );
        std::process::exit(2);
    }
}

fn run(options: &Options) -> Result<()> {
    let framework = existing_directory(&options.framework, "--framework")?;
    let policies = existing_directory(&options.policies, "--policies")?;
    let output_root = resolve(&options.output_root)?;
    ensure!(
        separate(&output_root, &framework),
        "output root must be outside the framework: {}",
        output_root.display()
    );
    // Every policy is read before the first candidate is copied, so a missing
    // one refuses without leaving a half-prepared run.
    let mut selected = Vec::new();
    for slug in SLUGS {
        let policy = policies.join(format!("{slug}.json"));
        ensure!(
            policy.is_file(),
            "missing fixture policy: {}",
            policy.display()
        );
        selected.push((slug, policy));
    }

    let run_id = match &options.run_id {
        Some(given) => {
            ensure!(
                !given.is_empty()
                    && given
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_'),
                "--run-id must be a single nonempty [A-Za-z0-9_-] component"
            );
            given.clone()
        }
        None => format!("{}-{}", utc_compact(SystemTime::now()), std::process::id()),
    };
    let run_root = output_root.join(&run_id);
    ensure!(
        !run_root.exists(),
        "run directory already exists: {}",
        run_root.display()
    );
    let candidates = run_root.join("candidates");
    let authority = run_root.join("authority");
    fs::create_dir_all(&candidates).with_context(|| candidates.display().to_string())?;
    fs::create_dir_all(&authority).with_context(|| authority.display().to_string())?;

    let runtime = install_runtime(&run_root)?;
    let runtime_sha256 = format!("{:x}", Sha256::digest(fs::read(&runtime)?));
    let executable = std::env::current_exe().context("this executable's own path")?;

    let mut projects = Vec::new();
    for (slug, policy) in selected {
        let fixture = framework.join("examples").join(slug);
        let project = candidates.join(slug);
        let state = authority.join(slug);
        copy_tree(&fixture.join("seed"), &project)?;
        fs::create_dir_all(&state).with_context(|| state.display().to_string())?;
        install(&executable, &framework, &project, &runtime)?;

        let mut events = Vec::new();
        let mut gate = |target: &Path, command: &[&str], success: bool| -> Result<Value> {
            call(
                &executable,
                target,
                &policy,
                &state,
                command,
                success,
                &mut events,
            )
        };
        let bind = format!("experts/{slug}-persistence");
        gate(&project, &["expert", "prepare"], true)?;
        gate(&project, &["expert", "bind", "--expert", &bind], true)?;
        gate(&project, &["check"], true)?;
        gate(&project, &["init"], true)?;
        if !options.prepare_only {
            gate(&project, &["green"], false)?;
            copy_file(
                &fixture.join("changes/tests/test_store.py"),
                &project.join("tests/test_store.py"),
            )?;
            gate(&project, &["red"], true)?;
            copy_file(
                &fixture.join("changes/src/store.py"),
                &project.join("src/store.py"),
            )?;
            gate(&project, &["green"], true)?;
            gate(&project, &["accept"], true)?;
            gate(&project, &["verify"], true)?;
            // The refresh runs in a separate copy, preserving the accepted
            // original and its exact evidence.
            let refresh = candidates.join(format!("{slug}-refresh"));
            copy_tree(&project, &refresh)?;
            let story = refresh.join("docs/story.md");
            let amended = format!("{}{AMENDMENT}", fs::read_to_string(&story)?);
            fs::write(&story, amended).with_context(|| story.display().to_string())?;
            gate(&refresh, &["expert", "status"], true)?;
            gate(&refresh, &["check"], false)?;
            gate(&refresh, &["expert", "bind", "--expert", &bind], true)?;
            gate(&refresh, &["check"], true)?;
        }
        projects.push(Project {
            project: project.display().to_string(),
            policy: policy.display().to_string(),
            state: state.display().to_string(),
            events,
            interactive_prompt: format!(
                "Use devforge-project-expert-creator to review and improve this project's \
                 {slug}-persistence skill against docs/expert-spec.md. Do not modify external \
                 policy or gates."
            ),
            model_behavior: "NOT_EVALUATED",
        });
        let outcome = if options.prepare_only {
            "INITIALIZED"
        } else {
            "VERIFIED + STALE/REFRESH CHECKED"
        };
        println!("{slug}: {outcome}");
    }

    let report = Report {
        schema: 1,
        run_id,
        runtime: runtime.display().to_string(),
        runtime_sha256,
        fixture_execution: "PASS",
        model_calls: 0,
        scope: SCOPE,
        projects,
    };
    let path = run_root.join("demo-report.json");
    let mut document = serde_json::to_string_pretty(&report)?;
    document.push('\n');
    fs::write(&path, document).with_context(|| path.display().to_string())?;
    println!("Report: {}", path.display());
    println!("Candidates: {}", candidates.display());
    Ok(())
}

/// A single-hard-link copy of the running executable, which a delivery-aware
/// installation requires as `--runtime`. Cargo hard-links `target/debug/devforge`,
/// so the running binary itself cannot be selected.
fn install_runtime(run_root: &Path) -> Result<PathBuf> {
    let source = std::env::current_exe().context("this executable's own path")?;
    let directory = run_root.join("runtime");
    fs::create_dir_all(&directory).with_context(|| directory.display().to_string())?;
    let runtime = directory.join("devforge");
    fs::copy(&source, &runtime)
        .with_context(|| format!("copying {} to {}", source.display(), runtime.display()))?;
    fs::set_permissions(&runtime, fs::Permissions::from_mode(0o755))
        .with_context(|| runtime.display().to_string())?;
    Ok(runtime)
}

fn install(executable: &Path, framework: &Path, project: &Path, runtime: &Path) -> Result<()> {
    let mut command = Command::new(executable);
    command
        .arg("--project")
        .arg(project)
        .args(["install", "framework", "--framework"])
        .arg(framework)
        // The Codex plugin ships promoted expert packages that need
        // owner-selected adoption evidence this demonstration does not carry,
        // so only the Claude package is installed.
        .args(["--provider", "claude", "--include-experts", "--runtime"])
        .arg(runtime);
    let finished = bounded(&mut command, INSTALL_DEADLINE, project)?;
    ensure!(
        finished.code == Some(0),
        "installation failed with status {:?}: {}{}",
        finished.code,
        finished.stdout.trim(),
        finished.stderr.trim()
    );
    Ok(())
}

fn call(
    executable: &Path,
    project: &Path,
    policy: &Path,
    state: &Path,
    action: &[&str],
    success: bool,
    events: &mut Vec<Event>,
) -> Result<Value> {
    let mut command = Command::new(executable);
    command
        .args(action)
        .arg("--project")
        .arg(project)
        .arg("--policy")
        .arg(policy)
        .arg("--state")
        .arg(state);
    let finished = bounded(&mut command, GATE_DEADLINE, project)?;
    let result: Value = serde_json::from_str(finished.stdout.trim()).map_err(|error| {
        anyhow!(
            "unexpected gate result: {} produced no JSON ({error}): {}{}",
            action.join(" "),
            finished.stdout.trim(),
            finished.stderr.trim()
        )
    })?;
    let code = finished.code.unwrap_or(-1);
    ensure!(
        (code == 0) == success,
        "unexpected gate result: {} exited {code} where success={success}: {}{}",
        action.join(" "),
        finished.stdout.trim(),
        finished.stderr.trim()
    );
    events.push(Event {
        command: action.iter().map(|part| (*part).to_owned()).collect(),
        exit_code: code,
        result: result.clone(),
    });
    Ok(result)
}

pub(crate) struct Finished {
    pub(crate) code: Option<i32>,
    pub(crate) stdout: String,
    pub(crate) stderr: String,
}

/// Run one child with a wall deadline, capturing both streams through files so
/// a large output cannot deadlock the pipe. `near` only selects where the
/// temporary capture files live.
pub(crate) fn bounded(command: &mut Command, deadline: Duration, near: &Path) -> Result<Finished> {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let directory = std::env::temp_dir().join(format!(
        "devforge-capture-{}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::SeqCst)
    ));
    fs::create_dir_all(&directory).with_context(|| directory.display().to_string())?;
    let capture = Capture(directory);
    let out = capture.0.join("stdout");
    let err = capture.0.join("stderr");
    let describe = format!("{:?}", command.get_program());
    let status = command
        .stdin(Stdio::null())
        .stdout(Stdio::from(fs::File::create(&out)?))
        .stderr(Stdio::from(fs::File::create(&err)?))
        .spawn()
        .with_context(|| format!("could not start {describe} for {}", near.display()));
    let mut child = status?;
    let started = Instant::now();
    let code = loop {
        match child.try_wait()? {
            Some(status) => break status.code(),
            None => {
                if started.elapsed() > deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    bail!("{describe} exceeded {} seconds", deadline.as_secs());
                }
                std::thread::sleep(Duration::from_millis(10));
            }
        }
    };
    Ok(Finished {
        code,
        stdout: String::from_utf8_lossy(&fs::read(&out)?).into_owned(),
        stderr: String::from_utf8_lossy(&fs::read(&err)?).into_owned(),
    })
}

struct Capture(PathBuf);

impl Drop for Capture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn copy_file(source: &Path, destination: &Path) -> Result<()> {
    fs::copy(source, destination)
        .with_context(|| format!("copying {} to {}", source.display(), destination.display()))?;
    Ok(())
}

/// Recursive copy preserving file modes, as `shutil.copytree` did. Symlinks are
/// refused rather than followed.
fn copy_tree(source: &Path, destination: &Path) -> Result<()> {
    fs::create_dir_all(destination).with_context(|| destination.display().to_string())?;
    let mut entries: Vec<PathBuf> = fs::read_dir(source)
        .with_context(|| source.display().to_string())?
        .collect::<std::io::Result<Vec<_>>>()?
        .into_iter()
        .map(|entry| entry.path())
        .collect();
    entries.sort();
    for path in entries {
        let target = destination.join(path.file_name().context("fixture entry name")?);
        let metadata = fs::symlink_metadata(&path)?;
        ensure!(
            !metadata.file_type().is_symlink(),
            "symlink in fixture: {}",
            path.display()
        );
        if metadata.is_dir() {
            copy_tree(&path, &target)?;
        } else {
            fs::copy(&path, &target).with_context(|| format!("copying {}", path.display()))?;
            fs::set_permissions(&target, metadata.permissions())?;
        }
    }
    Ok(())
}

fn existing_directory(path: &Path, flag: &str) -> Result<PathBuf> {
    let resolved = resolve(path)?;
    ensure!(
        resolved.is_dir(),
        "{flag} must be an existing directory: {}",
        resolved.display()
    );
    Ok(resolved)
}

/// Absolute path with existing components resolved, without requiring the tail
/// to exist, and without creating anything.
pub(crate) fn resolve(path: &Path) -> Result<PathBuf> {
    let absolute = std::path::absolute(path)?;
    let mut existing = absolute.as_path();
    let mut tail = Vec::new();
    while !existing.exists() {
        let name = existing
            .file_name()
            .context("path has no existing ancestor")?
            .to_os_string();
        tail.push(name);
        existing = existing.parent().context("path has no parent")?;
    }
    let mut resolved = fs::canonicalize(existing)?;
    for name in tail.iter().rev() {
        if name == ".." {
            resolved.pop();
        } else if name != "." {
            resolved.push(name);
        }
    }
    Ok(resolved)
}

pub(crate) fn separate(a: &Path, b: &Path) -> bool {
    !a.starts_with(b) && !b.starts_with(a)
}

/// `datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")`.
fn utc_compact(now: SystemTime) -> String {
    let (date, time, _) = civil(now);
    format!("{date}T{time}")
}

/// Date, time and microseconds of a UTC instant, as Python's `strftime` writes them.
pub(crate) fn civil(now: SystemTime) -> (String, String, u32) {
    let elapsed = now.duration_since(UNIX_EPOCH).unwrap_or_default();
    let seconds = elapsed.as_secs();
    let days = (seconds / 86_400) as i64;
    let shifted = days + 719_468;
    let era = shifted.div_euclid(146_097);
    let day_of_era = shifted - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let shifted_month = (5 * day_of_year + 2) / 153;
    let day = (day_of_year - (153 * shifted_month + 2) / 5 + 1) as u32;
    let month = if shifted_month < 10 {
        shifted_month + 3
    } else {
        shifted_month - 9
    } as u32;
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    (
        format!("{year:04}{month:02}{day:02}"),
        format!(
            "{:02}{:02}{:02}",
            seconds / 3_600 % 24,
            seconds / 60 % 60,
            seconds % 60
        ),
        elapsed.subsec_micros(),
    )
}

/// Components of a relative path, for the source inventory's exclusions.
pub(crate) fn parts(path: &Path) -> Vec<String> {
    path.components()
        .filter_map(|component| match component {
            Component::Normal(name) => Some(name.to_string_lossy().into_owned()),
            _ => None,
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::{civil, parts, resolve, separate};
    use std::path::Path;
    use std::time::{Duration, UNIX_EPOCH};

    #[test]
    fn utc_fields_match_python_strftime() {
        assert_eq!(civil(UNIX_EPOCH), ("19700101".into(), "000000".into(), 0));
        let stamp = UNIX_EPOCH + Duration::new(1_788_000_000, 5_000);
        assert_eq!(civil(stamp), ("20260829".into(), "104000".into(), 5));
    }

    #[test]
    fn containment_and_components_follow_the_legacy_rules() {
        assert!(!separate(Path::new("/a/b"), Path::new("/a")));
        assert!(!separate(Path::new("/a"), Path::new("/a/b")));
        assert!(separate(Path::new("/a/b"), Path::new("/a/c")));
        assert_eq!(
            parts(Path::new("docs/validation/x.json")),
            ["docs", "validation", "x.json"]
        );
        assert!(parts(Path::new("")).is_empty());
    }

    #[test]
    fn resolution_keeps_a_nonexistent_tail_without_creating_it() {
        let root = std::env::temp_dir();
        let target = root.join("devforge-resolve-probe/deeper");
        let resolved = resolve(&target).unwrap();
        assert!(resolved.is_absolute());
        assert!(resolved.ends_with("devforge-resolve-probe/deeper"));
        assert!(!target.exists());
    }
}
