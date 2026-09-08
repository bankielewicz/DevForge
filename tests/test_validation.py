import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location("validator", Path(__file__).parents[1] / "scripts/validate_framework.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class FrameworkTraversalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "framework"
        for provider in ("codex", "claude"):
            plugin = self.root / f"providers/{provider}/plugins/devforgeai"
            for name in ("devforge-brainstorm", "devforge-project-expert-creator", "devforge-develop", "devforge-review"):
                skill = plugin / "skills" / name / "SKILL.md"
                skill.parent.mkdir(parents=True)
                skill.write_text(f"---\nname: {name}\ndescription: Synthetic test skill.\n---\n")
            manifest = plugin / f".{provider}-plugin/plugin.json"
            manifest.parent.mkdir()
            manifest.write_text(json.dumps({"name": "devforgeai"}))
        self.expected = {"status": "PASS", "skills": 8, "scope": "structure only", "behavior": "NOT_EVALUATED"}

    def test_source_only_passes(self):
        self.assertEqual(validator.validate(self.root), self.expected)

    def test_retained_entrypoint_keeps_original_name_in_evidence_container(self):
        archive = self.root / "docs/skill-authoring/history/previous-builder-revision-2026-09-07/SKILL.md"
        archive.parent.mkdir(parents=True)
        original = "---\nname: skill-builder\ndescription: Preserved original entrypoint.\n---\n"
        archive.write_text(original)
        self.assertEqual(validator.validate(self.root), self.expected)
        self.assertEqual(archive.read_text(), original)

    def test_retained_entrypoint_still_requires_valid_name_and_description(self):
        archive = self.root / "docs/skill-authoring/history/previous-builder-revision-2026-09-07/SKILL.md"
        archive.parent.mkdir(parents=True)
        for content in ("---\nname: INVALID_NAME\ndescription: Original.\n---\n",
                        "---\nname: skill-builder\n---\n"):
            with self.subTest(content=content):
                archive.write_text(content)
                with self.assertRaisesRegex(ValueError, "previous-builder-revision-2026-09-07/SKILL.md"):
                    validator.validate(self.root)

    def test_named_snapshot_entrypoints_preserve_original_metadata(self):
        for container in ("history/previous-revision-20260907T010203Z", "integration-20260907T145039Z"):
            for snapshot in ("source-before", "source-after", "installed-before",
                             "installed-after", "installed-builder-before", "installed-builder-before-02"):
                relative = f"docs/skill-authoring/{container}/{snapshot}/SKILL.md"
                with self.subTest(relative=relative):
                    path = self.root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    original = "---\nname: skill-builder\ndescription: Preserved original.\n---\n"
                    path.write_text(original)
                    try:
                        self.assertEqual(validator.validate(self.root), self.expected)
                        self.assertEqual(path.read_text(), original)
                    finally:
                        path.unlink()

    def test_snapshot_name_exception_is_bounded_and_metadata_still_checked(self):
        invalid = (
            ("docs/skill-authoring/history/revision-2026-09-07/source-before/SKILL.md", "---\nname: INVALID\ndescription: Old.\n---\n"),
            ("docs/skill-authoring/history/revision-2026-09-07/source-before/SKILL.md", "---\nname: skill-builder\n---\n"),
        )
        original = "---\nname: skill-builder\ndescription: Preserved original.\n---\n"
        invalid += tuple((relative, original) for relative in (
            "docs/skill-authoring/history/revision-2026-09-07/source-before/deeper/SKILL.md",
            "docs/skill-authoring/history/undated/SKILL.md",
            "docs/skill-authoring/history/revision-2026-09-07/source-before-lookalike/SKILL.md",
            "docs/skill-authoring/undated-container/installed-before/SKILL.md",
            "docs/skill-authoring/history-lookalike/revision/source-before/SKILL.md",
            "authored/docs/skill-authoring/history/revision/source-before/SKILL.md",
            "providers/codex/plugins/devforgeai/skills/source-before/SKILL.md",
        ))
        for relative, content in invalid:
            with self.subTest(relative=relative, content=content):
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                try:
                    with self.assertRaisesRegex(ValueError, "SKILL.md"):
                        validator.validate(self.root)
                finally:
                    path.unlink()

    def test_archive_does_not_exempt_deeper_or_lookalike_skill_directories(self):
        for relative in ("docs/skill-authoring/history/retained/authored/SKILL.md",
                         "docs/skill-authoring/history-lookalike/retained/SKILL.md",
                         "authored/docs/skill-authoring/history/retained/SKILL.md",
                         "providers/codex/plugins/devforgeai/skills/wrong-name/SKILL.md"):
            with self.subTest(relative=relative):
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("---\nname: skill-builder\ndescription: Synthetic mismatched package.\n---\n")
                try:
                    with self.assertRaisesRegex(ValueError, "SKILL.md"):
                        validator.validate(self.root)
                finally:
                    path.unlink()

    def test_retained_tree_json_and_python_are_still_inspected(self):
        for filename, content, exception in (("bad.json", "{malformed", json.JSONDecodeError),
                                             ("bad.py", "def broken(:\n", SyntaxError)):
            with self.subTest(filename=filename):
                path = self.root / "docs/skill-authoring/history/retained/references" / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                try:
                    with self.assertRaises(exception):
                        validator.validate(self.root)
                finally:
                    path.unlink()

    def test_frozen_runtime_review_workflows_are_inert_evidence(self):
        relative = "docs/skill-authoring/integration-20260907T145039Z/runtime-review-01/frozen-source/.github/workflows/ci.yml"
        path = self.root / relative
        path.parent.mkdir(parents=True)
        original = "name: Preserved runtime workflow\non: push\n"
        path.write_text(original)
        self.assertEqual(validator.validate(self.root), self.expected)
        self.assertEqual(path.read_text(), original)

    def test_workflow_ownership_is_still_enforced_outside_frozen_runtime_review(self):
        for relative in (
            ".github/workflows/ci.yml",
            "providers/codex/.github/workflows/ci.yml",
            "docs/skill-authoring/undated/runtime-review-01/frozen-source/.github/workflows/ci.yml",
            "docs/skill-authoring/integration-20260907T145039Z/authored/.github/workflows/ci.yml",
            "docs/skill-authoring/integration-20260907T145039Z/runtime-review-lookalike/frozen-source/.github/workflows/ci.yml",
        ):
            with self.subTest(relative=relative):
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("name: Synthetic workflow\n")
                try:
                    with self.assertRaisesRegex(ValueError, "GitHub workflows belong in DevForge"):
                        validator.validate(self.root)
                finally:
                    path.unlink()

    def test_retained_entrypoint_symlink_is_rejected(self):
        archive = self.root / "docs/skill-authoring/history/retained/SKILL.md"
        archive.parent.mkdir(parents=True)
        target = Path(self.temp.name) / "original-SKILL.md"
        target.write_text("---\nname: skill-builder\ndescription: Synthetic original.\n---\n")
        archive.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink not accepted"):
            validator.validate(self.root)

    def test_root_runtime_is_pruned_before_enumeration(self):
        runtime = self.root / ".devforge-runtime"
        launcher = runtime / "codex/home/tmp/arg0/synthetic/codex-execve-wrapper"
        launcher.parent.mkdir(parents=True)
        target = Path(self.temp.name) / "benign-target"
        target.write_text("synthetic launcher target; never executed\n")
        launcher.symlink_to(target)
        (runtime / "non-source.json").write_text("{malformed runtime JSON")
        authored = self.root / "authored/nested"
        authored.mkdir(parents=True)
        (authored / "valid.json").write_text("{}")
        visited = []

        def observe(original):
            def enumerate_directory(path):
                directory = Path(path)
                visited.append(directory)
                self.assertFalse(directory == runtime or runtime in directory.parents,
                                 f"enumerated private runtime: {directory}")
                return original(path)
            return enumerate_directory

        # Observe filesystem enumeration, including both pathlib and os traversal.
        with mock.patch("os.scandir", side_effect=observe(os.scandir)), \
                mock.patch("os.listdir", side_effect=observe(os.listdir)):
            self.assertEqual(validator.validate(self.root), self.expected)
        self.assertIn(self.root, visited)
        self.assertIn(authored, visited)

    def test_root_runtime_symlinks_are_rejected(self):
        directory = Path(self.temp.name) / "target-directory"
        directory.mkdir()
        file = Path(self.temp.name) / "target-file"
        file.write_text("synthetic target\n")
        link = self.root / ".devforge-runtime"
        for target in (directory, file, Path(self.temp.name) / "missing-target"):
            with self.subTest(target=target.name):
                link.symlink_to(target)
                try:
                    with self.assertRaisesRegex(ValueError, r"symlink not accepted: \.devforge-runtime$"):
                        validator.validate(self.root)
                finally:
                    link.unlink()

    def test_authored_symlinks_are_rejected_including_nested_runtime(self):
        target_file = Path(self.temp.name) / "target-file"
        target_file.write_text("synthetic target\n")
        target_directory = Path(self.temp.name) / "target-directory"
        target_directory.mkdir()
        for relative, target in (("authored/link", target_file),
                                 ("authored/directory-link", target_directory),
                                 ("authored/.devforge-runtime/link", target_file)):
            with self.subTest(relative=relative):
                link = self.root / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(target)
                try:
                    with self.assertRaisesRegex(ValueError, "symlink not accepted:") as raised:
                        validator.validate(self.root)
                    self.assertEqual(str(raised.exception), f"symlink not accepted: {relative}")
                finally:
                    link.unlink()

    def test_authored_json_is_checked_including_nested_runtime(self):
        for relative in ("authored/invalid.json", "authored/.devforge-runtime/invalid.json"):
            with self.subTest(relative=relative):
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{malformed authored JSON")
                try:
                    with self.assertRaises(json.JSONDecodeError):
                        validator.validate(self.root)
                finally:
                    path.unlink()

    def test_existing_exclusions_retain_root_and_nested_scope(self):
        for prefix in ("", "authored/"):
            for name in (".git", ".poc", "__pycache__"):
                ignored = self.root / prefix / name
                ignored.mkdir(parents=True)
                (ignored / "invalid.json").write_text("{malformed excluded JSON")
                (ignored / "link").symlink_to(Path(self.temp.name) / "missing-target")
        self.assertEqual(validator.validate(self.root), self.expected)

    def test_existing_excluded_symlink_entries_remain_excluded(self):
        for prefix in ("", "authored/"):
            for name in (".git", ".poc", "__pycache__"):
                link = self.root / prefix / name
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(Path(self.temp.name) / "missing-target")
        self.assertEqual(validator.validate(self.root), self.expected)

    def test_required_structure_checks_remain_active(self):
        plugin = "providers/codex/plugins/devforgeai"
        cases = (
            (f"{plugin}/skills/devforge-review/SKILL.md", None, ValueError, "codex core skill missing"),
            (f"{plugin}/.codex-plugin/plugin.json", '{"name": "wrong"}', ValueError, "plugin.json"),
            (f"{plugin}/.codex-plugin/plugin.json", None, FileNotFoundError, "plugin.json"),
            ("plugins/devforgeai/retired.txt", "retired", ValueError, "retired shared plugin"),
            (f"{plugin}/skills/devforge-review/evals/evals.json",
             '{"skill_name": "wrong", "evals": []}', ValueError, "wrong eval skill"),
            (f"{plugin}/skills/devforge-review/evals/evals.json",
             '{"skill_name": "devforge-review", "evals": [{"files": ["missing.txt"]}]}',
             ValueError, "missing fixture"),
        )
        for index, (relative, content, error, message) in enumerate(cases):
            with self.subTest(relative=relative, message=message):
                root = Path(self.temp.name) / f"invalid-framework-{index}"
                shutil.copytree(self.root, root)
                path = root / relative
                if content is None:
                    path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content)
                with self.assertRaisesRegex(error, message):
                    validator.validate(root)


class RuntimeRequirementsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "framework"
        for provider in ("codex", "claude"):
            plugin = self.plugin(provider)
            for name in ("devforge-brainstorm", "devforge-project-expert-creator", "devforge-develop", "devforge-review"):
                skill = plugin / "skills" / name / "SKILL.md"
                skill.parent.mkdir(parents=True)
                skill.write_text(f"---\nname: {name}\ndescription: Synthetic test skill.\n---\n")
            manifest = plugin / f".{provider}-plugin/plugin.json"
            manifest.parent.mkdir()
            manifest.write_text(json.dumps({"name": "devforgeai"}))
        self.expected = {"status": "PASS", "skills": 8, "scope": "structure only", "behavior": "NOT_EVALUATED"}

    def plugin(self, provider="codex"):
        return self.root / f"providers/{provider}/plugins/devforgeai"

    def requirement(self, provider="codex"):
        return {
            "schema_version": "devforge.runtime-requirement/v1",
            "runtime": "devforge.delivery",
            "protocol": "devforge.delivery-runtime/v1",
            "provider": provider,
            "completion_mode": "managed-session",
            "required_events": ["SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"],
        }

    def hooks(self, provider="codex"):
        command = '"${DEVFORGE_DELIVERY_EXECUTABLE:-devforge}" delivery hook --provider ' + provider
        return {"hooks": {
            event: [{"hooks": [{"type": "command", "command": command}]}]
            for event in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd")
        }}

    def write_delivery(self, provider="codex"):
        directory = self.plugin(provider) / "hooks"
        directory.mkdir(exist_ok=True)
        (directory / "runtime-requirements.json").write_text(json.dumps(self.requirement(provider)))
        (directory / "hooks.json").write_text(json.dumps(self.hooks(provider)))
        return directory

    def validate(self):
        # Structural checks must not launch a host, including for invalid input.
        with mock.patch("subprocess.Popen", side_effect=AssertionError("runtime execution is forbidden")), \
                mock.patch("subprocess.run", side_effect=AssertionError("runtime execution is forbidden")):
            return validator.validate(self.root)

    def test_legacy_result_omits_runtime_claims(self):
        # Existing validation only parses hook JSON when no runtime sidecar exists.
        directory = self.plugin() / "hooks"
        directory.mkdir()
        (directory / "hooks.json").write_text('{"hooks": []}')
        self.assertEqual(self.validate(), self.expected)

    def test_valid_sidecars_report_only_declared_providers_without_executing_host(self):
        for providers in (("codex",), ("claude",), ("codex", "claude")):
            with self.subTest(providers=providers):
                for provider in ("codex", "claude"):
                    directory = self.plugin(provider) / "hooks"
                    if directory.exists():
                        shutil.rmtree(directory)
                for provider in providers:
                    self.write_delivery(provider)
                with mock.patch.dict(os.environ, {"DEVFORGE_DELIVERY_EXECUTABLE": "/missing/synthetic-devforge"}):
                    self.assertEqual(self.validate(), {
                        **self.expected,
                        "runtime_requirements": {provider: self.requirement(provider) for provider in providers},
                        "runtime_host": "NOT_VERIFIED",
                    })

    def test_invalid_json_and_duplicate_requirement_keys_are_rejected(self):
        directory = self.write_delivery()
        valid = json.dumps(self.requirement())
        cases = (
            "{",
            '{"schema_version":"devforge.runtime-requirement/v1",' + valid[1:],
            valid[:-1] + ',"protocol":"devforge.delivery-runtime/v1"}',
            valid.replace('"managed-session"', "NaN"),
        )
        for document in cases:
            with self.subTest(document=document):
                (directory / "runtime-requirements.json").write_text(document)
                with self.assertRaises(ValueError):
                    self.validate()

    def test_requirement_must_be_an_object_with_exact_keys(self):
        directory = self.write_delivery()
        cases = [None, [], "requirement", 1, True, {}]
        cases.append({**self.requirement(), "unknown": "unsupported"})
        for key in self.requirement():
            document = self.requirement()
            del document[key]
            cases.append(document)
        for document in cases:
            with self.subTest(document=document):
                (directory / "runtime-requirements.json").write_text(json.dumps(document))
                with self.assertRaises(ValueError):
                    self.validate()

    def test_requirement_literals_and_scalar_types_are_strict(self):
        directory = self.write_delivery()
        unsupported = {
            "schema_version": "devforge.runtime-requirement/v2",
            "runtime": "other.runtime",
            "protocol": "devforge.delivery-runtime/v2",
            "provider": "unsupported",
            "completion_mode": "unmanaged-session",
        }
        for key, value in unsupported.items():
            for invalid in (value, None, True, 1, [], {}, ""):
                with self.subTest(key=key, value=invalid):
                    document = {**self.requirement(), key: invalid}
                    (directory / "runtime-requirements.json").write_text(json.dumps(document))
                    with self.assertRaises(ValueError):
                        self.validate()

    def test_requirement_provider_must_match_its_plugin(self):
        for provider, wrong in (("codex", "claude"), ("claude", "codex")):
            with self.subTest(provider=provider):
                directory = self.write_delivery(provider)
                document = {**self.requirement(provider), "provider": wrong}
                (directory / "runtime-requirements.json").write_text(json.dumps(document))
                with self.assertRaises(ValueError):
                    self.validate()
                (directory / "runtime-requirements.json").write_text(json.dumps(self.requirement(provider)))

    def test_required_events_are_exact_ordered_literals(self):
        directory = self.write_delivery()
        events = self.requirement()["required_events"]
        invalid_events = (None, True, 4, "Stop", {}, [], events[:-1],
                          list(reversed(events)), events + ["Stop"], events + ["Interrupt"],
                          ["SessionStart", "UserPromptSubmit", "stop", "SessionEnd"],
                          ["SessionStart", "UserPromptSubmit", 1, "SessionEnd"])
        for invalid in invalid_events:
            with self.subTest(events=invalid):
                document = {**self.requirement(), "required_events": invalid}
                (directory / "runtime-requirements.json").write_text(json.dumps(document))
                with self.assertRaises(ValueError):
                    self.validate()

    def test_runtime_requirement_needs_a_hook_source(self):
        directory = self.write_delivery()
        (directory / "hooks.json").unlink()
        with self.assertRaises(ValueError):
            self.validate()

    def test_hook_source_requires_each_event_once_and_no_extra_events(self):
        directory = self.write_delivery()
        cases = ({"hooks": {}}, {"hooks": []}, {})
        documents = list(cases)
        for event in self.requirement()["required_events"]:
            for replacement in (None, [], {}, "command"):
                document = self.hooks()
                if replacement is None:
                    del document["hooks"][event]
                else:
                    document["hooks"][event] = replacement
                documents.append(document)
            document = self.hooks()
            document["hooks"][event] *= 2
            documents.append(document)
        document = self.hooks()
        document["hooks"]["Interrupt"] = document["hooks"]["Stop"]
        documents.append(document)
        for document in documents:
            with self.subTest(document=document):
                (directory / "hooks.json").write_text(json.dumps(document))
                with self.assertRaises(ValueError):
                    self.validate()

    def test_hook_source_requires_one_correct_command_handler_per_event(self):
        directory = self.write_delivery()
        wrong_commands = (
            "", None, 1,
            '"${DEVFORGE_DELIVERY_EXECUTABLE:-devforge}" delivery hook --provider claude',
            "${DEVFORGE_DELIVERY_EXECUTABLE:-devforge} delivery hook --provider codex",
            "devforge delivery hook --provider codex",
            '"${DEVFORGE_DELIVERY_EXECUTABLE:-devforge}" delivery status --provider codex',
        )
        for event in self.requirement()["required_events"]:
            valid = self.hooks()["hooks"][event][0]["hooks"][0]
            handlers = ([], [valid, valid], [{"type": "prompt", "prompt": "continue"}],
                        [{"command": valid["command"]}], [{"type": "command"}], [None])
            handlers += tuple([{**valid, "command": command}] for command in wrong_commands)
            for invalid in handlers:
                with self.subTest(event=event, handlers=invalid):
                    document = self.hooks()
                    document["hooks"][event][0]["hooks"] = invalid
                    (directory / "hooks.json").write_text(json.dumps(document))
                    with self.assertRaises(ValueError):
                        self.validate()

    def test_duplicate_hook_event_keys_are_rejected(self):
        directory = self.write_delivery()
        groups = json.dumps(self.hooks()["hooks"]["Stop"])
        document = json.dumps(self.hooks())
        (directory / "hooks.json").write_text(document[:-2] + ',"Stop":' + groups + "}}")
        with self.assertRaises(ValueError):
            self.validate()


class EvalDeclarationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "framework"
        for provider in ("codex", "claude"):
            plugin = self.root / f"providers/{provider}/plugins/devforgeai"
            for name in ("devforge-brainstorm", "devforge-project-expert-creator", "devforge-develop", "devforge-review"):
                skill = plugin / "skills" / name / "SKILL.md"
                skill.parent.mkdir(parents=True)
                skill.write_text(f"---\nname: {name}\ndescription: Synthetic test skill.\n---\n")
            manifest = plugin / f".{provider}-plugin/plugin.json"
            manifest.parent.mkdir()
            manifest.write_text(json.dumps({"name": "devforgeai"}))
        self.path = self.root / "providers/codex/plugins/devforgeai/skills/devforge-review/evals/evals.json"
        self.path.parent.mkdir()
        self.expected = {"status": "PASS", "skills": 8, "scope": "structure only", "behavior": "NOT_EVALUATED"}

    def document(self):
        # Synthetic identifiers and every native tier prevent a fixture-specific
        # pass rule. Intentionally invalid payloads must remain opaque strings.
        return {
            "schema_version": "devforge.skill-validator-self-evals/v1",
            "skill_under_test": "devforge-review", "provider": "codex",
            "purpose": "Synthetic authored cases, never executed by structural validation.",
            "authoring_status": "AUTHORED_NOT_EXECUTED", "last_authored_date": "2025-01-02",
            "execution_status": "NOT_RUN",
            "execution_boundary": {
                "run_now": False, "instruction": "Retain as data.", "target_write_policy": "Read only.",
                "required_runtime": "Separately assigned.", "output_root": "Separately assigned.",
                "source_export_policy": "Source only.", "missing_prerequisite_result": "Report unavailable.",
            },
            "fixture_materialization": {
                "format": "Inline UTF-8.", "path_rule": "Contained relative paths.",
                "operator_record": "Freeze actual inputs.", "workers": "Isolated workers.",
                "comparison": "Separate outcomes.", "control_bundle": "Real evidence if required.",
                "synthetic_limit": "Not native evidence.", "artifact_case_facts": "Synthetic facts.",
                "protected_file_observation": "Read-only source.",
            },
            "common_grading": {
                "schema": "Separate grading.", "pass_rule": "Evidence required.",
                "fail_rule": "Observed contradiction.", "unavailable_rule": "Missing observation.",
                "preserve_targets": "Do not edit.", "no_auto_repair": "Author owns repair.",
                "expected_result_distinction": "Target and evaluator outcomes differ.",
            },
            "fixture_sets": {
                "origin": {"files": {
                    "target/SKILL.md": "---\nname: [deliberately malformed\n",
                    "target/data.json": "{deliberately malformed JSON",
                    "target/never.py": "raise AssertionError('inline code must never execute')\n",
                }, "authority_note": "Synthetic source fixture."},
                "variant": {"base": "origin", "append_files": {"target/SKILL.md": "Opaque appendix.\n"},
                            "replace_files": {"target/data.json": "still not JSON"},
                            "absent_files": ["target/missing.md"]},
            },
            "cases": [{
                "id": f"case-{tier.lower()}", "title": "Synthetic case", "tier": tier,
                "status": "NOT_RUN", "fixture_set": "variant", "validator_request": "Read only.",
                "operator_setup": ["Use the frozen fixture."],
                "required_observations": ["Preserve separate outcomes."],
                "requires_real_control_bundle": tier == "A",
            } for tier in ("A", "B", "C")],
        }

    def validate(self, document):
        self.path.write_text(json.dumps(document))
        with mock.patch("subprocess.Popen", side_effect=AssertionError("host execution forbidden")), \
                mock.patch("subprocess.run", side_effect=AssertionError("host execution forbidden")):
            return validator.validate(self.root)

    def test_explicit_schema_checks_all_tiers_without_executing_or_materializing_payloads(self):
        document = self.document()
        self.path.write_text(json.dumps(document))
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(self.validate(document), self.expected)
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.assertFalse((self.path.parent / "target").exists())

    def test_unknown_schema_and_unversioned_self_evals_do_not_fall_back(self):
        for schema in (None, "devforge.skill-validator-self-evals/v2", "arbitrary/v1"):
            with self.subTest(schema=schema):
                document = self.document()
                document["schema_version"] = schema
                with self.assertRaisesRegex(ValueError, "unsupported eval schema"):
                    self.validate(document)
        document = self.document()
        del document["schema_version"]
        with self.assertRaisesRegex(ValueError, "wrong eval skill"):
            self.validate(document)

    def test_optional_workspace_refinement_is_typed_declarative_metadata(self):
        document = self.document()
        document["workspace_allocation_refinement"] = {
            "change_id": "SYNTHETIC-CHANGE",
            "requirement_ids": ["SYNTHETIC-ONE", "SYNTHETIC-TWO"],
            "status": "AUTHORED_NOT_EXECUTED",
            "historical_expectations": "Retain original evidence.",
        }
        self.assertEqual(self.validate(document), self.expected)
        for key, value in (("change_id", ""), ("requirement_ids", []),
                           ("requirement_ids", ["SYNTHETIC-ONE", False]),
                           ("requirement_ids", ["SYNTHETIC-ONE", "SYNTHETIC-ONE"]),
                           ("status", None), ("historical_expectations", [])):
            with self.subTest(key=key, value=value):
                invalid = json.loads(json.dumps(document))
                invalid["workspace_allocation_refinement"][key] = value
                with self.assertRaises(ValueError):
                    self.validate(invalid)
        for value in (None, [], {}, {**document["workspace_allocation_refinement"], "unknown": True}):
            with self.subTest(value=value):
                invalid = json.loads(json.dumps(document))
                invalid["workspace_allocation_refinement"] = value
                with self.assertRaises(ValueError):
                    self.validate(invalid)

    def test_optional_case_requirement_ids_are_nonempty_unique_text(self):
        document = self.document()
        document["cases"][0]["requirement_ids"] = ["SYNTHETIC-ONE", "SYNTHETIC-TWO"]
        self.assertEqual(self.validate(document), self.expected)
        for value in (None, "ONE", [], [False], [""], ["ONE", "ONE"]):
            with self.subTest(value=value):
                document["cases"][0]["requirement_ids"] = value
                with self.assertRaises(ValueError):
                    self.validate(document)

    def test_case_requirement_references_resolve_when_refinement_is_declared(self):
        document = self.document()
        document["workspace_allocation_refinement"] = {
            "change_id": "SYNTHETIC-CHANGE", "requirement_ids": ["DECLARED-ONE"],
            "status": "AUTHORED_NOT_EXECUTED", "historical_expectations": "Retain evidence.",
        }
        document["cases"][0]["requirement_ids"] = ["DECLARED-ONE"]
        self.assertEqual(self.validate(document), self.expected)
        document["cases"][0]["requirement_ids"] = ["UNDECLARED-TWO"]
        with self.assertRaisesRegex(ValueError, "unknown case requirement ID"):
            self.validate(document)
    def test_identity_and_provider_must_match_with_other_fields_valid(self):
        for field, value, message in (("skill_under_test", "different-skill", "wrong eval skill"),
                                      ("skill_under_test", None, "wrong eval skill"),
                                      ("provider", "claude", "wrong eval provider"),
                                      ("provider", True, "wrong eval provider")):
            with self.subTest(field=field, value=value):
                document = self.document()
                document[field] = value
                with self.assertRaisesRegex(ValueError, message):
                    self.validate(document)

    def test_envelope_and_metadata_shapes_are_required(self):
        for document in (None, [], "evals"):
            with self.subTest(document=document):
                with self.assertRaisesRegex(ValueError, "eval declaration must be an object"):
                    self.validate(document)
        for key in self.document():
            if key == "schema_version":
                continue
            with self.subTest(missing=key):
                document = self.document()
                del document[key]
                with self.assertRaisesRegex(ValueError, "unsupported fields"):
                    self.validate(document)
        for field, value in (("purpose", ""), ("execution_status", False),
                             ("execution_boundary", []), ("fixture_materialization", {}),
                             ("common_grading", None)):
            with self.subTest(field=field):
                document = self.document()
                document[field] = value
                with self.assertRaises(ValueError):
                    self.validate(document)
        document = self.document()
        document["execution_boundary"]["run_now"] = 0
        with self.assertRaisesRegex(ValueError, "run_now must be a boolean"):
            self.validate(document)

    def test_cases_must_be_a_nonempty_array_and_ids_unique(self):
        for cases in (None, {}, "cases", True, []):
            with self.subTest(cases=cases):
                document = self.document()
                document["cases"] = cases
                with self.assertRaisesRegex(ValueError, "cases must be a nonempty list"):
                    self.validate(document)
        document = self.document()
        document["cases"][1]["id"] = document["cases"][0]["id"]
        with self.assertRaisesRegex(ValueError, "duplicate case ID"):
            self.validate(document)

    def test_case_fields_and_optional_boolean_are_checked(self):
        for field, value in (("id", " "), ("title", 3), ("tier", "unknown"),
                             ("status", None), ("validator_request", []),
                             ("operator_setup", "setup"), ("operator_setup", []),
                             ("required_observations", [False]),
                             ("requires_real_control_bundle", "true")):
            with self.subTest(field=field, value=value):
                document = self.document()
                document["cases"][0][field] = value
                with self.assertRaises(ValueError):
                    self.validate(document)
        for replacement in (None, {"id": "incomplete"}):
            document = self.document()
            document["cases"][0] = replacement
            with self.assertRaises(ValueError):
                self.validate(document)

    def test_case_fixture_and_base_references_must_resolve(self):
        document = self.document()
        document["cases"][0]["fixture_set"] = "undefined"
        with self.assertRaisesRegex(ValueError, "unknown case fixture_set"):
            self.validate(document)
        document = self.document()
        document["fixture_sets"]["variant"]["base"] = "undefined"
        with self.assertRaisesRegex(ValueError, "unknown fixture base"):
            self.validate(document)

    def test_self_and_multi_node_fixture_cycles_are_rejected(self):
        for base in ("origin", "variant"):
            with self.subTest(base=base):
                document = self.document()
                document["fixture_sets"]["origin"]["base"] = base
                with self.assertRaisesRegex(ValueError, "fixture base cycle"):
                    self.validate(document)

    def test_every_inline_path_operation_rejects_escaping_or_nonportable_paths(self):
        paths = ("../escape", "/absolute", "C:/absolute", "C:drive-relative", "folder\\escape",
                 "folder/../escape", "folder//alias", "./alias", "nul\x00name")
        for operation in ("files", "replace_files", "append_files", "absent_files"):
            for relative in paths:
                with self.subTest(operation=operation, relative=relative):
                    document = self.document()
                    fixture = document["fixture_sets"]["variant"]
                    fixture[operation] = [relative] if operation == "absent_files" else {relative: "opaque"}
                    with self.assertRaisesRegex(ValueError, "unsafe inline fixture path"):
                        self.validate(document)

    def test_inline_content_must_be_utf8_text_in_every_map(self):
        for operation in ("files", "replace_files", "append_files"):
            for content in (None, True, 7, [], {}, "\ud800"):
                with self.subTest(operation=operation, content=repr(content)):
                    document = self.document()
                    document["fixture_sets"]["variant"][operation] = {"target/data.json": content}
                    with self.assertRaisesRegex(ValueError, "inline content"):
                        self.validate(document)

    def test_fixture_objects_and_operations_have_declared_shapes(self):
        for fixtures in (None, [], {}):
            document = self.document()
            document["fixture_sets"] = fixtures
            with self.assertRaisesRegex(ValueError, "fixture_sets must be a nonempty object"):
                self.validate(document)
        for field, value in (("base", None), ("files", []), ("replace_files", "text"),
                             ("append_files", None), ("absent_files", {}),
                             ("authority_note", False), ("unsupported_operation", {})):
            with self.subTest(field=field):
                document = self.document()
                document["fixture_sets"]["variant"][field] = value
                with self.assertRaises(ValueError):
                    self.validate(document)

    def test_patch_targets_resolve_and_declared_absence_stays_absent(self):
        for operation in ("replace_files", "append_files"):
            with self.subTest(operation=operation):
                document = self.document()
                document["fixture_sets"]["variant"][operation] = {"target/undefined.txt": "opaque"}
                with self.assertRaisesRegex(ValueError, f"unresolved {operation} target"):
                    self.validate(document)
        document = self.document()
        document["fixture_sets"]["variant"]["absent_files"] = ["target/SKILL.md"]
        with self.assertRaisesRegex(ValueError, "declared absent file is present"):
            self.validate(document)

    def test_duplicate_keys_and_nonfinite_json_are_rejected(self):
        original = json.dumps(self.document())
        documents = [('{"schema_version":"devforge.skill-validator-self-evals/v1",' + original[1:],
                      "duplicate JSON key: schema_version")]
        documents.extend((original.replace('"requires_real_control_bundle": true',
                                           f'"requires_real_control_bundle": {token}'),
                          f"non-finite JSON value: {token}")
                         for token in ("NaN", "Infinity", "-Infinity", "1e999"))
        for raw, diagnostic in documents:
            with self.subTest(diagnostic=diagnostic):
                self.path.write_text(raw)
                with self.assertRaises(ValueError) as raised:
                    validator.validate(self.root)
                self.assertEqual(str(raised.exception), diagnostic)

    def test_legacy_nonfinite_values_require_strict_json_parsing(self):
        # Legacy validation permits this extra field, so a later type check
        # cannot disguise removal of the strict parser's nonfinite checks.
        valid = {"skill_name": "devforge-review", "evals": [], "numeric_metadata": 1.5}
        self.assertEqual(self.validate(valid), self.expected)
        original = json.dumps(valid)
        for token in ("NaN", "Infinity", "-Infinity", "1e999"):
            with self.subTest(token=token):
                raw = original.replace('"numeric_metadata": 1.5', f'"numeric_metadata": {token}')
                self.path.write_text(raw)
                with self.assertRaises(ValueError) as raised:
                    validator.validate(self.root)
                self.assertEqual(str(raised.exception), f"non-finite JSON value: {token}")

    def test_legacy_eval_format_keeps_existing_file_checks(self):
        fixture = self.path.parent / "fixtures/input.txt"
        fixture.parent.mkdir()
        fixture.write_text("Synthetic fixture bytes.")
        valid = {"skill_name": "devforge-review", "evals": [{"id": 1, "files": ["fixtures/input.txt"]}]}
        self.assertEqual(self.validate(valid), self.expected)
        for document, message in (
            ({"skill_name": "wrong", "evals": []}, "wrong eval skill"),
            ({"skill_name": "devforge-review", "evals": [{"files": ["../outside"]}]}, "escapes eval root"),
            ({"skill_name": "devforge-review", "evals": [{"files": ["missing.txt"]}]}, "missing fixture"),
            ({"skill_name": "devforge-review", "evals": None}, "evals must be a list"),
            ({"skill_name": "devforge-review", "evals": [None]}, "legacy eval case must be an object"),
            ({"skill_name": "devforge-review", "evals": [{"files": "input.txt"}]}, "legacy files must be a list"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    self.validate(document)


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
