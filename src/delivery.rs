//! CLI boundary for the embedded, external mechanical delivery controller.
use anyhow::{Context, Result, bail, ensure};
use clap::Subcommand;
use rustix::fd::OwnedFd;
use rustix::process::{Pid, PidfdFlags, Signal, kill_process, pidfd_open, pidfd_send_signal};
use serde::Deserialize;
use serde::de::{self, MapAccess, SeqAccess, Visitor};
use serde_json::{Value, json};
use std::ffi::OsString;
use std::fmt;
use std::fs::{self, DirBuilder, File, Metadata, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, mpsc};
use std::thread;
use std::time::{Duration, Instant};

const JSON_LIMIT: usize = 1024 * 1024;
const HOOK_LIMIT: usize = 64 * 1024;
const HOOK_TIMEOUT: Duration = Duration::from_secs(5);
// Linux O_NOFOLLOW and O_NONBLOCK prevent final-entry symlink/FIFO substitution.
const SAFE_READ_FLAGS: i32 = 0x20000 | 0x800;

#[cfg(test)]
mod tests {
    use super::parse_json;

    #[test]
    fn external_result_duration_retains_its_ieee_754_value() {
        // Actual protected Python result from the direct-supervisor cancellation
        // probe. Without correctly rounded parsing it loses one binary ULP.
        let raw = br#"{"duration_seconds":0.10033450700029789}"#;
        let value = parse_json(raw, "external result duration").unwrap();
        assert_eq!(
            value["duration_seconds"].as_f64().unwrap().to_bits(),
            0x3fb9_af85_b23a_0000
        );
        assert_eq!(serde_json::to_vec(&value).unwrap(), raw);
    }
}

struct CancellationSignal {
    received: Arc<AtomicBool>,
    signal: Signal,
    forwarded: bool,
}

struct CancellationSignals {
    registrations: Vec<signal_hook::SigId>,
    signals: Vec<CancellationSignal>,
}

impl CancellationSignals {
    fn install() -> Result<Self> {
        let mut scope = Self {
            registrations: Vec::new(),
            signals: Vec::new(),
        };
        for (number, signal) in [
            (signal_hook::consts::SIGTERM, Signal::TERM),
            (signal_hook::consts::SIGHUP, Signal::HUP),
            (signal_hook::consts::SIGINT, Signal::INT),
        ] {
            let received = Arc::new(AtomicBool::new(false));
            scope.registrations.push(
                signal_hook::flag::register(number, Arc::clone(&received))
                    .context("cannot install delivery cancellation latch")?,
            );
            scope.signals.push(CancellationSignal {
                received,
                signal,
                forwarded: false,
            });
        }
        Ok(scope)
    }

    fn forward(&mut self, process: &OwnedFd) -> Result<()> {
        for signal in &mut self.signals {
            if signal.received.load(Ordering::SeqCst) && !signal.forwarded {
                match pidfd_send_signal(process, signal.signal) {
                    Ok(()) | Err(rustix::io::Errno::SRCH) => signal.forwarded = true,
                    Err(error) => {
                        return Err(error).context("cannot cancel owned delivery supervisor");
                    }
                }
            }
        }
        Ok(())
    }
}

impl Drop for CancellationSignals {
    fn drop(&mut self) {
        // signal-hook unregisters our actions, not the previous OS handlers.
        // This scope therefore lasts through result readback and ends only as
        // this delivery CLI action returns to final output/exit.
        for registration in self.registrations.drain(..) {
            signal_hook::low_level::unregister(registration);
        }
    }
}

fn terminate_supervisor(child: &mut Child, process: Option<&OwnedFd>) -> Result<()> {
    // The numeric PID is used only if descriptor acquisition failed, before
    // any wait/try_wait. This exclusively owned, unreaped Child reserves it.
    let signaled = match process {
        Some(process) => pidfd_send_signal(process, Signal::TERM),
        None => kill_process(Pid::from_child(child), Signal::TERM),
    };
    let reaped = child.wait();
    match signaled {
        Ok(()) | Err(rustix::io::Errno::SRCH) => {}
        Err(error) => return Err(error).context("cannot terminate owned delivery supervisor"),
    }
    reaped.context("cannot reap owned delivery supervisor")?;
    Ok(())
}

