use anyhow::{Context, Result, bail, ensure};
use clap::{Parser, Subcommand};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::ffi::OsString;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

mod delivery;
mod install;
mod plugin;
mod validate_mvp;

const RUNNER: &str = include_str!("../runners/unittest_runner.py");
static COUNTER: AtomicU64 = AtomicU64::new(0);

#[derive(Parser)]
#[command(
    version,
    about = "External project-expertise and development gates (Linux POC)"
)]
struct Cli {
    #[arg(long, global = true)]
    project: Option<PathBuf>,
    #[arg(long, global = true)]
    policy: Option<PathBuf>,
    #[arg(long, global = true)]
    state: Option<PathBuf>,
    #[arg(long, global = true)]
    expert: Option<String>,
    #[command(subcommand)]
    command: Action,
}
#[derive(Subcommand)]
enum Action {
    /// Manage external mechanical delivery state and protected worker sessions.
    Delivery {
        #[command(subcommand)]
        action: delivery::Action,
    },
    /// Install framework components from owner-selected evidence; manual-only path.
    Install {
        #[command(subcommand)]
        action: install::Action,
    },
    /// Structural document validation; reports only, never behavioral acceptance.
    Validate {
        #[command(subcommand)]
        action: validate_mvp::Action,
    },
    /// Prepare expertise context, inspect freshness, or bind an AI-authored skill.
    Expert {
        #[command(subcommand)]
        action: ExpertAction,
    },
    /// Check structural policy and provenance; does not certify semantic behavior.
    Check,
    /// Record baseline in an external, human-controlled state directory.
    Init,
    /// Observe a test-only assertion failure.
    Red,
    /// Observe passing tests with exactly the tests evaluated at RED.
    Green,
    /// Preserve the exact GREEN candidate as an accepted local snapshot.
    Accept,
    /// Verify accepted archive and current candidate integrity.
    Verify,
    /// Inspect durable phase state.
    Status,
    /// Run with only the project writable; no unconfined fallback.
    Isolate {
        /// Private per-project client state mounted over the normal client home.
        #[arg(long, value_parser = ["codex", "claude"])]
        runtime: Option<String>,
        #[arg(last = true, required = true)]
        command: Vec<OsString>,
    },
}
#[derive(Subcommand)]
enum ExpertAction {
    Prepare,
    Bind,
    Status,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Policy {
    schema: u32,
    project_id: String,
    goal: String,
    story_id: String,
    upstream: Vec<String>,
    dependencies: BTreeMap<String, String>,
    dependency_file: String,
    source_roots: Vec<String>,
    test_root: String,
    expert_dirs: Vec<String>,
    forbidden_tokens: Vec<String>,
    #[serde(default)]
    tooling_files: Manifest,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Fingerprint {
    sha256: String,
    mode: u32,
}
type Manifest = BTreeMap<String, Fingerprint>;
#[derive(Clone)]
struct Entry {
    bytes: Vec<u8>,
    mode: u32,
}
type Tree = BTreeMap<String, Entry>;
#[derive(Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Binding {
    schema: u32,
    project_id: String,
    policy_sha256: String,
    upstream: Manifest,
    skill_files: Manifest,
    evaluation: String,
}
#[derive(Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum Phase {
    Initialized,
    Red,
    Green,
    Accepted,
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RunState {
    schema: u32,
    project: PathBuf,
    project_id: String,
    policy_sha256: String,
    runner_sha256: String,
    phase: Phase,
    baseline: Manifest,
    red_tests: Option<Manifest>,
    red_result: Option<TestResult>,
    green: Option<Manifest>,
    green_result: Option<TestResult>,
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct TestResult {
    tests: usize,
    failures: usize,
    errors: usize,
    skipped: usize,
    unexpected_successes: usize,
    expected_failures: usize,
    test_ids: Vec<String>,
    output: String,
}
struct Gate {
    project: PathBuf,
    policy: Policy,
    policy_hash: String,
    state: Option<PathBuf>,
}

fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn relative(name: &str) -> Result<()> {
    ensure!(
        !name.is_empty() && !name.contains('\\'),
        "invalid relative path: {name}"
    );
    ensure!(
        Path::new(name)
            .components()
            .all(|c| matches!(c, Component::Normal(_))),
        "invalid relative path: {name}"
    );
    Ok(())
}
fn under(name: &str, root: &str) -> bool {
    name == root || name.strip_prefix(root).is_some_and(|s| s.starts_with('/'))
}
fn no_symlinks(path: &Path) -> Result<()> {
    let mut prefix = PathBuf::new();
    for part in path.components() {
        prefix.push(part.as_os_str());
        if let Ok(meta) = fs::symlink_metadata(&prefix) {
            ensure!(
                !meta.file_type().is_symlink(),
                "symlink not allowed: {}",
                prefix.display()
            );
        }
    }
    Ok(())
}
fn resolved(path: &Path) -> Result<PathBuf> {
    let absolute = std::path::absolute(path)?;
    no_symlinks(&absolute)?;
    let mut normalized = PathBuf::new();
    for part in absolute.components() {
        match part {
            Component::ParentDir => {
                normalized.pop();
            }
            Component::CurDir => {}
            _ => normalized.push(part.as_os_str()),
        }
    }
    Ok(normalized)
}
fn separate(a: &Path, b: &Path) -> bool {
    !a.starts_with(b) && !b.starts_with(a)
}

impl Gate {
    fn load(cli: &Cli) -> Result<Self> {
        let project = resolved(cli.project.as_ref().context("--project is required")?)?;
        ensure!(project.is_dir(), "project directory does not exist");
        let policy_path = resolved(cli.policy.as_ref().context("--policy is required")?)?;
        ensure!(
            separate(&project, &policy_path),
            "policy must be outside candidate"
        );
        let bytes = fs::read(&policy_path)?;
        let policy: Policy = serde_json::from_slice(&bytes).context("invalid policy")?;
        ensure!(policy.schema == 1, "unsupported policy schema");
        ensure!(
            !policy.project_id.is_empty() && !policy.goal.is_empty() && !policy.story_id.is_empty(),
            "missing identity, goal, or story"
        );
        ensure!(
            !policy.source_roots.is_empty()
                && !policy.expert_dirs.is_empty()
                && !policy.upstream.is_empty(),
            "empty policy roots"
        );
        for p in policy
            .upstream
            .iter()
            .chain(policy.source_roots.iter())
            .chain(policy.expert_dirs.iter())
            .chain([&policy.test_root, &policy.dependency_file])
        {
            relative(p)?;
        }
        for (path, pin) in &policy.tooling_files {
            relative(path)?;
            ensure!(
                pin.sha256.len() == 64
                    && pin.sha256.bytes().all(|b| b.is_ascii_hexdigit())
                    && pin.mode <= 0o777,
                "invalid tooling fingerprint: {path}"
            );
            ensure!(
                !under(path, &policy.test_root)
                    && !policy.source_roots.iter().any(|root| under(path, root)),
                "tooling file overlaps editable source/test scope: {path}"
            );
        }
        for root in &policy.source_roots {
            ensure!(
                !under(root, &policy.test_root) && !under(&policy.test_root, root),
                "test/source overlap"
            );
            for p in policy
                .expert_dirs
                .iter()
                .chain(policy.upstream.iter())
                .chain([&policy.dependency_file])
            {
                ensure!(
                    !under(p, root) && !under(root, p),
                    "source root overlaps policy inputs"
                );
            }
        }
        for p in policy
            .expert_dirs
            .iter()
            .chain(policy.upstream.iter())
            .chain([&policy.dependency_file])
        {
            ensure!(
                !under(p, &policy.test_root) && !under(&policy.test_root, p),
                "test root overlaps policy inputs"
            );
        }
        let state = cli.state.as_ref().map(|p| resolved(p)).transpose()?;
        if let Some(s) = &state {
            ensure!(separate(&project, s), "state must be outside candidate");
            ensure!(separate(&policy_path, s), "state and policy overlap");
        }
        Ok(Self {
            project,
            policy,
            policy_hash: hash(&bytes),
            state,
        })
    }
    fn state_dir(&self) -> Result<&Path> {
        self.state.as_deref().context("--state is required")
    }
    fn desired_binding(&self, tree: &Tree, expert: &str) -> Result<Binding> {
        ensure!(
            self.policy.expert_dirs.contains(&expert.to_string()),
            "undeclared expert directory"
        );
        let content = String::from_utf8(
            tree.get(&format!("{expert}/SKILL.md"))
                .context("missing SKILL.md")?
                .bytes
                .clone(),
        )?;
        ensure!(
            content.starts_with("---\n") && content.lines().filter(|l| *l == "---").count() >= 2,
            "invalid frontmatter"
        );
        let frontmatter = content.split("---\n").nth(1).unwrap_or_default();
        ensure!(
            frontmatter.lines().any(|s| s.starts_with("name: "))
                && frontmatter.lines().any(|s| s.starts_with("description: ")),
            "skill needs name and description"
        );
        let upstream = self
            .policy
            .upstream
            .iter()
            .map(|p| {
                Ok((
                    p.clone(),
                    fingerprint(
                        tree.get(p)
                            .with_context(|| format!("missing upstream {p}"))?,
                    ),
                ))
            })
            .collect::<Result<Manifest>>()?;
        let skill_files = manifest(tree)
            .into_iter()
            .filter(|(p, _)| {
                under(p, expert)
                    && !p.ends_with("/provenance.json")
                    && !under(p, &format!("{expert}/history"))
            })
            .collect();
        Ok(Binding {
            schema: 1,
            project_id: self.policy.project_id.clone(),
            policy_sha256: self.policy_hash.clone(),
            upstream,
            skill_files,
            evaluation: "STRUCTURAL_ONLY_BEHAVIOR_NOT_EVALUATED".into(),
        })
    }
    fn check(&self, tree: &Tree) -> Result<()> {
        let entry = tree
            .get(&self.policy.dependency_file)
            .context("missing dependency manifest")?;
        let deps: BTreeMap<String, String> =
            serde_json::from_slice(&entry.bytes).context("invalid dependencies")?;
        ensure!(
            deps == self.policy.dependencies,
            "dependencies differ from approved policy"
        );
        for (p, entry) in tree {
            let code = ["py", "rs", "cs", "csproj", "js", "ts", "tsx", "sh"].contains(
                &Path::new(p)
                    .extension()
                    .and_then(|e| e.to_str())
                    .unwrap_or_default(),
            );
            if code {
                ensure!(
                    self.policy.source_roots.iter().any(|r| under(p, r))
                        || under(p, &self.policy.test_root)
                        || self.policy.tooling_files.contains_key(p),
                    "source outside approved layout: {p}"
                );
            }
            if self.policy.source_roots.iter().any(|r| under(p, r)) {
                let content = String::from_utf8_lossy(&entry.bytes);
                for token in &self.policy.forbidden_tokens {
                    ensure!(
                        !content.contains(token),
                        "prohibited source token {token} in {p}"
                    );
                }
            }
        }
        for (path, expected) in &self.policy.tooling_files {
            let actual = tree
                .get(path)
                .with_context(|| format!("missing tooling file: {path}"))?;
            ensure!(
                fingerprint(actual) == *expected,
                "tooling file changed: {path}"
            );
        }
        for expert in &self.policy.expert_dirs {
            let expected = self.desired_binding(tree, expert)?;
            let binding: Binding = serde_json::from_slice(
                &tree
                    .get(&format!("{expert}/provenance.json"))
                    .context("expert has no binding")?
                    .bytes,
            )?;
            ensure!(
                binding == expected,
                "STALE: expert {expert} or its inputs changed"
            );
        }
        Ok(())
    }
    fn read_state(&self) -> Result<RunState> {
        let s: RunState = serde_json::from_slice(&fs::read(self.state_dir()?.join("state.json"))?)?;
        ensure!(
            s.schema == 1 && s.project == self.project && s.project_id == self.policy.project_id,
            "state identity mismatch"
        );
        ensure!(
            s.policy_sha256 == self.policy_hash && s.runner_sha256 == hash(RUNNER.as_bytes()),
            "STALE: policy or runner changed; start a new run"
        );
        Ok(s)
    }
}
fn fingerprint(e: &Entry) -> Fingerprint {
    Fingerprint {
        sha256: hash(&e.bytes),
        mode: e.mode,
    }
}
fn manifest(tree: &Tree) -> Manifest {
    tree.iter()
        .map(|(p, e)| (p.clone(), fingerprint(e)))
        .collect()
}
fn tree_hash(tree: &Tree) -> Result<String> {
    Ok(hash(&serde_json::to_vec(&manifest(tree))?))
}
fn read_tree(root: &Path) -> Result<Tree> {
    fn visit(root: &Path, dir: &Path, tree: &mut Tree, size: &mut u64) -> Result<()> {
        for item in fs::read_dir(dir)? {
            let path = item?.path();
            let name = path
                .strip_prefix(root)?
                .to_str()
                .context("non-UTF8 path")?
                .to_string();
            let meta = fs::symlink_metadata(&path)?;
            ensure!(
                !meta.file_type().is_symlink(),
                "symlink not allowed: {name}"
            );
            if name == ".git"
                || name == ".devforge-runtime"
                || name.split('/').any(|p| p == "__pycache__")
            {
                continue;
            }
            if meta.is_dir() {
                visit(root, &path, tree, size)?;
            } else {
                ensure!(
                    meta.is_file() && meta.nlink() == 1,
                    "special file/hardlink: {name}"
                );
                ensure!(meta.len() <= 1_048_576, "file exceeds 1 MiB: {name}");
                *size += meta.len();
                ensure!(
                    *size <= 16_777_216 && tree.len() < 1024,
                    "project exceeds POC limit"
                );
                let bytes = fs::read(&path)?;
                let after = fs::symlink_metadata(&path)?;
                ensure!(
                    meta.ino() == after.ino()
                        && meta.len() == after.len()
                        && meta.mtime_nsec() == after.mtime_nsec()
                        && meta.mtime() == after.mtime(),
                    "file changed during snapshot: {name}"
                );
                tree.insert(
                    name,
                    Entry {
                        bytes,
                        mode: meta.mode() & 0o777,
                    },
                );
            }
        }
        Ok(())
    }
    let mut tree = Tree::new();
    visit(root, root, &mut tree, &mut 0)?;
    Ok(tree)
}
fn write_new(path: &Path, bytes: &[u8]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}
fn write_tree(path: &Path, tree: &Tree) -> Result<()> {
    ensure!(!path.exists(), "snapshot destination exists");
    fs::create_dir(path)?;
    for (name, e) in tree {
        let dest = path.join(name);
        write_new(&dest, &e.bytes)?;
        fs::set_permissions(dest, fs::Permissions::from_mode(e.mode))?;
    }
    ensure!(
        manifest(&read_tree(path)?) == manifest(tree),
        "snapshot readback failed"
    );
    Ok(())
}
struct Lock(PathBuf);
impl Drop for Lock {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}
fn lock(dir: &Path) -> Result<Lock> {
    fs::create_dir_all(dir)?;
    no_symlinks(dir)?;
    let path = dir.join(".lock");
    write_new(&path, format!("pid={}\n", std::process::id()).as_bytes())
        .context("gate locked; inspect owner before stale-lock recovery")?;
    Ok(Lock(path))
}
fn save_state(dir: &Path, s: &RunState) -> Result<()> {
    let tmp = dir.join("state.next.json");
    write_new(&tmp, &serde_json::to_vec_pretty(s)?)?;
    fs::rename(tmp, dir.join("state.json"))?;
    fs::File::open(dir)?.sync_all()?;
    Ok(())
}
struct Scratch(PathBuf);
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn scratch() -> Result<Scratch> {
    for _ in 0..100 {
        let path = std::env::temp_dir().join(format!(
            "devforge-run-{}-{}",
            std::process::id(),
            COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        if fs::create_dir(&path).is_ok() {
            fs::set_permissions(&path, fs::Permissions::from_mode(0o700))?;
            return Ok(Scratch(path));
        }
    }
    bail!("cannot allocate scratch directory")
}
fn run_tests(tree: &Tree, test_root: &str) -> Result<TestResult> {
    let temp = scratch()?;
    write_tree(&temp.0.join("work"), tree)?;
    write_new(&temp.0.join("runner.py"), RUNNER.as_bytes())?;
    fs::create_dir(temp.0.join("output"))?;
    let log = fs::File::create(temp.0.join("log"))?;
    let mut cmd = Command::new("/usr/bin/bwrap");
    cmd.args([
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--cap-drop",
        "ALL",
    ]);
    for dir in ["/usr", "/lib", "/lib64"] {
        if Path::new(dir).exists() {
            cmd.args(["--ro-bind", dir, dir]);
        }
    }
    cmd.args([
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/work",
    ])
    .arg("--ro-bind")
    .arg(temp.0.join("work"))
    .arg("/work")
    .arg("--ro-bind")
    .arg(temp.0.join("runner.py"))
    .arg("/runner.py")
    .arg("--bind")
    .arg(temp.0.join("output"))
    .arg("/output")
    .args([
        "--chdir",
        "/work",
        "/usr/bin/python3",
        "-I",
        "-B",
        "/runner.py",
        test_root,
    ])
    .env_clear()
    .env("PATH", "/usr/bin:/bin")
    .env("LANG", "C.UTF-8")
    .stdout(Stdio::from(log.try_clone()?))
    .stderr(Stdio::from(log));
    let mut child = cmd
        .spawn()
        .context("COULD_NOT_RUN: bubblewrap/Python unavailable")?;
    let started = Instant::now();
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if started.elapsed() > Duration::from_secs(10) {
            let _ = child.kill();
            let _ = child.wait();
            bail!("COULD_NOT_RUN: runner exceeded 10 seconds");
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    ensure!(
        status.success(),
        "COULD_NOT_RUN: isolated runner failed: {}",
        String::from_utf8_lossy(&fs::read(temp.0.join("log"))?)
            .chars()
            .take(2000)
            .collect::<String>()
    );
    let path = temp.0.join("output/result.json");
    ensure!(
        path.is_file() && fs::metadata(&path)?.len() <= 1_048_576,
        "COULD_NOT_RUN: missing/oversized report"
    );
    let result: TestResult =
        serde_json::from_slice(&fs::read(path)?).context("COULD_NOT_RUN: invalid report")?;
    ensure!(
        result.tests > 0
            && result.errors == 0
            && result.skipped == 0
            && result.expected_failures == 0
            && result.unexpected_successes == 0
            && result.test_ids.len() == result.tests,
        "COULD_NOT_RUN: suite empty, broken, or skipped: {}",
        result.output
    );
    Ok(result)
}
fn test_manifest(m: &Manifest, root: &str) -> Manifest {
    m.iter()
        .filter(|(p, _)| under(p, root))
        .map(|(p, v)| (p.clone(), v.clone()))
        .collect()
}
fn only_changes(baseline: &Manifest, candidate: &Manifest, roots: &[String]) -> Result<()> {
    for path in baseline.keys().chain(candidate.keys()) {
        if baseline.get(path) != candidate.get(path) {
            ensure!(
                roots.iter().any(|root| under(path, root)),
                "change outside phase scope: {path}"
            );
        }
    }
    Ok(())
}
fn execute(cli: Cli) -> Result<Value> {
    if let Action::Isolate { command, runtime } = &cli.command {
        let project = resolved(cli.project.as_ref().context("--project is required")?)?;
        ensure!(
            project.is_dir() && project.parent().is_some(),
            "invalid workspace"
        );
        ensure!(
            !std::env::current_exe()?.starts_with(&project),
            "validator must be outside writable workspace"
        );
        let mut execution = command.clone();
        let mut isolation = Command::new("/usr/bin/bwrap");
        isolation
            .args([
                "--die-with-parent",
                "--new-session",
                "--unshare-pid",
                "--cap-drop",
                "ALL",
                "--ro-bind",
                "/",
                "/",
            ])
            .args(["--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev"])
            .arg("--bind")
            .arg(&project)
            .arg(&project);
        if let Some(provider) = runtime {
            // Some CLI installations place the binary under the client home we mask.
            let requested = PathBuf::from(&execution[0]);
            let executable = if requested.components().count() > 1 {
                fs::canonicalize(&requested)?
            } else {
                let search = std::env::var_os("PATH").context("PATH unavailable")?;
                let found = std::env::split_paths(&search)
                    .map(|dir| dir.join(&requested))
                    .find(|path| path.is_file())
                    .context("client executable unavailable")?;
                fs::canonicalize(found)?
            };
            isolation
                .arg("--ro-bind")
                .arg(&executable)
                .arg("/tmp/devforge-client");
            execution[0] = OsString::from("/tmp/devforge-client");
            let private = project.join(".devforge-runtime").join(provider);
            no_symlinks(&private)?;
            fs::create_dir_all(&private)?;
            fs::set_permissions(
                project.join(".devforge-runtime"),
                fs::Permissions::from_mode(0o700),
            )?;
            let home = PathBuf::from(std::env::var_os("HOME").context("HOME unavailable")?);
            let client_home = private.join("home");
            fs::create_dir_all(&client_home)?;
            let target_home = if provider == "codex" {
                std::env::var_os("CODEX_HOME")
                    .map(PathBuf::from)
                    .unwrap_or_else(|| home.join(".codex"))
            } else {
                ensure!(
                    std::env::var_os("CLAUDE_CONFIG_DIR").is_none(),
                    "custom CLAUDE_CONFIG_DIR is unsupported by this POC launcher"
                );
                home.join(".claude")
            };
            isolation.arg("--bind").arg(&client_home).arg(target_home);
            for name in [
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
                "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_USE_VERTEX",
                "CLAUDE_CODE_USE_FOUNDRY",
            ] {
                isolation.env_remove(name);
            }
            if provider == "claude" {
                let settings = private.join("claude.json");
                if !settings.exists() {
                    write_new(&settings, b"{}\n")?;
                }
                fs::set_permissions(&settings, fs::Permissions::from_mode(0o600))?;
                isolation
                    .arg("--bind")
                    .arg(&settings)
                    .arg(home.join(".claude.json"));
            }
            let ignore = project.join(".gitignore");
            no_symlinks(&ignore)?;
            let mut content = fs::read_to_string(&ignore).unwrap_or_default();
            if !content.lines().any(|line| line == "/.devforge-runtime/") {
                content.push_str("\n/.devforge-runtime/\n");
                fs::write(ignore, content)?;
            }
        }
        let status = isolation
            .arg("--chdir")
            .arg(&project)
            .arg("--")
            .args(&execution)
            .status()
            .context("COULD_NOT_RUN: isolation unavailable; no unconfined fallback")?;
        ensure!(status.success(), "isolated command failed: {status}");
        return Ok(
            json!({"status":"COMPLETED","isolation":"filesystem","network_isolation":false,"writable_project":project}),
        );
    }
    if let Action::Install { action } = &cli.command {
        return install::run(action, cli.project.as_deref());
    }
    let gate = Gate::load(&cli)?;
    let tree = read_tree(&gate.project)?;
    let current = manifest(&tree);
    match cli.command {
        Action::Expert { action } => match action {
            ExpertAction::Prepare => Ok(
                json!({"status":"CONTEXT_READY","project_id":gate.policy.project_id,"goal":gate.policy.goal,"story_id":gate.policy.story_id,"approved_dependencies":gate.policy.dependencies,"required_experts":gate.policy.expert_dirs,"upstream":gate.policy.upstream,"policy_sha256":gate.policy_hash,"instruction":"AI authors SKILL.md, then expert bind records provenance. Structural binding is not behavioral acceptance."}),
            ),
            ExpertAction::Status => {
                let mut experts = Vec::new();
                for e in &gate.policy.expert_dirs {
                    let expected = gate.desired_binding(&tree, e);
                    let binding = tree
                        .get(&format!("{e}/provenance.json"))
                        .and_then(|v| serde_json::from_slice::<Binding>(&v.bytes).ok());
                    let status = if binding.is_none() {
                        "MISSING"
                    } else if expected
                        .as_ref()
                        .is_ok_and(|want| Some(want) == binding.as_ref())
                    {
                        "CURRENT"
                    } else {
                        "STALE"
                    };
                    experts.push(json!({"expert":e,"status":status,"behavior":"NOT_EVALUATED"}));
                }
                Ok(json!({"status":"INSPECTED","experts":experts}))
            }
            ExpertAction::Bind => {
                let e = cli.expert.context("--expert is required")?;
                relative(&e)?;
                let binding = gate.desired_binding(&tree, &e)?;
                let dir = gate.project.join(&e);
                let _lock = lock(&dir)?;
                let path = dir.join("provenance.json");
                let bytes = serde_json::to_vec_pretty(&binding)?;
                if let Ok(old) = fs::read(&path) {
                    if old == bytes {
                        return Ok(
                            json!({"status":"BOUND","expert":e,"behavior":"NOT_EVALUATED","changed":false}),
                        );
                    }
                    let history = dir.join("history").join(format!("{}.json", hash(&old)));
                    if history.exists() {
                        ensure!(fs::read(&history)? == old, "history collision");
                    } else {
                        write_new(&history, &old)?;
                    }
                }
                write_new(&dir.join("provenance.next.json"), &bytes)?;
                fs::rename(dir.join("provenance.next.json"), path)?;
                Ok(json!({"status":"BOUND","expert":e,"behavior":"NOT_EVALUATED","changed":true}))
            }
        },
        Action::Check => {
            gate.check(&tree)?;
            Ok(
                json!({"status":"PASS","scope":"structural_policy_and_provenance","behavior":"NOT_EVALUATED","candidate_sha256":tree_hash(&tree)?}),
            )
        }
        Action::Status => {
            let state = gate.read_state()?;
            Ok(
                json!({"status":"INSPECTED","phase":state.phase,"candidate_check":gate.check(&tree).err().map(|e|e.to_string()),"candidate_sha256":tree_hash(&tree)?}),
            )
        }
        Action::Init => {
            gate.check(&tree)?;
            if tree.keys().any(|p| {
                under(p, &gate.policy.test_root)
                    && Path::new(p)
                        .file_name()
                        .and_then(|n| n.to_str())
                        .is_some_and(|n| n.starts_with("test_") && n.ends_with(".py"))
            }) {
                let baseline_result = run_tests(&tree, &gate.policy.test_root)?;
                ensure!(
                    baseline_result.failures == 0,
                    "baseline tests must pass before initialization"
                );
            }
            let dir = gate.state_dir()?;
            let _lock = lock(dir)?;
            ensure!(
                !dir.join("state.json").exists(),
                "run already exists; use a new external state directory"
            );
            let state = RunState {
                schema: 1,
                project: gate.project.clone(),
                project_id: gate.policy.project_id.clone(),
                policy_sha256: gate.policy_hash.clone(),
                runner_sha256: hash(RUNNER.as_bytes()),
                phase: Phase::Initialized,
                baseline: current,
                red_tests: None,
                red_result: None,
                green: None,
                green_result: None,
            };
            save_state(dir, &state)?;
            Ok(json!({"status":"INITIALIZED","project_id":state.project_id}))
        }
        Action::Red | Action::Green | Action::Accept | Action::Verify => {
            gate.check(&tree)?;
            let dir = gate.state_dir()?;
            let _lock = if matches!(cli.command, Action::Verify) {
                None
            } else {
                Some(lock(dir)?)
            };
            let mut state = gate.read_state()?;
            match cli.command {
                Action::Red => {
                    ensure!(
                        state.phase == Phase::Initialized,
                        "RED requires initialized phase"
                    );
                    only_changes(
                        &state.baseline,
                        &current,
                        std::slice::from_ref(&gate.policy.test_root),
                    )?;
                    ensure!(
                        test_manifest(&state.baseline, &gate.policy.test_root)
                            != test_manifest(&current, &gate.policy.test_root),
                        "RED requires changed tests"
                    );
                    let result = run_tests(&tree, &gate.policy.test_root)?;
                    ensure!(result.failures > 0, "RED requires assertion failure");
                    state.red_tests = Some(test_manifest(&current, &gate.policy.test_root));
                    state.red_result = Some(result);
                    state.phase = Phase::Red;
                }
                Action::Green => {
                    ensure!(
                        state.phase == Phase::Red,
                        "GREEN requires recorded RED evidence"
                    );
                    let mut roots = gate.policy.source_roots.clone();
                    roots.push(gate.policy.test_root.clone());
                    only_changes(&state.baseline, &current, &roots)?;
                    ensure!(
                        state.red_tests.as_ref()
                            == Some(&test_manifest(&current, &gate.policy.test_root)),
                        "tests differ from recorded RED"
                    );
                    let result = run_tests(&tree, &gate.policy.test_root)?;
                    ensure!(result.failures == 0, "GREEN requires passing tests");
                    ensure!(
                        state
                            .red_result
                            .as_ref()
                            .is_some_and(|red| red.test_ids == result.test_ids),
                        "test selection differs from RED"
                    );
                    state.green = Some(current.clone());
                    state.green_result = Some(result);
                    state.phase = Phase::Green;
                }
                Action::Accept => {
                    ensure!(
                        state.phase == Phase::Green && state.green.as_ref() == Some(&current),
                        "accept requires exact GREEN candidate"
                    );
                    let dest = dir.join("accepted");
                    if dest.exists() {
                        ensure!(
                            manifest(&read_tree(&dest)?) == current,
                            "existing accepted snapshot differs"
                        );
                    } else {
                        write_tree(&dest, &tree)?;
                    }
                    state.phase = Phase::Accepted;
                }
                Action::Verify => {
                    ensure!(
                        state.phase == Phase::Accepted && state.green.as_ref() == Some(&current),
                        "candidate differs from accepted revision"
                    );
                    ensure!(
                        manifest(&read_tree(&dir.join("accepted"))?) == current,
                        "accepted archive integrity failure"
                    );
                    return Ok(
                        json!({"status":"VERIFIED","candidate_sha256":tree_hash(&tree)?,"policy_sha256":gate.policy_hash}),
                    );
                }
                _ => unreachable!(),
            }
            ensure!(
                manifest(&read_tree(&gate.project)?) == current,
                "candidate changed during evaluation; evidence not committed"
            );
            save_state(dir, &state)?;
            let status = match state.phase {
                Phase::Red => "RED",
                Phase::Green => "GREEN",
                Phase::Accepted => "ACCEPTED",
                Phase::Initialized => unreachable!(),
            };
            Ok(
                json!({"status":status,"candidate_sha256":tree_hash(&tree)?,"state":dir,"runner_isolation":"filesystem; network not isolated","scope":"local POC acceptance; semantic review still required"}),
            )
        }
        Action::Isolate { .. }
        | Action::Delivery { .. }
        | Action::Install { .. }
        | Action::Validate { .. } => unreachable!(),
    }
}
fn main() {
    let cli = Cli::parse();
    if let Action::Delivery { action } = &cli.command {
        delivery::main(action, cli.state.as_deref());
        return;
    }
    if let Action::Validate { action } = &cli.command {
        validate_mvp::main(action);
        return;
    }
    match execute(cli) {
        Ok(value) => {
            println!(
                "{}",
                serde_json::to_string_pretty(&value).expect("JSON output")
            );
            // A structured BLOCKED report (the evidence preflight) exits like a refusal.
            if value["status"] == "BLOCKED" {
                std::process::exit(2);
            }
        }
        Err(error) => {
            let message = format!("{error:#}");
            let status = if message.contains("COULD_NOT_RUN:") {
                "COULD_NOT_RUN"
            } else if message.contains("STALE:") {
                "STALE"
            } else {
                "BLOCKED"
            };
            println!("{}", json!({"status":status,"reason":message}));
            std::process::exit(2);
        }
    }
}
