"""External operator entrypoint for the embedded mechanical delivery runtime."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from delivery import delivery_core, workflow_runtime


def operate(action, state, contract=None, receipt=None, attempt=None, schedule=None, reason=None, review=None):
    if action == "init":
        if contract is None:
            raise ValueError("init requires the selected session contract")
        return workflow_runtime.start(contract, state)
    if action == "status":
        return workflow_runtime.engine_for_state(state).context(state)
    if action == "advance":
        return workflow_runtime.engine_for_state(state).advance(state)
    if action == "resume":
        return workflow_runtime.engine_for_state(state).resume(state)
    if action == "complete":
        return workflow_runtime.engine_for_state(state).complete(state)
    if action == "native-admission":
        engine = workflow_runtime.engine_for_state(state)
        if not hasattr(engine, "native_admission"):
            raise ValueError("native attempt admission requires a utility workflow")
        return engine.native_admission(state, attempt)
    if action in {"native-schedule-bind", "native-schedule-reserve", "native-schedule-cancel"}:
        engine = workflow_runtime.engine_for_state(state)
        if not hasattr(engine, "native_schedule_bind"):
            raise ValueError("native scheduling requires a utility workflow")
        if action == "native-schedule-bind":
            if schedule is None:
                raise ValueError("native schedule binding requires --schedule")
            return engine.native_schedule_bind(state, schedule)
        if action == "native-schedule-reserve":
            return engine.native_schedule_reserve(state)
        return engine.native_schedule_cancel(state, attempt, reason)
    if action in {"native-process-launch", "native-process-import", "native-result-review", "native-result-close"}:
        return workflow_runtime.native_operation(action, state, attempt=attempt, receipt=receipt, review=review, reason=reason)
    if action == "check":
        return delivery_core.check(contract)
    if action == "verify":
        return delivery_core.verify(contract, receipt)
    raise ValueError("Unsupported delivery operation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "status", "advance", "resume", "complete", "check", "verify", "native-admission",
                                          "native-schedule-bind", "native-schedule-reserve", "native-schedule-cancel",
                                          "native-process-launch", "native-process-import", "native-result-review", "native-result-close"))
    parser.add_argument("--state", type=Path)
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--attempt")
    parser.add_argument("--schedule", type=Path)
    parser.add_argument("--reason")
    parser.add_argument("--review", type=Path)
    args = parser.parse_args()
    try:
        if args.action not in {"check", "verify"} and args.state is None:
            raise ValueError("a protected state directory is required")
        if args.action in {"check", "verify"} and args.contract is None:
            raise ValueError("a selected delivery contract is required")
        if args.action == "verify" and args.receipt is None:
            raise ValueError("an external receipt is required")
        result = operate(args.action, args.state, args.contract, args.receipt, args.attempt, args.schedule, args.reason, args.review)
    except delivery_core._Problem as error:
        result = {"status": error.result, "issues": [error.issue]}
    except (OSError, ValueError) as error:
        result = {"status": "COULD_NOT_RUN", "issues": [str(error)],
                  "scope": "External mechanical delivery operation; no native evaluation"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status", result.get("result")) not in {"FAIL", "COULD_NOT_RUN"} else 2


if __name__ == "__main__":
    sys.exit(main())