fn wait_supervisor(
    command: &mut Command,
    cancellation: &mut CancellationSignals,
) -> Result<ExitStatus> {
    ensure!(
        !cancellation
            .signals
            .iter()
            .any(|signal| signal.received.load(Ordering::SeqCst)),
        "COULD_NOT_RUN: owner cancelled before supervisor admission"
    );
    let mut child = command
        .spawn()
        .context("cannot launch external process supervisor")?;
    let process = match pidfd_open(Pid::from_child(&child), PidfdFlags::empty()) {
        Ok(process) => process,
        Err(error) => {
            terminate_supervisor(&mut child, None)?;
            return Err(error).context("cannot bind owned delivery supervisor process descriptor");
        }
    };
    loop {
        // try_wait reaps on Some. Never signal again after observing that state.
        match child.try_wait() {
            Ok(Some(status)) => return Ok(status),
            Ok(None) => {}
            Err(error) => {
                terminate_supervisor(&mut child, Some(&process))?;
                return Err(error).context("cannot observe owned delivery supervisor");
            }
        }
        if let Err(error) = cancellation.forward(&process) {
            terminate_supervisor(&mut child, Some(&process))?;
            return Err(error);
        }
        thread::sleep(Duration::from_millis(20));
    }
}

const SOURCES: &[(&str, &str)] = &[
    ("__init__.py", ""),
    (
        "delivery_core.py",
        include_str!("../runtime/delivery/delivery_core.py"),
    ),
    (
        "phase_state.py",
        include_str!("../runtime/delivery/phase_state.py"),
    ),
    (
        "utility_evidence.py",
        include_str!("../runtime/delivery/utility_evidence.py"),
    ),
    (
        "utility_state.py",
        include_str!("../runtime/delivery/utility_state.py"),
    ),
    (
        "native_schedule.py",
        include_str!("../runtime/delivery/native_schedule.py"),
    ),
    (
        "utility_schedule.py",
        include_str!("../runtime/delivery/utility_schedule.py"),
    ),
    (
        "workflow_runtime.py",
        include_str!("../runtime/delivery/workflow_runtime.py"),
    ),
    (
        "hook_protocol.py",
        include_str!("../runtime/delivery/hook_protocol.py"),
    ),
    (
        "controller.py",
        include_str!("../runtime/delivery/controller.py"),
    ),
    (
        "supervisor.py",
        include_str!("../runtime/delivery/supervisor.py"),
    ),
    (
        "native_terminal.py",
        include_str!("../runtime/delivery/native_terminal.py"),
    ),
];

