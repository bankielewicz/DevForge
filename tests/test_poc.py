"""Black-box acceptance cases. Expected outcomes do not call gate internals."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

BIN = Path(os.environ.get("DEVFORGE_BIN", Path(__file__).parents[1] / "target/debug/devforge")).resolve()
BASE = "def save_and_list(name):\n    return []\n"
GOOD = '''import sqlite3
def save_and_list(name):
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE notes (name TEXT NOT NULL)")
        db.execute("INSERT INTO notes VALUES (?)", (name,))
        return [row[0] for row in db.execute("SELECT name FROM notes")]
'''
TEST = '''import unittest
from src.store import save_and_list
class PersistenceTest(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(save_and_list("hello"), ["hello"])
'''


class GatePOC(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devforge-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "candidate"
        self.state = self.root / "authority/state"
        self.policy = self.root / "authority/policy.json"
        self.project.mkdir()
        self.policy.parent.mkdir()
        self.put("docs/idea.md", "IDEA-001: Keep a personal list of notes.\n")
        self.put("docs/story.md", "STORY-001: Persist and retrieve a note. AC-001: round trip.\n")
        self.put("dependencies.json", json.dumps({"persistence": "sqlite3"}))
        self.put("src/store.py", BASE)
        self.put("src/__init__.py", "")
        self.put("tests/__init__.py", "")
        self.put("experts/persistence/SKILL.md", "---\nname: persistence\ndescription: Implement note persistence using the approved SQLite contract.\n---\n\nUse parameterized SQLite statements and check the note round trip.\n")
        self.rules = {
            "schema": 1, "project_id": "notes-sqlite", "goal": "Persist notes",
            "story_id": "STORY-001", "upstream": ["docs/idea.md", "docs/story.md"],
            "dependencies": {"persistence": "sqlite3"},
            "dependency_file": "dependencies.json", "source_roots": ["src"],
            "test_root": "tests", "expert_dirs": ["experts/persistence"],
            "forbidden_tokens": ["Microsoft.EntityFrameworkCore", "sqlalchemy"],
        }
        self.write_policy()
        self.call("expert", "bind", "--expert", "experts/persistence")

    def put(self, name, content):
        p = self.project / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def write_policy(self):
        self.policy.write_text(json.dumps(self.rules))

    def call(self, *args, ok=True):
        proc = subprocess.run([str(BIN), *args, "--project", str(self.project), "--policy", str(self.policy),
                               "--state", str(self.state)], text=True, capture_output=True, timeout=30)
        if ok:
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        else:
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def red(self):
        self.call("init")
        self.put("tests/test_store.py", TEST)
        self.call("red")

    def green(self):
        self.red()
        self.put("src/store.py", GOOD)
        self.call("green")

    def test_complete_red_green_accept_verify(self):
        self.green()
        self.assertEqual(self.call("accept")["status"], "ACCEPTED")
        self.assertEqual(self.call("verify")["status"], "VERIFIED")
        self.assertTrue((self.state / "accepted/src/store.py").exists())

    def test_green_without_red_is_denied(self):
        self.call("init")
        self.put("src/store.py", GOOD)
        self.assertEqual(self.call("green", ok=False)["status"], "BLOCKED")

    def test_red_rejects_production_changes(self):
        self.call("init")
        self.put("src/store.py", GOOD)
        self.put("tests/test_store.py", TEST)
        self.call("red", ok=False)

    def test_runner_error_is_not_valid_red(self):
        self.call("init")
        self.put("tests/test_store.py", "this is not python !!!\n")
        self.assertEqual(self.call("red", ok=False)["status"], "COULD_NOT_RUN")
        self.assertEqual(self.call("status")["phase"], "initialized")

    def test_no_tests_is_not_valid_red(self):
        self.call("init")
        self.call("red", ok=False)

    def test_skipped_tests_are_not_valid_red(self):
        self.call("init")
        self.put("tests/test_store.py", TEST.replace("    def test_round_trip", "    @unittest.skip('bypass')\n    def test_round_trip"))
        self.call("red", ok=False)

    def test_changed_tests_invalidate_red(self):
        self.red()
        self.put("src/store.py", GOOD)
        self.put("tests/test_store.py", TEST + "\n# changed since RED\n")
        self.call("green", ok=False)

    def test_dependency_change_is_denied(self):
        self.put("dependencies.json", json.dumps({"persistence": "sqlite3", "orm": "Microsoft.EntityFrameworkCore"}))
        self.call("check", ok=False)

    def test_prohibited_import_is_denied(self):
        self.put("src/other.py", "import sqlalchemy\n")
        self.call("check", ok=False)

    def test_source_outside_layout_is_denied(self):
        self.put("misplaced.py", "pass\n")
        self.call("check", ok=False)

    def test_pinned_runtime_tool_is_allowed_but_not_mutable_source(self):
        relative = ".claude/skills/demo/scripts/check.py"
        self.put(relative, "print('validated')\n")
        tool = self.project / relative
        self.rules["tooling_files"] = {relative: {
            "sha256": hashlib.sha256(tool.read_bytes()).hexdigest(),
            "mode": tool.stat().st_mode & 0o777,
        }}
        self.write_policy()
        self.call("expert", "bind", "--expert", "experts/persistence")
        self.call("check")
        self.red()
        self.put("src/store.py", GOOD)
        self.put(relative, "print('changed')\n")
        self.call("green", ok=False)

    def test_runtime_tool_requires_exact_bytes_mode_and_declared_path(self):
        relative = ".agents/skills/demo/scripts/check.py"
        self.put(relative, "pass\n")
        tool = self.project / relative
        original_mode = tool.stat().st_mode & 0o777
        self.rules["tooling_files"] = {relative: {
            "sha256": hashlib.sha256(tool.read_bytes()).hexdigest(), "mode": original_mode,
        }}
        self.write_policy()
        self.call("expert", "bind", "--expert", "experts/persistence")
        self.call("check")
        tool.chmod(original_mode ^ 0o100)
        self.call("check", ok=False)
        tool.chmod(original_mode)
        self.put(".agents/skills/demo/scripts/extra.py", "pass\n")
        self.call("check", ok=False)

    def test_runtime_tool_cannot_overlap_editable_source(self):
        self.rules["tooling_files"] = {"src/store.py": {
            "sha256": hashlib.sha256(BASE.encode()).hexdigest(), "mode": 0o644,
        }}
        self.write_policy()
        self.call("check", ok=False)

    def test_upstream_change_stales_expert(self):
        self.put("docs/story.md", "STORY-001: Changed acceptance criterion.\n")
        result = self.call("expert", "status")
        self.assertEqual(result["experts"][0]["status"], "STALE")
        self.call("check", ok=False)

    def test_refresh_preserves_old_binding(self):
        old = (self.project / "experts/persistence/provenance.json").read_bytes()
        self.put("docs/story.md", "STORY-001: New approved scope in this fixture.\n")
        self.call("expert", "bind", "--expert", "experts/persistence")
        self.call("check")
        versions = list((self.project / "experts/persistence/history").glob("*.json"))
        self.assertTrue(any(p.read_bytes() == old for p in versions))

    def test_skill_edit_stales_binding(self):
        p = self.project / "experts/persistence/SKILL.md"
        p.write_text(p.read_text() + "\nChanged procedure.\n")
        self.call("check", ok=False)

    def test_policy_change_stales_existing_run(self):
        self.red()
        self.rules["goal"] = "Changed goal"
        self.write_policy()
        self.call("expert", "bind", "--expert", "experts/persistence")
        self.call("green", ok=False)

    def test_mutation_after_green_blocks_accept(self):
        self.green()
        self.put("src/store.py", GOOD + "\n# untested\n")
        self.call("accept", ok=False)

    def test_accepted_archive_tampering_detected(self):
        self.green()
        self.call("accept")
        (self.state / "accepted/src/store.py").write_text("tampered")
        self.call("verify", ok=False)

    def test_state_inside_project_is_denied(self):
        self.state = self.project / "state"
        self.call("init", ok=False)

    def test_policy_inside_project_is_denied(self):
        self.policy = self.project / "policy.json"
        self.write_policy()
        self.call("init", ok=False)

    def test_symlink_is_denied(self):
        (self.project / "src/link.py").symlink_to(self.policy)
        self.call("check", ok=False)

    def test_undeclared_policy_field_is_denied(self):
        self.rules["skip_gate"] = True
        self.write_policy()
        self.call("check", ok=False)

    def test_path_traversal_policy_is_denied(self):
        self.rules["source_roots"] = ["../authority"]
        self.write_policy()
        self.call("check", ok=False)

    def test_external_lock_prevents_transition(self):
        self.call("init")
        (self.state / ".lock").write_text("another validator")
        self.put("tests/test_store.py", TEST)
        self.call("red", ok=False)

    def test_another_project_binding_is_not_reused(self):
        self.rules["project_id"] = "other-project"
        self.write_policy()
        self.call("check", ok=False)

    def test_mode_change_after_green_blocks_accept(self):
        self.green()
        (self.project / "src/store.py").chmod(0o755)
        self.call("accept", ok=False)

    def test_cli_accepts_relative_project_paths(self):
        self.project = Path(os.path.relpath(self.project))
        self.call("check")

    def test_preexisting_failing_suite_blocks_init(self):
        self.put("tests/test_store.py", TEST)
        self.call("init", ok=False)

    def test_isolation_allows_candidate_but_denies_authority(self):
        # Use a disposable authority file, never try overwriting the actual binary.
        protected = self.policy.parent / "validator-canary"
        protected.write_text("protected")
        code = '''from pathlib import Path
import errno
import sys
Path("candidate-write").write_text("allowed")
for name in sys.argv[1:]:
    try:
        Path(name).write_text("unauthorized")
    except OSError as error:
        assert error.errno in (errno.EROFS, errno.EACCES, errno.ENOENT), error
    else:
        raise AssertionError("authority was writable")
'''
        proc = subprocess.run([str(BIN), "isolate", "--project", str(self.project), "--",
                               "/usr/bin/python3", "-c", code, str(self.policy), str(protected)],
                              capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(protected.read_text(), "protected")
        self.assertEqual((self.project / "candidate-write").read_text(), "allowed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
