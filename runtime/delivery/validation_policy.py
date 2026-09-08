"""Protected opt-in VPR-2 parser and reducer (G1 canonical record contract).

This module validates selected custody, not semantic review quality or native
provenance. Executed native evidence additionally requires the runtime importer.
Legacy callers never enter this policy; unknown versions have no fallback.
"""
from pathlib import Path

try:
    from . import delivery_core as core, phase_state as store
except ImportError:
    import delivery_core as core
    import phase_state as store

DELIVERY_SCHEMA = "devforge.utility-delivery/v2"
PLAN_SCHEMA = "devforge.skill-validation-plan/v2"
GATE_SCHEMA = "devforge.utility-gate-input/v2"
REVIEW_SCHEMA = "devforge.skill-ai-review/v2"
RESULTS_SCHEMA = "devforge.skill-validation-results/v2"
DECISION_SCHEMA = "devforge.skill-validation-decision/v2"
RESULT_ADDITIONS = set("task_results assertion_results report_completion validation_disposition routine_adoption_eligible lineage owner_acceptance_ref receiving_transfer".split())
TASKS = tuple(f"T{i:02}" for i in range(1, 13))
NATIVE_TASKS = {"native-prerequisites": "T05", "native-C": "T06", "native-B": "T07", "native-A": "T08"}
OUTCOMES = {"PASS", "FAIL", "NOT_RUN", "COULD_NOT_RUN", "NOT_APPLICABLE"}
SELECTIONS = {"REQUIRED", "NOT_SELECTED", "NOT_APPLICABLE"}
DISPOSITIONS = {"SATISFIED", "SATISFIED_BY_REVIEWED_SELECTION", "BLOCKED", "NOT_RUN"}
PLAN_KEYS = set("schema_version run_id provider candidate_root input_refs baseline assignment runtime budget checks findings_from_previous_iteration criteria_freeze_record scope_exclusions plan_scope validation_policy".split())
POLICY_KEYS = set("version policy_ref acceptance_ref mode requested_claim accepted_scope_ref baseline_identity candidate_identity lineage impact compatibility catalog_refs catalog_assertions assertions task_selection observations call_graph selection_reviewer".split())
CONDITION_KEYS = set("identity input_refs prompt_ref arm variant repetition invocation visibility_ref freshness_ref before_task".split())
RESULT_KEYS = set("schema_version run_id plan structural_report ai_review results findings task_results assertion_results report_completion validation_disposition routine_adoption_eligible lineage owner_acceptance_ref receiving_transfer".split())
RUN_KEYS = set("schema_version run_id tier provider client_version model_configuration installation_mode source_files_sha256 installed_files_sha256 baseline specification_files_sha256 case_files_sha256 fixture_files_sha256 execution_ref context_isolation sibling_availability output_directory transcript outcome cause metrics grading_evidence case_id attempt_id arm transcript_sha256 installation_path native_observations boundary_refs authentication_observation_ref client_state_observation_ref process_ownership_ref effective_configuration_ref worker_visible_input_refs operator_only_input_refs deviations environment_setup_ref validation_plan_ref workspace_allocation_ref workspace_id client_state_directory observation_id assertion_ids selection evidence_kind conditions integrity raw_output_refs".split())
GRADE_KEYS = set("schema_version run_id case_id attempt_id arm run_manifest case_definition grader dimensions overall cause finding_ids limitations assertion_judgments".split())
RULES = {f"CI-{i:02}" for i in range(1, 10)} | {f"CP-{i:02}" for i in range(1, 5)}
INVARIANTS = {f"R{i:02}" for i in range(1, 11)}


def require(condition, reason):
    if not condition:
        core._fail("validation policy: " + reason)


def rows(value, label, nonempty=False):
    require(isinstance(value, list) and len(value) <= 256 and (not nonempty or value), label + " must be a bounded array")
    return value


def ids(value, label, nonempty=False):
    rows(value, label, nonempty)
    for item in value:
        core._text(item, label)
    require(len(set(value)) == len(value), label + " contains duplicate identities")
    return set(value)


def indexed(value, key, fields, label, nonempty=False):
    result = {}
    for row in rows(value, label, nonempty):
        core._exact(row, fields, label)
        identity = core._text(row[key], label + " identity")
        require(identity not in result, label + " duplicate identity")
        result[identity] = row
    return result


def positive(value, label):
    require(type(value) is int and 0 < value <= 86400, label + " must be a positive bounded integer")


