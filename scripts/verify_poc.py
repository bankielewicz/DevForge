"""Run the bounded local POC checks and save logs plus a source manifest."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framework", type=Path, default=Path(__file__).resolve().parents[2] / "DevForgeAI")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    framework = args.framework.resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    evidence = root / "docs/validation" / stamp
    evidence.mkdir(parents=True)
    stages = [
        ("format", ["cargo", "fmt", "--check"]),
        ("clippy", ["cargo", "clippy", "--locked", "--all-targets", "--", "-D", "warnings"]),
        ("build", ["cargo", "build", "--locked"]),
        ("tests", ["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"]),
        ("framework-structure", ["target/debug/devforge", "validate", "framework", "--framework", str(framework)]),
        ("mvp-documents", ["target/debug/devforge", "validate", "mvp", "--mvp", str(framework / "docs/mvp")]),
        ("fixture-demo", ["target/debug/devforge", "demo", "--framework", str(framework), "--policies", "policies", "--output-root", ".poc"]),
    ]
    results = []
    for name, command in stages:
        process = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=180)
        log = process.stdout + process.stderr
        (evidence / f"{name}.log").write_text(log)
        results.append({"check": name, "command": command, "exit_code": process.returncode,
                        "status": "PASS" if process.returncode == 0 else "FAIL"})
        print(f"{name}: {results[-1]['status']}", flush=True)
        if process.returncode:
            print(log, file=sys.stderr)
            break
    sources = {}
    for label, repo in (("DevForge", root), ("DevForgeAI", framework)):
        for path in sorted(repo.rglob("*")):
            rel = path.relative_to(repo)
            if any(p in (".git", "target", ".poc", "__pycache__") for p in rel.parts):
                continue
            if rel.parts[:2] == ("docs", "validation") or not path.is_file():
                continue
            sources[f"{label}/{rel.as_posix()}"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = {"schema": 1, "created_at": stamp, "checks": results, "sources_sha256": sources,
              "model_behavior": "NOT_EVALUATED", "hosted_ci": "NOT_RUN",
              "scope": "Local structural checks, isolated-runner gate tests, installer tests, and scripted project fixtures."}
    report_path = evidence / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Evidence: {report_path}")
    if len(results) != len(stages) or any(r["exit_code"] for r in results):
        sys.exit(2)


if __name__ == "__main__":
    main()
