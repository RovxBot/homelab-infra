#!/usr/bin/env python3
"""Exercise resource budgets with the real Kyverno CLI and production exceptions."""
import argparse
import copy
import re
from pathlib import Path
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / 'infra/kyverno/policies/platform-pod-standards.yaml'


def run(kyverno, policy=POLICY):
    version = subprocess.check_output([kyverno, 'version'], text=True)
    match = re.search(r'^Version: v?(\d+)\.(\d+)\.(\d+)$', version, re.MULTILINE)
    assert match, 'Unknown executed Kyverno version'
    cel = tuple(map(int,match.groups())) >= (1,20,0)
    if cel:
        docs=list(yaml.safe_load_all((ROOT/'migration/kyverno/policies/workloads.yaml').read_text()))
        chosen=next(d for d in docs if d['metadata']['name']=='platform-pod-standards-require-requests-and-limits')
        exception_docs=[d for d in yaml.safe_load_all((ROOT/'migration/kyverno/exceptions/exceptions.yaml').read_text()) if d['metadata']['name'] in ['chart-managed-workloads','home-assistant']]
    else:
        chosen=yaml.safe_load(Path(policy).read_text())
        exception_docs=[yaml.safe_load((ROOT/f'infra/kyverno/exceptions/{name}.yaml').read_text()) for name in ['chart-managed-workloads','home-assistant']]
    resources, results = [], []
    for kind in ('Deployment', 'StatefulSet', 'DaemonSet', 'Job', 'CronJob'):
        for where in ('containers', 'initContainers'):
            cases = [('valid-milli', {'cpu': '100m', 'memory': '128Mi'}),
                     ('valid-decimal', {'cpu': '0.5', 'memory': '1G'}),
                     ('valid-integral', {'cpu': 1, 'memory': '128Mi'}),
                     ('valid-zero', {'cpu': '0', 'memory': '0'})]
            for name in ('resources', 'requests', 'limits', 'requests-cpu', 'requests-memory', 'limits-cpu', 'limits-memory', 'empty-cpu'):
                cases.append((name, None))
            for case, quantities in cases:
                name = f'{kind.lower()}-{where.lower()}-{case}'
                container = {'name': 'app', 'image': 'example.invalid/app@sha256:' + 'a' * 64,
                             'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'},
                                           'limits': {'cpu': '1', 'memory': '256Mi'}},
                             'securityContext': {'runAsNonRoot': True, 'allowPrivilegeEscalation': False,
                                                 'capabilities': {'drop': ['ALL']}, 'seccompProfile': {'type': 'RuntimeDefault'}}}
                target = copy.deepcopy(container)
                if quantities:
                    target['resources'] = {'requests': quantities, 'limits': quantities}
                elif case == 'resources':
                    del target['resources']
                elif case in ('requests', 'limits'):
                    del target['resources'][case]
                elif case == 'empty-cpu':
                    target['resources']['requests']['cpu'] = ''
                else:
                    budget, field = case.split('-')
                    del target['resources'][budget][field]
                pod = {'metadata': {'labels': {'homelab.cooked.beer/owner': 'test'}},
                       'spec': {'containers': [target if where == 'containers' else container]}}
                if where == 'initContainers':
                    pod['spec']['initContainers'] = [target]
                spec = {'jobTemplate': {'spec': {'template': pod}}} if kind == 'CronJob' else {'template': pod}
                resources.append({'apiVersion': 'batch/v1' if kind in ('Job', 'CronJob') else 'apps/v1',
                                  'kind': kind, 'metadata': {'name': name, 'namespace': 'test'}, 'spec': spec})
                results.append({'policy': 'platform-pod-standards', 'rule': 'require-requests-and-limits',
                                'kind': kind, 'resources': [name],
                                'result': 'pass' if quantities else 'fail'})
    # The existing Longhorn resource exception remains scoped to its namespace.
    # Home Assistant's hardening exception must never waive resource budgets.
    for namespace, name, expected in (('longhorn-system', 'chart-test', 'skip'),
                                      ('test', 'chart-test-outside', 'fail'),
                                      ('home-assistant', 'home-assistant', 'fail')):
        resource = copy.deepcopy(resources[4])  # Deployment with resources absent.
        resource['metadata'] = {'name': name, 'namespace': namespace}
        resources.append(resource)
        results.append({'policy': 'platform-pod-standards', 'rule': 'require-requests-and-limits',
                        'kind': 'Deployment', 'resources': [name], 'result': expected})
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        (path / 'resources.yaml').write_text(yaml.safe_dump_all(resources))
        (path / 'policy.yaml').write_text(yaml.safe_dump(chosen))
        if cel:
            for row in results:
                row.update(policy=chosen['metadata']['name'], rule='validation')
        (path / 'exceptions.yaml').write_text(yaml.safe_dump_all(exception_docs))
        (path / 'kyverno-test.yaml').write_text(yaml.safe_dump({
            'apiVersion': 'cli.kyverno.io/v1alpha1', 'kind': 'Test', 'metadata': {'name': 'resource-budgets'},
            'policies': ['policy.yaml'], 'resources': ['resources.yaml'],
            'exceptions': ['exceptions.yaml'], 'results': results}))
        result = subprocess.run([kyverno, 'test', directory, '--require-tests', '--remove-color', '--v=2'],
                                capture_output=True, text=True, timeout=120)
        print(result.stdout)
        print(result.stderr)
        if result.returncode or "Failed to parse value type" in result.stderr + result.stdout:
            raise SystemExit('Resource budget regression tests failed')
        print(f'{len(results)} resource budget cases passed without quantity comparison errors')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kyverno', required=True)
    parser.add_argument('--policy', type=Path, default=POLICY)
    args = parser.parse_args()
    run(args.kyverno, args.policy)
