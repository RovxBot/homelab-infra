#!/usr/bin/env python3
"""Apply review patches only in a disposable filesystem; verify phase boundaries."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import yaml

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/ci'))
from kyverno_gate import render_kustomization


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-renovate',action='store_true')
    args=parser.parse_args()
    rows=[]
    with tempfile.TemporaryDirectory(prefix='kyverno-activation-review-') as tmp:
        folder=Path(tmp)
        names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode().split('\0')
        for name in filter(None,names):
            source=ROOT/name
            if source.is_file():
                target=folder/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        subprocess.run(['git','init','-q'],cwd=folder,check=True)
        for patch in sorted((ROOT/'migration/kyverno/activation').glob('*.patch')):
            subprocess.run(['git','apply','--check',str(patch)],cwd=folder,check=True)
            subprocess.run(['git','apply',str(patch)],cwd=folder,check=True)
            docs=render_kustomization(folder,'infra/kyverno-policies')
            for path in ['apps/gitea-runners','clusters/home/infrastructure']:
                render_kustomization(folder,path)
            hr=yaml.safe_load((folder/'infra/kyverno/helmrelease.yaml').read_text())
            assert not hr['spec']['values']['backgroundController'].get('rbac'),'New generator write RBAC is forbidden'
            assert hr['spec']['values']['admissionController']['container']['lifecycle']['preStop']=={'sleep':{'seconds':10}}
            assert not any(d['kind'] in ['GeneratingPolicy','NamespacedGeneratingPolicy'] for d in docs),'Abandoned generator activation is forbidden'
            legacy=[d for d in docs if d['apiVersion'].startswith('kyverno.io/')]
            frozen=any(d['metadata']['name']=='freeze-namespace-bootstrap' for d in docs)
            if patch.name.startswith(('02a-','02b-','02c-','02d-')):assert frozen
            if patch.name.startswith('02b-'):
                bootstrap=next(d for d in legacy if d['metadata']['name']=='platform-namespace-bootstrap')
                assert all(r['generate']['orphanDownstreamOnPolicyDelete'] for r in bootstrap['spec']['rules'])
            if patch.name.startswith(('02c-','02d-','02e-','03-','04-')):
                assert not any(d['metadata']['name']=='platform-namespace-bootstrap' for d in legacy)
            if patch.name.startswith(('02d-','02e-','03-','04-')):
                assert len([d for d in docs if d['kind']=='ValidatingAdmissionPolicy' and d['metadata']['name'].startswith('flux-')])==4
            if patch.name.startswith(('02e-','03-','04-')):assert not frozen
            if patch.name.startswith(('01-','02')):
                assert len([d for d in docs if d['kind']=='PolicyException' and d['apiVersion'].startswith('kyverno.io/')])==6
                assert len([d for d in docs if d['kind']=='PolicyException' and d['apiVersion'].startswith('policies.kyverno.io/')])==6
            if patch.name.startswith(('03-','04-')):
                assert not legacy
                assert sum(d['kind']=='NamespacedValidatingPolicy' for d in docs)==15
            if not patch.name.startswith('04-'):assert hr['spec']['chart']['spec']['version']=='3.9.2'
            rows.append(dict(patch=patch.name,status='verified',legacy_objects=len(legacy),render_sha256=hashlib.sha256(yaml.safe_dump_all(docs,sort_keys=True).encode()).hexdigest()))
        assert hr['spec']['chart']['spec']['version']=='3.10.0'
        assert hr['spec']['values']['upgrade']['legacyPolicyCheck']['enabled'] is True
        assert hr['spec']['values']['upgrade']['acknowledgeLegacyPoliciesNotEnforced'] is False
        assert hr['spec']['upgrade']['crds']=='CreateReplace'
        assert hr['spec']['values']['admissionController']['replicas']==2
        assert not any(r.get('allowedVersions')=='<3.10.0' for r in json.loads((folder/'renovate.json').read_text())['packageRules'])
        if args.verify_renovate:
            command=['npx','--yes','--package','renovate@44.126.1','-c','renovate-config-validator --strict renovate.json && node --test .github/tests/renovate-policy.test.cjs']
            subprocess.run(command,cwd=folder,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=180)
    print(json.dumps(dict(status='verified',scope='filesystem only; production not contacted',stages=rows,renovate_after_cap_removal='verified' if args.verify_renovate else 'not run'),indent=2))


if __name__=='__main__':main()
