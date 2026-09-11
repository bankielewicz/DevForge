import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("installer", Path(__file__).parents[1] / "scripts/install_framework.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

# The compiled CLI that owns runtime probing and capability validation.
BINARY = Path(os.environ.get("DEVFORGE_BIN", Path(__file__).resolve().parents[1] / "target/debug/devforge"))
SELECTED = object()


class InstallerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A missing build is a failure, never a skip: no other component validates capabilities.
        if not BINARY.is_file() or not os.access(BINARY, os.X_OK):
            raise AssertionError(f"build the CLI first (cargo build --locked); missing {BINARY}")
        holder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(holder.cleanup)
        # Cargo hard-links target/debug/devforge, so select a single-link copy of those bytes.
        cls.validator = Path(holder.name) / "devforge"
        shutil.copy(BINARY, cls.validator)
        cls.validator.chmod(0o755)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.framework = self.root / "framework"
        self.project = self.root / "project"
        self.project.mkdir()
        for provider in ("codex", "claude"):
            skill = self.framework / f"providers/{provider}/plugins/devforgeai/skills/demo/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(f"{provider} skill")
            manifest = skill.parents[2] / f".{provider}-plugin/plugin.json"
            manifest.parent.mkdir()
            manifest.write_text(json.dumps({"name": "devforgeai"}))
        self.skill = self.framework / "providers/codex/plugins/devforgeai/skills/demo/SKILL.md"
        (self.framework / "providers/claude/plugins/devforgeai/agents").mkdir()
        (self.framework / "providers/codex/agents").mkdir(parents=True)

    def install(self):
        return installer.install(self.framework, self.project, "both")

    def test_promoted_codex_expert_requires_evidence_before_any_install_write(self):
        # A recognized identity cannot opt out by omitting a candidate-owned profile.
        self.skill.parent.rename(self.skill.parent.with_name("devforge-evaluate-expert"))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "manual.*evidence"):
            self.install()
        self.assertEqual(self.snapshot(), before)

    def test_installs_both_providers_and_repeats(self):
        self.install()
        self.install()
        self.assertEqual((self.project / ".agents/skills/demo/SKILL.md").read_text(), "codex skill")
        self.assertEqual((self.project / ".claude/skills/demo/SKILL.md").read_text(), "claude skill")

    def test_authoring_material_excluded_and_runtime_resources_preserved(self):
        for rel in ("evals/evals.json", "evals/files/input.md", "__pycache__/helper.pyc",
                    "assets/template.md", "references/rules.md", "scripts/check.py"):
            path = self.skill.parent / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rel)
        self.install()
        installed = self.project / ".agents/skills/demo"
        self.assertFalse((installed / "evals").exists())
        self.assertFalse((installed / "__pycache__").exists())
        for rel in ("assets/template.md", "references/rules.md", "scripts/check.py"):
            self.assertEqual((installed / rel).read_text(), rel)

    def test_missing_provider_source_cannot_fall_back_to_shared_tree(self):
        self.skill.unlink()
        self.skill.parent.rmdir()
        self.skill.parent.parent.rmdir()
        legacy = self.framework / "plugins/devforgeai/skills/demo/SKILL.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("wrong provider")
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.project / ".claude").exists())

    def test_managed_old_eval_file_is_removed_but_edited_one_blocks(self):
        self.install()
        relative = ".agents/skills/demo/evals/evals.json"
        dest = self.project / relative
        dest.parent.mkdir(parents=True)
        dest.write_text("old cases")
        record = self.project / ".devforge-install.json"
        data = json.loads(record.read_text())
        data["files"][relative] = installer.digest(dest.read_bytes())
        record.write_text(json.dumps(data))
        dest.write_text("user cases")
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(dest.read_text(), "user cases")
        dest.write_text("old cases")
        self.install()
        self.assertFalse(dest.exists())
        self.assertNotIn(relative, json.loads(record.read_text())["files"])

    def test_export_preserves_runtime_and_excludes_authoring_material(self):
        extra = self.skill.parent / "evals/evals.json"
        extra.parent.mkdir()
        extra.write_text("{}")
        helper = self.skill.parent / "scripts/check.py"
        helper.parent.mkdir()
        helper.write_text("print('ok')")
        output = self.root / "export/devforgeai"
        result = installer.export_plugin(self.framework, "codex", output)
        self.assertEqual((output / "skills/demo/SKILL.md").read_text(), "codex skill")
        self.assertEqual((output / "skills/demo/scripts/check.py").read_bytes(), helper.read_bytes())
        self.assertFalse((output / "skills/demo/evals").exists())
        self.assertTrue((output / ".codex-plugin/plugin.json").is_file())
        self.assertEqual(result["behavior"], "NOT_EVALUATED")
        with self.assertRaises(ValueError):
            installer.export_plugin(self.framework, "codex", output)

    def test_local_edit_collision_is_preserved(self):
        self.install()
        dest = self.project / ".agents/skills/demo/SKILL.md"
        dest.write_text("user modification")
        self.skill.write_text("upstream update")
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(dest.read_text(), "user modification")

    def test_managed_refresh_updates_unmodified_copy(self):
        self.install()
        self.skill.write_text("upstream update")
        self.install()
        self.assertEqual((self.project / ".agents/skills/demo/SKILL.md").read_text(), "upstream update")

    def test_symlink_destination_is_rejected(self):
        (self.project / ".agents").symlink_to(self.root / "elsewhere")
        with self.assertRaises(ValueError):
            self.install()

    def hook_group(self, command="devforge delivery hook --provider codex"):
        return {"hooks": [{"type": "command", "command": command, "timeout": 3}],
                "matcher": "", "custom_group_metadata": {"retained": True}}

    def hook_source(self, provider="codex", group=None, declared=False):
        plugin = self.framework / f"providers/{provider}/plugins/devforgeai"
        path = plugin / "hooks/hooks.json"
        path.parent.mkdir(exist_ok=True)
        document = {"description": "framework delivery hooks",
                    "hooks": {"Stop": [group if group is not None else self.hook_group()]}}
        path.write_text(json.dumps(document))
        if declared:
            (plugin / f".{provider}-plugin/plugin.json").write_text(json.dumps(
                {"name": "devforgeai", "hooks": "./hooks/hooks.json"}))
        return path

    def settings_path(self, provider="codex"):
        relative = ".codex/hooks.json" if provider == "codex" else ".claude/settings.local.json"
        return self.project / relative

    def settings(self, document, provider="codex"):
        path = self.settings_path(provider)
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(document))
        return path

    def inventory(self):
        return json.loads((self.project / ".devforge-install.json").read_text())

    def snapshot(self):
        return {str(p.relative_to(self.project)): p.read_bytes()
                for p in self.project.rglob("*") if p.is_file() and not p.is_symlink()}

    def test_hook_sources_install_for_both_providers_and_repeat_without_duplicates(self):
        codex = self.hook_group()
        claude = self.hook_group("devforge delivery hook --provider claude")
        self.hook_source("codex", codex)
        self.hook_source("claude", claude, declared=True)
        self.install()
        before = self.snapshot()
        self.install()
        self.assertEqual(self.snapshot(), before)
        for provider, group in (("codex", codex), ("claude", claude)):
            self.assertEqual(json.loads(self.settings_path(provider).read_text()), {"hooks": {"Stop": [group]}})
            record = self.inventory()["managed_hooks"][provider]
            self.assertEqual(record["owned"][0]["definition"], group)
            canonical = json.dumps(group, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            self.assertEqual(record["owned"][0]["sha256"], hashlib.sha256(canonical).hexdigest())
            self.assertEqual(record["reused"], [])
            self.assertNotIn(record["path"], self.inventory()["files"])

    def test_hook_merge_preserves_settings_and_noncommand_user_groups(self):
        user = {"hooks": [{"type": "prompt", "prompt": "user check", "timeout": 12}]}
        unrelated = {"hooks": [{"type": "mcp_tool", "server": "existing", "tool": "inspect"}]}
        before = {"description": "user description", "permissions": {"allow": ["Read"]},
                  "hooks": {"Stop": [user], "SessionStart": [unrelated]}}
        self.settings(before)
        wanted = self.hook_group()
        self.hook_source(group=wanted)
        self.install()
        self.assertEqual(json.loads(self.settings_path().read_text()),
                         {**before, "hooks": {"Stop": [user, wanted], "SessionStart": [unrelated]}})

    def test_owned_group_updates_and_retires_without_losing_unrelated_group(self):
        user = self.hook_group("user-check")
        self.settings({"hooks": {"Stop": [user]}})
        source = self.hook_source()
        self.install()
        changed = self.hook_group("new-delivery-command")
        self.hook_source(group=changed)
        self.install()
        self.assertEqual(json.loads(self.settings_path().read_text())["hooks"]["Stop"], [user, changed])
        source.unlink()
        source.parent.rmdir()
        self.install()
        self.assertEqual(json.loads(self.settings_path().read_text())["hooks"]["Stop"], [user])
        self.assertEqual(self.inventory()["managed_hooks"]["codex"]["owned"], [])

    def test_identical_unowned_group_is_reused_and_never_removed_on_source_update(self):
        user = self.hook_group()
        self.settings({"description": "mine", "hooks": {"Stop": [user]}})
        self.hook_source(group=user)
        self.install()
        record = self.inventory()["managed_hooks"]["codex"]
        self.assertEqual(record["owned"], [])
        self.assertEqual(record["reused"][0]["definition"], user)
        changed = self.hook_group("changed-upstream")
        self.hook_source(group=changed)
        self.install()
        self.assertEqual(json.loads(self.settings_path().read_text())["hooks"]["Stop"], [user, changed])
        self.hook_source(group=user)
        self.install()
        self.assertEqual(json.loads(self.settings_path().read_text())["hooks"]["Stop"], [user])
        self.assertEqual(self.inventory()["managed_hooks"]["codex"]["owned"], [])

    def test_removed_edited_or_duplicated_owned_group_blocks_all_writes(self):
        self.hook_source()
        self.install()
        original = self.settings_path().read_bytes()
        group = self.hook_group()
        for groups in ([], [self.hook_group("local edit")], [group, group]):
            with self.subTest(groups=groups):
                self.settings({"hooks": {"Stop": groups}})
                before = self.snapshot()
                self.skill.write_text("upstream skill update")
                with self.assertRaises(ValueError):
                    self.install()
                self.assertEqual(self.snapshot(), before)
                self.settings_path().write_bytes(original)

    def test_owned_definition_digest_distinguishes_json_boolean_from_integer(self):
        group = self.hook_group()
        group["extra"] = 1
        self.hook_source(group=group)
        self.install()
        changed = {**group, "extra": True}
        self.settings({"hooks": {"Stop": [changed]}})
        with self.assertRaises(ValueError):
            self.install()

    def test_missing_settings_rebuilds_current_groups_only(self):
        self.settings({"description": "lost user setting", "hooks": {"Stop": [self.hook_group("user")]}})
        group = self.hook_group()
        self.hook_source(group=group)
        self.install()
        self.settings_path().unlink()
        self.install()
        self.assertEqual(json.loads(self.settings_path().read_text()), {"hooks": {"Stop": [group]}})

    def test_selected_provider_preserves_unselected_hook_inventory_and_settings(self):
        self.hook_source("codex")
        self.hook_source("claude", self.hook_group("claude"))
        self.install()
        claude_settings = self.settings_path("claude").read_bytes()
        claude_record = self.inventory()["managed_hooks"]["claude"]
        self.hook_source("codex", self.hook_group("new codex"))
        installer.install(self.framework, self.project, "codex")
        self.assertEqual(self.settings_path("claude").read_bytes(), claude_settings)
        self.assertEqual(self.inventory()["managed_hooks"]["claude"], claude_record)

    def test_malformed_or_duplicate_settings_preserve_all_existing_bytes(self):
        self.hook_source()
        path = self.settings({})
        for text in ('{', '[]', '{"hooks": [], "description": "user"}',
                     '{"hooks":{},"hooks":{}}', '{"hooks":{"Stop":{}}}',
                     '{"hooks":{"Stop":[{"hooks":[]}]}}', '{"bad":NaN}'):
            with self.subTest(text=text):
                path.write_text(text)
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.install()
                self.assertEqual(self.snapshot(), before)

    def test_symlink_settings_preserves_target_and_does_not_install_skills(self):
        self.hook_source()
        outside = self.root / "outside-settings.json"
        outside.write_text('{"hooks":{}}')
        self.settings_path().parent.mkdir()
        self.settings_path().symlink_to(outside)
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(outside.read_text(), '{"hooks":{}}')
        self.assertFalse((self.project / ".agents").exists())

    def test_skill_collision_does_not_update_hooks(self):
        self.hook_source()
        self.install()
        (self.project / ".agents/skills/demo/SKILL.md").write_text("local edit")
        self.skill.write_text("upstream edit")
        self.hook_source(group=self.hook_group("updated hook"))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(self.snapshot(), before)

    def test_parent_file_collision_preflights_before_other_writes(self):
        self.hook_source("claude")
        (self.project / ".claude").write_text("not a directory")
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(self.snapshot(), before)

    def test_hook_default_and_exact_declarations_export_runtime_only(self):
        source = self.hook_source()
        for relative in ("history/old.json", "evals/case.json", "__pycache__/hook.pyc"):
            path = source.parent / relative
            path.parent.mkdir(exist_ok=True)
            path.write_text("excluded")
        plugin = source.parent.parent
        manifest = plugin / ".codex-plugin/plugin.json"
        for index, declaration in enumerate((None, "hooks/hooks.json", "./hooks/hooks.json")):
            with self.subTest(declaration=declaration):
                document = {"name": "devforgeai"}
                if declaration is not None:
                    document["hooks"] = declaration
                manifest.write_text(json.dumps(document))
                self.assertEqual(installer.load_plugin_hooks(plugin, "codex")["hooks"]["Stop"], [self.hook_group()])
                output = self.root / f"export-{index}/devforgeai"
                installer.export_plugin(self.framework, "codex", output)
                self.assertEqual((output / "hooks/hooks.json").read_bytes(), source.read_bytes())
                self.assertEqual((output / ".codex-plugin/plugin.json").read_bytes(), manifest.read_bytes())
                self.assertFalse((output / "hooks/evals").exists())
                self.assertFalse((output / "hooks/history").exists())
                self.assertFalse((output / "hooks/__pycache__").exists())

    def test_unsupported_hook_declarations_fail_install_and_export_before_writes(self):
        source = self.hook_source()
        manifest = source.parent.parent / ".codex-plugin/plugin.json"
        for value in (None, [], {}, ["./hooks/hooks.json"], "../hooks/hooks.json", "./other.json", 1):
            with self.subTest(value=value):
                manifest.write_text(json.dumps({"name": "devforgeai", "hooks": value}))
                with self.assertRaises(ValueError):
                    self.install()
                output = self.root / "bad-export/devforgeai"
                with self.assertRaises(ValueError):
                    installer.export_plugin(self.framework, "codex", output)
                self.assertFalse(output.exists())
                self.assertEqual(list(self.project.iterdir()), [])

    def test_missing_malformed_duplicate_or_symlink_hook_source_is_rejected(self):
        source = self.hook_source(declared=True)
        valid = source.read_bytes()
        for text in ('[]', '{"hooks":[]}', '{"hooks":{},"hooks":{}}',
                     '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":""}]}]}}',
                     '{"hooks":{"Stop":[{"hooks":[{"type":"prompt","prompt":"x"}]}]}}'):
            with self.subTest(text=text):
                source.write_text(text)
                with self.assertRaises(ValueError):
                    self.install()
                with self.assertRaises(ValueError):
                    installer.export_plugin(self.framework, "codex", self.root / "bad/devforgeai")
                self.assertFalse((self.root / "bad").exists())
                self.assertEqual(list(self.project.iterdir()), [])
        source.unlink()
        with self.assertRaises(ValueError):
            self.install()
        outside = self.root / "source.json"
        outside.write_bytes(valid)
        source.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.install()

    def test_empty_default_hook_directory_and_duplicate_manifest_are_rejected(self):
        source = self.hook_source()
        source.unlink()
        with self.assertRaises(ValueError):
            self.install()
        self.hook_source()
        (source.parent.parent / ".codex-plugin/plugin.json").write_text(
            '{"name":"devforgeai","hooks":"hooks/hooks.json","hooks":"hooks/hooks.json"}')
        with self.assertRaises(ValueError):
            self.install()

    def delivery_requirement(self, provider="codex"):
        plugin = self.framework / f"providers/{provider}/plugins/devforgeai"
        path = plugin / "hooks/runtime-requirements.json"
        path.parent.mkdir(exist_ok=True)
        requirement = {"schema_version": "devforge.runtime-requirement/v1",
                       "runtime": "devforge.delivery", "protocol": "devforge.delivery-runtime/v1",
                       "provider": provider, "completion_mode": "managed-session",
                       "required_events": ["SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"]}
        path.write_text(json.dumps(requirement))
        command = f'"${{DEVFORGE_DELIVERY_EXECUTABLE:-devforge}}" delivery hook --provider {provider}'
        source = {"hooks": {event: [{"hooks": [{"type": "command", "command": command}]}]
                            for event in requirement["required_events"]}}
        (path.parent / "hooks.json").write_text(json.dumps(source))
        return path, requirement

    def capabilities(self):
        return {"schema_version": "devforge.delivery-capabilities/v1",
                "protocol": "devforge.delivery-runtime/v1", "supported_providers": ["codex", "claude"],
                "completion_modes": ["managed-session"], "io_modes": ["inherited", "interactive-tty"],
                "hook_events": ["SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"],
                "native_admission": "NOT_VALIDATED",
                "mechanical_scope": "phase evidence and persisted artifact verification; no semantic acceptance"}

    def extended_capabilities(self):
        """The eight base fields plus every declared extension the CLI reports."""
        return {**self.capabilities(),
                "utility_workflows": ["skill-builder", "skill-validator"],
                "utility_session_schema": "devforge.utility-session/v1",
                "utility_native_schedule_schema": "devforge.utility-native-schedule/v1",
                "native_execution_enabled": False,
                "native_process_interface": "EXPLICIT_FROZEN_CONFIGURATION_REQUIRED",
                "native_process_receipt_schema": "devforge.native-process-receipt/v1",
                "native_semantic_review": "SEPARATE_SELECTED_OPERATOR_OR_INDEPENDENT_REVIEW"}

    def explicit_runtime(self, text=None, prefix="", name="devforge-standin"):
        path = self.root / name
        text = json.dumps(self.capabilities()) if text is None else text
        path.write_text(f"#!{sys.executable}\nimport sys\n"
                        "assert sys.argv[1:] == ['delivery', 'capabilities']\n"
                        + prefix + f"print({text!r})\n")
        path.chmod(0o700)
        return path

    def execution_marker(self):
        """A stand-in that records the fact it ran, proving refusals precede execution."""
        marker = self.root / "probe-executed.marker"
        runtime = self.explicit_runtime(prefix=f"open({str(marker)!r}, 'w').write('executed')\n")
        return marker, runtime

    def validator_identity(self):
        report = json.loads(subprocess.run([str(self.validator), "install", "identity"],
                                           stdin=subprocess.DEVNULL, capture_output=True,
                                           check=True).stdout)
        return {"executable": report["executable"], "source_sha256": report["source_sha256"]}

    def install_delivery(self, runtime=None, provider="codex", validator=SELECTED):
        validator = self.validator if validator is SELECTED else validator
        return installer.install(self.framework, self.project, provider, runtime=runtime,
                                 validator=validator)

    def test_legacy_absence_never_probes_a_runtime(self):
        self.hook_source()
        with mock.patch.object(installer.runtime_requirements, "probe_runtime",
                               side_effect=AssertionError("legacy install must not probe")):
            self.install_delivery(runtime=Path("ignored-relative-runtime"))
        self.assertNotIn("runtime_evidence", self.inventory())

    def test_delivery_install_requires_explicit_runtime_even_with_path_and_environment(self):
        self.delivery_requirement()
        marker, runtime = self.execution_marker()
        # A discoverable `devforge` becomes neither the runtime nor the validation authority.
        discoverable = self.root / "devforge"
        discoverable.write_bytes(runtime.read_bytes())
        discoverable.chmod(0o700)
        with mock.patch.dict("os.environ", {"PATH": str(self.root),
                                            "DEVFORGE_BIN": str(discoverable),
                                            "DEVFORGE_DELIVERY_EXECUTABLE": str(discoverable)}):
            with self.assertRaisesRegex(ValueError, "requires --validator"):
                self.install_delivery(runtime=runtime, validator=None)
            with self.assertRaisesRegex(ValueError, "requires --runtime"):
                self.install_delivery()
        self.assertFalse(marker.exists())
        self.assertEqual(list(self.project.iterdir()), [])

    def test_delivery_validator_must_be_an_absolute_canonical_single_link_executable(self):
        self.delivery_requirement()
        marker, runtime = self.execution_marker()
        copy = self.root / "validator-copy"
        shutil.copy(self.validator, copy)
        copy.chmod(0o755)
        link = self.root / "validator-link"
        link.symlink_to(copy)
        alias = self.root / "validator-alias"
        alias.hardlink_to(copy)
        folder = self.root / "validator-folder"
        folder.mkdir()
        plain = self.root / "validator-not-executable"
        plain.write_bytes(b"#!/bin/sh\nexit 0\n")
        for candidate in (Path("devforge"), link, alias, folder, plain):
            with self.subTest(candidate=str(candidate)):
                with self.assertRaisesRegex(ValueError, "^--validator must "):
                    self.install_delivery(runtime=runtime, validator=candidate)
                self.assertFalse(marker.exists())
                self.assertEqual(list(self.project.iterdir()), [])
        with self.assertRaises((ValueError, OSError)):  # An absent selection cannot be read.
            self.install_delivery(runtime=runtime, validator=self.root / "absent")
        self.assertFalse(marker.exists())
        self.assertEqual(list(self.project.iterdir()), [])
        # The same single-link copy is a valid authority, proving only selection was refused.
        alias.unlink()
        self.install_delivery(runtime=runtime, provider="codex", validator=copy)
        self.assertTrue(marker.exists())

    def test_delivery_sidecar_rejects_malformed_duplicate_unknown_and_unsupported_values(self):
        path, requirement = self.delivery_requirement()
        cases = ['{', '[]', '{"runtime": "x", "runtime": "x"}',
                 json.dumps({**requirement, "extra": True}),
                 json.dumps({**requirement, "protocol": "devforge.delivery-runtime/v999"}),
                 json.dumps({**requirement, "provider": "claude"}),
                 json.dumps({**requirement, "required_events": "SessionStart"}),
                 json.dumps({**requirement, "completion_mode": True})]
        for raw in cases:
            with self.subTest(raw=raw):
                path.write_text(raw)
                with mock.patch.object(installer.runtime_requirements, "probe_runtime",
                                       side_effect=AssertionError("malformed dependency must precede probe")):
                    with self.assertRaises(ValueError):
                        self.install_delivery()
                    output = self.root / "bad-dependency/devforgeai"
                    with self.assertRaises(ValueError):
                        installer.export_plugin(self.framework, "codex", output)
                self.assertFalse(output.exists())
                self.assertEqual(list(self.project.iterdir()), [])

    def test_delivery_requires_complete_unique_synchronous_hook_selection(self):
        path, _ = self.delivery_requirement()
        source = path.parent / "hooks.json"
        valid = json.loads(source.read_text())
        cases = [{"hooks": {}}, {"hooks": {"Stop": []}}]
        for event in valid["hooks"]:
            missing = json.loads(json.dumps(valid))
            del missing["hooks"][event]
            cases.append(missing)
        for alteration in ("empty", "groups", "handlers", "provider", "matcher", "async", "timeout"):
            changed = json.loads(json.dumps(valid))
            group = changed["hooks"]["Stop"][0]
            if alteration == "empty":
                group["hooks"] = []
            elif alteration == "groups":
                changed["hooks"]["Stop"].append(group)
            elif alteration == "handlers":
                group["hooks"].append(group["hooks"][0])
            elif alteration == "provider":
                group["hooks"][0]["command"] = group["hooks"][0]["command"].replace("codex", "claude")
            elif alteration == "matcher":
                group["matcher"] = "sometimes"
            elif alteration == "async":
                group["hooks"][0]["async"] = True
            elif alteration == "timeout":
                group["hooks"][0]["timeout"] = True
            cases.append(changed)
        extra = json.loads(json.dumps(valid))
        extra["hooks"]["PreToolUse"] = extra["hooks"]["Stop"]
        cases.append(extra)
        for document in cases:
            with self.subTest(document=document):
                source.write_text(json.dumps(document))
                with mock.patch.object(installer.runtime_requirements, "probe_runtime",
                                       side_effect=AssertionError("invalid hooks must precede probe")):
                    with self.assertRaises(ValueError):
                        self.install_delivery()
                self.assertEqual(list(self.project.iterdir()), [])
        source.unlink()
        with self.assertRaisesRegex(ValueError, "missing framework hook component"):
            self.install_delivery()
        self.assertEqual(list(self.project.iterdir()), [])

    def test_delivery_runtime_must_be_absolute_canonical_regular_and_executable(self):
        self.delivery_requirement()
        runtime = self.explicit_runtime()
        link = self.root / "runtime-link"
        link.symlink_to(runtime)
        parent_link = self.root / "runtime-parent-link"
        parent_link.symlink_to(self.root, target_is_directory=True)
        folder = self.root / "folder"
        folder.mkdir()
        no_execute = self.root / "not-executable"
        no_execute.write_bytes(runtime.read_bytes())
        for path in (Path("devforge"), link, parent_link / runtime.name, folder,
                     no_execute, self.root / "folder/../devforge-standin", self.root / "missing"):
            with self.subTest(path=path):
                with self.assertRaises((ValueError, OSError)):
                    self.install_delivery(runtime=path)
                self.assertEqual(list(self.project.iterdir()), [])

    def test_delivery_incompatible_capabilities_block_all_installation_writes(self):
        self.delivery_requirement()
        valid = self.capabilities()
        cases = ["{", "[]", '{"schema_version":"x","schema_version":"x"}']
        for key, value in (("schema_version", "devforge.delivery-capabilities/v2"),
                           ("protocol", "other"), ("supported_providers", ["claude"]),
                           ("completion_modes", ["unmanaged"]), ("hook_events", ["Stop"]),
                           ("io_modes", "inherited"), ("native_admission", True),
                           ("mechanical_scope", None), ("mechanical_scope", "\ud800"),
                           ("extra", "unknown")):
            cases.append(json.dumps({**valid, key: value}))
        missing = dict(valid)
        del missing["native_admission"]
        cases.append(json.dumps(missing))
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    self.install_delivery(runtime=self.explicit_runtime(raw))
                self.assertEqual(list(self.project.iterdir()), [])
        # The extended contract is admitted only in full and only with well-formed values.
        extended = self.extended_capabilities()
        partial = dict(extended)
        del partial["native_semantic_review"]
        for raw, reason in (
                (json.dumps(partial), "unsupported runtime capabilities extension combination"),
                (json.dumps({**extended, "native_execution_enabled": "false"}),
                 "malformed runtime capabilities extension: native_execution_enabled"),
                (json.dumps({**extended, "utility_session_schema": "devforge.utility-session/v2"}),
                 "malformed runtime capabilities extension: utility_session_schema"),
                (json.dumps({**extended, "utility_workflows": []}),
                 "malformed runtime capabilities extension: utility_workflows")):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, reason):
                    self.install_delivery(runtime=self.explicit_runtime(raw))
                self.assertEqual(list(self.project.iterdir()), [])

    def test_delivery_compatible_explicit_runtime_records_exact_evidence_and_preserves_user_hooks(self):
        _, codex = self.delivery_requirement()
        _, claude = self.delivery_requirement("claude")
        user = self.hook_group("user-only")
        self.settings({"permissions": {"allow": ["Read"]}, "hooks": {"Stop": [user], "PreToolUse": [user]}})
        runtime = self.explicit_runtime()
        expected_hash = hashlib.sha256(runtime.read_bytes()).hexdigest()
        result = self.install_delivery(runtime=runtime, provider="both")
        evidence = self.inventory()["runtime_evidence"]
        for provider, requirement in (("codex", codex), ("claude", claude)):
            self.assertEqual(evidence[provider], {
                "schema_version": "devforge.runtime-probe/v1",
                "path": str(runtime), "sha256_before": expected_hash, "sha256_after": expected_hash,
                "capabilities": self.capabilities(), "contract": "base",
                "providers": ["codex", "claude"], "native_activation": "NOT_VERIFIED",
                "project": str(self.project.resolve()),
                "validator": self.validator_identity(), "requirement": requirement})
        self.assertEqual(result["runtime_compatibility"], "VERIFIED")
        self.assertEqual(result["native_activation"], "NOT_VERIFIED")
        settings = json.loads(self.settings_path().read_text())
        self.assertEqual(settings["hooks"]["PreToolUse"], [user])
        self.assertEqual(settings["hooks"]["Stop"][0], user)
        self.assertEqual(settings["permissions"], {"allow": ["Read"]})
        self.assertEqual(len(settings["hooks"]["Stop"]), 2)
        before = self.snapshot()
        self.install_delivery(runtime=runtime, provider="both")
        self.assertEqual(self.snapshot(), before)

    def test_delivery_binary_mutation_during_probe_blocks_all_installation_writes(self):
        self.delivery_requirement()
        runtime = self.explicit_runtime(prefix="with open(__file__, 'a') as stream: stream.write('# changed\\n')\n")
        with self.assertRaisesRegex(ValueError, "binary changed"):
            self.install_delivery(runtime=runtime)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_delivery_selected_runtime_cannot_be_overwritten_by_installation(self):
        self.delivery_requirement()
        standin = self.explicit_runtime()
        self.skill.write_bytes(standin.read_bytes())
        runtime = self.project / ".agents/skills/demo/SKILL.md"
        runtime.parent.mkdir(parents=True)
        runtime.write_bytes(standin.read_bytes())
        runtime.chmod(0o700)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "runtime binary overlaps"):
            self.install_delivery(runtime=runtime)
        self.assertEqual(self.snapshot(), before)

    def test_delivery_binary_mutation_after_probe_blocks_all_installation_writes(self):
        self.delivery_requirement()
        runtime = self.explicit_runtime()
        original = installer.plan_hook_merge

        def mutate_after_probe(*args, **kwargs):
            result = original(*args, **kwargs)
            runtime.write_bytes(runtime.read_bytes() + b"# changed during preflight\n")
            return result

        with mock.patch.object(installer, "plan_hook_merge", side_effect=mutate_after_probe):
            with self.assertRaisesRegex(ValueError, "binary changed before installation writes"):
                self.install_delivery(runtime=runtime)
        self.assertEqual(list(self.project.iterdir()), [])

    def validator_copy(self, name="validator-selected"):
        """A single-link copy of the compiled CLI, selectable as the validating authority."""
        copy = self.root / name
        shutil.copy(self.validator, copy)
        copy.chmod(0o755)
        return copy

    def test_delivery_validator_inside_the_project_is_refused_by_the_compiled_authority(self):
        # The compiled validator is told which project the installer is about to write.
        self.delivery_requirement()
        marker, runtime = self.execution_marker()
        inside = self.project / "tools/devforge"
        inside.parent.mkdir()
        shutil.copy(self.validator, inside)
        inside.chmod(0o755)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError,
                                    "validating executable must be outside the installation project"):
            self.install_delivery(runtime=runtime, validator=inside)
        self.assertFalse(marker.exists())  # The refusal precedes executing the runtime.
        self.assertEqual(self.snapshot(), before)

    def test_delivery_selected_validator_cannot_be_overwritten_by_installation(self):
        # Defense in depth behind the compiled refusal above: the installer refuses a
        # validator that is itself a planned destination, here a retired authoring file.
        self.delivery_requirement()
        runtime = self.explicit_runtime()
        relative = ".agents/skills/demo/evals/evals.json"
        destination = self.project / relative
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"#!/bin/sh\nexit 0\n")
        destination.chmod(0o755)
        (self.project / ".devforge-install.json").write_text(json.dumps(
            {"schema": 1, "files": {relative: installer.digest(destination.read_bytes())}}))
        real = installer.runtime_requirements.probe_runtime

        def bound_to_the_destination(_selected, probed, providers, *rest):
            # Probe with the outside authority, then report the destination as the validator.
            report = real(self.validator, probed, providers, *rest)
            executable = {"path": str(destination),
                          "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}
            return {**report, "validator": {**report["validator"], "executable": executable}}

        before = self.snapshot()
        with mock.patch.object(installer.runtime_requirements, "probe_runtime",
                               side_effect=bound_to_the_destination):
            with self.assertRaisesRegex(ValueError,
                                        "selected validator binary overlaps an installation destination"):
                self.install_delivery(runtime=runtime, validator=destination)
        self.assertEqual(self.snapshot(), before)

    def test_delivery_validator_aliased_by_a_destination_blocks_all_installation_writes(self):
        self.delivery_requirement()
        runtime = self.explicit_runtime()
        validator = self.validator_copy()
        digest = hashlib.sha256(validator.read_bytes()).hexdigest()
        record = self.project / ".devforge-install.json"
        original = installer.plan_hook_merge

        def alias_after_probe(*args, **kwargs):
            result = original(*args, **kwargs)
            if not record.exists():  # A managed destination now names the validator's inode.
                record.hardlink_to(validator)
            return result

        with mock.patch.object(installer, "plan_hook_merge", side_effect=alias_after_probe):
            with self.assertRaisesRegex(
                    ValueError, "installation would overwrite the selected validator binary through an alias"):
                self.install_delivery(runtime=runtime, validator=validator)
        self.assertEqual([p.name for p in self.project.iterdir()], [".devforge-install.json"])
        self.assertTrue(record.samefile(validator))
        self.assertEqual(hashlib.sha256(validator.read_bytes()).hexdigest(), digest)

    def test_delivery_validator_mutation_after_probe_blocks_all_installation_writes(self):
        self.delivery_requirement()
        runtime = self.explicit_runtime()
        validator = self.validator_copy()
        original = installer.plan_hook_merge

        def mutate_after_probe(*args, **kwargs):
            result = original(*args, **kwargs)
            with validator.open("ab") as stream:
                stream.write(b"# changed during preflight\n")
            return result

        with mock.patch.object(installer, "plan_hook_merge", side_effect=mutate_after_probe):
            with self.assertRaisesRegex(ValueError,
                                        "selected validator binary changed before installation writes"):
                self.install_delivery(runtime=runtime, validator=validator)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_delivery_runtime_hardlink_to_managed_destination_blocks_before_probe(self):
        # The stand-in records execution, so an absent marker proves nothing ran.
        marker, runtime = self.execution_marker()
        self.skill.write_bytes(runtime.read_bytes())
        self.install()
        destination = self.project / ".agents/skills/demo/SKILL.md"
        destination.chmod(0o700)
        runtime.unlink()
        runtime.hardlink_to(destination)
        self.assertTrue(runtime.samefile(destination))
        self.delivery_requirement()
        self.skill.write_bytes(self.skill.read_bytes() + b"# updated candidate\n")
        before = self.snapshot()
        runtime_before = runtime.read_bytes()
        with self.assertRaisesRegex(ValueError, "exactly one hard link"):
            self.install_delivery(runtime=runtime)
        self.assertFalse(marker.exists())  # Nothing was executed before the refusal.
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(runtime.read_bytes(), runtime_before)

    def test_delivery_capability_probe_limits_time_output_and_exit_status(self):
        self.delivery_requirement()
        prefixes = [("import time\ntime.sleep(10)\n", "timed out"),
                    ("sys.stdout.write('x' * (1024 * 1024 + 1))\n", "exceeds 1 MiB"),
                    ("sys.stderr.write('x' * (1024 * 1024 + 1))\n", "exceeds 1 MiB"),
                    ("sys.exit(7)\n", "status 7")]
        for prefix, error in prefixes:
            with self.subTest(error=error, prefix=prefix):
                # The five-second deadline is the compiled CLI's and is not adjustable.
                runtime = self.explicit_runtime(prefix=prefix)
                with self.assertRaisesRegex(ValueError, error):
                    self.install_delivery(runtime=runtime)
                self.assertEqual(list(self.project.iterdir()), [])

    def selected_runtime(self):
        """The compiled CLI itself, as a single-link copy the installer may select."""
        runtime = self.root / "devforge-runtime"
        shutil.copy(self.validator, runtime)
        runtime.chmod(0o755)
        return runtime

    def test_delivery_selected_validator_admits_the_real_extended_runtime_contract(self):
        _, requirement = self.delivery_requirement("claude")
        runtime = self.selected_runtime()
        reported = json.loads(subprocess.run([str(runtime), "delivery", "capabilities"],
                                             stdin=subprocess.DEVNULL, capture_output=True,
                                             check=True).stdout)
        self.assertEqual(len(reported), 15)  # Eight base fields plus the seven extensions.
        result = self.install_delivery(runtime=runtime, provider="claude")
        self.assertEqual(result["status"], "INSTALLED")
        self.assertEqual(result["runtime_compatibility"], "VERIFIED")
        self.assertEqual(result["native_activation"], "NOT_VERIFIED")
        self.assertEqual((self.project / ".claude/skills/demo/SKILL.md").read_text(), "claude skill")
        digest = hashlib.sha256(runtime.read_bytes()).hexdigest()
        self.assertEqual(self.inventory()["runtime_evidence"]["claude"], {
            "schema_version": "devforge.runtime-probe/v1", "path": str(runtime),
            "sha256_before": digest, "sha256_after": digest, "capabilities": reported,
            "contract": "extended", "providers": ["claude"], "native_activation": "NOT_VERIFIED",
            "project": str(self.project.resolve()),
            "validator": self.validator_identity(), "requirement": requirement})

    def test_delivery_validator_refusal_blocks_every_installation_write(self):
        self.delivery_requirement("claude")
        malformed = self.explicit_runtime(json.dumps({**self.extended_capabilities(),
                                                      "native_process_interface": "  "}))
        with self.assertRaisesRegex(ValueError,
                                    "malformed runtime capabilities extension: native_process_interface"):
            self.install_delivery(runtime=malformed, provider="claude")
        self.assertEqual(list(self.project.iterdir()), [])

    def test_delivery_export_retains_dependency_without_executing_runtime(self):
        path, requirement = self.delivery_requirement()
        output = self.root / "delivery-export/devforgeai"
        with mock.patch.object(installer.runtime_requirements, "probe_runtime",
                               side_effect=AssertionError("export must not probe")):
            result = installer.export_plugin(self.framework, "codex", output)
        self.assertEqual((output / "hooks/runtime-requirements.json").read_bytes(), path.read_bytes())
        self.assertEqual(result["runtime_requirements"], {"codex": requirement})
        self.assertEqual(result["runtime_host"], "NOT_VERIFIED")
        self.assertEqual(result["files_sha256"]["hooks/runtime-requirements.json"],
                         hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(result["behavior"], "NOT_EVALUATED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
