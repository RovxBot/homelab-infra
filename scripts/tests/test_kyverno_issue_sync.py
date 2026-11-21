"""Migration must retain incident history and require affirmative recovery evidence."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]


class ReportMigrationTests(unittest.TestCase):
    def setUp(self):
        source=(ROOT/'infra/kyverno/automation/github-issue-sync-configmap.yaml').read_text().split('  sync.py: |\n')[1]
        self.h={'__name__':'test_sync'}
        exec(compile('\n'.join(l[4:] for l in source.splitlines()),'sync.py','exec'),self.h)
        self.now=datetime.now(timezone.utc)
        self.h.update(IGNORED_SCOPE_KINDS={'Pod','ReplicaSet'},IGNORED_POLICIES={'platform-flux-guardrails'},EXCLUDE_NAMESPACE_RE=None,
                      CUTOFF=self.now-timedelta(hours=24),GITHUB_OWNER='fixture',GITHUB_REPO='fixture',GITHUB_TOKEN='fixture',
                      ISSUE_TITLE_PREFIX='[kyverno] ',ISSUE_LABEL='kyverno-policy',DRY_RUN=False)
        self.legacy=dict(source='kyverno',policy='platform-pod-standards',rule='require-owner-label',result='fail',timestamp={'seconds':int((self.now-timedelta(days=2)).timestamp())})
        self.cel={**self.legacy,'source':'KyvernoValidatingPolicy','policy':'platform-pod-standards-require-owner-label','rule':'validation'}
        self.scope=dict(kind='Deployment',namespace='app',name='app')
        self.reports=[];self.issues={};self.writes=[]
        self.h['list_kubernetes_resource']=lambda path:{'items':self.reports} if path.endswith('/policyreports') else {'items':[]}
        self.h['list_open_issues']=lambda:self.issues
        self.h['api_request']=lambda url,**kw:self.writes.append((url,kw))

    def report(self,result):
        return dict(scope=self.scope,metadata=dict(namespace='app',creationTimestamp=self.now.isoformat()),results=[deepcopy(result)])

    def issue(self,cel=False):
        title='[kyverno] '+('platform-pod-standards-require-owner-label/validation' if cel else 'platform-pod-standards/require-owner-label')+' on Deployment app/app'
        self.issues[title]=dict(title=title,url='github/issues/42',created_at=(self.now-timedelta(days=3)).isoformat(),body='Incident history')

    def invoke(self):
        with redirect_stdout(io.StringIO()): return self.h['main']()

    def test_formats_sources_and_rule_variants_have_one_identity(self):
        for rule in [None,'','validation']:
            result={**self.cel,'rule':rule}
            self.assertEqual(self.h['canonical_identity'](result),('platform-pod-standards','require-owner-label'))
        self.reports=[self.report(self.legacy),self.report(self.cel)]
        self.assertEqual(len(self.h['load_failures']()),1)

    def test_overlap_and_identifier_change_preserve_original_incident(self):
        self.issue()
        self.reports=[self.report(self.cel),self.report(self.legacy)]
        self.assertEqual(self.invoke(),0);self.assertEqual(self.writes,[])
        self.reports=[self.report(self.cel)]
        self.assertEqual(self.invoke(),0);self.assertEqual(self.writes,[])

    def test_existing_cel_title_correlates_without_replacement(self):
        self.issue(cel=True);self.reports=[self.report(self.legacy)]
        self.assertEqual(self.invoke(),0);self.assertEqual(self.writes,[])

    def test_audit_and_enforce_failures_are_findings(self):
        for mode in ['Audit','Enforce']:
            self.reports=[self.report({**self.cel,'properties':{'validationActions':mode}})]
            self.assertEqual(len(self.h['load_failures']()),1)

    def test_exception_skip_preserves_incident(self):
        self.issue();self.reports=[self.report({**self.cel,'result':'skip','properties':{'exception':'home-assistant'}})]
        self.assertEqual(self.invoke(),0);self.assertEqual(self.writes,[])

    def test_young_failure_does_not_close_old_incident(self):
        self.issue();self.reports=[self.report({**self.cel,'timestamp':self.now.isoformat()})]
        self.assertEqual(self.invoke(),0);self.assertEqual(self.writes,[])

    def test_fresh_explicit_pass_closes_existing_issue_in_place(self):
        self.issue();self.reports=[self.report({**self.cel,'result':'pass','timestamp':self.now.isoformat()})]
        self.assertEqual(self.invoke(),0)
        self.assertEqual(self.writes,[('github/issues/42',dict(method='PATCH',payload={'state':'closed'},headers=self.h['github_headers']()))])

    def test_absence_stale_future_or_unstamped_pass_is_not_recovery(self):
        self.issue()
        for stamp in [None,(self.now-timedelta(hours=1)).isoformat(),(self.now+timedelta(hours=1)).isoformat()]:
            result={**self.cel,'result':'pass','timestamp':stamp}
            self.reports=[self.report(result)]
            self.assertEqual(self.invoke(),0);self.assertEqual(self.writes,[])
        self.reports=[];self.assertEqual(self.invoke(),0);self.assertEqual(self.writes,[])

    def test_fail_or_error_dominates_pass_during_overlap(self):
        self.issue()
        for status in ['fail','error','skip']:
            self.reports=[self.report({**self.cel,'result':'pass','timestamp':self.now.isoformat()}),self.report({**self.legacy,'result':status})]
            self.assertEqual(self.invoke(),0);self.assertEqual(self.writes,[])

    def test_collection_failure_is_unknown_with_zero_writes(self):
        self.issue()
        def fail(path): raise OSError('fixture failure')
        self.h['list_kubernetes_resource']=fail
        self.assertEqual(self.invoke(),1);self.assertEqual(self.writes,[])

    def test_unmapped_cel_identity_is_unknown_not_lost(self):
        self.issue();self.reports=[self.report({**self.cel,'policy':'unknown-cel-policy'})]
        self.assertEqual(self.invoke(),1);self.assertEqual(self.writes,[])

    def test_namespaced_runner_identity(self):
        self.assertEqual(self.h['canonical_identity'](dict(source='KyvernoNamespacedValidatingPolicy',policy='gitea-jobs/gitea-job-security-one-job-container',rule='validation')),('gitea-job-security','one-job-container'))

    def test_kubernetes_collection_follows_continuation(self):
        self.h.update(KUBE_API='https://kube',KUBE_HEADERS={},KUBE_SSL_CONTEXT=None)
        pages=iter([dict(items=[{'first':True}],metadata={'continue':'next'}),dict(items=[{'second':True}])])
        self.h['api_get']=lambda *a,**kw:next(pages)
        # Restore the implementation rather than the per-test fixture.
        source=(ROOT/'infra/kyverno/automation/github-issue-sync-configmap.yaml').read_text().split('  sync.py: |\n')[1]
        h={'__name__':'test_sync'};exec(compile('\n'.join(l[4:] for l in source.splitlines()),'sync.py','exec'),h)
        self.h['list_kubernetes_resource']=type(h['list_kubernetes_resource'])(h['list_kubernetes_resource'].__code__,self.h)
        self.assertEqual(len(self.h['list_kubernetes_resource']('/reports')['items']),2)

    def test_rbac_reads_reports_without_policy_or_secret_privileges(self):
        import yaml
        docs=list(yaml.safe_load_all((ROOT/'infra/kyverno/automation/github-issue-sync-rbac.yaml').read_text()))
        self.assertEqual(docs[1]['rules'],[dict(apiGroups=['wgpolicyk8s.io'],resources=['clusterpolicyreports','policyreports'],verbs=['get','list','watch'])])


if __name__=='__main__': unittest.main()
