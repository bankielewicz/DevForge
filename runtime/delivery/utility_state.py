"""Protected utility phases; substantive authoring and semantic review stay outside.

Only the external controller/supervisor may write this state. Shared no-follow,
locked, fsynced publication primitives are reused from the brainstorm runtime;
its session and checkpoint schemas are not reinterpreted as utility schemas.
"""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
from typing import Any

try:
    from . import delivery_core as core, phase_state as store, utility_evidence, utility_schedule, validation_policy
except ImportError:
    import delivery_core as core
    import phase_state as store
    import utility_evidence
    import utility_schedule
    import validation_policy

SESSION_SCHEMA = "devforge.utility-session/v1"
DELIVERY_SCHEMA = "devforge.utility-delivery/v1"
STATE_SCHEMA = "devforge.utility-state/v1"
CHECKPOINT_SCHEMA = "devforge.utility-checkpoint/v1"
RECEIPT_SCHEMA = "devforge.utility-receipt/v1"
SCOPE = ("Protected utility evidence, ordered transitions and artifact custody only; "
         "semantic quality, native callback authentication, rendered delivery and acceptance are separate")
PHASES = {"skill-builder": ("Intake", "Selection", "Design", "Authoring", "PreparedTransfer"),
          "skill-validator": ("P1", "P2", "P3", "P4", "P5", "P6")}
TASKS = {"P1": ("T01", "T02"), "P2": ("T03",), "P3": ("T04",),
         "P4": ("T05", "T06", "T07", "T08"), "P5": ("T09",), "P6": ("T10", "T11", "T12")}
VALIDATOR_GATES = {"deterministic-inspection": "P2", "independent-review": "P3",
                   "native-prerequisites": "P4", "native-C": "P4", "native-B": "P4", "native-A": "P4"}
LIMIT = 8 * 1024 * 1024
MAX_RECORDS = 512


def _result(status, **fields):
    return {"status": status, "issues": [], "scope": SCOPE,
            "native_completion": "NOT_EVALUATED", **fields}


def _guard(function):
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except core._Problem as error:
            return _result(error.result, issues=[error.issue])
        except OSError as error:
            return _result("COULD_NOT_RUN", issues=[f"Filesystem prerequisite unavailable: {error}"])
        except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError, UnicodeError) as error:
            if type(error).__name__ == "NativeProcessError":
                return _result("COULD_NOT_RUN", issues=[str(error)])
            return _result("FAIL", issues=[f"Malformed utility input/state: {type(error).__name__}"])
    call.__name__, call.__doc__ = function.__name__, function.__doc__
    return call


def _list(value, label, *, nonempty=False):
    if not isinstance(value, list) or len(value) > 256 or nonempty and not value:
        core._fail(f"{label}: expected bounded {'nonempty ' if nonempty else ''}array")
    return value


def _json_file(path, label, limit=LIMIT, frozen=None):
    raw = frozen[str(path)] if frozen is not None else store._external(path, limit, label)
    return core._json(raw, label), raw


def _pin(entry, label, frozen=None):
    core._exact(entry, {"path", "sha256"}, label)
    path = store._absolute(entry["path"], label)
    core._digest(entry["sha256"], label)
    raw = frozen[str(path)] if frozen is not None else store._external(path, LIMIT, label)
    if store._hash(raw) != entry["sha256"]:
        core._fail(f"{label}: selected bytes changed")
    return path, raw


def _nonoverlap(paths, label):
    for i, path in enumerate(paths):
        if any(store._collide(path, other) for other in paths[:i]):
            core._fail(f"{label}: colliding paths")


def _load_contract(path, frozen=None):
    path = store._absolute(path, "utility delivery contract")
    contract, raw = _json_file(path, "utility delivery contract", frozen=frozen)
    core._exact(contract, {"schema_version", "task_id", "project_root", "workflow", "mode",
                           "inputs", "outputs", "phases", "gate_inputs", "questions"}
                | ({"validation_policy"} if contract.get("schema_version") == validation_policy.DELIVERY_SCHEMA else set()), "utility delivery contract")
    if contract["schema_version"] not in {DELIVERY_SCHEMA, validation_policy.DELIVERY_SCHEMA} or contract["workflow"] not in PHASES:
        core._fail("unsupported utility delivery schema/workflow")
    if contract["schema_version"] == validation_policy.DELIVERY_SCHEMA and contract["workflow"] != "skill-validator":
        core._fail("v2 validation policy is validator-only")
    if contract["mode"] != "utility":
        core._fail("utility mode must be explicit")
    core._text(contract["task_id"], "utility task_id")
    project = store._absolute(contract["project_root"], "utility project_root")
    if core._within(path, project):
        core._fail("utility delivery contract must be outside worker project")
    selected = []
    for entry in _list(contract["inputs"], "utility inputs", nonempty=True):
        core._exact(entry, {"path", "sha256"}, "utility input")
        selected.append(project / core._relative(entry["path"], "utility input path"))
        core._digest(entry["sha256"], "utility input digest")
    phases = _list(contract["phases"], "utility phases", nonempty=True)
    if [entry.get("id") for entry in phases if isinstance(entry, dict)] != list(PHASES[contract["workflow"]]):
        core._fail("utility phase order/coverage differs from its workflow")
    for phase in phases:
        core._exact(phase, {"id", "classification", "applicable", "basis", "tasks"}, "utility phase")
        if phase["classification"] not in {"Enforced", "Optional"} or type(phase["applicable"]) is not bool:
            core._fail("phase requires an explicit owner-selected classification/applicability")
        core._text(phase["basis"], "phase classification source")
        if phase["classification"] == "Enforced" and not phase["applicable"]:
            core._fail("an Enforced phase cannot be silently excluded")
        if not isinstance(phase["tasks"], dict):
            core._fail("phase tasks must be an explicit item/classification mapping")
        for task, classification in phase["tasks"].items():
            core._text(task, "task ID")
            if classification not in {"Enforced", "Optional"}:
                core._fail("task classification missing or invalid")
        if contract["workflow"] == "skill-validator":
            if (phase["classification"] != "Enforced" or not phase["applicable"]
                    or phase["tasks"] != {task: "Enforced" for task in TASKS[phase["id"]]}):
                core._fail("validator's six phases and twelve tasks remain Enforced")
    if not phases[0]["applicable"] or not phases[-1]["applicable"]:
        core._fail("intake and final transfer must be applicable")
    output_ids = set()
    active = {row["id"] for row in phases if row["applicable"]}
    for entry in _list(contract["outputs"], "utility outputs", nonempty=True):
        core._exact(entry, {"id", "path", "phase", "format", "schema_version", "required_fields"}, "utility output")
        identity = core._text(entry["id"], "output ID")
        if identity in output_ids or entry["phase"] not in active:
            core._fail("duplicate output ID or output assigned to an inapplicable phase")
        output_ids.add(identity)
        selected.append(project / core._relative(entry["path"], "utility output path"))
        if entry["format"] not in {"json", "artifact", "text"}:
            core._fail("utility output format must be json, artifact or text")
        for field in _list(entry["required_fields"], "output required fields"):
            core._text(field, "required field")
        if entry["format"] == "text":
            if entry["schema_version"] is not None or entry["required_fields"]:
                core._fail("text outputs cannot declare structured fields")
        else:
            core._text(entry["schema_version"], "output schema")
            if entry["format"] == "artifact" and entry["schema_version"] != "devforge.artifact/v1":
                core._fail("human-readable artifacts reuse devforge.artifact/v1")
    gate_ids = set()
    for gate in _list(contract["gate_inputs"], "gate inputs"):
        core._exact(gate, {"id", "phase", "path", "producer", "allowed_outcomes"}, "gate input")
        identity = core._text(gate["id"], "gate input ID")
        if identity in gate_ids or gate["phase"] not in active:
            core._fail("duplicate gate ID or invalid phase")
        gate_ids.add(identity)
        target = store._absolute(gate["path"], "gate input path")
        if core._within(target, project):
            core._fail("gate input must be outside worker writes")
        selected.append(target)
        core._text(gate["producer"], "allocated gate producer")
        outcomes = _list(gate["allowed_outcomes"], "gate outcomes", nonempty=True)
        if len(set(outcomes)) != len(outcomes) or not set(outcomes) <= ({"PASS", "FAIL", "COULD_NOT_RUN", "NOT_RUN"} | ({"NOT_APPLICABLE"} if contract["schema_version"] == validation_policy.DELIVERY_SCHEMA else set())):
            core._fail("invalid gate outcome selection")
    if contract["workflow"] == "skill-validator":
        declared = {row["id"]: row["phase"] for row in contract["gate_inputs"]}
        if any(declared.get(identity) != phase for identity, phase in VALIDATOR_GATES.items()):
            core._fail("validator requires independent inspection/review and separate native prerequisite/C/B/A gates")
    # An applicable phase must consume actual selected artifact or external evidence.
    for phase in active:
        if not any(row["phase"] == phase for row in contract["outputs"] + contract["gate_inputs"]):
            core._fail("every applicable phase requires actual file evidence")
    question_ids = set()
    for question in _list(contract["questions"], "questions"):
        core._exact(question, {"id", "phase", "question", "blocking_dependency", "choices", "decision_path"}, "question")
        identity = core._text(question["id"], "question ID")
        if identity in question_ids or question["phase"] not in active:
            core._fail("duplicate question ID or invalid phase")
        question_ids.add(identity)
        core._text(question["question"], "question text")
        core._text(question["blocking_dependency"], "blocking dependency")
        choices = _list(question["choices"], "question choices")
        for choice in choices:
            core._text(choice, "question choice")
        if len({choice.strip().casefold() for choice in choices}) != len(choices):
            core._fail("duplicate answer choices")
        if choices:
            if question["decision_path"] is not None:
                core._fail("finite-choice question cannot also select external interpretation")
        else:
            target = store._absolute(question["decision_path"], "answer interpretation path")
            if core._within(target, project):
                core._fail("answer interpretation must be outside worker writes")
            selected.append(target)
    _nonoverlap([path, *selected], "utility selected files")
    return contract, raw, project


