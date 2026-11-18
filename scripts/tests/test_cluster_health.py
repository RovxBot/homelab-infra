"""Run the deployed shell health functions against bounded kubectl fixtures."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]


def generator_source():
    text = (ROOT / 'infra/pod-report/deployment.yaml').read_text()
    source = text.split('            - |\n', 1)[1].split('          volumeMounts:', 1)[0]
    return '\n'.join(line[14:] for line in source.splitlines())


class ClusterHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = {
            'nodes': 'node1\tTrue\tFalse\tFalse\tFalse',
            'deployments': 'koito/bridge\t1\t1\ngitea/gitea-mirror\t0\t',
            'statefulsets': 'gitea/postgres\t1\t1',
            'daemonsets': 'kube-system/cilium\t8\t8',
            'persistentvolumeclaims': 'gitea/data\tBound',
            'kustomizations.kustomize.toolkit.fluxcd.io,helmreleases.helm.toolkit.fluxcd.io': 'flux-system/Kustomization/kyverno-policies\tTrue',
            'volumes.longhorn.io': 'attached-vol\tattached\thealthy\nretired-vol\tdetached\tunknown',
            'nodes.longhorn.io': 'node1\tTrue', 'jobs': '',
        }
        command = self.root / 'kubectl'
        command.write_text('''#!/usr/bin/env python3
import json, os, sys
args=sys.argv[1:]
value=json.load(open(os.environ['HEALTH_FIXTURE']))[args[args.index('get')+1]]
if value == 'API_ERROR': sys.exit(1)
print(value)
''')
        command.chmod(0o755)
        self.script = generator_source().split('while true; do', 1)[0].replace('/work/', str(self.root) + '/')

    def health(self):
        fixture = self.root / 'data.json'
        fixture.write_text(json.dumps(self.data))
        result = subprocess.run(['sh', '-c', self.script + '\nnow="2026-11-19T00:00:00Z"\nevaluate_health\n'],
                                env=dict(os.environ, PATH=str(self.root)+':'+os.environ['PATH'], HEALTH_FIXTURE=str(fixture)),
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads((self.root / 'health.json').read_text())

    def test_healthy_detached_volumes_and_retired_deployment(self):
        self.assertEqual(self.health()['status'], 'healthy')

    def test_all_failing_workload_types_and_flux_are_reported(self):
        self.data['deployments'] = 'koito/bridge\t1\t'
        self.data['statefulsets'] = 'gitea/postgres\t1\t0'
        self.data['daemonsets'] = 'kube-system/cilium\t8\t7'
        self.data['kustomizations.kustomize.toolkit.fluxcd.io,helmreleases.helm.toolkit.fluxcd.io'] = 'flux-system/Kustomization/kyverno-policies\tFalse'
        health = self.health()
        self.assertEqual(health['status'], 'unhealthy')
        self.assertEqual(health['checks']['workloads'], 'failed')
        self.assertEqual(len(health['failures']['workloads']), 3)
        self.assertIn('kyverno-policies', health['failures']['flux'][0])

    def test_degraded_attached_or_detached_volume_remains_failed(self):
        for state in ('attached', 'detached'):
            self.data['volumes.longhorn.io'] = f'vol\t{state}\tdegraded'
            self.assertEqual(self.health()['checks']['longhorn'], 'failed')

    def test_recent_failed_job_is_reported_but_completed_retry_is_healthy(self):
        now = datetime.now(timezone.utc).isoformat()
        for completed, expected in (('', 'failed'), ('True', 'ok')):
            self.data['jobs'] = f'gitea/backup|1|{now}|{completed}'
            health = self.health()
            self.assertEqual(health['checks']['recentJobs'], expected)
            if expected == 'failed':
                self.assertIn('gitea/backup', health['failures']['recentJobs'][0])

    def test_old_failed_and_active_without_failed_attempts_are_healthy(self):
        self.data['jobs'] = 'test/old|1|2000-01-01T00:00:00Z|\ntest/running||2026-11-19T00:00:00Z|'
        self.assertEqual(self.health()['checks']['recentJobs'], 'ok')

    def test_api_errors_fail_closed(self):
        for key, category in (('nodes','nodes'), ('deployments','workloads'),
                              ('persistentvolumeclaims','pvcs'), ('jobs','recentJobs'),
                              ('volumes.longhorn.io','longhorn')):
            previous = self.data[key]
            self.data[key] = 'API_ERROR'
            self.assertEqual(self.health()['checks'][category], 'failed')
            self.data[key] = previous


if __name__ == '__main__':
    unittest.main()
