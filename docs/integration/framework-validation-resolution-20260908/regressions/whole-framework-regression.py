import json, subprocess, sys
r = subprocess.run([sys.executable, sys.argv[1], "--framework", sys.argv[2]], text=True, capture_output=True)
print(json.dumps({"exit_code":r.returncode,"stdout":r.stdout,"stderr":r.stderr}))
assert r.returncode == 0, "Valid frozen framework with archived entrypoints and authored self-evals must pass: " + r.stderr
result = json.loads(r.stdout)
assert result["status"] == "PASS"
assert result["scope"] == "structure only"
assert result["behavior"] == "NOT_EVALUATED"
assert not r.stderr
