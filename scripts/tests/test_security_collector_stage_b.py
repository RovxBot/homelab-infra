"""Source -> reader -> validation -> durable state -> safe preview, no external writes."""
import copy
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
import gzip
import importlib.util
import json
from pathlib import Path
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security_collector.assessment import AssessmentReader, History
from security_collector.engine import Collector
from security_collector.intake import Intake
from security_collector.issues import Delivery, Publication, render
from security_collector.model import Rejected, stamp
from security_collector.pipeline import Pipeline
from security_collector.runtime import AGENT_IMAGE, RuntimeLogReader
from security_collector.sources import APPS, CORE, STORAGE, InventoryReader, KubernetesAPI, NoRedirect, resource
from test_security_collector import FIXTURES, NOW, W, MockGitHub, runtime

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('stage_b_runner_contract', ROOT / 'infra/kubescape/recurring/runner.py')
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)
EXPECTED = json.loads((ROOT / 'infra/kubescape/grype-database.json').read_text())
RUN = 'vulnerability-20261123000000-abcdef'
JOB = 'recurring-' + 'c' * 32
REF = 'registry.invalid/example@sha256:' + 'a' * 64


def save(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if name.endswith('.gz'):
        with gzip.open(path, 'wt') as out:
            json.dump(value, out)
    else:
        path.write_text(json.dumps(value))


class FakeAPI:
    """Only GET/raw exist; attempts to write fail the test immediately."""
    def __init__(self):
        self.calls, self.hook, self.error = [], None, False
        self.objects = {}
        self.dep = dict(metadata=dict(name='example', namespace='example', uid='deployment-uid',
                        generation=1, creationTimestamp='2026-11-22T00:00:00Z'),
                        spec=dict(replicas=1), status=dict(observedGeneration=1, replicas=1,
                        updatedReplicas=1, readyReplicas=1, availableReplicas=1))
        self.pod = dict(metadata=dict(name='example-synthetic', namespace='example', uid='pod-uid',
                        ownerReferences=[dict(name='example-rs', kind='ReplicaSet', uid='rs-uid', controller=True)]),
                        spec=dict(nodeName='metal6'), status=dict(phase='Running', containerStatuses=[
                        dict(name='app', ready=True, imageID=REF, containerID='containerd://application',
                             state=dict(running=dict(startedAt='2026-11-22T00:00:00Z')))]))
        self.agent = dict(metadata=dict(name='node-agent-test', namespace='kubescape', uid='agent-pod-uid',
                        ownerReferences=[dict(kind='DaemonSet', uid='agent-ds-uid', controller=True)]),
                        spec=dict(nodeName='metal6', serviceAccountName='node-agent',
                                  containers=[dict(name='node-agent', image=AGENT_IMAGE)]),
                        status=dict(phase='Running', containerStatuses=[dict(name='node-agent', ready=True,
                        imageID=AGENT_IMAGE, containerID='containerd://agent', restartCount=0,
                        state=dict(running={}))]))
        self.objects[resource(APPS, 'example', 'deployments', 'example')] = self.dep
        self.objects[resource(CORE, 'example', 'pods')] = dict(items=[self.pod])
        self.objects[resource(APPS, 'example', 'replicasets', 'example-rs')] = dict(metadata=dict(uid='rs-uid',
            ownerReferences=[dict(kind='Deployment', uid='deployment-uid', controller=True)]))
        self.objects[resource(APPS, 'kubescape', 'daemonsets', 'node-agent')] = dict(metadata=dict(uid='agent-ds-uid'))
        self.objects[resource(CORE, 'kubescape', 'pods', 'node-agent-test')] = self.agent
        event = json.loads((FIXTURES / 'r1000.json').read_text())
        self.logs = b'2026-11-23T00:00:01Z ' + json.dumps(event).encode() + b'\n'

    def get(self, path):
        self.calls.append(path)
        if self.error:
            raise Rejected('api_unavailable')
        result = copy.deepcopy(self.objects[path])
        if self.hook:
            self.hook(path)
        return result

    def raw(self, path):
        self.calls.append(path)
        if self.error:
            raise Rejected('api_unavailable')
        return self.logs


class StageBTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.c = Collector(self.root / 'collector.sqlite')
        self.addCleanup(lambda: self.c.close())
        self.i, self.api = Intake(self.c), FakeAPI()
        # Independent inventory was observed before the retained scan request.
        # Current coverage alone cannot prove its historical generation.
        self.c.deployment(InventoryReader(self.api).read(W, NOW - timedelta(minutes=10)).deployment)
        self.history = self.root / 'history'
        self.history.mkdir()
        self.make_assessment()
        self.runtime = RuntimeLogReader(self.api, self.i, [W], pod_name='node-agent-test',
                                       pod_uid='agent-pod-uid', daemonset_uid='agent-ds-uid')
        self.reader = AssessmentReader(self.api, self.i, self.history, EXPECTED,
                                       [dict(identity='example/Deployment/example/app')])

    def make_assessment(self, *, clean=False, created_at=None):
        bundle = json.loads((FIXTURES / 'assessment.json').read_text())
        db = EXPECTED['metadata']
        bundle['report']['spec']['metadata']['tool']['databaseVersion'] = db['checksum'].removeprefix('sha256:')
        bundle['report']['spec']['payload']['descriptor']['db'].update(built=db['built'], schemaVersion=db['schemaVersion'])
        if clean:
            bundle['report']['spec']['payload']['matches'] = []
            bundle['report']['spec']['metadata']['report']['createdAt'] = '2026-11-23T00:01:00Z'
        if created_at is not None:
            bundle['report']['spec']['metadata']['report']['createdAt'] = created_at
        for key, name, namespace, kind in (('report', 'synthetic-report', 'kubescape', 'vulnerabilitymanifests'),
                    ('sbom', 'synthetic-sbom', 'kubescape', 'sbomsyfts'),
                    ('summary', 'deployment-example-app', 'example', 'vulnerabilitymanifestsummaries')):
            bundle[key]['metadata'].update(name=name, namespace=namespace, uid=key+'-uid', resourceVersion='1')
            self.api.objects[resource(STORAGE, namespace, kind, name)] = bundle[key]
        # Use the active runner's own contracts to construct history and coverage.
        row = dict(namespace='example', kind='Deployment', workload='example', workloadUID='deployment-uid',
                   generation=1, container='app', containerType='containers', runningDigest=REF,
                   ready=True, podUIDs=['pod-uid'])
        record = RUNNER.check_report(bundle['report'], bundle['sbom'], row, EXPECTED, '2026-11-22T23:59:00Z')
        record.update(jobID=JOB, workloadUID='deployment-uid', imageDigest=REF, container='app', durationSeconds=60)
        record['lastAttempt'] = dict(state='healthy', checkedAt='2026-11-23T00:02:00Z')
        result = dict(schemaVersion=1, runID=RUN, mode='vulnerability', state='healthy', reasons=[],
                      startedAt='2026-11-22T23:58:00Z', finishedAt='2026-11-23T00:03:00Z', assessments=[record],
                      targets=[dict(identity='example/Deployment/example/app', state='healthy', reasons=[])])
        records = {RUNNER.row_key(row): record}
        config = dict(targets=[dict(identity='example/Deployment/example/app')], maximumScanAgeHours=72,
                      databaseChecksum=db['checksum'])
        coverage = RUNNER.coverage([row], records, config, NOW)
        save(self.history, 'records.json', records)
        save(self.history, 'latest-vulnerability.json', result)
        save(self.history, 'coverage.json', coverage)
        save(self.history, RUN+'/result.json.gz', result)
        save(self.history, RUN+'/request-'+JOB+'.json.gz', dict(jobID=JOB, imageTag=REF, imageHash=REF,
             wlid='wlid://cluster-home/namespace-example/deployment-example', containerName='app',
             args=dict(platform='linux/amd64', forceRescan=True)))
        save(self.history, RUN+'/terminal-'+JOB+'.json', dict(state='succeeded', phase='completed',
             startedAt='2026-11-22T23:59:00Z', finishedAt='2026-11-23T00:01:00Z'))
        source = 'https://grype.anchore.io/db?checksum='+db['checksum']
        save(self.history, RUN+'/cache-audit.json.gz', dict(checkedAt='2026-11-22T23:58:30Z',
             podUID='engine-uid', sqlite=dict(metadataReadable=True, sha256=EXPECTED['database']['sha256'],
             built=db['built'], schemaVersion=db['schemaVersion'], **{'import': dict(source=source)}),
             checksum=dict(valid=True, built=db['built'], schemaVersion=db['schemaVersion'], **{'from': source})))
        save(self.history, RUN+'/reports-'+JOB+'.json.gz', dict(vulnerability=bundle['report'], sbom=bundle['sbom']))
        save(self.history, RUN+'/summary-'+JOB+'.json.gz', bundle['summary'])
        return bundle

    def mutate(self, name, callback):
        value = History(self.history).read(name)
        callback(value)
        save(self.history, name, value)

    def restart(self):
        self.c.close()
        self.c = Collector(self.root / 'collector.sqlite')
        self.i = Intake(self.c)
        self.runtime.intake, self.reader.intake = self.i, self.i

    def test_real_format_end_to_end_preview_and_disabled_writes(self):
        self.assertEqual(self.runtime.poll(NOW), 'unknown')  # Explicit initial boundary gap.
        self.assertEqual(self.reader.poll(W, NOW), 'confirmed')
        self.assertEqual(len(self.c.all()), 2)
        self.restart()
        github = MockGitHub()
        publications = {W.key: Publication(W, (2138,))}
        self.assertEqual(Delivery(self.c, github, publications).sync(NOW), 'disabled')
        previews = [render(incident, publications[W.key]) for incident in self.c.all()]
        for value in ('SYNTHETIC_SECRET', 'UNTRUSTED_PAYLOAD', 'private-package', '/private/', 'registry.invalid'):
            self.assertNotIn(value, json.dumps(previews) + json.dumps(self.c.all()))
            self.assertNotIn(value.encode(), (self.root / 'collector.sqlite').read_bytes())
        self.assertEqual(github.calls, [])
        self.assertTrue(any('CVE-2026-10001' in body for _, body in previews))

    def test_pipeline_is_disabled_until_explicitly_enabled(self):
        self.api.calls.clear()
        pipeline = Pipeline(self.i, self.runtime, [(self.reader, W)], publications={W.key: Publication(W)})
        self.assertEqual(pipeline.tick(NOW)['state'], 'disabled')
        self.assertEqual(self.api.calls, [])
        pipeline.enabled = True
        result = pipeline.tick(NOW)
        self.assertEqual(len(result['previews']), 2)
        self.assertEqual(len(result['health']), 2)

    def test_restart_after_queue_commit_before_drain(self):
        with patch.object(self.i, 'drain', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                self.runtime.poll(NOW)
        self.assertIsNotNone(self.i.cursor(self.runtime.key))
        self.assertEqual(self.c.all(), [])
        self.restart()
        self.i.drain(NOW)
        self.runtime.poll(NOW + timedelta(seconds=15))
        self.assertEqual(self.c.all()[0]['count'], 1)
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM pending').fetchone()[0], 0)

    def test_duplicate_across_restarts(self):
        self.runtime.poll(NOW)
        self.reader.poll(W, NOW)
        self.restart()
        self.runtime.poll(NOW + timedelta(seconds=15))
        self.reader.poll(W, NOW + timedelta(seconds=15))
        self.assertEqual([r['count'] for r in self.c.all()], [1, 1])

    def test_restart_rotation_and_disconnection_gaps(self):
        for index, (mode, reason) in enumerate((('restart', 'agent_restarted'), ('rotation', 'overlap_missing'),
                                               ('disconnect', 'disconnected'))):
            with self.subTest(mode=mode):
                base = NOW + timedelta(minutes=index * 15)
                self.runtime.poll(base)
                at = base + timedelta(minutes=6)
                if mode == 'restart':
                    self.api.agent['status']['containerStatuses'][0]['restartCount'] += 1
                elif mode == 'rotation':
                    at = base + timedelta(minutes=1)
                    self.api.logs = stamp(at).encode() + b' ordinary message\n'
                self.assertEqual(self.runtime.poll(at), 'unknown')
                status = self.i.health(at)[0]
                self.assertEqual(status['reason'], reason)
                self.assertGreater(status['gap_count'], 0)
                self.restart()
                # Reset source cursor only inside this isolated scenario fixture.
                with self.c.db:
                    self.c.db.execute('DELETE FROM cursors')
                    self.c.db.execute('DELETE FROM polls')

    def test_quiet_source_heartbeat_expires_without_resolution(self):
        self.runtime.poll(NOW)
        health = self.i.health(NOW + timedelta(minutes=6))
        self.assertEqual(health[0]['reason'], 'heartbeat_expired')
        self.assertEqual(self.c.all()[0]['status'], 'open')
        self.assertEqual(self.c.all()[0]['freshness'], 'stale')

    def test_authenticated_source_identity_rejected(self):
        for mutate in (lambda: self.api.agent['spec'].update(serviceAccountName='forged'),
                       lambda: self.api.agent['spec'].update(nodeName='metal1'),
                       lambda: self.api.agent['spec']['containers'][0].update(image='wrong'),
                       lambda: self.api.agent['metadata'].update(uid='forged'),
                       lambda: self.api.agent['metadata']['ownerReferences'][0].update(uid='forged')):
            old = copy.deepcopy(self.api.agent)
            mutate()
            self.assertEqual(self.runtime.poll(NOW), 'failed')
            self.assertEqual(self.c.all(), [])
            self.api.agent.clear()
            self.api.agent.update(old)
            with self.c.db:
                self.c.db.execute('DELETE FROM polls')

    def test_malformed_input_has_static_failure_and_no_cursor(self):
        for payload in (b'{bad', b'{"RuleID":"R1000","RuleID":"R1000"}', b'{"RuleID":NaN}'):
            self.api.logs = b'2026-11-23T00:00:01Z '+payload+b'\n'
            self.assertEqual(self.runtime.poll(NOW), 'failed')
            self.assertIsNone(self.i.cursor(self.runtime.key))
            self.assertEqual(self.c.all(), [])
            with self.c.db:
                self.c.db.execute('DELETE FROM polls')

    def test_source_outage_and_persisted_poll_rate_limit(self):
        self.api.error = True
        self.assertEqual(self.runtime.poll(NOW), 'failed')
        self.restart()
        with self.assertRaisesRegex(Rejected, 'poll_rate_limit'):
            self.runtime.poll(NOW)
        self.assertEqual(self.i.health(NOW)[0]['reason'], 'read_failed')

    def test_spool_saturation_does_not_advance_cursor(self):
        self.i.max_pending = 0
        self.assertEqual(self.runtime.poll(NOW), 'failed')
        self.assertIsNone(self.i.cursor(self.runtime.key))
        self.assertEqual(self.i.health(NOW)[0]['reason'], 'saturated')
        self.i.max_pending = 1000
        self.runtime.poll(NOW + timedelta(seconds=15))
        self.assertEqual(self.c.all()[0]['count'], 1)

    def test_collector_backpressure_retains_pending_and_cursor(self):
        self.c.events_per_hour = 0
        self.assertEqual(self.runtime.poll(NOW), 'failed')
        self.assertEqual(self.i.health(NOW)[0]['reason'], 'saturated')
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM pending').fetchone()[0], 1)
        self.restart()
        self.i.drain(NOW)
        self.assertEqual(self.c.all()[0]['count'], 1)

    def test_physical_storage_saturation_rolls_back(self):
        self.c.close()
        self.c = Collector(self.root / 'collector.sqlite', storage_bytes=128 * 1024)
        self.i = Intake(self.c)
        self.reader.intake = self.i
        # A valid large normalized vulnerability snapshot exceeds the page quota.
        event = self.reader.qualify(W, NOW)
        large = replace(event, identities=tuple(f'CVE-2026-{10000+i}' for i in range(5000)))
        with self.assertRaises(sqlite3.DatabaseError):
            self.i.enqueue([large], NOW, cursor_key='test', cursor=dict(at=stamp(NOW)))
        self.assertIsNone(self.i.cursor('test'))
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM pending').fetchone()[0], 0)

    def test_failed_empty_and_missing_preserve_qualified_findings(self):
        self.assertEqual(self.reader.poll(W, NOW), 'confirmed')
        before = self.c.all()[0]
        self.make_assessment(clean=True)
        self.mutate('latest-vulnerability.json', lambda v: v.update(state='failed'))
        self.assertEqual(self.reader.poll(W, NOW + timedelta(seconds=15)), 'failed')
        after = self.c.all()[0]
        self.assertEqual(after['identities'], before['identities'])
        self.assertEqual(after['status'], 'open')
        (self.history / 'records.json').unlink()
        self.assertEqual(self.reader.poll(W, NOW + timedelta(seconds=30)), 'unknown')
        self.assertEqual(self.c.all()[0]['identities'], before['identities'])

    def test_crashed_clean_queue_requires_current_requalification(self):
        self.reader.poll(W, NOW)
        before = self.c.all()[0]['identities']
        self.make_assessment(clean=True)
        with patch.object(self.i, 'drain', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                self.reader.poll(W, NOW + timedelta(seconds=15))
        self.restart()
        self.mutate('latest-vulnerability.json', lambda v: v.update(state='failed'))
        self.i.drain(NOW + timedelta(seconds=30))
        self.reader.poll(W, NOW + timedelta(seconds=30))
        self.assertEqual(self.c.all()[0]['status'], 'open')
        self.assertEqual(self.c.all()[0]['identities'], before)
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM pending').fetchone()[0], 1)

    def test_crashed_assessment_requalifies_after_restart(self):
        with patch.object(self.i, 'drain', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                self.reader.poll(W, NOW)
        self.restart()
        self.assertEqual(self.reader.poll(W, NOW + timedelta(seconds=15)), 'confirmed')
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM pending').fetchone()[0], 0)
        self.assertTrue(self.c.all()[0]['findings_qualified'])

    def test_crashed_clean_queue_rejects_same_identity_positive_requalification(self):
        self.reader.poll(W, NOW)
        before = self.c.all()[0]
        self.i.enqueue([], NOW, cursor_key='retained', cursor=dict(at=stamp(NOW)))
        self.make_assessment(clean=True)
        clean = self.reader.qualify(W, NOW)
        with patch.object(self.i, 'drain', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                self.reader.poll(W, NOW + timedelta(seconds=15))
        pending = tuple(self.c.db.execute('SELECT * FROM pending').fetchone())
        receipts = [tuple(r) for r in self.c.db.execute('SELECT * FROM receipts ORDER BY id')]
        self.restart()
        self.make_assessment(created_at=clean.observed_at)
        positive = self.reader.qualify(W, NOW + timedelta(seconds=30))
        self.assertEqual(clean.event_id, positive.event_id)
        self.assertTrue(clean.clean)
        self.assertTrue(positive.identities)
        self.assertEqual(self.reader.poll(W, NOW + timedelta(seconds=30)), 'unknown')
        after = self.c.get(clean.incident_key)
        self.assertEqual((after['status'], after['resolution']), ('open', None))
        self.assertEqual(after['identities'], before['identities'])
        self.assertEqual(after['count'], before['count'])
        self.assertEqual(self.i.health(NOW + timedelta(seconds=30))[0]['reason'], 'assessment_identity_conflict')
        self.assertEqual(tuple(self.c.db.execute('SELECT * FROM pending').fetchone()), pending)
        self.assertEqual([tuple(r) for r in self.c.db.execute('SELECT * FROM receipts ORDER BY id')], receipts)
        self.assertEqual(self.i.cursor('retained'), dict(at=stamp(NOW)))

    def test_pending_and_drain_reject_changed_observation_fields(self):
        event = self.reader.qualify(W, NOW)
        self.i.enqueue([event], NOW, cursor_key='retained', cursor=dict(at=stamp(NOW)))
        pending = tuple(self.c.db.execute('SELECT * FROM pending').fetchone())
        with self.assertRaisesRegex(Rejected, '^assessment_requalification_invalid$'):
            self.i.drain(NOW, requalified=(event.event_id,))
        for changes in (dict(severity='High'), dict(remediation='no_known_fix'),
                        dict(generation=2), dict(workload_uid='replacement-uid'),
                        dict(valid_until=stamp(NOW + timedelta(hours=1)))):
            with self.subTest(changes=changes):
                changed = replace(event, **changes)
                self.assertNotEqual(event, changed)
                with self.assertRaisesRegex(Rejected, '^assessment_identity_conflict$'):
                    self.i.enqueue([changed], NOW, cursor_key='retained', cursor=dict(at='changed'))
                with self.assertRaisesRegex(Rejected, '^assessment_identity_conflict$'):
                    self.i.drain(NOW, requalified=(changed,))
                self.assertEqual(tuple(self.c.db.execute('SELECT * FROM pending').fetchone()), pending)
                self.assertEqual(self.i.cursor('retained'), dict(at=stamp(NOW)))
                self.assertEqual(self.c.db.execute('SELECT count(*) FROM receipts').fetchone()[0], 0)
                self.assertEqual(self.c.all(), [])

    def test_crashed_clean_queue_accepts_identical_requalification(self):
        self.reader.poll(W, NOW)
        self.make_assessment(clean=True)
        clean = self.reader.qualify(W, NOW)
        with patch.object(self.i, 'drain', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                self.reader.poll(W, NOW + timedelta(seconds=15))
        self.restart()
        at = NOW + timedelta(seconds=30)
        self.assertEqual(self.reader.qualify(W, at), clean)
        self.assertEqual(self.reader.poll(W, at), 'confirmed')
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM pending').fetchone()[0], 0)
        incident = self.c.get(clean.incident_key)
        self.assertEqual((incident['status'], incident['resolution']), ('resolved', 'assessment'))
        self.assertEqual(incident['count'], 2)
        self.restart()
        self.assertEqual(self.reader.poll(W, NOW + timedelta(seconds=45)), 'confirmed')
        self.assertEqual(self.c.get(clean.incident_key), incident)

    def test_receipt_rejects_same_identity_mutation_after_restart(self):
        self.reader.poll(W, NOW)
        self.make_assessment(clean=True)
        clean = self.reader.qualify(W, NOW)
        self.assertEqual(self.reader.poll(W, NOW + timedelta(seconds=15)), 'confirmed')
        self.assertEqual(self.c.get(clean.incident_key)['status'], 'resolved')
        receipts = [tuple(r) for r in self.c.db.execute('SELECT * FROM receipts ORDER BY id')]
        self.restart()
        self.make_assessment(created_at=clean.observed_at)
        at = NOW + timedelta(seconds=30)
        positive = self.reader.qualify(W, at)
        self.assertEqual(clean.event_id, positive.event_id)
        with self.assertRaisesRegex(Rejected, '^assessment_identity_conflict$'):
            self.c.ingest(positive, at)
        self.assertEqual(self.reader.poll(W, at), 'unknown')
        self.assertEqual(self.i.health(at)[0]['reason'], 'assessment_identity_conflict')
        incident = self.c.get(clean.incident_key)
        self.assertEqual((incident['status'], incident['resolution']), ('open', None))
        self.assertEqual(incident['count'], 2)
        self.assertEqual([tuple(r) for r in self.c.db.execute('SELECT * FROM receipts ORDER BY id')], receipts)
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM pending').fetchone()[0], 0)

    def test_restart_after_receipt_commit_before_pending_delete(self):
        ingest = self.c.ingest

        def crash(event, now):
            ingest(event, now)
            raise SystemExit

        with patch.object(self.c, 'ingest', side_effect=crash):
            with self.assertRaises(SystemExit):
                self.reader.poll(W, NOW)
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM pending').fetchone()[0], 1)
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM receipts').fetchone()[0], 1)
        self.restart()
        self.assertEqual(self.reader.poll(W, NOW + timedelta(seconds=15)), 'confirmed')
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM pending').fetchone()[0], 0)
        self.assertEqual(self.c.all()[0]['count'], 1)

    def test_legacy_receipt_without_observation_hash_fails_closed(self):
        event = self.reader.qualify(W, NOW)
        path = self.root / 'legacy.sqlite'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE receipts (id TEXT PRIMARY KEY, received TEXT NOT NULL)')
            db.execute('INSERT INTO receipts VALUES (?,?)', (event.event_id, stamp(NOW)))
        collector = Collector(path)
        try:
            intake = Intake(collector)
            with self.assertRaisesRegex(Rejected, '^assessment_identity_conflict$'):
                intake.enqueue([event], NOW, cursor_key='test', cursor=dict(at=stamp(NOW)))
            with self.assertRaisesRegex(Rejected, '^assessment_identity_conflict$'):
                collector.ingest(event, NOW)
            self.assertIsNone(intake.cursor('test'))
            self.assertEqual(collector.db.execute('SELECT count(*) FROM receipts').fetchone()[0], 1)
            self.assertEqual(collector.db.execute('SELECT count(*) FROM pending').fetchone()[0], 0)
            self.assertEqual(collector.all(), [])
        finally:
            collector.close()

    def test_changed_digest_and_uid_cannot_inherit_clean(self):
        for change in ('digest', 'uid', 'generation'):
            with self.subTest(change=change):
                self.make_assessment(clean=True)
                self.c.deployment(InventoryReader(self.api).read(W, NOW - timedelta(minutes=10)).deployment)
                with self.c.db:
                    self.c.db.execute('DELETE FROM polls')
                # Seed a positive incident before the clean assessment.
                self.c.ingest(replace(self.reader.qualify(W, NOW), identities=('CVE-2026-10001',),
                                     clean=False, event_id=change[0].encode().hex().ljust(64, 'a')), NOW)
                self.reader.poll(W, NOW)
                self.assertEqual(self.c.all()[0]['status'], 'resolved')
                old_dep, old_pod = copy.deepcopy(self.api.dep), copy.deepcopy(self.api.pod)
                if change == 'digest':
                    self.api.pod['status']['containerStatuses'][0]['imageID'] = REF.replace('a'*64, 'd'*64)
                elif change == 'generation':
                    self.api.dep['metadata']['generation'] = 2
                    self.api.dep['status']['observedGeneration'] = 2
                else:
                    self.api.dep['metadata']['uid'] = 'replacement-uid'
                    self.api.objects[resource(APPS, 'example', 'replicasets', 'example-rs')]['metadata']['ownerReferences'][0]['uid'] = 'replacement-uid'
                self.assertNotEqual(self.reader.poll(W, NOW + timedelta(seconds=15)), 'confirmed')
                self.assertEqual(self.c.all()[0]['status'], 'open')
                self.api.dep.clear(); self.api.dep.update(old_dep)
                self.api.pod.clear(); self.api.pod.update(old_pod)
                self.api.objects[resource(APPS, 'example', 'replicasets', 'example-rs')]['metadata']['ownerReferences'][0]['uid'] = 'deployment-uid'
                # Independent cases use a fresh state file.
                self.c.close()
                (self.root / 'collector.sqlite').unlink()
                self.c = Collector(self.root / 'collector.sqlite')
                self.i = Intake(self.c)
                self.reader.intake = self.i

    def test_old_report_cannot_invent_generation_before_collector_observation(self):
        with self.c.db:
            self.c.db.execute('DELETE FROM deployments')
        self.assertEqual(self.reader.poll(W, NOW), 'unknown')
        self.assertEqual(self.c.all(), [])
        self.assertEqual(self.c.inventory_epoch(W), stamp(NOW))

    def test_expired_evidence_and_failed_latest_attempt_never_clean(self):
        self.reader.poll(W, NOW)
        before = self.c.all()[0]['identities']
        self.make_assessment(clean=True)
        self.assertEqual(self.reader.poll(W, NOW + timedelta(days=4)), 'stale')
        self.assertEqual(self.c.all()[0]['identities'], before)
        self.assertEqual(self.c.all()[0]['status'], 'open')

    def test_bad_database_report_summary_sbom_and_coverage_reject(self):
        cases = [
            (RUN+'/cache-audit.json.gz', lambda v: v['checksum'].update(valid=False)),
            (RUN+'/terminal-'+JOB+'.json', lambda v: v.update(state='failed')),
            (RUN+'/request-'+JOB+'.json.gz', lambda v: v.update(wlid='forged')),
            ('coverage.json', lambda v: v['rows'][0].update(generation=2)),
            ('coverage.json', lambda v: v['rows'][0].update(assessmentState='unsupported')),
            ('records.json', lambda v: next(iter(v.values()))['lastAttempt'].update(state='failed')),
            (RUN+'/reports-'+JOB+'.json.gz', lambda v: v['sbom']['spec']['syft']['source']['metadata'].update(config='invalid')),
            (RUN+'/summary-'+JOB+'.json.gz', lambda v: v['spec']['vulnerabilitiesRef']['all'].update(name='forged')),
        ]
        for name, mutate in cases:
            with self.subTest(name=name):
                self.make_assessment(clean=True)
                self.mutate(name, mutate)
                self.assertNotEqual(self.reader.poll(W, NOW), 'confirmed')
                self.assertEqual(self.c.all(), [])
                with self.c.db:
                    self.c.db.execute('DELETE FROM polls')

    def test_independent_named_reread_and_history_race(self):
        def change(path):
            if path.startswith(STORAGE):
                self.api.hook = None
                self.mutate('latest-vulnerability.json', lambda v: v.update(state='failed'))
        self.api.hook = change
        self.assertEqual(self.reader.poll(W, NOW), 'unknown')
        self.assertEqual(self.c.all(), [])

    def test_inventory_race_and_report_race(self):
        def change(path):
            if path.startswith(STORAGE):
                self.api.hook = None
                self.api.dep['metadata']['generation'] = 2
                self.api.dep['status']['observedGeneration'] = 2
        self.api.hook = change
        self.assertEqual(self.reader.poll(W, NOW), 'unknown')
        self.assertEqual(self.c.all(), [])

    def test_inflight_excluded_and_unsupported_are_explicit(self):
        save(self.history, 'inflight.json', dict(jobID='private-fence'))
        self.assertEqual(self.reader.poll(W, NOW), 'unknown')
        (self.history / 'inflight.json').unlink()
        self.reader.targets = frozenset()
        self.assertEqual(self.reader.poll(W, NOW + timedelta(seconds=15)), 'unsupported')
        self.assertEqual(self.i.health(NOW + timedelta(seconds=15))[0]['reason'], 'excluded')

    def test_history_symlink_and_gzip_bomb_reject(self):
        (self.history / 'records.json').unlink()
        (self.history / 'records.json').symlink_to(self.root / 'collector.sqlite')
        self.assertEqual(self.reader.poll(W, NOW), 'unknown')
        with gzip.open(self.history / 'bomb.json.gz', 'wb') as out:
            out.write(b' ' * (4 * 1024 * 1024 + 1))
        with self.assertRaises(Rejected):
            History(self.history).read('bomb.json.gz')

    def test_tls_origin_and_redirect_refusal(self):
        for origin in ('http://example', 'https://user:secret@example', 'https://example/path'):
            with self.assertRaisesRegex(Rejected, 'api_origin_invalid'):
                KubernetesAPI(origin, '/unused', '/unused')
        with self.assertRaisesRegex(Rejected, 'api_redirect_refused'):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'https://untrusted.invalid')

    def test_local_https_ca_token_rotation_redirect_and_response_bound(self):
        cert, key = self.root / 'ca.pem', self.root / 'server.key'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-keyout', str(key), '-out', str(cert), '-subj', '/CN=localhost',
                        '-addext', 'subjectAltName=DNS:localhost'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get('Authorization'))
                if self.path.endswith('redirect'):
                    self.send_response(302)
                    self.send_header('Location', 'http://untrusted.invalid/token')
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"items":[]}')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        token = self.root / 'token'
        token.write_text('local-test-token-1')
        origin = f'https://localhost:{server.server_port}'
        api = KubernetesAPI(origin, cert, token)
        self.assertEqual(api.get('/api/v1/pods'), {'items': []})
        token.write_text('local-test-token-2')
        self.assertEqual(api.get('/api/v1/pods'), {'items': []})
        self.assertEqual(seen, ['Bearer local-test-token-1', 'Bearer local-test-token-2'])
        with self.assertRaisesRegex(Rejected, 'api_redirect_refused'):
            api.get('/api/v1/redirect')
        with self.assertRaisesRegex(Rejected, 'api_response_limit'):
            api.raw('/api/v1/pods', limit=3)
        untrusted = KubernetesAPI(origin, None, token)
        with self.assertRaisesRegex(Rejected, 'api_unavailable'):
            untrusted.get('/api/v1/pods')


if __name__ == '__main__':
    unittest.main()
