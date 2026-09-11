//! Framework package readers shared by installation and structural validation.
//!
//! These are direct ports of the readers in `scripts/runtime_requirements.py`:
//! the runtime-requirement sidecar, the bounded hook source and the strict JSON
//! decoding both rely on. They read declarative files only and never execute a
//! hook, a runtime or candidate code. Capability probing and admission stay in
//! `install probe-runtime`; nothing here re-checks or second-guesses it.
//!
//! Every refusal keeps the exact legacy message so callers ported from the
//! Python scripts preserve their observable `BLOCKED` reasons.
#![allow(dead_code)] // Consumed by the `validate framework` and `install framework` slices.
use crate::delivery::StrictJson;
use anyhow::{Result, bail, ensure};
use serde_json::{Value, json};
use std::fs;
use std::path::Path;

/// The synchronous session events a delivery-aware package must hook, in order.
pub(crate) const REQUIRED_EVENTS: [&str; 4] =
    ["SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"];
/// Plugin-relative location of the runtime requirement sidecar.
pub(crate) const REQUIREMENT_PATH: &str = "hooks/runtime-requirements.json";
const PROVIDERS: [&str; 2] = ["codex", "claude"];

/// Decode JSON, refusing duplicate object keys and non-finite numbers.
pub(crate) fn strict_json(raw: &[u8]) -> Result<Value> {
    let StrictJson(value) = serde_json::from_slice(raw)?;
    Ok(value)
}

/// Read one file and decode it strictly.
pub(crate) fn read_json(path: &Path) -> Result<Value> {
    strict_json(&fs::read(path)?)
}

fn is_symlink(path: &Path) -> bool {
    fs::symlink_metadata(path).is_ok_and(|meta| meta.file_type().is_symlink())
}

fn parent_is_symlink(path: &Path) -> bool {
    path.parent().is_some_and(is_symlink)
}

/// Read the plugin's runtime requirement, if declared, and require its exact
/// supported form. Returns `None` when the sidecar is absent.
pub(crate) fn load_requirement(plugin: &Path, provider: &str) -> Result<Option<Value>> {
    let path = plugin.join(REQUIREMENT_PATH);
    ensure!(
        !is_symlink(&path) && !parent_is_symlink(&path),
        "symlink runtime requirement: {}",
        path.display()
    );
    if !path.exists() {
        return Ok(None);
    }
    ensure!(
        path.is_file(),
        "runtime requirement must be a regular file: {}",
        path.display()
    );
    let expected = json!({
        "schema_version": "devforge.runtime-requirement/v1",
        "runtime": "devforge.delivery",
        "protocol": "devforge.delivery-runtime/v1",
        "provider": provider,
        "completion_mode": "managed-session",
        "required_events": REQUIRED_EVENTS,
    });
    let data = read_json(&path)?;
    ensure!(
        PROVIDERS.contains(&provider) && data.is_object() && data == expected,
        "unsupported or malformed runtime requirement: {}",
        path.display()
    );
    Ok(Some(data))
}

/// Check an event-to-group-list hook object without running any handler.
pub(crate) fn validate_hook_groups(hooks: &Value, command_only: bool) -> Result<()> {
    let Some(hooks) = hooks.as_object() else {
        bail!("hooks must be an event-to-group-list object");
    };
    for (event, groups) in hooks {
        let Some(groups) = groups.as_array().filter(|_| !event.trim().is_empty()) else {
            bail!("hook event needs a nonempty name and group list");
        };
        for group in groups {
            let handlers = group
                .as_object()
                .and_then(|group| group.get("hooks"))
                .and_then(Value::as_array)
                .filter(|handlers| !handlers.is_empty());
            let Some(handlers) = handlers else {
                bail!("hook group needs a nonempty hooks list");
            };
            if let Some(matcher) = group.get("matcher") {
                ensure!(matcher.is_string(), "hook matcher must be a string");
            }
            for handler in handlers {
                let kind = handler
                    .as_object()
                    .and_then(|handler| handler.get("type"))
                    .and_then(Value::as_str)
                    .filter(|kind| !kind.trim().is_empty());
                let Some(kind) = kind else {
                    bail!("hook handler needs a nonempty type");
                };
                ensure!(
                    !command_only || kind == "command",
                    "framework hook handlers must be command handlers"
                );
                if kind == "command" {
                    let command = handler.get("command").and_then(Value::as_str);
                    ensure!(
                        command.is_some_and(|command| !command.trim().is_empty()),
                        "command hook needs a nonempty command string"
                    );
                }
            }
        }
    }
    Ok(())
}

