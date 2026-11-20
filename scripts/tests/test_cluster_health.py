"""Run the deployed shell health functions against bounded kubectl fixtures."""
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[2]
FLUX_QUERY = 'kustomizations.kustomize.toolkit.fluxcd.io,helmreleases.helm.toolkit.fluxcd.io'
HEALTH_QUERIES = {
    'nodes': ('nodes', 'nodes'),
    'deployments': ('workloads', 'deployments'),
    'statefulsets': ('workloads', 'statefulsets'),
    'daemonsets': ('workloads', 'daemonsets'),
    'persistentvolumeclaims': ('pvcs', 'pvcs'),
    FLUX_QUERY: ('flux', 'flux'),
    'volumes.longhorn.io': ('longhorn', 'longhornVolumes'),
    'nodes.longhorn.io': ('longhorn', 'longhornNodes'),
    'jobs': ('recentJobs', 'recentJobs'),
}
API_ERROR_OUTPUT = 'raw-api-error: Bearer fixture-secret-token "unsafe"\\payload'


def generator_source():
    text = (ROOT / 'infra/pod-report/deployment.yaml').read_text()
    source = text.split('            - |\n', 1)[1].split('          volumeMounts:', 1)[0]
    return '\n'.join(line[14:] for line in source.splitlines())


def readiness_command(report):
    deployment = yaml.safe_load((ROOT / 'infra/pod-report/deployment.yaml').read_text())
    generator = next(c for c in deployment['spec']['template']['spec']['containers']
                     if c['name'] == 'generator')
    return [arg.replace('/work/health.json', str(report))
            for arg in generator['readinessProbe']['exec']['command']]


class ClusterHealthVolumeTests(unittest.TestCase):
    def test_nginx_http_readiness_and_liveness_are_unchanged(self):
        deployment = yaml.safe_load((ROOT / 'infra/pod-report/deployment.yaml').read_text())
        web = next(c for c in deployment['spec']['template']['spec']['containers']
                   if c['name'] == 'web')
        self.assertEqual(web['readinessProbe'], {
            'httpGet': {'path': '/', 'port': 'http'},
            'periodSeconds': 10, 'timeoutSeconds': 2, 'failureThreshold': 6,
        })
        self.assertEqual(web['livenessProbe'], {
            'httpGet': {'path': '/', 'port': 'http'},
            'periodSeconds': 20, 'timeoutSeconds': 2, 'failureThreshold': 3,
        })

    def test_freshness_only_gates_readiness_and_service_uses_ready_endpoints(self):
        deployment = yaml.safe_load((ROOT / 'infra/pod-report/deployment.yaml').read_text())
        pod = deployment['spec']['template']
        generator = next(c for c in pod['spec']['containers'] if c['name'] == 'generator')
        self.assertIn('readinessProbe', generator)
        self.assertNotIn('livenessProbe', generator)
        self.assertNotIn('startupProbe', generator)
        service = yaml.safe_load((ROOT / 'infra/pod-report/service.yaml').read_text())
        self.assertFalse(service['spec'].get('publishNotReadyAddresses', False))
        for label, value in service['spec']['selector'].items():
            self.assertEqual(pod['metadata']['labels'][label], value)

    def test_kubeconfig_is_outside_nginx_served_volumes(self):
        deployment = yaml.safe_load((ROOT / 'infra/pod-report/deployment.yaml').read_text())
        pod = deployment['spec']['template']['spec']
        generator = next(c for c in pod['containers'] if c['name'] == 'generator')
        web = next(c for c in pod['containers'] if c['name'] == 'web')
        config_line = next(line for line in generator['args'][0].splitlines()
                           if line.startswith('export KUBECONFIG='))
        config_path = PurePosixPath(config_line.split('=', 1)[1])
        self.assertTrue(config_path.is_absolute())
        config_mount = next(m for m in generator['volumeMounts']
                            if config_path.is_relative_to(PurePosixPath(m['mountPath'])))
        self.assertFalse(config_mount.get('readOnly', False))
        volume = next(v for v in pod['volumes'] if v['name'] == config_mount['name'])
        self.assertEqual(volume['emptyDir'], {})
        for container in pod['containers']:
            if container['name'] != 'generator':
                self.assertNotIn(config_mount['name'],
                                 {m['name'] for m in container.get('volumeMounts', [])})
        published = next(m for m in generator['volumeMounts'] if m['mountPath'] == '/work')
        served = next(m for m in web['volumeMounts']
                      if m['mountPath'] == '/usr/share/nginx/html')
        self.assertEqual(published['name'], served['name'])
        self.assertTrue(served['readOnly'])
        self.assertFalse(config_path.is_relative_to(PurePosixPath('/work')))


class ClusterHealthFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.report = self.root / 'health.json'
        self.now = 2_000_000_000
        # Fix only the clock; execute the deployed shell command and real stat
        # against actual file contents, permissions and modification timestamps.
        clock = self.root / 'date'
        clock.write_text(f'#!/bin/sh\nprintf "%s\\n" {self.now}\n')
        clock.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.root) + ':' + os.environ['PATH'])

    def write_report(self, age=0, content='{"status":"healthy"}\n'):
        self.report.write_text(content)
        os.utime(self.report, (self.now - age, self.now - age))

    def probe(self):
        return subprocess.run(readiness_command(self.report), env=self.env,
                              capture_output=True, text=True, timeout=2)

    def test_fresh_report_is_ready(self):
        self.write_report()
        result = self.probe()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_report_is_unready(self):
        self.assertNotEqual(self.probe().returncode, 0)

    def test_empty_report_is_unready(self):
        self.write_report(content='')
        self.assertNotEqual(self.probe().returncode, 0)

    def test_unreadable_report_is_unready(self):
        if os.geteuid() == 0:
            self.skipTest('permission check requires a non-root user like the generator')
        self.write_report()
        self.report.chmod(0)
        self.assertNotEqual(self.probe().returncode, 0)

    def test_directory_is_unready(self):
        self.report.mkdir()
        os.utime(self.report, (self.now, self.now))
        self.assertNotEqual(self.probe().returncode, 0)

    def test_five_minute_freshness_boundary(self):
        for age, ready in ((299, True), (300, True), (301, False), (600, False)):
            with self.subTest(age=age):
                self.write_report(age=age)
                self.assertEqual(self.probe().returncode == 0, ready)

    def test_future_dated_report_is_unready(self):
        for age in (-1, -600):
            with self.subTest(age=age):
                self.write_report(age=age)
                self.assertNotEqual(self.probe().returncode, 0)

    def test_atomic_replacement_of_stale_report_recovers_readiness(self):
        self.write_report(age=600)
        self.assertNotEqual(self.probe().returncode, 0)
        replacement = self.report.with_suffix('.json.tmp')
        replacement.write_text('{"status":"healthy"}\n')
        os.utime(replacement, (self.now, self.now))
        replacement.replace(self.report)
        self.assertEqual(self.probe().returncode, 0)

    def test_freshness_does_not_classify_health(self):
        for status in ('healthy', 'unhealthy', 'unknown'):
            with self.subTest(status=status):
                self.write_report(content=json.dumps({'status': status}))
                self.assertEqual(self.probe().returncode, 0)

    def test_timestamp_command_errors_fail_closed(self):
        self.write_report()
        for command in ('stat', 'date'):
            with self.subTest(command=command):
                stub = self.root / command
                previous = stub.read_text() if stub.exists() else None
                stub.write_text('#!/bin/sh\nexit 1\n')
                stub.chmod(0o755)
                self.assertNotEqual(self.probe().returncode, 0)
                if previous is None:
                    stub.unlink()
                else:
                    stub.write_text(previous)


class ClusterHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.config = self.root / 'config'
        self.config.mkdir()
        self.data = {
            'nodes': 'node1\tTrue\tFalse\tFalse\tFalse',
            'deployments': 'koito/bridge\t1\t1\ngitea/gitea-mirror\t0\t',
            'statefulsets': 'gitea/postgres\t1\t1',
            'daemonsets': 'kube-system/cilium\t8\t8',
            'persistentvolumeclaims': 'gitea/data\tBound',
            FLUX_QUERY: 'flux-system/Kustomization/kyverno-policies\tReady=True\t',
            'volumes.longhorn.io': 'attached-vol\tattached\thealthy\nretired-vol\tdetached\tunknown',
            'nodes.longhorn.io': 'node1\tTrue', 'jobs': '',
        }
        command = self.root / 'kubectl'
        command.write_text('''#!/usr/bin/env python3
import json, os, sys
args=sys.argv[1:]
value=json.load(open(os.environ['HEALTH_FIXTURE']))[args[args.index('get')+1]]
if isinstance(value, dict):
    print(value.get('stdout', ''))
    print(value.get('stderr', ''), file=sys.stderr)
    sys.exit(value.get('returncode', 1))
if value == 'API_ERROR': sys.exit(1)
print(value)
''')
        command.chmod(0o755)
        self.script = (generator_source().split('while true; do', 1)[0]
                       .replace('/work/', str(self.work) + '/')
                       .replace('/var/run/pod-report/', str(self.config) + '/'))

    def health(self):
        fixture = self.root / 'data.json'
        fixture.write_text(json.dumps(self.data))
        result = subprocess.run(['sh', '-c', self.script + '\nnow="2026-11-19T00:00:00Z"\nevaluate_health\n'],
                                env=dict(os.environ, PATH=str(self.root)+':'+os.environ['PATH'], HEALTH_FIXTURE=str(fixture)),
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads((self.work / 'health.json').read_text())

    def test_healthy_detached_volumes_and_retired_deployment(self):
        health = self.health()
        self.assertEqual(health['status'], 'healthy')
        self.assertEqual(set(health['checks']), {category for category, _ in HEALTH_QUERIES.values()})
        self.assertTrue(all(state == 'ok' for state in health['checks'].values()))
        self.assertEqual(health['collectionErrors'], {})
        self.assertEqual(health['observedAt'], '2026-11-19T00:00:00Z')
        self.assertTrue(all(not failures for failures in health['failures'].values()))

    def test_freshly_generated_report_passes_readiness(self):
        self.health()
        result = subprocess.run(readiness_command(self.work / 'health.json'),
                                capture_output=True, text=True, timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_expected_kubescape_suspension_is_neutral_with_healthy_active_flux(self):
        self.data[FLUX_QUERY] += ('\nmonitoring/HelmRelease/gatus\tReady=True\tfalse'
                                  '\nkubescape/HelmRelease/kubescape\t\ttrue')
        health = self.health()
        self.assertEqual(health['status'], 'healthy')
        self.assertEqual(health['checks']['flux'], 'ok')
        self.assertEqual(health['collectionErrors'], {})
        self.assertEqual(health['failures']['flux'], [])
        # Neutral evidence is omitted, rather than rewritten as reconciled.
        result = subprocess.run(
            ['sh', '-c', self.script + '\nprintf "kubescape/HelmRelease/kubescape\\t\\ttrue\\n" | valid_status_rows flux'],
            capture_output=True, text=True, timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_only_expected_kubescape_suspension_is_unknown(self):
        neutral = 'kubescape/HelmRelease/kubescape\t\ttrue'
        for rows in ((neutral,), (neutral, neutral)):
            with self.subTest(rows=rows):
                self.data[FLUX_QUERY] = '\n'.join(rows)
                health = self.health()
                self.assertEqual(health['status'], 'unknown')
                self.assertEqual(health['checks']['flux'], 'unknown')
                self.assertEqual(health['collectionErrors'], {'flux': 'empty_result'})
                self.assertEqual(health['failures']['flux'], [])

    def test_missing_flux_ready_is_unknown_without_exact_suspension_allowlist(self):
        for identity, suspend in (
                ('kubescape/HelmRelease/kubescape', ''),
                ('kubescape/HelmRelease/kubescape', 'false'),
                ('other/HelmRelease/kubescape', 'true'),
                ('kubescape/HelmRelease/other', 'true'),
                ('kubescape/Kustomization/kubescape', 'true'),
                ('flux-system/Kustomization/running', 'false'),
                ('monitoring/HelmRelease/running', '')):
            with self.subTest(identity=identity, suspend=suspend):
                self.data[FLUX_QUERY] = f'{identity}\t\t{suspend}'
                health = self.health()
                self.assertEqual(health['status'], 'unknown')
                self.assertEqual(health['checks']['flux'], 'unknown')
                self.assertEqual(health['collectionErrors'], {'flux': 'invalid_data'})
                self.assertEqual(health['failures']['flux'], [])

    def test_kubescape_activation_reenters_normal_flux_health(self):
        for suspend in ('', 'false'):
            for ready in ('True', 'False', 'Unknown'):
                with self.subTest(suspend=suspend, ready=ready):
                    self.data[FLUX_QUERY] = f'kubescape/HelmRelease/kubescape\tReady={ready}\t{suspend}'
                    health = self.health()
                    success = ready == 'True'
                    self.assertEqual(health['status'], 'healthy' if success else 'unhealthy')
                    self.assertEqual(health['checks']['flux'], 'ok' if success else 'failed')
                    self.assertEqual(health['collectionErrors'], {})
                    self.assertEqual(health['failures']['flux'], [] if success else
                                     [f'kubescape/HelmRelease/kubescape Ready={ready}'])

    def test_suspended_flux_failure_conditions_are_never_hidden(self):
        for identity in ('kubescape/HelmRelease/kubescape', 'other/HelmRelease/release',
                         'flux-system/Kustomization/other'):
            for ready in ('False', 'Unknown'):
                with self.subTest(identity=identity, ready=ready):
                    self.data[FLUX_QUERY] = f'{identity}\tReady={ready}\ttrue'
                    health = self.health()
                    self.assertEqual(health['status'], 'unhealthy')
                    self.assertEqual(health['checks']['flux'], 'failed')
                    self.assertEqual(health['collectionErrors'], {})
                    self.assertEqual(health['failures']['flux'], [f'{identity} Ready={ready}'])

    def test_suspended_ready_true_cannot_claim_successful_flux_health(self):
        for identity in ('kubescape/HelmRelease/kubescape', 'other/HelmRelease/release',
                         'flux-system/Kustomization/other'):
            with self.subTest(identity=identity):
                self.data[FLUX_QUERY] = f'{identity}\tReady=True\ttrue'
                health = self.health()
                self.assertEqual(health['status'], 'unknown')
                self.assertEqual(health['checks']['flux'], 'unknown')
                self.assertEqual(health['collectionErrors'], {'flux': 'invalid_data'})
                self.assertEqual(health['failures']['flux'], [])

    def test_flux_suspension_does_not_hide_failures_or_malformed_rows(self):
        neutral = 'kubescape/HelmRelease/kubescape\t\ttrue'
        failure = 'flux-system/Kustomization/kyverno-policies\tReady=False\t'
        malformed = 'other/HelmRelease/missing-ready\t\ttrue'
        for rows in ((neutral, failure), (failure, neutral), (neutral, malformed), (malformed, neutral),
                     (neutral, failure, malformed), (malformed, failure, neutral)):
            with self.subTest(rows=rows):
                self.data[FLUX_QUERY] = '\n'.join(rows)
                health = self.health()
                failed = failure in rows
                self.assertEqual(health['status'], 'unhealthy' if failed else 'unknown')
                self.assertEqual(health['checks']['flux'], 'failed' if failed else 'unknown')
                self.assertEqual(health['collectionErrors'],
                                 {'flux': 'invalid_data'} if malformed in rows else {})
                self.assertEqual(health['failures']['flux'],
                                 ['flux-system/Kustomization/kyverno-policies Ready=False'] if failed else [])

    def test_flux_failure_identities_remain_bounded_with_expected_suspension(self):
        self.data[FLUX_QUERY] = '\n'.join(
            ['kubescape/HelmRelease/kubescape\t\ttrue'] +
            [f'test/HelmRelease/failed-{i}\tReady=False\tfalse' for i in range(25)])
        health = self.health()
        self.assertEqual(health['status'], 'unhealthy')
        self.assertEqual(health['checks']['flux'], 'failed')
        self.assertEqual(health['collectionErrors'], {})
        self.assertEqual(health['failures']['flux'],
                         [f'test/HelmRelease/failed-{i} Ready=False' for i in range(10)])

    def test_unready_node_is_confirmed_failure(self):
        self.data['nodes'] = 'node1\tFalse\tFalse\tFalse\tFalse'
        health = self.health()
        self.assertEqual(health['status'], 'unhealthy')
        self.assertEqual(health['checks']['nodes'], 'failed')
        self.assertEqual(health['collectionErrors'], {})

    def test_all_failing_workload_types_and_flux_are_reported(self):
        self.data['deployments'] = 'koito/bridge\t1\t'
        self.data['statefulsets'] = 'gitea/postgres\t1\t0'
        self.data['daemonsets'] = 'kube-system/cilium\t8\t7'
        self.data[FLUX_QUERY] = 'flux-system/Kustomization/kyverno-policies\tReady=False\t'
        health = self.health()
        self.assertEqual(health['status'], 'unhealthy')
        self.assertEqual(health['checks']['workloads'], 'failed')
        self.assertEqual(len(health['failures']['workloads']), 3)
        self.assertIn('kyverno-policies', health['failures']['flux'][0])

    def test_degraded_attached_or_detached_volume_remains_failed(self):
        for state in ('attached', 'detached'):
            self.data['volumes.longhorn.io'] = f'vol\t{state}\tdegraded'
            health = self.health()
            self.assertEqual(health['status'], 'unhealthy')
            self.assertEqual(health['checks']['longhorn'], 'failed')
            self.assertEqual(health['collectionErrors'], {})

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

    def test_each_query_error_is_unknown_and_cannot_report_healthy(self):
        for key, (category, subcheck) in HEALTH_QUERIES.items():
            previous = self.data[key]
            with self.subTest(query=key):
                self.data[key] = 'API_ERROR'
                health = self.health()
                self.assertEqual(health['status'], 'unknown')
                self.assertEqual(health['checks'][category], 'unknown')
                self.assertEqual(health['collectionErrors'], {subcheck: 'query_failed'})
                self.assertTrue(all(state == 'ok' for name, state in health['checks'].items()
                                    if name != category))
                self.assertTrue(all(not failures for failures in health['failures'].values()))
            self.data[key] = previous

    def test_unavailable_api_reports_all_categories_unknown_without_raw_output(self):
        self.data = {key: {'stdout': API_ERROR_OUTPUT, 'stderr': API_ERROR_OUTPUT,
                           'returncode': 1} for key in self.data}
        health = self.health()  # json.loads also verifies the published JSON is valid.
        self.assertEqual(health['status'], 'unknown')
        self.assertTrue(all(state == 'unknown' for state in health['checks'].values()))
        self.assertEqual(health['collectionErrors'],
                         {subcheck: 'query_failed' for _, subcheck in HEALTH_QUERIES.values()})
        self.assertTrue(all(not failures for failures in health['failures'].values()))
        published = (self.work / 'health.json').read_text()
        self.assertNotIn('raw-api-error', published)
        self.assertNotIn('fixture-secret-token', published)
        self.assertNotIn('unsafe', published)
        self.assertLess(len(published), 1024)

    def test_workload_failure_survives_another_workload_query_error(self):
        for failed, unavailable in (('deployments', 'statefulsets'),
                                    ('statefulsets', 'deployments'),
                                    ('daemonsets', 'statefulsets')):
            original = self.data.copy()
            with self.subTest(failed=failed, unavailable=unavailable):
                self.data[failed] = 'test/unhealthy\t2\t1'
                self.data[unavailable] = {'stdout': 'test/untrusted\t2\t0',
                                          'stderr': API_ERROR_OUTPUT}
                health = self.health()
                self.assertEqual(health['status'], 'unhealthy')
                self.assertEqual(health['checks']['workloads'], 'failed')
                self.assertEqual(health['collectionErrors'], {unavailable: 'query_failed'})
                self.assertEqual(health['failures']['workloads'],
                                 ['test/unhealthy desired=2 available=1'])
            self.data = original

    def test_longhorn_failure_survives_other_longhorn_query_error(self):
        for failed, unavailable, evidence in (
                ('volumes.longhorn.io', 'nodes.longhorn.io', 'vol\tattached\tdegraded'),
                ('nodes.longhorn.io', 'volumes.longhorn.io', 'node1\tFalse')):
            original = self.data.copy()
            with self.subTest(failed=failed):
                self.data[failed] = evidence
                self.data[unavailable] = 'API_ERROR'
                health = self.health()
                self.assertEqual(health['status'], 'unhealthy')
                self.assertEqual(health['checks']['longhorn'], 'failed')
                self.assertEqual(health['collectionErrors'],
                                 {HEALTH_QUERIES[unavailable][1]: 'query_failed'})
            self.data = original

    def test_confirmed_failure_keeps_other_category_unknown(self):
        self.data['nodes'] = 'node1\tFalse\tFalse\tFalse\tFalse'
        self.data['jobs'] = 'API_ERROR'
        health = self.health()
        self.assertEqual(health['status'], 'unhealthy')
        self.assertEqual(health['checks']['nodes'], 'failed')
        self.assertEqual(health['checks']['recentJobs'], 'unknown')
        self.assertEqual(health['collectionErrors'], {'recentJobs': 'query_failed'})

    def test_empty_required_results_are_unknown(self):
        for key in ('nodes', FLUX_QUERY, 'volumes.longhorn.io', 'nodes.longhorn.io'):
            previous = self.data[key]
            with self.subTest(query=key):
                self.data[key] = ''
                health = self.health()
                category, subcheck = HEALTH_QUERIES[key]
                self.assertEqual(health['status'], 'unknown')
                self.assertEqual(health['checks'][category], 'unknown')
                self.assertEqual(health['collectionErrors'], {subcheck: 'empty_result'})
            self.data[key] = previous

    def test_malformed_successful_query_rows_are_unknown(self):
        cases = {
            'nodes': ('node1\t\tFalse\tFalse\tFalse',
                      'node1\tTrue\tFalse\tFalse', 'node1\tbogus\tFalse\tFalse\tFalse'),
            'deployments': ('test/bad\tbogus\tjunk', 'test/bad\t1\tjunk',
                            'test/bad\t\t0', 'test/bad\t0', 'test/bad\t0\t0\textra', '\t0\t0',
                            'test/bad\t99999999999999999999\t0'),
            'statefulsets': ('test/bad\tbogus\tjunk', 'test/bad\t-1\t0'),
            'daemonsets': ('test/bad\tbogus\tjunk', 'test/bad\t1.5\t0'),
            'persistentvolumeclaims': ('test/bad', 'test/bad\tbogus'),
            FLUX_QUERY: ('flux-system/Kustomization/bad\t\t',
                         'flux-system/Kustomization/bad\tbogus\t',
                         'kubescape/HelmRelease/kubescape\t\tTRUE',
                         'kubescape/HelmRelease/kubescape\t\t1',
                         'kubescape/HelmRelease/kubescape\t\tnull',
                         'kubescape/HelmRelease/kubescape\t\ttrue ',
                         'kubescape/HelmRelease/kubescape\tReady=True\tbogus',
                         'kubescape/HelmRelease/kubescape\t\ttrue\textra',
                         'kubescape/HelmRelease/kubescape\ttrue',
                         'kubescape/HelmRelease/kubescape\tReady=\ttrue',
                         'kubescape/HelmRelease/kubescape\tTrue\ttrue',
                         'kubescape/HelmRelease/kubescape\tReady=True',
                         'kubescape/HelmRelease/kubescape\tReady=TrueTrue\ttrue',
                         'kubescape/HelmRelease/kubescape\tReady=FalseTrue\tfalse',
                         'Kubescape/HelmRelease/kubescape\tReady=True\tfalse',
                         'kubescape/HelmRelease/kubescape-\tReady=True\tfalse',
                         'kubescape/HelmRelease/kubescape..bad\tReady=True\tfalse',
                         'kubescape/HelmRelease/kubescape/extra\tReady=True\tfalse',
                         'kubescape/Deployment/kubescape\tReady=True\tfalse',
                         f'{"n" * 64}/HelmRelease/kubescape\tReady=True\tfalse',
                         f'kubescape/HelmRelease/{"n" * 254}\tReady=True\tfalse'),
            'volumes.longhorn.io': ('bad\tattached', 'bad\tbogus\thealthy',
                                    'bad\tattached\tbogus'),
            'nodes.longhorn.io': ('bad\t', 'bad\tbogus'),
            'jobs': ('test/bad', 'test/bad|0||True|extra',
                     'test/bad|0||bogus', 'test/bad|bogus||True',
                     'test/bad|0|bogus|True', 'test/bad|99999999999999999999||'),
        }
        for query, rows in cases.items():
            previous = self.data[query]
            for row in (*rows, 'bad identity'):
                with self.subTest(query=query, row=row):
                    self.data[query] = row
                    health = self.health()
                    category, subcheck = HEALTH_QUERIES[query]
                    self.assertEqual(health['status'], 'unknown')
                    self.assertEqual(health['checks'][category], 'unknown')
                    self.assertEqual(health['collectionErrors'], {subcheck: 'invalid_data'})
                    self.assertTrue(all(not failures for failures in health['failures'].values()))
            self.data[query] = previous

    def test_zero_replica_workloads_and_empty_lists_remain_healthy(self):
        for counts in ('0\t', '0\t0', None):
            with self.subTest(counts=counts):
                for query in ('deployments', 'statefulsets', 'daemonsets'):
                    self.data[query] = f'test/zero\t{counts}' if counts is not None else ''
                health = self.health()
                self.assertEqual(health['status'], 'healthy')
                self.assertEqual(health['checks']['workloads'], 'ok')
                self.assertEqual(health['collectionErrors'], {})

    def test_confirmed_failure_survives_invalid_workload_subcheck(self):
        self.data['deployments'] = 'test/bad\tbogus\tjunk'
        self.data['statefulsets'] = 'test/unhealthy\t2\t'
        health = self.health()
        self.assertEqual(health['status'], 'unhealthy')
        self.assertEqual(health['checks']['workloads'], 'failed')
        self.assertEqual(health['collectionErrors'], {'deployments': 'invalid_data'})
        self.assertEqual(health['failures']['workloads'], ['test/unhealthy desired=2 available=0'])

    def test_confirmed_failure_rows_survive_other_invalid_rows(self):
        rows = {
            'nodes': 'node1\tFalse\tFalse\tFalse\tFalse',
            'deployments': 'test/unhealthy\t2\t1',
            'statefulsets': 'test/unhealthy\t2\t1',
            'daemonsets': 'test/unhealthy\t2\t1',
            'persistentvolumeclaims': 'test/unhealthy\tPending',
            FLUX_QUERY: 'flux-system/Kustomization/unhealthy\tReady=False\t',
            'volumes.longhorn.io': 'unhealthy\tattached\tdegraded',
            'nodes.longhorn.io': 'unhealthy\tFalse',
            'jobs': f'test/unhealthy|1|{datetime.now(timezone.utc).isoformat()}|',
        }
        for query, row in rows.items():
            previous = self.data[query]
            for combined in (row + '\n' + API_ERROR_OUTPUT, API_ERROR_OUTPUT + '\n' + row):
                with self.subTest(query=query, rows=combined):
                    self.data[query] = combined
                    health = self.health()
                    category, subcheck = HEALTH_QUERIES[query]
                    self.assertEqual(health['status'], 'unhealthy')
                    self.assertEqual(health['checks'][category], 'failed')
                    self.assertEqual(health['collectionErrors'], {subcheck: 'invalid_data'})
                    if category in health['failures']:
                        self.assertEqual(len(health['failures'][category]), 1)
                        self.assertIn('unhealthy', health['failures'][category][0])
                    self.assertNotIn('raw-api-error', (self.work / 'health.json').read_text())
                    self.assertNotIn('fixture-secret-token', (self.work / 'health.json').read_text())
            self.data[query] = previous

    def test_job_timestamp_error_retains_confirmed_failure_in_either_order(self):
        failed = f'test/failed|1|{datetime.now(timezone.utc).isoformat()}|'
        invalid = 'test/invalid|1|unparseable-time|'
        for rows in ((failed, invalid), (invalid, failed), (invalid,)):
            with self.subTest(rows=rows):
                self.data['jobs'] = '\n'.join(rows)
                health = self.health()
                self.assertEqual(health['status'], 'unhealthy' if failed in rows else 'unknown')
                self.assertEqual(health['checks']['recentJobs'], 'failed' if failed in rows else 'unknown')
                self.assertEqual(health['collectionErrors'], {'recentJobs': 'invalid_data'})
                self.assertEqual(health['failures']['recentJobs'],
                                 ['test/failed failed-attempts=1'] if failed in rows else [])

    def test_missing_job_timestamp_or_invalid_count_is_unknown(self):
        for row in ('test/invalid|1||', 'test/invalid|invalid-count|2026-11-19T00:00:00Z|'):
            with self.subTest(row=row):
                self.data['jobs'] = row
                health = self.health()
                self.assertEqual(health['status'], 'unknown')
                self.assertEqual(health['checks']['recentJobs'], 'unknown')
                self.assertEqual(health['collectionErrors'], {'recentJobs': 'invalid_data'})
                self.assertEqual(health['failures']['recentJobs'], [])

    def test_failed_local_check_command_is_unknown(self):
        self.data['jobs'] = 'test/failed|1|2026-11-19T00:00:00Z|'
        command = self.root / 'date'
        command.write_text('#!/bin/sh\necho raw-date-error >&2\nexit 42\n')
        command.chmod(0o755)
        health = self.health()
        self.assertEqual(health['status'], 'unknown')
        self.assertEqual(health['checks']['recentJobs'], 'unknown')
        self.assertEqual(health['collectionErrors'], {'recentJobs': 'check_failed'})
        self.assertNotIn('raw-date-error', (self.work / 'health.json').read_text())


class ClusterHealthAPIConfigTests(unittest.TestCase):
    """Exercise the collector's connection setup with real kubectl and TLS."""

    def setUp(self):
        self.kubectl = shutil.which('kubectl')
        if not self.kubectl or not shutil.which('openssl'):
            self.skipTest('kubectl and openssl are required for TLS regression tests')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.config = self.root / 'config'
        self.config.mkdir()
        self.sa = self.root / 'serviceaccount'
        self.sa.mkdir()
        (self.sa / 'token').write_text('test-pod-report-token')
        self.make_certificate(self.sa / 'ca.crt', self.sa / 'server.key')
        self.requests = []
        self.api_error = None
        self.flux_items = []
        fixture = self

        class API(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                fixture.requests.append((self.path, self.headers.get('Authorization')))
                path = urlsplit(self.path).path
                code = 200
                error = fixture.api_error
                if self.headers.get('Authorization') != 'Bearer test-pod-report-token':
                    error = 401
                if error:
                    code = error
                    reason = {401: 'Unauthorized', 403: 'Forbidden', 404: 'NotFound'}[error]
                    data = {'kind': 'Status', 'apiVersion': 'v1', 'status': 'Failure',
                            'reason': reason, 'message': reason, 'code': code}
                elif path == '/api':
                    data = {'kind': 'APIVersions', 'apiVersion': 'v1', 'versions': ['v1']}
                elif path == '/apis':
                    data = {'kind': 'APIGroupList', 'apiVersion': 'v1', 'groups': [
                        {'name': group, 'versions': [{'groupVersion': group + '/v1', 'version': 'v1'}],
                         'preferredVersion': {'groupVersion': group + '/v1', 'version': 'v1'}}
                        for group in ('kustomize.toolkit.fluxcd.io', 'helm.toolkit.fluxcd.io')]}
                elif path in ('/apis/kustomize.toolkit.fluxcd.io/v1', '/apis/helm.toolkit.fluxcd.io/v1'):
                    group_version = path.removeprefix('/apis/')
                    kind, resource = (('Kustomization', 'kustomizations') if 'kustomize.' in path
                                      else ('HelmRelease', 'helmreleases'))
                    data = {'kind': 'APIResourceList', 'apiVersion': 'v1', 'groupVersion': group_version,
                            'resources': [{'name': resource, 'singularName': resource[:-1],
                                           'namespaced': True, 'kind': kind, 'verbs': ['list']}]}
                elif path in ('/apis/kustomize.toolkit.fluxcd.io/v1/kustomizations',
                              '/apis/helm.toolkit.fluxcd.io/v1/helmreleases'):
                    kind = 'Kustomization' if 'kustomize.' in path else 'HelmRelease'
                    data = {'kind': kind + 'List', 'apiVersion': path.split('/')[2] + '/v1',
                            'items': [item for item in fixture.flux_items if item['kind'] == kind]}
                elif path == '/api/v1':
                    data = {'kind': 'APIResourceList', 'apiVersion': 'v1', 'groupVersion': 'v1',
                            'resources': [{'name': 'nodes', 'singularName': 'node',
                                           'namespaced': False, 'kind': 'Node', 'verbs': ['list']}]}
                elif path == '/api/v1/nodes':
                    data = {'kind': 'NodeList', 'apiVersion': 'v1', 'items': [
                        {'metadata': {'name': 'node1'}, 'status': {'conditions': [
                            {'type': kind, 'status': state} for kind, state in (
                                ('Ready', 'True'), ('DiskPressure', 'False'),
                                ('MemoryPressure', 'False'), ('PIDPressure', 'False'))]}}]}
                else:
                    code, data = 404, {'kind': 'Status', 'apiVersion': 'v1', 'code': 404}
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), API)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.sa / 'ca.crt', self.sa / 'server.key')
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.script = (generator_source().split('while true; do', 1)[0]
                       .replace('/work/', str(self.work) + '/')
                       .replace('/var/run/pod-report/', str(self.config) + '/')
                       .replace('/var/run/secrets/kubernetes.io/serviceaccount', str(self.sa))
                       .replace('https://kubernetes.default.svc:443',
                                f'https://127.0.0.1:{self.server.server_port}'))
        self.env = dict(os.environ, KUBECONFIG=str(self.root / 'missing-config'),
                        KUBERNETES_SERVICE_HOST='127.0.0.1',
                        KUBERNETES_SERVICE_PORT=str(self.server.server_port),
                        KUBERNETES_MASTER='http://127.0.0.1:1',
                        HTTP_PROXY='', HTTPS_PROXY='', ALL_PROXY='', NO_PROXY='127.0.0.1')

    def make_certificate(self, cert, key):
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(cert), '-days', '1',
                        '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1'],
                       check=True, capture_output=True)

    def nodes(self):
        # Keep discovery caches inside the fixture; use the real executable.
        wrapper = ('kubectl() { command ' + shlex.quote(self.kubectl)
                   + ' --cache-dir=' + shlex.quote(str(self.root / 'cache')) + ' "$@"; }')
        command = self.script + '\n' + wrapper + '\nnodes_ok\n'
        return subprocess.run(['sh', '-c', command], env=self.env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=10)

    def test_timeout_query_uses_explicit_api_ca_and_projected_token(self):
        result = self.nodes()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('/api/v1/nodes', {urlsplit(path).path for path, _ in self.requests})
        self.assertTrue(all(auth == 'Bearer test-pod-report-token' for _, auth in self.requests))
        self.assertTrue(any(parse_qs(urlsplit(path).query).get('timeout') == ['15s']
                            for path, _ in self.requests))
        self.assertNotIn("Couldn't get current server API group list", result.stderr)
        self.assertNotIn('test-pod-report-token', result.stdout + result.stderr)
        self.assertNotIn('test-pod-report-token', (self.config / 'kubeconfig').read_text())
        self.assertFalse((self.work / 'kubeconfig').exists())

    def test_api_errors_are_not_treated_as_success(self):
        for code in (401, 403, 404):
            with self.subTest(code=code):
                self.requests.clear()
                self.api_error = code
                result = self.nodes()
                self.assertEqual(result.returncode, 2)
                self.assertTrue(self.requests)

    def test_flux_jsonpath_collects_ready_and_suspend_with_real_kubectl(self):
        self.flux_items = [
            {'apiVersion': group + '/v1', 'kind': kind,
             'metadata': {'namespace': namespace, 'name': name}, 'spec': spec,
             'status': {'conditions': conditions}}
            for group, kind, namespace, name, spec, conditions in (
                ('kustomize.toolkit.fluxcd.io', 'Kustomization', 'flux-system', 'active', {},
                 [{'type': 'Ready', 'status': 'True'}]),
                ('helm.toolkit.fluxcd.io', 'HelmRelease', 'monitoring', 'gatus', {'suspend': False},
                 [{'type': 'Ready', 'status': 'True'}]),
                ('helm.toolkit.fluxcd.io', 'HelmRelease', 'kubescape', 'kubescape', {'suspend': True}, []))]
        wrapper = ('kubectl() { command ' + shlex.quote(self.kubectl)
                   + ' --cache-dir=' + shlex.quote(str(self.root / 'cache')) + ' "$@"; }')
        command = self.script + '\n' + wrapper + '\nflux_ok\nprintf "%s\\n" "$flux_statuses"\n'
        result = subprocess.run(['sh', '-c', command], env=self.env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.splitlines()), {
            'flux-system/Kustomization/active\tTrue\t',
            'monitoring/HelmRelease/gatus\tTrue\tfalse'})
        self.assertIn('/apis/helm.toolkit.fluxcd.io/v1/helmreleases',
                      {urlsplit(path).path for path, _ in self.requests})
        self.assertIn('/apis/kustomize.toolkit.fluxcd.io/v1/kustomizations',
                      {urlsplit(path).path for path, _ in self.requests})
        # A present Ready condition with missing status must not be confused
        # with the absent condition on the deliberately staged release.
        self.flux_items[-1]['status']['conditions'] = [{'type': 'Ready'}]
        result = subprocess.run(['sh', '-c', self.script + '\n' + wrapper + '\nflux_ok\n'],
                                env=self.env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_untrusted_ca_is_rejected(self):
        self.make_certificate(self.sa / 'ca.crt', self.sa / 'other.key')
        result = self.nodes()
        self.assertEqual(result.returncode, 2)
        self.assertIn('certificate', result.stderr)
        self.assertFalse(self.requests)


if __name__ == '__main__':
    unittest.main()
