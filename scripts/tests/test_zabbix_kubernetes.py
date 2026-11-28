"""Security contracts for the bounded Zabbix cluster-state integration."""
import importlib.util
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('profile', ROOT / 'scripts/zabbix/build_kubernetes_profile.py')
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class KubernetesMonitoringContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source, cls.release = yaml.safe_load_all((ROOT / 'infra/zabbix/kubernetes/collectors.yaml').read_text())
        cls.values = cls.release['spec']['values']

    def test_no_unqualified_host_collector_or_stack(self):
        self.assertEqual(self.source['spec']['url'], 'oci://ghcr.io/prometheus-community/charts/kube-state-metrics')
        self.assertFalse(self.values['hostNetwork'])
        self.assertFalse(self.values['kubeRBACProxy']['enabled'])
        self.assertFalse(self.values['prometheus']['monitor']['enabled'])
        self.assertTrue(all('hostPath' not in v for v in self.values['volumes']))
        self.assertEqual(self.values['replicas'], 1)
        self.assertEqual(self.values['updateStrategy'], 'Recreate')

    def test_api_identity_has_only_health_access(self):
        docs = list(yaml.safe_load_all((ROOT / 'infra/zabbix/kubernetes/api-reader.yaml').read_text()))
        self.assertFalse(docs[0]['automountServiceAccountToken'])
        self.assertEqual(docs[1]['rules'], [{'nonResourceURLs': ['/livez', '/readyz'], 'verbs': ['get']}])
        self.assertEqual(docs[2]['subjects'][0]['name'], docs[0]['metadata']['name'])

    def test_collectors_cannot_read_secrets_or_configs(self):
        self.assertNotIn('secrets', self.values['collectors'])
        self.assertNotIn('configmaps', self.values['collectors'])
        self.assertEqual(self.values['rbac']['extraRules'], [])
        self.assertEqual(self.values['metricLabelsAllowlist'], [])
        self.assertEqual(self.values['metricAnnotationsAllowList'], [])

    def test_tls_material_is_encrypted_and_scoped(self):
        doc = yaml.safe_load((ROOT / 'secrets/zabbix-kubernetes-tls.enc.yaml').read_text())
        self.assertTrue(all(v.startswith('ENC[AES256_GCM,') for v in doc['stringData'].values()))
        self.assertEqual({i['key'] for i in self.values['volumes'][0]['secret']['items']}, {'tls.crt', 'tls.key', 'web.yaml'})
        self.assertIn('--tls-config=/etc/ksm-tls/web.yaml', self.values['extraArgs'])
        self.assertTrue(self.values['containerSecurityContext']['readOnlyRootFilesystem'])

    def test_source_mutation_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'not the qualified'):
            profile.build(b'zabbix_export: {}')

    def test_budget_and_build_placement(self):
        self.assertEqual(self.values['resources'], {'requests': {'cpu': '100m', 'memory': '256Mi'},
                                                   'limits': {'cpu': '300m', 'memory': '512Mi'}})
        self.assertEqual(self.values['tolerations'], [])
        self.assertEqual(self.values['affinity']['nodeAffinity']['preferredDuringSchedulingIgnoredDuringExecution'][1]
                         ['preference']['matchExpressions'][0],
                         {'key': 'kubernetes.io/hostname', 'operator': 'NotIn', 'values': ['metal4']})


if __name__ == '__main__':
    unittest.main()
