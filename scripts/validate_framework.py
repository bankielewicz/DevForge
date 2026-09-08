"""Read-only structural validation; never execute code from the framework candidate."""
import argparse
import ast
import importlib.util
import json
from pathlib import Path
import re
import tomllib


# This is framework-owned validation code, never a candidate-provided module.
_runtime_spec = importlib.util.spec_from_file_location(
    "devforge_runtime_requirements", Path(__file__).with_name("runtime_requirements.py"))
runtime_requirements = importlib.util.module_from_spec(_runtime_spec)
_runtime_spec.loader.exec_module(runtime_requirements)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def eval_object(value, required, optional, label):
    require(isinstance(value, dict), f"{label}: expected an object")
    require(required <= set(value) <= required | optional, f"{label}: unsupported fields")


def eval_text(value, label):
    require(isinstance(value, str) and bool(value.strip()), f"{label}: expected nonempty text")


def eval_text_list(value, label):
    require(isinstance(value, list) and bool(value), f"{label}: expected a nonempty list")
    for item in value:
        eval_text(item, label)


def inline_fixture_path(value, label):
    eval_text(value, label)
    require(not value.startswith("/") and not re.match(r"^[A-Za-z]:", value)
            and "\\" not in value and "\x00" not in value
            and all(part not in ("", ".", "..") for part in value.split("/")),
            f"{label}: unsafe inline fixture path {value!r}")


