import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class StructuralGateTest(unittest.TestCase):
    def test_optimized_python_cannot_disable_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for provider in ("codex", "claude"):
                for name in ("devforge-brainstorm", "devforge-project-expert-creator", "devforge-develop", "devforge-review"):
                    skill = root / f"providers/{provider}/plugins/devforgeai/skills/{name}/SKILL.md"
                    skill.parent.mkdir(parents=True)
                    skill.write_text(f"---\nname: {name}\n---\nMissing required description.\n")
                manifest = root / f"providers/{provider}/plugins/devforgeai/.{provider}-plugin/plugin.json"
                manifest.parent.mkdir()
                manifest.write_text(json.dumps({"name": "devforgeai"}))
            runner = Path(__file__).parents[1] / "scripts/validate_framework.py"
            result = subprocess.run(["python3", "-O", str(runner), "--framework", str(root)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