def protected_paths(contract, *, frozen=None):
    paths = [Path(row["path"]) for row in contract["gate_inputs"]] + [
        Path(row["decision_path"]) for row in contract["questions"] if row["decision_path"] is not None]
    if contract.get("schema_version") == validation_policy.DELIVERY_SCHEMA:
        selection = contract["validation_policy"]
        paths += [store._absolute(selection[key]["path"], "policy protected input")
                  for key in ("policy_ref", "acceptance_ref", "plan")]
        _, raw = _pin(selection["plan"], "protected frozen validation plan", frozen)
        plan = core._json(raw, "protected frozen validation plan")
        # Enumerate references, not directories to expose in the worker. Profile,
        # result and state collision checks cover every selected external source.
        def visit(value):
            if isinstance(value, dict):
                if {"path", "sha256"} <= set(value):
                    paths.append(store._absolute(value["path"], "policy protected reference"))
                if value.get("review_path") is not None:
                    paths.append(store._absolute(value["review_path"], "selected graph review destination"))
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)
        visit(plan)
    return list(dict.fromkeys(paths))


def _configuration(session_path, root, *, initial=False, frozen=None):
    session_path, root = store._absolute(session_path, "session"), store._absolute(root, "state")
    session, raw = _json_file(session_path, "utility session", store.SESSION_LIMIT, frozen)
    core._exact(session, store._SESSION_KEYS, "utility session")
    if session["schema_version"] != SESSION_SCHEMA or session["provider"] != "codex":
        core._fail("utility session requires selected Codex schema")
    if type(session["max_corrections_per_phase"]) is not int or not 0 <= session["max_corrections_per_phase"] <= 3:
        core._fail("correction budget must be an integer in [0,3]")
    deadline = store._stamp(session["deadline_utc"], "utility deadline")
    contract_path = store._absolute(session["delivery_contract"], "delivery path")
    contract, contract_raw, project = _load_contract(contract_path, frozen)
    if session["delivery_contract_sha256"] != store._hash(contract_raw) or session["task_id"] != contract["task_id"]:
        core._fail("utility session/delivery binding differs")
    assignment, assignment_raw = _pin(session["assignment"], "utility assignment", frozen)
    if core._within(assignment, project) or core._within(session_path, project):
        core._fail("utility authority must be outside worker project")
    fixed = [("session", str(session_path), raw), ("delivery", str(contract_path), contract_raw),
             ("assignment", str(assignment), assignment_raw)]
    policy = None
    if contract["schema_version"] == validation_policy.DELIVERY_SCHEMA:
        policy = validation_policy.load(contract["validation_policy"], assignment_raw, project, frozen=frozen)
        reviewer = next(g for g in contract["gate_inputs"] if g["id"] == "independent-review")["producer"]
        validation_policy.require(reviewer == policy.value["selection_reviewer"], "delivery producer differs from selected reviewer")
        fixed.extend(policy.fixed())
    installed_paths = []
    for entry in _list(session["installed_inputs"], "installed inputs", nonempty=True):
        path, value = _pin(entry, "installed input", frozen)
        installed_paths.append(path)
        fixed.append(("installed", str(path), value))
    _nonoverlap(installed_paths, "installed files")
    checkpoint = core._relative(session["checkpoint_path"], "checkpoint path")
    receipt = store._absolute(session["receipt_path"], "receipt path")
    outputs = {row["path"]: row for row in contract["outputs"]}
    baselines = {}
    for row in _list(session["output_baselines"], "output baselines", nonempty=True):
        core._exact(row, {"path", "sha256", "archive", "allow_unchanged"}, "output baseline")
        if row["path"] not in outputs or row["path"] in baselines or type(row["allow_unchanged"]) is not bool:
            core._fail("baseline must cover a selected output once")
        if row["sha256"] is None:
            if row["archive"] is not None or row["allow_unchanged"]:
                core._fail("absent baseline requires null archive and allow_unchanged false")
        else:
            core._digest(row["sha256"], "preimage digest")
            core._relative(row["archive"], "preimage archive")
        baselines[row["path"]] = row
    if set(baselines) != set(outputs):
        core._fail("baseline coverage differs from selected outputs")
    mutable = [project / p for p in outputs] + [project / checkpoint] + [
        project / row["archive"] for row in baselines.values() if row["archive"] is not None]
    immutable = [session_path, contract_path, assignment, *installed_paths,
                 *[project / row["path"] for row in contract["inputs"]], *protected_paths(contract, frozen=frozen)]
    _nonoverlap([*mutable, *immutable], "utility mutable/fixed selection")
    if any(store._collide(root, p) for p in [project, *immutable, Path(__file__).resolve().parent]):
        core._fail("utility state overlaps worker/code/input scope")
    if any(store._collide(receipt, p) for p in [project, *immutable, Path(__file__).resolve().parent]):
        core._fail("utility receipt overlaps worker/code/input scope")
    if core._within(receipt, root):
        if receipt.relative_to(root).parts[0] in {"LOCK", "MANIFEST.json", "HEAD.json", "records", "snapshots", "pending",
                                                 "native-collector", "native-launch-requests"}:
            core._fail("receipt collides with protected journal")
    elif store._collide(receipt, root):
        core._fail("receipt is an ancestor of state")
    store._safe_destination(receipt)
    if not core._within(receipt, root):
        with core._directory(receipt.parent, "receipt parent"):
            pass
    preimages = {}
    with core._directory(project, "utility project") as fd:
        for row in contract["inputs"]:
            value = (frozen[str(project / row["path"])] if frozen is not None else
                     store._read_at(fd, row["path"], LIMIT, "utility fixed input"))
            if store._hash(value) != row["sha256"]:
                core._fail("utility fixed input changed")
            fixed.append(("input", str(project / row["path"]), value))
        for path in mutable:
            core._inspect_destination(fd, path.relative_to(project).as_posix())
        for rel, row in baselines.items():
            if initial:
                value = store._read_at(fd, rel, LIMIT, "output preimage", optional=True)
                if (None if value is None else store._hash(value)) != row["sha256"]:
                    core._fail("prelaunch output differs from selected baseline")
                if value is not None:
                    preimages[rel] = value
            if row["archive"] is not None and frozen is None:
                value = store._read_at(fd, row["archive"], LIMIT, "output archive", optional=initial)
                if value is not None and store._hash(value) != row["sha256"]:
                    core._fail("output preimage archive changed")
    if initial and store._external(receipt, LIMIT, "receipt", optional=True) is not None:
        core._fail("receipt already exists before admission")
    return {"session": session, "raw": raw, "contract": contract, "contract_raw": contract_raw,
            "project": project, "receipt": receipt, "deadline": deadline, "fixed": fixed,
            "preimages": preimages, "baselines": baselines, "outputs": outputs, "validation_policy": policy}


def _structured(raw, spec, cfg=None, state=None):
    extra = []
    if not raw or not raw.strip():
        core._fail("required output is empty")
    if spec["format"] == "text":
        text = raw.decode("utf-8")
        if store._PLACEHOLDER.search(text):
            core._fail("output contains unresolved template placeholders")
        return
    if spec["format"] == "artifact":
        value, body = core._envelope(raw, "utility artifact")
        if not body.strip():
            core._fail("utility artifact lacks substantive body")
        for key in ("artifact_id", "artifact_type", "project_id", "status", "created_at_utc"):
            core._text(value.get(key), f"artifact {key}")
        core._revision(value.get("revision"), "artifact revision")
    else:
        value = core._json(raw, "utility JSON artifact")
    if not isinstance(value, dict) or value.get("schema_version") != spec["schema_version"]:
        core._fail("utility output schema differs from selected format")
    if str(value.get("schema_version", "")).startswith(("devforge.skill-validation-results/", "devforge.skill-validation-decision/")) and value["schema_version"] not in {"devforge.skill-validation-results/v1", "devforge.skill-validation-decision/v1", validation_policy.RESULTS_SCHEMA, validation_policy.DECISION_SCHEMA}:
        core._fail("unsupported validation results schema")
    if value.get("schema_version") in {validation_policy.RESULTS_SCHEMA, validation_policy.DECISION_SCHEMA}:
        if cfg is None or cfg.get("validation_policy") is None or state is None:
            core._fail("v2 result reduction requires selected external policy/review context")
        review_gate = next(g for g in cfg["contract"]["gate_inputs"] if g["id"] == "independent-review")
        if review_gate["path"] not in state.gates:
            core._fail("v2 results require admitted T04 evidence")
        review_ref = core._json(state.source(review_gate["path"], "T04 gate"), "T04 gate")["evidence"][0]
        reducer = utility_evidence.validation_results if value["schema_version"] == validation_policy.RESULTS_SCHEMA else utility_evidence.validation_decision
        derived = reducer(raw, cfg["validation_policy"], review_ref,
            delivery_ref={"path": cfg["session"]["delivery_contract"], "sha256": store._hash(cfg["contract_raw"])},
            native_receipts=state.native_imports)
        extra = [("validation_result_evidence", path, data) for path, data in cfg["validation_policy"].sources.items()]
        for key in ("report_completion", "validation_disposition", "routine_adoption_eligible", "lineage"):
            if value[key] != derived[key]:
                core._fail("v2 result claimed summary differs from original assertion reduction: " + key)
    for path in spec["required_fields"]:
        current = value
        for field in path.split("."):
            if not isinstance(current, dict) or field not in current:
                core._fail(f"output lacks required evidence field {path}")
            current = current[field]
        if (current is None or isinstance(current, str) and not current.strip()
                or isinstance(current, (list, dict)) and not current):
            core._fail(f"output has no evidence for required field {path}")
    if store._PLACEHOLDER.search(raw.decode("utf-8")):
        core._fail("output contains unresolved template placeholders")
    # No complete-byte self identity in any structured digest locator.
    def inspect(node):
        if isinstance(node, dict):
            if node.get("sha256") == store._hash(raw):
                core._fail("an artifact cannot contain its own complete-byte digest")
            for child in node.values():
                inspect(child)
        elif isinstance(node, list):
            for child in node:
                inspect(child)
    inspect(value)
    return extra