class Policy:
    """Frozen policy context; every referenced byte is retained for state replay."""
    def __init__(self, selection, assignment, project, *, frozen=None):
        core._exact(selection, {"version", "policy_ref", "acceptance_ref", "plan"}, "delivery validation_policy")
        require(selection["version"] == "VPR-2", "unknown version")
        self.selection, self.project, self.frozen = selection, Path(project), frozen
        self.sources = {}
        self.pin(selection["policy_ref"], "accepted policy")
        self.pin(selection["acceptance_ref"], "owner acceptance")
        plan_raw = self.pin(selection["plan"], "frozen plan")
        self.plan = core._json(plan_raw, "validation plan")
        core._exact(self.plan, PLAN_KEYS, "validation plan")
        require(self.plan["schema_version"] == PLAN_SCHEMA and self.plan["provider"] == "codex", "unsupported plan version/provider")
        core._text(self.plan["run_id"], "plan run_id")
        store._absolute(self.plan["candidate_root"], "candidate_root")
        self.value = self.plan["validation_policy"]
        core._exact(self.value, POLICY_KEYS, "plan validation_policy")
        p = self.value
        require(all(p[k] == selection[k] for k in ("version", "policy_ref", "acceptance_ref")), "plan policy/acceptance differs from delivery")
        authority = core._json(assignment, "external assignment authorization")
        auth = authority.get("authorization", {})
        require(auth.get("validation_policy") == selection and auth.get("selection_reviewer") == p["selection_reviewer"], "assignment does not select exact policy and reviewer")
        require(authority.get("owner") == self.plan["assignment"].get("owner"), "plan owner differs from assignment")
        self.author = authority.get("author", self.plan["assignment"]["owner"])
        core._text(p["selection_reviewer"], "selection reviewer")
        require(p["selection_reviewer"] != self.author, "author cannot approve own selection")
        require(p["mode"] in {"Routine", "Full"}, "unsupported mode")
        self.walk_pins(p)
        for ref in rows(self.plan["input_refs"], "plan input refs", True):
            core._exact(ref, {"kind", "path", "sha256"}, "plan input")
            self.pin({k: ref[k] for k in ("path", "sha256")}, "plan input")
        require({r["kind"] for r in self.plan["input_refs"]} >= {"specification", "rubric", "cases", "framework_contract"}, "missing original plan input kind")
        self._lineage()
        self._inventory()
        self._impact()
        self._observations()
        self._calls()
        if any(self.tasks[t]["selection"] == "REQUIRED" for t in ("T06", "T07", "T08")):
            require(isinstance(self.plan["runtime"], dict) and isinstance(self.plan["budget"], dict), "selected native work requires resolved runtime/budget")
            for key in ("max_attempts", "max_seconds", "repeats_per_case"):
                positive(self.plan["budget"].get(key), "native budget " + key)

    def pin(self, ref, label, *, protected=True):
        core._exact(ref, {"path", "sha256"}, label)
        path = store._absolute(ref["path"], label)
        core._digest(ref["sha256"], label)
        if protected:
            require(not core._within(path, self.project), label + " is worker-writable")
        raw = self.frozen[str(path)] if self.frozen is not None else store._external(path, 8 * 1024 * 1024, label)
        require(store._hash(raw) == ref["sha256"], label + " selected bytes changed")
        old = self.sources.get(str(path))
        require(old is None or old == raw, label + " has conflicting pins")
        self.sources[str(path)] = raw
        return raw

    def walk_pins(self, value):
        if isinstance(value, dict):
            if set(value) == {"path", "sha256"}:
                self.pin(value, "policy input")
            else:
                for child in value.values():
                    self.walk_pins(child)
        elif isinstance(value, list):
            rows(value, "policy collection")
            for child in value:
                self.walk_pins(child)

    def identity(self, value, nullable=False):
        if value is None and nullable:
            return
        core._exact(value, {"candidate", "environment"}, "candidate/environment identity")
        self.pin(value["candidate"], "candidate manifest")
        self.pin(value["environment"], "environment manifest")

    def _lineage(self):
        p = self.value
        self.identity(p["candidate_identity"])
        self.identity(p["baseline_identity"], True)
        lineage = p["lineage"]
        core._exact(lineage, {"qualified_anchor", "accepted_unqualified_baseline", "current_routinely_accepted", "previous_acceptance", "acceptance_chain"}, "lineage")
        anchor = lineage["qualified_anchor"]
        core._exact(anchor, {"status", "identity", "evidence"}, "qualified anchor")
        require(anchor["status"] in {"QUALIFIED", "ABSENT", "UNKNOWN"}, "unknown qualification status")
        if anchor["status"] == "QUALIFIED":
            self.identity(anchor["identity"])
            self.pin(anchor["evidence"], "Full qualified evidence")
        else:
            require(anchor["identity"] is None and anchor["evidence"] is None, "unqualified anchor cannot carry qualified identity")
        for key in ("accepted_unqualified_baseline", "current_routinely_accepted"):
            self.identity(lineage[key], True)
        chain = rows(lineage["acceptance_chain"], "acceptance chain")
        require(len({(r["path"], r["sha256"]) for r in chain}) == len(chain), "duplicate acceptance chain")
        if p["mode"] == "Routine":
            require(p["accepted_scope_ref"] is not None and p["baseline_identity"] is not None, "Routine needs accepted scope and baseline")
            require(lineage["current_routinely_accepted"] == p["baseline_identity"], "Routine current acceptance must equal immediate baseline")
            require(anchor["status"] == "QUALIFIED" or lineage["accepted_unqualified_baseline"] is not None, "Routine cumulative anchor is missing")
            require(chain and lineage["previous_acceptance"] == chain[-1], "Routine needs exact previous owner acceptance")
            # Successor owner records carry these named policy fields; helper output is not acceptance.
            previous = core._json(self.pin(chain[-1], "previous acceptance"), "previous acceptance")
            require(previous.get("candidate_identity") == p["baseline_identity"] and previous.get("accepted_scope_ref") == p["accepted_scope_ref"], "previous acceptance candidate/scope mismatch")
            prior_lineage = previous.get("lineage", {})
            require(prior_lineage.get("qualified_anchor") == anchor and prior_lineage.get("accepted_unqualified_baseline") == lineage["accepted_unqualified_baseline"], "Routine cannot reset cumulative anchor")
            require(prior_lineage.get("acceptance_chain") == chain[:-1], "acceptance chain predecessor mismatch")

    def _inventory(self):
        p = self.value
        catalog = indexed(p["catalog_assertions"], "assertion_id", set("assertion_id case_id source_ref source_pointer variant arm repetition requirement_ids evidence_kinds".split()), "catalog", True)
        rows(p["catalog_refs"], "original catalogs", True)
        for a in catalog.values():
            require(a["source_ref"] in p["catalog_refs"], "catalog assertion selects unknown source")
            raw = self.pin(a["source_ref"], "original catalog")
            pointer = core._text(a["source_pointer"], "source pointer")
            if raw.lstrip().startswith((b"{", b"[")):
                require(pointer.startswith("/"), "JSON source needs RFC6901 pointer")
                value = core._json(raw, "original catalog")
                try:
                    for part in pointer[1:].split("/"):
                        part = part.replace("~1", "/").replace("~0", "~")
                        value = value[int(part)] if isinstance(value, list) else value[part]
                except (KeyError, ValueError, TypeError, IndexError):
                    core._fail("validation policy: source pointer does not resolve original assertion")
            else:
                require(pointer in raw.decode("utf-8"), "source section does not resolve")
            core._text(a["case_id"], "original case ID")
            core._text(a["variant"], "variant")
            require(a["arm"] in {"candidate", "baseline", "none"}, "invalid arm")
            positive(a["repetition"], "repetition")
            ids(a["requirement_ids"], "original requirements", True)
            require(ids(a["evidence_kinds"], "original kinds", True) <= {"D", "S", "N"}, "invalid evidence kind")
        selected = indexed(p["assertions"], "assertion_id", set("assertion_id task_id tier selection rule_ids reason expectation dependency_ids observation_ids".split()), "assertion selection", True)
        require(set(selected) == set(catalog), "selection omits original assertion or adds unknown assertion")
        for a in selected.values():
            require(a["task_id"] in TASKS and a["tier"] in {"D", "S", "C", "B", "A"} and a["selection"] in SELECTIONS, "invalid assertion selection")
            require(a["expectation"] in {"pass", "observation"}, "invalid assertion expectation")
            require(a["expectation"] != "observation" or catalog[a["assertion_id"]]["arm"] == "baseline", "candidate judgment cannot be observation-only")
            require(ids(a["rule_ids"], "selection rules", True) <= RULES, "unknown selection rule")
            require(ids(a["dependency_ids"], "assertion dependencies") <= set(selected), "unknown assertion prerequisite")
            ids(a["observation_ids"], "assertion observations")
            core._text(a["reason"], "selection reason")
            require(p["mode"] == "Routine" or a["selection"] != "NOT_SELECTED", "Full cannot omit native/original obligations")
            if a["tier"] in {"C", "B", "A"}:
                require("N" in catalog[a["assertion_id"]]["evidence_kinds"], "native assertion evidence kind weakened")
        tasks = indexed(p["task_selection"], "task_id", {"task_id", "classification", "selection", "assertion_ids", "reason"}, "task selection", True)
        require(tuple(tasks) == TASKS, "exact twelve ordered task selections required")
        for task, t in tasks.items():
            require(t["classification"] == "Enforced" and t["selection"] in SELECTIONS, "task is not Enforced")
            core._text(t["reason"], "task selection reason")
            require(ids(t["assertion_ids"], "task assertions") == {a["assertion_id"] for a in selected.values() if a["task_id"] == task}, "task assertion coverage mismatch")
            if task not in NATIVE_TASKS.values():
                require(t["selection"] == "REQUIRED", "T01-T04 and T09-T12 remain required")
            if t["selection"] != "REQUIRED":
                require(not any(selected[a]["selection"] == "REQUIRED" for a in t["assertion_ids"]), "unselected task contains required work")
                require(t["selection"] != "NOT_SELECTED" or p["mode"] == "Routine", "Full task cannot be NOT_SELECTED")
        check_ids = ids([c["id"] for c in rows(self.plan["checks"], "checks", True)], "check IDs", True)
        require(check_ids <= set(catalog), "check not in original assertion inventory")
        self.catalog, self.assertions, self.tasks = catalog, selected, tasks

    def _impact(self):
        p = self.value
        claim = p["requested_claim"]
        core._exact(claim, {"kind", "text", "requires_full", "contract_ref"}, "requested claim")
        require(claim["kind"] in {"scoped_update", "qualification", "release_support", "diagnostic"} and type(claim["requires_full"]) is bool, "invalid requested claim")
        core._text(claim["text"], "actual requested claim")
        impact = p["impact"]
        core._exact(impact, set("immediate_diff cumulative_diff immediate_requirements cumulative_requirements dependency_closure matched_rules bounded full_triggers".split()), "impact")
        changed = ids(impact["immediate_requirements"], "immediate requirements") | ids(impact["cumulative_requirements"], "cumulative requirements") | ids(impact["dependency_closure"], "dependency closure")
        require(ids(impact["matched_rules"], "matched rules") <= RULES, "invalid impact rule")
        full = ids(impact["full_triggers"], "Full triggers")
        require(full <= {"FIRST_QUALIFICATION", "EXPLICIT_QUALIFICATION", "NEW_CAPABILITY_ENVIRONMENT", "CONTROL_AUTHORITY_CHANGE", "TRANSFER_CHANGE", "FULL_CLAIM_CONTRACT", "UNBOUNDED_IMPACT"}, "unknown Full trigger")
        require(type(impact["bounded"]) is bool, "bounded must be boolean")
        covered = set().union(*(set(self.catalog[a]["requirement_ids"]) for a, s in self.assertions.items() if s["selection"] != "NOT_SELECTED"))
        require(changed <= covered, "immediate/cumulative dependency union lacks selected coverage")
        cp = indexed(p["compatibility"], "id", set("id disposition old_environment new_environment used_capabilities affected_assertions evidence reason".split()), "compatibility", True)
        require(set(cp) == {f"CP-{i:02}" for i in range(1, 5)}, "all four compatibility predicates required")
        for row in cp.values():
            require(row["disposition"] in {"UNCHANGED", "EVIDENCED", "UNRESOLVED", "NOT_APPLICABLE"}, "invalid compatibility disposition")
            require(row["new_environment"] == p["candidate_identity"]["environment"], "compatibility candidate environment mismatch")
            old = p["baseline_identity"]["environment"] if p["baseline_identity"] else None
            require(row["old_environment"] == old, "compatibility baseline environment mismatch")
            ids(row["used_capabilities"], "used capabilities")
            require(ids(row["affected_assertions"], "affected assertions") <= set(self.catalog), "unknown affected assertion")
            core._text(row["reason"], "compatibility reason")
            if row["disposition"] == "UNCHANGED":
                require(old == row["new_environment"] and row["evidence"], "version labels cannot prove unchanged environment")
            if row["disposition"] == "EVIDENCED":
                require(row["evidence"], "compatibility needs actual evidence")
                if row["id"] == "CP-03":
                    require(row["affected_assertions"] and all(self.assertions[a]["selection"] == "REQUIRED" and "N" in self.catalog[a]["evidence_kinds"] for a in row["affected_assertions"]), "CP-03 requires selected actual load/task and affected denial/activation observations")
        matched = set(impact["matched_rules"])
        required_tiers = {a["tier"] for a in self.assertions.values() if a["selection"] == "REQUIRED"}
        for rule, tiers in {"CI-01": {"D", "S"}, "CI-02": {"C", "A"}, "CI-03": {"D", "C", "B"}, "CI-04": {"C", "B"}}.items():
            if rule in matched:
                require(tiers <= required_tiers, rule + " requires its original affected evidence tiers")
        if required_tiers & {"C", "B", "A"}:
            require(self.tasks["T05"]["selection"] == "REQUIRED", "selected native tiers require actual preparation/readiness")
        if p["mode"] == "Routine":
            require(not matched & {"CI-05", "CI-06", "CI-09"}, "matched control/transfer/unbounded impact requires Full")
            require(not full and impact["bounded"] and not claim["requires_full"] and claim["kind"] != "qualification", "requested claim or cumulative impact requires Full")
            require(all(r["disposition"] != "UNRESOLVED" for r in cp.values()), "unresolved used capability prevents Routine adoption")

    def _observations(self):
        p = self.value
        observations = indexed(p["observations"], "observation_id", set("observation_id evidence_kind assertion_ids conditions prerequisite_observation_ids reuse_ref".split()), "observations")
        for oid, row in observations.items():
            require(row["evidence_kind"] in {"D", "S", "N"}, "unsupported observation kind")
            aids = ids(row["assertion_ids"], "observation assertion IDs", True)
            require(aids <= set(self.catalog), "observation orphan assertion")
            c = row["conditions"]
            core._exact(c, CONDITION_KEYS, "observation conditions")
            self.identity(c["identity"])
            require(c["identity"] == (p["baseline_identity"] if c["arm"] == "baseline" else p["candidate_identity"]), "observation identity differs from selected arm")
            require(c["invocation"] in {"explicit", "implicit", "loaded", "none"} and c["before_task"] in TASKS, "unsupported observation invocation/task")
            require(row["evidence_kind"] == "D" or c["prompt_ref"] is not None, "model/native observation needs exact prompt")
            require(c["visibility_ref"] is not None and c["freshness_ref"] is not None, "observation needs actual visibility and freshness bindings")
            for a in aids:
                cat = self.catalog[a]
                require(all(c[k] == cat[k] for k in ("arm", "variant", "repetition")), "cross-arm/variant/repetition evidence sharing")
                require(row["evidence_kind"] in cat["evidence_kinds"] and oid in self.assertions[a]["observation_ids"], "incompatible evidence kind or asymmetric observation mapping")
                require(TASKS.index(c["before_task"]) <= TASKS.index(self.assertions[a]["task_id"]), "late evidence for earlier task")
            require(ids(row["prerequisite_observation_ids"], "observation prerequisites") <= set(observations), "unknown observation predecessor")
        for a in self.assertions.values():
            require(set(a["observation_ids"]) <= set(observations), "unknown selected observation")
        self._acyclic({k: v["prerequisite_observation_ids"] for k, v in observations.items()})
        self._acyclic({k: v["dependency_ids"] for k, v in self.assertions.items()})
        self.observations = observations

    @staticmethod
    def _acyclic(graph):
        seen, active = set(), set()
        def visit(node):
            require(node not in active, "cyclic prerequisite graph")
            if node in seen:
                return
            active.add(node)
            for previous in graph[node]:
                visit(previous)
            active.remove(node)
            seen.add(node)
        for node in graph:
            visit(node)

    def _calls(self):
        fields = set("call_id kind parent_call_id attempt_id assertion_ids observation_ids depends_on producer reviewer review_path interaction managed_worker_required max_seconds".split())
        calls = indexed(self.value["call_graph"], "call_id", fields, "call graph", True)
        attempts = set()
        for cid, row in calls.items():
            require(row["kind"] in {"native_worker", "static_review", "grader", "parent_return", "continuation", "control", "probe", "receiving"}, "unknown call kind")
            aids = ids(row["assertion_ids"], "call assertions")
            oids = ids(row["observation_ids"], "call observations")
            require(aids <= set(self.catalog) and oids <= set(self.observations) and (aids or oids), "orphan call graph node")
            require(ids(row["depends_on"], "call prerequisites") <= set(calls), "unknown call predecessor")
            core._text(row["producer"], "selected call producer")
            if row["kind"] != "static_review":
                require(row["producer"] != self.value["selection_reviewer"], "selection reviewer is a measured worker")
            require(row["interaction"] in {"single-turn", "awaiting-user"} and type(row["managed_worker_required"]) is bool, "invalid typed call interaction")
            positive(row["max_seconds"], "call seconds")
            if row["kind"] == "continuation":
                require(row["parent_call_id"] in calls and row["parent_call_id"] in row["depends_on"], "continuation lacks charged parent dependency")
            else:
                require(row["parent_call_id"] is None, "noncontinuation has parent call")
            if row["attempt_id"] is not None:
                core._text(row["attempt_id"], "native attempt")
                require(row["attempt_id"] not in attempts and row["kind"] != "static_review", "duplicate attempt/static reviewer is not native attempt")
                attempts.add(row["attempt_id"])
            require((row["reviewer"] is None) == (row["review_path"] is None), "reviewer and path must be selected together")
            if row["reviewer"] is not None:
                require(row["reviewer"] != row["producer"], "author-as-grader")
                require(not core._within(store._absolute(row["review_path"], "grade destination"), self.project), "grade destination is worker-writable")
        require(any(c["kind"] == "static_review" and c["producer"] == self.value["selection_reviewer"] and set(self.tasks["T04"]["assertion_ids"]) <= set(c["assertion_ids"]) for c in calls.values()), "T04 must have a real independently selected call")
        self._acyclic({k: v["depends_on"] for k, v in calls.items()})
        self.calls = calls

    def fixed(self):
        return [("validation_policy", path, raw) for path, raw in self.sources.items()]

    def review(self, ref, delivery_ref):
        value = core._json(self.pin(ref, "actual independent T04 review"), "T04 review")
        core._exact(value, set("schema_version run_id candidate_ref reviewer input_refs criteria disagreements additional_reviewer_refs overall plan selection_review".split()), "T04 review")
        require(value["schema_version"] == REVIEW_SCHEMA and value["run_id"] == self.plan["run_id"] and value["plan"] == self.selection["plan"], "T04 schema/run/plan binding mismatch")
        require(value["candidate_ref"] == self.value["candidate_identity"]["candidate"], "T04 candidate mismatch")
        r = value["reviewer"]
        core._exact(r, {"identity", "model", "runtime", "independence_evidence", "limits"}, "T04 reviewer")
        require(r["identity"] == self.value["selection_reviewer"] and r["identity"] != self.author, "T04 producer is not independently selected")
        core._text(r["independence_evidence"], "review independence evidence")
        refs = [{k: x[k] for k in ("path", "sha256")} for x in rows(value["input_refs"], "T04 input refs", True)]
        require(delivery_ref in refs, "T04 lacks exact delivery binding")
        self.walk_pins(refs)
        selection = value["selection_review"]
        core._exact(selection, {"outcome", "reason", "evidence", "reviewed_assertion_ids", "reviewed_rule_ids", "invariant_ids"}, "selection review")
        require(ids(selection["reviewed_assertion_ids"], "reviewed assertions") == set(self.catalog), "T04 lacks complete original assertion projection")
        required_rules = set(self.value["impact"]["matched_rules"]) | {r["id"] for r in self.value["compatibility"]} | set().union(*(set(a["rule_ids"]) for a in self.assertions.values()))
        require(ids(selection["reviewed_rule_ids"], "reviewed rules") == required_rules and ids(selection["invariant_ids"], "review invariants") == INVARIANTS, "T04 omits applicable rules/invariants")
        criteria = indexed(value["criteria"], "id", {"id", "outcome", "reason", "evidence", "finding_ids", "applicability"}, "R01-R10", True)
        require(set(criteria) == INVARIANTS, "T04 must retain R01-R10")
        for c in [selection, *criteria.values()]:
            require(c["outcome"] in OUTCOMES, "unknown review outcome")
            core._text(c["reason"], "review reason")
            rows(c["evidence"], "actual review output", c["outcome"] in {"PASS", "FAIL"})
            self.walk_pins(c["evidence"])
        require(value["overall"] in OUTCOMES, "unknown review summary")
        require(value["overall"] != "PASS" or selection["outcome"] == "PASS" and all(c["outcome"] == "PASS" for c in criteria.values()), "review PASS contradicts actual criteria/selection")
        return value


