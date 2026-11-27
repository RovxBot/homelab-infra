"""Keep legacy bootstrap exceptions/fallback distinct from combined references."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

import yaml
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_secret_refs as refs


class SecretReferenceTests(unittest.TestCase):
    def bootstrap(self, docs):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'rendered.yaml'
            path.write_text(yaml.safe_dump_all(docs))
            with contextlib.redirect_stdout(io.StringIO()):
                return refs.main(str(path))

    @staticmethod
    def doc(kind='Deployment', namespace='app', **extra):
        return {'apiVersion': 'v1', 'kind': kind, 'metadata': {'name': 'credentials', **({'namespace': namespace} if namespace else {})}, **extra}

    def test_bootstrap_allowlist_is_unchanged(self):
        self.assertEqual(refs.ALLOWLIST_NAMES, {'flux-system', 'sops-age', 'wireguard-client-conf'})
        for name in refs.ALLOWLIST_NAMES:
            self.assertEqual(self.bootstrap([self.doc(spec={'secretRef': {'name': name}})]), 0)

    def test_legacy_bootstrap_fallback_is_retained_explicitly(self):
        consumer = self.doc(spec={'secretName': 'credentials'})
        self.assertEqual(self.bootstrap([consumer, self.doc('Secret', None)]), 0)
        self.assertEqual(self.bootstrap([consumer, self.doc('Secret', 'other')]), 1)
        self.assertEqual(self.bootstrap([consumer]), 1)

    def test_missing_invalid_and_empty_bootstrap_inputs_fail_without_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'missing.yaml'
            for raw in [None, '', 'PRIVATE-SENTINEL: [', '[]']:
                if raw is not None:
                    path.write_text(raw)
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(refs.main(str(path)), 1)
                self.assertNotIn('PRIVATE-SENTINEL', out.getvalue())

    def test_production_reference_shapes_preserve_namespace_and_name(self):
        doc = self.doc(spec={'secretKeyRef': {'name': 'key'}, 'secretRef': {'name': 'explicit', 'namespace': 'other'},
                             'secretName': 'volume', 'imagePullSecrets': [{'name': 'pull'}],
                             'csi': {'controllerExpandSecretRef': {'name': 'csi', 'namespace': 'storage'}}})
        self.assertEqual(refs.production_refs_from_doc(doc, 'app'),
                         {('app', 'key'), ('app', 'explicit'), ('app', 'volume'), ('app', 'pull'), ('app', 'csi')})
        # Legacy bootstrap namespace semantics are intentionally unchanged.
        self.assertIn(('app', 'explicit'), refs.refs_from_doc(doc, 'app'))

    def test_helm_values_and_flux_substitution_references(self):
        helm = self.doc(); helm.update(apiVersion='helm.toolkit.fluxcd.io/v2', kind='HelmRelease',
                                      spec={'valuesFrom': [{'name': 'secret-values'}, {'kind': 'ConfigMap', 'name': 'config'}]})
        self.assertEqual(refs.production_refs_from_doc(helm, 'app'), {('app', 'secret-values')})
        flux = self.doc(); flux.update(apiVersion='kustomize.toolkit.fluxcd.io/v1', kind='Kustomization',
                                      spec={'postBuild': {'substituteFrom': [{'kind': 'Secret', 'name': 'substitution'}]}})
        self.assertEqual(refs.production_refs_from_doc(flux, 'flux-system'), {('flux-system', 'substitution')})

    def test_secret_payloads_are_opaque_to_both_scanners(self):
        secret = self.doc('Secret', data={'secretName': 'PRIVATE-SENTINEL'},
                          stringData={'secretName': 'PRIVATE-SENTINEL'})
        self.assertEqual(refs.refs_from_doc(secret, 'app'), set())
        self.assertEqual(refs.production_refs_from_doc(secret, 'app'), set())
        self.assertEqual(self.bootstrap([secret]), 0)


if __name__ == '__main__':
    unittest.main()