#[derive(Subcommand)]
pub enum Action {
    /// Report this delivery interface without reading project or runtime state.
    Capabilities,
    /// Initialize a new external state root from a selected session contract.
    Init {
        #[arg(long)]
        contract: PathBuf,
    },
    Status,
    /// Reserve a fully bound validator native attempt; does not launch a client.
    NativeAdmission {
        #[arg(long)]
        attempt: String,
    },
    /// Bind a frozen native dependency schedule; does not launch a client.
    NativeScheduleBind {
        #[arg(long)]
        schedule: PathBuf,
    },
    /// Journal the next unlaunched reservation or budget/dependency decision.
    NativeScheduleReserve,
    /// Cancel an unlaunched reservation; never import a native execution grade.
    NativeScheduleCancel {
        #[arg(long)]
        attempt: String,
        #[arg(long)]
        reason: String,
    },
    Advance,
    Resume,
    Complete,
    /// Check current persisted outputs against a delivery contract.
    Check {
        #[arg(long)]
        contract: PathBuf,
    },
    /// Verify an existing external receipt against current delivery bytes.
    Verify {
        #[arg(long)]
        contract: PathBuf,
        #[arg(long)]
        receipt: PathBuf,
    },
    /// Run an argv under the external process supervisor with inherited terminal IO.
    Run {
        #[arg(long)]
        contract: PathBuf,
        /// Existing, externally allocated private HOME directory.
        #[arg(long)]
        profile: PathBuf,
        /// Intact external client/package directory containing the executable.
        #[arg(long)]
        client_root: Option<PathBuf>,
        /// Admit only a deterministic fixture process with network isolation.
        #[arg(long)]
        synthetic: bool,
        /// Select external mechanical task commit or successful process exit.
        #[arg(long, default_value = "process", value_parser = ["process", "managed-session"])]
        completion_mode: String,
        /// Preserve inherited IO or relay through an owned controlling terminal.
        #[arg(long, default_value = "inherited", value_parser = ["inherited", "interactive-tty"])]
        io_mode: String,
        #[arg(long, value_parser = clap::value_parser!(u64).range(1..=86400))]
        timeout: u64,
        #[arg(last = true, required = true)]
        command: Vec<OsString>,
    },
    /// Forward one native hook event to an explicitly configured local broker.
    Hook {
        #[arg(long, value_parser = ["codex", "claude"])]
        provider: String,
    },
}

// Reject duplicate keys before interpreting a path or forwarding a hook event.
struct StrictJson(Value);
impl<'de> Deserialize<'de> for StrictJson {
    fn deserialize<D: de::Deserializer<'de>>(
        deserializer: D,
    ) -> std::result::Result<Self, D::Error> {
        struct JsonVisitor;
        impl<'de> Visitor<'de> for JsonVisitor {
            type Value = Value;
            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("JSON without duplicate object keys")
            }
            fn visit_bool<E: de::Error>(self, value: bool) -> std::result::Result<Value, E> {
                Ok(value.into())
            }
            fn visit_i64<E: de::Error>(self, value: i64) -> std::result::Result<Value, E> {
                Ok(value.into())
            }
            fn visit_u64<E: de::Error>(self, value: u64) -> std::result::Result<Value, E> {
                Ok(value.into())
            }
            fn visit_f64<E: de::Error>(self, value: f64) -> std::result::Result<Value, E> {
                serde_json::Number::from_f64(value)
                    .map(Value::Number)
                    .ok_or_else(|| E::custom("nonfinite JSON number"))
            }
            fn visit_str<E: de::Error>(self, value: &str) -> std::result::Result<Value, E> {
                Ok(value.into())
            }
            fn visit_string<E: de::Error>(self, value: String) -> std::result::Result<Value, E> {
                Ok(value.into())
            }
            fn visit_unit<E: de::Error>(self) -> std::result::Result<Value, E> {
                Ok(Value::Null)
            }
            fn visit_seq<A: SeqAccess<'de>>(
                self,
                mut seq: A,
            ) -> std::result::Result<Value, A::Error> {
                let mut array = Vec::new();
                while let Some(StrictJson(value)) = seq.next_element()? {
                    array.push(value);
                }
                Ok(Value::Array(array))
            }
            fn visit_map<A: MapAccess<'de>>(
                self,
                mut map: A,
            ) -> std::result::Result<Value, A::Error> {
                let mut object = serde_json::Map::new();
                while let Some((key, StrictJson(value))) = map.next_entry::<String, StrictJson>()? {
                    if object.insert(key.clone(), value).is_some() {
                        return Err(de::Error::custom(format!("duplicate JSON key: {key}")));
                    }
                }
                Ok(Value::Object(object))
            }
        }
        deserializer.deserialize_any(JsonVisitor).map(StrictJson)
    }
}

fn parse_json(raw: &[u8], label: &str) -> Result<Value> {
    let StrictJson(value) =
        serde_json::from_slice(raw).with_context(|| format!("FAIL: invalid {label}"))?;
    ensure!(value.is_object(), "FAIL: {label} must be a JSON object");
    Ok(value)
}

