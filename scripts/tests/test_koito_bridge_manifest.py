"""The distroless bridge must start through its upstream entrypoint."""
from pathlib import Path
import copy
import unittest

import yaml
from scripts.ci.image_reference import pinned_release

ROOT = Path(__file__).resolve().parents[2]


class KoitoBridgeManifestTests(unittest.TestCase):
    def setUp(self):
        self.doc = yaml.safe_load((ROOT / 'apps/koito/lastfm-bridge.yaml').read_text())

    def assert_bridge(self, doc):
        pod = doc['spec']['template']['spec']
        container = pod['containers'][0]
        self.assertNotIn('command', container)
        self.assertNotIn('args', container)
        # Only patches on the reviewed distroless 0.21 line are accepted.
        pinned_release(container['image'], repository='foxxmd/multi-scrobbler',
                       minimum=(0, 21, 0), release_line=(0, 21))
        self.assertFalse(pod['automountServiceAccountToken'])
        self.assertTrue(pod['securityContext']['runAsNonRoot'])
        self.assertEqual(pod['securityContext']['runAsUser'], 1000)
        self.assertEqual(pod['securityContext']['runAsGroup'], 1000)
        self.assertEqual(pod['securityContext']['fsGroup'], 1000)
        self.assertEqual(pod['securityContext']['seccompProfile']['type'], 'RuntimeDefault')
        self.assertFalse(container['securityContext']['allowPrivilegeEscalation'])
        self.assertEqual(container['securityContext']['capabilities']['drop'], ['ALL'])
        self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
        mounts = {mount['mountPath']: mount for mount in container['volumeMounts']}
        self.assertFalse(mounts['/config'].get('readOnly', False))
        self.assertFalse(mounts['/tmp'].get('readOnly', False))
        self.assertTrue(mounts['/config/config.json']['readOnly'])
        volume = next(v for v in pod['volumes'] if v['name'] == mounts['/config']['name'])
        self.assertEqual(volume['persistentVolumeClaim']['claimName'], 'koito-lastfm-bridge-data')
        for probe in ('startupProbe','readinessProbe','livenessProbe'):
            self.assertNotIn('exec',container[probe])
        self.assertEqual(container['readinessProbe']['httpGet']['path'], '/api/health')
        self.assertEqual(container['readinessProbe']['httpGet']['port'], 'http')
        self.assertEqual(container['startupProbe']['tcpSocket']['port'], 'http')
        self.assertEqual(container['livenessProbe']['tcpSocket']['port'], 'http')
        credentials = {item['name']: item.get('valueFrom') for item in container['env']}
        for key, secret_key in {'SOURCE_LASTFM_API_KEY': 'LASTFM_API_KEY',
                                'SOURCE_LASTFM_SECRET': 'LASTFM_SHARED_SECRET',
                                'KOITO_TOKEN': 'KOITO_API_TOKEN'}.items():
            self.assertIn('secretKeyRef', credentials.get(key) or {})
            self.assertEqual(credentials[key]['secretKeyRef'], {'name': 'koito', 'key': secret_key})
        env = {item['name']: item.get('value') for item in container['env']}
        for key, path in {'CONFIG_DIR': '/config', 'DATA_DIR': '/config', 'LOGS_DIR': '/config/logs'}.items():
            self.assertEqual(env[key], path)

    def test_distroless_startup_and_persistence(self):
        self.assert_bridge(self.doc)

    def test_compatible_patches_need_no_assertion_update(self):
        for patch in (0, 1, 12):
            doc = copy.deepcopy(self.doc)
            doc['spec']['template']['spec']['containers'][0]['image'] = (
                f'foxxmd/multi-scrobbler:0.21.{patch}@sha256:' + 'a' * 64)
            self.assert_bridge(doc)

    def test_unsafe_or_unreviewed_references_are_rejected(self):
        for image in ('foxxmd/multi-scrobbler:latest',
                      'foxxmd/multi-scrobbler:0.21.1',
                      *('foxxmd/multi-scrobbler:' + tag + '@sha256:' + 'a' * 64
                        for tag in ('0.20.9', '0.22.0', '1.0.0', '0.21.1-rc1', '0.21', '0.21.01')),
                      'other/multi-scrobbler:0.21.1@sha256:' + 'a' * 64,
                      'foxxmd/multi-scrobbler:0.21.1@sha256:abcd'):
            with self.subTest(image=image), self.assertRaises(ValueError):
                doc = copy.deepcopy(self.doc)
                doc['spec']['template']['spec']['containers'][0]['image'] = image
                self.assert_bridge(doc)

    def test_security_startup_persistence_and_credential_drift_fails(self):
        for change in ('shell', 'writable-root', 'privilege', 'ephemeral', 'credentials', 'exec-probe'):
            with self.subTest(change=change), self.assertRaises(AssertionError):
                doc = copy.deepcopy(self.doc)
                pod = doc['spec']['template']['spec']
                container = pod['containers'][0]
                if change == 'shell':
                    container['command'] = ['/bin/sh']
                elif change == 'writable-root':
                    container['securityContext']['readOnlyRootFilesystem'] = False
                elif change == 'privilege':
                    container['securityContext']['allowPrivilegeEscalation'] = True
                elif change == 'ephemeral':
                    pod['volumes'][0]['persistentVolumeClaim']['claimName'] = 'wrong-data'
                elif change == 'credentials':
                    container['env'] = [v for v in container['env'] if v['name'] != 'KOITO_TOKEN']
                    # Missing credentials must fail the same assertion as inline values.
                    container['env'].append({'name': 'KOITO_TOKEN', 'value': 'unsafe'})
                else:
                    container['livenessProbe']['exec'] = {'command': ['sh']}
                self.assert_bridge(doc)


if __name__ == '__main__':
    unittest.main()
