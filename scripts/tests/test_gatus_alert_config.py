"""Keep the native GitHub alert and helper recovery contracts aligned."""
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]


class GatusAlertConfigTests(unittest.TestCase):
    def test_only_cluster_health_overrides_recovery_and_preserves_detection(self):
        release = yaml.safe_load((ROOT / 'infra/observability/gatus-helmrelease.yaml').read_text())
        values = release['spec']['values']
        self.assertEqual(values['image']['tag'], 'v5.34.0')
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
                else:
                    self.assertEqual(endpoint['alerts'], [{'type': 'github'}])
        self.assertEqual(config['storage'], {'type': 'sqlite', 'path': '/data/data.db'})

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
