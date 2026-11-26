#!/usr/bin/env python3
"""Validate Repo Checks selection and run one named unittest suite."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unittest

from detect_changes import TERRAFORM_STACKS


BOOLEAN_OUTPUTS = (
    'unit_tests', 'ci_tests', 'gitea_tests', 'manifests', 'public_edge',
    'shell', 'workflows', 'renovate', 'terraform',
)
SUITES = {
    'ci': ('ci_tests', 'scripts/ci/tests'),
    'scripts': ('script_tests', 'scripts/tests'),
    'gitea': ('gitea_tests', 'ops/gitea/tests'),
}


def selection_errors(outputs: object) -> list[str]:
    """Check the entire output contract, also used when the unit job is skipped."""
    if not isinstance(outputs, dict):
        return ['Missing or invalid check selection: expected an output object']
    errors = []
    for name in BOOLEAN_OUTPUTS:
        if outputs.get(name) not in ('true', 'false'):
            errors.append('Missing or invalid check selection: ' + name + '; dependent checks could not be selected')

    try:
        patterns = json.loads(outputs.get('script_tests', ''))
        if not isinstance(patterns, list) or any(
            not isinstance(pattern, str)
            or not re.fullmatch(r'test_[A-Za-z0-9_*?\[\]!-]+\.py', pattern)
            for pattern in patterns
        ):
            raise ValueError
    except (TypeError, ValueError):
        errors.append('Invalid script_tests: expected a JSON list of nonempty test_*.py basename patterns')
    else:
        selected = outputs.get('ci_tests') == 'true' or outputs.get('gitea_tests') == 'true' or bool(patterns)
        if outputs.get('unit_tests') != str(selected).lower():
            errors.append('Inconsistent unit_tests selection: must match ci_tests, gitea_tests or script_tests')

    try:
        matrix = json.loads(outputs.get('terraform_matrix', ''))
        if not isinstance(matrix, dict) or set(matrix) != {'stack'}:
            raise ValueError
        stacks = matrix['stack']
        if not isinstance(stacks, list) or any(
            not isinstance(stack, str) or stack not in TERRAFORM_STACKS for stack in stacks
        ) or len(stacks) != len(set(stacks)):
            raise ValueError
    except (TypeError, ValueError):
        errors.append('Invalid terraform_matrix: expected unique recognised stack names in {"stack": [...]}')
    else:
        if outputs.get('terraform') != str(bool(stacks)).lower():
            errors.append('Inconsistent terraform selection: must match terraform_matrix contents')
    return errors


def read_selection() -> dict:
    outputs = json.loads(os.environ['CHECK_SELECTION'])
    errors = selection_errors(outputs)
    if errors:
        raise ValueError('\n'.join(errors))
    return outputs


def test_cases(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_cases(test)
        else:
            yield test


def discover_suite(directory: str, patterns: list[str]) -> unittest.TestSuite:
    """Discover every pattern before execution; overlapping IDs run only once."""
    # Match python -m unittest and the former inline runner's import root.
    sys.path.insert(0, os.getcwd())
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    names = set()
    for pattern in patterns:
        discovered = list(test_cases(loader.discover(directory, pattern=pattern)))
        if loader.errors:
            raise ValueError('\n'.join(loader.errors))
        if not discovered:
            raise ValueError(f'Selected tests were not found: {directory}/{pattern}')
        for test in discovered:
            if test.id() not in names:
                names.add(test.id())
                suite.addTest(test)
    return suite


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--validate-only', action='store_true')
    mode.add_argument('--suite', choices=SUITES)
    args = parser.parse_args()
    try:
        outputs = read_selection()
        if args.validate_only:
            print('Complete check selection is valid.')
            return 0
        selector, directory = SUITES[args.suite]
        patterns = json.loads(outputs['script_tests']) if args.suite == 'scripts' else ['test*.py']
        if not patterns or (args.suite != 'scripts' and outputs[selector] != 'true'):
            raise ValueError('Suite was not selected: ' + args.suite)
        suite = discover_suite(directory, patterns)
    except (KeyError, ValueError, TypeError, ImportError) as error:
        print(f'Invalid test selection or discovery: {error}', file=sys.stderr)
        return 1
    return int(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())


if __name__ == '__main__':
    sys.exit(main())