/// Require the exact synchronous delivery hook selection for one provider.
pub(crate) fn validate_delivery_hooks(source: &Value, provider: &str) -> Result<()> {
    let hooks = source["hooks"].as_object().filter(|hooks| {
        hooks.len() == REQUIRED_EVENTS.len()
            && REQUIRED_EVENTS
                .iter()
                .all(|event| hooks.contains_key(*event))
    });
    let Some(hooks) = hooks else {
        bail!("delivery requirement needs exactly its four required hook events");
    };
    let expected_command = format!(
        "\"${{DEVFORGE_DELIVERY_EXECUTABLE:-devforge}}\" delivery hook --provider {provider}"
    );
    for event in REQUIRED_EVENTS {
        let groups = hooks[event].as_array();
        let group = groups
            .filter(|groups| groups.len() == 1)
            .map(|groups| &groups[0]);
        let handlers = group.and_then(|group| group["hooks"].as_array());
        let Some(handler) = handlers
            .filter(|handlers| handlers.len() == 1)
            .map(|handlers| &handlers[0])
        else {
            bail!("delivery requirement needs one command group and handler: {event}");
        };
        let group = group.expect("group exists when its handler does");
        ensure!(
            group.get("matcher").unwrap_or(&json!("")) == "",
            "delivery hook must select every {event} event"
        );
        ensure!(
            handler["type"] == "command" && handler["command"] == expected_command.as_str(),
            "incompatible delivery hook command: {event}"
        );
        // Async and conditional handlers cannot supply synchronous completion evidence.
        let group_keys_ok = group.as_object().is_some_and(|g| {
            g.keys()
                .all(|key| matches!(key.as_str(), "matcher" | "hooks"))
        });
        let handler_keys_ok = handler.as_object().is_some_and(|h| {
            h.keys()
                .all(|key| matches!(key.as_str(), "type" | "command" | "timeout"))
        });
        ensure!(
            group_keys_ok && handler_keys_ok,
            "unsupported delivery hook options: {event}"
        );
        if let Some(timeout) = handler.get("timeout") {
            ensure!(
                timeout.as_f64().is_some_and(|seconds| seconds > 0.0),
                "delivery hook timeout must be a positive number: {event}"
            );
        }
    }
    Ok(())
}

