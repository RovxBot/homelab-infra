"""Fail-closed release, inventory and mandatory qualification controls."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import yaml
import copy

ROOT=Path(__file__).resolve().parents[3]


def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'migration/kyverno/harness'/f'{name}.py')
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj


class AdmissionConflictTests(unittest.TestCase):
    def fixture(self):
        return dict(apiVersion='apps/v1',kind='Deployment',metadata=dict(name='probe',namespace='isolated',resourceVersion='1'),spec=dict(replicas=0))

    def test_update_conflict_refreshes_only_version(self):
        rehearsal=module('rehearse');doc=self.fixture()
        conflict=subprocess.CompletedProcess([],1,'','Error from server (Conflict): object modified')
        allowed=subprocess.CompletedProcess([],0,'{}','')
        with patch.object(rehearsal,'k',side_effect=[conflict,allowed]) as invoke,patch.object(rehearsal,'get',return_value=dict(metadata=dict(resourceVersion='2'))),patch.object(rehearsal.time,'sleep'):
            self.assertTrue(rehearsal.admission(doc,'UPDATE',check_policy=False)['allowed'])
        submitted=yaml.safe_load(invoke.call_args.kwargs['data'])
        self.assertEqual(submitted,{**doc,'metadata':{**doc['metadata'],'resourceVersion':'2'}})
        self.assertEqual(doc['metadata']['resourceVersion'],'1')

    def test_exhausted_conflict_is_never_a_denial(self):
        rehearsal=module('rehearse')
        conflict=subprocess.CompletedProcess([],1,'','Error from server (Conflict): object modified')
        with patch.object(rehearsal,'k',return_value=conflict),patch.object(rehearsal,'get',return_value=dict(metadata=dict(resourceVersion='2'))),patch.object(rehearsal.time,'sleep'):
            with self.assertRaisesRegex(AssertionError,'not a policy decision'):
                rehearsal.admission(self.fixture(),'UPDATE',check_policy=False)

    def test_actual_policy_denial_is_not_retried(self):
        rehearsal=module('rehearse')
        denied=subprocess.CompletedProcess([],1,'','admission webhook kyverno denied the request')
        with patch.object(rehearsal,'k',return_value=denied) as invoke:
            self.assertFalse(rehearsal.admission(self.fixture(),'UPDATE')['allowed'])
        self.assertEqual(invoke.call_count,1)


class EmptyStateGateTests(unittest.TestCase):
    def test_unavailable_runner_probe_cannot_qualify_continuity(self):
        sys.path.insert(0,str(ROOT/'migration/kyverno/harness'))
        continuity=module('production_sequence')
        with tempfile.TemporaryDirectory() as tmp,patch.object(continuity,'EVIDENCE',Path(tmp)),patch.object(continuity,'run',side_effect=subprocess.TimeoutExpired('probe',120)):
            monitor=continuity.RunnerContinuity()
            with self.assertRaises(AssertionError):
                with monitor:
                    monitor.thread.join(timeout=2)
            self.assertEqual(monitor.samples[0]['collection'],'unavailable')

    def test_nonempty_inventory_always_stops(self):
        inventory=module('inventory')
        empty={kind:{'items':[]} for kind in ['namespaces',*inventory.OUTPUTS,'updaterequests.kyverno.io']}
        self.assertEqual(inventory.summarize(empty)['status'],'verified')
        for kind,obj in [('namespaces',dict(metadata=dict(name='eligible',labels={inventory.LABEL:'enabled'}))),
                         ('resourcequotas',dict(metadata=dict(name='namespace-defaults'))),
                         ('limitranges',dict(metadata=dict(name='renamed',labels={'generate.kyverno.io/policy-name':'platform-namespace-bootstrap'}))),
                         ('updaterequests.kyverno.io',dict(status=dict(state='Completed')))]:
            objects=json.loads(json.dumps(empty));objects[kind]['items'].append(obj)
            self.assertEqual(inventory.summarize(objects)['status'],'failed',kind)

    def test_skipped_mandatory_scenarios_cannot_qualify(self):
        workflow=yaml.safe_load((ROOT/'.github/workflows/kyverno-compatibility.yml').read_text())
        command=workflow['jobs']['deployment-qualification']['steps'][0]['run']
        required=['namespace-lifecycle','production-sequence','mutation','validation']
        for selected,status,expected in [(required,'success',0),(required,'skipped',1),(required,'failure',1),([], 'success',1)]+[(list(set(required)-{missing}),'success',1) for missing in required]:
            result=subprocess.run(['bash','-e','-c',command],env={**os.environ,'CONTRACT_RESULT':status,'REQUESTED_SCENARIOS':json.dumps(selected)},capture_output=True)
            self.assertEqual(result.returncode,expected,(selected,status))


class ProposedReleaseTests(unittest.TestCase):
    def test_release_selection_distinguishes_artifacts_from_drain_and_replicas(self):
        spec=importlib.util.spec_from_file_location('release_change',ROOT/'scripts/ci/kyverno_release_change.py')
        detector=importlib.util.module_from_spec(spec);spec.loader.exec_module(detector)
        baseline=yaml.safe_load((ROOT/'infra/kyverno/helmrelease.yaml').read_text())
        prepared=copy.deepcopy(baseline)
        prepared['spec']['values']['admissionController']['container']['lifecycle']={'preStop':{'sleep':{'seconds':10}}}
        prepared['spec']['values']['admissionController']['replicas']=3
        self.assertEqual(detector.release_inputs(baseline),detector.release_inputs(prepared))
        for key in ['tag','digest','registry','repository']:
            changed=copy.deepcopy(prepared)
            changed['spec']['values']['admissionController']['container']['image'][key]='changed'
            self.assertNotEqual(detector.release_inputs(baseline),detector.release_inputs(changed),key)
        changed=copy.deepcopy(baseline);changed['spec']['chart']['spec']['version']+='-changed'
        self.assertNotEqual(detector.release_inputs(baseline),detector.release_inputs(changed))
        changed=copy.deepcopy(baseline);changed['spec']['values']['upgrade']={'acknowledgeLegacyPoliciesNotEnforced':True}
        self.assertNotEqual(detector.release_inputs(baseline),detector.release_inputs(changed))

    def fixture(self,chart='3.11.2',version='1.21.4',signed=True):
        blob=io.BytesIO()
        with tarfile.open(fileobj=blob,mode='w:gz') as archive:
            raw=yaml.safe_dump(dict(version=chart,appVersion='v'+version)).encode()
            info=tarfile.TarInfo('kyverno/Chart.yaml');info.size=len(raw);archive.addfile(info,io.BytesIO(raw))
        import hashlib
        digest=hashlib.sha256(blob.getvalue()).hexdigest()
        cli=f'kyverno-cli_v{version}_linux_x86_64.tar.gz';chartname=f'kyverno-{chart}.tgz'
        assets=[dict(name=n,digest='sha256:'+digest) for n in [cli,cli+'.sigstore.json',chartname,*([chartname+'.sigstore.json'] if signed else [])]]
        responses={'https://raw.githubusercontent.com/kyverno/website/main/src/content/docs/docs/installation/releases.md':f'| **Supported Release:** | v{version.rsplit(".",1)[0]} |\n| **Kubernetes Versions Supported:** | v1.34 - v1.37 |'.encode(),
                   'https://kyverno.github.io/kyverno/index.yaml':yaml.safe_dump(dict(entries=dict(kyverno=[dict(version=chart,appVersion='v'+version,digest=digest,urls=[chartname])]))).encode(),
                   'https://kyverno.github.io/kyverno/'+chartname:blob.getvalue(),
                   f'https://api.github.com/repos/kyverno/kyverno/releases/tags/v{version}':json.dumps(dict(draft=False,prerelease=False,assets=assets)).encode(),
                   f'https://api.github.com/repos/kyverno/kyverno/commits/v{version}':json.dumps(dict(sha='a'*40)).encode()}
        return responses

    def test_future_pair_is_tested_exactly_without_release_ceiling(self):
        resolver=module('resolve');responses=self.fixture()
        with tempfile.TemporaryDirectory() as tmp,patch.object(resolver,'fetch',side_effect=lambda url:responses[url]) as fetch:
            result=resolver.resolve('3.11.2',Path(tmp))
        self.assertEqual((result['chart'],result['application']),('3.11.2','1.21.4'))
        self.assertTrue(result['chart_signed'])
        self.assertTrue(all('1.20.0' not in str(call) and '1.19.2' not in str(call) for call in fetch.call_args_list))

    def test_missing_release_bundle_never_falls_back(self):
        resolver=module('resolve');responses=self.fixture(signed=False)
        with tempfile.TemporaryDirectory() as tmp,patch.object(resolver,'fetch',side_effect=lambda url:responses[url]):
            with self.assertRaises(AssertionError):resolver.resolve('3.11.2',Path(tmp))

    def test_major_change_requires_new_reviewed_contract(self):
        resolver=module('resolve');responses=self.fixture(version='2.0.0')
        with tempfile.TemporaryDirectory() as tmp,patch.object(resolver,'fetch',side_effect=lambda url:responses[url]):
            with self.assertRaisesRegex(AssertionError,'major change'):resolver.resolve('3.11.2',Path(tmp))

    def test_unsupported_kubernetes_fails_before_qualification(self):
        resolver=module('resolve');responses=self.fixture()
        responses['https://raw.githubusercontent.com/kyverno/website/main/src/content/docs/docs/installation/releases.md']=responses['https://raw.githubusercontent.com/kyverno/website/main/src/content/docs/docs/installation/releases.md'].replace(b'v1.34',b'v1.35')
        with tempfile.TemporaryDirectory() as tmp,patch.object(resolver,'fetch',side_effect=lambda url:responses[url]):
            with self.assertRaisesRegex(AssertionError,'unsupported'):resolver.resolve('3.11.2',Path(tmp))

    def test_unknown_or_prerelease_chart_cannot_select_older_artifacts(self):
        resolver=module('resolve')
        for chart in ['latest','3.10.*','3.11.0-beta.1']:
            with patch.object(resolver,'fetch') as fetch:
                with self.assertRaises(ValueError):resolver.resolve(chart,Path('/unreachable'))
                fetch.assert_not_called()


if __name__=='__main__':unittest.main()