def load(selection, assignment_raw, project, *, frozen=None):
    return Policy(selection, assignment_raw, project, frozen=frozen)


def gate(value, spec, cfg, state=None):
    """Validate typed disposition; return extra review/policy custody snapshots."""
    p = cfg["validation_policy"]
    core._exact(value, set("schema_version task_id phase producer inputs_sha256 outcome reason evidence gate_id selection disposition validation_plan_sha256 review_sha256".split()), "v2 utility gate")
    require(value["schema_version"] == GATE_SCHEMA and value["gate_id"] == spec["id"], "gate version/identity mismatch")
    require(value["validation_plan_sha256"] == p.selection["plan"]["sha256"], "stale gate plan")
    require(value["selection"] in SELECTIONS and value["disposition"] in DISPOSITIONS, "unknown gate selection/disposition")
    before = set(p.sources)
    if spec["id"] in {"deterministic-inspection", "independent-review"}:
        require(value["selection"] == "REQUIRED" and value["review_sha256"] is None, "static/T04 cannot be excluded or circular")
        require(value["disposition"] == ("SATISFIED" if value["outcome"] == "PASS" else "NOT_RUN" if value["outcome"] == "NOT_RUN" else "BLOCKED"), "static disposition contradicts outcome")
        if spec["id"] == "independent-review":
            require(len(value["evidence"]) == 1, "independent gate requires exactly the actual T04 review")
            review = p.review(value["evidence"][0], {"path": cfg["session"]["delivery_contract"], "sha256": store._hash(cfg["contract_raw"])})
            require(review["overall"] == value["outcome"], "independent gate outcome differs from actual review")
    else:
        task = p.tasks[NATIVE_TASKS[spec["id"]]]
        require(value["selection"] == task["selection"], "post-hoc native selection change")
        require(state is not None, "P4 needs previously admitted independent review")
        prior = next(g for g in cfg["contract"]["gate_inputs"] if g["id"] == "independent-review")
        require(prior["path"] in state.gates, "missing admitted T04 review")
        prior_gate = core._json(state.source(prior["path"], "admitted T04 gate"), "T04 gate")
        review_ref = prior_gate["evidence"][0]
        review = p.review(review_ref, {"path": cfg["session"]["delivery_contract"], "sha256": store._hash(cfg["contract_raw"])})
        require(value["review_sha256"] == review_ref["sha256"], "stale P4 review digest")
        if value["selection"] != "REQUIRED":
            require(review["overall"] == "PASS" and review["selection_review"]["outcome"] == "PASS", "unselected native needs passing independent selection review")
            outcome = "NOT_RUN" if value["selection"] == "NOT_SELECTED" else "NOT_APPLICABLE"
            require(value["outcome"] == outcome and value["disposition"] == "SATISFIED_BY_REVIEWED_SELECTION", "unselected native cannot become PASS or invented N_A")
            require(value["evidence"] == [p.selection["plan"], review_ref], "unselected native must cite exact plan/review")
        else:
            require(value["disposition"] == ("SATISFIED" if value["outcome"] == "PASS" else "NOT_RUN" if value["outcome"] == "NOT_RUN" else "BLOCKED"), "required native outcome cannot be excluded")
            if value["outcome"] in {"PASS", "FAIL"}:
                require(review["overall"] == "PASS", "native execution requires independent T04 PASS")
    return [("gate_evidence", path, raw) for path, raw in p.sources.items() if path not in before]