def validate_self_evals(data, skill, provider, label):
    """Check declarations and path relationships only; inline bytes stay inert."""
    fields = {"schema_version", "skill_under_test", "provider", "purpose", "authoring_status",
              "last_authored_date", "execution_status", "execution_boundary",
              "fixture_materialization", "fixture_sets", "common_grading", "cases"}
    eval_object(data, fields, {"workspace_allocation_refinement"}, label)
    require(data["skill_under_test"] == skill, f"wrong eval skill: {label}")
    require(data["provider"] == provider, f"wrong eval provider: {label}")
    for key in ("purpose", "authoring_status", "last_authored_date", "execution_status"):
        eval_text(data[key], f"{label}: {key}")
    if "workspace_allocation_refinement" in data:
        refinement = data["workspace_allocation_refinement"]
        detail = f"{label}: workspace_allocation_refinement"
        eval_object(refinement, {"change_id", "requirement_ids", "status", "historical_expectations"},
                    set(), detail)
        for key in ("change_id", "status", "historical_expectations"):
            eval_text(refinement[key], f"{detail}: {key}")
        eval_text_list(refinement["requirement_ids"], f"{detail}: requirement_ids")
        require(len(set(refinement["requirement_ids"])) == len(refinement["requirement_ids"]),
                f"{detail}: duplicate requirement ID")
    text_objects = {
        "execution_boundary": {"instruction", "target_write_policy", "required_runtime", "output_root",
                               "source_export_policy", "missing_prerequisite_result"},
        "fixture_materialization": {"format", "path_rule", "operator_record", "workers", "comparison",
                                    "control_bundle", "synthetic_limit", "artifact_case_facts",
                                    "protected_file_observation"},
        "common_grading": {"schema", "pass_rule", "fail_rule", "unavailable_rule", "preserve_targets",
                           "no_auto_repair", "expected_result_distinction"},
    }
    for key, text_fields in text_objects.items():
        extra = {"run_now"} if key == "execution_boundary" else set()
        eval_object(data[key], text_fields | extra, set(), f"{label}: {key}")
        for field in text_fields:
            eval_text(data[key][field], f"{label}: {key}.{field}")
    require(type(data["execution_boundary"]["run_now"]) is bool,
            f"{label}: run_now must be a boolean declaration")

    fixtures = data["fixture_sets"]
    require(isinstance(fixtures, dict) and bool(fixtures), f"{label}: fixture_sets must be a nonempty object")
    operations = ("files", "replace_files", "append_files")
    for name, fixture in fixtures.items():
        eval_text(name, f"{label}: fixture identity")
        detail = f"{label}: fixture {name}"
        eval_object(fixture, set(), {"base", *operations, "absent_files", "authority_note"}, detail)
        if "base" in fixture:
            eval_text(fixture["base"], f"{detail}: base")
            require(fixture["base"] in fixtures, f"{detail}: unknown fixture base")
        if "authority_note" in fixture:
            eval_text(fixture["authority_note"], f"{detail}: authority_note")
        for operation in operations:
            values = fixture.get(operation, {})
            require(isinstance(values, dict), f"{detail}: {operation} must be an object")
            for relative, content in values.items():
                inline_fixture_path(relative, detail)
                require(isinstance(content, str), f"{detail}: inline content must be a string")
                try:
                    content.encode("utf-8")
                except UnicodeError as error:
                    raise ValueError(f"{detail}: inline content must encode as UTF-8") from error
        absent = fixture.get("absent_files", [])
        require(isinstance(absent, list), f"{detail}: absent_files must be a list")
        for relative in absent:
            inline_fixture_path(relative, detail)

    # Resolve only path sets, never payloads or filesystem writes. Iteration avoids
    # recursing through candidate-controlled inheritance chains.
    resolved = {}
    pending = set(fixtures)
    while pending:
        ready = [name for name in sorted(pending)
                 if "base" not in fixtures[name] or fixtures[name]["base"] in resolved]
        require(ready, f"{label}: fixture base cycle")
        for name in ready:
            fixture = fixtures[name]
            paths = set(resolved.get(fixture.get("base"), set())) | set(fixture.get("files", {}))
            for operation in ("replace_files", "append_files"):
                require(set(fixture.get(operation, {})) <= paths,
                        f"{label}: fixture {name}: unresolved {operation} target")
            require(not set(fixture.get("absent_files", [])) & paths,
                    f"{label}: fixture {name}: declared absent file is present")
            resolved[name] = paths
            pending.remove(name)

    cases = data["cases"]
    require(isinstance(cases, list) and bool(cases), f"{label}: cases must be a nonempty list")
    identities = set()
    required = {"id", "title", "tier", "status", "fixture_set", "validator_request",
                "operator_setup", "required_observations"}
    for case in cases:
        eval_object(case, required, {"requires_real_control_bundle", "requirement_ids"}, f"{label}: case")
        for key in ("id", "title", "tier", "status", "fixture_set", "validator_request"):
            eval_text(case[key], f"{label}: case {key}")
        require(case["id"] not in identities, f"{label}: duplicate case ID {case['id']}")
        identities.add(case["id"])
        require(case["tier"] in ("A", "B", "C"), f"{label}: unsupported case tier")
        require(case["fixture_set"] in fixtures, f"{label}: unknown case fixture_set")
        for key in ("operator_setup", "required_observations"):
            eval_text_list(case[key], f"{label}: {key}")
        if "requires_real_control_bundle" in case:
            require(type(case["requires_real_control_bundle"]) is bool,
                    f"{label}: requires_real_control_bundle must be boolean")
        if "requirement_ids" in case:
            eval_text_list(case["requirement_ids"], f"{label}: case requirement_ids")
            require(len(set(case["requirement_ids"])) == len(case["requirement_ids"]),
                    f"{label}: duplicate case requirement ID")
            if "workspace_allocation_refinement" in data:
                require(set(case["requirement_ids"]) <= set(data["workspace_allocation_refinement"]["requirement_ids"]),
                        f"{label}: unknown case requirement ID")


def validate_evals(path, provider):
    data = runtime_requirements.read_json(path)
    require(isinstance(data, dict), f"{path}: eval declaration must be an object")
    if "schema_version" in data:
        require(data["schema_version"] == "devforge.skill-validator-self-evals/v1",
                f"{path}: unsupported eval schema")
        validate_self_evals(data, path.parents[1].name, provider, str(path))
        return
    require(data.get("skill_name") == path.parents[1].name, f"wrong eval skill: {path}")
    require(isinstance(data.get("evals"), list), f"{path}: evals must be a list")
    for case in data["evals"]:
        require(isinstance(case, dict), f"{path}: legacy eval case must be an object")
        files = case.get("files", [])
        require(isinstance(files, list), f"{path}: legacy files must be a list")
        for relative in files:
            eval_text(relative, f"{path}: fixture path")
            fixture = path.parent / relative
            require(not Path(relative).is_absolute() and ".." not in Path(relative).parts,
                    f"fixture path escapes eval root: {relative}")
            require(fixture.is_file(), f"missing fixture: {fixture}")


