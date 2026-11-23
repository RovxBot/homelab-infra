"""Two authenticated sources, additive state, attribution and staged enrollment."""
import copy
from dataclasses import asdict, replace
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security_collector.intake import Intake
from security_collector.model import Rejected, Workload, stamp
from security_collector.runtime import AGENT_IMAGE
from security_collector.service import Service
from security_collector.sources import APPS, CORE, resource
import test_security_collector as a
import test_security_collector_stage_b as b

ROOT = Path(__file__).resolve().parents[2]
NOW, W = a.NOW, a.W


class TwoNodes(b.FakeAPI):
    def __init__(self):
        super().__init__()
        self.other = copy.deepcopy(self.agent)
        self.other['metadata'].update(name='node-agent-second', uid='second-agent-uid')
        self.other['spec']['nodeName'] = 'metal3'
        self.other['status']['containerStatuses'][0]['containerID'] = 'containerd://second'
        self.objects[resource(CORE, 'kubescape', 'pods', 'node-agent-second')] = self.other
        self.logs_by_pod = {'node-agent-test': self.logs, 'node-agent-second': b''}
        self.failed = set()

    def get(self, path):
        if any(path == resource(CORE, 'kubescape', 'pods', name) for name in self.failed):
            raise Rejected('api_unavailable')
        return super().get(path)

    def raw(self, path):
        self.calls.append(path)
        name = path.split('/pods/')[1].split('/log')[0]
        if name in self.failed:
            raise Rejected('api_unavailable')
        return self.logs_by_pod[name]


class MultiSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path, self.api = Path(self.temp.name), TwoNodes()
        self.config = dict(workloads=[asdict(W)], history=str(self.path / 'unavailable'),
            database_lock=b.EXPECTED, assessment_history_available=False, agents=[
                dict(node='metal6', pod_name='node-agent-test', pod_uid='agent-pod-uid', daemonset_uid='agent-ds-uid'),
                dict(node='metal3', pod_name='node-agent-second', pod_uid='second-agent-uid', daemonset_uid='agent-ds-uid')])
        self.services = []
        self.addCleanup(lambda: [s.c.close() for s in self.services])
        self.s = self.create()

    def create(self):
        s = Service(self.config, self.path / 'state', self.api)
        self.services.append(s)
        return s

    def restart(self):
        self.s.c.close()
        self.services.remove(self.s)
        self.s = self.create()

    def test_two_independent_cursors_read_only_calls_and_private_attribution(self):
        status = self.s.tick(NOW)
        self.assertEqual((status['expected_agents'], status['healthy_agents']), (2, 2))
        self.assertTrue(status['runtime_read_ok'])
        self.assertEqual(status['covered_workloads'], 1)
        cursors = [self.s.intake.cursor('runtime/' + n) for n in ('metal6', 'metal3')]
        self.assertNotEqual(cursors[0]['agent'], cursors[1]['agent'])
        self.assertIsNone(cursors[1]['at'])
        proof = self.s.c.all()[0]['runtime_identity']
        self.assertEqual(proof['pod_uid'], 'pod-uid')
        self.assertEqual(proof['deployment_uid'], 'deployment-uid')
        self.assertIn('containerd://application', (self.path / 'state/previews.json').read_text())
        self.assertNotIn('SYNTHETIC_SECRET', (self.path / 'state/previews.json').read_text())
        self.assertNotIn('example-synthetic', json.dumps(self.s.response('/healthz')))
        for table in ('writes', 'delivery', 'api_calls'):
            self.assertEqual(self.s.c.db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)

    def test_failed_source_cannot_hide_behind_healthy_source(self):
        self.s.tick(NOW)
        old = self.s.intake.cursor('runtime/metal3')
        self.api.failed.add('node-agent-second')
        status = self.s.tick(NOW + timedelta(seconds=30))
        self.assertEqual(status['healthy_agents'], 1)
        self.assertFalse(status['runtime_read_ok'])
        self.assertEqual(status['covered_workloads'], 1)  # workload remains on healthy metal6
        self.assertEqual(self.s.intake.cursor('runtime/metal3'), old)
        self.assertNotEqual(self.s.intake.cursor('runtime/metal6')['checked'], old['checked'])
        self.restart()
        self.assertEqual(self.s.intake.source_status('runtime/metal3')['reason'], 'read_failed')
        self.assertTrue(self.s.intake.source_status('runtime/metal3')['gap'])

    def test_exporter_format_fixtures_ingest_independently_on_both_nodes(self):
        pod = copy.deepcopy(self.api.pod)
        pod['metadata'].update(name='metal3-replica', uid='metal3-replica-uid')
        pod['spec']['nodeName'] = 'metal3'
        pod['status']['containerStatuses'][0]['containerID'] = 'containerd://metal3-container'
        self.api.objects[resource(CORE, 'example', 'pods')]['items'].append(pod)
        self.api.dep['spec']['replicas'] = 2
        for key in ('replicas', 'readyReplicas', 'updatedReplicas', 'availableReplicas'):
            self.api.dep['status'][key] = 2
        event = json.loads(self.api.logs_by_pod['node-agent-test'].partition(b' ')[2])
        event['BaseRuntimeMetadata']['uniqueID'] = 'synthetic-node3-observation'
        event['RuntimeK8sDetails'].update(podName='metal3-replica', podUID='metal3-replica-uid',
                                         containerID='metal3-container')
        self.api.logs_by_pod['node-agent-second'] = b'2026-11-23T00:00:02Z ' + json.dumps(event).encode() + b'\n'
        self.s.tick(NOW)
        self.assertEqual(self.s.snapshot['healthy_agents'], 2)
        self.assertEqual(self.s.snapshot['attributed_detection_count'], 2)
        self.assertEqual(len(self.s.c.all()), 1)
        self.assertEqual(self.s.c.all()[0]['count'], 2)
        self.assertEqual(self.s.sources.coverage[0]['covered_replicas'], 2)
        self.s.tick(NOW + timedelta(seconds=30))
        self.assertEqual(self.s.c.all()[0]['count'], 2)
        self.assertEqual(self.s.snapshot['attributed_detection_count'], 2)

    def test_movement_and_mixed_replica_coverage(self):
        self.api.logs_by_pod['node-agent-test'] = b''
        self.s.tick(NOW)
        self.api.pod['spec']['nodeName'] = 'metal3'
        self.assertEqual(self.s.tick(NOW + timedelta(seconds=30))['covered_workloads'], 1)
        self.assertEqual(self.s.sources.coverage[0]['nodes'], ['metal3'])
        self.api.pod['spec']['nodeName'] = 'metal7'
        status = self.s.tick(NOW + timedelta(seconds=60))
        self.assertEqual((status['covered_workloads'], status['uncovered_workloads']), (0, 1))
        self.api.pod['spec']['nodeName'] = 'metal3'
        extra = copy.deepcopy(self.api.pod)
        extra['metadata'].update(name='second-replica', uid='second-replica-uid')
        extra['spec']['nodeName'] = 'metal6'
        self.api.objects[resource(CORE, 'example', 'pods')]['items'].append(extra)
        self.api.dep['spec']['replicas'] = 2
        for key in ('replicas', 'readyReplicas', 'updatedReplicas', 'availableReplicas'):
            self.api.dep['status'][key] = 2
        self.api.failed.add('node-agent-second')
        self.s.tick(NOW + timedelta(seconds=90))
        self.assertFalse(self.s.sources.coverage[0]['covered'])
        self.assertEqual(self.s.sources.coverage[0]['covered_replicas'], 1)

    def test_two_homepage_style_deployments_on_metal3(self):
        other = Workload(W.cluster, W.namespace, 'Deployment', 'assets', W.container)
        self.config['workloads'].append(asdict(other))
        deployment, pod = copy.deepcopy(self.api.dep), copy.deepcopy(self.api.pod)
        deployment['metadata'].update(name='assets', uid='assets-uid')
        pod['metadata'].update(name='assets-pod', uid='assets-pod-uid')
        pod['metadata']['ownerReferences'][0].update(name='assets-rs', uid='assets-rs-uid')
        pod['spec']['nodeName'] = self.api.pod['spec']['nodeName'] = 'metal3'
        self.api.objects[resource(APPS, 'example', 'deployments', 'assets')] = deployment
        self.api.objects[resource(APPS, 'example', 'replicasets', 'assets-rs')] = dict(metadata=dict(
            uid='assets-rs-uid', ownerReferences=[dict(kind='Deployment', uid='assets-uid', controller=True)]))
        self.api.objects[resource(CORE, 'example', 'pods')]['items'].append(pod)
        self.api.logs_by_pod['node-agent-test'] = b''
        self.restart()
        self.assertEqual(self.s.tick(NOW)['covered_workloads'], 2)
        self.api.failed.add('node-agent-second')
        self.assertEqual(self.s.tick(NOW + timedelta(seconds=30))['covered_workloads'], 0)

    def test_missing_or_changed_running_digest_is_explicit(self):
        self.api.logs_by_pod['node-agent-test'] = b''
        self.s.tick(NOW)
        state = self.api.pod['status']['containerStatuses'][0]
        state['imageID'] = 'not-an-immutable-image'
        self.assertEqual(self.s.tick(NOW + timedelta(seconds=30))['covered_workloads'], 0)
        state['imageID'] = 'registry.invalid/example@sha256:' + 'b' * 64
        self.assertEqual(self.s.tick(NOW + timedelta(seconds=60))['covered_workloads'], 1)
        self.assertEqual(self.s.sources.coverage[0]['digest'], 'sha256:' + 'b' * 64)

    def test_payload_identity_conflicts_fail_closed(self):
        original = self.api.logs_by_pod['node-agent-test']
        changes = [dict(containerID='wrong'), dict(podUID='wrong'), dict(workloadUID='wrong'),
                   dict(workloadName='forged'), dict(nodeName='metal3'), dict(imageDigest='sha256:' + 'b' * 64)]
        for index, fields in enumerate(changes):
            event = json.loads(original.partition(b' ')[2])
            event['RuntimeK8sDetails'].update(fields)
            self.api.logs_by_pod['node-agent-test'] = b'2026-11-23T00:00:01Z ' + json.dumps(event).encode() + b'\n'
            status = self.s.tick(NOW + timedelta(seconds=index * 30))
            self.assertEqual(status['healthy_agents'], 1)
            self.assertEqual(self.s.c.all(), [])
        self.assertIsNone(self.s.intake.cursor('runtime/metal6'))

    def test_event_predating_running_container_is_rejected(self):
        self.api.pod['status']['containerStatuses'][0]['state']['running']['startedAt'] = '2026-11-23T00:00:00Z'
        self.s.tick(NOW)
        self.assertEqual(self.s.c.all(), [])
        self.assertFalse(self.s.snapshot['runtime_read_ok'])

    def test_agent_identity_and_mid_read_changes_are_rejected(self):
        self.api.other['status']['containerStatuses'][0]['imageID'] = 'sha256:' + 'b' * 64
        status = self.s.tick(NOW)
        self.assertEqual(status['invalid_source_identities'], 1)
        self.assertEqual(len(self.s.c.all()), 1)
        self.assertIsNone(self.s.intake.cursor('runtime/metal3'))
        self.api.other['status']['containerStatuses'][0]['imageID'] = AGENT_IMAGE
        calls = []
        def change(path):
            if path.endswith('/pods/node-agent-second'):
                calls.append(path)
                if len(calls) == 1:
                    self.api.other['metadata']['uid'] = 'unexpected-replacement'
        self.api.hook = change
        self.s.tick(NOW + timedelta(seconds=30))
        self.assertEqual(self.s.snapshot['invalid_source_identities'], 1)
        self.assertIsNone(self.s.intake.cursor('runtime/metal3'))

    def test_restart_audit_is_bounded_and_survives_service_restart(self):
        self.s.tick(NOW)
        state = self.api.other['status']['containerStatuses'][0]
        for number in range(1, 5):
            state.update(containerID='containerd://restart-' + str(number), restartCount=number)
            self.s.tick(NOW + timedelta(seconds=30 * number))
        source = self.s.intake.source_status('runtime/metal3')
        self.assertEqual(source['reason'], 'source_refresh_limited')
        self.assertEqual(self.s.snapshot['healthy_agents'], 1)
        self.assertEqual(self.s.c.db.execute('SELECT count(*) FROM runtime_identities WHERE source=?',
                         ('runtime/metal3',)).fetchone()[0], 4)
        self.restart()
        self.assertEqual(self.s.intake.source_status('runtime/metal3'), source)
        self.assertTrue(self.s.intake.cursor('runtime/metal3')['gap'])

    def test_unverified_replacement_requires_reviewed_pin_refresh(self):
        self.s.tick(NOW)
        self.api.other['metadata']['uid'] = 'reviewed-replacement'
        self.s.tick(NOW + timedelta(seconds=30))
        self.assertEqual(self.s.snapshot['healthy_agents'], 1)
        old = self.s.intake.cursor('runtime/metal3')
        self.config['agents'][1]['pod_uid'] = 'reviewed-replacement'
        self.restart()
        self.s.tick(NOW + timedelta(seconds=60))
        self.assertEqual(self.s.snapshot['healthy_agents'], 2)
        self.assertNotEqual(self.s.intake.cursor('runtime/metal3')['agent'], old['agent'])
        self.assertTrue(self.s.intake.cursor('runtime/metal3')['gap'])

    def test_pending_enrollment_does_not_broaden_api_or_hide_other_source(self):
        self.config['agents'][1].update(pod_name=None, pod_uid=None)
        self.restart()
        self.s.tick(NOW)
        self.assertEqual(self.s.snapshot['healthy_agents'], 1)
        self.assertEqual(self.s.intake.source_status('runtime/metal3')['reason'], 'source_enrollment_pending')
        self.assertFalse(any(p.endswith('/kubescape/pods') for p in self.api.calls))

    def test_additive_upgrade_preserves_legacy_state_and_rollback(self):
        self.s.tick(NOW)
        with self.s.c.db:
            for row in self.s.c.db.execute('SELECT key,data FROM cursors').fetchall():
                cursor = json.loads(row['data'])
                cursor.pop('event_count', None)
                cursor.pop('attributed_detections', None)
                self.s.c.db.execute('UPDATE cursors SET data=? WHERE key=?', (json.dumps(cursor), row['key']))
        retained = {name: [tuple(r) for r in self.s.c.db.execute('SELECT * FROM ' + name)]
                    for name in ('cursors', 'source_observations', 'incidents', 'receipts', 'reader_status', 'pending')}
        with self.s.c.db:
            for table in ('runtime_source_status', 'runtime_identities', 'runtime_evidence'):
                self.s.c.db.execute('DROP TABLE ' + table)
        self.restart()  # legacy DB has none of the additive tables
        for name, rows in retained.items():
            self.assertEqual([tuple(r) for r in self.s.c.db.execute('SELECT * FROM ' + name)], rows)
        self.s.tick(NOW + timedelta(seconds=30))
        self.assertEqual(self.s.c.all()[0]['count'], 1)
        before = self.s.intake.cursor('runtime/metal3')
        self.config['agent'] = self.config.pop('agents')[0]
        self.restart()
        self.s.tick(NOW + timedelta(seconds=60))
        self.assertEqual(self.s.intake.cursor('runtime/metal3'), before)
        self.assertEqual(len(self.s.c.all()), 1)

    def test_receipts_do_not_expire_and_replays_do_not_inflate_source_counts(self):
        self.s.tick(NOW)
        self.s.tick(NOW + timedelta(seconds=30))
        self.assertEqual(self.s.snapshot['event_count'], 1)
        self.assertEqual(self.s.snapshot['attributed_detection_count'], 1)
        self.s.c.refresh(NOW + timedelta(days=8))
        self.assertEqual(self.s.c.db.execute('SELECT count(*) FROM receipts').fetchone()[0], 1)

    def test_cross_source_replay_keeps_one_incident_and_receipt(self):
        self.s.tick(NOW)
        row = self.s.c.db.execute('SELECT data FROM runtime_evidence WHERE attributed=1').fetchone()
        proof = json.loads(row[0])
        event = replace(a.runtime(), runtime_identity=proof['identity'])
        # The authenticated reader is tested above; here replay crosses input queues.
        self.s.intake.enqueue([event], NOW + timedelta(seconds=30), cursor_key='runtime/metal3',
                              cursor=self.s.intake.cursor('runtime/metal3'))
        self.s.intake.drain(NOW + timedelta(seconds=30))
        self.assertEqual(len(self.s.c.all()), 1)
        self.assertEqual(self.s.c.all()[0]['count'], 1)

    def test_duplicate_sources_and_pod_pins_are_rejected(self):
        for field, value in (('node', 'metal6'), ('pod_name', 'node-agent-test')):
            config = copy.deepcopy(self.config)
            config['agents'][1][field] = value
            if field == 'pod_name':
                config['agents'][1]['pod_uid'] = 'agent-pod-uid'
            with self.assertRaises(Rejected):
                # Isolate the rejected writer's state path from the running service.
                Service(config, self.path / ('rejected-' + field), self.api)


class EnrollmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('enroll_agents', ROOT / 'ops/security-collector/enroll-agents.py')
        cls.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.helper)

    def setUp(self):
        self.api = TwoNodes()
        self.api.objects[resource(CORE, 'kubescape', 'pods')] = dict(items=[self.api.agent, self.api.other])
        self.config = dict(workloads=[asdict(W)], agents=[
            dict(node=node, pod_name=None, pod_uid=None, daemonset_uid='agent-ds-uid')
            for node in ('metal6', 'metal3')])

    def test_read_only_enrollment_produces_only_exact_names(self):
        config, proof = self.helper.reviewed_pins(self.config, self.api)
        self.assertEqual([a['pod_name'] for a in config['agents']], ['node-agent-test', 'node-agent-second'])
        self.assertTrue(all(a['image'] == AGENT_IMAGE for a in proof))
        roles = list(yaml.safe_load_all((ROOT / 'infra/kubescape/collector/rbac.yaml').read_text()))
        scoped = self.helper.scoped_roles(config, roles)
        for role in (d for d in scoped if d['kind'] == 'Role'):
            for rule in role['rules']:
                if 'pods/log' in rule['resources']:
                    self.assertEqual(rule['resourceNames'], ['node-agent-second', 'node-agent-test'])
                self.assertFalse(set(rule['resources']) & {'secrets', 'pods/exec', 'nodes/proxy'})
        self.assertFalse(any('/log' in path for path in self.api.calls))
        self.assertIsNone(self.config['agents'][0]['pod_name'])

    def test_wrong_uid_image_or_multiple_candidates_cannot_create_a_pin(self):
        for change in ('uid', 'image', 'ambiguous'):
            with self.subTest(change=change):
                self.setUp()
                if change == 'uid':
                    self.config['agents'][1]['daemonset_uid'] = 'wrong-ds-uid'
                elif change == 'image':
                    self.api.other['spec']['containers'][0]['image'] = 'unreviewed:latest'
                else:
                    self.api.objects[resource(CORE, 'kubescape', 'pods')]['items'].append(copy.deepcopy(self.api.other))
                with self.assertRaises(Rejected):
                    self.helper.reviewed_pins(self.config, self.api)

    def test_acceptance_requires_explicit_composition_and_workload_enrollment(self):
        production = yaml.safe_load((ROOT / 'infra/kubescape/kustomization.yaml').read_text())
        enabled = any('ops/security-collector/acceptance' in str(r) for r in production['resources'])
        config = json.loads((ROOT / 'infra/kubescape/collector/collector.json').read_text())
        enrolled = [w for w in config['workloads'] if w['namespace'] == 'kubescape-runtime-test']
        acceptance = dict(cluster='home', namespace='kubescape-runtime-test', kind='Deployment',
                          name='runtime-acceptance', container='test')
        self.assertEqual(enrolled, [acceptance] if enabled else [])
        workload = list(yaml.safe_load_all((ROOT / 'ops/security-collector/acceptance/base/workload.yaml').read_text()))[-1]
        spec = workload['spec']['template']['spec']
        self.assertEqual(spec['nodeSelector']['kubernetes.io/hostname'], 'metal3')
        self.assertNotIn('/dev/shm', spec['containers'][0]['args'][0])
        self.assertFalse(spec['automountServiceAccountToken'])
        self.assertNotIn('persistentVolumeClaim', json.dumps(spec))


if __name__ == '__main__':
    unittest.main()
