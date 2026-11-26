"""Security boundaries: partial enrollment and dependency bypass must fail CI."""
import copy
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import json
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('namespace_defaults', ROOT/'scripts/ci/check_namespace_defaults.py')
contract = importlib.util.module_from_spec(spec); spec.loader.exec_module(contract)


class NamespaceBundleTests(unittest.TestCase):
    def setUp(self):
        raw = subprocess.check_output(['kubectl','kustomize','--load-restrictor=LoadRestrictionsNone',str(ROOT/'scripts/ci/kyverno/fixtures/namespace-example/namespace')], text=True)
        self.docs = list(yaml.safe_load_all(raw))
        self.ks, self.workload = list(yaml.safe_load_all((ROOT/'scripts/ci/kyverno/fixtures/namespace-example/flux.yaml').read_text()))
        self.marker = {'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'marker','namespace':'namespace-example'}}

    def errors(self, docs=None, ks=None, workloads=None):
        return contract.check_graph([(ks or self.ks, docs if docs is not None else self.docs), (self.workload, workloads if workloads is not None else [self.marker])])

    def test_complete_bundle_passes_and_each_partial_bundle_fails(self):
        self.assertEqual(self.errors(), [])
        for i in range(4):
            self.assertTrue(self.errors(self.docs[:i]+self.docs[i+1:]), i)

    def test_label_only_enrollment_and_malformed_specs_fail(self):
        for obj in self.docs:
            bad = copy.deepcopy(self.docs); target = bad[self.docs.index(obj)]
            if obj['kind'] == 'Namespace':
                target['metadata']['annotations'].pop(contract.CONTRACT)
            else:
                target['spec'] = {}
            self.assertTrue(self.errors(bad), obj['kind'])

    def test_contract_only_partial_enrollment_fails(self):
        ns=next(d for d in self.docs if d['kind']=='Namespace')
        ns['metadata']['labels'].pop(contract.LABEL)
        self.assertTrue(self.errors())

    def test_missing_dependency_and_unsafe_prune_fail(self):
        self.workload['spec'].pop('dependsOn')
        self.assertTrue(self.errors())
        for key, value in [('wait',False),('prune',False),('deletionPolicy','Delete'),('suspend',True)]:
            ks = copy.deepcopy(self.ks); ks['spec'][key] = value
            self.assertTrue(self.errors(ks=ks), key)

    def test_helm_target_namespace_cannot_bypass_dependency(self):
        self.workload['spec'].pop('dependsOn')
        hr = {'apiVersion':'helm.toolkit.fluxcd.io/v2','kind':'HelmRelease','metadata':{'name':'probe','namespace':'flux-system'},'spec':{'targetNamespace':'namespace-example'}}
        self.assertTrue(self.errors(workloads=[hr]))

    def test_ready_expression_cannot_bypass_namespace_readiness(self):
        self.workload['spec']['dependsOn'][0]['readyExpr'] = 'true'
        self.assertTrue(self.errors())

    def test_controller_transform_and_reconcile_bypasses_fail(self):
        ks = copy.deepcopy(self.ks); ks['spec']['postBuild'] = {'substitute': {'IGNORED':'true'}}
        self.assertTrue(self.errors(ks=ks))
        obj = next(d for d in self.docs if d['kind']=='ResourceQuota')
        obj['metadata']['annotations']['kustomize.toolkit.fluxcd.io/ssa'] = 'IfNotPresent'
        self.assertTrue(self.errors())

    def test_decommission_requires_consumers_absent(self):
        ns = next(d for d in self.docs if d['kind']=='Namespace')
        ns['metadata']['annotations'][contract.DECOMMISSION] = 'approved'
        self.assertTrue(self.errors())
        self.assertEqual(self.errors(workloads=[]), [])

    def test_duplicate_owners_fail(self):
        self.assertTrue(contract.check_graph([(self.ks,self.docs),(self.ks,self.docs)]))

    def test_unlabelled_competing_namespace_declaration_fails(self):
        competing={'apiVersion':'v1','kind':'Namespace','metadata':{'name':'namespace-example'}}
        self.assertTrue(self.errors(workloads=[competing]))

    def test_namespace_scope_does_not_use_unavailable_match_condition_variables(self):
        guards=list(yaml.safe_load_all((ROOT/'infra/namespace-defaults/admission.yaml').read_text()))
        for guard in guards:
            if guard['kind']!='ValidatingAdmissionPolicy' or not guard['metadata']['name'].startswith('flux-default-'):
                continue
            self.assertEqual(guard['spec']['failurePolicy'],'Fail')
            self.assertEqual(guard['spec']['matchConstraints']['namespaceSelector']['matchLabels'],{contract.LABEL:'enabled'})
            self.assertTrue(all('namespaceObject' not in c['expression'] for c in guard['spec'].get('matchConditions',[])))

    def live(self):
        owner = copy.deepcopy(self.ks)
        owner['metadata']['generation'] = 4
        inventory = []
        for doc in self.docs:
            meta = doc['metadata']
            meta.setdefault('labels', {}).update({'kustomize.toolkit.fluxcd.io/name': owner['metadata']['name'],
                                                  'kustomize.toolkit.fluxcd.io/namespace': 'flux-system'})
            group = doc['apiVersion'].split('/')[0] if '/' in doc['apiVersion'] else ''
            inventory.append({'id': '_'.join([meta.get('namespace', ''), meta['name'], group, doc['kind']]), 'v': 'v1'})
        owner['status'] = {'observedGeneration': 4, 'conditions': [{'type': 'Ready', 'status': 'True', 'observedGeneration': 4}],
                           'inventory': {'entries': inventory}}
        kinds = {'namespaces': 'Namespace', 'limitranges': 'LimitRange', 'resourcequotas': 'ResourceQuota', 'networkpolicies': 'NetworkPolicy'}
        return {**{key: {'items': [d for d in self.docs if d['kind'] == kind]} for key, kind in kinds.items()},
                'kustomizations': {'items': [owner]}}

    def test_live_complete_bundle_and_empty_unenrolled_cluster_pass(self):
        objects = self.live()
        self.assertEqual(contract.check_live(objects), [])
        self.assertEqual(contract.check_live({key: {'items': []} for key in objects}), [])

    def test_live_deleted_suspended_and_stale_owner_are_detected(self):
        for failure in ['absent', 'suspended', 'not-ready', 'stale-generation']:
            objects = self.live(); owner = objects['kustomizations']['items'][0]
            if failure == 'absent': objects['kustomizations']['items'] = []
            elif failure == 'suspended': owner['spec']['suspend'] = True
            elif failure == 'not-ready': owner['status']['conditions'][0]['status'] = 'False'
            else: owner['metadata']['generation'] = 5
            self.assertTrue(contract.check_live(objects), failure)

    def test_live_retained_objects_require_owner_labels_and_inventory(self):
        for failure in ['inventory', 'labels', 'missing-default', 'missing-namespace', 'spec-drift']:
            objects = self.live()
            if failure == 'inventory': objects['kustomizations']['items'][0]['status']['inventory']['entries'] = []
            elif failure == 'labels': objects['resourcequotas']['items'][0]['metadata']['labels'].pop('kustomize.toolkit.fluxcd.io/name')
            elif failure == 'missing-default': objects['resourcequotas']['items'] = []
            elif failure == 'missing-namespace': objects['namespaces']['items'] = []
            else: objects['resourcequotas']['items'][0]['spec']['hard']['pods'] = '21'
            self.assertTrue(contract.check_live(objects), failure)

    def test_live_incomplete_collection_cannot_report_success(self):
        for response in [{'items': [], 'metadata': {'continue': 'more'}}, {}]:
            with patch.object(contract.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(response))):
                with self.assertRaises(ValueError): contract.collect_live()


if __name__ == '__main__': unittest.main()
