"""Synthetic local-adoption predicates; never native qualification evidence."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

import test_installer

installer = test_installer.installer
CHECKS = {
    'package_integrity': 'D', 'installed_resources': 'D', 'independent_semantics': 'S',
    'grounded_creation': 'N', 'reuse': 'N', 'bounded_enhancement': 'N',
    'missing_evidence_refusal': 'N', 'creator_to_evaluator': 'N', 'evaluator_to_creator': 'N',
}


class LocalBaselineTest(unittest.TestCase):
    setUp = test_installer.InstallerTest.setUp
    snapshot = test_installer.InstallerTest.snapshot

    def put(self, name, value):
        path = self.root / 'evidence' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return self.pin(path)

    @staticmethod
    def pin(path):
        return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def prepare(self):
        self.raw = self.put('raw.txt', 'Synthetic fixture: not real native observation or acceptance.')
        self.history = self.put('historical-failure.txt', 'Retained original FAIL; never rewritten.')
        self.packages = []
        for name in sorted(installer.manual_adoption.NAMES):
            source = self.skill.parent.with_name(name)
            source.mkdir()
            (source / 'SKILL.md').write_text('Exact synthetic ' + name)
            (source / 'evals').mkdir()
            cases = source / 'evals/evals.json'
            cases.write_text(json.dumps({'cases': [{'id': 'ORIGINAL-1', 'required_observations': ['Original native assertion']},
                                                   {'id': 'ORIGINAL-2', 'required_observations': ['Remaining qualification case']}]}))
            files = {str(p.relative_to(source)): self.pin(p)['sha256'] for p in source.rglob('*') if p.is_file()}
            manifest = self.put(name + '-runtime.json', {'schema_version': 'devforge.expert-runtime-manifest/v1',
                'name': name, 'files_sha256': {'SKILL.md': files['SKILL.md']}})
            source_manifest = self.put(name + '-source.json', {'source_root': str(source), 'files_sha256': files})
            self.packages.append({'name': name, 'manifest': manifest, 'source_manifest': source_manifest,
                'specification': self.raw, 'cases': self.pin(cases), 'author': 'fixture-author'})
        self.plan = {'schema_version': 'devforge.manual-local-acceptance-set/v1',
            'project_root': str(self.project), 'owner': 'fixture-owner', 'authorization': self.raw,
            'packages': self.packages, 'historical_evidence': [self.history],
            'frozen_at_utc': '2026-09-08T12:00:00Z', 'max_seconds': 600, 'max_native_turns': 7,
            'checks': {key: {'kind': kind, 'expectations': ['Frozen observable requirement for ' + key]}
                       for key, kind in CHECKS.items()}}
        self.observations = {}
        for key, kind in CHECKS.items():
            if kind != 'N':
                continue
            self.observations[key] = {'schema_version': 'devforge.manual-local-observation/v1',
                'outcome': 'PASS', 'packages': {p['name']: p['manifest'] for p in self.packages},
                'native_client': 'codex', 'model': 'gpt-6-astra', 'reasoning_effort': 'medium',
                'actor': 'fixture-worker',
                'state_isolation': self.raw, 'transcript': self.raw, 'artifacts': [self.raw],
                'started_at_utc': '2026-09-08T12:00:01Z', 'finished_at_utc': '2026-09-08T12:00:02Z',
                'manual_transfer': None}
            if key in ('creator_to_evaluator', 'evaluator_to_creator'):
                self.observations[key]['manual_transfer'] = {'direction': key, 'user': 'fixture-user',
                    **{field: self.raw for field in ('user_request', 'producer_output', 'receiver_observation', 'completed_action')}}
        self.results = {'schema_version': 'devforge.manual-local-acceptance-results/v1',
            'qualification_status': 'UNQUALIFIED', 'started_at_utc': '2026-09-08T12:00:01Z',
            'finished_at_utc': '2026-09-08T12:00:03Z', 'native_turns': 6,
            'qualification_cases': {p['name']: {'ORIGINAL-1': 'NOT_RUN', 'ORIGINAL-2': 'NOT_RUN'} for p in self.packages}}
        self.review = {'schema_version': 'devforge.manual-local-review/v1', 'reviewer': 'fixture-independent-reviewer',
            'independence_evidence': self.raw, 'overall': 'PASS',
            'criteria': {f'R{i:02}': {'outcome': 'PASS', 'reason': 'Synthetic semantic judgment', 'evidence': [self.raw]}
                         for i in range(1, 11)}}
        self.freeze()

    def freeze(self):
        plan = self.put('set.json', self.plan)
        rows = {}
        for key, kind in CHECKS.items():
            obs = None
            if kind == 'N':
                self.observations[key]['acceptance_set'] = plan
                obs = self.put(key + '.json', self.observations[key])
            rows[key] = {'outcome': 'PASS', 'evidence': [obs or self.raw], 'native_observation': obs}
        self.results.update(acceptance_set=plan, checks=rows)
        self.review.update(acceptance_set=plan, packages=self.packages,
            check_judgments={key: {'outcome': 'PASS', 'reason': 'Synthetic separate judgment', 'evidence': row['evidence']}
                             for key, row in rows.items()})
        self.publish()

    def publish(self):
        record = {'schema_version': 'devforge.manual-expert-local-baseline/v1',
            'project_root': str(self.project), 'owner': 'fixture-owner', 'authorization': self.raw,
            'packages': self.packages, 'acceptance_set': self.put('set.json', self.plan),
            'results': self.put('results.json', self.results), 'review': self.put('review.json', self.review),
            'historical_evidence': [self.history]}
        record['acceptance'] = self.put('acceptance.json', {'schema_version': 'devforge.manual-local-owner-acceptance/v1',
            'owner': 'fixture-owner', 'action': 'install_unqualified_local_baseline',
            'qualification_status': 'UNQUALIFIED', 'inputs': copy.deepcopy(record),
            'observation_basis': 'operator-reviewed actual evidence'})
        self.evidence_path = Path(self.put('adoption.json', record)['path'])

    def install(self):
        return installer.install(self.framework, self.project, 'codex', manual_experts_only=True,
                                 manual_evidence=self.evidence_path)

    def refused(self, reason):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, reason):
            self.install()
        self.assertEqual(self.snapshot(), before)

    def test_owner_approved_local_set_installs_exact_unqualified_baseline(self):
        self.prepare()
        try:
            result = self.install()
        except ValueError as error:
            self.fail('Owner-approved passing local set should install unqualified: ' + str(error))
        self.assertEqual(result['qualification_status'], 'UNQUALIFIED')
        inventory = json.loads((self.project / '.devforge-install.json').read_text())
        self.assertEqual(inventory['manual_expert_adoption']['acceptance_status'], 'LOCAL_ACCEPTANCE_SET_PASS')
        for package in self.packages:
            installed = self.project / '.agents/skills' / package['name']
            self.assertEqual((installed / 'SKILL.md').read_bytes(),
                             (self.skill.parent.with_name(package['name']) / 'SKILL.md').read_bytes())
            self.assertFalse((installed / 'evals').exists())
        self.assertEqual(Path(self.history['path']).read_text(), 'Retained original FAIL; never rewritten.')

    def test_remaining_cases_cannot_be_omitted_or_relabelled_pass(self):
        self.prepare()
        self.results['qualification_cases'][self.packages[0]['name']].pop('ORIGINAL-2')
        self.publish()
        self.refused('qualification cases')
        self.results['qualification_cases'][self.packages[0]['name']]['ORIGINAL-2'] = 'PASS'
        self.publish()
        self.refused('qualification cases')

    def test_failed_selected_check_blocks_even_with_passing_summary(self):
        self.prepare()
        self.results['checks']['grounded_creation']['outcome'] = 'NOT_RUN'
        self.publish()
        self.refused('acceptance check')

    def test_plan_must_precede_observations(self):
        self.prepare()
        self.plan['frozen_at_utc'] = '2026-09-08T12:00:04Z'
        self.freeze()
        self.refused('predefined')

    def test_manual_handoff_cannot_be_replaced_with_prepared_text(self):
        self.prepare()
        self.observations['creator_to_evaluator']['manual_transfer'] = None
        self.freeze()
        self.refused('manual transfer')

    def test_source_only_addition_refuses_before_writes(self):
        self.prepare()
        source = self.skill.parent.with_name(self.packages[0]['name'])
        (source / 'evals/new.md').write_text('Unreviewed source-only addition')
        self.refused('source identity')

    def test_historical_failure_changes_refuse_before_writes(self):
        self.prepare()
        Path(self.history['path']).write_text('Rewritten to PASS')
        self.refused('stale evidence')

    def test_exact_owner_acceptance_cannot_be_downgraded_to_generic_install(self):
        self.prepare()
        root = json.loads(self.evidence_path.read_text())
        acceptance = json.loads(Path(root['acceptance']['path']).read_text())
        acceptance['action'] = 'install'
        root['acceptance'] = self.put('acceptance.json', acceptance)
        self.evidence_path.write_text(json.dumps(root))
        self.refused('owner acceptance')

    def test_author_cannot_supply_independent_review(self):
        self.prepare()
        self.review['reviewer'] = 'fixture-author'
        self.publish()
        self.refused('independent reviewer')

    def test_last_moment_source_only_addition_is_rechecked(self):
        self.prepare()
        source = self.skill.parent.with_name(self.packages[0]['name'])
        original = installer.manual_adoption.Evidence.recheck
        calls = 0

        def recheck(evidence):
            nonlocal calls
            calls += 1
            if calls == 2:
                (source / 'evals/late.md').write_text('Added after initial source validation')
            return original(evidence)

        with mock.patch.object(installer.manual_adoption.Evidence, 'recheck', recheck):
            self.refused('source identity changed before install')

    def test_native_actor_cannot_grade_its_own_observation(self):
        self.prepare()
        self.review['reviewer'] = 'fixture-worker'
        self.publish()
        self.refused('independent reviewer')

    def test_local_baseline_cannot_refresh_unrelated_framework_components(self):
        self.prepare()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'manual-experts-only'):
            installer.install(self.framework, self.project, 'codex', manual_evidence=self.evidence_path)
        self.assertEqual(self.snapshot(), before)
