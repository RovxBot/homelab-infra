#!/usr/bin/env python3
"""Require successful selected jobs and complete Repo Checks selection metadata."""

from __future__ import annotations

import json
import os
import sys

from run_selected_tests import selection_errors


SELECTED_JOBS = {
    'unit_tests': ('unit-tests',),
    'manifests': ('manifest-validation',),
    'public_edge': ('public-edge',),
    'shell': ('shell-syntax',),
    'workflows': ('actionlint', 'yamllint', 'zizmor'),
    'renovate': ('renovate-config',),
    'terraform': ('validate',),
}


def check_errors(results: object) -> list[str]:
    if not isinstance(results, dict) or any(not isinstance(job, dict) for job in results.values()):
        return ['Invalid CHECK_RESULTS: expected a job-result object']
    errors = []
    for name, job in results.items():
        if job.get('result') not in ('success', 'skipped'):
            errors.append(f"Check {name}: {job.get('result', 'missing')} (see the failing step and its original log)")
    required = {'changes', 'gitleaks', 'kyverno-release-change'}
    release = results.get('kyverno-release-change', {}).get('outputs', {})
    release_changed = release.get('changed') if isinstance(release, dict) else None
    if release_changed not in ('true', 'false'):
        errors.append('Missing Kyverno release selection; dependent compatibility check cannot be selected')
    if release_changed == 'true':
        required.add('kyverno-compatibility')
    outputs = results.get('changes', {}).get('outputs', {})
    errors.extend(selection_errors(outputs))
    if isinstance(outputs, dict):
        for selector, jobs in SELECTED_JOBS.items():
            if outputs.get(selector) == 'true':
                required.update(jobs)
    for name in sorted(required):
        result = results.get(name, {}).get('result', 'missing')
        if result != 'success':
            errors.append(f'Required check did not succeed: {name} ({result})')
    return errors


def main() -> int:
    try:
        errors = check_errors(json.loads(os.environ['CHECK_RESULTS']))
    except (KeyError, ValueError, TypeError) as error:
        errors = [f'Missing or invalid CHECK_RESULTS: {error}']
    summary = ['## Required check results', *['- ' + error for error in errors]]
    summary.append('Failure types are reported by the original named step: unit test, build qualification, compatibility, or external fetch/registry. A job status alone does not establish the cause.')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as handle:
            handle.write('\n'.join(summary) + '\n')
    if errors:
        print('\n'.join(errors))
        return 1
    print('All selected checks passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
