from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

import test_detect_changes as controls


CI = Path(__file__).resolve().parents[1]
ROOT = CI.parents[1]


class RequiredChecksContractTests(unittest.TestCase):
    def run_gate(self, results):
        return controls.RequiredGateTests.run_step('Require successful checks',
            {'CHECK_RESULTS': json.dumps(results)}, capture=True)

    def test_missing_secondary_metadata_fails_even_when_unit_job_is_skipped(self):
        for full in (True, False):
            for name in ('ci_tests', 'gitea_tests', 'script_tests', 'terraform_matrix'):
                with self.subTest(full=full, name=name):
                    results = controls.RequiredGateTests.successful_results()
                    results['changes']['outputs'] = controls.detect.classify_changes([], full=full)
                    if not full:
                        results['unit-tests']['result'] = 'skipped'
                    del results['changes']['outputs'][name]
                    self.assertNotEqual(self.run_gate(results).returncode, 0)

    def test_aggregate_rejects_malformed_and_inconsistent_selection(self):
        for update in ({'ci_tests': ''}, {'gitea_tests': True}, {'script_tests': '['},
                       {'script_tests': '["test_*.py", ""]'}, {'unit_tests': 'false'},
                       {'ci_tests': 'false', 'gitea_tests': 'false', 'script_tests': '[]'},
                       {'terraform_matrix': '{"stack":[]}'},
                       {'terraform_matrix': '{"stack":["oci-free-tier","oci-free-tier"]}'},
                       {'terraform_matrix': '{"stack":["unknown"]}'}, {'terraform': 'false'}):
            with self.subTest(update=update):
                results = controls.RequiredGateTests.successful_results()
                results['changes']['outputs'].update(update)
                self.assertNotEqual(self.run_gate(results).returncode, 0)

    def test_each_missing_required_job_is_rejected(self):
        for name in controls.RequiredGateTests.successful_results():
            with self.subTest(name=name):
                results = controls.RequiredGateTests.successful_results()
                del results[name]
                result = self.run_gate(results)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f'Required check did not succeed: {name} (missing)'.encode(), result.stdout)

    def test_missing_or_invalid_release_selection_is_rejected(self):
        for value in ({}, {'changed': ''}, {'changed': True}, None, []):
            results = controls.RequiredGateTests.successful_results()
            results['kyverno-release-change']['outputs'] = value
            self.assertNotEqual(self.run_gate(results).returncode, 0)

    def test_missing_execution_input_and_malformed_job_results_are_rejected(self):
        env = {key: value for key, value in os.environ.items() if key != 'CHECK_RESULTS'}
        for value in (None, '', '{', '[]', 'null', '{"changes":null}', '{"changes":{}}'):
            with self.subTest(value=value):
                if value is not None:
                    env['CHECK_RESULTS'] = value
                result = subprocess.run([sys.executable, str(CI / 'require_checks.py')],
                                        env=env, capture_output=True)
                self.assertNotEqual(result.returncode, 0)

    def test_missing_checkout_or_helper_fails_closed(self):
        for missing in ('checkout', 'require_checks.py', 'run_selected_tests.py', 'detect_changes.py'):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                if missing != 'checkout':
                    (root / 'scripts/ci').mkdir(parents=True)
                    for name in ('require_checks.py', 'run_selected_tests.py', 'detect_changes.py'):
                        if name != missing:
                            shutil.copyfile(CI / name, root / 'scripts/ci' / name)
                result = controls.RequiredGateTests.run_step('Require successful checks',
                    {'CHECK_RESULTS': json.dumps(controls.RequiredGateTests.successful_results())},
                    cwd=root, capture=True)
                self.assertNotEqual(result.returncode, 0)

    def test_new_helper_and_test_paths_force_full_selection(self):
        expected = controls.detect.classify_changes([], full=True)
        for path in ('scripts/ci/run_selected_tests.py', 'scripts/ci/require_checks.py',
                     'scripts/ci/tests/test_run_selected_tests.py', 'scripts/ci/tests/test_require_checks.py'):
            with self.subTest(path=path):
                self.assertEqual(controls.detect.classify_changes([path]), expected)

    def test_workflow_contract_and_original_failure_diagnostics(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
        jobs = workflow['jobs']
        self.assertEqual(len(jobs), 15)
        self.assertTrue({'repo-checks', 'actionlint', 'gitleaks', 'validate', 'yamllint'} <= jobs.keys())
        self.assertNotIn('if', jobs['gitleaks'])
        self.assertNotIn('if', jobs['kyverno-release-change'])
        self.assertEqual(jobs['repo-checks']['if'], 'always()')
        checkout, aggregate = jobs['repo-checks']['steps']
        self.assertEqual(checkout['uses'], 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1')
        self.assertIs(checkout['with']['persist-credentials'], False)
        self.assertEqual(aggregate['if'], 'always()')
        unit = jobs['unit-tests']
        self.assertEqual(unit['env']['CHECK_SELECTION'], '${{ toJSON(needs.changes.outputs) }}')
        steps = {step.get('id'): step for step in unit['steps'] if 'id' in step}
        self.assertLess(unit['steps'].index(steps['selection']), unit['steps'].index(steps['validators']))
        for name in ('guards', 'gitea'):
            self.assertIn("steps.selection.outcome == 'success'", steps[name]['if'])
        for failed, message in (('dependencies', 'Test dependency setup failed'),
                                ('selection', 'Invalid check selection'),
                                ('validators', 'Actual validator unit-test failure'),
                                ('guards', 'Actual application or maintenance unit-test failure'),
                                ('gitea', 'Actual Gitea protection unit-test failure')):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as temporary:
                summary = Path(temporary) / 'summary'
                result = controls.RequiredGateTests.run_step('Summarize the original unit-test failure',
                    {'STEPS': json.dumps({failed: {'outcome': 'failure'}}),
                     'GITHUB_STEP_SUMMARY': str(summary)}, capture=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(message, summary.read_text())

    def test_pyyaml_is_installed_once_per_relevant_job_before_trust(self):
        for path, name in (('ci.yml', 'manifest-validation'), ('kubescape-checks.yml', 'qualify-release')):
            workflow = yaml.safe_load((ROOT / '.github/workflows' / path).read_text())
            commands = '\n'.join(step.get('run', '') for step in workflow['jobs'][name]['steps'])
            installs = [line for line in commands.splitlines() if 'pip install' in line and 'pyyaml' in line.lower()]
            self.assertEqual(len(installs), 1)
            self.assertIn('PyYAML==6.0.3', installs[0])
            self.assertLess(commands.index(installs[0]), commands.index('TRUST_SCOPE=cli'))
            if path == 'kubescape-checks.yml':
                self.assertIn('zstandard==0.25.0', installs[0])


if __name__ == '__main__':
    unittest.main()