fn checked_path(path: &Path) -> Result<PathBuf> {
    let path = crate::resolved(path)?;
    let mut prefix = PathBuf::new();
    for part in path.components() {
        prefix.push(part.as_os_str());
        match fs::symlink_metadata(&prefix) {
            Ok(meta) => {
                ensure!(
                    !meta.file_type().is_symlink(),
                    "FAIL: symlink path: {}",
                    prefix.display()
                );
                if prefix != path {
                    ensure!(
                        meta.is_dir(),
                        "FAIL: parent is not a directory: {}",
                        prefix.display()
                    );
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(error).with_context(|| format!("cannot inspect {}", prefix.display()));
            }
        }
    }
    Ok(path)
}

fn unchanged(a: &Metadata, b: &Metadata) -> bool {
    a.dev() == b.dev()
        && a.ino() == b.ino()
        && a.len() == b.len()
        && a.mtime() == b.mtime()
        && a.mtime_nsec() == b.mtime_nsec()
        && a.ctime() == b.ctime()
        && a.ctime_nsec() == b.ctime_nsec()
        && a.mode() == b.mode()
        && a.nlink() == b.nlink()
}

fn read_file(path: &Path, limit: usize) -> Result<Vec<u8>> {
    let path = checked_path(path)?;
    let before = fs::symlink_metadata(&path)?;
    ensure!(
        before.is_file() && before.nlink() == 1,
        "FAIL: expected regular single-link file: {}",
        path.display()
    );
    ensure!(
        before.len() <= limit as u64,
        "FAIL: oversized file: {}",
        path.display()
    );
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(SAFE_READ_FLAGS)
        .open(&path)?;
    ensure!(
        unchanged(&before, &file.metadata()?),
        "FAIL: file changed before read: {}",
        path.display()
    );
    let mut bytes = Vec::new();
    (&mut file).take(limit as u64 + 1).read_to_end(&mut bytes)?;
    ensure!(
        bytes.len() <= limit
            && unchanged(&before, &file.metadata()?)
            && unchanged(&before, &fs::symlink_metadata(checked_path(&path)?)?),
        "FAIL: file changed during read: {}",
        path.display()
    );
    Ok(bytes)
}

fn read_json(path: &Path) -> Result<Value> {
    parse_json(&read_file(path, JSON_LIMIT)?, &path.display().to_string())
}

fn field_path(value: &Value, name: &str) -> Result<PathBuf> {
    let raw = value
        .get(name)
        .and_then(Value::as_str)
        .with_context(|| format!("FAIL: missing path field {name}"))?;
    let original = Path::new(raw);
    ensure!(original.is_absolute(), "FAIL: {name} must be absolute");
    let path = checked_path(original)?;
    ensure!(path == original, "FAIL: {name} must be canonical");
    Ok(path)
}

fn project_from_session(path: &Path) -> Result<PathBuf> {
    let session = read_json(path)?;
    let delivery = field_path(&session, "delivery_contract")?;
    let project = field_path(&read_json(&delivery)?, "project_root")?;
    ensure!(
        crate::separate(path, &project) && crate::separate(&delivery, &project),
        "FAIL: contracts must be outside the writable project"
    );
    Ok(project)
}

fn project_for(action: &Action, state: Option<&Path>) -> Result<PathBuf> {
    match action {
        Action::Init { contract } | Action::Run { contract, .. } => {
            project_from_session(&checked_path(contract)?)
        }
        Action::Check { contract } | Action::Verify { contract, .. } => {
            let contract = checked_path(contract)?;
            let project = field_path(&read_json(&contract)?, "project_root")?;
            ensure!(
                crate::separate(&contract, &project),
                "FAIL: contract overlaps writable project"
            );
            Ok(project)
        }
        Action::Status
        | Action::Advance
        | Action::Resume
        | Action::Complete
        | Action::NativeAdmission { .. }
        | Action::NativeScheduleBind { .. }
        | Action::NativeScheduleReserve
        | Action::NativeScheduleCancel { .. } => {
            let root = state.context("FAIL: --state is required")?;
            let manifest = read_json(&root.join("MANIFEST.json"))?;
            let project = field_path(&manifest, "project_root")?;
            let session = field_path(&manifest, "session_path")?;
            ensure!(
                project_from_session(&session)? == project,
                "FAIL: protected state and selected session disagree on writable project"
            );
            Ok(project)
        }
        Action::Capabilities | Action::Hook { .. } => unreachable!(),
    }
}

fn private_directory(path: &Path) -> Result<()> {
    checked_path(path)?;
    DirBuilder::new().mode(0o700).create(path)?;
    ensure!(
        fs::symlink_metadata(path)?.is_dir(),
        "FAIL: directory creation changed"
    );
    Ok(())
}

fn verify_directory(path: &Path, names: &[&str]) -> Result<()> {
    checked_path(path)?;
    let meta = fs::symlink_metadata(path)?;
    ensure!(
        meta.is_dir() && meta.mode() & 0o777 == 0o555,
        "FAIL: code cache directory must have mode 0555: {}",
        path.display()
    );
    let mut entries = fs::read_dir(path)?
        .map(|entry| entry.map(|e| e.file_name()))
        .collect::<std::io::Result<Vec<_>>>()?;
    entries.sort();
    let mut expected: Vec<OsString> = names.iter().map(OsString::from).collect();
    expected.sort();
    ensure!(
        entries == expected,
        "FAIL: unexpected code cache entries: {}",
        path.display()
    );
    Ok(())
}

fn cache(
    parent: &Path,
    project: &Path,
    state: Option<&Path>,
    profile: Option<&Path>,
) -> Result<PathBuf> {
    let mut identity = Vec::new();
    for (name, source) in SOURCES {
        identity.extend_from_slice(name.as_bytes());
        identity.push(0);
        identity.extend_from_slice(source.as_bytes());
        identity.push(0);
    }
    let root = checked_path(&parent.join(format!(
        ".devforge-delivery-code-{}",
        crate::hash(&identity)
    )))?;
    for protected in [Some(project), state, profile].into_iter().flatten() {
        ensure!(
            crate::separate(&root, protected),
            "FAIL: runtime cache overlaps a mutable or protected root"
        );
    }
    let package = root.join("delivery");
    match fs::symlink_metadata(&root) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            private_directory(&root)?;
            private_directory(&package)?;
            for (name, source) in SOURCES {
                let path = package.join(name);
                let mut file = OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .mode(0o444)
                    .open(&path)?;
                file.write_all(source.as_bytes())?;
                file.set_permissions(fs::Permissions::from_mode(0o444))?;
                file.sync_all()?;
            }
            fs::set_permissions(&package, fs::Permissions::from_mode(0o555))?;
            File::open(&package)?.sync_all()?;
            fs::set_permissions(&root, fs::Permissions::from_mode(0o555))?;
            File::open(&root)?.sync_all()?;
            File::open(parent)?.sync_all()?;
        }
        Ok(_) => {}
        Err(error) => return Err(error).context("cannot inspect runtime cache"),
    }
    verify_directory(&root, &["delivery"])?;
    verify_directory(
        &package,
        &SOURCES.iter().map(|(name, _)| *name).collect::<Vec<_>>(),
    )?;
    for (name, source) in SOURCES {
        let path = package.join(name);
        ensure!(
            fs::symlink_metadata(&path)?.mode() & 0o777 == 0o444,
            "FAIL: runtime cache file must have mode 0444: {}",
            path.display()
        );
        ensure!(
            read_file(&path, source.len())? == source.as_bytes(),
            "FAIL: embedded runtime cache bytes differ: {}",
            path.display()
        );
    }
    Ok(package)
}