def _aggregate(values):
    values = list(values)
    for outcome in ("FAIL", "COULD_NOT_RUN", "NOT_RUN"):
        if outcome in values:
            return outcome
    return "PASS" if values else "NOT_RUN"


def reduce_results(raw, policy, review_ref, *, delivery_ref, native_receipts=()):
    """Reduce original assertion judgments; never trust submitted summary fields.

    Returns derived v2 additions and display fields. The caller compares claimed
    additions before accepting bytes. Synthetic records cannot authenticate N.
    """
    p = policy
    value = core._json(raw, "validation results")
    core._exact(value, RESULT_KEYS, "validation results")
    require(value["schema_version"] == RESULTS_SCHEMA and value["run_id"] == p.plan["run_id"] and value["plan"] == p.selection["plan"], "results schema/run/plan mismatch")
    require(value["ai_review"] == review_ref, "results selected review mismatch")
    review = p.review(review_ref, delivery_ref)
    require(value["lineage"] == p.value["lineage"], "results cannot overwrite owner lineage")
    if value["owner_acceptance_ref"] is not None:
        p.pin(value["owner_acceptance_ref"], "external owner acceptance")
        require(value["owner_acceptance_ref"] in p.value["lineage"]["acceptance_chain"], "new acceptance cannot be manufactured by results")
    tasks = indexed(value["task_results"], "task_id", set("task_id classification selection disposition outcome reason evidence".split()), "task results", True)
    require(tuple(tasks) == TASKS, "results omit ordered twelve task obligations")
    for task, row in tasks.items():
        selection = p.tasks[task]
        require(all(row[k] == selection[k] for k in ("classification", "selection")), "task result changes frozen selection")
        require(row["outcome"] in OUTCOMES and row["disposition"] in DISPOSITIONS, "invalid task result")
        core._text(row["reason"], "task result reason")
        if row["selection"] != "REQUIRED":
            require(row["outcome"] == ("NOT_RUN" if row["selection"] == "NOT_SELECTED" else "NOT_APPLICABLE") and row["disposition"] == "SATISFIED_BY_REVIEWED_SELECTION", "unselected task outcome was relabeled")
        if row["disposition"] == "SATISFIED":
            rows(row["evidence"], "task completion evidence", True)
        p.walk_pins(row["evidence"])
    results = indexed(value["assertion_results"], "assertion_id", set("assertion_id selection outcome integrity observation_refs grade_refs reason".split()), "assertion results", True)
    require(set(results) == set(p.catalog), "results must account for every original assertion/variant")
    effective, tier_outcomes, observed = {}, {t: [] for t in ("D", "S", "C", "B", "A")}, {}
    for aid, row in results.items():
        selected, cat = p.assertions[aid], p.catalog[aid]
        require(row["selection"] == selected["selection"] and row["outcome"] in OUTCOMES, "assertion selection/outcome mismatch")
        require(row["integrity"] in {"INTACT", "UNOBTAINABLE", "CONTAMINATED", "NOT_OBSERVED"}, "unknown observation integrity")
        core._text(row["reason"], "assertion reason")
        rows(row["observation_refs"], "assertion observations")
        rows(row["grade_refs"], "assertion grades")
        if row["selection"] == "NOT_SELECTED":
            require(row["outcome"] == "NOT_RUN" and row["integrity"] == "NOT_OBSERVED" and not row["observation_refs"] and not row["grade_refs"], "unselected assertion must remain NOT_RUN/NOT_OBSERVED")
        elif row["selection"] == "NOT_APPLICABLE":
            require(row["outcome"] == "NOT_APPLICABLE", "scope exclusion outcome mismatch")
        elif row["outcome"] in {"PASS", "FAIL"}:
            require(row["integrity"] == "INTACT" and row["observation_refs"], "PASS/FAIL needs intact actual evidence")
            kinds, raw_refs = set(), []
            for ref in row["observation_refs"]:
                run = core._json(p.pin(ref, "actual observation"), "actual observation")
                core._exact(run, RUN_KEYS, "v2 observation")
                require(run.get("schema_version") == "devforge.skill-run/v2", "unknown observation schema")
                oid = run.get("observation_id")
                require(oid in p.observations and oid in selected["observation_ids"], "unselected/orphan observation")
                planned = p.observations[oid]
                require(run.get("validation_plan_ref") == p.selection["plan"] and run.get("conditions") == planned["conditions"] and run.get("assertion_ids") == planned["assertion_ids"], "observation candidate/environment/prompt/arm/visibility/freshness mismatch")
                require(run.get("selection") == "REQUIRED" and run.get("integrity") == "INTACT" and run.get("outcome") in {"PASS", "FAIL"} and run.get("evidence_kind") == planned["evidence_kind"], "observation disposition/kind mismatch")
                refs = rows(run.get("raw_output_refs"), "complete actual raw outputs", True)
                p.walk_pins(refs)
                require(all(x in observed for x in planned["prerequisite_observation_ids"]), "evidence predates required predecessor")
                require(run["case_id"] == cat["case_id"] and run["arm"] == cat["arm"] and run["tier"] == selected["tier"] and run["provider"] == "codex", "observation case/arm/tier/provider mismatch")
                if planned["evidence_kind"] == "N":
                    imported = native_receipts.get(run["attempt_id"]) if isinstance(native_receipts, dict) else None
                    require(imported is not None and imported.get("eligible") is True and any(r["sha256"] == imported["sha256"] for r in refs), "synthetic/unimported evidence cannot satisfy native assertion")
                    require(any(c["attempt_id"] == run["attempt_id"] and oid in c["observation_ids"] for c in p.calls.values()), "native receipt does not belong to selected observation call")
                observed[oid] = run
                kinds.add(planned["evidence_kind"])
                raw_refs.extend(refs)
            require(set(cat["evidence_kinds"]) <= kinds, "original evidence kind coverage incomplete")
            if "S" in kinds or "N" in kinds:
                require(row["grade_refs"], "selected semantic/native evidence needs per-assertion independent judgment")
                judgments = []
                for ref in row["grade_refs"]:
                    grade = core._json(p.pin(ref, "actual per-arm grade"), "actual per-arm grade")
                    core._exact(grade, GRADE_KEYS, "v2 per-arm grade")
                    require(grade.get("schema_version") == "devforge.skill-case-grade/v2" and grade.get("arm") == cat["arm"] and grade.get("case_id") == cat["case_id"], "grade schema/arm/case mismatch")
                    require(grade["run_manifest"] in row["observation_refs"] and grade["case_definition"] == cat["source_ref"], "grade run/original case identity mismatch")
                    grader = grade.get("grader", {})
                    core._exact(grader, {"identity", "model", "independence_evidence"}, "independent grader")
                    core._text(grader["independence_evidence"], "grader independence evidence")
                    grader = grader.get("identity") if isinstance(grader, dict) else grader
                    allowed = {c["reviewer"] for c in p.calls.values() if aid in c["assertion_ids"]} | {c["producer"] for c in p.calls.values() if c["kind"] in {"grader", "static_review"} and aid in c["assertion_ids"]}
                    require(grader in allowed and grader is not None and grader != p.author, "author-as-grader or unselected grade producer")
                    batch = indexed(grade.get("assertion_judgments"), "assertion_id", {"assertion_id", "outcome", "reason", "evidence"}, "independent assertion judgments", True)
                    require(aid in batch and batch[aid]["outcome"] == row["outcome"], "pair summary cannot overwrite candidate-specific judgment")
                    require(all(r in batch[aid]["evidence"] for r in raw_refs), "grade omits actual raw output or arm")
                    p.walk_pins(batch[aid]["evidence"])
                    judgments.append(batch[aid])
        else:
            require(row["outcome"] in {"NOT_RUN", "COULD_NOT_RUN"}, "selected assertion cannot invent N_A")
        result = row["outcome"]
        if selected["expectation"] == "observation" and result in {"PASS", "FAIL"}:
            result = "PASS"
        effective[aid] = result
        tier_outcomes[selected["tier"]].append(row["outcome"])
    transfer = value["receiving_transfer"]
    core._exact(transfer, set("selection outcome target_output receiver_contract receiver_observation completed_action observed_at_utc reason".split()), "receiving transfer")
    require(transfer["selection"] in SELECTIONS and transfer["outcome"] in OUTCOMES, "invalid receiving transfer")
    core._text(transfer["reason"], "receiving reason")
    receiving_required = p.value["mode"] == "Full" and any(a["task_id"] == "T09" and a["selection"] == "REQUIRED" and "N" in p.catalog[k]["evidence_kinds"] for k, a in p.assertions.items())
    if receiving_required:
        require(transfer["selection"] == "REQUIRED", "Full target receiving cannot be replaced by T12 prepared handoff")
    if transfer["outcome"] in {"PASS", "FAIL"}:
        require(transfer["selection"] == "REQUIRED", "unselected receiving cannot claim an observation")
        for key in ("target_output", "receiver_contract", "receiver_observation", "completed_action"):
            p.pin(transfer[key], "actual receiving " + key)
        store._stamp(transfer["observed_at_utc"], "actual receiving UTC")
        require(isinstance(native_receipts, dict) and any(r.get("eligible") is True and r["sha256"] == transfer["receiver_observation"]["sha256"] for r in native_receipts.values()), "receiving needs authenticated actual target/receiver observation")
    required = [effective[k] for k, a in p.assertions.items() if a["selection"] == "REQUIRED"]
    required.append(review["overall"])
    if receiving_required:
        required.append(transfer["outcome"])
    overall = _aggregate(required)
    coverage = all(x in {"PASS", "FAIL"} for x in required)
    disposition = ("ROUTINE_PASS" if p.value["mode"] == "Routine" else "FULL_PASS") if overall == "PASS" else "FAIL" if overall == "FAIL" else "INSUFFICIENT_EVIDENCE"
    completion = "COMPLETE" if all(t["disposition"] in {"SATISFIED", "SATISFIED_BY_REVIEWED_SELECTION"} for t in tasks.values()) else "BLOCKED" if any(t["disposition"] == "BLOCKED" for t in tasks.values()) else "PARTIAL"
    eligible = disposition == "ROUTINE_PASS"
    for cp in p.value["compatibility"]:
        if cp["disposition"] == "EVIDENCED":
            eligible = eligible and all(effective[a] == "PASS" for a in cp["affected_assertions"])
    derived = {"task_results": value["task_results"], "assertion_results": value["assertion_results"], "report_completion": completion,
               "validation_disposition": disposition, "routine_adoption_eligible": eligible, "lineage": p.value["lineage"],
               "owner_acceptance_ref": value["owner_acceptance_ref"], "receiving_transfer": transfer,
               "overall": overall, "coverage_complete": coverage, "groups": {t: _aggregate(v) for t, v in tier_outcomes.items()},
               "disposition": "suitable_for_stated_scope" if overall == "PASS" else "revise" if overall == "FAIL" else "insufficient_evidence", "external_acceptance": "NOT_GRANTED"}
    return derived


