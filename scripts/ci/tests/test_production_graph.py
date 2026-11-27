"""Combined graph checks must not lose ownership or namespace boundaries."""
import contextlib
import copy
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_production_graph as graph


def resource(kind='Secret', name='example', namespace='app', api='v1', **extra):
    meta = {'name': name}
    if namespace is not None:
        meta['namespace'] = namespace
    return {'apiVersion': api, 'kind': kind, 'metadata': meta, **extra}


def owner(name, docs, **spec):
    return ({'apiVersion': 'kustomize.toolkit.fluxcd.io/v1', 'kind': 'Kustomization',
             'metadata': {'name': name, 'namespace': 'flux-system'},
             'spec': {'path': './apps/example', **spec}}, docs)


def wotlk():
    ns = resource('Namespace', 'wotlk', None)
    return [owner('infrastructure', [copy.deepcopy(ns)]), owner('apps-wotlk', [ns])]


def consumer(name='example', namespace='app'):
    return resource('Deployment', name, namespace, 'apps/v1',
                    spec={'template': {'spec': {'containers': [{'env': [{'valueFrom': {
                        'secretKeyRef': {'name': 'credentials', 'key': 'password'}}}]}]}}})


class CombinedGraphTests(unittest.TestCase):
    def errors(self, entries):
        return graph.validate(wotlk() + entries)[0]

    def test_valid_cross_owner_reference(self):
        entries = [owner('consumer', [consumer()]), owner('secrets', [resource(name='credentials')])]
        self.assertEqual(self.errors(entries), [])
        receipt = graph.validate(wotlk() + entries)[1]
        self.assertEqual(receipt['ownerCount'], 4)
        self.assertEqual(receipt['ownerResourceEntries'], 4)
        self.assertEqual(receipt['distinctResourceIdentities'], 3)
        self.assertEqual(receipt['secretReferenceCount'], 1)

    @staticmethod
    def deployment_reference(shape, explicit_namespace=None):
        ref = {'name': 'credentials'}
        if explicit_namespace is not None:
            ref['namespace'] = explicit_namespace
        container = {'name': 'app', 'image': 'example.invalid/app:fixture'}
        if shape == 'env':
            container['env'] = [{'name': 'PASSWORD', 'valueFrom': {'secretKeyRef': {**ref, 'key': 'password'}}}]
        else:
            container['envFrom'] = [{'secretRef': ref}]
        return resource('Deployment', 'app', 'app', 'apps/v1', spec={
            'selector': {'matchLabels': {'app': 'fixture'}},
            'template': {'metadata': {'labels': {'app': 'fixture'}}, 'spec': {'containers': [container]}},
        })

    def test_deployment_env_and_envfrom_invalid_namespaces_cannot_resolve_remote_secrets(self):
        for shape in ['env', 'envFrom']:
            with self.subTest(shape=shape):
                doc = self.deployment_reference(shape, explicit_namespace='other')
                errors = self.errors([owner('workload', [doc]), owner('remote', [resource(name='credentials', namespace='other')])])
                self.assertIn('Unresolved Secret app/credentials', str(errors))

    def test_deployment_env_and_envfrom_missing_local_secret_fail(self):
        for shape in ['env', 'envFrom']:
            for secrets in [[], [resource(name='credentials', namespace='other')]]:
                with self.subTest(shape=shape, secrets=bool(secrets)):
                    errors = self.errors([owner('workload', [self.deployment_reference(shape)]), owner('secrets', secrets)])
                    self.assertIn('Unresolved Secret app/credentials', str(errors))

    def test_deployment_env_and_envfrom_valid_local_references_pass(self):
        for shape in ['env', 'envFrom']:
            with self.subTest(shape=shape):
                self.assertEqual(self.errors([owner('workload', [self.deployment_reference(shape)]),
                                              owner('secrets', [resource(name='credentials')])]), [])

    def test_invalid_local_namespace_field_is_ignored_by_resolution(self):
        for shape in ['env', 'envFrom']:
            with self.subTest(shape=shape):
                self.assertEqual(self.errors([owner('workload', [self.deployment_reference(shape, 'other')]),
                                              owner('secrets', [resource(name='credentials')])]), [])

    def test_inline_pod_csi_reference_cannot_resolve_remote_secret(self):
        doc = resource('Pod', 'inline-csi', 'app', spec={'volumes': [{'name': 'inline', 'csi': {
            'driver': 'example.invalid/csi', 'nodePublishSecretRef': {'name': 'credentials', 'namespace': 'other'}}}]})
        self.assertTrue(self.errors([owner('workload', [doc]), owner('remote', [resource(name='credentials', namespace='other')])]))
        self.assertEqual(self.errors([owner('workload', [doc]), owner('local', [resource(name='credentials')])]), [])

    def test_persistent_volume_csi_secret_references_keep_explicit_namespace(self):
        for field in ['nodeStageSecretRef', 'nodePublishSecretRef', 'controllerPublishSecretRef',
                      'controllerExpandSecretRef', 'nodeExpandSecretRef']:
            with self.subTest(field=field):
                doc = resource('PersistentVolume', 'csi', None, spec={'csi': {
                    field: {'name': 'credentials', 'namespace': 'storage'}}})
                self.assertEqual(self.errors([owner('volume', [doc]), owner('secrets', [resource(name='credentials', namespace='storage')])]), [])
                self.assertTrue(self.errors([owner('volume', [doc]), owner('secrets', [resource(name='credentials', namespace='app')])]))

    def test_missing_secret_reference_identifies_owner_without_payload(self):
        doc = consumer()
        doc['spec']['sentinel'] = 'PAYLOAD-MUST-NOT-APPEAR'
        errors = self.errors([owner('consumer', [doc])])
        self.assertIn('Unresolved Secret app/credentials', errors[0])
        self.assertIn('consumer', errors[0])
        self.assertNotIn('PAYLOAD-MUST-NOT-APPEAR', str(errors))

    def test_same_name_secret_in_other_namespace_cannot_resolve(self):
        self.assertTrue(self.errors([owner('consumer', [consumer()]),
                                     owner('secrets', [resource(name='credentials', namespace='other')])]))
        self.assertEqual(self.errors([owner('a', [consumer(namespace='one'), resource(name='credentials', namespace='one')]),
                                      owner('b', [consumer(namespace='two'), resource(name='credentials', namespace='two')])]), [])

    def test_effective_namespace_uses_flux_target_then_metadata_then_default(self):
        for target, source, expected in [('target', 'source', 'target'), (None, 'source', 'source'), (None, None, 'default')]:
            with self.subTest(target=target, source=source):
                ks, docs = owner('consumer', [consumer(namespace=source)], **({'targetNamespace': target} if target else {}))
                self.assertEqual(self.errors([(ks, docs), owner('secrets', [resource(name='credentials', namespace=expected)])]), [])
                self.assertTrue(self.errors([(ks, docs), owner('secrets', [resource(name='credentials', namespace='wrong')])]))

    def test_unnamespaced_secret_is_default_not_global_fallback(self):
        self.assertTrue(self.errors([owner('consumer', [consumer()]), owner('secrets', [resource(name='credentials', namespace=None)])]))
        self.assertEqual(self.errors([owner('consumer', [consumer(namespace=None)]), owner('secrets', [resource(name='credentials', namespace=None)])]), [])

    def test_explicit_cross_namespace_reference_and_cluster_scoped_csi_reference(self):
        pv = resource('PersistentVolume', 'pv', None, spec={'csi': {'nodeStageSecretRef': {'name': 'credentials', 'namespace': 'storage'}}})
        self.assertEqual(self.errors([owner('pv', [pv]), owner('secret', [resource(name='credentials', namespace='storage')])]), [])
        self.assertTrue(self.errors([owner('pv', [pv]), owner('secret', [resource(name='credentials', namespace='other')])]))

    def test_bootstrap_exceptions_remain_explicit(self):
        for name in ['flux-system', 'sops-age', 'wireguard-client-conf']:
            doc = resource('GitRepository', 'bootstrap', 'flux-system', 'source.toolkit.fluxcd.io/v1', spec={'secretRef': {'name': name}})
            # Source kind scope comes from its rendered CRD in production.
            crd = self.crd('source.toolkit.fluxcd.io', 'GitRepository', 'Namespaced')
            self.assertEqual(self.errors([owner('bootstrap', [doc, crd])]), [])
        self.assertEqual(graph.ALLOWLIST_NAMES, {'flux-system', 'sops-age', 'wireguard-client-conf'})

    def test_duplicate_occurrence_fails_even_when_identical(self):
        doc = resource()
        self.assertIn('Duplicate resource occurrence', str(self.errors([owner('same', [doc, copy.deepcopy(doc)])])))

    def test_unexpected_multiple_ownership_and_conflicting_documents(self):
        a = resource(); b = copy.deepcopy(a)
        errors = self.errors([owner('a', [a]), owner('b', [b])])
        self.assertIn('Unexpected multiple ownership', str(errors))
        b['data'] = {'key': 'PRIVATE-SENTINEL'}
        errors = self.errors([owner('a', [a]), owner('b', [b])])
        self.assertIn('Conflicting desired documents', str(errors))
        self.assertNotIn('PRIVATE-SENTINEL', str(errors))

    def test_api_versions_share_identity_and_effective_namespace_is_normalized(self):
        a = resource('Deployment', namespace=None, api='apps/v1')
        b = resource('Deployment', namespace='default', api='apps/v1beta2')
        errors = self.errors([owner('a', [a]), owner('b', [b])])
        self.assertIn('Unexpected multiple ownership', str(errors))
        self.assertIn('Conflicting desired documents', str(errors))

    def test_cluster_scope_is_not_inferred_from_metadata_or_kind_alone(self):
        a = resource('Node', 'worker', None)
        b = resource('Node', 'worker', 'ignored')
        self.assertIn('Unexpected multiple ownership', str(self.errors([owner('a', [a]), owner('b', [b])])) )
        # Longhorn Node is namespaced; same kind in core is cluster-scoped.
        self.assertEqual(self.errors([owner('a', [a, resource('Node', 'worker', 'one', 'longhorn.io/v1beta2')]),
                                      owner('b', [resource('Node', 'worker', 'two', 'longhorn.io/v1beta2')])]), [])

    def test_duplicate_owner_entry_is_rejected(self):
        entry = owner('same', [resource()])
        self.assertIn('Duplicate Flux owner entry', str(self.errors([entry, entry])))

    def test_wotlk_requires_exact_owners_even_if_documents_match(self):
        self.assertEqual(graph.validate(wotlk())[0], [])
        for entries in [[], wotlk()[:1], wotlk()[1:], wotlk() + [owner('extra', [resource('Namespace', 'wotlk', None)])]]:
            self.assertTrue(graph.validate(entries)[0])
        entries = wotlk(); entries[1][0]['metadata']['namespace'] = 'other'
        self.assertTrue(graph.validate(entries)[0])

    def test_wotlk_conflict_and_repeated_occurrence_fail(self):
        entries = wotlk(); entries[1][1][0]['metadata']['labels'] = {'pod-security.kubernetes.io/enforce': 'privileged'}
        self.assertIn('Conflicting desired documents', str(graph.validate(entries)[0]))
        entries = wotlk(); entries[1][1].append(copy.deepcopy(entries[1][1][0]))
        self.assertTrue(graph.validate(entries)[0])

    def test_wotlk_documents_must_match_even_for_cluster_namespace_aliases(self):
        entries = wotlk()
        entries[1][1][0]['metadata']['namespace'] = 'ignored-for-identity'
        self.assertIn('Conflicting desired documents', str(graph.validate(entries)[0]))

    @staticmethod
    def crd(group, kind, scope):
        return resource('CustomResourceDefinition', 'examples.' + group, None, 'apiextensions.k8s.io/v1',
                        spec={'group': group, 'names': {'kind': kind}, 'scope': scope})

    def test_rendered_crd_scope_is_used_and_unknown_scope_fails_closed(self):
        doc = resource('Example', namespace='one', api='example.io/v1')
        self.assertEqual(self.errors([owner('a', [self.crd('example.io', 'Example', 'Namespaced'), doc]),
                                      owner('b', [resource('Example', namespace='two', api='example.io/v1')])]), [])
        with self.assertRaisesRegex(ValueError, 'Unknown resource scope'):
            self.errors([owner('a', [doc])])
        for scope in ['invalid', 'Namespaced']:
            with self.assertRaisesRegex(ValueError, 'conflicting resource scope'):
                self.errors([owner('a', [self.crd('', 'Namespace', scope)])])

    def test_collection_failure_is_nonzero_and_payload_is_suppressed(self):
        with patch.object(graph, 'render_roots', side_effect=ValueError('PRIVATE-SENTINEL')), patch.object(sys, 'argv', ['check']), contextlib.redirect_stderr(io.StringIO()) as out:
            self.assertEqual(graph.main(), 1)
        self.assertNotIn('PRIVATE-SENTINEL', out.getvalue())

    def test_receipt_never_serializes_desired_documents(self):
        errors, receipt = graph.validate(wotlk() + [owner('secret', [resource(
            data={'secretName': 'PRIVATE-SENTINEL'}, stringData={'secretName': 'PRIVATE-SENTINEL'})])])
        self.assertEqual(errors, [])
        self.assertNotIn('PRIVATE-SENTINEL', str(receipt))


if __name__ == '__main__':
    unittest.main()
