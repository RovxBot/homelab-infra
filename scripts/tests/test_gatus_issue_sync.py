"""Incident enrichment and closure use actual endpoint evidence only."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]


class GatusIssueTests(unittest.TestCase):
    def setUp(self):
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

    def invoke(self, endpoints=None, health=None, issues=None):
        health = self.health if health is None else health
        endpoints = endpoints if endpoints is not None else [{'name':'cluster-health','group':'cluster','results':[self.older,self.success]}]
        issues = issues if issues is not None else [self.issue]
        def request(url, method='GET', payload=None, headers=None):
            if method != 'GET':
                self.calls.append((url,method,payload))
                return {}
            if url == 'gatus-status': return endpoints
            if url == 'cluster-health':
                self.assertIsNone(headers)  # Never send GitHub credentials here.
                if isinstance(health, Exception): raise health
                return health
            if url == 'github/user': return {'login':'fixture'}
            if '/issues?' in url: return issues
            raise AssertionError(url)
        self.helper['api_request'] = request
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
        self.health['status']='healthy'
        self.health['checks']['workloads']='ok'
        self.invoke()
        self.assertEqual(self.calls,[('github/issues/1','PATCH',{'state':'closed','state_reason':'completed'})])

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
        self.assertEqual(self.calls,[])


if __name__ == '__main__':
    unittest.main()
