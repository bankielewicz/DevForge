"""Check authored document links, indexes and source hashes; never run model evaluations."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


def local(root, relative):
    path = root / relative
    if Path(relative).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"path outside selected document root: {relative}")
    if path.is_symlink():
        raise ValueError(f"symlink document: {relative}")
    return path


def validate(root):
    errors = []
    index = json.loads(local(root, "package-index.json").read_text())
    skills = index["skills"]
    names = {skill["name"] for skill in skills}
    if len(names) != 12 or len(skills) != 12:
        errors.append("expected 12 unique skill specifications")
    templates = []
    for skill in skills:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill["name"]):
            raise ValueError("invalid indexed skill name")
        path = local(root, skill["specification"])
        text = path.read_text()
        for heading in ("User goal", "Inputs and provenance", "Workflow and phase exits",
                        "Outputs and standardized templates", "Validation and behavioral acceptance",
                        "Native creator authoring prompt", "Shared authoring requirements"):
            if heading not in text:
                errors.append(f"{path.name}: missing {heading}")
        for template in skill["templates"]:
            templates.append(template["path"])
            if not local(root, template["path"]).is_file():
                errors.append(f"missing template: {template['path']}")
            for consumer in template["consumers"]:
                if consumer not in names:
                    errors.append(f"unknown consumer: {consumer}")
        for provider, entry in skill["implementations"].items():
            if entry["source"]:
                expected = f"providers/{provider}/plugins/devforgeai/skills/{skill['name']}"
                if entry["source"] != expected or not (root.parents[1] / expected / "SKILL.md").is_file():
                    errors.append(f"incorrect provider source: {entry['source']}")
    for relative in index["shared_contracts"] + index["shared_templates"] + index["authoring_templates"]:
        if not local(root, relative).is_file():
            errors.append(f"missing indexed document: {relative}")
    files = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_symlink():
            errors.append(f"symlink document: {rel}")
            continue
        if not path.is_file() or rel.parts[0] == "validation" or rel.as_posix() == "validation.json":
            continue
        files[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.suffix == ".json":
            json.loads(path.read_text())
        if path.suffix != ".md":
            continue
        text = path.read_text()
        if sum(line.startswith("```") for line in text.splitlines()) % 2:
            errors.append(f"unbalanced code fence: {rel}")
        if any(line.rstrip() != line for line in text.splitlines()):
            errors.append(f"trailing whitespace: {rel}")
        for link in re.findall(r"\]\(([^)]+)\)", text):
            if "://" in link or link.startswith("#") or "{{" in link:
                continue
            target = link.split("#", 1)[0]
            if target and not (path.parent / target).exists():
                errors.append(f"broken local link: {rel} -> {target}")
    for source in json.loads((root / "research/sources.json").read_text())["sources"]:
        if "snapshot" in source:
            path = local(root / "research", source["snapshot"])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
                errors.append(f"research response digest mismatch: {source['id']}")
    return {"schema_version": 2, "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "FAIL" if errors else "PASS", "errors": errors,
            "specifications": len(skills), "skill_output_templates": len(templates),
            "shared_templates": len(index["shared_templates"]),
            "authoring_templates": len(index["authoring_templates"]),
            "scope": "Local links, JSON, index/source mappings, required sections, cached research digests, whitespace/fences",
            "not_checked": ["Full YAML/schema semantics", "semantic provenance", "Mermaid rendering", "native skill behavior"],
            "native_skill_behavior": "NOT_EVALUATED", "files_sha256": files}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mvp", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        result = validate(args.mvp.resolve())
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(2, f"BLOCKED: {error}\n")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "files_sha256"}, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 2)
