"""Fixed POC harness embedded in Rust, never loaded from the candidate."""
import io
import json
from pathlib import Path
import resource
import sys
import unittest

resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
sys.path.insert(0, "/work")

def identifiers(suite):
    result = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            result.extend(identifiers(item))
        else:
            result.append(item.id())
    return result

suite = unittest.defaultTestLoader.discover(sys.argv[1], pattern="test_*.py", top_level_dir="/work")
test_ids = identifiers(suite)
output = io.StringIO()
result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
Path("/output/result.json").write_text(json.dumps({
    "tests": result.testsRun,
    "failures": len(result.failures),
    "errors": len(result.errors),
    "skipped": len(result.skipped),
    "unexpected_successes": len(result.unexpectedSuccesses),
    "expected_failures": len(result.expectedFailures),
    "test_ids": test_ids,
    "output": output.getvalue()[:32768],
}))