fn python(package: &Path, script: &str) -> Command {
    let mut command = Command::new("/usr/bin/python3");
    command
        .args(["-I", "-B"])
        .arg(package.join(script))
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME");
    command
}

struct Outcome {
    value: Value,
    failed: bool,
}

fn outcome(raw: &[u8], status: ExitStatus) -> Result<Outcome> {
    let value = parse_json(raw, "runtime result")?;
    let label = value
        .get("status")
        .or_else(|| value.get("result"))
        .and_then(Value::as_str)
        .context("runtime result has no status or result field")?;
    let failed = matches!(label, "FAIL" | "COULD_NOT_RUN" | "BLOCKED" | "STALE");
    ensure!(
        status.success() || failed,
        "runtime exited {status} while reporting {label}"
    );
    Ok(Outcome { value, failed })
}

fn capture(mut command: Command) -> Result<Outcome> {
    let mut child = command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .context("cannot launch isolated Python controller")?;
    let stdout = child
        .stdout
        .take()
        .context("controller stdout unavailable")?;
    let stderr = child
        .stderr
        .take()
        .context("controller stderr unavailable")?;
    fn collect(stream: impl Read) -> std::io::Result<Vec<u8>> {
        let mut bytes = Vec::new();
        stream.take(JSON_LIMIT as u64 + 1).read_to_end(&mut bytes)?;
        Ok(bytes)
    }
    let out = thread::spawn(move || collect(stdout));
    let err = thread::spawn(move || collect(stderr));
    let status = child.wait()?;
    let stdout = out
        .join()
        .map_err(|_| anyhow::anyhow!("controller stdout reader failed"))??;
    let stderr = err
        .join()
        .map_err(|_| anyhow::anyhow!("controller stderr reader failed"))??;
    ensure!(
        stdout.len() <= JSON_LIMIT && stderr.len() <= JSON_LIMIT,
        "controller output exceeds bounded result size"
    );
    if !stderr.is_empty() {
        eprint!("{}", String::from_utf8_lossy(&stderr));
    }
    outcome(&stdout, status)
}

