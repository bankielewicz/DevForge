"""Evidence prerequisite for operational adoption of the two Codex expert workflows.

This is an installer guard, not a native collector or phase-order interceptor.
The operator owns acceptance and observation truth; hashes bind declared evidence.
No candidate code is executed. Runtime-only exports remain unaccepted staging.
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path
import re


NAMES = {"devforge-project-expert-creator", "devforge-evaluate-expert"}
CREATOR_PHASES = ("Intake", "Selection", "Design", "Authoring", "PreparedTransfer")
TASKS = {"P1": ("T01", "T02"), "P2": ("T03",), "P3": ("T04",),
         "P4": ("T05", "T06", "T07", "T08"), "P5": ("T09",),
         "P6": ("T10", "T11", "T12")}
MAX_BYTES = 32 * 1024 * 1024
LOCAL_CHECKS = {
    "package_integrity": "D", "installed_resources": "D", "independent_semantics": "S",
    "grounded_creation": "N", "reuse": "N", "bounded_enhancement": "N",
    "missing_evidence_refusal": "N", "creator_to_evaluator": "N", "evaluator_to_creator": "N",
}


def require(condition, reason):
    if not condition:
        raise ValueError("manual expert evidence: " + reason)


def exact(value, fields, name):
    require(isinstance(value, dict) and set(value) == set(fields), "invalid " + name)
    return value


def text(value, name):
    require(isinstance(value, str) and value.strip() and "{{" not in value,
            "missing " + name)
    return value


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key: " + key)
            result[key] = value
        return result

    def constant(value):
        raise ValueError("manual expert evidence: nonfinite JSON value " + value)

    require(len(raw) <= MAX_BYTES, "record exceeds size limit")
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("manual expert evidence: invalid JSON") from error
    require(isinstance(value, dict), "record must be an object")
    return value


def selected_packages(planned):
    result = {}
    for name in sorted(NAMES):
        prefix = ".agents/skills/" + name + "/"
        files = {path[len(prefix):]: hashlib.sha256(data).hexdigest()
                 for path, data in planned.items() if path.startswith(prefix)}
        if files:
            require("SKILL.md" in files, "recognized package has no entrypoint")
            result[name] = files
    return result


class Evidence:
    def __init__(self, project, framework):
        self.project, self.framework = project, framework
        self.pins = {}
        self.source_trees = {}

    def pin(self, ref, *, authority=False):
        exact(ref, ("path", "sha256"), "evidence pin")
        path = Path(text(ref["path"], "pin path"))
        require(path.is_absolute() and str(path.resolve()) == str(path), "noncanonical pin path")
        if authority:
            require(not any(path.is_relative_to(self.project / root) for root in (".agents", ".codex", ".claude"))
                    and not path.is_relative_to(self.framework),
                    "authority evidence must be outside candidate and installation roots")
        expected = ref["sha256"]
        require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected), "invalid digest")
        require(path.is_file() and path.stat().st_size <= MAX_BYTES, "missing or oversized evidence")
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == expected, "missing/stale evidence: " + str(path))
        require(str(path) not in self.pins or self.pins[str(path)] == expected, "conflicting pin")
        self.pins[str(path)] = expected
        return raw

    def document(self, ref, *, authority=False):
        return decode(self.pin(ref, authority=authority))

    def walk(self, value):
        if isinstance(value, dict):
            if {"path", "sha256"} <= set(value):
                self.pin({key: value[key] for key in ("path", "sha256")})
            else:
                for child in value.values():
                    self.walk(child)
        elif isinstance(value, list):
            for child in value:
                self.walk(child)

    def refs(self, values):
        require(isinstance(values, list) and bool(values), "required obligation has no evidence")
        for ref in values:
            self.pin(ref)

    def recheck(self):
        for path, files in self.source_trees.items():
            require(_source_files(Path(path)) == files, "source identity changed before install")
        for path, sha in list(self.pins.items()):
            self.pin({"path": path, "sha256": sha})


def catalog_coverage(cases_ref, policy, evidence):
    """Bind the projection to every original case assertion, including excluded ones."""
    refs = policy.get("catalog_refs", [])
    require(isinstance(refs, list) and cases_ref in refs, "missing original case catalog")
    projection = policy.get("catalog_assertions", [])
    require(isinstance(projection, list) and projection, "missing catalog assertion projection")
    known, obligations = set(), {}
    for ref in refs:
        raw = evidence.pin(ref)
        if ref != cases_ref and not ref["path"].endswith(".json"):
            continue  # Additional specification anchors are reviewed separately.
        catalog = decode(raw)
        key = "cases" if "cases" in catalog else "evals"
        cases = catalog.get(key)
        require(isinstance(cases, list) and cases, "unsupported/empty case catalog")
        case_ids = set()
        for index, case in enumerate(cases):
            require(isinstance(case, dict), "invalid catalog case")
            case_id = text(case.get("id"), "case ID")
            require(case_id not in case_ids, "duplicate catalog case")
            case_ids.add(case_id)
            fields = [field for field in ("required_observations", "expectations", "frozen_discriminator") if field in case]
            require(len(fields) == 1, "case needs one supported assertion list/discriminator")
            field = fields[0]
            assertions = [case[field]] if field == "frozen_discriminator" else case[field]
            require(isinstance(assertions, list) and assertions, "empty case assertions")
            for number, assertion in enumerate(assertions):
                text(assertion, "original assertion")
                pointer = f"/{key}/{index}/{field}" + ("" if field == "frozen_discriminator" else f"/{number}")
                matches = [row for row in projection if row.get("source_ref") == ref
                           and row.get("source_pointer") == pointer and row.get("case_id") == case_id]
                require(matches, "catalog assertion omitted: " + case_id + pointer)
                for row in matches:
                    if case.get("tier") in ("C", "B", "A"):
                        require("N" in row.get("evidence_kinds", []), "native catalog evidence kind weakened")
                    obligations[row.get("assertion_id")] = {"tier": case.get("tier"), "kinds": row.get("evidence_kinds", [])}
                    known.add(row.get("assertion_id"))
    ids = [row.get("assertion_id") for row in projection]
    require(all(isinstance(value, str) and value for value in ids) and len(ids) == len(set(ids)), "invalid/duplicate catalog projection")
    require({row.get("assertion_id") for row in policy.get("assertions", [])} == set(ids), "catalog/selection inventory differs")
    # Every additional (for example specification) projection resolves its pinned anchor.
    for row in projection:
        require(row.get("source_ref") in refs, "unknown catalog source")
        if row["assertion_id"] not in known:
            raw = evidence.pin(row["source_ref"])
            require(not row["source_ref"]["path"].endswith(".json")
                    and text(row.get("source_pointer"), "source anchor") in raw.decode(), "unknown catalog assertion pointer")
            obligations[row["assertion_id"]] = {"tier": None, "kinds": row.get("evidence_kinds", [])}
    return obligations


def validate(evidence_path, planned, project, framework):
    try:
        return _validate(evidence_path, planned, project, framework)
    except (TypeError, KeyError, AttributeError, RecursionError) as error:
        raise ValueError("manual expert evidence: malformed nested record") from error


def _time(value):
    try:
        result = datetime.fromisoformat(text(value, "timestamp").replace("Z", "+00:00"))
        require(result.utcoffset() is not None, "timestamp needs timezone")
        return result
    except ValueError as error:
        raise ValueError("manual expert evidence: invalid timestamp") from error


def _source_files(root):
    files = {}
    for path in root.rglob("*"):
        require(not path.is_symlink(), "source identity contains symlink")
        relative = path.relative_to(root)
        if path.is_file() and "__pycache__" not in relative.parts and path.suffix != ".pyc":
            files[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def _local_baseline(record, root_pin, packages, evidence):
    """A separately authorized local claim; never a Routine or Full decision."""
    fields = ("schema_version", "project_root", "owner", "authorization", "packages",
              "acceptance_set", "results", "review", "acceptance", "historical_evidence")
    exact(record, fields, "local baseline record")
    project, framework = evidence.project, evidence.framework
    require(record["project_root"] == str(project), "wrong local installation destination")
    owner = text(record["owner"], "local owner")
    evidence.pin(record["authorization"], authority=True)
    evidence.refs(record["historical_evidence"])
    require(isinstance(record["packages"], list) and len(record["packages"]) == len(NAMES)
            and {p.get("name") for p in record["packages"]} == set(packages) == NAMES,
            "local baseline requires both exact manual packages")
    authors, catalogs = set(), {}
    for package in record["packages"]:
        exact(package, ("name", "manifest", "source_manifest", "specification", "cases", "author"), "local package")
        name = package["name"]
        authors.add(text(package["author"], "package author"))
        runtime = evidence.document(package["manifest"])
        require(runtime == {"schema_version": "devforge.expert-runtime-manifest/v1", "name": name,
                            "files_sha256": packages[name]}, "local runtime manifest differs from planned bytes")
        source = evidence.document(package["source_manifest"])
        root = framework / "providers/codex/plugins/devforgeai/skills" / name
        exact(source, ("source_root", "files_sha256"), "source manifest")
        actual = _source_files(root)
        require(source == {"source_root": str(root), "files_sha256": actual}, "source identity changed")
        evidence.source_trees[str(root)] = actual
        for relative, digest in actual.items():
            evidence.pin({"path": str(root / relative), "sha256": digest})
        require(package["cases"]["path"] == str(root / "evals/evals.json"), "wrong source case catalog")
        catalog = evidence.document(package["cases"])
        cases = catalog.get("cases", catalog.get("evals"))
        require(isinstance(cases, list) and cases, "missing qualification cases")
        ids = [text(case.get("id"), "case ID") for case in cases]
        require(len(ids) == len(set(ids)), "duplicate qualification cases")
        catalogs[name] = dict.fromkeys(ids, "NOT_RUN")
        evidence.pin(package["specification"])
    plan = evidence.document(record["acceptance_set"], authority=True)
    exact(plan, ("schema_version", "project_root", "owner", "authorization", "packages", "checks",
                 "frozen_at_utc", "max_seconds", "max_native_turns", "historical_evidence"), "local acceptance set")
    require(plan["schema_version"] == "devforge.manual-local-acceptance-set/v1", "unsupported local set")
    require(all(plan[key] == record[key] for key in ("owner", "project_root", "authorization", "packages", "historical_evidence")),
            "local set authority/identity differs")
    require(isinstance(plan["checks"], dict) and set(plan["checks"]) == set(LOCAL_CHECKS), "local acceptance check coverage differs")
    for key, kind in LOCAL_CHECKS.items():
        check = exact(plan["checks"][key], ("kind", "expectations"), "predefined check")
        require(check["kind"] == kind and isinstance(check["expectations"], list) and check["expectations"], "acceptance check weakened")
        for expectation in check["expectations"]:
            text(expectation, "predefined expectation")
    require(all(type(plan[key]) is int and plan[key] > 0 for key in ("max_seconds", "max_native_turns")), "unbounded local set")
    results = evidence.document(record["results"])
    exact(results, ("schema_version", "acceptance_set", "qualification_status", "checks", "qualification_cases",
                    "started_at_utc", "finished_at_utc", "native_turns"), "local results")
    require(results["schema_version"] == "devforge.manual-local-acceptance-results/v1"
            and results["acceptance_set"] == record["acceptance_set"]
            and results["qualification_status"] == "UNQUALIFIED", "local result/claim mismatch")
    require(results["qualification_cases"] == catalogs, "qualification cases must remain complete and NOT_RUN")
    start, end = _time(results["started_at_utc"]), _time(results["finished_at_utc"])
    require(_time(plan["frozen_at_utc"]) < start <= end, "acceptance set must be predefined")
    require((end - start).total_seconds() <= plan["max_seconds"], "local time allowance exceeded")
    require(type(results["native_turns"]) is int and 0 < results["native_turns"] <= plan["max_native_turns"], "native turn allowance exceeded")
    require(isinstance(results["checks"], dict) and set(results["checks"]) == set(LOCAL_CHECKS), "local acceptance check coverage differs")
    observations = set()
    actors = set()
    identities = {p["name"]: p["manifest"] for p in record["packages"]}
    for key, kind in LOCAL_CHECKS.items():
        result = exact(results["checks"][key], ("outcome", "evidence", "native_observation"), "acceptance check")
        require(result["outcome"] == "PASS", "required acceptance check did not pass")
        evidence.refs(result["evidence"])
        if kind != "N":
            require(result["native_observation"] is None, "deterministic/semantic check is not native")
            continue
        require(result["native_observation"] in result["evidence"], "native observation not bound to check")
        observation = evidence.document(result["native_observation"])
        exact(observation, ("schema_version", "acceptance_set", "outcome", "packages", "actor", "native_client", "model",
                            "reasoning_effort", "state_isolation", "transcript", "artifacts", "started_at_utc",
                            "finished_at_utc", "manual_transfer"), "native local observation")
        require(observation["schema_version"] == "devforge.manual-local-observation/v1"
                and observation["acceptance_set"] == record["acceptance_set"]
                and observation["packages"] == identities and observation["outcome"] == "PASS"
                and observation["native_client"] == "codex", "native observation identity/result differs")
        text(observation["model"], "observed native model")
        actors.add(text(observation["actor"], "observed native actor"))
        text(observation["reasoning_effort"], "observed reasoning effort")
        require(start <= _time(observation["started_at_utc"]) <= _time(observation["finished_at_utc"]) <= end,
                "observation outside predefined set interval")
        evidence.pin(observation["state_isolation"])
        evidence.pin(observation["transcript"])
        evidence.refs(observation["artifacts"])
        if key in ("creator_to_evaluator", "evaluator_to_creator"):
            transfer = exact(observation["manual_transfer"], ("direction", "user", "user_request", "producer_output",
                             "receiver_observation", "completed_action"), "manual transfer")
            require(transfer["direction"] == key, "wrong manual transfer direction")
            text(transfer["user"], "actual receiving user")
            for field in ("user_request", "producer_output", "receiver_observation", "completed_action"):
                evidence.pin(transfer[field])
        evidence.walk(observation)
        observations.add(result["native_observation"]["path"])
    require(len(observations) <= results["native_turns"], "native turn count omits observations")
    review = evidence.document(record["review"], authority=True)
    exact(review, ("schema_version", "reviewer", "independence_evidence", "overall", "criteria", "acceptance_set",
                   "packages", "check_judgments"), "local independent review")
    require(review["schema_version"] == "devforge.manual-local-review/v1"
            and review["acceptance_set"] == record["acceptance_set"] and review["packages"] == record["packages"]
            and text(review["reviewer"], "reviewer") not in authors | actors | {owner}, "local independent reviewer mismatch")
    evidence.pin(review["independence_evidence"])
    require(review["overall"] == "PASS" and isinstance(review["criteria"], dict)
            and set(review["criteria"]) == {f"R{i:02}" for i in range(1, 11)}
            and isinstance(review["check_judgments"], dict) and set(review["check_judgments"]) == set(LOCAL_CHECKS),
            "local semantic review coverage incomplete")
    for judgment in [*review["criteria"].values(), *review["check_judgments"].values()]:
        exact(judgment, ("outcome", "reason", "evidence"), "independent judgment")
        require(judgment["outcome"] == "PASS", "local independent judgment did not pass")
        text(judgment["reason"], "judgment reason")
        evidence.refs(judgment["evidence"])
    for key, judgment in review["check_judgments"].items():
        require(all(ref in judgment["evidence"] for ref in results["checks"][key]["evidence"]), "review omits actual acceptance evidence")
    acceptance = evidence.document(record["acceptance"], authority=True)
    require(acceptance == {"schema_version": "devforge.manual-local-owner-acceptance/v1", "owner": owner,
                          "action": "install_unqualified_local_baseline", "qualification_status": "UNQUALIFIED",
                          "inputs": {key: value for key, value in record.items() if key != "acceptance"},
                          "observation_basis": "operator-reviewed actual evidence"}, "missing exact local owner acceptance")
    evidence.recheck()
    return {"record": root_pin, "owner": owner, "packages": sorted(packages), "predicate": "manual-local-baseline/v1",
            "qualification_status": "UNQUALIFIED", "acceptance_status": "LOCAL_ACCEPTANCE_SET_PASS", "_evidence": evidence}


def _validate(evidence_path, planned, project, framework):
    packages = selected_packages(planned)
    if not packages:
        require(evidence_path is None, "no promoted Codex package selected")
        return None
    require(evidence_path is not None, "manual adoption evidence is required for promoted Codex packages")
    evidence_path = evidence_path.resolve()
    evidence = Evidence(project, framework)
    raw = evidence_path.read_bytes()
    root_pin = {"path": str(evidence_path), "sha256": hashlib.sha256(raw).hexdigest()}
    record = evidence.document(root_pin, authority=True)
    if record.get("schema_version") == "devforge.manual-expert-local-baseline/v1":
        return _local_baseline(record, root_pin, packages, evidence)
    exact(record, ("schema_version", "project_root", "owner", "authorization", "packages"), "adoption record")
    require(record["schema_version"] == "devforge.manual-expert-adoption/v1", "unsupported adoption version")
    require(record["project_root"] == str(project), "wrong installation destination")
    owner = text(record["owner"], "integration owner")
    evidence.pin(record["authorization"], authority=True)
    require(isinstance(record["packages"], list), "packages must be an array")
    indexed = {}
    keys = ("name", "manifest", "specification", "plan", "cases", "creator", "results",
            "decision", "review", "acceptance")
    for package in record["packages"]:
        exact(package, keys, "package adoption")
        name = package["name"]
        require(isinstance(name, str) and name not in indexed, "duplicate package")
        indexed[name] = package
    require(set(indexed) == set(packages), "adoption package selection differs from planned installation")
    for name, package in indexed.items():
        docs = {key: evidence.document(package[key], authority=key in ("review", "acceptance")) for key in ("manifest", "plan", "creator", "results", "decision", "review", "acceptance")}
        for document in docs.values():
            evidence.walk(document)
        evidence.pin(package["specification"])
        evidence.pin(package["cases"])
        manifest = docs["manifest"]
        exact(manifest, ("schema_version", "name", "files_sha256"), "runtime manifest")
        require(manifest["schema_version"] == "devforge.expert-runtime-manifest/v1"
                and manifest["name"] == name and manifest["files_sha256"] == packages[name],
                "candidate manifest differs from planned bytes")
        creator = docs["creator"]
        exact(creator, ("schema_version", "author", "candidate", "specification", "phases"), "creator completion")
        require(creator["schema_version"] == "devforge.expert-creator-completion/v1"
                and creator["candidate"] == package["manifest"]
                and creator["specification"] == package["specification"], "creator identity mismatch")
        author = text(creator["author"], "candidate author")
        require(isinstance(creator["phases"], dict) and set(creator["phases"]) == set(CREATOR_PHASES),
                "all five creator phases are required")
        for phase in creator["phases"].values():
            exact(phase, ("classification", "evidence"), "creator phase")
            require(phase["classification"] == "Enforced", "creator classification changed")
            evidence.refs(phase["evidence"])
        plan, results, decision, review = (docs[key] for key in ("plan", "results", "decision", "review"))
        require(plan.get("schema_version") == "devforge.skill-validation-plan/v2", "unsupported evaluation plan")
        policy = plan.get("validation_policy", {})
        require(policy.get("version") == "VPR-2" and policy.get("mode") in ("Routine", "Full"), "unknown validation policy")
        require(policy.get("candidate_identity", {}).get("candidate") == package["manifest"], "plan candidate mismatch")
        require(review.get("candidate_ref") == package["manifest"], "review candidate mismatch")
        for kind in ("specification", "cases"):
            require(any(ref == {"kind": kind, **package[kind]} for ref in plan.get("input_refs", [])), "plan input mismatch: " + kind)
        require(results.get("schema_version") == "devforge.skill-validation-results/v2"
                and decision.get("schema_version") == "devforge.skill-validation-decision/v2"
                and results.get("plan") == package["plan"] and decision.get("plan") == package["plan"]
                and decision.get("results") == package["results"], "evaluation binding mismatch")
        require(results.get("run_id") == plan.get("run_id") == decision.get("run_id") == review.get("run_id"), "run identity mismatch")
        evaluator = text(plan.get("assignment", {}).get("owner"), "evaluator")
        require(evaluator != author, "creator cannot evaluate its own target")
        require(review.get("schema_version") == "devforge.skill-ai-review/v2"
                and review.get("plan") == package["plan"]
                and results.get("ai_review") == package["review"]
                and decision.get("ai_review") == package["review"], "independent review binding mismatch")
        reviewer = review.get("reviewer", {})
        identity = text(reviewer.get("identity"), "reviewer")
        require(identity not in (author, evaluator) and identity == policy.get("selection_reviewer"), "reviewer is not separately assigned")
        text(reviewer.get("independence_evidence"), "observed review independence")
        require(review.get("overall") == "PASS" and review.get("selection_review", {}).get("outcome") == "PASS", "independent selection review did not pass")
        criteria = review.get("criteria", [])
        require(isinstance(criteria, list) and len(criteria) == 10
                and {row.get("id") for row in criteria} == {f"R{i:02}" for i in range(1, 11)}, "review invariant coverage incomplete")
        for criterion in criteria:
            require(criterion.get("outcome") in ("PASS", "NOT_APPLICABLE"), "required semantic review incomplete/failed")
            text(criterion.get("reason"), "review reason")
            evidence.refs(criterion.get("evidence"))
        mode = policy["mode"]
        require(decision.get("validation_disposition") == mode.upper() + "_PASS"
                and decision.get("overall") == "PASS" and decision.get("coverage_complete") is True
                and decision.get("external_acceptance") == "NOT_GRANTED", "required evaluation is incomplete/failed")
        require(results.get("validation_disposition") == decision["validation_disposition"], "result/decision disposition differs")
        impact = policy.get("impact", {})
        if mode == "Routine":
            lineage = policy.get("lineage", {})
            baseline = policy.get("baseline_identity")
            require(isinstance(baseline, dict) and baseline == lineage.get("current_routinely_accepted"), "Routine requires an accepted baseline")
            scope = policy.get("accepted_scope_ref")
            evidence.pin(scope)
            chain = lineage.get("acceptance_chain", [])
            require(isinstance(chain, list) and chain and chain[-1] == lineage.get("previous_acceptance"), "Routine baseline acceptance chain missing")
            previous = evidence.document(lineage["previous_acceptance"])
            require(previous.get("candidate_identity") == baseline and previous.get("accepted_scope_ref") == scope, "Routine baseline acceptance differs")
            old = previous.get("lineage", {})
            require(old.get("qualified_anchor") == lineage.get("qualified_anchor")
                    and old.get("accepted_unqualified_baseline") == lineage.get("accepted_unqualified_baseline")
                    and old.get("acceptance_chain") == chain[:-1], "Routine cumulative anchor/chain changed")
            anchor = lineage.get("qualified_anchor", {})
            require(anchor.get("status") in ("QUALIFIED", "ABSENT", "UNKNOWN"), "Routine anchor status missing")
            require(anchor.get("status") == "QUALIFIED" or isinstance(lineage.get("accepted_unqualified_baseline"), dict), "Routine cumulative baseline missing")
            require(anchor.get("status") == "QUALIFIED" or (anchor.get("identity") is None and anchor.get("evidence") is None), "Routine invented qualification")
            for field in ("immediate_diff", "cumulative_diff"):
                evidence.pin(impact.get(field))
            compatibility = policy.get("compatibility", [])
            require(isinstance(compatibility, list) and len(compatibility) == 4
                    and {row.get("id") for row in compatibility} == {f"CP-{i:02}" for i in range(1, 5)}, "Routine compatibility coverage missing")
            for row in compatibility:
                require(row.get("disposition") in ("UNCHANGED", "EVIDENCED", "NOT_APPLICABLE"), "Routine compatibility unresolved")
                text(row.get("reason"), "compatibility reason")
                evidence.refs(row.get("evidence"))
            require(impact.get("bounded") is True and not impact.get("full_triggers")
                    and not ({"CI-05", "CI-06", "CI-09"} & set(impact.get("matched_rules", [])))
                    and policy.get("requested_claim", {}).get("requires_full") is False
                    and decision.get("routine_adoption_eligible") is True,
                    "consequential/unbounded or ineligible Routine adoption")
        require(decision.get("lineage") == results.get("lineage") == policy.get("lineage")
                and isinstance(policy.get("lineage"), dict), "lineage differs/missing")
        obligations = catalog_coverage(package["cases"], policy, evidence)
        selected = policy.get("assertions", [])
        require(isinstance(selected, list) and selected, "missing assertion selection")
        selected_ids = [row.get("assertion_id") for row in selected]
        require(len(selected_ids) == len(set(selected_ids)), "duplicate selected assertion")
        assertions = results.get("assertion_results", [])
        require(isinstance(assertions, list) and len(assertions) == len(selected_ids)
                and {row.get("assertion_id") for row in assertions} == set(selected_ids)
                and decision.get("assertion_results") == assertions, "assertion results differ/missing")
        reviewed = review.get("selection_review", {}).get("reviewed_assertion_ids", [])
        require(len(reviewed) == len(selected_ids) and set(reviewed) == set(selected_ids), "review omits assertion selection")
        checks = decision.get("checks", [])
        require(isinstance(checks, list) and len(checks) == len(selected_ids)
                and {row.get("check_id") for row in checks} == set(selected_ids), "decision coverage differs")
        by_id = {row["assertion_id"]: row for row in assertions}
        for selection in selected:
            obligation = obligations[selection["assertion_id"]]
            kinds = obligation["kinds"]
            require(isinstance(kinds, list) and kinds and set(kinds) <= {"D", "S", "N"}, "invalid catalog evidence obligation")
            if "N" in kinds:
                require(selection.get("tier") in ("C", "B", "A")
                        and (obligation["tier"] not in ("C", "B", "A") or selection["tier"] == obligation["tier"]),
                        "native catalog tier/obligation changed")
            result = by_id[selection["assertion_id"]]
            require(result.get("selection") == selection.get("selection"), "post-hoc assertion exclusion")
            if selection.get("selection") == "REQUIRED":
                allowed = ("PASS", "FAIL") if selection.get("expectation") == "observation" else ("PASS",)
                require(result.get("integrity") == "INTACT" and result.get("outcome") in allowed,
                        "required assertion incomplete/failed")
                evidence.refs(result.get("observation_refs"))
                require(next(row for row in checks if row["check_id"] == selection["assertion_id"]).get("effective_outcome") == "PASS", "required decision check did not pass")
                if {"S", "N"} & set(kinds) or selection.get("tier") in ("S", "C", "B", "A"):
                    evidence.refs(result.get("grade_refs"))
            else:
                require((selection.get("selection"), result.get("outcome")) in
                        (("NOT_SELECTED", "NOT_RUN"), ("NOT_APPLICABLE", "NOT_APPLICABLE"))
                        and (mode == "Routine" or selection.get("selection") == "NOT_APPLICABLE"),
                        "invalid assertion exclusion")
        tasks = results.get("task_results", [])
        required_tasks = [task for phase in TASKS.values() for task in phase]
        require(isinstance(tasks, list) and [row.get("task_id") for row in tasks] == required_tasks,
                "all six evaluator phases and twelve tasks are required")
        require(decision.get("task_results") == tasks, "task decision differs from results")
        selections = policy.get("task_selection", [])
        require(isinstance(selections, list) and [row.get("task_id") for row in selections] == required_tasks, "plan task coverage differs")
        for task, selection in zip(tasks, selections):
            require(selection.get("classification") == "Enforced" and selection.get("selection") == task.get("selection"), "task selection/classification changed")
            require(task.get("classification") == "Enforced", "evaluator classification changed")
            if task.get("selection") == "REQUIRED":
                require(task.get("disposition") == "SATISFIED" and task.get("outcome") == "PASS", "required task incomplete/failed")
            else:
                require(task["task_id"] in ("T05", "T06", "T07", "T08")
                        and task.get("disposition") == "SATISFIED_BY_REVIEWED_SELECTION"
                        and (task.get("selection"), task.get("outcome")) in (("NOT_APPLICABLE", "NOT_APPLICABLE"), ("NOT_SELECTED", "NOT_RUN"))
                        and (mode == "Routine" or task.get("selection") == "NOT_APPLICABLE"),
                        "unreviewed task exclusion")
            evidence.refs(task.get("evidence"))
        if mode == "Full":
            require({row.get("tier") for row in selected if row.get("selection") == "REQUIRED"} >= {"D", "S", "C", "B", "A"}, "Full catalog must retain applicable deterministic, semantic and native tiers")
            require(all(task.get("selection") == "REQUIRED" for task in tasks if task["task_id"] in ("T05", "T06", "T07", "T08")), "Full cannot exclude every native obligation")
            transfer = results.get("receiving_transfer", {})
            require(transfer.get("selection") == "REQUIRED" and transfer.get("outcome") == "PASS", "Full receiving transfer unavailable")
            for field in ("target_output", "receiver_contract", "receiver_observation", "completed_action"):
                evidence.pin(transfer.get(field))
            text(transfer.get("observed_at_utc"), "actual transfer time")
        acceptance = docs["acceptance"]
        expected = {key: package[key] for key in keys if key not in ("name", "acceptance")}
        require(acceptance == {"schema_version": "devforge.expert-install-acceptance/v1",
                               "owner": owner, "project_root": str(project), "package": name,
                               "action": "install", "inputs": expected,
                               "observation_basis": "operator-reviewed actual evidence"},
                "missing exact owner acceptance and observation attestation")
    evidence.recheck()
    return {"record": root_pin, "owner": owner, "packages": sorted(packages),
            "predicate": "manual-expert-adoption/v1", "_evidence": evidence}
