"""Synthetic installer predicates; no real evaluation, acceptance or native claim."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

import test_installer

installer = test_installer.installer


class ManualAdoptionTest(unittest.TestCase):
    setUp = test_installer.InstallerTest.setUp
    snapshot = test_installer.InstallerTest.snapshot

    def put(self, name, value):
        path = self.root / 'evidence' / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def prepare(self, mode='Full'):
        self.name = 'devforge-evaluate-expert'
        source = self.skill.parent.with_name(self.name)
        self.skill.parent.rename(source)
        self.skill = source / 'SKILL.md'
        self.raw = self.put('raw.txt', 'Synthetic evidence only. No real model or acceptance.')
        self.manifest = self.put('manifest.json', {
            'schema_version': 'devforge.expert-runtime-manifest/v1', 'name': self.name,
            'files_sha256': {'SKILL.md': hashlib.sha256(self.skill.read_bytes()).hexdigest()}})
        self.specification = self.put('spec.md', 'Frozen fixture requirements, independent of implementation.')
        tiers = ['D', 'S', 'C', 'B', 'A'] if mode == 'Full' else ['D']
        self.cases = self.put('cases.json', {'cases': [
            {'id': f'CASE-{i}', 'tier': tier, 'required_observations': ['Preserve the declared fixture boundary']}
            for i, tier in enumerate(tiers, 1)]})
        self.creator = {'schema_version': 'devforge.expert-creator-completion/v1',
                        'author': 'fixture-author', 'candidate': self.manifest,
                        'specification': self.specification,
                        'phases': {name: {'classification': 'Enforced', 'evidence': [self.raw]}
                                   for name in ('Intake', 'Selection', 'Design', 'Authoring', 'PreparedTransfer')}}
        tasks = [f'T{i:02}' for i in range(1, 13)]
        self.plan = {'schema_version': 'devforge.skill-validation-plan/v2', 'run_id': 'synthetic-install',
                     'assignment': {'owner': 'fixture-evaluator'},
                     'input_refs': [{'kind': key, **value} for key, value in
                                    [('specification', self.specification), ('cases', self.cases)]],
                     'validation_policy': {
                         'version': 'VPR-2', 'mode': mode, 'selection_reviewer': 'fixture-reviewer',
                         'candidate_identity': {'candidate': self.manifest},
                         'impact': {'bounded': True, 'full_triggers': [] if mode == 'Routine' else ['TRANSFER_CHANGE'],
                                    'matched_rules': ['CI-01'] if mode == 'Routine' else ['CI-06']},
                         'requested_claim': {'requires_full': mode == 'Full'}, 'lineage': {'fixture': 'unqualified'},
                         'assertions': [{'assertion_id': 'CASE-1', 'task_id': 'T03', 'tier': 'D',
                                         'selection': 'REQUIRED', 'expectation': 'pass'}],
                         'task_selection': [{'task_id': task, 'classification': 'Enforced', 'selection': 'REQUIRED'} for task in tasks]}}
        self.review = {'schema_version': 'devforge.skill-ai-review/v2', 'run_id': 'synthetic-install',
                       'candidate_ref': self.manifest,
                       'reviewer': {'identity': 'fixture-reviewer', 'independence_evidence': 'Synthetic distinct producer'},
                       'overall': 'PASS', 'selection_review': {'outcome': 'PASS', 'reviewed_assertion_ids': ['CASE-1']},
                       'criteria': [{'id': f'R{i:02}', 'outcome': 'PASS', 'reason': 'Synthetic fixture judgment', 'evidence': [self.raw]}
                                    for i in range(1, 11)]}
        self.results = {'schema_version': 'devforge.skill-validation-results/v2', 'run_id': 'synthetic-install',
                        'validation_disposition': mode.upper() + '_PASS',
                        'lineage': self.plan['validation_policy']['lineage'],
                        'task_results': [{'task_id': task, 'classification': 'Enforced', 'selection': 'REQUIRED',
                                          'disposition': 'SATISFIED', 'outcome': 'PASS', 'evidence': [self.raw]} for task in tasks],
                        'assertion_results': [{'assertion_id': 'CASE-1', 'selection': 'REQUIRED', 'integrity': 'INTACT',
                                               'outcome': 'PASS', 'observation_refs': [self.raw], 'grade_refs': []}],
                        'receiving_transfer': {'selection': 'REQUIRED', 'outcome': 'PASS',
                                               'observed_at_utc': '2026-09-08T12:00:00Z',
                                               **{key: self.raw for key in ('target_output', 'receiver_contract', 'receiver_observation', 'completed_action')}}}
        self.decision = {'schema_version': 'devforge.skill-validation-decision/v2', 'run_id': 'synthetic-install',
                         'overall': 'PASS', 'coverage_complete': True, 'external_acceptance': 'NOT_GRANTED',
                         'validation_disposition': mode.upper() + '_PASS', 'routine_adoption_eligible': mode == 'Routine',
                         'lineage': self.plan['validation_policy']['lineage'],
                         'checks': [{'check_id': 'CASE-1', 'effective_outcome': 'PASS'}]}
        selection = self.plan['validation_policy']
        if mode == 'Routine':
            baseline = {'candidate': self.manifest, 'environment': self.raw}
            lineage = {'qualified_anchor': {'status': 'ABSENT', 'identity': None, 'evidence': None},
                       'accepted_unqualified_baseline': baseline, 'current_routinely_accepted': baseline,
                       'previous_acceptance': None, 'acceptance_chain': []}
            previous = self.put('previous.json', {'candidate_identity': baseline,
                                                 'accepted_scope_ref': self.raw, 'lineage': copy.deepcopy(lineage)})
            lineage.update(previous_acceptance=previous, acceptance_chain=[previous])
            selection.update(baseline_identity=baseline, accepted_scope_ref=self.raw, lineage=lineage,
                             compatibility=[{'id': f'CP-{i:02}', 'disposition': 'UNCHANGED',
                                             'reason': 'Synthetic unchanged environment', 'evidence': [self.raw]}
                                            for i in range(1, 5)])
            selection['impact'].update(immediate_diff=self.raw, cumulative_diff=self.raw)
            self.results['lineage'] = self.decision['lineage'] = lineage
        selection['catalog_refs'] = [self.cases]
        selection['catalog_assertions'] = [
            {'assertion_id': f'CASE-{i}', 'case_id': f'CASE-{i}', 'source_ref': self.cases,
             'source_pointer': f'/cases/{i-1}/required_observations/0',
             'evidence_kinds': ['N' if tier in ('C', 'B', 'A') else tier]}
            for i, tier in enumerate(tiers, 1)]
        selection['assertions'] = [
            {'assertion_id': f'CASE-{i}', 'task_id': 'T03', 'tier': tier,
             'selection': 'REQUIRED', 'expectation': 'pass'} for i, tier in enumerate(tiers, 1)]
        self.review['selection_review']['reviewed_assertion_ids'] = [f'CASE-{i}' for i in range(1, len(tiers)+1)]
        self.results['assertion_results'] = [
            {'assertion_id': f'CASE-{i}', 'selection': 'REQUIRED', 'integrity': 'INTACT',
             'outcome': 'PASS', 'observation_refs': [self.raw], 'grade_refs': [self.raw] if tier != 'D' else []}
            for i, tier in enumerate(tiers, 1)]
        self.decision['checks'] = [{'check_id': f'CASE-{i}', 'effective_outcome': 'PASS'} for i in range(1, len(tiers)+1)]
        self.freeze()

    def freeze(self):
        creator = self.put('creator.json', self.creator)
        plan = self.put('plan.json', self.plan)
        self.review['plan'] = plan
        review = self.put('review.json', self.review)
        self.results.update(plan=plan, ai_review=review)
        results = self.put('results.json', self.results)
        self.decision.update(plan=plan, ai_review=review, results=results,
                             task_results=copy.deepcopy(self.results['task_results']),
                             assertion_results=copy.deepcopy(self.results['assertion_results']))
        decision = self.put('decision.json', self.decision)
        package = {'name': self.name, 'manifest': self.manifest, 'specification': self.specification,
                   'cases': self.cases, 'creator': creator, 'plan': plan, 'review': review,
                   'results': results, 'decision': decision}
        acceptance = {'schema_version': 'devforge.expert-install-acceptance/v1', 'owner': 'fixture-operator',
                      'project_root': str(self.project), 'package': self.name, 'action': 'install',
                      'inputs': {key: value for key, value in package.items() if key != 'name'},
                      'observation_basis': 'operator-reviewed actual evidence'}
        package['acceptance'] = self.put('acceptance.json', acceptance)
        bundle = {'schema_version': 'devforge.manual-expert-adoption/v1', 'owner': 'fixture-operator',
                  'project_root': str(self.project), 'authorization': self.raw, 'packages': [package]}
        self.evidence_path = Path(self.put('adoption.json', bundle)['path'])

    def install(self):
        return installer.install(self.framework, self.project, 'both', manual_evidence=self.evidence_path)

    def refused(self, reason):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, reason):
            self.install()
        self.assertEqual(self.snapshot(), before)

    def test_exact_full_evidence_installs_and_records_custody(self):
        self.prepare()
        self.install()
        self.install()
        installed = self.project / '.agents/skills' / self.name / 'SKILL.md'
        self.assertEqual(installed.read_bytes(), self.skill.read_bytes())
        inventory = json.loads((self.project / '.devforge-install.json').read_text())
        self.assertEqual(inventory['manual_expert_adoption']['record']['sha256'],
                         hashlib.sha256(self.evidence_path.read_bytes()).hexdigest())

    def test_bounded_routine_can_be_adopted_without_qualification(self):
        self.prepare('Routine')
        self.install()
        self.assertEqual(self.decision['validation_disposition'], 'ROUTINE_PASS')

    def test_creator_phase_and_evaluator_task_cannot_be_omitted(self):
        self.prepare()
        del self.creator['phases']['Design']
        self.freeze()
        self.refused('five creator phases')
        self.creator['phases']['Design'] = {'classification': 'Enforced', 'evidence': [self.raw]}
        self.results['task_results'].pop(3)
        self.freeze()
        self.refused('twelve tasks')

    def test_self_review_and_optional_classification_are_refused(self):
        self.prepare()
        self.review['reviewer']['identity'] = 'fixture-author'
        self.freeze()
        self.refused('reviewer is not separately assigned')
        self.review['reviewer']['identity'] = 'fixture-reviewer'
        self.creator['phases']['Authoring']['classification'] = 'Optional'
        self.freeze()
        self.refused('classification changed')

    def test_missing_evidence_does_not_become_pass_from_summary(self):
        self.prepare()
        self.results['assertion_results'][0]['outcome'] = 'NOT_RUN'
        self.freeze()
        self.refused('assertion incomplete')

    def test_stale_candidate_or_leaf_evidence_refuses_before_writes(self):
        self.prepare()
        self.skill.write_text('new unreviewed bytes')
        self.refused('planned bytes')
        self.skill.write_text('codex skill')
        Path(self.raw['path']).write_text('changed evidence')
        self.refused('stale evidence')

    def test_full_trigger_cannot_be_relabelled_routine(self):
        self.prepare('Routine')
        self.plan['validation_policy']['impact']['matched_rules'] = ['CI-05']
        self.freeze()
        self.refused('ineligible Routine')

    def test_full_requires_actual_transfer_references(self):
        self.prepare()
        self.results['receiving_transfer']['receiver_observation'] = None
        self.freeze()
        self.refused('invalid evidence pin')

    def test_duplicate_record_and_wrong_destination_are_refused(self):
        self.prepare()
        raw = self.evidence_path.read_text()
        self.evidence_path.write_text(raw[:-1] + ', "owner": "another"}')
        self.refused('duplicate JSON key')
        self.freeze()
        bundle = json.loads(self.evidence_path.read_text())
        bundle['project_root'] = str(self.root)
        self.evidence_path.write_text(json.dumps(bundle))
        self.refused('wrong installation destination')

    def test_last_moment_evidence_drift_preserves_existing_installation(self):
        self.prepare()
        self.install()
        original = installer.plan_hook_merge
        def drift(*args, **kwargs):
            result = original(*args, **kwargs)
            Path(self.raw['path']).write_text('drift after preflight')
            return result
        with mock.patch.object(installer, 'plan_hook_merge', side_effect=drift):
            self.refused('stale evidence')

    def test_export_is_unaccepted_staging_without_circular_adoption_requirement(self):
        self.prepare()
        result = installer.export_plugin(self.framework, 'codex', self.root / 'stage/devforgeai')
        self.assertEqual(result['adoption'], 'NOT_ACCEPTED_STAGING')
        self.assertFalse((self.project / '.agents').exists())

    def test_case_catalog_cannot_be_dropped_from_mutually_consistent_summaries(self):
        self.prepare()
        self.cases = self.put('cases.json', {'cases': [
            {'id': 'CASE-1', 'tier': 'D', 'required_observations': ['Must inspect exact bytes']},
            {'id': 'MISSING-CASE', 'tier': 'B', 'required_observations': ['Must exercise the failure path']}]})
        self.plan['input_refs'] = [{'kind': 'specification', **self.specification}, {'kind': 'cases', **self.cases}]
        self.plan['validation_policy']['catalog_refs'] = [self.cases]
        for row in self.plan['validation_policy']['catalog_assertions']:
            row['source_ref'] = self.cases
        self.freeze()
        self.refused('catalog')

    def test_install_cannot_invalidate_its_own_accepted_evidence(self):
        self.prepare()
        self.install()
        record = self.project / '.devforge-install.json'
        self.creator['phases']['Intake']['evidence'].append(
            {'path': str(record), 'sha256': hashlib.sha256(record.read_bytes()).hexdigest()})
        self.freeze()
        self.refused('invalidate')

    def test_malformed_nested_record_is_structured_refusal(self):
        self.prepare()
        self.review['reviewer'] = None
        self.freeze()
        self.refused('malformed|invalid')

    def test_routine_cannot_invent_an_accepted_baseline(self):
        self.prepare('Routine')
        self.plan['validation_policy']['baseline_identity'] = None
        self.freeze()
        self.refused('Routine.*baseline')

    def test_targeted_install_preserves_other_skills_agents_and_hook_inventory(self):
        self.prepare()
        # Both promoted identities are selected only when present; this fixture supplies one.
        other = self.skill.parent.with_name('unrelated') / 'SKILL.md'
        other.parent.mkdir()
        other.write_text('unrelated upstream source')
        settings = self.project / '.codex/hooks.json'
        settings.parent.mkdir()
        settings.write_text('{"hooks":{},"user_setting":true}')
        inventory = self.project / '.devforge-install.json'
        inventory.write_text(json.dumps({'schema': 1, 'files': {}, 'managed_hooks': {},
                                         'runtime_evidence': {'codex': {'retained': 'unrelated runtime'}}}))
        before_settings = settings.read_bytes()
        result = installer.install(self.framework, self.project, 'codex',
                                   manual_evidence=self.evidence_path, manual_experts_only=True)
        self.assertEqual(settings.read_bytes(), before_settings)
        self.assertFalse((self.project / '.agents/skills/unrelated').exists())
        self.assertEqual(result['scope'], 'promoted Codex experts only; agents/hooks preserved')
        self.assertEqual(json.loads(inventory.read_text())['runtime_evidence'],
                         {'codex': {'retained': 'unrelated runtime'}})

    def test_install_cannot_invalidate_evidence_through_hardlink_alias(self):
        self.prepare()
        self.install()
        record = self.project / '.devforge-install.json'
        alias = self.root / 'evidence/inventory-alias.json'
        alias.hardlink_to(record)
        self.creator['phases']['Intake']['evidence'].append(
            {'path': str(alias), 'sha256': hashlib.sha256(alias.read_bytes()).hexdigest()})
        self.freeze()
        self.refused('invalidate')
