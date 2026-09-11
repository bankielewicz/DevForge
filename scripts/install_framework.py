"""Install project-local skills and agents without touching user-global configuration."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


HOOK_DESTINATIONS = {"codex": ".codex/hooks.json", "claude": ".claude/settings.local.json"}

# Load only this trusted framework helper, including when callers use importlib.
_runtime_spec = importlib.util.spec_from_file_location(
    "devforge_runtime_requirements", Path(__file__).with_name("runtime_requirements.py"))
runtime_requirements = importlib.util.module_from_spec(_runtime_spec)
_runtime_spec.loader.exec_module(runtime_requirements)
json_object = runtime_requirements.json_object
read_json = runtime_requirements.read_json
validate_hook_groups = runtime_requirements.validate_hook_groups
load_plugin_hooks = runtime_requirements.load_plugin_hooks

_adoption_spec = importlib.util.spec_from_file_location(
    "devforge_manual_expert_adoption", Path(__file__).with_name("manual_expert_adoption.py"))
manual_adoption = importlib.util.module_from_spec(_adoption_spec)
_adoption_spec.loader.exec_module(manual_adoption)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def group_digest(group):
    return digest(json.dumps(group, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8"))


def regular_files(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"missing or symlink source directory: {root}")
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlink source is unsupported: {path}")
        if path.is_file():
            yield path, path.relative_to(root)


def authoring_only(relative):
    return (relative.parts[0] in ("evals", "history")
            or "__pycache__" in relative.parts
            or relative.suffix == ".pyc" or relative.name == "provenance.json")


def provider_plugin(framework, provider):
    if provider not in ("codex", "claude"):
        raise ValueError(f"unknown provider: {provider}")
    root = framework / "providers" / provider / "plugins/devforgeai"
    for path in (root, *root.parents):
        if path == framework:
            break
        if path.is_symlink():
            raise ValueError(f"symlink source: {path}")
    if not (root / "skills").is_dir() or (root / "skills").is_symlink():
        raise ValueError(f"provider skill source missing or symlink: {root / 'skills'}")
    return root


def runtime_skill_files(skill):
    if not (skill / "SKILL.md").is_file():
        raise ValueError(f"skill entry missing: {skill}")
    for path, relative in regular_files(skill):
        if not authoring_only(relative):
            yield path, relative


def export_plugin(framework, provider, output):
    """Build a new runtime-only plugin; never overwrite an existing export."""
    framework = framework.resolve()
    plugin = provider_plugin(framework, provider)
    if output.name != "devforgeai" or output.exists() or output.is_symlink():
        raise ValueError("export needs a new directory named devforgeai")
    for parent in output.parents:
        if parent.is_symlink():
            raise ValueError(f"symlink export parent: {parent}")
        if parent.exists() and not parent.is_dir():
            raise ValueError(f"non-directory export parent: {parent}")
    if output.resolve().is_relative_to(plugin):
        raise ValueError("export must be outside the source plugin")
    load_plugin_hooks(plugin, provider)
    requirement = runtime_requirements.load_requirement(plugin, provider)
    planned = {}
    for path, relative in regular_files(plugin):
        if "__pycache__" in relative.parts or relative.suffix == ".pyc":
            continue
        if relative.parts[0] not in (f".{provider}-plugin", "skills", "agents", "hooks"):
            raise ValueError(f"unsupported plugin component for this POC export: {relative}")
        if len(relative.parts) >= 3 and relative.parts[0] == "skills":
            if authoring_only(Path(*relative.parts[2:])):
                continue
        if relative.parts[0] == "hooks" and authoring_only(Path(*relative.parts[1:])):
            continue
        planned[relative.as_posix()] = path.read_bytes()
    manifest = f".{provider}-plugin/plugin.json"
    if read_json(plugin / manifest).get("name") != "devforgeai":
        raise ValueError("plugin manifest name must be devforgeai")
    output.mkdir(parents=True, exist_ok=False)
    for relative, data in planned.items():
        dest = output / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    result = {"status": "EXPORTED", "provider": provider, "output": str(output.resolve()),
              "files_sha256": {rel: digest(data) for rel, data in planned.items()},
              "behavior": "NOT_EVALUATED", "adoption": "NOT_ACCEPTED_STAGING"}
    if requirement is not None:
        result.update(runtime_requirements={provider: requirement}, runtime_host="NOT_VERIFIED")
    return result


def safe_destination(project, relative):
    path = project / relative
    for parent in (path, *path.parents):
        if parent == project:
            break
        if parent.is_symlink():
            raise ValueError(f"symlink destination: {parent}")
        if parent != path and parent.exists() and not parent.is_dir():
            raise ValueError(f"non-directory destination parent: {parent}")
    if path.resolve() == project or not path.resolve().is_relative_to(project):
        raise ValueError(f"destination escapes project: {relative}")
    if path.exists() and not path.is_file():
        raise ValueError(f"destination is not a regular file: {path}")
    return path


def hook_record(event, definition):
    return {"event": event, "definition": definition, "sha256": group_digest(definition)}


def validate_hook_registry(entry, relative):
    if not isinstance(entry, dict) or set(entry) != {"path", "owned", "reused"} or entry["path"] != relative:
        raise ValueError("invalid managed hook registry entry")
    identities = set()
    for category in ("owned", "reused"):
        if not isinstance(entry[category], list):
            raise ValueError("managed hook records must be lists")
        for row in entry[category]:
            if not isinstance(row, dict) or set(row) != {"event", "definition", "sha256"}:
                raise ValueError("invalid managed hook group record")
            if not isinstance(row["event"], str) or not isinstance(row["sha256"], str):
                raise ValueError("invalid managed hook group identity")
            validate_hook_groups({row["event"]: [row["definition"]]}, command_only=True)
            if row["sha256"] != group_digest(row["definition"]):
                raise ValueError("managed hook definition digest mismatch")
            identity = (row["event"], row["sha256"])
            if identity in identities:
                raise ValueError("duplicate managed hook group identity")
            identities.add(identity)


def plan_hook_merge(project, provider, source, previous):
    """Plan group-level ownership; never claim a complete shared settings file."""
    relative = HOOK_DESTINATIONS[provider]
    old = previous if previous is not None else {"path": relative, "owned": [], "reused": []}
    validate_hook_registry(old, relative)
    if source is None and previous is None:
        return None, None
    destination = safe_destination(project, relative)
    exists = destination.exists()
    document = read_json(destination) if exists else {}
    if not isinstance(document, dict):
        raise ValueError(f"hook settings must be an object: {relative}")
    hooks = document.setdefault("hooks", {})
    validate_hook_groups(hooks, command_only=False)
    desired = []
    wanted = set()
    for event, groups in (source or {"hooks": {}})["hooks"].items():
        for group in groups:
            row = hook_record(event, group)
            identity = (event, row["sha256"])
            if identity not in wanted:
                wanted.add(identity)
                desired.append(row)
    retained = set()
    # Verify all prior ownership before deciding whether to replace or retire it.
    if exists:
        for row in old["owned"]:
            matches = [group for group in hooks.get(row["event"], []) if group_digest(group) == row["sha256"]]
            if len(matches) != 1:
                raise ValueError(f"local edit/collision in owned {provider} hook: {row['event']}")
        for row in old["owned"]:
            identity = (row["event"], row["sha256"])
            if identity in wanted:
                retained.add(identity)
            else:
                hooks[row["event"]] = [group for group in hooks[row["event"]]
                                       if group_digest(group) != row["sha256"]]
    entry = {"path": relative, "owned": [], "reused": []}
    for row in desired:
        groups = hooks.setdefault(row["event"], [])
        if (row["event"], row["sha256"]) in retained:
            entry["owned"].append(row)
        elif any(group_digest(group) == row["sha256"] for group in groups):
            entry["reused"].append(row)
        else:
            groups.append(row["definition"])
            entry["owned"].append(row)
    # Missing settings can be rebuilt from selected groups, not lost unrelated data.
    payload = None
    if exists or desired:
        payload = (json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if exists and group_digest(read_json(destination)) == group_digest(document):
            payload = None  # Preserve formatting on a semantically identical install.
    return payload, entry


def install(framework, project, provider, include_experts=False, runtime=None, manual_evidence=None,
            manual_experts_only=False, validator=None):
    framework, project = framework.resolve(), project.resolve()
    if not project.is_dir():
        raise ValueError("project must already exist")
    if manual_experts_only and (provider != "codex" or include_experts):
        raise ValueError("manual-experts-only requires Codex and excludes project experts")
    planned = {}
    hook_sources = {}
    requirements = {}
    providers = ("codex", "claude") if provider == "both" else (provider,)
    for target in providers:
        plugin = provider_plugin(framework, target)
        hook_sources[target] = None if manual_experts_only else load_plugin_hooks(plugin, target)
        requirement = None if manual_experts_only else runtime_requirements.load_requirement(plugin, target)
        if requirement is not None:
            requirements[target] = requirement
        skills = [p for p in sorted((plugin / "skills").iterdir()) if p.is_dir()]
        if manual_experts_only:
            skills = [p for p in skills if p.name in manual_adoption.NAMES]
            if not skills:
                raise ValueError("no promoted Codex experts found")
        if include_experts and (project / "experts").is_dir():
            # Explicit portable POC experts, not a fallback for provider framework sources.
            skills += [p for p in sorted((project / "experts").iterdir()) if (p / "SKILL.md").is_file()]
        skill_root = ".agents/skills" if target == "codex" else ".claude/skills"
        for skill in skills:
            for path, rel in runtime_skill_files(skill):
                name = f"{skill_root}/{skill.name}/{rel.as_posix()}"
                if name in planned:
                    raise ValueError(f"skill name collision: {name}")
                planned[name] = path.read_bytes()
        if manual_experts_only:
            continue
        agents = framework / "providers/codex/agents" if target == "codex" else plugin / "agents"
        dest = ".codex/agents" if target == "codex" else ".claude/agents"
        for path, rel in regular_files(agents):
            planned[f"{dest}/{rel.as_posix()}"] = path.read_bytes()
    adoption = manual_adoption.validate(manual_evidence, planned, project, framework)
    if adoption is not None and adoption.get("qualification_status") == "UNQUALIFIED" and not manual_experts_only:
        raise ValueError("local baseline adoption requires --manual-experts-only")
    runtime_evidence = None
    if requirements:
        # The validating executable is selected explicitly and separately from the
        # runtime under test; compiled Rust, not this script, admits the capabilities.
        if validator is None:
            raise ValueError("delivery-aware project installation requires --validator ABSOLUTE_PATH")
        try:
            runtime_requirements.runtime_digest(validator)  # Identical selection hygiene.
        except ValueError as error:
            raise ValueError(str(error).replace("--runtime", "--validator")) from error
        runtime_evidence = runtime_requirements.probe_runtime(validator, runtime, requirements)
    record_path = safe_destination(project, ".devforge-install.json")
    previous = read_json(record_path) if record_path.exists() else {"schema": 1, "files": {}}
    if (not isinstance(previous, dict) or previous.get("schema", 1) != 1
            or not isinstance(previous.get("files"), dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in previous["files"].items())
            or not isinstance(previous.get("managed_hooks", {}), dict)
            or not isinstance(previous.get("runtime_evidence", {}), dict)):
        raise ValueError("invalid installation inventory")
    managed_hooks = dict(previous.get("managed_hooks", {}))
    hook_writes = {}
    for target in (() if manual_experts_only else providers):
        payload, entry = plan_hook_merge(project, target, hook_sources[target], managed_hooks.get(target))
        if entry is not None:
            managed_hooks[target] = entry
        if payload is not None:
            hook_writes[HOOK_DESTINATIONS[target]] = payload
    retired = []
    selected_roots = {".agents" if target == "codex" else ".claude" for target in providers}
    for rel, old_digest in previous["files"].items():
        parts = Path(rel).parts
        # Only retire previously managed authoring files in the selected skill scopes.
        if (len(parts) >= 4 and parts[0] in selected_roots and parts[1] == "skills"
                and (not manual_experts_only or parts[2] in manual_adoption.NAMES)
                and authoring_only(Path(*parts[3:]))):
            dest = safe_destination(project, rel)
            if dest.exists() and digest(dest.read_bytes()) != old_digest:
                raise ValueError(f"local edit/collision; refusing removal: {rel}")
            retired.append(rel)
    for rel, data in planned.items():
        dest = safe_destination(project, rel)
        if dest.exists():
            old = dest.read_bytes()
            if old != data and previous["files"].get(rel) != digest(old):
                raise ValueError(f"local edit/collision; refusing replacement: {rel}")
    evidence = dict(previous.get("runtime_evidence", {}))
    for target in (() if manual_experts_only else providers):
        evidence.pop(target, None)
        if target in requirements:
            evidence[target] = {**runtime_evidence, "requirement": requirements[target]}
    # Preflight every destination before writes. Existing identical installs are idempotent.
    tracked = dict(previous["files"])
    for target in (() if manual_experts_only else providers):
        tracked.pop(HOOK_DESTINATIONS[target], None)
    for rel in retired:
        tracked.pop(rel, None)
    tracked.update({rel: digest(data) for rel, data in planned.items()})
    updated = {**previous, "schema": 1, "files": tracked, "managed_hooks": managed_hooks}
    if evidence or "runtime_evidence" in previous:
        updated["runtime_evidence"] = evidence
    if adoption is not None:
        updated["manual_expert_adoption"] = {k: v for k, v in adoption.items() if k != "_evidence"}
    record_bytes = (json.dumps(updated, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if runtime_evidence is not None:
        write_paths = set(planned) | set(hook_writes) | set(retired) | {".devforge-install.json"}
        if any(project / relative == Path(runtime) for relative in write_paths):
            raise ValueError("selected runtime binary overlaps an installation destination")
        if runtime_requirements.runtime_digest(runtime) != runtime_evidence["sha256_after"]:
            raise ValueError("selected runtime binary changed before installation writes")
    if adoption is not None:
        replacements = {**planned, **hook_writes, ".devforge-install.json": record_bytes}
        overwritten_inodes = {}
        for relative, payload in replacements.items():
            destination = project / relative
            if destination.is_file():
                stat = destination.stat()
                overwritten_inodes.setdefault((stat.st_dev, stat.st_ino), set()).add(digest(payload))
        for path, sha in adoption["_evidence"].pins.items():
            resolved = Path(path)
            stat = resolved.stat()
            if any(value != sha for value in overwritten_inodes.get((stat.st_dev, stat.st_ino), ())):
                raise ValueError("manual expert evidence: installation would invalidate selected evidence through an alias")
            if resolved.is_relative_to(project):
                relative = resolved.relative_to(project).as_posix()
                if relative in retired or (relative in replacements and digest(replacements[relative]) != sha):
                    raise ValueError("manual expert evidence: installation would invalidate selected evidence")
        adoption["_evidence"].recheck()
    for rel in retired:
        safe_destination(project, rel).unlink(missing_ok=True)
    for rel, data in planned.items():
        dest = safe_destination(project, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    for rel, data in hook_writes.items():
        dest = safe_destination(project, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    record_path.write_bytes(record_bytes)
    result = {"status": "INSTALLED", "project": str(project), "providers": providers,
              "files": len(planned), "removed_authoring_files": retired,
              "scope": "promoted Codex experts only; agents/hooks preserved" if manual_experts_only
                       else "project-local; no global configuration changed"}
    if requirements:
        result.update(runtime_requirements=requirements,
                      runtime_compatibility="VERIFIED", native_activation="NOT_VERIFIED")
    if adoption is not None and adoption.get("qualification_status") == "UNQUALIFIED":
        result.update(qualification_status="UNQUALIFIED", acceptance_status="LOCAL_ACCEPTANCE_SET_PASS")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framework", type=Path, required=True)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--project", type=Path)
    destination.add_argument("--export-plugin", type=Path,
                             help="Build a new runtime-only devforgeai plugin for one provider")
    parser.add_argument("--provider", choices=["codex", "claude", "both"], default="both")
    parser.add_argument("--include-experts", action="store_true")
    parser.add_argument("--runtime", type=Path,
                        help="Explicit absolute devforge executable required by delivery-aware project installs")
    parser.add_argument("--validator", type=Path,
                        help="Explicit absolute DevForge executable that probes and validates the "
                             "selected runtime; required with --runtime")
    parser.add_argument("--manual-evidence", type=Path,
                        help="Exact owner-selected evidence required to adopt promoted Codex expert workflows")
    parser.add_argument("--manual-experts-only", action="store_true",
                        help="Refresh only promoted Codex expert skills, preserving agents and hooks")
    args = parser.parse_args()
    try:
        if args.export_plugin:
            if args.provider == "both" or args.include_experts or args.manual_evidence or args.manual_experts_only:
                raise ValueError("export is unaccepted staging; requires one provider and excludes project experts/adoption evidence")
            result = export_plugin(args.framework, args.provider, args.export_plugin)
        else:
            result = install(args.framework, args.project, args.provider, args.include_experts, args.runtime, args.manual_evidence,
                             args.manual_experts_only, args.validator)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}))
        sys.exit(2)


if __name__ == "__main__":
    main()
