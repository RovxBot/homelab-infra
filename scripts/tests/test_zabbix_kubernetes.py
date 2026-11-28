"""Security contracts for the bounded Zabbix cluster-state integration."""
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import unittest
import uuid

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


class NativeTemplateContract(unittest.TestCase):
    """Static export contracts, NOT Zabbix import or runtime evidence.

    Fixture is the complete byte-exact upstream AGPL-3.0 export from profile.SOURCE,
    compressed only for storage. No network or hash bypass is used in CI.
    """

    @classmethod
    def setUpClass(cls):
        fixture = ROOT / 'scripts/tests/fixtures/zabbix/template_kubernetes_cluster-8.0.0.yaml.gz'
        cls.raw = gzip.decompress(fixture.read_bytes())
        cls.original = yaml.safe_load(cls.raw)
        cls.data, cls.changes = profile.build(cls.raw)
        cls.cluster, cls.node = cls.data['zabbix_export']['templates']
        cls.items = {i['key']: i for i in cls.cluster['items']}

    def test_byte_exact_source_and_deterministic_native_export(self):
        provenance = json.loads((ROOT / 'scripts/tests/fixtures/zabbix/SOURCE.json').read_text())
        self.assertEqual(provenance['source'], profile.SOURCE)
        self.assertEqual(provenance['decompressedSHA256'], profile.SHA256)
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(),
                         '381387062c667528a5b87865dc4bd1d1f96a668ebf19accc0648b80496d8d629')
        self.assertEqual(profile.build(self.raw), (self.data, self.changes))
        self.assertEqual(yaml.safe_load(profile.serialize(self.data)), self.data)
        self.assertEqual([t['template'] for t in self.data['zabbix_export']['templates']], list(profile.NAMES.values()))

    def test_state_master_saves_each_poll_for_native_freshness(self):
        item = self.items['kube.state.metrics']
        self.assertEqual(item['type'], 'HTTP_AGENT')
        self.assertEqual(item['delay'], '1m')
        # history=0 cannot support trigger functions; no throttling may mask polls.
        self.assertEqual(item['history'], '1h')
        self.assertEqual(item['preprocessing'], [{'type': 'MATCHES_REGEX', 'parameters': [
            r'(?m)^kube_node_info\{[^\r\n]+\}[ \t]+1(?:\.0+)?[ \t]*$']}])
        self.assertEqual(item['status_codes'], '200')
        self.assertEqual(item['url'], '{$KUBE.STATE.METRICS.URL}')
        for field in ('params', 'parameters', 'headers'):
            self.assertNotIn(field, item)
        self.assertEqual(len(item['triggers']), 1)
        trigger = item['triggers'][0]
        path = '/Homelab Kubernetes Cluster state by HTTP/kube.state.metrics'
        self.assertEqual(trigger['expression'], f'nodata({path},5m)=1')
        self.assertEqual(trigger['recovery_mode'], 'RECOVERY_EXPRESSION')
        self.assertEqual(trigger['recovery_expression'], f'count({path},3m)>=3 and nodata({path},90s)=0')

    def test_native_yaml_does_not_fold_quoted_vendor_javascript(self):
        js = "return ['Pending', 'Failed'].indexOf(value) + 1 || 'Problem with status processing: ' + value;"
        text = profile.serialize({'parameters': [js, 'line one\nline two\n']})
        self.assertEqual(yaml.safe_load(text), {'parameters': [js, 'line one\nline two\n']})
        self.assertIn("+ 1 || ''Problem with status processing: '' + value;'", text)
        self.assertIn('|\n', text)

    def test_native_import_requires_uuid_v4(self):
        def check(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == 'uuid':
                        self.assertEqual(uuid.UUID(child).version, 4, child)
                    else:
                        check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        check(self.data)

    def test_metric_reimport_preserves_every_existing_uuid(self):
        def identifiers(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == 'uuid':
                        yield child
                    else:
                        yield from identifiers(child)
            elif isinstance(value, list):
                for child in value:
                    yield from identifiers(child)
        # Fingerprint of the complete accepted #2352 profile, in export order.
        self.assertEqual(hashlib.sha256('\n'.join(identifiers(self.data)).encode()).hexdigest(),
                         '4879c5f95a1a84f011fc3efa0cfcecca77d3801f9e4930b8954660dfe55f7238')
        item = self.items['kube.pod.daemonset_count']
        self.assertEqual(item['uuid'], 'f5f90e5313aa4eb1ae8866ae0d4d4bf8')
        self.assertEqual(item['preprocessing'][0]['parameters'],
                         ['kube_daemonset_created', 'function', 'count'])
        self.assertEqual(item['master_item']['key'], 'kube.state.metrics')

    def test_enabled_dependencies_do_not_require_arbitrary_labels_or_annotations(self):
        def enabled_dependencies(value):
            if isinstance(value, dict):
                if value.get('status') == 'DISABLED':
                    return
                for child in value.values():
                    yield from enabled_dependencies(child)
            elif isinstance(value, list):
                for child in value:
                    yield from enabled_dependencies(child)
            elif isinstance(value, str):
                yield from re.findall(r'\bkube_\w+_(?:labels|annotations)\b', value)
        self.assertEqual(list(enabled_dependencies(self.data['zabbix_export']['templates'])), [])

    def test_daemonset_fixture_represents_eleven_objects_without_label_collection(self):
        # Exposition input also exercised by disposable native Zabbix (retained
        # evidence). This static check is not an implementation of its parser.
        text = (ROOT / 'scripts/tests/fixtures/zabbix/daemonsets-no-labels.prom').read_text()
        series = re.findall(r'^kube_daemonset_created\{([^}]+)\} (\d+)$', text, re.M)
        self.assertEqual(len(series), 11)
        self.assertEqual(len({labels for labels, _ in series}), 11)
        self.assertNotRegex(text, r'(?m)^kube_daemonset_(?:labels|annotations)\b')
        self.assertEqual(self.items['kube.pod.daemonset_count']['preprocessing'][0]['parameters'],
                         ['kube_daemonset_created', 'function', 'count'])

    def test_script_json_error_check_and_misleading_trigger_are_disabled(self):
        item = self.items['kube.state.metrics.check']
        self.assertEqual(item['status'], 'DISABLED')
        self.assertTrue(item['triggers'])
        self.assertTrue(all(t['status'] == 'DISABLED' for t in item['triggers']))

    def test_health_uses_status_not_body_and_retains_all_failure_statuses(self):
        for key in ('kube.get.livez', 'kube.get.readyz'):
            with self.subTest(key=key):
                item = self.items[key]
                self.assertEqual(item['status_codes'], '')
                self.assertEqual(item['retrieve_mode'], 'HEADERS')
                self.assertEqual(item['preprocessing'], [{'type': 'REGEX', 'parameters': [
                    r'^HTTP/[0-9.]+[ \t]+([0-9]{3})\b', r'\1']}])
                self.assertEqual(item['headers'], [{'name': 'Authorization', 'value': 'Bearer {$KUBE.API.TOKEN}'}])
                self.assertEqual((item['delay'], item['history']), ('1m', '1h'))
                self.assertEqual(len(item['triggers']), 1)
                path = '/Homelab Kubernetes Cluster state by HTTP/' + key
                trigger = item['triggers'][0]
                # 401/403 (expired/revoked token), 500/503 and 3xx must be
                # unhealthy values. TLS/refusal/timeouts instead stop updates.
                self.assertEqual(trigger['expression'], f'nodata({path},5m)=1 or last({path})<>200')
                self.assertEqual(trigger['recovery_mode'], 'RECOVERY_EXPRESSION')
                self.assertEqual(trigger['recovery_expression'],
                                 f'count({path},3m,"eq",200)>=3 and count({path},3m,"ne",200)=0 '
                                 f'and nodata({path},90s)=0')

    def test_header_pattern_handles_failure_codes_with_a_healthy_looking_body(self):
        # A syntax/fixture regression only; native PCRE execution is qualified
        # separately in disposable Zabbix, not emulated by this Python test.
        pattern = self.items['kube.get.livez']['preprocessing'][0]['parameters'][0]
        for protocol in ('1.1', '2', '3'):
            for status in (200, 302, 401, 403, 500, 503):
                with self.subTest(protocol=protocol, status=status):
                    response = f'HTTP/{protocol} {status} status\r\nContent-Type: text/plain\r\n\r\nok'
                    self.assertEqual(re.search(pattern, response)[1], str(status))

    def test_transport_controls_and_unsupported_coverage_are_preserved(self):
        for key in ('kube.state.metrics', 'kube.get.livez', 'kube.get.readyz'):
            item = self.items[key]
            self.assertEqual((item['verify_peer'], item['verify_host'], item['follow_redirects']),
                             ('YES', 'YES', 'NO'))
        for key in ('kube.resource.metrics', 'kube.secret.count', 'kube.configmap.count', 'kube.endpoint.count'):
            self.assertEqual(self.items[key]['status'], 'DISABLED')
        self.assertTrue(all(i['status'] == 'DISABLED' for i in self.node['items']))
        self.assertTrue(all(d['status'] == 'DISABLED' for d in self.node['discovery_rules']))
        self.assertNotIn('dashboards', self.cluster)
        # Do not widen discovery or change normal native dependent metric parsing.
        original = self.original['zabbix_export']['templates'][0]
        for key in ('kube.node.ready.count', 'kube.node.count'):
            before = next(i for i in original['items'] if i['key'] == key)
            self.assertEqual(self.items[key]['preprocessing'], before['preprocessing'])
        self.assertEqual(next(d for d in self.cluster['discovery_rules'] if d['key']=='kube.node.discovery')
                         ['host_prototypes'][0]['host'], 'cooked-k8s Node {#UUID}')


if __name__ == '__main__':
    unittest.main()
