"""Security boundaries: partial enrollment and dependency bypass must fail CI."""
import copy
import importlib.util
from pathlib import Path
import subprocess
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('namespace_defaults', ROOT/'scripts/ci/check_namespace_defaults.py')
contract = importlib.util.module_from_spec(spec); spec.loader.exec_module(contract)


class NamespaceBundleTests(unittest.TestCase):
    def setUp(self):
        raw = subprocess.check_output(['kubectl','kustomize','--load-restrictor=LoadRestrictionsNone',str(ROOT/'migration/kyverno/namespace-example/namespace')], text=True)
        self.docs = list(yaml.safe_load_all(raw))
        self.ks, self.workload = list(yaml.safe_load_all((ROOT/'migration/kyverno/namespace-example/flux.yaml').read_text()))
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

    def test_namespace_scope_does_not_use_unavailable_match_condition_variables(self):
        guards=list(yaml.safe_load_all((ROOT/'infra/namespace-defaults/admission.yaml').read_text()))
        for guard in guards:
            if guard['kind']!='ValidatingAdmissionPolicy' or not guard['metadata']['name'].startswith('flux-default-'):
                continue
            self.assertEqual(guard['spec']['failurePolicy'],'Fail')
            self.assertEqual(guard['spec']['matchConstraints']['namespaceSelector']['matchLabels'],{contract.LABEL:'enabled'})
            self.assertTrue(all('namespaceObject' not in c['expression'] for c in guard['spec'].get('matchConditions',[])))


if __name__ == '__main__': unittest.main()