def source_files(root):
    pending = [root]
    while pending:
        for path in pending.pop().iterdir():
            if path.name in (".git", ".poc", "__pycache__"):
                continue
            rel = path.relative_to(root)
            if path.is_symlink():
                raise ValueError(f"symlink not accepted: {rel}")
            if path.is_dir():
                # Only the real root operational directory is outside source scope.
                if rel != Path(".devforge-runtime"):
                    pending.append(path)
            elif path.is_file():
                yield path


def retained_container_tail(rel):
    """Return a dated custody container's descendants, without excluding them."""
    parts = rel.parts
    if parts[:2] != ("docs", "skill-authoring") or len(parts) < 4:
        return ()
    tail = parts[2:]
    if tail[0] == "history":
        tail = tail[1:]
    # Existing evidence uses calendar dates or UTC timestamps with an optional
    # fractional-second suffix. Ordinary authored directories get no exemption.
    dated = re.fullmatch(r"[a-z0-9][a-z0-9-]*-(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{8}T[0-9]{6,12}Z)", tail[0])
    return tail[1:] if dated is not None else ()


def retained_entrypoint(rel):
    """Only entrypoints directly retained in the documented snapshot shapes."""
    tail = retained_container_tail(rel)
    if not tail or tail[-1] != "SKILL.md":
        return False
    if len(tail) == 1:
        return rel.parts[2] == "history"
    return (len(tail) == 2 and
            re.fullmatch(r"(?:source|installed)(?:-[a-z][a-z0-9-]*)?-(?:before|after)(?:-[0-9]+)?",
                         tail[0]) is not None)


def retained_runtime_workflow(rel):
    tail = retained_container_tail(rel)
    return (len(tail) == 5 and re.fullmatch(r"runtime-review-[0-9]+", tail[0]) is not None
            and tail[1:4] == ("frozen-source", ".github", "workflows"))


def validate(root):
    count = 0
    for path in source_files(root):
        rel = path.relative_to(root)
        if ".github" in rel.parts and "workflows" in rel.parts and not retained_runtime_workflow(rel):
            raise ValueError("GitHub workflows belong in DevForge")
        if path.suffix == ".json":
            json.loads(path.read_text())
        if path.suffix == ".toml":
            data = tomllib.loads(path.read_text())
            if "agents" in rel.parts:
                require(all(data.get(k) for k in ("name", "description", "developer_instructions")), str(rel))
        if path.suffix == ".py":
            ast.parse(path.read_text(), filename=str(rel))
        if path.name == "SKILL.md":
            text = path.read_text()
            require(text.startswith("---\n"), str(rel))
            front = text.split("---\n", 2)[1]
            name = re.search(r"^name: ([a-z0-9-]+)$", front, re.M)
            # A directly retained original entrypoint is documentation inside a
            # dated evidence container, not an installed package directory.
            # Still inspect its metadata and every descendant source file.
            archived_entrypoint = retained_entrypoint(rel)
            require(name and (archived_entrypoint or name.group(1) == path.parent.name), str(rel))
            require(re.search(r"^description: .+", front, re.M), str(rel))
            if not archived_entrypoint:
                count += 1
    require(not (root / "plugins/devforgeai").exists(), "retired shared plugin source still exists")
    core = ("devforge-brainstorm", "devforge-project-expert-creator", "devforge-develop", "devforge-review")
    requirements = {}
    for provider in ("claude", "codex"):
        plugin = root / f"providers/{provider}/plugins/devforgeai"
        manifest = plugin / f".{provider}-plugin/plugin.json"
        require(json.loads(manifest.read_text())["name"] == "devforgeai", str(manifest))
        require(all((plugin / "skills" / name / "SKILL.md").is_file() for name in core),
                f"{provider} core skill missing")
        requirement = runtime_requirements.load_requirement(plugin, provider)
        if requirement is not None:
            runtime_requirements.load_plugin_hooks(plugin, provider)
            requirements[provider] = requirement
        for cases in (plugin / "skills").glob("*/evals/evals.json"):
            validate_evals(cases, provider)
    result = {"status": "PASS", "skills": count, "scope": "structure only", "behavior": "NOT_EVALUATED"}
    if requirements:
        result.update(runtime_requirements=requirements, runtime_host="NOT_VERIFIED")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framework", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.framework.resolve()), indent=2))
    except (ValueError, OSError, AssertionError, KeyError) as error:
        parser.exit(2, f"BLOCKED: {error}\n")
