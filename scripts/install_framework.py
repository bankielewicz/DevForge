"""Install project-local skills and agents without touching user-global configuration."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def digest(data):
    return hashlib.sha256(data).hexdigest()


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
    if output.resolve().is_relative_to(plugin):
        raise ValueError("export must be outside the source plugin")
    planned = {}
    for path, relative in regular_files(plugin):
        if relative.parts[0] not in (f".{provider}-plugin", "skills", "agents"):
            raise ValueError(f"unsupported plugin component for this POC export: {relative}")
        if len(relative.parts) >= 3 and relative.parts[0] == "skills":
            if authoring_only(Path(*relative.parts[2:])):
                continue
        if "__pycache__" not in relative.parts and relative.suffix != ".pyc":
            planned[relative.as_posix()] = path.read_bytes()
    manifest = f".{provider}-plugin/plugin.json"
    if json.loads(planned.get(manifest, b"{}"))["name"] != "devforgeai":
        raise ValueError("plugin manifest name must be devforgeai")
    output.mkdir(parents=True, exist_ok=False)
    for relative, data in planned.items():
        dest = output / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return {"status": "EXPORTED", "provider": provider, "output": str(output.resolve()),
            "files_sha256": {rel: digest(data) for rel, data in planned.items()},
            "behavior": "NOT_EVALUATED"}


def safe_destination(project, relative):
    path = project / relative
    for parent in (path, *path.parents):
        if parent == project:
            break
        if parent.is_symlink():
            raise ValueError(f"symlink destination: {parent}")
    if not path.resolve().is_relative_to(project):
        raise ValueError(f"destination escapes project: {relative}")
    return path


def install(framework, project, provider, include_experts=False):
    framework, project = framework.resolve(), project.resolve()
    if not project.is_dir():
        raise ValueError("project must already exist")
    planned = {}
    providers = ("codex", "claude") if provider == "both" else (provider,)
    for target in providers:
        plugin = provider_plugin(framework, target)
        skills = [p for p in sorted((plugin / "skills").iterdir()) if p.is_dir()]
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
        agents = framework / "providers/codex/agents" if target == "codex" else plugin / "agents"
        dest = ".codex/agents" if target == "codex" else ".claude/agents"
        for path, rel in regular_files(agents):
            planned[f"{dest}/{rel.as_posix()}"] = path.read_bytes()
    record_path = safe_destination(project, ".devforge-install.json")
    previous = json.loads(record_path.read_text()) if record_path.exists() else {"files": {}}
    retired = []
    selected_roots = {".agents" if target == "codex" else ".claude" for target in providers}
    for rel, old_digest in previous["files"].items():
        parts = Path(rel).parts
        # Only retire previously managed authoring files in the selected skill scopes.
        if (len(parts) >= 4 and parts[0] in selected_roots and parts[1] == "skills"
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
    # Preflight every destination before writes. Existing identical installs are idempotent.
    tracked = dict(previous["files"])
    for rel in retired:
        safe_destination(project, rel).unlink(missing_ok=True)
        tracked.pop(rel, None)
    for rel, data in planned.items():
        dest = safe_destination(project, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        tracked[rel] = digest(data)
    record_path.write_text(json.dumps({"schema": 1, "files": tracked}, indent=2) + "\n")
    return {"status": "INSTALLED", "project": str(project), "providers": providers,
            "files": len(planned), "removed_authoring_files": retired,
            "scope": "project-local; no global configuration changed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framework", type=Path, required=True)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--project", type=Path)
    destination.add_argument("--export-plugin", type=Path,
                             help="Build a new runtime-only devforgeai plugin for one provider")
    parser.add_argument("--provider", choices=["codex", "claude", "both"], default="both")
    parser.add_argument("--include-experts", action="store_true")
    args = parser.parse_args()
    try:
        if args.export_plugin:
            if args.provider == "both" or args.include_experts:
                raise ValueError("export requires one provider and excludes project experts")
            result = export_plugin(args.framework, args.provider, args.export_plugin)
        else:
            result = install(args.framework, args.project, args.provider, args.include_experts)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}))
        sys.exit(2)


if __name__ == "__main__":
    main()
