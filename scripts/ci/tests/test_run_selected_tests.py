from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_detect_changes as controls


CI = Path(__file__).resolve().parents[1]
RUNNER = CI / 'run_selected_tests.py'


class SelectedTestRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for directory in ('scripts/ci/tests', 'scripts/tests', 'ops/gitea/tests'):
            (self.root / directory).mkdir(parents=True)
        for name in ('detect_changes.py', 'run_selected_tests.py', 'require_checks.py'):
            shutil.copyfile(CI / name, self.root / 'scripts/ci' / name)
        self.outputs = controls.detect.classify_changes([], full=True)

    def run_runner(self, outputs, *args):
        env = {key: value for key, value in os.environ.items() if key != 'CHECK_SELECTION'}
        if outputs is not None:
            env['CHECK_SELECTION'] = outputs if isinstance(outputs, str) else json.dumps(outputs)
        return subprocess.run([sys.executable, str(RUNNER), *args], cwd=self.root,
                              env=env, capture_output=True, text=True)

    def validate(self, **updates):
        return self.run_runner({**self.outputs, **updates}, '--validate-only')

    def write_test(self, directory='scripts/tests', *, fail=False):
        (self.root / directory / 'test_probe.py').write_text(
            'from pathlib import Path\nimport unittest\n'
            'class Probe(unittest.TestCase):\n'
            '    def test_probe(self):\n'
            '        with Path("executed").open("a") as handle:\n'
            '            handle.write("ran\\n")\n'
            + ('        self.fail("fixture failure")\n' if fail else '')
        )

    def test_complete_selection_and_valid_empty_selection(self):
        self.assertEqual(self.validate().returncode, 0)
        outputs = controls.detect.classify_changes(['docs/unrelated.md'])
        self.assertEqual(self.run_runner(outputs, '--validate-only').returncode, 0)

    def test_every_boolean_must_be_present_and_exact(self):
        booleans = [name for name, value in self.outputs.items() if value == 'true']
        self.assertEqual(len(booleans), 9)
        for name in booleans:
            outputs = dict(self.outputs)
            del outputs[name]
            self.assertNotEqual(self.run_runner(outputs, '--validate-only').returncode, 0, name)
            for value in ('', 'True', '1', True, False, None, [], {}):
                with self.subTest(name=name, value=value):
                    self.assertNotEqual(self.validate(**{name: value}).returncode, 0)

    def test_json_metadata_must_be_present_and_parseable(self):
        for name in ('script_tests', 'terraform_matrix'):
            outputs = dict(self.outputs)
            del outputs[name]
            self.assertNotEqual(self.run_runner(outputs, '--validate-only').returncode, 0)
            for value in ('', '[', 'null', 'true', '"value"', None, [], {}):
                with self.subTest(name=name, value=value):
                    self.assertNotEqual(self.validate(**{name: value}).returncode, 0)

    def test_empty_partial_and_unsafe_patterns_are_rejected(self):
        for patterns in ([''], ['test_*.py', ''], [None], [1], [[]], [{}],
                         ['../test_escape.py'], ['sub/test_probe.py'], ['test_*.txt'],
                         [' test_probe.py'], ['test_probe.py\n'], ['*'], ['test_$(id).py']):
            with self.subTest(patterns=patterns):
                self.assertNotEqual(self.validate(script_tests=json.dumps(patterns)).returncode, 0)
        self.assertFalse((self.root / 'executed').exists())

    def test_unit_flag_must_match_any_selected_suite(self):
        for ci, gitea, patterns, unit in (
            ('false', 'false', [], 'true'),
            ('true', 'false', [], 'false'),
            ('false', 'true', [], 'false'),
            ('false', 'false', ['test_probe.py'], 'false'),
        ):
            with self.subTest(ci=ci, gitea=gitea, patterns=patterns, unit=unit):
                self.assertNotEqual(self.validate(ci_tests=ci, gitea_tests=gitea,
                    script_tests=json.dumps(patterns), unit_tests=unit).returncode, 0)

    def test_invalid_terraform_matrix_shapes_stacks_and_duplicates(self):
        for matrix in ([], {}, {'include': []}, {'stack': [], 'extra': []},
                       {'stack': 'oci-free-tier'}, {'stack': None},
                       {'stack': ['unknown']}, {'stack': ['../oci-free-tier']},
                       {'stack': [True]}, {'stack': [{}]}, {'stack': [[]]},
                       {'stack': ['oci-free-tier', 'oci-free-tier']}):
            with self.subTest(matrix=matrix):
                self.assertNotEqual(self.validate(terraform_matrix=json.dumps(matrix)).returncode, 0)

    def test_terraform_flag_must_match_matrix_contents(self):
        self.assertNotEqual(self.validate(terraform_matrix='{"stack":[]}').returncode, 0)
        self.assertNotEqual(self.validate(terraform='false').returncode, 0)
        self.assertEqual(self.validate(terraform_matrix='{"stack":["oci-free-tier"]}').returncode, 0)

    def test_each_selected_suite_executes_real_tests(self):
        for suite, directory in (('ci', 'scripts/ci/tests'), ('scripts', 'scripts/tests'),
                                 ('gitea', 'ops/gitea/tests')):
            with self.subTest(suite=suite):
                self.write_test(directory)
                result = self.run_runner(self.outputs, '--suite', suite)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Ran 1 test', result.stderr)
        self.assertEqual((self.root / 'executed').read_text(), 'ran\n' * 3)

    def test_each_selected_suite_rejects_zero_discovery(self):
        for suite in ('ci', 'scripts', 'gitea'):
            with self.subTest(suite=suite):
                result = self.run_runner(self.outputs, '--suite', suite)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Selected tests were not found', result.stderr)

    def test_partially_empty_discovery_fails_before_running_any_tests(self):
        self.write_test()
        outputs = {**self.outputs, 'script_tests': '["test_probe.py","test_missing.py"]'}
        result = self.run_runner(outputs, '--suite', 'scripts')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('test_missing.py', result.stderr)
        self.assertFalse((self.root / 'executed').exists())

    def test_import_errors_are_discovery_failures(self):
        self.write_test()
        (self.root / 'scripts/tests/test_broken.py').write_text('raise ImportError("fixture import failure")\n')
        result = self.run_runner(self.outputs, '--suite', 'scripts')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('fixture import failure', result.stderr)
        self.assertFalse((self.root / 'executed').exists())

    def test_repository_imports_match_the_former_inline_runner(self):
        self.write_test()
        (self.root / 'scripts/ci/fixture_support.py').write_text('VALUE = 1\n')
        test = self.root / 'scripts/tests/test_probe.py'
        test.write_text('from scripts.ci.fixture_support import VALUE\n' + test.read_text())
        result = self.run_runner(self.outputs, '--suite', 'scripts')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Ran 1 test', result.stderr)

    def test_overlapping_and_duplicate_patterns_execute_each_id_once(self):
        self.write_test()
        outputs = {**self.outputs, 'script_tests': '["test_*.py","test_probe.py","test_*.py"]'}
        result = self.run_runner(outputs, '--suite', 'scripts')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Ran 1 test', result.stderr)
        self.assertEqual((self.root / 'executed').read_text(), 'ran\n')

    def test_test_failures_propagate(self):
        self.write_test(fail=True)
        result = self.run_runner(self.outputs, '--suite', 'scripts')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('fixture failure', result.stderr)

    def test_unselected_suite_execution_is_rejected(self):
        self.write_test()
        outputs = controls.detect.classify_changes(['docs/unrelated.md'])
        for suite in ('ci', 'scripts', 'gitea'):
            self.assertNotEqual(self.run_runner(outputs, '--suite', suite).returncode, 0)
        self.assertFalse((self.root / 'executed').exists())

    def test_missing_metadata_or_execution_mode_fails_closed(self):
        for outputs in (None, '', '{', '{}', '[]', 'null'):
            with self.subTest(outputs=outputs):
                self.assertNotEqual(self.run_runner(outputs, '--suite', 'scripts').returncode, 0)
        self.assertNotEqual(self.run_runner(self.outputs).returncode, 0)
        self.assertNotEqual(self.run_runner(self.outputs, '--suite', 'unknown').returncode, 0)

    def test_named_workflow_steps_use_the_actual_runner(self):
        for directory in ('scripts/ci/tests', 'scripts/tests', 'ops/gitea/tests'):
            self.write_test(directory)
        env = {'CHECK_SELECTION': json.dumps(self.outputs)}
        for name in ('Validate complete check selection', 'Test check selection and validators',
                     'Test selected application and maintenance guards',
                     'Test Gitea destructive-update protection'):
            with self.subTest(name=name):
                result = controls.RequiredGateTests.run_step(name, env, cwd=self.root, capture=True)
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'executed').read_text(), 'ran\n' * 3)


if __name__ == '__main__':
    unittest.main()
