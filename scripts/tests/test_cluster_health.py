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
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(self.requests)

    def test_untrusted_ca_is_rejected(self):
        self.make_certificate(self.sa / 'ca.crt', self.sa / 'other.key')
        result = self.nodes()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('certificate', result.stderr)
        self.assertFalse(self.requests)


if __name__ == '__main__':
    unittest.main()