/// Read the bounded framework hook source without running any handler.
/// Returns `None` when the plugin declares no hooks and has no hook directory.
pub(crate) fn load_plugin_hooks(plugin: &Path, provider: &str) -> Result<Option<Value>> {
    ensure!(
        PROVIDERS.contains(&provider),
        "unknown provider: {provider}"
    );
    let requirement = load_requirement(plugin, provider)?;
    let manifest = plugin.join(format!(".{provider}-plugin/plugin.json"));
    ensure!(
        !is_symlink(&manifest) && !parent_is_symlink(&manifest),
        "symlink hook manifest: {}",
        manifest.display()
    );
    let data = if manifest.exists() {
        read_json(&manifest)?
    } else {
        json!({})
    };
    let Some(data) = data.as_object() else {
        bail!("plugin manifest must be an object");
    };
    let declared = data.contains_key("hooks");
    ensure!(
        !declared || data["hooks"] == "hooks/hooks.json" || data["hooks"] == "./hooks/hooks.json",
        "framework hooks must select hooks/hooks.json"
    );
    let directory = plugin.join("hooks");
    let source = directory.join("hooks.json");
    ensure!(
        !is_symlink(&directory) && !is_symlink(&source),
        "symlink hook source: {}",
        source.display()
    );
    if !declared && !directory.exists() {
        return Ok(None);
    }
    ensure!(
        directory.is_dir() && source.is_file(),
        "missing framework hook component: {}",
        source.display()
    );
    let hooks = read_json(&source)?;
    let shape_ok = hooks.as_object().is_some_and(|hooks| {
        hooks.contains_key("hooks")
            && hooks
                .keys()
                .all(|key| matches!(key.as_str(), "hooks" | "description"))
    });
    ensure!(
        shape_ok,
        "hook source needs hooks and optional description only"
    );
    if let Some(description) = hooks.get("description") {
        ensure!(description.is_string(), "hook description must be a string");
    }
    validate_hook_groups(&hooks["hooks"], true)?;
    if requirement.is_some() {
        validate_delivery_hooks(&hooks, provider)?;
    }
    Ok(Some(hooks))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    struct Scratch(PathBuf);
    impl Scratch {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "devforge-plugin-{}-{}",
                std::process::id(),
                COUNTER.fetch_add(1, Ordering::SeqCst)
            ));
            fs::create_dir_all(&root).unwrap();
            Scratch(root)
        }
        fn plugin(&self) -> PathBuf {
            let plugin = self.0.join("providers/claude/plugins/devforgeai");
            fs::create_dir_all(plugin.join("skills")).unwrap();
            plugin
        }
        fn write(&self, relative: &str, text: &str) {
            let path = self.0.join(relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, text).unwrap();
        }
    }
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    const PLUGIN: &str = "providers/claude/plugins/devforgeai";

    fn requirement(provider: &str) -> String {
        json!({
            "schema_version": "devforge.runtime-requirement/v1",
            "runtime": "devforge.delivery",
            "protocol": "devforge.delivery-runtime/v1",
            "provider": provider,
            "completion_mode": "managed-session",
            "required_events": REQUIRED_EVENTS,
        })
        .to_string()
    }

    fn delivery_hooks(provider: &str) -> Value {
        let command = format!(
            "\"${{DEVFORGE_DELIVERY_EXECUTABLE:-devforge}}\" delivery hook --provider {provider}"
        );
        let mut hooks = serde_json::Map::new();
        for event in REQUIRED_EVENTS {
            hooks.insert(
                event.to_string(),
                json!([{"hooks": [{"type": "command", "command": command}]}]),
            );
        }
        json!({"hooks": hooks})
    }

    fn reason(error: anyhow::Error) -> String {
        error.to_string()
    }

    #[test]
    fn strict_json_rejects_duplicates_and_nonfinite_values() {
        assert_eq!(strict_json(br#"{"a": 1}"#).unwrap(), json!({"a": 1}));
        assert!(
            reason(strict_json(br#"{"a": 1, "a": 2}"#).unwrap_err())
                .contains("duplicate JSON key: a")
        );
        assert!(strict_json(b"NaN").is_err());
        assert!(strict_json(b"1e999").is_err());
        assert!(strict_json(b"[1, 2").is_err());
    }

    #[test]
    fn requirement_is_optional_exact_and_provider_bound() {
        let scratch = Scratch::new();
        let plugin = scratch.plugin();
        assert_eq!(load_requirement(&plugin, "claude").unwrap(), None);
        scratch.write(
            &format!("{PLUGIN}/{REQUIREMENT_PATH}"),
            &requirement("claude"),
        );
        let loaded = load_requirement(&plugin, "claude").unwrap().unwrap();
        assert_eq!(loaded["provider"], "claude");
        assert_eq!(loaded["required_events"], json!(REQUIRED_EVENTS));
        let wrong = reason(load_requirement(&plugin, "codex").unwrap_err());
        assert!(
            wrong.starts_with("unsupported or malformed runtime requirement: "),
            "{wrong}"
        );
        scratch.write(
            &format!("{PLUGIN}/{REQUIREMENT_PATH}"),
            &requirement("claude").replace("managed-session", "detached"),
        );
        assert!(load_requirement(&plugin, "claude").is_err());
        scratch.write(&format!("{PLUGIN}/{REQUIREMENT_PATH}"), r#"{"a":1,"a":2}"#);
        assert!(
            reason(load_requirement(&plugin, "claude").unwrap_err()).contains("duplicate JSON key")
        );
        fs::remove_file(plugin.join(REQUIREMENT_PATH)).unwrap();
        fs::create_dir(plugin.join(REQUIREMENT_PATH)).unwrap();
        assert!(
            reason(load_requirement(&plugin, "claude").unwrap_err())
                .starts_with("runtime requirement must be a regular file: ")
        );
    }

    #[test]
    fn symlinked_requirement_or_hook_components_are_refused() {
        let scratch = Scratch::new();
        let plugin = scratch.plugin();
        scratch.write(
            "elsewhere/runtime-requirements.json",
            &requirement("claude"),
        );
        fs::create_dir_all(plugin.join("hooks")).unwrap();
        symlink(
            scratch.0.join("elsewhere/runtime-requirements.json"),
            plugin.join(REQUIREMENT_PATH),
        )
        .unwrap();
        assert!(
            reason(load_requirement(&plugin, "claude").unwrap_err())
                .starts_with("symlink runtime requirement: ")
        );
        fs::remove_file(plugin.join(REQUIREMENT_PATH)).unwrap();
        fs::remove_dir(plugin.join("hooks")).unwrap();
        fs::create_dir_all(scratch.0.join("elsewhere/hooks")).unwrap();
        symlink(scratch.0.join("elsewhere/hooks"), plugin.join("hooks")).unwrap();
        assert!(
            reason(load_requirement(&plugin, "claude").unwrap_err())
                .starts_with("symlink runtime requirement: ")
        );
        assert!(
            reason(load_plugin_hooks(&plugin, "claude").unwrap_err())
                .starts_with("symlink runtime requirement: ")
        );
        fs::remove_file(plugin.join("hooks")).unwrap();
        scratch.write("elsewhere/plugin.json", r#"{"name":"devforgeai"}"#);
        fs::create_dir_all(plugin.join(".claude-plugin")).unwrap();
        symlink(
            scratch.0.join("elsewhere/plugin.json"),
            plugin.join(".claude-plugin/plugin.json"),
        )
        .unwrap();
        assert!(
            reason(load_plugin_hooks(&plugin, "claude").unwrap_err())
                .starts_with("symlink hook manifest: ")
        );
    }

    #[test]
    fn hook_groups_are_checked_field_by_field() {
        let ok = json!({"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "x"}]}]});
        validate_hook_groups(&ok, true).unwrap();
        let cases: [(Value, &str); 8] = [
            (json!([]), "hooks must be an event-to-group-list object"),
            (
                json!({" ": []}),
                "hook event needs a nonempty name and group list",
            ),
            (
                json!({"Stop": {}}),
                "hook event needs a nonempty name and group list",
            ),
            (
                json!({"Stop": [{"hooks": []}]}),
                "hook group needs a nonempty hooks list",
            ),
            (
                json!({"Stop": [{"matcher": 1, "hooks": [{"type": "command", "command": "x"}]}]}),
                "hook matcher must be a string",
            ),
            (
                json!({"Stop": [{"hooks": [{"type": " "}]}]}),
                "hook handler needs a nonempty type",
            ),
            (
                json!({"Stop": [{"hooks": [{"type": "prompt", "prompt": "x"}]}]}),
                "framework hook handlers must be command handlers",
            ),
            (
                json!({"Stop": [{"hooks": [{"type": "command", "command": " "}]}]}),
                "command hook needs a nonempty command string",
            ),
        ];
        for (hooks, expected) in cases {
            assert_eq!(
                reason(validate_hook_groups(&hooks, true).unwrap_err()),
                expected
            );
        }
        let prompt = json!({"Stop": [{"hooks": [{"type": "prompt", "prompt": "x"}]}]});
        validate_hook_groups(&prompt, false).unwrap();
    }

    #[test]
    fn delivery_hooks_require_the_exact_synchronous_selection() {
        validate_delivery_hooks(&delivery_hooks("claude"), "claude").unwrap();
        assert_eq!(
            reason(validate_delivery_hooks(&delivery_hooks("claude"), "codex").unwrap_err()),
            "incompatible delivery hook command: SessionStart"
        );
        let mut extra = delivery_hooks("claude");
        extra["hooks"]["PreToolUse"] = json!([]);
        assert_eq!(
            reason(validate_delivery_hooks(&extra, "claude").unwrap_err()),
            "delivery requirement needs exactly its four required hook events"
        );
        let mut two = delivery_hooks("claude");
        let group = two["hooks"]["Stop"][0].clone();
        two["hooks"]["Stop"].as_array_mut().unwrap().push(group);
        assert_eq!(
            reason(validate_delivery_hooks(&two, "claude").unwrap_err()),
            "delivery requirement needs one command group and handler: Stop"
        );
        let mut matched = delivery_hooks("claude");
        matched["hooks"]["Stop"][0]["matcher"] = json!("Bash");
        assert_eq!(
            reason(validate_delivery_hooks(&matched, "claude").unwrap_err()),
            "delivery hook must select every Stop event"
        );
        let mut asynchronous = delivery_hooks("claude");
        asynchronous["hooks"]["SessionEnd"][0]["hooks"][0]["async"] = json!(true);
        assert_eq!(
            reason(validate_delivery_hooks(&asynchronous, "claude").unwrap_err()),
            "unsupported delivery hook options: SessionEnd"
        );
        let mut conditional = delivery_hooks("claude");
        conditional["hooks"]["SessionEnd"][0]["if"] = json!("x");
        assert_eq!(
            reason(validate_delivery_hooks(&conditional, "claude").unwrap_err()),
            "unsupported delivery hook options: SessionEnd"
        );
        for timeout in [json!(0), json!(-1.5), json!(true), json!("5")] {
            let mut timed = delivery_hooks("claude");
            timed["hooks"]["Stop"][0]["hooks"][0]["timeout"] = timeout;
            assert_eq!(
                reason(validate_delivery_hooks(&timed, "claude").unwrap_err()),
                "delivery hook timeout must be a positive number: Stop"
            );
        }
        let mut timed = delivery_hooks("claude");
        timed["hooks"]["Stop"][0]["hooks"][0]["timeout"] = json!(2.5);
        validate_delivery_hooks(&timed, "claude").unwrap();
    }

    #[test]
    fn plugin_hooks_follow_declaration_and_default_directory_rules() {
        let scratch = Scratch::new();
        let plugin = scratch.plugin();
        assert_eq!(
            reason(load_plugin_hooks(&plugin, "gemini").unwrap_err()),
            "unknown provider: gemini"
        );
        // No manifest and no hooks directory: nothing to install.
        assert_eq!(load_plugin_hooks(&plugin, "claude").unwrap(), None);
        // A declaration without the component is an error, never a silent skip.
        scratch.write(
            &format!("{PLUGIN}/.claude-plugin/plugin.json"),
            r#"{"name": "devforgeai", "hooks": "./hooks/hooks.json"}"#,
        );
        assert!(
            reason(load_plugin_hooks(&plugin, "claude").unwrap_err())
                .starts_with("missing framework hook component: ")
        );
        scratch.write(
            &format!("{PLUGIN}/.claude-plugin/plugin.json"),
            r#"{"name": "devforgeai", "hooks": "custom/hooks.json"}"#,
        );
        assert_eq!(
            reason(load_plugin_hooks(&plugin, "claude").unwrap_err()),
            "framework hooks must select hooks/hooks.json"
        );
        scratch.write(&format!("{PLUGIN}/.claude-plugin/plugin.json"), "[]");
        assert_eq!(
            reason(load_plugin_hooks(&plugin, "claude").unwrap_err()),
            "plugin manifest must be an object"
        );
        // Default location: a hooks directory without a declaration is read.
        scratch.write(
            &format!("{PLUGIN}/.claude-plugin/plugin.json"),
            r#"{"name": "devforgeai"}"#,
        );
        fs::create_dir_all(plugin.join("hooks")).unwrap();
        assert!(
            reason(load_plugin_hooks(&plugin, "claude").unwrap_err())
                .starts_with("missing framework hook component: ")
        );
        scratch.write(
            &format!("{PLUGIN}/hooks/hooks.json"),
            r#"{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}, "description": "x"}"#,
        );
        let hooks = load_plugin_hooks(&plugin, "claude").unwrap().unwrap();
        assert_eq!(hooks["description"], "x");
        scratch.write(
            &format!("{PLUGIN}/hooks/hooks.json"),
            r#"{"hooks": {}, "description": 1}"#,
        );
        assert_eq!(
            reason(load_plugin_hooks(&plugin, "claude").unwrap_err()),
            "hook description must be a string"
        );
        scratch.write(
            &format!("{PLUGIN}/hooks/hooks.json"),
            r#"{"hooks": {}, "extra": 1}"#,
        );
        assert_eq!(
            reason(load_plugin_hooks(&plugin, "claude").unwrap_err()),
            "hook source needs hooks and optional description only"
        );
        scratch.write(
            &format!("{PLUGIN}/hooks/hooks.json"),
            r#"{"hooks": {"Stop": [{"hooks": [{"type": "prompt", "prompt": "x"}]}]}}"#,
        );
        assert_eq!(
            reason(load_plugin_hooks(&plugin, "claude").unwrap_err()),
            "framework hook handlers must be command handlers"
        );
        scratch.write(
            &format!("{PLUGIN}/hooks/hooks.json"),
            r#"{"hooks": {"a": [], "a": []}}"#,
        );
        assert!(
            reason(load_plugin_hooks(&plugin, "claude").unwrap_err())
                .contains("duplicate JSON key")
        );
    }

    #[test]
    fn a_declared_requirement_binds_the_hook_source_to_the_delivery_contract() {
        let scratch = Scratch::new();
        let plugin = scratch.plugin();
        scratch.write(
            &format!("{PLUGIN}/{REQUIREMENT_PATH}"),
            &requirement("claude"),
        );
        scratch.write(
            &format!("{PLUGIN}/hooks/hooks.json"),
            &delivery_hooks("claude").to_string(),
        );
        let hooks = load_plugin_hooks(&plugin, "claude").unwrap().unwrap();
        assert_eq!(hooks, delivery_hooks("claude"));
        scratch.write(
            &format!("{PLUGIN}/hooks/hooks.json"),
            &delivery_hooks("codex").to_string(),
        );
        assert_eq!(
            reason(load_plugin_hooks(&plugin, "claude").unwrap_err()),
            "incompatible delivery hook command: SessionStart"
        );
        // Without the sidecar the same source is an ordinary command hook set.
        fs::remove_file(plugin.join(REQUIREMENT_PATH)).unwrap();
        assert!(load_plugin_hooks(&plugin, "claude").unwrap().is_some());
    }

    #[test]
    fn the_real_claude_and_codex_plugins_are_read_when_present() {
        let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
        let Some(framework) = ["../DevForgeAI", "../../framework/DevForgeAI"]
            .iter()
            .map(|relative| manifest.join(relative))
            .find(|candidate| candidate.join("providers").is_dir())
        else {
            eprintln!("companion framework absent; real-tree read NOT_RUN");
            return;
        };
        for provider in PROVIDERS {
            let plugin = framework.join(format!("providers/{provider}/plugins/devforgeai"));
            let requirement = load_requirement(&plugin, provider).unwrap();
            let hooks = load_plugin_hooks(&plugin, provider).unwrap();
            if requirement.is_some() {
                assert!(
                    hooks.is_some(),
                    "{provider}: requirement without hook source"
                );
            }
        }
    }
}
