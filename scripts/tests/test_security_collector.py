"""Offline integration tests: real format boundaries, SQLite restart, mock GitHub only."""
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security_collector.adapters import AssessmentContext, RuntimeStderrAdapter, VulnerabilityAdapter
from security_collector.engine import Collector
from security_collector.issues import Delivery, Issue, Publication, merge_section, render, start, END
from security_collector.model import Deployment, Rejected, Workload, fingerprint, stamp

FIXTURES = Path(__file__).parent / 'fixtures/security-collector'
NOW = datetime(2026, 11, 23, 0, 5, tzinfo=timezone.utc)
W = Workload('home', 'example', 'Deployment', 'example', 'app')
D = Deployment(W, 'sha256:' + 'a' * 64, 1, stamp(NOW), '2026-11-22T00:00:00Z')
CTX = AssessmentContext('sha256:'+'b'*64, '2026-11-21T06:20:06Z', 'v6.1.10',
                        '2026-11-22T23:59:00Z', 1, 'healthy', True)


def runtime(**changes):
    value = json.loads((FIXTURES / 'r1000.json').read_text())
    value['BaseRuntimeMetadata'].update(changes)
    return RuntimeStderrAdapter().read(json.dumps(value).encode(), D, NOW)


def assessment(bundle=None, context=CTX, deployment=D, now=NOW):
    bundle = copy.deepcopy(bundle) if bundle is not None else json.loads((FIXTURES / 'assessment.json').read_text())
    return VulnerabilityAdapter(context).read(json.dumps(bundle).encode(), deployment, now)


def clean_bundle(at='2026-11-23T00:01:00Z'):
    value = json.loads((FIXTURES / 'assessment.json').read_text())
    value['report']['spec']['payload']['matches'] = []
    value['report']['spec']['metadata']['report']['createdAt'] = at
    return value


