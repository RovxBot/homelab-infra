"""Production loop, private outputs, HTTP supervision and deployment boundaries."""
from dataclasses import asdict
from datetime import timedelta
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security_collector.model import Rejected
from security_collector.service import Service, server_for
from security_collector.sources import STORAGE, resource
import test_security_collector as stage_a
import test_security_collector_stage_b as stage_b

ROOT = Path(__file__).resolve().parents[2]
NOW, W = stage_a.NOW, stage_a.W


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.api, self.elapsed = stage_b.FakeAPI(), 0
        self.config = dict(workloads=[asdict(W)], history=str(self.path / 'missing-history'),
            database_lock=stage_b.EXPECTED, agent=dict(pod_name='node-agent-test',
            pod_uid='agent-pod-uid', daemonset_uid='agent-ds-uid'))
        self.services = []
        self.addCleanup(lambda: [s.c.close() for s in self.services])
        self.s = self.create()

    def create(self):
        service = Service(self.config, self.path / 'private', self.api, clock=lambda: self.elapsed)
        self.services.append(service)
        return service

    def test_actual_tick_ingests_and_persists_private_preview_without_writer(self):
        status = self.s.tick(NOW)
        self.assertTrue(status['runtime_read_ok'])
        self.assertFalse(status['assessment_qualified'])
        self.assertTrue(status['continuity_gap'])
        self.assertEqual(status['covered_workloads'], 1)
        self.assertEqual(len(self.s.c.all()), 1)
        preview = (self.path / 'private/previews.json').read_text()
        self.assertIn('R1000', preview)
        self.assertNotIn('UNTRUSTED', preview)
        for path in (self.path / 'private').iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.path / 'private').stat().st_mode & 0o777, 0o700)
        saved = json.loads(self.s.c.db.execute('SELECT data FROM service_status').fetchone()[0])
        self.assertFalse(saved['public_writes_enabled'])
        self.assertFalse(saved['automatic_closure_enabled'])

    def test_restart_preserves_cursor_receipts_and_incident_count(self):
        self.s.tick(NOW)
        before = self.s.intake.cursor('runtime/metal6')
        self.s.c.close()
        self.services.remove(self.s)
        self.s = self.create()
        self.assertEqual(self.s.intake.cursor('runtime/metal6'), before)
        self.s.tick(NOW + timedelta(seconds=30))
        self.assertEqual(self.s.c.all()[0]['count'], 1)
        self.assertEqual(self.s.c.db.execute('SELECT count(*) FROM receipts').fetchone()[0], 1)

    def test_deleted_or_unenrolled_pod_is_observed_without_fabricated_incident(self):
        event = json.loads(self.api.logs.partition(b' ')[2])
        event['RuntimeK8sDetails']['podName'] = 'deleted-standalone-test'
        self.api.logs = b'2026-11-23T00:00:01Z ' + json.dumps(event).encode() + b'\n'
        self.s.tick(NOW)
        self.s.tick(NOW + timedelta(seconds=30))
        self.assertEqual(self.s.c.all(), [])
        rows = self.s.c.db.execute('SELECT data FROM source_observations').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0][0])['rule'], 'R1000')
        self.assertNotIn('deleted-standalone-test', rows[0][0])
        self.assertTrue(self.s.snapshot['continuity_gap'])

    def test_quiet_logs_are_successful_reads_without_incidents(self):
        for payload in (b'', b'2026-11-23T00:00:01Z ordinary Go logger message\n'):
            self.api.logs = payload
            self.s.tick(NOW)
            self.assertEqual(self.s.c.all(), [])
            self.assertTrue(self.s.snapshot['runtime_read_ok'])
            with self.s.c.db:
                self.s.c.db.execute('DELETE FROM polls')

    def test_first_read_is_bounded_to_the_replay_window(self):
        self.s.tick(NOW)
        log = next(path for path in self.api.calls if '/log?' in path)
        self.assertEqual(parse_qs(urlsplit(log).query)['sinceTime'],
                         [stage_a.stamp(NOW - timedelta(days=7))])

    def test_failed_api_does_not_report_success_or_advance_cursor(self):
        self.s.tick(NOW)
        before = self.s.intake.cursor('runtime/metal6')
        self.api.error = True
        self.s.tick(NOW + timedelta(seconds=30))
        self.assertFalse(self.s.snapshot['runtime_read_ok'])
        self.assertEqual(self.s.intake.cursor('runtime/metal6'), before)
        self.assertEqual(self.s.response('/healthz')[0], 503)

    def test_unavailable_history_is_explicit_without_blocking_runtime(self):
        self.s.assessment_available = False
        with patch.object(self.s.assessment, 'poll', side_effect=AssertionError('history must not be read')):
            self.s.tick(NOW)
        self.assertTrue(self.s.snapshot['runtime_read_ok'])
        self.assertFalse(self.s.snapshot['assessment_available'])
        self.assertFalse(self.s.snapshot['assessment_qualified'])
        rows = self.s.c.db.execute("SELECT data FROM reader_status WHERE category='vulnerability'").fetchall()
        self.assertTrue(all(json.loads(r[0])['reason'] == 'history_unavailable' for r in rows))

    def test_hung_loop_becomes_unready_even_if_http_thread_runs(self):
        self.s.tick(NOW)
        self.assertEqual(self.s.response('/readyz')[0], 200)
        self.elapsed = 301
        for path in ('/livez', '/readyz', '/healthz'):
            self.assertEqual(self.s.response(path)[0], 503)
        self.assertTrue(self.s.response('/healthz')[1]['telemetry_stale'])
        self.assertFalse(self.s.response('/healthz')[1]['runtime_read_ok'])
        self.assertEqual(self.s.response('/healthz')[1]['covered_workloads'], 0)

    def test_storage_failure_and_saturation_reach_external_health(self):
        with patch('security_collector.service.atomic', side_effect=OSError('PRIVATE_CREDENTIAL')):
            self.s.tick(NOW)
        self.assertEqual(self.s.response('/readyz')[0], 503)
        self.assertNotIn('PRIVATE_CREDENTIAL', json.dumps(self.s.snapshot))
        fs = os.statvfs(self.path)
        with patch('security_collector.service.os.statvfs', return_value=type('Full', (),
                {'f_bavail': 0, 'f_frsize': fs.f_frsize})()):
            self.s.tick(NOW + timedelta(seconds=30))
        self.assertTrue(self.s.snapshot['saturated'])
        self.assertEqual(self.s.response('/readyz')[0], 503)

    def test_normalized_source_capacity_never_advances_cursor(self):
        self.s.intake.max_pending = 0
        event = json.loads(self.api.logs.partition(b' ')[2])
        event['RuntimeK8sDetails']['podName'] = 'deleted'
        self.api.logs = b'2026-11-23T00:00:01Z ' + json.dumps(event).encode() + b'\n'
        self.s.tick(NOW)
        self.assertIsNone(self.s.intake.cursor('runtime/metal6'))
        self.assertEqual(self.s.c.db.execute('SELECT count(*) FROM source_observations').fetchone()[0], 0)

    def test_production_does_not_automatically_resolve_clean_assessment(self):
        self.s.c.deployment(stage_a.D)
        first = self.s.c.ingest(stage_a.assessment(), NOW)
        self.s.c.ingest(stage_a.assessment(stage_a.clean_bundle()), NOW)
        self.assertEqual(self.s.c.get(first['key'])['status'], 'open')

    def test_real_http_health_has_no_preview_or_detail_route(self):
        self.s.tick(NOW)
        server = server_for(self.s, '127.0.0.1', 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = 'http://127.0.0.1:' + str(server.server_port)
            with urlopen(origin + '/readyz', timeout=2) as response:
                self.assertEqual(json.load(response), {'ready': True})
            for path in ('/previews.json', '/status.json', '/state/collector.sqlite'):
                with self.assertRaises(HTTPError) as error:
                    urlopen(origin + path, timeout=2)
                self.assertEqual(error.exception.code, 404)
                error.exception.close()
            self.assertNotIn('R1000', json.dumps(self.s.response('/healthz')))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_real_kubescape_names_are_valid_but_path_injection_is_rejected(self):
        name = 'docker.io-nginxinc-nginx-unprivileged-sha256-' + 'a' * 64 + '-aaaaaa'
        self.assertTrue(resource(STORAGE, 'kubescape', 'sbomsyfts', name).endswith(name))
        for name in ('../secrets', 'x?y', 'x/y', 'a' * 254):
            with self.assertRaises(Rejected):
                resource(STORAGE, 'kubescape', 'sbomsyfts', name)


class DeploymentBoundaries(unittest.TestCase):
    def test_only_named_reads_no_cluster_role_and_retained_state(self):
        config = json.loads((ROOT / 'infra/kubescape/collector/collector.json').read_text())
        names = sorted(a['pod_name'] for a in config.get('agents', [config.get('agent')]) if a['pod_name'])
        self.assertTrue(names)
        docs = list(yaml.safe_load_all((ROOT / 'infra/kubescape/collector/rbac.yaml').read_text()))
        self.assertEqual({d['kind'] for d in docs}, {'Role', 'RoleBinding'})
        for role in (d for d in docs if d['kind'] == 'Role'):
            for rule in role['rules']:
                self.assertIn(rule['verbs'], (['get'], ['list']))
                self.assertFalse(set(rule['resources']) & {'secrets', 'pods/exec', 'pods/attach'})
                if 'pods/log' in rule['resources']:
                    self.assertEqual(sorted(rule['resourceNames']), names)
        workload = list(yaml.safe_load_all((ROOT / 'infra/kubescape/collector/workload.yaml').read_text()))
        pvc = next(d for d in workload if d['kind'] == 'PersistentVolumeClaim')
        self.assertEqual(pvc['metadata']['annotations']['kustomize.toolkit.fluxcd.io/prune'], 'disabled')
        deployment = next(d for d in workload if d['kind'] == 'Deployment')
        spec = deployment['spec']['template']['spec']
        self.assertFalse(spec['automountServiceAccountToken'])
        projection = next(v['projected'] for v in spec['volumes'] if v['name'] == 'api')
        self.assertEqual(projection['sources'][0]['serviceAccountToken'],
                         {'expirationSeconds': 3600, 'path': 'token'})
        self.assertEqual(deployment['spec']['strategy']['type'], 'Recreate')
        self.assertEqual([v['persistentVolumeClaim']['claimName'] for v in spec['volumes']
                          if 'persistentVolumeClaim' in v], ['security-collector-state'])
        config = json.loads((ROOT / 'infra/kubescape/collector/collector.json').read_text())
        self.assertFalse(config['assessment_history_available'])
        gatus = yaml.safe_load((ROOT / 'infra/observability/gatus-helmrelease.yaml').read_text())
        checks = [e for e in gatus['spec']['values']['config']['endpoints']
                  if e['name'].startswith('security-collector-')]
        self.assertEqual(checks, [])
        source = yaml.safe_load((ROOT / "infra/kubescape/kustomization.yaml").read_text())
        self.assertNotIn("collector", source["resources"])
        self.assertNotIn("collector-retained-state.yaml", source["resources"])

    def test_source_layer_reproducibility(self):
        spec = importlib.util.spec_from_file_location('collector_image', ROOT / 'ops/security-collector/build-image.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as root:
            first, second = Path(root) / 'a.tar', Path(root) / 'b.tar'
            self.assertEqual(builder.layer(first), builder.layer(second))
            self.assertEqual(first.read_bytes(), second.read_bytes())
