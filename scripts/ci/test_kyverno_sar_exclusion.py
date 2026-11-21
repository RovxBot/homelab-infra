#!/usr/bin/env python3
"""Offline SAR matching/security/reconciliation regression against pinned sources.

Requires PyYAML, Go >=1.26.9, Helm 3, the Kyverno v1.19.2 CLI, an unmodified
v1.19.2 checkout and the unpacked kyverno-3.9.2 chart (including dependencies).
Dependencies may download; no Kubernetes client, kubeconfig or live API is used.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
TESTS = Path(__file__).with_name('kyverno-sar-tests')
KYVERNO_COMMIT = 'e63308f1e16c05cd009ac4864be6ec25c66b49be'
CONDITION = 'exclude-filtered-access-review-creates'


def run(command, **kwargs):
    return subprocess.run([str(v) for v in command], check=True, text=True, **kwargs)


def test_guardrail_and_psa(kyverno, work):
    """Evaluate existing policies, including their current Audit/Enforce actions."""
    resource = {'apiVersion': 'apps/v1', 'kind': 'Deployment',
                'metadata': {'name': 'synthetic', 'namespace': 'tenant',
                             'labels': {'kustomize.toolkit.fluxcd.io/name': 'synthetic'}},
                'spec': {'template': {'spec': {'containers': [{'name': 'app', 'image': 'example.invalid/app:1'}]}}}}
    for label, username, expected in [
        ('manual-edit', 'synthetic-user', 'fail'),
        ('helm-reconcile', 'system:serviceaccount:flux-system:helm-controller', 'pass'),
        ('kustomize-reconcile', 'system:serviceaccount:flux-system:kustomize-controller', 'pass'),
    ]:
        path = work / label
        path.mkdir()
        (path / 'policy.yaml').write_text((ROOT / 'infra/kyverno/policies/platform-flux-guardrails.yaml').read_text())
        (path / 'resources.yaml').write_text(yaml.safe_dump(resource))
        (path / 'userinfo.yaml').write_text(yaml.safe_dump({'apiVersion': 'cli.kyverno.io/v1alpha1',
            'kind': 'UserInfo', 'userInfo': {'username': username}}))
        (path / 'values.yaml').write_text(yaml.safe_dump({'apiVersion': 'cli.kyverno.io/v1alpha1',
            'kind': 'Value', 'metadata': {'name': 'synthetic'}, 'globalValues': {'request.operation': 'UPDATE'}}))
        test = {'apiVersion': 'cli.kyverno.io/v1alpha1', 'kind': 'Test', 'metadata': {'name': label},
                'policies': ['policy.yaml'], 'resources': ['resources.yaml'], 'userinfo': 'userinfo.yaml',
                'variables': 'values.yaml', 'results': [{'policy': 'platform-flux-guardrails',
                'rule': 'audit-manual-edits-to-flux-managed-resources', 'kind': 'Deployment',
                'resources': ['synthetic'], 'result': expected}]}
        (path / 'kyverno-test.yaml').write_text(yaml.safe_dump(test))
        run([kyverno, 'test', path, '--require-tests', '--remove-color'])
    path = work / 'psa'
    path.mkdir()
    (path / 'policy.yaml').write_text((ROOT / 'infra/kyverno/policies/platform-namespace-standards.yaml').read_text())
    valid_labels = {'homelab.cooked.beer/owner': 'synthetic'}
    valid_labels.update({f'pod-security.kubernetes.io/{mode}{suffix}': value
                         for mode in ('enforce', 'audit', 'warn')
                         for suffix, value in (('', 'restricted'), ('-version', 'v1.34'))})
    (path / 'resources.yaml').write_text(yaml.safe_dump_all([
        {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': name, 'labels': labels}}
        for name, labels in [('valid', valid_labels), ('missing-psa', {'homelab.cooked.beer/owner': 'synthetic'})]]))
    (path / 'kyverno-test.yaml').write_text(yaml.safe_dump({
        'apiVersion': 'cli.kyverno.io/v1alpha1', 'kind': 'Test', 'metadata': {'name': 'psa'},
        'policies': ['policy.yaml'], 'resources': ['resources.yaml'],
        'results': [{'policy': 'platform-namespace-standards', 'rule': 'require-psa-and-owner-labels',
                     'kind': 'Namespace', 'resources': [name], 'result': expected}
                    for name, expected in [('valid', 'pass'), ('missing-psa', 'fail')]]}))
    run([kyverno, 'test', path, '--require-tests', '--remove-color'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kyverno-source', type=Path, required=True)
    parser.add_argument('--chart', type=Path, required=True)
    parser.add_argument('--helm', default='helm')
    parser.add_argument('--go', default='go')
    parser.add_argument('--kyverno', required=True, help='Deployed-version v1.19.2 CLI for guardrail/PSA tests')
    args = parser.parse_args()
    source = args.kyverno_source.resolve()
    chart = args.chart.resolve()
    cli_version = run([args.kyverno, 'version'], capture_output=True).stdout
    if 'Version: 1.19.2\n' not in cli_version or KYVERNO_COMMIT not in cli_version:
        raise SystemExit('Requires the exact deployed Kyverno v1.19.2 CLI')
    revision = run(['git', '-C', source, 'rev-parse', 'HEAD'], capture_output=True).stdout.strip()
    if revision != KYVERNO_COMMIT or run(['git', '-C', source, 'status', '--porcelain'], capture_output=True).stdout:
        raise SystemExit('Requires the clean, exact deployed Kyverno v1.19.2 source')
    metadata = yaml.safe_load((chart / 'Chart.yaml').read_text())
    if (metadata['version'], metadata['appVersion']) != ('3.9.2', 'v1.19.2'):
        raise SystemExit('Requires the deployed chart 3.9.2 / app v1.19.2')
    # Use the release chart's dependencies, while checking that the templates
    # under test are byte-identical to the pinned application source.
    for name in ('configmap.yaml', '_helpers.tpl'):
        relative = Path('templates/config') / name
        if (chart / relative).read_bytes() != (source / 'charts/kyverno' / relative).read_bytes():
            raise SystemExit(f'Chart/source template mismatch: {relative}')
    values = yaml.safe_load((ROOT / 'infra/kyverno/helmrelease.yaml').read_text())['spec']['values']
    baseline = copy.deepcopy(values)
    conditions = baseline['config']['matchConditions']
    if sum(c['name'] == CONDITION for c in conditions) != 1:
        raise SystemExit('Expected exactly one additional SAR condition')
    baseline['config']['matchConditions'] = [c for c in conditions if c['name'] != CONDITION]
    with tempfile.TemporaryDirectory(prefix='kyverno-sar-') as directory:
        work = Path(directory)
        for label, config in [('baseline', baseline), ('candidate', values)]:
            path = work / f'{label}-values.yaml'
            path.write_text(yaml.safe_dump(config))
            rendered = run([args.helm, 'template', 'kyverno', chart, '--namespace', 'kyverno',
                            '--kube-version', '1.34.1', '--values', path,
                            '--show-only', 'templates/config/configmap.yaml'], capture_output=True)
            cm = yaml.safe_load(rendered.stdout)
            (work / f'{label}-config.json').write_text(json.dumps(cm))
        before = json.loads((work / 'baseline-config.json').read_text())
        after = json.loads((work / 'candidate-config.json').read_text())
        if json.loads(after['data']['matchConditions']) != values['config']['matchConditions']:
            raise SystemExit('Helm discarded or altered configured conditions')
        before['data'].pop('matchConditions')
        after['data'].pop('matchConditions')
        if before != after:
            raise SystemExit('Unexpected ConfigMap change outside matchConditions')
        (work / 'guardrail.json').write_text(json.dumps(yaml.safe_load(
            (ROOT / 'infra/kyverno/policies/platform-flux-guardrails.yaml').read_text())))
        replacements = {
            str(source / 'pkg' / package / 'zz_homelab_sar_test.go'): str(TESTS / 'testdata' / fixture)
            for package, fixture in [('controllers/webhook', 'controller_test.go'),
                                     ('webhooks/handlers', 'handlers_test.go')]
        }
        overlay = work / 'overlay.json'
        overlay.write_text(json.dumps({'Replace': replacements}))
        env = dict(os.environ, SAR_FIXTURES=str(work), GOMAXPROCS='6')
        print('Testing real Kyverno configuration, filters and controller reconciliation', flush=True)
        run([args.go, 'test', '-p', '4', '-count=1', '-overlay', overlay,
             './pkg/config', './pkg/webhooks/handlers', './pkg/controllers/webhook'], cwd=source, env=env)
        print('Testing Kubernetes v1.34.1 CEL matching and native SAR validation', flush=True)
        run([args.go, 'test', '-p', '4', '-mod=readonly', '-count=1', '-v', '.'], cwd=TESTS, env=env)
        test_guardrail_and_psa(args.kyverno, work)
        print('Verified: chart propagation, fail-closed matching, pre-filter guard equivalence, '
              'reconciliation drift repair/restart/rollback and unchanged policy webhook')


if __name__ == '__main__':
    main()