fn result_path(parent: &Path, project: &Path, state: &Path, profile: &Path) -> Result<PathBuf> {
    for _ in 0..100 {
        let path = checked_path(&parent.join(format!(
            ".devforge-delivery-result-{}-{}",
            std::process::id(),
            crate::COUNTER.fetch_add(1, Ordering::Relaxed)
        )))?;
        for protected in [project, state, profile] {
            ensure!(
                crate::separate(&path, protected),
                "FAIL: supervisor result overlaps a mutable or protected root"
            );
        }
        match fs::symlink_metadata(&path) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                private_directory(&path)?;
                return Ok(path.join("result.json"));
            }
            Ok(_) => {}
            Err(error) => {
                return Err(error).context("cannot inspect supervisor result destination");
            }
        }
    }
    bail!("cannot allocate exclusive external supervisor result directory")
}

fn operate(action: &Action, state: Option<&Path>) -> Result<Outcome> {
    let needs_state = !matches!(action, Action::Check { .. } | Action::Verify { .. });
    let state = if needs_state {
        Some(checked_path(state.context("FAIL: --state is required")?)?)
    } else {
        None
    };
    let project = project_for(action, state.as_deref())?;
    ensure!(
        project.is_dir(),
        "FAIL: writable project directory is missing"
    );
    let parent = if let Some(state) = &state {
        ensure!(
            crate::separate(&project, state),
            "FAIL: state overlaps writable project"
        );
        state
            .parent()
            .context("FAIL: state root has no parent")?
            .to_path_buf()
    } else {
        PathBuf::from("/tmp")
    };
    let profile = match action {
        Action::Run { profile, .. } => {
            let path = checked_path(profile)?;
            ensure!(path.is_dir(), "FAIL: private profile must already exist");
            Some(path)
        }
        _ => None,
    };
    let package = cache(&parent, &project, state.as_deref(), profile.as_deref())?;
    if let Action::Run {
        contract,
        client_root,
        synthetic,
        completion_mode,
        io_mode,
        timeout,
        command,
        ..
    } = action
    {
        let state = state.as_deref().context("FAIL: --state is required")?;
        let profile = profile.as_deref().context("FAIL: --profile is required")?;
        let result = result_path(&parent, &project, state, profile)?;
        let mut supervisor = python(&package, "supervisor.py");
        supervisor
            .env(
                "DEVFORGE_DELIVERY_EXECUTABLE",
                checked_path(&std::env::current_exe()?)?,
            )
            .arg("run")
            .arg("--contract")
            .arg(checked_path(contract)?)
            .arg("--state")
            .arg(state)
            .arg("--profile")
            .arg(profile)
            .arg("--timeout")
            .arg(timeout.to_string())
            .arg("--completion-mode")
            .arg(completion_mode)
            .arg("--io-mode")
            .arg(io_mode)
            .arg("--result")
            .arg(&result);
        if let Some(client_root) = client_root {
            supervisor
                .arg("--client-root")
                .arg(checked_path(client_root)?);
        }
        if *synthetic {
            supervisor.arg("--synthetic");
        }
        supervisor
            .arg("--")
            .args(command)
            .stdin(Stdio::inherit())
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit());
        let mut cancellation = CancellationSignals::install()?;
        let status = wait_supervisor(&mut supervisor, &mut cancellation)?;
        let raw = read_file(&result, JSON_LIMIT)
            .context("supervisor did not publish a valid external result")?;
        let mut output = outcome(&raw, status)?;
        output.value["result_readback"] = json!({
            "path": result,
            "sha256": crate::hash(&raw),
            "status": "VERIFIED"
        });
        return Ok(output);
    }
    let mut controller = python(&package, "controller.py");
    let name = match action {
        Action::Init { .. } => "init",
        Action::Status => "status",
        Action::NativeAdmission { .. } => "native-admission",
        Action::NativeScheduleBind { .. } => "native-schedule-bind",
        Action::NativeScheduleReserve => "native-schedule-reserve",
        Action::NativeScheduleCancel { .. } => "native-schedule-cancel",
        Action::Advance => "advance",
        Action::Resume => "resume",
        Action::Complete => "complete",
        Action::Check { .. } => "check",
        Action::Verify { .. } => "verify",
        _ => unreachable!(),
    };
    controller.arg(name);
    if let Some(state) = &state {
        controller.arg("--state").arg(state);
    }
    match action {
        Action::Init { contract }
        | Action::Check { contract }
        | Action::Verify { contract, .. } => {
            controller.arg("--contract").arg(checked_path(contract)?);
        }
        _ => {}
    }
    if let Action::Verify { receipt, .. } = action {
        controller.arg("--receipt").arg(checked_path(receipt)?);
    }
    if let Action::NativeAdmission { attempt } = action {
        controller.arg("--attempt").arg(attempt);
    }
    if let Action::NativeScheduleBind { schedule } = action {
        controller.arg("--schedule").arg(checked_path(schedule)?);
    }
    if let Action::NativeScheduleCancel { attempt, reason } = action {
        controller
            .arg("--attempt")
            .arg(attempt)
            .arg("--reason")
            .arg(reason);
    }
    capture(controller)
}

