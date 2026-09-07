"""External operator entrypoint for the embedded mechanical delivery runtime."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from delivery import delivery_core, phase_state


def operate(action, state, contract=None, receipt=None):
    if action == "init":
        if contract is None:
            raise ValueError("init requires the selected session contract")
        return phase_state.start(contract, state)
    if action == "status":
        return phase_state.context(state)
    if action == "advance":
        return phase_state.advance(state)
    if action == "resume":
        return phase_state.resume(state)
    if action == "complete":
        return phase_state.complete(state)
    if action == "check":
        return delivery_core.check(contract)
    if action == "verify":
        return delivery_core.verify(contract, receipt)
    raise ValueError("Unsupported delivery operation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "status", "advance", "resume", "complete", "check", "verify"))
    parser.add_argument("--state", type=Path)
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    try:
        if args.action not in {"check", "verify"} and args.state is None:
            raise ValueError("a protected state directory is required")
        if args.action in {"check", "verify"} and args.contract is None:
            raise ValueError("a selected delivery contract is required")
        if args.action == "verify" and args.receipt is None:
            raise ValueError("an external receipt is required")
        result = operate(args.action, args.state, args.contract, args.receipt)
    except (OSError, ValueError) as error:
        result = {"status": "COULD_NOT_RUN", "issues": [str(error)],
                  "scope": "External mechanical delivery operation; no native evaluation"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status", result.get("result")) not in {"FAIL", "COULD_NOT_RUN"} else 2


if __name__ == "__main__":
    sys.exit(main())