def _gate(raw, spec, cfg, state=None):
    value = core._json(raw, "external utility gate input")
    is_v2 = cfg["contract"]["schema_version"] == validation_policy.DELIVERY_SCHEMA
    extra = validation_policy.gate(value, spec, cfg, state) if is_v2 else []
    if not is_v2:
        core._exact(value, {"schema_version", "task_id", "phase", "producer", "inputs_sha256",
                            "outcome", "reason", "evidence"}, "external utility gate input")
    if (value["schema_version"] != (validation_policy.GATE_SCHEMA if is_v2 else "devforge.utility-gate-input/v1")
            or value["task_id"] != cfg["session"]["task_id"] or value["phase"] != spec["phase"]
            or value["producer"] != spec["producer"] or value["inputs_sha256"] != store._hash(cfg["contract_raw"])):
        core._fail("gate input has wrong task/phase/producer/selected input binding")
    if value["outcome"] not in spec["allowed_outcomes"]:
        core._fail("gate outcome does not admit the selected dependent action")
    if spec["id"] in {"native-C", "native-B", "native-A"} and value["outcome"] in {"PASS", "FAIL"}:
        if state is None:
            core._fail("native executed outcomes require an authenticated result importer")
        state.check_native_gate(spec, value)
    core._text(value["reason"], "gate scope/reason")
    evidence = list(extra)
    for ref in _list(value["evidence"], "gate underlying evidence", nonempty=True):
        path, evidence_raw = _pin(ref, "gate underlying evidence", state.frozen if state else None)
        if str(path) == spec["path"]:
            core._fail("gate input cannot cite itself as underlying evidence")
        evidence.append(("gate_evidence", str(path), evidence_raw))
    if spec["id"] == "native-prerequisites" and value["outcome"] == "PASS":
        selected_plan_paths = {ref["path"] for ref in value["evidence"]}
        plans = [(path, data) for kind, path, data in evidence
                 if kind == "gate_evidence" and path in selected_plan_paths]
        if len(value["evidence"]) != 1 or len(plans) != 1:
            core._fail("native prerequisite PASS requires exactly one complete frozen native plan")
        native, fixed = utility_evidence.native_plan(plans[0][1], cfg["session"]["task_id"], frozen=state.frozen if state else None)
        if is_v2:
            validation_policy.require(native["schema_version"] == "devforge.utility-native-plan/v2" and native["validation_plan"] == cfg["validation_policy"].selection["plan"], "native plan selection version/binding mismatch")
        elif native["schema_version"] != "devforge.utility-native-plan/v1":
            core._fail("v1 gate cannot select v2 native authority")
        evidence.extend(fixed)
    return evidence


def _prior_native_gates(state):
    for identity, phase in (("deterministic-inspection", "P2"), ("independent-review", "P3")):
        prior = next((row for row in state.cfg["contract"]["gate_inputs"]
                      if row["id"] == identity and row["phase"] == phase), None)
        if prior is None or prior["path"] not in state.gates:
            core._fail("native scheduling lacks prior independent gate input " + identity)
        raw = state.source(prior["path"], "prior native gate")
        if core._json(raw, "prior native gate").get("outcome") != "PASS":
            core._fail("prior native gate remains failed or unavailable: " + identity)


def _native_prerequisites(state):
    if state.cfg["contract"]["workflow"] != "skill-validator" or state.phase != "P4" or state.status != "ACTIVE":
        core._fail("native scheduling requires active validator P4 after independent inspection/review")
    _prior_native_gates(state)
    spec = next(row for row in state.cfg["contract"]["gate_inputs"] if row["id"] == "native-prerequisites")
    raw = state.source(spec["path"], "native prerequisite gate")
    value = core._json(raw, "native prerequisite gate")
    if value.get("outcome") != "PASS":
        core._fail("native prerequisites remain unresolved; reporting can continue separately")
    fixed = _gate(raw, spec, state.cfg, state)
    selected_plan_paths = {ref["path"] for ref in value["evidence"]}
    plans = [(path, data) for kind, path, data in fixed
             if kind == "gate_evidence" and path in selected_plan_paths]
    if len(value["evidence"]) != 1 or len(plans) != 1:
        core._fail("native plan binding is missing or ambiguous")
    path, plan_raw = plans[0]
    if state.cfg["contract"]["schema_version"] == validation_policy.DELIVERY_SCHEMA:
        fixed.extend(state.cfg["validation_policy"].fixed())
    return spec, raw, fixed, {"path": path, "sha256": store._hash(plan_raw)}, core._json(plan_raw, "native plan")


def _native_complete_allocation(state, plan):
    """Validate the independent coverage oracle before any campaign binding."""
    allocation, sources = utility_evidence.launch_allocation(plan, frozen=state.frozen)
    for call in allocation["required_calls"]:
        if call["review_path"] is None:
            continue
        _native_protected(state, call["review_path"], "native selected review path", plan=plan)
    for _, path, _ in sources:
        _native_protected(state, path, "native complete allocation input", plan=plan)
    return allocation, sources