fn hook(provider: &str) -> Result<Value> {
    let Some(socket) = std::env::var_os("DEVFORGE_DELIVERY_SOCKET") else {
        return Ok(json!({}));
    };
    ensure!(
        !socket.is_empty(),
        "FAIL: DEVFORGE_DELIVERY_SOCKET is empty"
    );
    let digest = std::env::var("DEVFORGE_DELIVERY_CONTRACT_SHA256")
        .context("DEVFORGE_DELIVERY_CONTRACT_SHA256 is required with delivery socket")?;
    ensure!(
        digest.len() == 64
            && digest
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)),
        "FAIL: delivery contract SHA-256 must be 64 lowercase hexadecimal characters"
    );
    let mut event = Vec::new();
    std::io::stdin()
        .take(HOOK_LIMIT as u64 + 1)
        .read_to_end(&mut event)?;
    ensure!(event.len() <= HOOK_LIMIT, "FAIL: hook stdin exceeds 64 KiB");
    let event = parse_json(&event, "hook event")?;
    let mut request =
        serde_json::to_vec(&json!({"provider":provider,"contract_sha256":digest,"event":event}))?;
    request.push(b'\n');
    let (sender, receiver) = mpsc::sync_channel(1);
    thread::spawn(move || {
        let _ = sender.send(UnixStream::connect(PathBuf::from(socket)));
    });
    let mut stream = receiver
        .recv_timeout(HOOK_TIMEOUT)
        .context("delivery broker connection timed out")??;
    stream.set_read_timeout(Some(HOOK_TIMEOUT))?;
    let deadline = Instant::now() + HOOK_TIMEOUT;
    let mut sent = 0;
    while sent < request.len() {
        let remaining = deadline
            .checked_duration_since(Instant::now())
            .context("delivery broker request timed out")?;
        stream.set_write_timeout(Some(remaining))?;
        let count = stream
            .write(&request[sent..])
            .context("cannot send delivery hook event")?;
        ensure!(
            count > 0,
            "delivery broker closed before the complete request"
        );
        sent += count;
    }
    let deadline = Instant::now() + HOOK_TIMEOUT;
    let mut response = Vec::new();
    loop {
        let remaining = deadline
            .checked_duration_since(Instant::now())
            .context("delivery broker response timed out")?;
        stream.set_read_timeout(Some(remaining))?;
        let mut byte = [0u8];
        ensure!(
            stream.read(&mut byte)? == 1,
            "delivery broker closed without a complete response line"
        );
        if byte[0] == b'\n' {
            break;
        }
        response.push(byte[0]);
        ensure!(
            response.len() <= HOOK_LIMIT,
            "delivery broker response exceeds 64 KiB"
        );
    }
    parse_json(&response, "delivery broker response")
}

