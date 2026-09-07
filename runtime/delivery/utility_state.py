"""Protected utility phases; substantive authoring and semantic review stay outside.

Only the external controller/supervisor may write this state. Shared no-follow,
locked, fsynced publication primitives are reused from the brainstorm runtime;
its session and checkpoint schemas are not reinterpreted as utility schemas.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

try:
    from . import delivery_core as core, phase_state as store, utility_evidence, utility_schedule
except ImportError:
    import delivery_core as core
    import phase_state as store
    import utility_evidence
    import utility_schedule

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
            return _result("FAIL", issues=[f"Malformed utility input/state: {type(error).__name__}"])
    call.__name__, call.__doc__ = function.__name__, function.__doc__
    return call


def _list(value, label, *, nonempty=False):
    if not isinstance(value, list) or len(value) > 256 or nonempty and not value:
        core._fail(f"{label}: expected bounded {'nonempty ' if nonempty else ''}array")
    return value


def _json_file(path, label, limit=LIMIT):
    raw = store._external(path, limit, label)
    return core._json(raw, label), raw


def _pin(entry, label):
    core._exact(entry, {"path", "sha256"}, label)
    path = store._absolute(entry["path"], label)
    core._digest(entry["sha256"], label)
    raw = store._external(path, LIMIT, label)
    if store._hash(raw) != entry["sha256"]:
        core._fail(f"{label}: selected bytes changed")
    return path, raw


def _nonoverlap(paths, label):
    for i, path in enumerate(paths):
        if any(store._collide(path, other) for other in paths[:i]):
            core._fail(f"{label}: colliding paths")


def _load_contract(path):
    path = store._absolute(path, "utility delivery contract")
    contract, raw = _json_file(path, "utility delivery contract")
    core._exact(contract, {"schema_version", "task_id", "project_root", "workflow", "mode",
                           "inputs", "outputs", "phases", "gate_inputs", "questions"}, "utility delivery contract")
    if contract["schema_version"] != DELIVERY_SCHEMA or contract["workflow"] not in PHASES:
        core._fail("unsupported utility delivery schema/workflow")
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
        if len(set(outcomes)) != len(outcomes) or not set(outcomes) <= {"PASS", "FAIL", "COULD_NOT_RUN", "NOT_RUN"}:
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


def protected_paths(contract):
    return [Path(row["path"]) for row in contract["gate_inputs"]] + [
        Path(row["decision_path"]) for row in contract["questions"] if row["decision_path"] is not None]


def _configuration(session_path, root, *, initial=False):
    session_path, root = store._absolute(session_path, "session"), store._absolute(root, "state")
    session, raw = _json_file(session_path, "utility session", store.SESSION_LIMIT)
    core._exact(session, store._SESSION_KEYS, "utility session")
    if session["schema_version"] != SESSION_SCHEMA or session["provider"] != "codex":
        core._fail("utility session requires selected Codex schema")
    if type(session["max_corrections_per_phase"]) is not int or not 0 <= session["max_corrections_per_phase"] <= 3:
        core._fail("correction budget must be an integer in [0,3]")
    deadline = store._stamp(session["deadline_utc"], "utility deadline")
    contract_path = store._absolute(session["delivery_contract"], "delivery path")
    contract, contract_raw, project = _load_contract(contract_path)
    if session["delivery_contract_sha256"] != store._hash(contract_raw) or session["task_id"] != contract["task_id"]:
        core._fail("utility session/delivery binding differs")
    assignment, assignment_raw = _pin(session["assignment"], "utility assignment")
    if core._within(assignment, project) or core._within(session_path, project):
        core._fail("utility authority must be outside worker project")
    fixed = [("session", str(session_path), raw), ("delivery", str(contract_path), contract_raw),
             ("assignment", str(assignment), assignment_raw)]
    installed_paths = []
    for entry in _list(session["installed_inputs"], "installed inputs", nonempty=True):
        path, value = _pin(entry, "installed input")
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
                 *[project / row["path"] for row in contract["inputs"]], *protected_paths(contract)]
    _nonoverlap([*mutable, *immutable], "utility mutable/fixed selection")
    if any(store._collide(root, p) for p in [project, *immutable, Path(__file__).resolve().parent]):
        core._fail("utility state overlaps worker/code/input scope")
    if any(store._collide(receipt, p) for p in [project, *immutable, Path(__file__).resolve().parent]):
        core._fail("utility receipt overlaps worker/code/input scope")
    if core._within(receipt, root):
        if receipt.relative_to(root).parts[0] in {"LOCK", "MANIFEST.json", "HEAD.json", "records", "snapshots", "pending"}:
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
            value = store._read_at(fd, row["path"], LIMIT, "utility fixed input")
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
            if row["archive"] is not None:
                value = store._read_at(fd, row["archive"], LIMIT, "output archive", optional=initial)
                if value is not None and store._hash(value) != row["sha256"]:
                    core._fail("output preimage archive changed")
    if initial and store._external(receipt, LIMIT, "receipt", optional=True) is not None:
        core._fail("receipt already exists before admission")
    return {"session": session, "raw": raw, "contract": contract, "contract_raw": contract_raw,
            "project": project, "receipt": receipt, "deadline": deadline, "fixed": fixed,
            "preimages": preimages, "baselines": baselines, "outputs": outputs}


def _structured(raw, spec):
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


def _gate(raw, spec, cfg):
    value = core._json(raw, "external utility gate input")
    core._exact(value, {"schema_version", "task_id", "phase", "producer", "inputs_sha256",
                        "outcome", "reason", "evidence"}, "external utility gate input")
    if (value["schema_version"] != "devforge.utility-gate-input/v1"
            or value["task_id"] != cfg["session"]["task_id"] or value["phase"] != spec["phase"]
            or value["producer"] != spec["producer"] or value["inputs_sha256"] != store._hash(cfg["contract_raw"])):
        core._fail("gate input has wrong task/phase/producer/selected input binding")
    if value["outcome"] not in spec["allowed_outcomes"]:
        core._fail("gate outcome does not admit the selected dependent action")
    if spec["id"] in {"native-C", "native-B", "native-A"} and value["outcome"] in {"PASS", "FAIL"}:
        core._fail("native executed outcomes require an authenticated result importer, which is not implemented")
    core._text(value["reason"], "gate scope/reason")
    evidence = []
    for ref in _list(value["evidence"], "gate underlying evidence", nonempty=True):
        path, evidence_raw = _pin(ref, "gate underlying evidence")
        if str(path) == spec["path"]:
            core._fail("gate input cannot cite itself as underlying evidence")
        evidence.append(("gate_evidence", str(path), evidence_raw))
    if spec["id"] == "native-prerequisites" and value["outcome"] == "PASS":
        plans = [(path, data) for _, path, data in evidence]
        if len(plans) != 1:
            core._fail("native prerequisite PASS requires exactly one complete frozen native plan")
        _, fixed = utility_evidence.native_plan(plans[0][1], cfg["session"]["task_id"])
        evidence.extend(fixed)
    return evidence


def _prior_native_gates(state):
    for identity, phase in (("deterministic-inspection", "P2"), ("independent-review", "P3")):
        prior = next((row for row in state.cfg["contract"]["gate_inputs"]
                      if row["id"] == identity and row["phase"] == phase), None)
        if prior is None or prior["path"] not in state.gates:
            core._fail("native scheduling lacks prior independent gate input " + identity)
        raw = store._external(Path(prior["path"]), LIMIT, "prior native gate")
        if core._json(raw, "prior native gate").get("outcome") != "PASS":
            core._fail("prior native gate remains failed or unavailable: " + identity)


def _native_prerequisites(state):
    if state.cfg["contract"]["workflow"] != "skill-validator" or state.phase != "P4" or state.status != "ACTIVE":
        core._fail("native scheduling requires active validator P4 after independent inspection/review")
    _prior_native_gates(state)
    spec = next(row for row in state.cfg["contract"]["gate_inputs"] if row["id"] == "native-prerequisites")
    raw = store._external(Path(spec["path"]), LIMIT, "native prerequisite gate")
    value = core._json(raw, "native prerequisite gate")
    if value.get("outcome") != "PASS":
        core._fail("native prerequisites remain unresolved; reporting can continue separately")
    fixed = _gate(raw, spec, state.cfg)
    plans = [(path, data) for kind, path, data in fixed if kind == "gate_evidence"]
    if len(plans) != 1:
        core._fail("native plan binding is missing or ambiguous")
    path, plan_raw = plans[0]
    return spec, raw, fixed, {"path": path, "sha256": store._hash(plan_raw)}, core._json(plan_raw, "native plan")


class State:
    def __init__(self, root, fd):
        self.root, self.fd = root, fd
        self.manifest, manifest_raw = store._json_at(fd, "MANIFEST.json", "utility manifest")
        core._exact(self.manifest, {"schema_version", "state_root", "session_path", "session_sha256",
                                   "task_id", "project_root", "started_at_utc", "snapshots"}, "utility manifest")
        if self.manifest["schema_version"] != STATE_SCHEMA or self.manifest["state_root"] != str(root):
            core._fail("utility manifest identity mismatch")
        self.cfg = _configuration(Path(self.manifest["session_path"]), root)
        if (store._hash(self.cfg["raw"]) != self.manifest["session_sha256"]
                or self.manifest["task_id"] != self.cfg["session"]["task_id"]
                or self.manifest["project_root"] != str(self.cfg["project"])):
            core._fail("selected utility session changed")
        self.blobs = store._object_directory(fd, "snapshots", ".bin", max(LIMIT, store.INSTALLED_LIMIT))
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
            if stamp < self.last_time or stamp >= self.cfg["deadline"] and row["operation"] != "native_schedule_cancel":
                core._fail("utility journal time violates original deadline")
            self.last_time = stamp
            for ref in row["snapshots"]:
                store._validate_ref(ref, self.blobs)
            self._apply(row)
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
            plan, _ = utility_evidence.native_plan(self.blobs[refs[0]["sha256"]], self.cfg["session"]["task_id"])
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
            fixed = _gate(gate_raw, gate_spec, self.cfg)
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
            kernel = utility_schedule.validate(self.blobs[refs[0]["sha256"]], plan, plan_ref,
                                               self.cfg["session"]["task_id"])
            self.schedule = utility_schedule.JournalSchedule(kernel, store._stamp(record["at_utc"], "schedule origin"),
                                                             self.cfg["deadline"])
            for (_, path), digest in expected.items():
                self.gates[path] = digest
            self._nonce(data["next_challenge"])
        elif op == "native_schedule_reserve":
            core._exact(data, {"next_challenge"}, "native schedule reservation")
            if self.schedule is None or self.phase != "P4" or self.status != "ACTIVE":
                core._fail("native schedule reservation requires a bound active P4 schedule")
            self.schedule.reserve(store._stamp(record["at_utc"], "schedule reservation time"))
            self._nonce(data["next_challenge"])
        elif op == "native_schedule_cancel":
            core._exact(data, {"attempt_id", "reason"}, "native unlaunched cancellation")
            core._text(data["reason"], "cancellation reason")
            if self.schedule is None or self.phase != "P4":
                core._fail("cancellation requires a bound P4 schedule")
            self.schedule.cancel_unlaunched(data["attempt_id"], store._stamp(record["at_utc"], "cancellation time"))
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
            _structured(self.blobs[ref["sha256"]], spec)
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
            for kind, path, raw in _gate(self.blobs[ref["sha256"]], spec, self.cfg):
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
        if operation != "native_schedule_cancel":
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
                    _structured(value, spec)
                    if by_id[spec["id"]] != {"id": spec["id"], "path": str(state.cfg["project"] / spec["path"]), "sha256": store._hash(value)}:
                        core._fail("checkpoint output identity differs from actual bytes")
                    sources.append(("output", str(state.cfg["project"] / spec["path"]), value))
            for spec in state.cfg["contract"]["gate_inputs"]:
                if spec["phase"] == state.phase:
                    value = store._external(Path(spec["path"]), LIMIT, "utility gate input")
                    sources.append(("gate", spec["path"], value))
                    sources.extend(_gate(value, spec, state.cfg))
            # Snapshots are checked before the atomic HEAD commit; later admissions recheck current bytes.
            state.append("accepted", {"next_challenge": None if state.phase == state.phases[-1] else store._fresh(state)}, sources)
            return state.view("READY" if state.status == "READY" else "PROGRESS")
        except core._Problem as error:
            if error.result != "FAIL":
                raise
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
        spec, raw, fixed, _, _ = _native_prerequisites(state)
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
