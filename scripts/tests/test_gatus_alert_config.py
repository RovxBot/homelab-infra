"""Keep the native GitHub alert and helper recovery contracts aligned."""
from pathlib import Path
import unittest

import yaml
from scripts.ci.image_reference import release_tag


ROOT = Path(__file__).resolve().parents[2]


class GatusAlertConfigTests(unittest.TestCase):
    def sync_helper(self):
        source = yaml.safe_load((ROOT / 'infra/observability/gatus-github-issue-sync-configmap.yaml').read_text())['data']['sync.py']
        helper = {'__name__': 'gatus_config_test'}
        exec(compile(source, 'sync.py', 'exec'), helper)
        return helper

    def test_allowlisted_endpoints_match_services_workloads_and_flux_manifests(self):
        targets = self.sync_helper()['TARGETS']
        release = yaml.safe_load((ROOT / 'infra/observability/gatus-helmrelease.yaml').read_text())
        endpoints = {f"alert(gatus): {item['group']}/{item['name']}": item
                     for item in release['spec']['values']['config']['endpoints']}
        self.assertEqual(set(targets), {'alert(gatus): internal/gitea', 'alert(gatus): internal/homepage',
                                       'alert(gatus): internal/koito', 'alert(gatus): cluster/kubescape-assessments'})
        sources = [('apps/homepage/deployment.yaml', 'homepage'), ('apps/koito/deployment.yaml', 'koito'),
                   ('apps/gitea/database.yaml', 'gitea'), ('infra/pod-report/deployment.yaml', 'homepage')]
        workloads = {}
        for path, namespace in sources:
            for obj in yaml.safe_load_all((ROOT / path).read_text()):
                if obj['kind'] == 'Deployment':
                    workloads[(namespace, 'deployments', obj['metadata']['name'])] = obj
        flux_sources = ['clusters/home/apps/gitea-kustomization.yaml', 'clusters/home/apps/homepage-kustomization.yaml',
                        'clusters/home/apps/koito-kustomization.yaml',
                        'clusters/home/infrastructure/infrastructure-kustomization.yaml',
                        'clusters/home/infrastructure/kubescape-kustomization.yaml',
                        'apps/gitea/helmrelease.yaml', 'infra/kubescape/helmrelease.yaml']
        flux = set()
        for path in flux_sources:
            obj = yaml.safe_load((ROOT / path).read_text())
            namespace = obj['metadata'].get('namespace', 'gitea')
            flux.add((namespace, {'Kustomization': 'kustomizations', 'HelmRelease': 'helmreleases'}[obj['kind']], obj['metadata']['name']))
        for title, target in targets.items():
            self.assertIn(title, endpoints)
            for namespace, resource, name, selector in target['workloads']:
                key = (namespace, resource, name)
                if key in workloads:
                    expected = dict(item.split('=', 1) for item in selector.split(','))
                    self.assertEqual(expected, workloads[key]['spec']['selector']['matchLabels'])
                else:
                    # These names/selectors are also checked against the pinned
                    # chart renders during focused validation, without live API reads.
                    self.assertIn(key, {('gitea', 'deployments', 'gitea')} |
                                  {('kubescape', 'deployments', n) for n in ('kubescape', 'kubevuln', 'operator', 'storage', 'grype-offline-db')})
            for obj in target['flux']:
                self.assertIn(obj, flux)
        self.assertIn('gitea-http.gitea.svc', endpoints['alert(gatus): internal/gitea']['url'])
        self.assertIn('homepage.homepage.svc', endpoints['alert(gatus): internal/homepage']['url'])
        self.assertIn('koito-web.koito.svc', endpoints['alert(gatus): internal/koito']['url'])
        self.assertIn('pod-report.homepage.svc', endpoints['alert(gatus): cluster/kubescape-assessments']['url'])
        for title, path in [('alert(gatus): internal/homepage', 'apps/homepage/service.yaml'),
                            ('alert(gatus): internal/koito', 'apps/koito/service.yaml'),
                            ('alert(gatus): cluster/kubescape-assessments', 'infra/pod-report/service.yaml')]:
            ns, resource, name, _ = targets[title]['workloads'][0]
            services = list(yaml.safe_load_all((ROOT / path).read_text()))
            service = next(obj for obj in services if obj['kind'] == 'Service'
                           and obj['metadata']['name'] + '.' + ns + '.svc' in endpoints[title]['url'])
            self.assertIn(service['metadata']['name'] + '.' + ns + '.svc', endpoints[title]['url'])
            labels = workloads[(ns, resource, name)]['spec']['template']['metadata']['labels']
            self.assertTrue(all(labels[key] == value for key, value in service['spec']['selector'].items()))

    def test_rbac_exactly_covers_mapping_without_mutation_or_secret_access(self):
        targets = self.sync_helper()['TARGETS']
        docs = list(yaml.safe_load_all((ROOT / 'infra/observability/gatus-github-issue-sync-rbac.yaml').read_text()))
        self.assertEqual({doc['kind'] for doc in docs}, {'ServiceAccount', 'Role', 'RoleBinding'})
        roles = {doc['metadata']['namespace']: doc for doc in docs if doc['kind'] == 'Role'}
        expected = {}
        for target in targets.values():
            for namespace, resource, name, _ in target['workloads']:
                expected.setdefault((namespace, resource), set()).add(name)
            for namespace, resource, name in target['flux']:
                expected.setdefault((namespace, resource), set()).add(name)
        actual = {}
        for namespace, role in roles.items():
            for rule in role['rules']:
                if rule['resources'] == ['pods', 'events']:
                    self.assertIn(namespace, {'gitea', 'homepage', 'koito', 'kubescape'})
                    self.assertEqual(rule['verbs'], ['list'])
                    self.assertEqual(rule['apiGroups'], [''])
                else:
                    self.assertEqual(rule['verbs'], ['get'])
                    self.assertEqual(len(rule['resources']), 1)
                    actual[(namespace, rule['resources'][0])] = set(rule['resourceNames'])
        self.assertEqual(actual, expected)
        for binding in (doc for doc in docs if doc['kind'] == 'RoleBinding'):
            self.assertEqual(binding['subjects'], [{'kind': 'ServiceAccount', 'name': 'gatus-github-issue-sync', 'namespace': 'monitoring'}])
            self.assertEqual(binding['roleRef']['kind'], 'Role')
        job = yaml.safe_load((ROOT / 'infra/observability/gatus-github-issue-sync-cronjob.yaml').read_text())
        spec = job['spec']['jobTemplate']['spec']
        self.assertLessEqual(spec['activeDeadlineSeconds'], 240)
        self.assertEqual(spec['template']['spec']['serviceAccountName'], 'gatus-github-issue-sync')
        self.assertTrue(spec['template']['spec']['automountServiceAccountToken'])

    def test_only_cluster_health_overrides_recovery_and_preserves_detection(self):
        release = yaml.safe_load((ROOT / 'infra/observability/gatus-helmrelease.yaml').read_text())
        values = release['spec']['values']
        # Native GitHub alert/recovery behavior was qualified on 5.34.0.
        release_tag(values['image']['tag'], minimum=(5, 34, 0), release_line=(5,))
        self.assertRegex(values['image']['sha'], r'^[a-f0-9]{64}$')
        config = values['config']
        defaults = config['alerting']['github']['default-alert']
        self.assertEqual(defaults['failure-threshold'], 3)
        self.assertEqual(defaults['success-threshold'], 2)
        self.assertIs(defaults['send-on-resolved'], True)
        for endpoint in config['endpoints']:
            with self.subTest(endpoint=endpoint['name']):
                if endpoint['name'] == 'cluster-health' and endpoint['group'] == 'cluster':
                    self.assertEqual(endpoint['interval'], '1m')
                    self.assertEqual(endpoint['conditions'], ['[STATUS] == 200', '[BODY].status == healthy'])
                    self.assertEqual(endpoint['alerts'], [{'type': 'github', 'failure-threshold': 3,
                                                         'success-threshold': 20, 'send-on-resolved': True}])
                elif endpoint['name'] in ('security-collector-process', 'security-collector-sources'):
                    self.assertEqual(endpoint['alerts'], [])
                else:
                    self.assertEqual(endpoint['alerts'], [{'type': 'github'}])
        self.assertEqual(config['storage'], {'type': 'sqlite', 'path': '/data/data.db'})

    def test_security_reporting_endpoint(self):
        release = yaml.safe_load((ROOT / 'infra/observability/gatus-helmrelease.yaml').read_text())
        endpoint, = [ep for ep in release['spec']['values']['config']['endpoints'] if ep['name'] == 'security-reporting']
        self.assertEqual(endpoint, {'name':'security-reporting','group':'cluster',
            'url':'http://pod-report.homepage.svc.cluster.local:80/security-reporting-health.json',
            'interval':'1m','conditions':['[STATUS] == 200','[BODY].status == healthy'],
            'alerts':[{'type':'github'}]})

    def test_cve_reporting_has_independent_health_condition(self):
        release = yaml.safe_load((ROOT / 'infra/observability/gatus-helmrelease.yaml').read_text())
        endpoint, = [ep for ep in release['spec']['values']['config']['endpoints'] if ep['name'] == 'security-cve-reporting']
        self.assertEqual(endpoint, {'name':'security-cve-reporting','group':'cluster',
            'url':'http://pod-report.homepage.svc.cluster.local:80/security-reporting-health.json',
            'interval':'1m','conditions':['[STATUS] == 200','[BODY].cvePublisher == healthy'],
            'alerts':[{'type':'github'}]})

    def test_security_receiver_transport_is_static_and_payload_free(self):
        release = yaml.safe_load((ROOT / 'infra/observability/gatus-helmrelease.yaml').read_text())
        endpoints = release['spec']['values']['config']['endpoints']
        receiver, = (ep for ep in endpoints if ep['name'] == 'security-receiver-transport')
        self.assertEqual(receiver, {
            'name': 'security-receiver-transport',
            'group': 'cluster',
            'url': 'tcp://security-receiver.kubescape.svc.cluster.local:8443',
            'interval': '1m',
            'conditions': ['[CONNECTED] == true'],
            'alerts': [{'type': 'github'}],
        })
        self.assertNotIn('Authorization', str(receiver))
        self.assertNotIn('runtimealerts', str(receiver))

    def test_cronjob_policy_matches_native_recovery_and_available_history(self):
        cronjob = yaml.safe_load((ROOT / 'infra/observability/gatus-github-issue-sync-cronjob.yaml').read_text())
        container = cronjob['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]
        env = {entry['name']: entry.get('value') for entry in container['env']}
        self.assertEqual(env['SUCCESS_THRESHOLD'], '2')
        self.assertEqual(env['MAX_RESULT_AGE_SECONDS'], '900')
        self.assertEqual(env['CLUSTER_SUCCESS_THRESHOLD'], '20')
        self.assertEqual(env['CLUSTER_RECOVERY_MIN_SECONDS'], str(19 * 60))
        self.assertEqual(env['CLUSTER_MAX_SAMPLE_GAP_SECONDS'], '90')
        self.assertEqual(env['CLUSTER_MAX_RESULT_AGE_SECONDS'], '120')
        self.assertEqual(env['CLUSTER_MAX_HEALTH_AGE_SECONDS'], '300')
        # v5.34.0's default SQLite status response contains the latest 100 results.
        self.assertLessEqual(int(env['CLUSTER_SUCCESS_THRESHOLD']), 100)


if __name__ == '__main__':
    unittest.main()