pub fn main(action: &Action, state: Option<&Path>) {
    if matches!(action, Action::Capabilities) {
        println!(
            "{}",
            json!({
                "schema_version": "devforge.delivery-capabilities/v1",
                "protocol": "devforge.delivery-runtime/v1",
                "supported_providers": ["codex", "claude"],
                "completion_modes": ["process", "managed-session"],
                "io_modes": ["inherited", "interactive-tty"],
                "hook_events": ["SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"],
                "native_admission": "NOT_VALIDATED",
                "utility_workflows": ["skill-builder", "skill-validator"],
                "utility_session_schema": "devforge.utility-session/v1",
                "utility_native_schedule_schema": "devforge.utility-native-schedule/v1",
                "native_execution_enabled": false,
                "mechanical_scope": "phase evidence and persisted artifact verification; no semantic acceptance"
            })
        );
        return;
    }
    if let Action::Hook { provider } = action {
        match hook(provider) {
            Ok(value) => println!("{value}"),
            Err(error) => {
                eprintln!("DevForge delivery hook failed: {error:#}");
                std::process::exit(2);
            }
        }
        return;
    }
    match operate(action, state) {
        Ok(result) => {
            println!(
                "{}",
                serde_json::to_string_pretty(&result.value).expect("JSON output")
            );
            if result.failed {
                std::process::exit(2);
            }
        }
        Err(error) => {
            let reason = format!("{error:#}");
            let status = if reason.contains("FAIL:") {
                "FAIL"
            } else {
                "COULD_NOT_RUN"
            };
            println!(
                "{}",
                json!({"status":status,"issues":[reason],
                "scope":"External mechanical delivery runtime; native behavior and human acceptance are not evaluated"})
            );
            std::process::exit(2);
        }
    }
}
