"""Run pod-report's actual assessment health function with bounded API fixtures."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 22, 8, tzinfo=timezone.utc)


def stamp(hours):
    return (NOW - timedelta(hours=hours)).isoformat().replace('+00:00', 'Z')


class AssessmentHealthTests(unittest.TestCase):
    def health(self, values=None, error=False):
        source = yaml.safe_load((ROOT / 'infra/pod-report/deployment.yaml').read_text())
        source = source['spec']['template']['spec']['containers'][0]['args'][0]
        source = source.split('while true; do', 1)[0]
        # Load the deployed functions; no cluster health or inventory calls run.
        source = source[source.index('assessment_health() {'):]
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            kubectl = root / 'kubectl'
            kubectl.write_text('#!/bin/sh\nprintf "%s" "$ASSESSMENT_FIXTURE"\nexit "$ASSESSMENT_ERROR"\n')
            kubectl.chmod(0o755)
            date = root / 'date'
            date.write_text('''#!/usr/bin/env python3
from datetime import datetime, timezone
import sys
try:
    args = sys.argv[1:]
    value = datetime.fromisoformat(args[args.index('-d') + 1].replace('Z', '+00:00')) if '-d' in args else datetime.fromtimestamp(1795334400, timezone.utc)
    print(int(value.timestamp()) if args[-1] == '+%s' else value.strftime('%Y-%m-%dT%H:%M:%SZ'))
except Exception:
    sys.exit(1)
''')
            date.chmod(0o755)
            values = values or [stamp(24), stamp(24), 'healthy', 'healthy', stamp(24)]
            if len(values) == 5:
                values = [*values, 'healthy', stamp(-1), '9', '9']
            if len(values) == 9:
                values = [*values, '5']
            result = subprocess.run(['sh', '-c', 'set -eu\n' + source.replace('/work/', str(root) + '/') + '\nassessment_health\n'],
                                    env=dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'],
                                             ASSESSMENT_FIXTURE='|'.join(values), ASSESSMENT_ERROR=str(int(error))),
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads((root / 'kubescape-health.json').read_text())

    def test_recent_success_ages_and_grace_are_reported(self):
        result = self.health([stamp(24), stamp(24), 'healthy', 'healthy', stamp(9 * 24)])
        self.assertEqual(result['status'], 'healthy')
        self.assertEqual(result['vulnerability']['evidenceAgeSeconds'], 86400)
        self.assertEqual(result['vulnerability']['lastAttemptState'], 'healthy')
        self.assertEqual(result['databaseFreshness'], 'grace')
        self.assertEqual(result['database']['evidenceAgeSeconds'], 9 * 86400)

    def test_latest_failed_unknown_or_stale_attempt_overrides_cached_success(self):
        for mode, index in (('vulnerability', 2), ('posture', 3)):
            for state in ('failed', 'unknown', 'stale'):
                with self.subTest(mode=mode, state=state):
                    values = [stamp(24), stamp(24), 'healthy', 'healthy', stamp(24)]
                    values[index] = state
                    result = self.health(values)
                    self.assertEqual(result['status'], state)
                    self.assertEqual(result[mode]['state'], state)
                    self.assertEqual(result[mode]['lastAttemptState'], state)
                    self.assertEqual(result[mode]['evidenceAgeSeconds'], 86400)

    def test_modes_expire_independently_and_future_is_unknown(self):
        for values, mode, state in [
            ([stamp(73), stamp(24), 'healthy', 'healthy', stamp(24)], 'vulnerability', 'stale'),
            ([stamp(24), stamp(27), 'healthy', 'healthy', stamp(24)], 'posture', 'stale'),
            ([stamp(-1), stamp(24), 'healthy', 'healthy', stamp(24)], 'vulnerability', 'unknown'),
            ([stamp(24), stamp(24), 'healthy', 'healthy', stamp(15 * 24)], 'database', 'stale'),
        ]:
            with self.subTest(mode=mode, state=state):
                result = self.health(values)
                self.assertEqual(result['status'], state)
                self.assertEqual(result[mode]['state'], state)

    def test_cohort_health_requires_all_targets_and_fresh_evidence(self):
        base = [stamp(24), stamp(24), 'healthy', 'healthy', stamp(24)]
        for fields, expected in (
                (['healthy', stamp(-1), '9', '9'], 'healthy'),
                (['healthy', stamp(-1), '6', '6'], 'unknown'),
                (['unknown', stamp(-1), '9', '8'], 'unknown'),
                (['healthy', stamp(1), '9', '9'], 'stale'),
                (['healthy', stamp(-1), '9', '10'], 'unknown'),
                (['healthy', stamp(-1), 'private', '9'], 'unknown'),
                (['', '', '', ''], 'unknown')):
            with self.subTest(fields=fields):
                result = self.health(base + fields)
                self.assertEqual(result['status'], expected)
                self.assertEqual(result['coverage']['state'], expected)
                self.assertNotIn('private', json.dumps(result))

    def test_previous_canary_posture_cannot_pass_expanded_scope(self):
        base = [stamp(24), stamp(24), 'healthy', 'healthy', stamp(24),
                'healthy', stamp(-1), '9', '9']
        for scope in ('', '1', '4', '6', 'private'):
            with self.subTest(scope=scope):
                result = self.health(base + [scope])
                self.assertEqual(result['posture']['state'], 'unknown')
                self.assertEqual(result['status'], 'unknown')

    def test_missing_failed_and_untrusted_api_data_stays_minimal(self):
        for error in (False, True):
            result = self.health(['raw-vulnerability-secret', '', 'unsafe', '', 'invalid'], error)
            self.assertEqual(result['status'], 'unknown')
            self.assertIsNone(result['vulnerability']['evidenceAgeSeconds'])
            self.assertNotIn('raw-vulnerability-secret', json.dumps(result))
            self.assertNotIn('unsafe', json.dumps(result))

    def reporting(self, native, policy, controllers, error=False, pods=None):
        source = yaml.safe_load((ROOT / 'infra/pod-report/deployment.yaml').read_text())
        source = source['spec']['template']['spec']['containers'][0]['args'][0]
        source = source[source.index('reporter_health() {'):].split('while true; do', 1)[0]
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            kubectl = root / 'kubectl'
            kubectl.write_text("""#!/bin/sh
