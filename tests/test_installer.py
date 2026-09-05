import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("installer", Path(__file__).parents[1] / "scripts/install_framework.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
