"""Provider command-hook responses for a protected mechanical phase controller.

Callback fields are observations supplied by the client, not authenticated user
authority. This module never reads transcript paths or runs a model.
"""
import json
from pathlib import Path


EVENTS = {
    "codex": {"SessionStart", "UserPromptSubmit", "Stop", "Interrupt", "SessionEnd"},
    "claude": {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"},
}
MAX_MESSAGE = 14000
ORIGIN = (
    "Runtime-generated mechanical workflow instruction. This is not a human "
    "decision, adoption, approval, or new assignment. Preserve that provenance. "
)


def validate_event(provider, event, project):
    if provider not in EVENTS or not isinstance(event, dict):
        raise ValueError("Unknown provider or malformed callback object")
    if not isinstance(event.get("hook_event_name"), str) or event["hook_event_name"] not in EVENTS[provider]:
        raise ValueError("Unsupported callback event for this provider")
    if not isinstance(event.get("session_id"), str) or not event["session_id"].strip():
        raise ValueError("Callback has no session identifier")
    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise ValueError("Callback cwd is not absolute")
    if not Path(cwd).resolve().is_relative_to(Path(project)):
        raise ValueError("Callback does not belong to the selected project")
    if event["hook_event_name"] == "Stop" and type(event.get("stop_hook_active")) is not bool:
        raise ValueError("Stop callback has no boolean continuation observation")


def _content_example(phase, contract):
    outputs = {row["artifact_type"]: row["path"] for row in contract["outputs"]}
    if phase == "Recover":
        return {"known_ideas": [], "known_decisions": [],
                "missing_inputs": ["Describe only an actually missing input, if any."]}
    if phase == "Explore":
        return {"ideas": [{"idea_id": "IDEA-001", "people": None, "problem": None,
                           "outcome": None, "alternatives": [],
                           "open_questions": ["State the real unresolved question."]}],
                "missing_inputs": []}
    if phase == "Record":
        ledger = (contract["primary_ledger_path"]
                  if contract["schema_version"] == "devforge.delivery-task/v2"
                  else outputs.get("idea-ledger"))
        return {"ledger_path": ledger}
    return {"next_action": "Describe the actual next action.",
            "owner": "Name its actual owner.",
            "completion_evidence": "Describe an observable completion condition.",
            "non_goals": [], "handoff_path": outputs["handoff"]}


def instructions(result, session_contract, delivery_contract):
    phase = result.get("phase")
    checkpoint = {
        "schema_version": "devforge.brainstorm-checkpoint/v1",
        "task_id": session_contract["task_id"], "phase": phase,
        "challenge": result.get("challenge"), "state": "ready",
        "content": _content_example(phase, delivery_contract),
    }
    destination = Path(delivery_contract["project_root"]) / session_contract["checkpoint_path"]
    note = (
        ORIGIN + f"Current phase: {phase}. Use the applicable installed workflow. "
        "Do the phase's useful work and save its evidence at " + str(destination)
        + ". Do not call phase/check/receipt commands yourself; the external runtime "
        "will inspect actual bytes automatically. The following is the JSON shape, "
        "not facts to copy: " + json.dumps(checkpoint, ensure_ascii=False)
        + ". Replace explanatory example strings with actual facts. Unknown facts "
        "may stay null with open questions. Do not invent a user or a decision. "
        "If the current phase genuinely needs user input, ask the actual question "
        "and use state awaiting_user with content containing exactly question and "
        "blocking_dependency; this preserves the phase without claiming completion. "
        "Artifact/helper/receipt mechanics are runtime-owned; semantic quality and "
        "human acceptance remain separate."
    )
    extra = result.get("instructions")
    if extra:
        note += " Runtime detail: " + (extra if isinstance(extra, str) else json.dumps(extra))
    return note[:MAX_MESSAGE]


def completion_response(result, task_id):
    """Build the bounded exact receipt message; no native display is inferred."""
    if (not isinstance(result, dict) or result.get("status") != "COMPLETED"
            or not isinstance(task_id, str) or not task_id.strip()
            or result.get("task_id") != task_id
            or not isinstance(result.get("receipt_path"), str)
            or not Path(result["receipt_path"]).is_absolute()
            or any(result.get(key) is not True for key in
                   ("receipt_published", "receipt_readback", "receipt_verified"))
            or not isinstance(result.get("receipt_sha256"), str)
            or len(result["receipt_sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in result["receipt_sha256"])):
        raise ValueError("Verified COMPLETED result and exact receipt binding are required")
    reply = {"systemMessage": (
        "Task " + task_id + ": mechanical task receipt verified; receipt "
        + result["receipt_path"] + "; SHA-256 " + result["receipt_sha256"]
        + ". native turn completion NOT_OBSERVED; semantic acceptance NOT_EVALUATED."
    )}
    if len(json.dumps(reply).encode()) > 8192:
        raise ValueError("Complete receipt response exceeds 8192 UTF-8 bytes")
    return reply


def response(event_name, result, session_contract, delivery_contract, *,
             completion_mode="process", task_result=None):
    """Return only provider-documented hook fields, never an internal state object."""
    status = result.get("status")
    issues = "; ".join(str(x) for x in result.get("issues", []))[:1200]
    if event_name in {"Interrupt", "SessionEnd"}:
        return {}  # Advisory events cannot prevent interruption or termination.
    if completion_mode == "managed-session":
        if status == "COMPLETED":
            return completion_response(result, session_contract["task_id"])
        if task_result is not None:
            # Historical completion is not current applicability, and does not
            # authorize indefinitely blocking later, unrelated conversation.
            return {"systemMessage": (
                "Task " + session_contract["task_id"]
                + ": current applicability could not be verified: " + (issues or str(status))
                + ". Historical receipt (unverified for current artifacts): "
                + task_result["receipt_path"] + "; historical SHA-256 "
                + task_result["receipt_sha256"]
                + ". native turn completion NOT_OBSERVED; semantic acceptance NOT_EVALUATED."
            )}
    if event_name in {"SessionStart", "UserPromptSubmit"}:
        if status in {"FAIL", "COULD_NOT_RUN"}:
            if event_name == "UserPromptSubmit":
                return {"decision": "block", "reason": "Runtime task unavailable: " + issues}
            return {"continue": False, "stopReason": "Runtime task unavailable: " + issues}
        if status in {"READY", "COMPLETED"}:
            context = ORIGIN + "The assigned task has reached " + status + ". No new task is allocated."
        elif status == "WAITING_USER":
            context = ORIGIN + "The current phase is waiting for input. " + str(result.get("question", ""))
        else:
            context = instructions(result, session_contract, delivery_contract)
        return {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": context}}
    if status == "WAITING_USER":
        return {"continue": False,
                "stopReason": "Current phase is waiting for user input. " + str(result.get("question", "")),
                "systemMessage": "Runtime delivery remains pending; no completion receipt was issued."}
    if status in {"READY", "COMPLETED"}:
        # Process-mode supervision finalizes only after successful native exit.
        return {}
    if status == "PROGRESS" or (status == "FAIL" and result.get("terminal") is False):
        reason = instructions(result, session_contract, delivery_contract)
        if issues:
            reason = ORIGIN + "Required phase evidence did not pass: " + issues + ". " + reason
        return {"decision": "block", "reason": reason[:MAX_MESSAGE]}
    return {"continue": False, "stopReason": "Runtime task incomplete: " + (issues or str(status)),
            "systemMessage": "No runtime completion receipt was issued."}