class MockGitHub:
    def __init__(self):
        self.issues, self.calls = {}, []
        self.fail_lookup, self.fail_update, self.lost_create = False, False, False
        self.fail_create_before_commit = False

    def lookup(self, key):
        self.calls.append('lookup')
        if self.fail_lookup:
            raise OSError('TOKEN_MUST_NOT_PERSIST')
        return copy.deepcopy(self.issues.get(key))

    def create(self, key, title, body):
        self.calls.append('create')
        if self.fail_create_before_commit:
            raise TimeoutError('TOKEN_MUST_NOT_PERSIST')
        assert key not in self.issues, 'duplicate creation'
        self.issues[key] = Issue(len(self.issues) + 1, body)
        if self.lost_create:
            raise TimeoutError('TOKEN_MUST_NOT_PERSIST')
        return copy.deepcopy(self.issues[key])

    def update(self, issue, title, body, state):
        self.calls.append('update')
        if self.fail_update:
            raise OSError('TOKEN_MUST_NOT_PERSIST')
        target = next(i for i in self.issues.values() if i.number == issue.number)
        assert target.body == issue.body, 'optimistic concurrency conflict'
        target.body, target.state = body, state


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.sqlite'
        self.c = Collector(self.path)
        self.addCleanup(lambda: self.c.close())
        self.c.deployment(D)
        self.github = MockGitHub()
        self.publications = {W.key: Publication(W, (2138,), 2138)}

    def delivery(self, **kwargs):
        return Delivery(self.c, self.github, self.publications, **kwargs)

    def test_single_creation_correlation_and_replay(self):
        first = self.c.ingest(runtime(), NOW)
        self.c.ingest(runtime(), NOW)
        second = self.c.ingest(runtime(uniqueID='synthetic-event-2', timestamp='2026-11-22T23:36:00Z'), NOW)
        self.assertEqual(first['key'], second['key'])
        self.assertEqual(second['count'], 2)
        self.assertEqual(self.delivery().sync(NOW), 'disabled')
        self.assertEqual(self.github.calls, [])
        delivery = self.delivery(enabled=True)
        delivery.sync(NOW)
        delivery.sync(NOW)
        self.assertEqual(self.github.calls.count('create'), 1)
        self.assertEqual(self.github.calls.count('update'), 0)

    def test_group_cves_and_digest_change(self):
        a = self.c.ingest(assessment(), NOW)
        self.assertEqual(a['finding_count'], 2)
        changed = replace(D, digest='sha256:'+'c'*64, generation=2)
        self.c.deployment(changed)
        self.assertEqual(self.c.get(a['key'])['freshness'], 'unknown')
        stale_old = self.c.ingest(assessment(clean_bundle()), NOW)
        self.assertEqual(stale_old['status'], 'open')
        bundle = json.loads((FIXTURES / 'assessment.json').read_text())
        ref = 'registry.invalid/example@'+changed.digest
        bundle['report']['spec']['payload']['source']['target']['userInput'] = ref
        bundle['sbom']['spec']['syft']['source']['metadata']['userInput'] = ref
        bundle['summary']['metadata']['annotations']['kubescape.io/image-id'] = ref
        bundle['report']['spec']['metadata']['report']['createdAt'] = '2026-11-23T00:02:00Z'
        b = self.c.ingest(assessment(bundle, replace(CTX, generation=2), changed), NOW)
        self.assertEqual(b['key'], a['key'])
        self.assertEqual(len(self.c.all()), 1)
        self.assertEqual(b['digest'], changed.digest)

    def test_public_projection_and_raw_separation(self):
        a = self.c.ingest(runtime(fixSuggestions='TOKEN_MUST_NOT_PERSIST'), NOW)
        title, body = render(a, self.publications[W.key])
        self.c.ingest(assessment(), NOW)
        stored = json.dumps(self.c.all())
        for forbidden in ('SYNTHETIC_SECRET', 'UNTRUSTED_PAYLOAD', '/private/', 'TOKEN_MUST_NOT_PERSIST',
                          'private-package', 'registry.invalid', 'cmdline', 'fixSuggestions'):
            self.assertNotIn(forbidden, title + body + stored)
        self.assertIn('R1000', body)
        self.assertIn('2138', body)
        self.assertNotIn('processTree', stored)

    def test_non_allowlisted_workload_never_publishes(self):
        self.c.ingest(runtime(), NOW)
        self.assertEqual(Delivery(self.c, self.github, {}, enabled=True).sync(NOW), 'complete')
        self.assertEqual(self.github.calls, [])

    def test_missing_stale_and_failed_do_not_recover(self):
        a = self.c.ingest(assessment(), NOW)
        for state in ('failed', 'unknown', 'stale', 'unsupported'):
            self.c.telemetry(W, state, source='kubescape', category='vulnerability')
            self.assertEqual(self.c.get(a['key'])['status'], 'open')
            self.assertEqual(self.c.get(a['key'])['freshness'], state)
        self.c.refresh(NOW + timedelta(days=4))
        self.assertEqual(self.c.get(a['key'])['status'], 'open')
        self.assertEqual(self.c.get(a['key'])['freshness'], 'stale')

    def test_telemetry_gaps_are_source_and_category_scoped(self):
        runtime_incident = self.c.ingest(runtime(), NOW)
        vulnerability = self.c.ingest(assessment(), NOW)
        for state in ('unknown', 'failed', 'stale', 'unsupported'):
            with self.subTest(state=state):
                self.c.telemetry(W, state, source='kubescape', category='vulnerability')
                self.assertEqual(self.c.get(runtime_incident['key']), runtime_incident)
                self.assertEqual(self.c.get(vulnerability['key'])['freshness'], state)
        vulnerability = self.c.ingest(assessment(clean_bundle()), NOW)
        self.assertEqual(vulnerability['status'], 'resolved')
        self.c.telemetry(W, 'failed', source='kubescape', category='runtime')
        self.assertEqual(self.c.get(vulnerability['key']), vulnerability)
        self.assertEqual(self.c.get(runtime_incident['key'])['confidence'], 'unverified')
        runtime_incident = self.c.ingest(runtime(uniqueID='recovered', timestamp='2026-11-23T00:03:00Z'), NOW)
        self.assertEqual(runtime_incident['freshness'], 'confirmed')
        self.assertEqual(runtime_incident['confidence'], 'detection')
        self.c.telemetry(W, 'unknown', source='kubescape', category='vulnerability')
        self.assertEqual(self.c.get(runtime_incident['key']), runtime_incident)
        self.assertEqual(self.c.get(vulnerability['key'])['status'], 'open')

    def test_invalid_telemetry_scope_is_rejected_without_mutation(self):
        self.c.ingest(runtime(), NOW)
        self.c.ingest(assessment(), NOW)
        before = self.c.all()
        for source, category in (('untrusted', 'runtime'), ('kubescape', 'posture')):
            with self.subTest(source=source, category=category), self.assertRaises(Rejected):
                self.c.telemetry(W, 'failed', source=source, category=category)
        with self.assertRaises(TypeError):
            self.c.telemetry(W, 'failed')
        self.assertEqual(self.c.all(), before)

    def test_unqualified_assessments_retain_qualified_findings_and_public_output(self):
        confirmed = self.c.ingest(assessment(), NOW)
        delivery = self.delivery(enabled=True, allow_closure=True)
        delivery.sync(NOW)
        candidate = assessment(clean_bundle(), context=replace(CTX, latest_attempt='failed'))
        snapshot_fields = ('identities', 'finding_count', 'severity', 'remediation', 'provenance',
                           'findings_at', 'findings_digest', 'findings_generation', 'findings_qualified')
        states = [('unverified', state) for state in ('unknown', 'failed', 'stale', 'unsupported')]
        for i, (confidence, freshness) in enumerate(states + [('qualified', 'stale')]):
            for identities in ((), ('CVE-2026-10003',)):
                with self.subTest(confidence=confidence, freshness=freshness, identities=identities):
                    at = stamp(NOW - timedelta(minutes=4) + timedelta(seconds=2*i + bool(identities)))
                    event = replace(candidate, observed_at=at, identities=identities, severity='Low',
                                    confidence=confidence, freshness=freshness,
                                    event_id=fingerprint((confidence, freshness, identities)))
                    updated = self.c.ingest(event, NOW)
                    self.assertEqual({k: updated[k] for k in snapshot_fields},
                                     {k: confirmed[k] for k in snapshot_fields})
                    self.assertEqual(updated['assessed_at'], at)
                    self.assertEqual(updated['freshness'], freshness)
                    self.assertEqual(updated['confidence'], confidence)
                    self.assertEqual(updated['status'], 'open')
                    self.assertIsNone(updated['resolution'])
                    title, body = render(updated, self.publications[W.key])
                    for expected in ('CVE-2026-10001', 'CVE-2026-10002', 'Severity: Critical',
                                     'Findings in last qualified assessment: 2', 'Scanner reports a fix',
                                     'Finding snapshot: 2026-11-23T00:00:00Z',
                                     f'Latest assessment attempt: {at}'):
                        self.assertIn(expected, body)
                    self.assertNotIn('None in', body)
                    self.assertNotIn('CVE-2026-10003', body)
                    for forbidden in ('UNTRUSTED_PAYLOAD', 'private-package',
                                      'registry.invalid', '/private/', 'TOKEN_MUST_NOT_PERSIST'):
                        self.assertNotIn(forbidden, title + body + json.dumps(self.c.all()))
        self.assertEqual(delivery.sync(NOW), 'complete')
        issue = self.github.issues[confirmed['key']]
        self.assertEqual(issue.state, 'open')
        self.assertEqual(issue.body, body)

    def test_qualified_finding_snapshot_survives_restart_history_and_deployment(self):
        confirmed = self.c.ingest(assessment(), NOW)
        candidate = assessment(clean_bundle(), context=replace(CTX, coverage_complete=False))
        for i in range(35):
            self.c.ingest(replace(candidate, event_id=fingerprint(('gap', i)),
                                 observed_at=stamp(NOW - timedelta(minutes=4) + timedelta(seconds=i))), NOW)
        self.c.close()
        self.c = Collector(self.path)
        changed = replace(D, digest='sha256:'+'c'*64, generation=2)
        self.c.deployment(changed)
        # A qualified-looking old-digest clean report cannot replace the snapshot.
        updated = self.c.ingest(assessment(clean_bundle('2026-11-23T00:03:00Z')), NOW)
        self.c.refresh(NOW + timedelta(hours=2))
        updated = self.c.get(confirmed['key'])
        self.assertEqual(len(updated['history']), 32)
        self.assertTrue(all(h['findings'] == 0 for h in updated['history']))
        self.assertEqual(updated['identities'], confirmed['identities'])
        self.assertEqual(updated['severity'], 'Critical')
        self.assertEqual(updated['remediation'], 'fix_available')
        self.assertEqual(updated['findings_digest'], D.digest)
        self.assertEqual(updated['digest'], changed.digest)
        self.assertEqual(updated['status'], 'open')
        self.assertEqual(updated['freshness'], 'stale')
        _, body = render(updated, self.publications[W.key])
        self.assertIn(f'Finding snapshot: {confirmed["findings_at"]}; image digest: {D.digest}; generation: 1', body)
        self.assertIn(f'Image digest: {changed.digest}', body)

    def test_new_qualified_assessment_supersedes_retained_snapshot(self):
        confirmed = self.c.ingest(assessment(), NOW)
        self.c.ingest(assessment(clean_bundle(), context=replace(CTX, coverage_complete=False)), NOW)
        bundle = json.loads((FIXTURES / 'assessment.json').read_text())
        bundle['report']['spec']['metadata']['report']['createdAt'] = '2026-11-23T00:02:00Z'
        bundle['report']['spec']['payload']['matches'] = [
            {'vulnerability': {'id': 'CVE-2026-10003', 'severity': 'High', 'fix': {'state': 'not-fixed'}}}]
        updated = self.c.ingest(assessment(bundle), NOW)
        self.assertEqual(updated['identities'], ['CVE-2026-10003'])
        self.assertEqual(updated['finding_count'], 1)
        self.assertEqual(updated['severity'], 'High')
        self.assertEqual(updated['remediation'], 'no_known_fix')
        self.assertEqual(updated['findings_at'], '2026-11-23T00:02:00Z')
        self.assertEqual(updated['status'], 'open')
        updated = self.c.ingest(assessment(clean_bundle('2026-11-23T00:03:00Z')), NOW)
        self.assertEqual(updated['key'], confirmed['key'])
        self.assertEqual(updated['identities'], [])
        self.assertEqual(updated['finding_count'], 0)
        self.assertEqual(updated['severity'], 'Unknown')
        self.assertEqual(updated['findings_at'], '2026-11-23T00:03:00Z')
        self.assertEqual(updated['status'], 'resolved')

    def test_clean_qualified_recovery_and_separate_closure_gate(self):
        a = self.c.ingest(assessment(), NOW)
        delivery = self.delivery(enabled=True)
        delivery.sync(NOW)
        b = self.c.ingest(assessment(clean_bundle()), NOW)
        self.assertEqual(b['status'], 'resolved')
        delivery.sync(NOW)
        self.assertEqual(self.github.issues[a['key']].state, 'open')
        self.delivery(enabled=True, allow_closure=True).sync(NOW)
        self.assertEqual(self.github.issues[a['key']].state, 'closed')
        self.c.telemetry(W, 'failed', source='kubescape', category='vulnerability')
        self.delivery(enabled=True, allow_closure=True).sync(NOW)
        self.assertEqual(self.github.issues[a['key']].state, 'open')

    def test_runtime_requires_disposition_and_new_event_reopens(self):
        a = self.c.ingest(runtime(), NOW)
        self.c.refresh(NOW + timedelta(days=2))
        self.assertEqual(self.c.get(a['key'])['status'], 'open')
        self.c.disposition(a['key'], action='acknowledged', reviewed_issue=2138)
        self.assertEqual(self.c.get(a['key'])['status'], 'acknowledged')
        self.c.disposition(a['key'], action='false_positive', reviewed_issue=2138)
        self.assertEqual(self.c.get(a['key'])['status'], 'resolved')
        self.c.ingest(runtime(), NOW)  # Exact replay preserves disposition.
        self.assertEqual(self.c.get(a['key'])['status'], 'resolved')
        self.c.ingest(runtime(uniqueID='new', timestamp='2026-11-23T00:03:00Z'), NOW)
        self.assertEqual(self.c.get(a['key'])['status'], 'open')

    def test_human_notes_preserved_and_bad_markers_rejected(self):
        a = self.c.ingest(runtime(), NOW)
        delivery = self.delivery(enabled=True)
        delivery.sync(NOW)
        issue = self.github.issues[a['key']]
        issue.body = 'Human notes before\n' + issue.body + '\nHuman notes after'
        self.c.ingest(runtime(uniqueID='new'), NOW)
        delivery.sync(NOW)
        self.assertTrue(issue.body.startswith('Human notes before\n'))
        self.assertTrue(issue.body.endswith('\nHuman notes after'))
        with self.assertRaises(Rejected):
            merge_section(issue.body + END, a['key'], 'section')

    def test_github_outage_backoff_and_retry(self):
        self.c.ingest(runtime(), NOW)
        delivery = self.delivery(enabled=True)
        self.github.fail_lookup = True
        self.assertEqual(delivery.sync(NOW), 'pending')
        delivery.sync(NOW + timedelta(seconds=20))
        self.assertEqual(self.github.calls, ['lookup'])
        self.github.fail_lookup = False
        delivery.sync(NOW + timedelta(seconds=61))
        self.assertEqual(self.github.calls.count('create'), 1)
        self.c.ingest(runtime(uniqueID='second'), NOW)
        self.github.fail_update = True
        delivery.sync(NOW + timedelta(minutes=2))
        self.github.fail_update = False
        delivery.sync(NOW + timedelta(minutes=4))
        self.assertEqual(self.github.calls.count('create'), 1)
        self.assertNotIn('TOKEN_MUST_NOT_PERSIST', str(list(self.c.db.execute('SELECT * FROM delivery'))))

    def test_ambiguous_create_and_restart(self):
        a = self.c.ingest(runtime(), NOW)
        self.github.lost_create = True
        self.delivery(enabled=True).sync(NOW)
        self.c.close()
        self.c = Collector(self.path)
        self.c.ingest(runtime(), NOW)
        self.delivery(enabled=True).sync(NOW + timedelta(minutes=2))
        self.assertEqual(self.c.get(a['key'])['count'], 1)
        self.assertEqual(self.github.calls.count('create'), 1)

    def test_unknown_create_outcome_never_resubmits(self):
        self.c.ingest(runtime(), NOW)
        self.github.fail_create_before_commit = True
        self.delivery(enabled=True).sync(NOW)
        self.github.fail_create_before_commit = False
        self.delivery(enabled=True).sync(NOW + timedelta(minutes=2))
        self.assertEqual(self.github.calls.count('create'), 1)
        self.assertEqual(len(self.github.issues), 0)

    def test_bounds_and_retention(self):
        self.c.events_per_hour = 1
        a = self.c.ingest(runtime(), NOW)
        with self.assertRaisesRegex(Rejected, 'ingest_rate_limit'):
            self.c.ingest(runtime(uniqueID='two'), NOW)
        self.c.events_per_hour = 1000
        self.c.max_receipts = 1
        with self.assertRaisesRegex(Rejected, 'receipt_capacity'):
            self.c.ingest(runtime(uniqueID='two'), NOW)
        self.c.max_receipts = 20000
        self.c.max_incidents = 1
        with self.assertRaisesRegex(Rejected, 'incident_capacity'):
            self.c.ingest(replace(runtime(), identities=('R1002',), event_id=fingerprint('other')), NOW)
        for i in range(40):
            self.c.ingest(runtime(uniqueID=str(i)), NOW)
        self.assertEqual(len(self.c.get(a['key'])['history']), 32)
        self.c.refresh(NOW + timedelta(days=8))
        self.assertEqual(len(self.c.all()), 1)  # Never discard stable incident tombstones.
        with self.assertRaisesRegex(Rejected, 'event_outside_replay_window'):
            self.c.ingest(runtime(), NOW + timedelta(days=8))

    def test_write_quotas_survive_restart(self):
        a = self.c.ingest(runtime(), NOW)
        for _ in range(10):
            self.c.db.execute('INSERT INTO writes VALUES (?,0)', (stamp(NOW),))
        self.c.db.commit()
        self.assertEqual(self.delivery(enabled=True).sync(NOW), 'write_rate_limit')
        self.c.close()
        self.c = Collector(self.path)
        self.assertEqual(self.delivery(enabled=True).sync(NOW), 'write_rate_limit')
        self.assertEqual(self.github.calls, [])
        self.assertIsNotNone(self.c.get(a['key']))

    def test_expired_clean_evidence_reopens_without_finding_disappearance_success(self):
        a = self.c.ingest(assessment(), NOW)
        self.c.ingest(assessment(clean_bundle()), NOW)
        self.assertEqual(self.c.get(a['key'])['status'], 'resolved')
        self.c.refresh(NOW + timedelta(hours=2))
        self.assertEqual(self.c.get(a['key'])['status'], 'open')
        self.assertEqual(self.c.get(a['key'])['freshness'], 'stale')

    def test_out_of_order_clean_assessment_cannot_clear_newer_findings(self):
        a = self.c.ingest(assessment(), NOW)
        self.c.ingest(assessment(clean_bundle('2026-11-22T23:59:00Z')), NOW)
        self.assertEqual(self.c.get(a['key'])['status'], 'open')
        self.assertEqual(self.c.get(a['key'])['finding_count'], 2)

    def test_creation_daily_and_api_limits(self):
        self.c.ingest(runtime(), NOW)
        for _ in range(3):
            self.c.db.execute('INSERT INTO writes VALUES (?,1)', (stamp(NOW),))
        self.c.db.commit()
        self.assertEqual(self.delivery(enabled=True).sync(NOW), 'create_rate_limit')
        self.c.db.execute('DELETE FROM writes')
        for _ in range(20):
            self.c.db.execute('INSERT INTO writes VALUES (?,0)', (stamp(NOW - timedelta(hours=2)),))
        self.c.db.commit()
        self.assertEqual(self.delivery(enabled=True).sync(NOW), 'daily_write_limit')
        self.c.db.execute('DELETE FROM writes')
        for _ in range(60):
            self.c.db.execute('INSERT INTO api_calls VALUES (?)', (stamp(NOW),))
        self.c.db.commit()
        self.assertEqual(self.delivery(enabled=True).sync(NOW), 'api_rate_limit')
        self.assertEqual(self.github.calls.count('create'), 0)

    def test_only_confirmed_important_findings_create_issues(self):
        self.c.ingest(runtime(severity=3), NOW)
        self.delivery(enabled=True).sync(NOW)
        self.assertEqual(self.github.calls, [])
        self.c.ingest(assessment(context=replace(CTX, latest_attempt='failed')), NOW)
        self.delivery(enabled=True).sync(NOW)
        self.assertEqual(self.github.calls, [])

    def test_one_writer_lock(self):
        with self.assertRaisesRegex(Rejected, 'writer_already_active'):
            Collector(self.path)

    def test_private_state_permissions(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_rollback_disabled_delivery_preserves_state(self):
        a = self.c.ingest(runtime(), NOW)
        self.delivery(enabled=True).sync(NOW)
        before = copy.deepcopy(self.github.issues)
        self.c.ingest(runtime(uniqueID='second'), NOW)
        self.assertEqual(self.delivery().sync(NOW), 'disabled')
        self.assertEqual(self.github.issues, before)
        self.assertEqual(self.c.get(a['key'])['count'], 2)


class AdapterTests(unittest.TestCase):
    def test_malicious_input(self):
        for payload in (b'{"RuleID":1,"RuleID":2}', b'[]', b'{' , b'\xff', b' ' * (1024*1024+1), b'['*2000):
            with self.subTest(payload=payload[:20]), self.assertRaises(Rejected):
                RuntimeStderrAdapter().read(payload, D, NOW)
        for rule in ('R1000\n@everyone', '<!-- injected -->', 'CVE-2026-10001'):
            value = json.loads((FIXTURES / 'r1000.json').read_text())
            value['RuleID'] = rule
            with self.assertRaises(Rejected):
                RuntimeStderrAdapter().read(json.dumps(value).encode(), D, NOW)
        for name in ('x\n@everyone', 'token/secret', '<script>'):
            with self.assertRaises(Rejected):
                replace(W, name=name)

    def test_actual_schema_and_normalized_values(self):
        event = runtime()
        self.assertEqual((event.identities, event.severity, event.digest), (('R1000',), 'High', D.digest))
        event = assessment()
        self.assertEqual(event.identities, ('CVE-2026-10001','CVE-2026-10002'))
        self.assertEqual(event.remediation, 'fix_available')
        self.assertEqual(event.freshness, 'confirmed')

    def test_runtime_image_digest_is_optional(self):
        value = json.loads((FIXTURES / 'r1000.json').read_text())
        value['RuntimeK8sDetails'].pop('imageDigest')
        event = RuntimeStderrAdapter().read(json.dumps(value).encode(), W, NOW)
        self.assertIsNone(event.digest)
        self.assertEqual(event.workload, W)

    def test_nanosecond_replay_identity(self):
        one = runtime(uniqueID='', timestamp='2026-11-22T23:35:19.421796939Z')
        two = runtime(uniqueID='', timestamp='2026-11-22T23:35:19.421796940Z')
        self.assertNotEqual(one.event_id, two.event_id)

    def test_empty_reports_require_all_recovery_gates(self):
        for context in (replace(CTX, latest_attempt='failed'), replace(CTX, coverage_complete=False),
                        replace(CTX, generation=2)):
            event = assessment(clean_bundle(), context)
            self.assertFalse(event.clean)
            self.assertNotEqual(event.freshness, 'confirmed')
        event = assessment(clean_bundle(), now=NOW + timedelta(days=4))
        self.assertFalse(event.clean)
        self.assertEqual(event.freshness, 'stale')
        old = clean_bundle()
        old['report']['spec']['payload']['descriptor']['db']['built'] = '2026-11-01T00:00:00Z'
        self.assertFalse(assessment(old, replace(CTX, database_built='2026-11-01T00:00:00Z')).clean)

    def test_corrupt_missing_or_unsupported_report_fails_closed(self):
        mutations = [
            lambda b: b['report']['spec']['payload'].pop('matches'),
            lambda b: b['report']['metadata']['annotations'].update({'kubescape.io/status':'failed'}),
            lambda b: b['sbom']['spec']['syft']['source']['metadata'].update({'manifest':'corrupt!'}),
            lambda b: b['sbom']['spec']['syft']['source']['metadata'].update({'architecture':'arm64'}),
            lambda b: b['summary']['metadata']['annotations'].update({'kubescape.io/workload-container-name':'wrong'}),
            lambda b: b['report']['spec']['metadata']['tool'].update({'databaseVersion':'c'*64}),
            lambda b: b['report']['spec']['payload']['matches'][0]['vulnerability'].update({'id':'CVE-2026-10001\nSECRET'}),
        ]
        for mutate in mutations:
            value = json.loads((FIXTURES / 'assessment.json').read_text())
            mutate(value)
            with self.subTest(mutation=mutate), self.assertRaises(Rejected):
                assessment(value)
        with self.assertRaisesRegex(Rejected, 'predates_deployment'):
            assessment(deployment=replace(D, deployed_at='2026-11-23T00:01:00Z'))


if __name__ == '__main__':
    unittest.main()