class State:
    def __init__(self, root, fd, *, cleanup=False):
        self.root, self.fd = root, fd
        self.manifest, manifest_raw = store._json_at(fd, "MANIFEST.json", "utility manifest")
        core._exact(self.manifest, {"schema_version", "state_root", "session_path", "session_sha256",
                                   "task_id", "project_root", "started_at_utc", "snapshots"}, "utility manifest")
        if self.manifest["schema_version"] != STATE_SCHEMA or self.manifest["state_root"] != str(root):
            core._fail("utility manifest identity mismatch")
        self.blobs = store._object_directory(fd, "snapshots", ".bin", max(LIMIT, store.INSTALLED_LIMIT))
        self.frozen = {} if cleanup else None
        if cleanup:
            for ref in self.manifest["snapshots"]:
                store._validate_ref(ref, self.blobs)
                self.frozen[ref["source"]] = self.blobs[ref["sha256"]]
        self.cfg = _configuration(Path(self.manifest["session_path"]), root, frozen=self.frozen)
        if (store._hash(self.cfg["raw"]) != self.manifest["session_sha256"]
                or self.manifest["task_id"] != self.cfg["session"]["task_id"]
                or self.manifest["project_root"] != str(self.cfg["project"])):
            core._fail("selected utility session changed")
        for ref in self.manifest["snapshots"]:
            store._validate_ref(ref, self.blobs)
        pins = {(kind, source): store._hash(raw) for kind, source, raw in self.cfg["fixed"]}
        prior = {(ref["kind"], ref["source"]): ref["sha256"] for ref in self.manifest["snapshots"] if ref["kind"] != "preimage"}
        if pins != prior:
            core._fail("fixed source/installed inputs differ from protected snapshot")
        self.head, _ = store._json_at(fd, "HEAD.json", "utility HEAD")
        core._exact(self.head, {"schema_version", "manifest_sha256", "records"}, "utility HEAD")
        if self.head["schema_version"] != "devforge.utility-head/v1" or self.head["manifest_sha256"] != store._hash(manifest_raw):
            core._fail("utility HEAD/manifest mismatch")
        if not isinstance(self.head["records"], list) or not 1 <= len(self.head["records"]) <= MAX_RECORDS:
            core._fail("utility journal count invalid")
        self.phases = [row["id"] for row in self.cfg["contract"]["phases"] if row["applicable"]]
        self.phase, self.status, self.challenge = self.phases[0], "ACTIVE", None
        self.accepted, self.gates, self.questions, self.nonces = {}, {}, {}, set()
        self.native_attempts = {}
        self.schedule = None
        self.schedule_allocation = None
        self.native_claims, self.native_imports, self.native_reviews = {}, {}, {}
        self.reservation_refs = {}
        self.schedule_plan, self.schedule_plan_ref = None, None
        self.gate_outcomes = {}
        self.pending, self.intent, self.receipt = None, None, None
        self.corrections, self.issues, self.last_time = 0, [], store._stamp(self.manifest["started_at_utc"], "start time")
        for index, digest in enumerate(self.head["records"]):
            core._digest(digest, "utility journal digest")
            raw = store._read_at(fd, "records/" + digest + ".json", store.STATE_JSON_LIMIT, "utility record")
            if store._hash(raw) != digest:
                core._fail("utility journal digest mismatch")
            row = core._json(raw, "utility journal record")
            core._exact(row, {"sequence", "previous", "at_utc", "operation", "data", "snapshots"}, "utility record")
            if row["sequence"] != index or row["previous"] != (self.head["records"][index - 1] if index else None):
                core._fail("utility journal chain mismatch")
            stamp = store._stamp(row["at_utc"], "utility record time")
            if stamp < self.last_time or stamp >= self.cfg["deadline"] and row["operation"] not in {"native_schedule_cancel", "native_process_import", "native_result_close"}:
                core._fail("utility journal time violates original deadline")
            self.last_time = stamp
            for ref in row["snapshots"]:
                store._validate_ref(ref, self.blobs)
                if self.frozen is not None:
                    self.frozen[ref["source"]] = self.blobs[ref["sha256"]]
            self._apply(row)
            self._retain_reservation_ref(row, digest)
        if cleanup:
            return
        for relative, digest in self.accepted.items():
            with core._directory(self.cfg["project"], "utility current outputs") as project_fd:
                raw = store._read_at(project_fd, relative, LIMIT, "accepted output")
            if store._hash(raw) != digest:
                core._fail("current output changed after its accepted phase")
        for path, digest in self.gates.items():
            raw = store._external(Path(path), LIMIT, "accepted external evidence")
            if store._hash(raw) != digest:
                core._fail("accepted external evidence changed")
        receipt = store._external(self.cfg["receipt"], LIMIT, "utility receipt", optional=True)
        if receipt is not None and self.intent is None:
            core._fail("receipt collision without protected publication intent")

    def source(self, path, label):
        return self.frozen[str(path)] if self.frozen is not None else store._external(Path(path), LIMIT, label)

    def _retain_reservation_ref(self, record, digest):
        if record["operation"] == "native_schedule_reserve" and self.schedule.inflight():
            identity = self.schedule.state.reservations[-1].attempt_id
            self.reservation_refs.setdefault(identity, {"sha256": digest, "challenge": self.challenge})

    def _nonce(self, nonce):
        store._nonce_value(nonce)
        if nonce in self.nonces:
            core._fail("utility journal repeats a challenge")
        self.nonces.add(nonce)
        self.challenge = nonce

    def checkpoint(self, raw):
        value = core._json(raw, "utility checkpoint")
        core._exact(value, {"schema_version", "task_id", "phase", "sequence", "challenge",
                           "inputs_sha256", "state", "evidence", "question_id"}, "utility checkpoint")
        if (value["schema_version"] != CHECKPOINT_SCHEMA or value["task_id"] != self.cfg["session"]["task_id"]
                or value["phase"] != self.phase or type(value["sequence"]) is not int
                or value["sequence"] != self.sequence or value["challenge"] != self.challenge
                or value["inputs_sha256"] != store._hash(self.cfg["contract_raw"])):
            core._fail("checkpoint has wrong task/phase/sequence/challenge/input snapshot")
        if value["state"] not in {"ready", "awaiting_user"}:
            core._fail("unsupported utility checkpoint state")
        if value["state"] == "awaiting_user":
            matches = [q for q in self.cfg["contract"]["questions"] if q["id"] == value["question_id"] and q["phase"] == self.phase]
            if not matches or value["question_id"] in self.questions or value["evidence"]:
                core._fail("waiting must identify a selected unresolved question with no completion evidence")
        else:
            if value["question_id"] is not None:
                core._fail("ready checkpoint cannot declare a waiting question")
            pending = [q for q in self.cfg["contract"]["questions"] if q["phase"] == self.phase and q["id"] not in self.questions]
            if pending:
                core._fail("required consequential answer is unresolved")
            evidence = _list(value["evidence"], "checkpoint evidence")
            ids = []
            for ref in evidence:
                core._exact(ref, {"id", "path", "sha256"}, "checkpoint evidence reference")
                core._digest(ref["sha256"], "checkpoint evidence digest")
                ids.append(ref["id"])
            wanted = {row["id"] for row in self.cfg["contract"]["outputs"] if row["phase"] == self.phase}
            if set(ids) != wanted or len(ids) != len(set(ids)):
                core._fail("checkpoint evidence coverage differs from required phase artifacts")
        return value

    def _apply(self, record):
        op, data = record["operation"], record["data"]
        self.sequence = record["sequence"] - 1
        if op == "start":
            core._exact(data, {"challenge"}, "utility start")
            if record["sequence"] != 0:
                core._fail("start is only admitted once")
            self._nonce(data["challenge"])
        elif op in {"accepted", "waiting"}:
            core._exact(data, {"next_challenge"}, "utility evidence transition")
            if self.status != "ACTIVE":
                core._fail("evidence submitted outside ACTIVE state")
            refs = [r for r in record["snapshots"] if r["kind"] == "checkpoint"]
            if len(refs) != 1:
                core._fail("utility transition lacks actual checkpoint")
            checkpoint = self.checkpoint(self.blobs[refs[0]["sha256"]])
            if op == "waiting":
                if checkpoint["state"] != "awaiting_user" or data["next_challenge"] is not None:
                    core._fail("invalid waiting transition")
                self.pending = next(q for q in self.cfg["contract"]["questions"] if q["id"] == checkpoint["question_id"])
                self.status = "WAITING_USER"
            else:
                if checkpoint["state"] != "ready":
                    core._fail("accepted phase lacks ready evidence")
                if self.phase == "P4" and self.schedule is not None and self.schedule.inflight():
                    core._fail("cannot leave native phase with an unsettled reservation")
                self._check_snapshot_evidence(record["snapshots"], checkpoint)
                i = self.phases.index(self.phase)
                self.corrections, self.issues = 0, []
                if i + 1 == len(self.phases):
                    if data["next_challenge"] is not None:
                        core._fail("READY cannot issue another phase challenge")
                    self.status, self.challenge = "READY", None
                else:
                    self.phase = self.phases[i + 1]
                    self._nonce(data["next_challenge"])
        elif op == "correction":
            core._exact(data, {"phase", "challenge", "issues", "next_challenge"}, "utility correction")
            if self.status != "ACTIVE" or data["phase"] != self.phase or data["challenge"] != self.challenge:
                core._fail("correction outside current admission")
            self.corrections += 1
            self.issues = store._strings(data["issues"], "correction issues")
            if self.corrections > self.cfg["session"]["max_corrections_per_phase"]:
                if data["next_challenge"] is not None:
                    core._fail("exhausted correction cannot issue challenge")
                self.status, self.challenge = "FAIL", None
            else:
                self._nonce(data["next_challenge"])
        elif op == "answer":
            core._exact(data, {"question_id", "prompt_sha256", "next_challenge"}, "utility answer")
            if self.status != "WAITING_USER" or data["question_id"] != self.pending["id"]:
                core._fail("answer outside matching waiting state")
            refs = [r for r in record["snapshots"] if r["kind"] == "user_prompt"]
            if len(refs) != 1 or refs[0]["sha256"] != data["prompt_sha256"]:
                core._fail("answer has no observed prompt binding")
            prompt = self.blobs[refs[0]["sha256"]].decode("utf-8")
            decisions = [r for r in record["snapshots"] if r["kind"] == "answer_decision"]
            decision = self.blobs[decisions[0]["sha256"]] if len(decisions) == 1 else None
            if not self.answer_matches(prompt, decision):
                core._fail("recorded message did not resolve pending question")
            if decisions:
                self.gates[decisions[0]["source"]] = decisions[0]["sha256"]
            self.questions[self.pending["id"]] = data["prompt_sha256"]
            self.pending, self.status = None, "ACTIVE"
            self._nonce(data["next_challenge"])
        elif op == "native_admission":
            core._exact(data, {"attempt_id", "next_challenge"}, "native admission")
            if self.cfg["contract"]["workflow"] != "skill-validator" or self.phase != "P4" or self.status != "ACTIVE":
                core._fail("native admission requires active validator P4")
            if self.schedule is not None:
                core._fail("legacy native admission cannot bypass the bound schedule")
            if data["attempt_id"] in self.native_attempts:
                core._fail("native attempt was already allocated; writable state cannot be reused")
            refs = [ref for ref in record["snapshots"] if ref["kind"] == "native_plan"]
            if len(refs) != 1:
                core._fail("native admission lacks a complete frozen plan")
            plan, _ = utility_evidence.native_plan(self.blobs[refs[0]["sha256"]], self.cfg["session"]["task_id"], frozen=self.frozen)
            matches = [row for row in plan["attempts"] if row["attempt_id"] == data["attempt_id"]]
            if len(matches) != 1:
                core._fail("native attempt is outside the selected plan")
            if matches[0]["tier"] != "C":
                core._fail("B/A reservation requires authenticated preceding native results; result import is unavailable")
            gate_refs = [ref for ref in record["snapshots"] if ref["kind"] == "native_gate"]
            gate_spec = next((row for row in self.cfg["contract"]["gate_inputs"]
                              if row["id"] == "native-prerequisites" and row["phase"] == "P4"), None)
            if len(gate_refs) != 1 or gate_spec is None or gate_refs[0]["source"] != gate_spec["path"]:
                core._fail("native admission lacks selected protected prerequisite producer")
            gate_raw = self.blobs[gate_refs[0]["sha256"]]
            if core._json(gate_raw, "native prerequisite gate").get("outcome") != "PASS":
                core._fail("native admission cannot consume failed prerequisites")
            fixed = _gate(gate_raw, gate_spec, self.cfg, self)
            by_source = {(ref["kind"], ref["source"]): ref for ref in record["snapshots"]}
            for kind, path, raw in fixed:
                expected = by_source.get((kind, path))
                if expected is None or expected["sha256"] != store._hash(raw):
                    core._fail("native admission evidence snapshot missing or stale")
                self.gates[path] = expected["sha256"]
            self.gates[gate_spec["path"]] = gate_refs[0]["sha256"]
            self.native_attempts[data["attempt_id"]] = {"plan_sha256": refs[0]["sha256"], **matches[0]}
            self._nonce(data["next_challenge"])
        elif op == "native_schedule_bind":
            core._exact(data, {"next_challenge"}, "native schedule binding")
            if self.schedule is not None or self.native_attempts:
                core._fail("native schedule binding is exclusive and cannot reset prior reservations")
            spec, gate_raw, fixed, plan_ref, plan = _native_prerequisites(self)
            _, allocation_sources = _native_complete_allocation(self, plan)
            fixed.extend(allocation_sources)
            refs = [r for r in record["snapshots"] if r["kind"] == "schedule_binding"]
            if len(refs) != 1:
                core._fail("native schedule lacks its one frozen dependency binding")
            binding_path = store._absolute(refs[0]["source"], "native schedule binding path")
            roots = [self.cfg["project"], self.root, self.cfg["receipt"], Path(__file__).resolve().parent]
            roots.extend(store._absolute(a[key], "native attempt root") for a in plan["attempts"]
                         for key in ("workspace", "client_state"))
            if any(store._collide(binding_path, root) for root in roots):
                core._fail("native schedule binding overlaps worker/state/code/receipt scope")
            expected = {(kind, path): store._hash(raw) for kind, path, raw in fixed}
            expected[("native_gate", spec["path"])] = store._hash(gate_raw)
            expected[("schedule_binding", str(binding_path))] = refs[0]["sha256"]
            actual = {(r["kind"], r["source"]): r["sha256"] for r in record["snapshots"]}
            if actual != expected:
                core._fail("native schedule snapshots differ from the selected prerequisite evidence")
            options = {}
            if self.cfg["contract"]["schema_version"] == validation_policy.DELIVERY_SCHEMA:
                review_gate = next(g for g in self.cfg["contract"]["gate_inputs"] if g["id"] == "independent-review")
                review_ref = core._json(self.source(review_gate["path"], "admitted T04 gate"), "T04 gate")["evidence"][0]
                options = {"policy": self.cfg["validation_policy"], "review_ref": review_ref,
                           "delivery_ref": {"path": self.cfg["session"]["delivery_contract"],
                                            "sha256": store._hash(self.cfg["contract_raw"])}}
            kernel = utility_schedule.validate(self.blobs[refs[0]["sha256"]], plan, plan_ref,
                                               self.cfg["session"]["task_id"], **options)
            self.schedule = utility_schedule.JournalSchedule(kernel, store._stamp(record["at_utc"], "schedule origin"),
                                                             self.cfg["deadline"])
            self.schedule_plan, self.schedule_plan_ref = plan, plan_ref
            self.schedule_allocation = next({"path": path, "sha256": store._hash(raw)}
                                            for kind, path, raw in allocation_sources if kind == "native_allocation")
            for (_, path), digest in expected.items():
                self.gates[path] = digest
            self._nonce(data["next_challenge"])
        elif op == "native_schedule_reserve":
            core._exact(data, {"next_challenge"}, "native schedule reservation")
            if self.schedule is None or self.schedule_allocation is None or self.phase != "P4" or self.status != "ACTIVE":
                core._fail("native schedule reservation requires a complete bound allocation and active P4 schedule")
            self.schedule.reserve(store._stamp(record["at_utc"], "schedule reservation time"))
            self._nonce(data["next_challenge"])
        elif op == "native_schedule_cancel":
            core._exact(data, {"attempt_id", "reason"}, "native unlaunched cancellation")
            core._text(data["reason"], "cancellation reason")
            if self.schedule is None or self.phase != "P4":
                core._fail("cancellation requires a bound P4 schedule")
            if data["attempt_id"] in self.native_claims:
                core._fail("a claimed launch cannot be cancelled as unlaunched; authenticate process cleanup")
            self.schedule.cancel_unlaunched(data["attempt_id"], store._stamp(record["at_utc"], "cancellation time"))
        elif op in {"native_process_claim", "native_process_import", "native_result_review", "native_result_close"}:
            self._native_apply(record)
        elif op == "completion_intent":
            core._exact(data, {"receipt_sha256"}, "utility completion intent")
            if self.status != "READY" or self.intent is not None:
                core._fail("completion intent requires READY without prior intent")
            refs = [r for r in record["snapshots"] if r["kind"] == "receipt_intent"]
            if len(refs) != 1 or refs[0]["sha256"] != data["receipt_sha256"]:
                core._fail("completion intent has no exact receipt bytes")
            self.intent = self.blobs[refs[0]["sha256"]]
            self._receipt_content(self.intent)
        elif op == "completed":
            core._exact(data, {"receipt_sha256"}, "utility completed")
            if self.status != "READY" or self.intent is None or store._hash(self.intent) != data["receipt_sha256"]:
                core._fail("completed transition lacks matching prior receipt intent")
            self.receipt, self.status = data["receipt_sha256"], "COMPLETED"
        else:
            core._fail("unknown utility operation")
        self.sequence = record["sequence"]

    def native_inflight(self, attempt_id, *, cleanup=False):
        if (self.schedule is None or self.phase != "P4" or not cleanup and self.status != "ACTIVE"
                or not self.schedule.inflight() or self.schedule.state.reservations[-1].attempt_id != attempt_id):
            core._fail("native lifecycle requires the exact unsettled protected reservation")
        return self.schedule.state.reservations[-1]

    def native_binding(self, attempt_id):
        self.native_inflight(attempt_id)
        ref = self.reservation_refs[attempt_id]
        return {"task_id": self.cfg["session"]["task_id"], "attempt_id": attempt_id,
                "plan_sha256": self.schedule_plan_ref["sha256"], "schedule_binding": self.schedule.kernel.binding,
                "reservation_sha256": ref["sha256"], "challenge": ref["challenge"]}

    def _native_apply(self, record):
        op, data = record["operation"], record["data"]
        identity = data.get("attempt_id")
        row = self.native_inflight(identity, cleanup=op in {"native_process_import", "native_result_close"})
        stamp = store._stamp(record["at_utc"], "native lifecycle time")
        elapsed = (stamp - self.schedule.origin).total_seconds()
        if op == "native_process_claim":
            core._exact(data, {"attempt_id", "next_challenge"}, "native process claim")
            if identity in self.native_claims or elapsed >= row.deadline:
                core._fail("native launch claim is repeated or its original deadline expired")
            refs = [r for r in record["snapshots"] if r["kind"] == "native_launch_request"]
            if len(refs) != 1:
                core._fail("native launch claim lacks the frozen host launch request")
            request = core._json(self.blobs[refs[0]["sha256"]], "native launch request")
            if any(request["binding"].get(k) != v for k, v in self.native_binding(identity).items()):
                core._fail("native launch request differs from original reservation/plan/challenge")
            allocation_refs = [r for r in record["snapshots"] if r["kind"] == "native_allocation"]
            if len(allocation_refs) != 1:
                core._fail("native launch claim lacks complete immutable call allocation")
            allocation = core._json(self.blobs[allocation_refs[0]["sha256"]], "complete native allocation")
            selected = [a for a in allocation["required_calls"] if a["attempt_id"] == identity]
            if len(selected) != 1:
                core._fail("native launch lacks selected independent/operator review authority")
            self.native_claims[identity] = {"request": request, "allocation": selected[0], "request_sha256": refs[0]["sha256"]}
            for ref in record["snapshots"]:
                if ref["kind"] != "native_launch_request":
                    self.gates[ref["source"]] = ref["sha256"]
            self._nonce(data["next_challenge"])
            return
        claim = self.native_claims.get(identity)
        if claim is None:
            core._fail("native result has no protected one-use launch claim")
        if op == "native_process_import":
            core._exact(data, {"attempt_id", "source_intact"}, "native process import")
            if type(data["source_intact"]) is not bool or identity in self.native_imports:
                core._fail("native process receipt is replayed or source identity is malformed")
            refs = [r for r in record["snapshots"] if r["kind"] == "native_process_receipt"]
            if len(refs) != 1:
                core._fail("native result import requires one authenticated collector receipt")
            ref = refs[0]
            path = store._absolute(ref["source"], "native process receipt")
            authority = self.root / "native-collector"
            if not core._within(path, authority):
                core._fail("native receipt is outside the selected host collector authority")
            body = _native_collector().verify_receipt(authority, path, claim["request"]["binding"])
            if store._hash(store._external(path, LIMIT, "authenticated native receipt")) != ref["sha256"]:
                core._fail("native receipt differs from protected imported bytes")
            if body.get("provenance") != "OWNED_NATIVE_COLLECTOR":
                core._fail("fixture or foreign receipt cannot establish native execution")
            process = body["process"]
            started, finished = process.get("started_elapsed_seconds"), process.get("finished_elapsed_seconds")
            if (any(type(value) not in (int, float) or not math.isfinite(value) for value in (started, finished))
                    or not row.reserved_at <= started <= finished <= elapsed
                    or process.get("deadline_elapsed_seconds") != row.deadline
                    or body.get("campaign_origin_utc") != self.schedule.origin.isoformat()
                    or body.get("request_sha256") != _native_collector().digest(
                        _native_collector().canonical_json(claim["request"]))):
                core._fail("native process timing/request differs from its original reservation and import clock")
            if process.get("leader_reaped") is not True or process.get("group_absent") is not True:
                core._fail("native process cleanup is unproven; reservation remains unsettled")
            eligible = (_native_grade_eligible(body, claim["allocation"].get("managed_worker_required", True))
                        and data["source_intact"] and elapsed < row.deadline
                        and self.status == "ACTIVE" and stamp < self.cfg["deadline"])
            self.native_imports[identity] = {"path": str(path), "sha256": ref["sha256"], "eligible": eligible,
                                             "body": body}
            if not eligible:
                outcome = "TIMED_OUT" if elapsed >= row.deadline or process.get("status") == "TIMED_OUT" else "COULD_NOT_RUN"
                integrity = "CONTAMINATED" if not data["source_intact"] or body["freshness"].get("status") != "INTACT" else "UNOBTAINABLE"
                self.schedule.record(identity, outcome, integrity, stamp)
            return
        imported = self.native_imports.get(identity)
        if imported is None or not imported["eligible"]:
            core._fail("native review/closure requires a prior authenticated intact process receipt")
        if op == "native_result_close":
            core._exact(data, {"attempt_id", "reason"}, "native result closure")
            core._text(data["reason"], "native result closure reason")
            self.schedule.record(identity, "TIMED_OUT" if elapsed >= row.deadline else "COULD_NOT_RUN", "UNOBTAINABLE", stamp)
            return
        core._exact(data, {"attempt_id"}, "native independent review")
        refs = [r for r in record["snapshots"] if r["kind"] == "native_review"]
        selected = claim["allocation"]
        if len(refs) != 1 or refs[0]["source"] != selected["review_path"]:
            core._fail("native result review must use its frozen selected external reviewer path")
        review = utility_evidence.semantic_review(self.blobs[refs[0]["sha256"]], task_id=self.cfg["session"]["task_id"],
                                                 attempt_id=identity, receipt_sha256=imported["sha256"],
                                                 binding=claim["request"]["binding"], reviewer=selected["reviewer"])
        expected = {ref["path"]: ref["sha256"] for ref in review["evidence"]}
        actual = {ref["source"]: ref["sha256"] for ref in record["snapshots"] if ref["kind"] == "native_review_evidence"}
        if expected != actual or len(expected) != len(review["evidence"]):
            core._fail("native review evidence must have exact independent pinned snapshot coverage")
        self.schedule.record(identity, review["outcome"], "INTACT", stamp)
        self.native_reviews[identity] = {"path": refs[0]["source"], "sha256": refs[0]["sha256"], "outcome": review["outcome"]}
        for ref in record["snapshots"]:
            self.gates[ref["source"]] = ref["sha256"]

    def check_native_gate(self, spec, value):
        tier = spec["id"].removeprefix("native-")
        attempts = [a.attempt_id for a in self.schedule.kernel.attempts if a.tier == tier] if self.schedule else []
        if not attempts or any(a not in self.native_reviews for a in attempts):
            core._fail("native tier outcome lacks exact authenticated result importer coverage and independent reviews")
        expected = [{"path": row["path"], "sha256": row["sha256"]}
                    for identity in attempts for row in (self.native_imports[identity], self.native_reviews[identity])]
        if value["evidence"] != expected:
            core._fail("native gate evidence differs from complete imported tier receipt/review coverage")
        outcome = "PASS" if all(self.native_reviews[a]["outcome"] == "PASS" for a in attempts) else "FAIL"
        if value["outcome"] != outcome:
            core._fail("native gate outcome disagrees with separately imported independent reviews")

    def _check_snapshot_evidence(self, snapshots, checkpoint):
        by_kind = {(r["kind"], r["source"]): r for r in snapshots}
        evidence = {r["id"]: r for r in checkpoint["evidence"]}
        for spec in self.cfg["contract"]["outputs"]:
            if spec["phase"] != self.phase:
                continue
            row = evidence[spec["id"]]
            target = self.cfg["project"] / spec["path"]
            ref = by_kind.get(("output", str(target)))
            if row["path"] != str(target) or ref is None or row["sha256"] != ref["sha256"]:
                core._fail("output evidence path/bytes differs from selected artifact")
            for kind, path, raw in _structured(self.blobs[ref["sha256"]], spec, self.cfg, self) or []:
                proof = by_kind.get((kind, path))
                if proof is None or proof["sha256"] != store._hash(raw):
                    core._fail("validation result evidence snapshot missing or stale")
                self.gates[path] = proof["sha256"]
            baseline = self.cfg["baselines"][spec["path"]]
            if ref["sha256"] == baseline["sha256"] and not baseline["allow_unchanged"]:
                core._fail("unchanged output is not permitted by this allocation")
            self.accepted[spec["path"]] = ref["sha256"]
        for spec in self.cfg["contract"]["gate_inputs"]:
            if spec["phase"] != self.phase:
                continue
            ref = by_kind.get(("gate", spec["path"]))
            if ref is None:
                core._fail("allocated producer gate evidence snapshot missing")
            for kind, path, raw in _gate(self.blobs[ref["sha256"]], spec, self.cfg, self):
                underlying = by_kind.get((kind, path))
                if underlying is None or underlying["sha256"] != store._hash(raw):
                    core._fail("underlying gate evidence snapshot missing")
                self.gates[path] = underlying["sha256"]
            self.gates[spec["path"]] = ref["sha256"]
            self.gate_outcomes[spec["id"]] = core._json(self.blobs[ref["sha256"]], "gate outcome")["outcome"]

    def answer_matches(self, prompt, decision):
        if self.pending["choices"]:
            return prompt.strip().casefold() in {c.strip().casefold() for c in self.pending["choices"]}
        if decision is None:
            return False
        value = core._json(decision, "authoritative answer interpretation")
        core._exact(value, {"schema_version", "task_id", "question_id", "challenge", "prompt_sha256", "resolved", "reason"}, "answer interpretation")
        core._text(value["reason"], "answer interpretation reason")
        return (value["schema_version"] == "devforge.utility-answer/v1" and value["task_id"] == self.cfg["session"]["task_id"]
                and value["question_id"] == self.pending["id"] and value["challenge"] == self.challenge
                and value["prompt_sha256"] == store._hash(prompt.encode()) and value["resolved"] is True)

    def append(self, operation, data, sources=()):
        now = store._now()
        if now < self.last_time:
            core._fail("utility operation clock moved behind its journal high-water mark")
        if operation not in {"native_schedule_cancel", "native_process_import", "native_result_close"}:
            store._before_deadline(now, self.cfg["deadline"])
        if len(self.head["records"]) >= MAX_RECORDS:
            core._fail("utility journal budget exhausted")
        refs = [store._snapshot(self.fd, kind, source, raw) for kind, source, raw in sources]
        for ref, (_, _, raw) in zip(refs, sources):
            self.blobs[ref["sha256"]] = raw
        record = {"sequence": len(self.head["records"]), "previous": self.head["records"][-1],
                  "at_utc": now.isoformat(), "operation": operation, "data": data, "snapshots": refs}
        # Replay prospective operation before committing; no self-declared state.
        self._apply(record)
        self.last_time = now
        raw = store._dump(record)
        digest = store._hash(raw)
        store._publish(self.fd, "records/" + digest + ".json", raw)
        self.head["records"].append(digest)
        store._publish(self.fd, "HEAD.json", store._dump(self.head), replace=True)
        self._retain_reservation_ref(record, digest)

    def _receipt_content(self, raw):
        value = core._json(raw, "utility receipt")
        core._exact(value, {"schema_version", "task_id", "session_sha256", "contract_sha256", "project_root",
                           "outputs", "gate_inputs", "gate_outcomes", "accepted_phases", "receipt_path", "completed_checks_at_utc", "scope",
                           "receipt_publication", "receipt_readback", "receiving_invocation"}, "utility receipt")
        if (value["schema_version"] != RECEIPT_SCHEMA or value["task_id"] != self.cfg["session"]["task_id"]
                or value["session_sha256"] != self.manifest["session_sha256"]
                or value["contract_sha256"] != store._hash(self.cfg["contract_raw"])
                or value["project_root"] != str(self.cfg["project"]) or value["outputs"] != self.accepted
                or value["gate_inputs"] != self.gates or value["gate_outcomes"] != self.gate_outcomes or value["accepted_phases"] != self.phases
                or value["receipt_path"] != str(self.cfg["receipt"]) or value["scope"] != SCOPE
                or value["receipt_publication"] != "NOT_RUN" or value["receipt_readback"] != "NOT_RUN"
                or value["receiving_invocation"] != "NOT_OBSERVED"):
            core._fail("utility receipt does not bind accepted state")
        store._stamp(value["completed_checks_at_utc"], "receipt creation time")

    def view(self, status=None):
        result = _result(status or self.status, task_id=self.cfg["session"]["task_id"], phase=self.phase,
                         workflow=self.cfg["contract"]["workflow"], sequence=self.sequence, corrections=self.corrections,
                         gate_outcomes=self.gate_outcomes, native_execution="NOT_ESTABLISHED_BY_RECEIPT",
                         deadline_utc=self.cfg["session"]["deadline_utc"], terminal=self.status == "FAIL", issues=self.issues,
                         phase_applicability={p["id"]: "REQUIRED" if p["applicable"] else "OWNER_EXCLUDED_OPTIONAL"
                                              for p in self.cfg["contract"]["phases"]})
        if self.status in {"ACTIVE", "WAITING_USER"}:
            result.update(challenge=self.challenge, inputs_sha256=store._hash(self.cfg["contract_raw"]))
        if self.pending:
            result.update(question=self.pending["question"], question_id=self.pending["id"], blocking_dependency=self.pending["blocking_dependency"])
        if self.schedule is not None:
            result["native_schedule"] = self.schedule.view(store._now())
            result["native_schedule"]["complete_allocation"] = self.schedule_allocation
            if self.native_claims:
                result["native_execution"] = "SEE_AUTHENTICATED_PROCESS_RECEIPTS" if self.native_imports else "NOT_ESTABLISHED_BY_RECEIPT"
                result["native_schedule"].update(
                    execution="AUTHENTICATED_PROCESS_IMPORTED" if self.native_imports else "LAUNCH_CLAIMED",
                    launch_claims=sorted(self.native_claims), imported_attempts=sorted(self.native_imports),
                    reviewed_attempts=sorted(self.native_reviews), native_callback_authentication="NOT_EVALUATED",
                    rendered_delivery="NOT_OBSERVED", receiving_execution="NOT_OBSERVED",
                    semantic_review_quality="NOT_EVALUATED")
        if self.status == "COMPLETED":
            result.update(receipt_path=str(self.cfg["receipt"]), receipt_sha256=self.receipt,
                          receipt_published=True, receipt_readback=True, receipt_verified=True)
        return result


