"""Repeatable fixture demonstration; does not call a model or claim expert behavioral evaluation."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys

from install_framework import install


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framework", type=Path, default=Path(__file__).resolve().parents[2] / "DevForgeAI")
    parser.add_argument("--prepare-only", action="store_true", help="Leave initialized candidates for interactive terminal work")
    args = parser.parse_args()
    cli_root = Path(__file__).resolve().parents[1]
    framework = args.framework.resolve()
    binary = cli_root / "target/debug/devforge"
    if not binary.is_file():
        parser.error("Build first with cargo build --locked")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"
    authority = cli_root / ".poc" / run_id
    candidates = framework / ".poc" / run_id
    authority.mkdir(parents=True)
    candidates.mkdir(parents=True)
    results = []
    for slug in ("notes-sqlite", "notes-json"):
        fixture = framework / "examples" / slug
        project = candidates / slug
        policy = cli_root / "policies" / f"{slug}.json"
        state = authority / slug
        shutil.copytree(fixture / "seed", project)
        install(framework, project, "both", include_experts=True, runtime=binary)
        events = []

        def call(*command, success=True):
            argv = [str(binary), *command, "--project", str(project), "--policy", str(policy), "--state", str(state)]
            p = subprocess.run(argv, text=True, capture_output=True, timeout=30)
            value = json.loads(p.stdout)
            if (p.returncode == 0) != success:
                raise RuntimeError(f"Unexpected result: {argv}\n{p.stdout}\n{p.stderr}")
            events.append({"command": list(command), "exit_code": p.returncode, "result": value})
            return value

        call("expert", "prepare")
        call("expert", "bind", "--expert", f"experts/{slug}-persistence")
        call("check")
        call("init")
        if not args.prepare_only:
            call("green", success=False)
            shutil.copy2(fixture / "changes/tests/test_store.py", project / "tests/test_store.py")
            call("red")
            shutil.copy2(fixture / "changes/src/store.py", project / "src/store.py")
            call("green")
            call("accept")
            call("verify")
            # Refresh in a separate copy, preserving the accepted original and its exact evidence.
            refresh = candidates / (slug + "-refresh")
            shutil.copytree(project, refresh)
            original_project = project
            project = refresh
            path = project / "docs/story.md"
            path.write_text(path.read_text() + "\nFixture-only amendment: review storage lifetime before a future multi-request story.\n")
            call("expert", "status")
            call("check", success=False)
            call("expert", "bind", "--expert", f"experts/{slug}-persistence")
            call("check")
            project = original_project
        result = {"project": str(project), "policy": str(policy), "state": str(state), "events": events,
                  "interactive_prompt": f"Use devforge-project-expert-creator to review and improve this project's {slug}-persistence skill against docs/expert-spec.md. Do not modify external policy or gates.",
                  "model_behavior": "NOT_EVALUATED"}
        results.append(result)
        print(f"{slug}: {'INITIALIZED' if args.prepare_only else 'VERIFIED + STALE/REFRESH CHECKED'}")
    report = {"schema": 1, "run_id": run_id, "fixture_execution": "PASS", "model_calls": 0,
              "scope": "Scripted fixtures exercise real gates; no terminal-model behavior is claimed.", "projects": results}
    report_path = authority / "demo-report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report: {report_path}")
    print(f"Candidates: {candidates}")


if __name__ == "__main__":
    main()
