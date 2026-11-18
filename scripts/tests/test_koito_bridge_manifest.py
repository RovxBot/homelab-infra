"""The distroless bridge must start through its upstream entrypoint."""
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


class KoitoBridgeManifestTests(unittest.TestCase):
    def test_distroless_startup_and_persistence(self):
        doc = yaml.safe_load((ROOT / 'apps/koito/lastfm-bridge.yaml').read_text())
        pod = doc['spec']['template']['spec']
        container = pod['containers'][0]
        self.assertNotIn('command', container)
        self.assertNotIn('args', container)
        self.assertTrue(container['image'].startswith('foxxmd/multi-scrobbler:0.21.0@sha256:'))
        self.assertEqual(pod['securityContext']['runAsUser'], 1000)
        self.assertEqual(pod['securityContext']['fsGroup'], 1000)
        self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
        mounts = {mount['mountPath']: mount for mount in container['volumeMounts']}
        self.assertFalse(mounts['/config'].get('readOnly', False))
        self.assertFalse(mounts['/tmp'].get('readOnly', False))
        volume = next(v for v in pod['volumes'] if v['name'] == mounts['/config']['name'])
        self.assertEqual(volume['persistentVolumeClaim']['claimName'], 'koito-lastfm-bridge-data')
        for probe in ('startupProbe','readinessProbe','livenessProbe'):
            self.assertNotIn('exec',container[probe])
        self.assertEqual(container['readinessProbe']['httpGet']['path'], '/api/health')
        credentials = {item['name']: item.get('valueFrom') for item in container['env']}
        for key in ('SOURCE_LASTFM_API_KEY','SOURCE_LASTFM_SECRET','KOITO_TOKEN'):
            self.assertIn('secretKeyRef', credentials[key])


if __name__ == '__main__':
    unittest.main()