case "$*" in
  *"get jobs -n monitoring"*) printf '%s\\n' "$NATIVE" ;;
  *"get pods -n monitoring"*) printf '%s\\n' "$PUBLISHER_PODS" ;;
  *"get jobs -n kyverno"*) printf '%s\\n' "$POLICY" ;;
  *"get deployments -n kyverno"*) printf '%s\\n' "$CONTROLLERS" ;;
  *) exit 1 ;;
esac
exit "$API_ERROR"
""")
            kubectl.chmod(0o755)
            date = root / 'date'
            date.write_text("""#!/usr/bin/env python3
from datetime import datetime, timezone
import sys
args = sys.argv[1:]
value = datetime.fromisoformat(args[args.index('-d')+1].replace('Z','+00:00')) if '-d' in args else datetime(2026,11,22,8,tzinfo=timezone.utc)
print(int(value.timestamp()) if args[-1] == '+%s' else value.strftime('%Y-%m-%dT%H:%M:%SZ'))
""")
            date.chmod(0o755)
            result = subprocess.run(['sh','-c','set -eu\n'+source.replace('/work/',str(root)+'/')+'\nsecurity_reporting_health\n'],
                                    env=dict(os.environ, PATH=str(root)+':'+os.environ['PATH'],
                                             NATIVE=native, POLICY=policy, CONTROLLERS=controllers, API_ERROR=str(int(error)),
                                             PUBLISHER_PODS=pods if pods is not None else 'Job/security-incident-publisher-1|uid-1|0|security-v1|healthy|healthy'),
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            return json.loads((root/'security-reporting-health.json').read_text())

    def test_security_reporting_latest_attempt_and_freshness(self):
        native = '2026-11-22T07:50:00Z|security-incident-publisher-1|uid-1|CronJob/security-incident-publisher|True|'
        policy = '2026-11-22T07:15:00Z|CronJob/kyverno-github-issue-sync|True||2026-11-22T07:15:30Z'
        controllers = '\n'.join('kyverno-'+name+'-controller|3|3|'+str(count)+'|'+str(count)
                               for name,count in [('admission',2),('reports',1),('background',1),('cleanup',1)])
        self.assertEqual(self.reporting(native,policy,controllers)['status'],'healthy')
        failed = '2026-11-22T07:59:00Z|security-incident-publisher-2|uid-2|CronJob/security-incident-publisher||True'
        active = '2026-11-22T07:59:00Z|security-incident-publisher-2|uid-2|CronJob/security-incident-publisher||'
        failed_pod = 'Job/security-incident-publisher-2|uid-2|1|security-v1|failed|healthy'
        self.assertEqual(self.reporting(native+'\n'+failed,policy,controllers,pods=failed_pod)['nativePublisher'],'failed')
        partial = self.reporting(native+'\n'+failed,policy,controllers,
                                 pods=failed_pod.replace('|failed|healthy','|healthy|failed'))
        self.assertEqual(partial['status'],'healthy')
        self.assertEqual(partial['cvePublisher'],'failed')
        for malformed in (failed_pod.replace('uid-2','wrong'), failed_pod+'\n'+failed_pod,
                          failed_pod.replace('|failed|healthy','|private|healthy'), ''):
            result=self.reporting(failed,policy,controllers,pods=malformed)
            self.assertEqual(result['nativePublisher'],'unknown')
            self.assertEqual(result['cvePublisher'],'unknown')
            self.assertNotIn('private',json.dumps(result))
        self.assertEqual(self.reporting(native+'\n'+active,policy,controllers)['nativePublisher'],'unknown')
        self.assertEqual(self.reporting('',policy,controllers)['nativePublisher'],'unknown')
        stale = native.replace('07:50','07:20')
        self.assertEqual(self.reporting(stale,policy,controllers)['nativePublisher'],'stale')
        self.assertEqual(self.reporting(native,policy,controllers.replace('admission-controller|3|3|2|2','admission-controller|3|3|2|1'))['kyvernoControllers'],'failed')
        self.assertEqual(self.reporting(native,policy,controllers,True)['status'],'unhealthy')
        self.assertEqual(self.reporting(native,policy,'')['kyvernoControllers'],'unknown')

    def test_reader_can_get_only_the_named_status_configmap(self):
        resources = list(yaml.safe_load_all((ROOT / 'infra/kubescape/recurring/rbac.yaml').read_text()))
        role = next(r for r in resources if r['kind'] == 'Role' and r['metadata']['name'] == 'pod-report-assessment-status')
        self.assertEqual(role['metadata']['namespace'], 'kubescape')
        self.assertEqual(role['rules'], [{'apiGroups': [''], 'resources': ['configmaps'],
                                         'resourceNames': ['kubescape-assessment-status'], 'verbs': ['get']}])
        binding = next(r for r in resources if r['kind'] == 'RoleBinding' and r['metadata']['name'] == role['metadata']['name'])
        self.assertEqual(binding['subjects'], [{'kind': 'ServiceAccount', 'name': 'pod-report', 'namespace': 'homepage'}])


if __name__ == '__main__':
    unittest.main()