@_guard
def start(session_contract, state_root):
    path, root = store._absolute(session_contract, "session"), store._absolute(state_root, "state")
    if root.exists() or root.is_symlink():
        core._fail("utility state initialization is exclusive")
    cfg = _configuration(path, root, initial=True)
    store._before_deadline(store._now(), cfg["deadline"])
    with core._directory(root.parent, "state parent") as parent:
        os.mkdir(root.name, 0o700, dir_fd=parent)
        os.fsync(parent)
    with core._directory(root, "new utility state") as fd:
        for directory in ("records", "snapshots", "pending"):
            os.mkdir(directory, 0o700, dir_fd=fd)
        store._new_at(fd, "LOCK", b"")
    with store._lock(root) as fd:
        refs = [store._snapshot(fd, kind, source, raw) for kind, source, raw in cfg["fixed"]]
        with core._directory(cfg["project"], "utility project") as project_fd:
            for rel, raw in cfg["preimages"].items():
                archive = cfg["baselines"][rel]["archive"]
                refs.append(store._snapshot(fd, "preimage", rel, raw))
                store._mkdir_chain(project_fd, str(Path(archive).parent) if Path(archive).parent != Path('.') else '')
                prior = store._read_at(project_fd, archive, LIMIT, "output archive", optional=True)
                if prior is None:
                    with core._parent(project_fd, archive, "archive", "FAIL") as (parent, name):
                        store._new_at(parent, name, raw)
                elif prior != raw:
                    core._fail("preimage archive collision")
        if core._within(cfg["receipt"], root):
            store._mkdir_chain(fd, str(cfg["receipt"].relative_to(root).parent) if cfg["receipt"].parent != root else '')
        again = _configuration(path, root, initial=True)
        pins = lambda c: {(kind, source): store._hash(raw) for kind, source, raw in c["fixed"]}
        if pins(cfg) != pins(again) or cfg["preimages"] != again["preimages"]:
            core._fail("utility inputs changed during admission")
        now = store._now()
        store._before_deadline(now, cfg["deadline"])
        manifest = {"schema_version": STATE_SCHEMA, "state_root": str(root), "session_path": str(path),
                    "session_sha256": store._hash(cfg["raw"]), "task_id": cfg["session"]["task_id"],
                    "project_root": str(cfg["project"]), "started_at_utc": now.isoformat(), "snapshots": refs}
        manifest_raw = store._dump(manifest)
        store._publish(fd, "MANIFEST.json", manifest_raw)
        record = {"sequence": 0, "previous": None, "at_utc": now.isoformat(), "operation": "start",
                  "data": {"challenge": store._fresh()}, "snapshots": []}
        record_raw = store._dump(record)
        digest = store._hash(record_raw)
        store._publish(fd, "records/" + digest + ".json", record_raw)
        store._publish(fd, "HEAD.json", store._dump({"schema_version": "devforge.utility-head/v1",
                       "manifest_sha256": store._hash(manifest_raw), "records": [digest]}))
        return State(root, fd).view()