def reduce_decision(raw, policy, review_ref, *, delivery_ref, native_receipts=()):
    """Recompute helper claims from its pinned actual results before custody."""
    value = core._json(raw, "validation decision")
    base = set("schema_version run_id created_at_utc plan results input_refs candidate_root structural_report ai_review ai_record_consistency candidate_freshness groups checks overall coverage_complete disposition external_acceptance limits".split())
    core._exact(value, base | RESULT_ADDITIONS, "v2 validation decision")
    require(value["schema_version"] == DECISION_SCHEMA and value["plan"] == policy.selection["plan"] and value["run_id"] == policy.plan["run_id"], "decision schema/plan/run binding mismatch")
    require(value["candidate_root"] == policy.plan["candidate_root"] and value["ai_review"] == review_ref, "decision candidate/review mismatch")
    store._stamp(value["created_at_utc"], "decision creation UTC")
    result_raw = policy.pin(value["results"], "actual reduced results", protected=False)
    derived = reduce_results(result_raw, policy, review_ref, delivery_ref=delivery_ref, native_receipts=native_receipts)
    for key in RESULT_ADDITIONS | {"overall", "coverage_complete", "groups", "disposition", "external_acceptance"}:
        require(value[key] == derived[key], "decision summary differs from original assertion reduction: " + key)
    return derived
