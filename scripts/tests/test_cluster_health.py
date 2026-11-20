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


class ClusterHealthVolumeTests(unittest.TestCase):
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
            FLUX_QUERY: 'flux-system/Kustomization/kyverno-policies\tTrue',
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
        self.data[FLUX_QUERY] = 'flux-system/Kustomization/kyverno-policies\tFalse'
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
            FLUX_QUERY: ('flux-system/Kustomization/bad\t',
                         'flux-system/Kustomization/bad\tbogus'),
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
            FLUX_QUERY: 'flux-system/Kustomization/unhealthy\tFalse',
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
                    data = {'kind': 'APIGroupList', 'apiVersion': 'v1', 'groups': []}
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

    def test_untrusted_ca_is_rejected(self):
        self.make_certificate(self.sa / 'ca.crt', self.sa / 'other.key')
        result = self.nodes()
        self.assertEqual(result.returncode, 2)
        self.assertIn('certificate', result.stderr)
        self.assertFalse(self.requests)


if __name__ == '__main__':
    unittest.main()