@_guard
def context(state_root):
    root = store._absolute(state_root, "state")
    with store._lock(root) as fd:
        state = State(root, fd)
        if state.status == "COMPLETED":
            _verify_receipt(state)
        result = state.view()
        expired = store._now() >= state.cfg["deadline"]
        result.update(expired=expired, admitted=state.status == "ACTIVE" and not expired)
        if expired and state.status != "COMPLETED":
            result.update(status="COULD_NOT_RUN", issues=["original deadline expired"])
            result.pop("challenge", None)
        return result


@_guard
def advance(state_root):
    root = store._absolute(state_root, "state")
    with store._lock(root) as fd:
        state = State(root, fd)
        store._before_deadline(store._now(), state.cfg["deadline"])
        if state.status != "ACTIVE":
            return state.view()
        try:
            with core._directory(state.cfg["project"], "checkpoint project") as project_fd:
                raw = store._read_at(project_fd, state.cfg["session"]["checkpoint_path"], store.CHECKPOINT_LIMIT, "utility checkpoint")
            checkpoint = state.checkpoint(raw)
            sources = [("checkpoint", str(state.cfg["project"] / state.cfg["session"]["checkpoint_path"]), raw)]
            if checkpoint["state"] == "awaiting_user":
                state.append("waiting", {"next_challenge": None}, sources)
                return state.view()
            by_id = {r["id"]: r for r in checkpoint["evidence"]}
            for spec in state.cfg["contract"]["outputs"]:
                if spec["phase"] == state.phase:
                    with core._directory(state.cfg["project"], "output project") as project_fd:
                        value = store._read_at(project_fd, spec["path"], LIMIT, "utility phase output")
                    sources.extend(_structured(value, spec, state.cfg, state) or [])
                    if by_id[spec["id"]] != {"id": spec["id"], "path": str(state.cfg["project"] / spec["path"]), "sha256": store._hash(value)}:
                        core._fail("checkpoint output identity differs from actual bytes")
                    sources.append(("output", str(state.cfg["project"] / spec["path"]), value))
            for spec in state.cfg["contract"]["gate_inputs"]:
                if spec["phase"] == state.phase:
                    value = store._external(Path(spec["path"]), LIMIT, "utility gate input")
                    sources.append(("gate", spec["path"], value))
                    sources.extend(_gate(value, spec, state.cfg, state))
            # Snapshots are checked before the atomic HEAD commit; later admissions recheck current bytes.
            state.append("accepted", {"next_challenge": None if state.phase == state.phases[-1] else store._fresh(state)}, sources)
            return state.view("READY" if state.status == "READY" else "PROGRESS")
        except core._Problem as error:
            if error.result != "FAIL":
                raise
            if state.cfg.get("validation_policy") is not None:
                result = state.view("FAIL")
                result["issues"] = [error.issue]
                return result
            # Reconstruct after a failed prospective replay; no partial state is authoritative.
            state = State(root, fd)
            state.append("correction", {"phase": state.phase, "challenge": state.challenge,
                         "issues": [error.issue], "next_challenge": store._fresh(state)
                         if state.corrections < state.cfg["session"]["max_corrections_per_phase"] else None})
            return state.view("FAIL")


