"""Incident enrichment and closure use actual endpoint evidence only."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch

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

    def invoke(self, endpoints=None, health=None, issues=None):
        health = self.health if health is None else health
        endpoints = endpoints if endpoints is not None else [{'name':'cluster-health','group':'cluster','results':[self.older,self.success]}]
        issues = issues if issues is not None else [self.issue]
        def request(url, method='GET', payload=None, headers=None):
            if method != 'GET':
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
            if '/issues?' in url:
                self.assertIn('creator=fixture', url)
                self.assertIn('state=open', url)
                return issues
            raise AssertionError(url)
        self.helper['api_request'] = request
        with redirect_stdout(io.StringIO()):
            self.assertEqual(self.helper['main'](),0)

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
        self.invoke(endpoints=[self.cluster_endpoint(self.recovery_samples())], health=self.healthy_collector())
        self.assertEqual(self.calls,[('github/issues/1','PATCH',{'state':'closed','state_reason':'completed'})])

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


if __name__ == '__main__':
    unittest.main()
