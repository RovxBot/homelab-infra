"""Incident enrichment and closure use actual endpoint evidence only."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, mock_open, patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]


class GatusIssueTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        text = (ROOT / 'infra/observability/gatus-github-issue-sync-configmap.yaml').read_text().split('  sync.py: |\n',1)[1]
        source = '\n'.join(line[4:] for line in text.splitlines())
        self.helper = {'__name__': 'gatus_test'}
        exec(compile(source, 'sync.py', 'exec'), self.helper)
        self.helper.update(MAX_RESULT_AGE_SECONDS=900, SUCCESS_THRESHOLD=2, GITHUB_TOKEN='fixture',
                           GATUS_API_URL='gatus-status', CLUSTER_HEALTH_URL='cluster-health',
                           ASSESSMENT_HEALTH_URL='assessment-health',
                           GITHUB_API_ROOT='github', GITHUB_REPOSITORY_API_URL='github/repos/fixture', DRY_RUN=False)
        self.now = datetime.now(timezone.utc)
        self.success = {'success':True, 'timestamp': self.now.isoformat()}
        self.older = {**self.success, 'timestamp': (self.now-timedelta(seconds=60)).isoformat()}
        self.health = {'status':'unhealthy', 'observedAt': self.now.isoformat(),
                       'checks': {key: 'ok' for key in self.helper['CHECKS']},
                       'failures': {'workloads': ['koito/bridge desired=1 available=0', 'TOKEN=secret\npassword']}}
        self.health['checks']['workloads'] = 'failed'
        self.issue = {'title': self.helper['CLUSTER_TITLE'], 'url':'github/issues/1', 'number':1, 'body':'Original incident'}
        self.calls = []
        self.reads = []

    def workload_fixture(self, name='koito', namespace='koito', ready=0, restarts=3):
        obj = {'metadata': {'generation': 2}, 'spec': {'replicas': 1},
               'status': {'observedGeneration': 2, 'availableReplicas': ready}}
        pod = {'metadata': {'uid': 'pod-uid', 'labels': {'app': name}, 'name': 'PRIVATE-POD'},
               'spec': {'containers': [{'env': [{'name': 'PASSWORD', 'value': 'SECRET'}]}]},
               'status': {'phase': 'Pending', 'conditions': [{'type': 'PodScheduled', 'status': 'False',
                                                          'reason': 'Unschedulable', 'message': 'SECRET'}],
                          'containerStatuses': [{'ready': bool(ready), 'restartCount': restarts,
                                                 'state': {'waiting': {'reason': 'CrashLoopBackOff', 'message': 'SECRET'}},
                                                 'lastState': {'terminated': {'reason': 'OOMKilled', 'message': 'SECRET'}}}]}}
        return {f'/apis/apps/v1/namespaces/{namespace}/deployments/{name}': obj,
                f'/api/v1/namespaces/{namespace}/pods?': {'items': [pod]},
                f'/api/v1/namespaces/{namespace}/events?': {'items': [
                    {'type': 'Warning', 'reason': 'FailedScheduling', 'message': 'SECRET',
                     'involvedObject': {'uid': 'pod-uid', 'kind': 'Pod'}, 'lastTimestamp': self.now.isoformat()},
                    {'type': 'Warning', 'reason': 'SECRET', 'message': 'SECRET',
                     'involvedObject': {'uid': 'pod-uid', 'kind': 'Pod'}, 'lastTimestamp': self.now.isoformat()},
                    {'type': 'Warning', 'reason': 'FailedMount', 'involvedObject': {'uid': 'unrelated', 'kind': 'Pod'},
                     'lastTimestamp': self.now.isoformat()}]}}

    def recovery_samples(self, count=20, spacing=60, age=0):
        return [{'success': True, 'timestamp': (self.now-timedelta(seconds=age+(count-1-i)*spacing)).isoformat()}
                for i in range(count)]

    def healthy_collector(self):
        return {'status': 'healthy', 'observedAt': self.now.isoformat(),
                'checks': {key: 'ok' for key in self.helper['CHECKS']},
                'failures': {}, 'collectionErrors': {}}

    def cluster_endpoint(self, samples):
        return {'name': 'cluster-health', 'group': 'cluster', 'results': samples}

    def assert_no_closure(self):
        self.assertTrue(all('state' not in payload for _, _, payload in self.calls), self.calls)

    def invoke(self, endpoints=None, health=None, issues=None, observations=None, assessment=None,
               live_body=None, failed_write=None, expected=0):
        health = self.health if health is None else health
        endpoints = endpoints if endpoints is not None else [{'name':'cluster-health','group':'cluster','results':[self.older,self.success]}]
        issues = issues if issues is not None else [self.issue]
        observations = observations or {}
        def read(path):
            self.reads.append(path)
            for key, value in observations.items():
                if path == key or ('?' in key and path.startswith(key)):
                    if isinstance(value, Exception):
                        raise value
                    return value
            return None
        self.helper['KubernetesReader'] = lambda: SimpleNamespace(get=read)
        def request(url, method='GET', payload=None, headers=None):
            if method != 'GET':
                if url == failed_write:
                    raise OSError('SECRET')
                self.calls.append((url,method,payload))
                return {}
            if url == 'gatus-status':
                if isinstance(endpoints, Exception): raise endpoints
                return endpoints
            if url == 'cluster-health':
                self.assertIsNone(headers)  # Never send GitHub credentials here.
                if isinstance(health, Exception): raise health
                return health
            if url == 'github/user': return {'login':'fixture'}
            if url == 'assessment-health':
                self.assertIsNone(headers)
                if isinstance(assessment, Exception): raise assessment
                return assessment
            if '/issues?' in url:
                self.assertIn('creator=fixture', url)
                self.assertIn('state=open', url)
                return issues
            for issue in issues:
                if url == issue['url']:
                    return {**issue, 'state': 'open', 'body': live_body if live_body is not None else issue.get('body')}
            raise AssertionError(url)
        self.helper['api_request'] = request
        with redirect_stdout(io.StringIO()):
            self.assertEqual(self.helper['main'](), expected)

    def test_diagnostics_show_failed_categories_and_resource_names(self):
        self.invoke()
        self.assertEqual(len(self.calls), 1)
        body = self.calls[0][2]['body']
        self.assertIn('Nodes: OK',body)
        self.assertIn('Workloads: FAILED',body)
        self.assertIn('koito/bridge',body)
        self.assertNotIn('TOKEN',body)
        self.assertNotIn('state',self.calls[0][2])

    def test_unchanged_diagnostics_do_not_update_or_create_duplicate_incidents(self):
        self.issue['body'] = self.helper['diagnostic_body'](self.issue['body'],self.health)
        self.invoke()
        self.assertEqual(self.calls,[])

    def test_cluster_requires_all_checks_and_recent_observation_as_well_as_gatus(self):
        self.invoke(health=OSError('unreachable'))
        self.assertEqual(self.calls,[])
        self.health['status']='healthy'
        self.invoke()
        self.assertTrue(all('state' not in payload for _,_,payload in self.calls))
        self.calls.clear()
        self.health['checks']['workloads']='ok'
        self.health['observedAt']='2000-01-01T00:00:00Z'
        self.invoke()
        self.assertEqual(self.calls,[])

    def test_verified_cluster_recovery_closes_original_issue(self):
        for failures in ({}, {key: [] for key in self.helper['CHECKS']}):
            with self.subTest(failures=failures):
                health = self.healthy_collector()
                health['failures'] = failures
                self.invoke(endpoints=[self.cluster_endpoint(self.recovery_samples())], health=health)
                self.assertEqual(self.calls,[('github/issues/1','PATCH',{'state':'closed','state_reason':'completed'})])
                self.calls.clear()

    def test_missing_diagnostic_fields_block_closure_despite_valid_recovery_history(self):
        for missing in (('failures',), ('collectionErrors',), ('failures', 'collectionErrors')):
            with self.subTest(missing=missing):
                health = self.healthy_collector()
                for field in missing:
                    del health[field]
                self.assertFalse(self.helper['cluster_is_healthy'](health))
                self.invoke(endpoints=[self.cluster_endpoint(self.recovery_samples())], health=health)
                self.assert_no_closure()
                self.calls.clear()

    def test_malformed_or_nonempty_diagnostic_fields_block_closure(self):
        cases = [(field, value) for field in ('failures', 'collectionErrors')
                 for value in (None, [], '', False, 0, 'invalid', ['invalid'])]
        cases += [('failures', {'nodes': [], 'flux': value})
                  for value in (None, {}, '', False, 0, 'invalid', ['infra/kyverno-policies Ready=False'])]
        cases.append(('collectionErrors', {'flux': 'query_failed'}))
        for field, value in cases:
            with self.subTest(field=field, value=value):
                health = self.healthy_collector()
                health[field] = value
                self.assertFalse(self.helper['cluster_is_healthy'](health))
                self.invoke(endpoints=[self.cluster_endpoint(self.recovery_samples())], health=health)
                self.assert_no_closure()
                self.calls.clear()

    def test_two_ten_and_nineteen_successes_keep_failure_diagnostics_and_user_text(self):
        self.issue['body'] = self.helper['diagnostic_body']('Original incident\n\nOperator note', self.health) + '\nUser follow-up\n'
        before = self.issue['body']
        for count in (2, 10, 19):
            with self.subTest(count=count):
                self.invoke(endpoints=[self.cluster_endpoint(self.recovery_samples(count))], health=self.healthy_collector())
                self.assertEqual(self.calls, [])
                self.assertEqual(self.issue['body'], before)

    def test_recovery_accepts_both_adapter_orders_and_older_history(self):
        samples = self.recovery_samples(100)
        samples[0]['success'] = False
        for results in (samples, list(reversed(samples))):
            self.assertTrue(self.helper['cluster_endpoint_is_stably_healthy'](self.cluster_endpoint(results)))

    def test_failure_resets_the_twenty_sample_sequence(self):
        samples = self.recovery_samples(41)
        samples[20]['success'] = False
        for count in (22, 31, 40):
            with self.subTest(count=count):
                self.invoke(endpoints=[self.cluster_endpoint(samples[:count])], health=self.healthy_collector())
                self.assert_no_closure()
                self.calls.clear()
        self.assertTrue(self.helper['cluster_endpoint_is_stably_healthy'](self.cluster_endpoint(samples)))

    def test_missing_malformed_duplicate_future_stale_and_disordered_samples_block_closure(self):
        valid = self.recovery_samples()
        cases = {'missing': valid[:-1], 'empty': [], 'not-list': {}, 'no-results': None,
                 'stale-latest': self.recovery_samples(age=121), 'too-fast': self.recovery_samples(spacing=30),
                 'excessive-gap': self.recovery_samples(spacing=91),
                 'missing-interval': self.recovery_samples(21)[:10] + self.recovery_samples(21)[11:]}
        for label, replacement in (('missing-time', None), ('invalid-time', 'invalid'),
                                   ('naive', self.now.replace(tzinfo=None).isoformat()),
                                   ('future', (self.now+timedelta(seconds=1)).isoformat()),
                                   ('duplicate', valid[1]['timestamp'])):
            samples = deepcopy(valid)
            samples[0]['timestamp'] = replacement
            cases[label] = samples
        for label, replacement in (('not-object', None), ('missing-success', {'timestamp': valid[0]['timestamp']}),
                                   ('non-boolean-success', {**valid[0], 'success': 'true'})):
            samples = deepcopy(valid)
            samples[0] = replacement
            cases[label] = samples
        samples = deepcopy(valid)
        samples[1], samples[2] = samples[2], samples[1]
        cases['shuffled'] = samples
        for label, samples in cases.items():
            with self.subTest(label=label):
                self.invoke(endpoints=[self.cluster_endpoint(samples)], health=self.healthy_collector())
                self.assert_no_closure()
                self.calls.clear()

    def test_duplicate_cluster_endpoint_records_block_closure(self):
        for count in (2, 3):
            self.invoke(endpoints=[self.cluster_endpoint(self.recovery_samples())] * count, health=self.healthy_collector())
            self.assert_no_closure()

    def test_unknown_unhealthy_failed_unknown_or_missing_category_blocks_closure(self):
        for status in ('unknown', 'unhealthy', None):
            health = self.healthy_collector()
            health['status'] = status
            self.invoke(endpoints=[self.cluster_endpoint(self.recovery_samples())], health=health)
            self.assert_no_closure()
            self.calls.clear()
        for key in self.helper['CHECKS']:
            for value in ('failed', 'unknown', None):
                with self.subTest(key=key, value=value):
                    health = self.healthy_collector()
                    if value is None:
                        del health['checks'][key]
                    else:
                        health['checks'][key] = value
                    self.invoke(endpoints=[self.cluster_endpoint(self.recovery_samples())], health=health)
                    self.assert_no_closure()
                    self.calls.clear()

    def test_unavailable_stale_future_or_contradictory_collector_blocks_closure(self):
        cases = [OSError('unreachable'), ValueError('bad JSON'), {}, []]
        for field, value in (('observedAt', (self.now-timedelta(seconds=301)).isoformat()),
                             ('observedAt', (self.now+timedelta(seconds=60)).isoformat()),
                             ('observedAt', 'invalid'), ('checks', []),
                             ('failures', {'flux': ['infra/kyverno-policies Ready=False']}),
                             ('failures', {'flux': 'invalid'}), ('failures', []),
                             ('collectionErrors', {'flux': 'query_failed'}), ('collectionErrors', None)):
            health = self.healthy_collector()
            health[field] = value
            cases.append(health)
        for health in cases:
            with self.subTest(health=health):
                self.invoke(endpoints=[self.cluster_endpoint(self.recovery_samples())], health=health)
                self.assert_no_closure()
                self.calls.clear()

    def test_gatus_api_failure_or_invalid_payload_cannot_close_incidents(self):
        for endpoints in (OSError('unreachable'), ValueError('bad JSON'), None, {'endpoints': None}):
            if endpoints is None:
                endpoints = 'invalid'
            with self.subTest(endpoints=endpoints):
                with self.assertRaises((OSError, ValueError, RuntimeError)):
                    self.invoke(endpoints=endpoints, health=self.healthy_collector())
                self.assertEqual(self.calls, [])

    def test_invalid_cluster_policy_fails_closed_without_affecting_other_endpoints(self):
        invalid = {'CLUSTER_SUCCESS_THRESHOLD': ('0', '-1', '2', 'invalid', '20.0', ''),
                   'CLUSTER_RECOVERY_MIN_SECONDS': ('0', '1139', 'invalid', '999999'),
                   'CLUSTER_MAX_SAMPLE_GAP_SECONDS': ('0', '-1', '59', '120', 'invalid'),
                   'CLUSTER_MAX_RESULT_AGE_SECONDS': ('0', '-1', 'invalid'),
                   'CLUSTER_MAX_HEALTH_AGE_SECONDS': ('0', '-1', 'invalid')}
        other = {'name': 'gitea-backup', 'group': 'internal', 'results': [self.older, self.success]}
        other_issue = {**self.issue, 'title': 'alert(gatus): internal/gitea-backup', 'url': 'github/issues/other'}
        for key, values in invalid.items():
            for value in values:
                with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}):
                    self.invoke(endpoints=[self.cluster_endpoint(self.recovery_samples()), other],
                                health=self.healthy_collector(), issues=[self.issue, other_issue])
                    self.assertEqual([url for url, _, payload in self.calls if 'state' in payload], ['github/issues/other'])
                    self.calls.clear()

    def test_cluster_policy_can_be_strengthened_independently(self):
        with patch.dict(os.environ, {'CLUSTER_SUCCESS_THRESHOLD': '21', 'CLUSTER_RECOVERY_MIN_SECONDS': '1200'}):
            check = self.helper['cluster_endpoint_is_stably_healthy']
            self.assertFalse(check(self.cluster_endpoint(self.recovery_samples())))
            self.assertTrue(check(self.cluster_endpoint(self.recovery_samples(21))))

    def test_diagnostic_update_preserves_original_content_and_user_edits(self):
        body = self.helper['diagnostic_body']('Original condition results\nOperator note', self.health)
        self.issue['body'] = body + '\nUser follow-up\n'
        self.health['failures']['workloads'] = ['koito/bridge desired=2 available=0']
        self.invoke()
        payload = self.calls[0][2]
        self.assertEqual(set(payload), {'body'})
        self.assertTrue(payload['body'].startswith('Original condition results\nOperator note\n'))
        self.assertTrue(payload['body'].endswith('\nUser follow-up\n'))
        self.assertEqual(payload['body'].count(self.helper['DIAGNOSTIC_START']), 1)

    def test_issue_selection_remains_creator_filtered_and_excludes_prs(self):
        self.invoke(issues=[{**self.issue, 'title': 'Human issue'}, {**self.issue, 'pull_request': {}}])
        self.assertEqual(self.calls, [])

    def test_other_incidents_close_only_for_their_own_endpoint(self):
        endpoints=[{'name':'gitea-backup','group':'internal','results':[self.older,self.success]},
                   {'name':'koito-lastfm-bridge','group':'internal','results':[self.older,{**self.success,'success':False}]}]
        issues=[{'title':'alert(gatus): internal/'+name,'url':'github/issues/'+name,'body':'', 'number':i}
                for i,name in enumerate(('gitea-backup','koito-lastfm-bridge'))]
        self.invoke(endpoints=endpoints,issues=issues)
        self.assertEqual([url for url,_,_ in self.calls],['github/issues/gitea-backup'])

    def test_newest_failure_blocks_recovery_in_either_storage_order(self):
        values = [{**self.success,'success':False}, self.older, {**self.older,'timestamp':(self.now-timedelta(seconds=120)).isoformat()}]
        for order in (values, list(reversed(values))):
            self.assertFalse(self.helper['endpoint_is_stably_healthy']({'results':order}))

    def test_stale_naive_invalid_insufficient_or_missing_samples_never_close(self):
        for results in ([], [self.success], [self.success, {**self.success,'timestamp':'2000-01-01T00:00:00Z'}],
                        [self.success, {**self.success,'timestamp':'2026-11-19'}],
                        [self.success, {**self.success,'timestamp':'invalid'}]):
            self.assertFalse(self.helper['endpoint_is_stably_healthy']({'results':results}))

    def test_dry_run_has_no_writes(self):
        self.helper['DRY_RUN']=True
        self.invoke()
        self.invoke(endpoints=[self.cluster_endpoint(self.recovery_samples())], health=self.healthy_collector())
        self.assertEqual(self.calls,[])

    def koito_incident(self, results=None):
        issue = {**self.issue, 'title': 'alert(gatus): internal/koito'}
        endpoint = {'name': 'koito', 'group': 'internal',
                    'results': results if results is not None else [{**self.success, 'success': False}]}
        return issue, endpoint

    def test_workload_observations_are_actionable_and_safe_for_public_output(self):
        issue, endpoint = self.koito_incident()
        self.invoke(issues=[issue], endpoints=[endpoint], observations=self.workload_fixture())
        body = self.calls[0][2]['body']
        for text in ('**Observed:**', '**Possible cause:**', '**Next investigation:**', '**Recovery condition:**',
                     'desired=1, available=0', 'containers ready=0/1, restarts=3', 'pending=1', 'OOMKilled',
                     'Unschedulable', 'CrashLoopBackOff', 'FailedScheduling', 'Snapshot collected at:',
                     'Hypotheses, not confirmed', 'kubectl -n koito get deployments koito', 'two fresh successful'):
            self.assertIn(text, body)
        for text in ('SECRET', 'PASSWORD', 'PRIVATE-POD', 'pod-uid', 'FailedMount'):
            self.assertNotIn(text, body)
        self.assertTrue(any('labelSelector=app%3Dkoito' in path and 'limit=20' in path for path in self.reads))
        self.assertTrue(any('fieldSelector=type%3DWarning' in path and 'limit=100' in path for path in self.reads))
        self.assertTrue(all('/namespaces/koito/' in path or '/namespaces/flux-system/' in path for path in self.reads))

    def test_absent_failed_and_malformed_observations_are_unknown_not_recovery(self):
        issue, endpoint = self.koito_incident([])
        path = '/apis/apps/v1/namespaces/koito/deployments/koito'
        for value in (None, OSError('403 SECRET'), ValueError('SECRET'), {'status': None}, {'status': {}}):
            with self.subTest(value=value):
                self.invoke(issues=[issue], endpoints=[endpoint], observations={path: value})
                self.assert_no_closure()
                body = self.calls[-1][2]['body']
                self.assertIn('unknown', body)
                self.assertNotIn('SECRET', body)
                self.calls.clear()

    def test_stale_controller_missing_pods_and_unavailable_events_are_explicit(self):
        issue, endpoint = self.koito_incident()
        observations = self.workload_fixture()
        observations['/apis/apps/v1/namespaces/koito/deployments/koito']['status']['observedGeneration'] = 1
        observations['/api/v1/namespaces/koito/pods?'] = {'items': [], 'metadata': {'continue': 'SECRET'}}
        observations['/api/v1/namespaces/koito/events?'] = None
        self.invoke(issues=[issue], endpoints=[endpoint], observations=observations)
        body = self.calls[0][2]['body']
        self.assertIn('controller=stale/unknown', body)
        self.assertIn('Pods=0 (partial', body)
        self.assertIn('unavailable): unknown', body)
        self.assertNotIn('SECRET', body)
        self.assert_no_closure()

    def test_gatus_missing_stale_future_and_duplicate_endpoints_never_recover(self):
        issue, endpoint = self.koito_incident()
        for endpoints in ([], [{**endpoint, 'results': []}],
                          [{**endpoint, 'results': [{**self.success, 'timestamp': '2000-01-01T00:00:00Z'}] * 2}],
                          [{**endpoint, 'results': [{**self.success, 'timestamp': (self.now+timedelta(hours=1)).isoformat()}] * 2}],
                          [{**endpoint, 'results': [self.older, self.success]}] * 3):
            self.invoke(issues=[issue], endpoints=endpoints)
            self.assert_no_closure()
            if not endpoints or len(endpoints) > 1:
                self.assertIn('Gatus latest result: unknown', self.calls[-1][2]['body'])
            self.calls.clear()

    def test_diagnostic_timestamps_do_not_churn_but_changed_restarts_update(self):
        issue, endpoint = self.koito_incident()
        observations = self.workload_fixture()
        self.invoke(issues=[issue], endpoints=[endpoint], observations=observations)
        issue['body'] = self.calls.pop()[2]['body']
        issue['body'] = issue['body'].replace('Snapshot collected at: ', 'Snapshot collected at: 2000-01-01T00:00:00Z ignored ')
        endpoint['results'][0]['timestamp'] = (self.now-timedelta(seconds=10)).isoformat()
        self.invoke(issues=[issue], endpoints=[endpoint], observations=observations)
        self.assertEqual(self.calls, [])
        self.invoke(issues=[issue], endpoints=[endpoint], observations=self.workload_fixture(restarts=4))
        self.assertIn('restarts=4', self.calls[0][2]['body'])

    def test_latest_user_content_and_trailing_whitespace_are_preserved(self):
        issue, endpoint = self.koito_incident()
        live = 'Operator edit after listing  \n\nUser-written guidance\n'
        self.invoke(issues=[issue], endpoints=[endpoint], live_body=live)
        self.assertTrue(self.calls[0][2]['body'].startswith(live))
        before = self.calls[0][2]['body'] + '\nOperator follow-up  \n'
        updated = self.helper['managed_body'](before, 'replacement', self.helper['INCIDENT_START'], self.helper['INCIDENT_END'])
        self.assertTrue(updated.startswith(live))
        self.assertTrue(updated.endswith('\nOperator follow-up  \n'))

    def test_ambiguous_managed_markers_are_left_untouched(self):
        start, end = self.helper['INCIDENT_START'], self.helper['INCIDENT_END']
        for body in ('Manual\n' + start, end + 'Manual' + start, start + end + start + end):
            self.assertEqual(self.helper['managed_body'](body, 'replacement'), body)

    def test_duplicate_issue_records_update_once_and_never_create(self):
        issue, endpoint = self.koito_incident()
        self.invoke(issues=[issue, issue], endpoints=[endpoint])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1], 'PATCH')
        self.assertEqual(set(self.calls[0][2]), {'body'})

    def test_duplicate_existing_incidents_share_endpoint_observations(self):
        issue, endpoint = self.koito_incident()
        self.invoke(issues=[issue, {**issue, 'number': 2, 'url': 'github/issues/2'}], endpoints=[endpoint])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.reads), len(set(self.reads)))

    def test_failed_enrichment_write_does_not_block_unrelated_recovery(self):
        issue, endpoint = self.koito_incident()
        other = {**issue, 'title': 'alert(gatus): internal/other', 'url': 'github/issues/other'}
        healthy = {'name': 'other', 'group': 'internal', 'results': [self.older, self.success]}
        self.invoke(issues=[issue, other], endpoints=[endpoint, healthy], failed_write=issue['url'], expected=1)
        self.assertEqual(self.calls, [('github/issues/other', 'PATCH', {'state': 'closed', 'state_reason': 'completed'})])

    def test_kubernetes_does_not_override_gatus_recovery_authority(self):
        issue, endpoint = self.koito_incident([self.older, self.success])
        self.invoke(issues=[issue], endpoints=[endpoint], observations=self.workload_fixture())
        self.assertEqual(self.calls, [('github/issues/1', 'PATCH', {'state': 'closed', 'state_reason': 'completed'})])
        self.assertEqual(self.reads, [])

    def test_unknown_endpoints_are_not_queried_or_enriched(self):
        issue, endpoint = self.koito_incident()
        issue['title'] = 'alert(gatus): internal/unmapped'
        endpoint['name'] = 'unmapped'
        self.invoke(issues=[issue], endpoints=[endpoint])
        self.assertEqual(self.reads, [])
        self.assertEqual(self.calls, [])

    def test_flux_ready_false_stale_and_unavailable_are_distinguished_safely(self):
        target = ('flux-system', 'kustomizations', 'apps-koito')
        obj = {'metadata': {'generation': 2}, 'spec': {}, 'status': {'conditions': [
            {'type': 'Ready', 'status': 'False', 'observedGeneration': 2, 'reason': 'HealthCheckFailed', 'message': 'SECRET'}]}}
        read = lambda value: SimpleNamespace(get=lambda path: value)
        line, state = self.helper['flux_observation'](read(obj), target)
        self.assertEqual(state, 'False')
        self.assertIn('HealthCheckFailed', line)
        self.assertNotIn('SECRET', line)
        obj['status']['conditions'][0]['observedGeneration'] = 1
        self.assertEqual(self.helper['flux_observation'](read(obj), target)[1], 'stale/unknown')
        self.assertEqual(self.helper['flux_observation'](read(None), target)[1], 'unavailable')

    def test_assessment_report_freshness_and_unavailability_are_sanitized(self):
        issue = {**self.issue, 'title': 'alert(gatus): cluster/kubescape-assessments'}
        endpoint = {'name': 'kubescape-assessments', 'group': 'cluster', 'results': []}
        health = {'observedAt': self.now.isoformat(), 'private': 'SECRET',
                  'vulnerability': {'evidenceAgeSeconds': 259201, 'lastAttemptState': 'failed', 'message': 'SECRET'},
                  'posture': {'evidenceAgeSeconds': 50, 'lastAttemptState': 'healthy'}}
        for assessment in (health, {**health, 'observedAt': '2000-01-01T00:00:00Z'}, OSError('SECRET'), None):
            self.invoke(issues=[issue], endpoints=[endpoint], assessment=assessment)
            body = self.calls.pop()[2]['body']
            self.assertNotIn('SECRET', body)
            self.assert_no_closure()
            if assessment is health:
                self.assertIn('vulnerability: evidence=stale; last attempt=failed', body)
                self.assertIn('posture: evidence=fresh', body)
                self.assertIn('database: evidence=unknown', body)
            else:
                self.assertIn('Assessment report: stale/unavailable', body)

    def test_kubernetes_read_budget_and_cache(self):
        reader = self.helper['KubernetesReader']()
        reader.remaining = 0
        with patch('urllib.request.build_opener') as opener:
            self.assertIsNone(reader.get('/api/v1/namespaces/koito/pods'))
            self.assertIsNone(reader.get('/api/v1/namespaces/koito/pods'))
        opener.assert_not_called()
        self.assertEqual(len(reader.cache), 1)

    def test_kubernetes_transport_uses_separate_credentials_tls_bounds_and_no_redirects(self):
        reader = self.helper['KubernetesReader']()
        response = MagicMock()
        response.read.return_value = b'{"status": {}}'
        with patch('builtins.open', mock_open(read_data='KUBERNETES_FIXTURE_TOKEN')), \
             patch('ssl.create_default_context') as tls, patch('urllib.request.build_opener') as build:
            opener = build.return_value
            opener.open.return_value.__enter__.return_value = response
            path = '/apis/apps/v1/namespaces/koito/deployments/koito'
            self.assertEqual(reader.get(path), {'status': {}})
            self.assertEqual(reader.get(path), {'status': {}})
            opener.open.assert_called_once()
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, 'https://kubernetes.default.svc' + path)
            self.assertEqual(request.get_header('Authorization'), 'Bearer KUBERNETES_FIXTURE_TOKEN')
            self.assertNotIn('fixture', request.get_header('Authorization'))
            self.assertEqual(opener.open.call_args.kwargs['timeout'], 5)
            tls.assert_called_once_with(cafile='/var/run/secrets/kubernetes.io/serviceaccount/ca.crt')
            response.read.assert_called_once_with(2 * 1024 * 1024 + 1)
            redirect_handler = build.call_args.args[0]()
            self.assertIsNone(redirect_handler.redirect_request(None, None, 302, None, None, 'https://untrusted.invalid'))

    def test_denied_kubernetes_transport_is_cached_as_unavailable_without_output(self):
        reader = self.helper['KubernetesReader']()
        with patch('builtins.open', mock_open(read_data='KUBERNETES_FIXTURE_TOKEN')), \
             patch('ssl.create_default_context'), patch('urllib.request.build_opener') as build, \
             redirect_stdout(io.StringIO()) as output:
            build.return_value.open.side_effect = OSError('403 SECRET')
            self.assertIsNone(reader.get('/denied'))
            self.assertIsNone(reader.get('/denied'))
            build.return_value.open.assert_called_once()
        self.assertEqual(output.getvalue(), '')

    def test_cluster_diagnostics_reject_arbitrary_single_line_text(self):
        self.health['failures']['workloads'] += ['TOKEN=secret', 'koito/bridge desired=secret available=0']
        self.invoke()
        self.assertNotIn('secret', self.calls[0][2]['body'])


if __name__ == '__main__':
    unittest.main()