@_guard
def resume(state_root, prompt=None):
    root = store._absolute(state_root, "state")
    with store._lock(root) as fd:
        state = State(root, fd)
        store._before_deadline(store._now(), state.cfg["deadline"])
        if state.status != "WAITING_USER":
            return state.view()
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > store.CHECKPOINT_LIMIT:
            return state.view()
        decision = None
        sources = [("user_prompt", state.pending["id"], prompt.encode())]
        if state.pending["decision_path"] is not None:
            decision = store._external(Path(state.pending["decision_path"]), LIMIT, "answer interpretation", optional=True)
        if not state.answer_matches(prompt, decision):
            return state.view()
        if decision is not None:
            sources.append(("answer_decision", state.pending["decision_path"], decision))
        state.append("answer", {"question_id": state.pending["id"], "prompt_sha256": store._hash(prompt.encode()),
                               "next_challenge": store._fresh(state)}, sources)
        return state.view()


def _verify_receipt(state):
    actual = store._external(state.cfg["receipt"], LIMIT, "utility receipt readback")
    if actual != state.intent or state.receipt is not None and store._hash(actual) != state.receipt:
        core._fail("utility receipt differs from protected publication intent")
    state._receipt_content(actual)
    return actual


@_guard
def complete(state_root):
    root = store._absolute(state_root, "state")
    with store._lock(root) as fd:
        state = State(root, fd)
        if state.status == "COMPLETED":
            _verify_receipt(state)
            return state.view()
        store._before_deadline(store._now(), state.cfg["deadline"])
        if state.status != "READY":
            core._fail("utility completion requires every applicable phase's current evidence")
        if set(state.accepted) != set(state.cfg["outputs"]):
            core._fail("utility final output coverage incomplete")
        if state.intent is None:
            receipt = {"schema_version": RECEIPT_SCHEMA, "task_id": state.cfg["session"]["task_id"],
                       "session_sha256": state.manifest["session_sha256"], "contract_sha256": store._hash(state.cfg["contract_raw"]),
                       "project_root": str(state.cfg["project"]), "outputs": state.accepted, "gate_inputs": state.gates,
                       "gate_outcomes": state.gate_outcomes, "accepted_phases": state.phases, "receipt_path": str(state.cfg["receipt"]),
                       "completed_checks_at_utc": store._now().isoformat(), "scope": SCOPE,
                       "receipt_publication": "NOT_RUN", "receipt_readback": "NOT_RUN", "receiving_invocation": "NOT_OBSERVED"}
            raw = store._dump(receipt)
            state.append("completion_intent", {"receipt_sha256": store._hash(raw)},
                         [("receipt_intent", str(state.cfg["receipt"]), raw)])
        prior = store._external(state.cfg["receipt"], LIMIT, "utility receipt", optional=True)
        if prior is None:
            with core._directory(state.cfg["receipt"].parent, "receipt parent") as parent:
                store._new_at(parent, state.cfg["receipt"].name, state.intent)
        _verify_receipt(state)
        # Reopen all current inputs/outputs after publication before reporting completion.
        state = State(root, fd)
        _verify_receipt(state)
        state.append("completed", {"receipt_sha256": store._hash(state.intent)})
        return State(root, fd).view()


@_guard
def native_admission(state_root, attempt_id):
    """Reserve a complete attempt allocation. This operation never starts a client."""
    root = store._absolute(state_root, "state")
    core._text(attempt_id, "native attempt ID")
    with store._lock(root) as fd:
        state = State(root, fd)
        if state.schedule is not None:
            core._fail("legacy native admission cannot bypass the bound schedule")
        store._before_deadline(store._now(), state.cfg["deadline"])
        if state.cfg["contract"]["workflow"] != "skill-validator" or state.phase != "P4" or state.status != "ACTIVE":
            core._fail("native admission requires prior validator intake/static/review phases and active P4")
        for identity, phase in (("deterministic-inspection", "P2"), ("independent-review", "P3")):
            prior = next((row for row in state.cfg["contract"]["gate_inputs"]
                          if row["id"] == identity and row["phase"] == phase), None)
            if prior is None or prior["path"] not in state.gates:
                core._fail("native admission lacks required prior independent gate input " + identity)
            gate_raw = store._external(Path(prior["path"]), LIMIT, "prior native gate")
            if core._json(gate_raw, "prior native gate").get("outcome") != "PASS":
                core._fail("prior native gate remains failed or unavailable: " + identity)
        spec = next((row for row in state.cfg["contract"]["gate_inputs"]
                     if row["id"] == "native-prerequisites" and row["phase"] == "P4"), None)
        if spec is None:
            core._fail("native admission needs a selected external prerequisites producer")
        raw = store._external(Path(spec["path"]), LIMIT, "native prerequisite gate")
        value = core._json(raw, "native prerequisite gate")
        if value.get("outcome") != "PASS":
            core._fail("native prerequisites remain unresolved; reporting can continue separately")
        fixed = _gate(raw, spec, state.cfg)
        plans = [(path, data) for kind, path, data in fixed if kind == "gate_evidence"]
        if len(plans) != 1:
            core._fail("native plan binding is missing or ambiguous")
        path, plan_raw = plans[0]
        state.append("native_admission", {"attempt_id": attempt_id, "next_challenge": store._fresh(state)},
                     [("native_plan", path, plan_raw), ("native_gate", spec["path"], raw), *fixed])
        return _result("NATIVE_ADMISSION_RECORDED", task_id=state.cfg["session"]["task_id"],
                       attempt=state.native_attempts[attempt_id], phase=state.phase,
                       native_launch_admitted=False, execution="NOT_RUN",
                       deadline_utc=state.cfg["session"]["deadline_utc"])


