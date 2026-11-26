"""Relocated qualification inputs must also work in the disposable Flux repo."""
import importlib.util
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[3]
HARNESS = ROOT / 'scripts/ci/kyverno'
FIXTURES = HARNESS / 'fixtures/namespace-example'


def render(path):
    return list(yaml.safe_load_all(subprocess.check_output(
        ['kubectl', 'kustomize', '--load-restrictor=LoadRestrictionsNone', str(path)],
        text=True)))


class QualificationLayoutTests(unittest.TestCase):
    def test_repository_roots_survive_relocation(self):
        # These entrypoints resolve inputs relative to __file__, not shell cwd.
        for name in ['build', 'rehearse', 'resolve', 'audit_exceptions']:
            with self.subTest(name=name), patch.object(sys, 'path', [str(HARNESS), *sys.path]):
                spec = importlib.util.spec_from_file_location(name, HARNESS/f'{name}.py')
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.assertEqual(module.ROOT, ROOT)

    def test_staged_namespace_and_workload_match_local_renders(self):
        with patch.object(sys, 'path', [str(HARNESS), *sys.path]):
            import namespace_lifecycle as lifecycle
        expected = render(FIXTURES/'namespace')
        nsks, appks = yaml.safe_load_all((FIXTURES/'flux.yaml').read_text())
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            flux = SimpleNamespace(repo=repo)
            with patch.object(lifecycle, 'bundle', return_value=expected):
                lifecycle.write_bundle(flux)
            namespace = repo/nsks['spec']['path']
            self.assertEqual(render(namespace), expected)
            workload = repo/appks['spec']['path']
            shutil.copytree(FIXTURES/'workloads', workload)
            self.assertEqual(render(workload), render(FIXTURES/'workloads'))
            # Flat malformed/decommission fixtures share the exact same target.
            with patch.object(lifecycle, 'bundle', return_value=expected):
                lifecycle.write_bundle(flux, empty=True)
            self.assertEqual(render(namespace), [])
            self.assertFalse((repo/'migration').exists())

    def test_relocation_is_complete_and_historical_payloads_keep_baseline_bytes(self):
        receipt = json.loads((ROOT/'docs/kyverno/relocation.json').read_text())
        rows = receipt['files']
        self.assertEqual(receipt['observed_file_count'], len(rows))
        self.assertEqual(len({r['original_path'] for r in rows}), len(rows))
        self.assertEqual(len({r['path'] for r in rows}), len(rows))
        for row in rows:
            path = ROOT/row['path']
            self.assertTrue(path.is_file(), row['path'])
            if row['path'].startswith('docs/kyverno/evidence/'):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row['original_sha256'], row['path'])
        self.assertFalse((ROOT/'migration').exists())


if __name__ == '__main__':
    unittest.main()