@_guard
def native_schedule_bind(state_root, schedule_path):
    """Bind a complete plan/dependency map once, without launching a client."""
    root = store._absolute(state_root, "state")
    path = store._absolute(schedule_path, "native schedule binding")
    with store._lock(root) as fd:
        state = State(root, fd)
        if state.schedule is not None or state.native_attempts:
            core._fail("native schedule binding is exclusive and cannot reset prior reservations")
        spec, raw, fixed, _, plan = _native_prerequisites(state)
        _, allocation_sources = _native_complete_allocation(state, plan)
        fixed.extend(allocation_sources)
        binding_raw = store._external(path, LIMIT, "native schedule binding")
        state.append("native_schedule_bind", {"next_challenge": store._fresh(state)},
                     [("schedule_binding", str(path), binding_raw), ("native_gate", spec["path"], raw), *fixed])
        return State(root, fd).view("NATIVE_SCHEDULE_BOUND")


@_guard
def native_schedule_reserve(state_root):
    """Journal one nonlaunching reservation/decision using the original clock."""
    root = store._absolute(state_root, "state")
    with store._lock(root) as fd:
        state = State(root, fd)
        state.append("native_schedule_reserve", {"next_challenge": store._fresh(state)})
        return State(root, fd).view("NATIVE_SCHEDULE_RECORDED")


@_guard
def native_schedule_cancel(state_root, attempt_id, reason):
    """Cancel only an unlaunched reservation; accepts no native grade/receipt."""
    root = store._absolute(state_root, "state")
    core._text(attempt_id, "native cancellation attempt ID")
    core._text(reason, "native cancellation reason")
    with store._lock(root) as fd:
        state = State(root, fd)
        state.append("native_schedule_cancel", {"attempt_id": attempt_id, "reason": reason})
        return State(root, fd).view("NATIVE_UNLAUNCHED_CANCELLED")


def _native_collector():
    try:
        from . import native_process
    except ImportError:
        import native_process
    return native_process


def _native_grade_eligible(body, managed_worker_required=False):
    """Pure evidence-shape predicate; this authenticates no receipt or provenance."""
    process = body["process"]
    worker = body.get("managed_worker", {})
    if managed_worker_required and (worker.get("required") is not True or worker.get("status") != "COMPLETED"
                                    or worker.get("broker_quiescent") is not True
                                    or not isinstance(worker.get("task_result"), dict)
                                    or worker["task_result"].get("receipt_verified") is not True):
        return False
    return (process.get("status") == "EXITED" and (process.get("exit_code") == 0
            or "interactive" in body and process.get("protocol_completed_before_owned_shutdown") is True
            and process.get("exit_code") in {-15, -9})
            and process.get("leader_reaped") is True and process.get("group_absent") is True
            and process.get("stdout_complete") is True and process.get("stderr_complete") is True
            and process.get("output_limit_exceeded") is False and body["events"].get("status") == "OBSERVED"
            and body["freshness"].get("status") == "INTACT")


def _native_protected(state, path, label, *, plan=None):
    path = store._absolute(path, label)
    roots = [state.cfg["project"], state.root, state.cfg["receipt"], Path(__file__).resolve().parent]
    roots.extend(Path(a[key]) for a in (plan or state.schedule_plan)["attempts"] for key in ("workspace", "client_state"))
    if any(store._collide(path, root) for root in roots):
        core._fail(f"{label}: must remain outside all worker/state/code/receipt roots")
    return path


@_guard
def native_process_launch(state_root, attempt_id):
    """Claim once while locked; launch the selected frozen configuration outside it.

    Crash/resume never retries this operation. A claim without a process receipt
    remains unavailable until host ownership and cleanup can be authenticated.
    """
    root = store._absolute(state_root, "state")
    core._text(attempt_id, "native launch attempt ID")
    with store._lock(root) as fd:
        state = State(root, fd)
        row = state.native_inflight(attempt_id)
        if attempt_id in state.native_claims:
            core._fail("native launch is already claimed; resume cannot relaunch an attempt")
        store._before_deadline(store._now(), state.cfg["deadline"])
        _, _, _, plan_ref, plan = _native_prerequisites(state)
        if plan_ref != state.schedule_plan_ref:
            core._fail("native plan changed after scheduling")
        _, sources = _native_complete_allocation(state, plan)
        allocation_ref = next({"path": path, "sha256": store._hash(raw)}
                              for kind, path, raw in sources if kind == "native_allocation")
        if allocation_ref != state.schedule_allocation:
            core._fail("native launch allocation differs from its complete campaign binding")
        attempt = next(a for a in plan["attempts"] if a["attempt_id"] == attempt_id)
        request = _native_collector().prepare_request(
            plan, attempt, state.native_binding(attempt_id), row.reserved_at, row.deadline,
            state.schedule.origin.isoformat(), state.cfg["session"]["installed_inputs"])
        request_raw = store._dump(request)
        state.append("native_process_claim", {"attempt_id": attempt_id, "next_challenge": store._fresh(state)},
                     [("native_launch_request", str(root / "native-launch-requests" / attempt_id), request_raw), *sources])
        origin = state.schedule.origin

    def check_reservation(candidate):
        with store._lock(root) as fd:
            current = State(root, fd)
            current.native_inflight(attempt_id)
            if candidate != current.native_claims[attempt_id]["request"] or attempt_id in current.native_imports:
                core._fail("collector launch differs from current one-use protected claim")
            store._before_deadline(store._now(), current.cfg["deadline"])
            if (store._now() - origin).total_seconds() >= row.deadline:
                core._fail("native launch deadline expired before actual process creation")
            utility_evidence.native_plan(current.source(plan_ref["path"], "native launch plan"), plan["task_id"])

    receipt = _native_collector().launch(root / "native-collector", request,
                                       check_reservation=check_reservation,
                                       elapsed_seconds=lambda: (store._now() - origin).total_seconds())
    return native_process_import(root, attempt_id, receipt)


@_guard
def native_process_import(state_root, attempt_id, receipt_path):
    """Import authenticated raw completion; drift/expiry allow cleanup only."""
    root = store._absolute(state_root, "state")
    path = store._absolute(receipt_path, "native receipt")
    with store._lock(root) as fd:
        state = State(root, fd, cleanup=True)
        state.native_inflight(attempt_id, cleanup=True)
        if not core._within(path, root / "native-collector"):
            core._fail("native receipt is outside selected host collector authority")
        source_intact = True
        try:
            current = State(root, fd)
            utility_evidence.native_plan(current.source(current.schedule_plan_ref["path"], "native plan"),
                                         current.cfg["session"]["task_id"])
        except (core._Problem, OSError):
            source_intact = False
        raw = store._external(path, LIMIT, "native process receipt")
        state.append("native_process_import", {"attempt_id": attempt_id, "source_intact": source_intact},
                     [("native_process_receipt", str(path), raw)])
        return state.view("NATIVE_PROCESS_IMPORTED" if state.native_imports[attempt_id]["eligible"] else "NATIVE_CLEANUP_RECORDED")


@_guard
def native_result_review(state_root, attempt_id, review_path):
    """Consume the frozen external reviewer path, never a worker grade argument."""
    root = store._absolute(state_root, "state")
    path = store._absolute(review_path, "native review")
    with store._lock(root) as fd:
        state = State(root, fd)
        state.native_inflight(attempt_id)
        claim = state.native_claims.get(attempt_id)
        if claim is None or str(path) != claim["allocation"]["review_path"]:
            core._fail("native review path differs from frozen independent/operator selection")
        _native_protected(state, path, "native independent review")
        raw = store._external(path, LIMIT, "native independent review")
        value = core._json(raw, "native review")
        sources = [("native_review", str(path), raw)]
        for ref in _list(value.get("evidence"), "native review evidence", nonempty=True):
            if isinstance(ref, dict) and "path" in ref:
                _native_protected(state, ref["path"], "native independent review evidence")
            evidence_path, evidence_raw = _pin(ref, "native independent review evidence")
            _native_protected(state, evidence_path, "native independent review evidence")
            sources.append(("native_review_evidence", str(evidence_path), evidence_raw))
        state.append("native_result_review", {"attempt_id": attempt_id}, sources)
        return state.view("NATIVE_REVIEW_RECORDED")


@_guard
def native_result_close(state_root, attempt_id, reason):
    """Close a completed but ungraded attempt without granting any quality grade."""
    root = store._absolute(state_root, "state")
    with store._lock(root) as fd:
        state = State(root, fd, cleanup=True)
        state.append("native_result_close", {"attempt_id": attempt_id, "reason": reason})
        return state.view("NATIVE_UNGRADED_CLOSED")
