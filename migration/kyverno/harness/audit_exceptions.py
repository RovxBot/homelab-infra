#!/usr/bin/env python3
"""Read private snapshots/renders; emit only identities and violated rule names."""
import argparse
import copy
import fnmatch
import hashlib
import json
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/ci'))
from kyverno_gate import evaluate_resources, find_flux_render_paths, render_kustomization


def in_scope(obj, exception):
    meta=obj['metadata']
    values=dict(kinds=obj['kind'],namespaces=meta.get('namespace',''),names=meta['name'])
    return any(all(any(fnmatch.fnmatchcase(values[k],p) for p in patterns) for k,patterns in branch['resources'].items()) for branch in exception['spec']['match']['any'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-input',type=Path,required=True)
    parser.add_argument('--kyverno',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();folder=args.private_input
    sources={'manifests':[],'helm':[],'live':[]};hashes={}
    for path in find_flux_render_paths(ROOT/'clusters/home'):
        sources['manifests'] += render_kustomization(ROOT,path)
    for ns in ['longhorn-system','gpu-operator','wireguard','wotlk','cloudflared','homepage']:
        p=folder/(ns+'.json');sources['live']+=json.loads(p.read_text())['items'];hashes[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
    for p in folder.glob('*-helm.yaml'):
        sources['helm'] += [d for d in yaml.safe_load_all(p.read_text()) if isinstance(d,dict)]
        hashes[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
    original=yaml.safe_load((ROOT/'infra/kyverno/policies/platform-pod-standards.yaml').read_text())
    result=dict(status='verified',read_only=True,method='Kyverno 1.19.2 CLI; selected original rules, exact exception match scopes, no exceptions supplied; CREATE evaluation of current specs',input_sha256=hashes,exceptions={})
    for name in ['chart-managed-workloads','gpu-operator-workloads','mutable-image-tags','privileged-namespaces','wotlk-latest-tag']:
        ex=yaml.safe_load((ROOT/'infra/kyverno/exceptions'/f'{name}.yaml').read_text());rules=ex['spec']['exceptions'][0]['ruleNames']
        policy=copy.deepcopy(original);policy['spec']['rules']=[r for r in policy['spec']['rules'] if r['name'] in rules]
        work=folder/name;work.mkdir(exist_ok=True);policies=work/'policies';policies.mkdir(exist_ok=True)
        (policies/'rules.yaml').write_text(yaml.safe_dump(policy))
        rows={}
        for source,docs in sources.items():
            resources=work/source;resources.mkdir(exist_ok=True)
            unique={}
            for doc in docs:
                if doc.get('kind') in ['Deployment','StatefulSet','DaemonSet','Job','CronJob'] and in_scope(doc,ex):
                    key=(doc['kind'],doc['metadata'].get('namespace',''),doc['metadata']['name'])
                    # All variants are evaluated separately by identity + spec hash.
                    sha=hashlib.sha256(json.dumps(doc.get('spec'),sort_keys=True).encode()).hexdigest()
                    unique[(*key,sha)]=doc
            for i,doc in enumerate(unique.values()):
                # Strip status and server metadata; command/env/config content
                # remains private and is never emitted into the decision table.
                obj=dict(apiVersion=doc['apiVersion'],kind=doc['kind'],metadata={k:v for k,v in doc['metadata'].items() if k in ['name','namespace','labels','annotations']},spec=doc['spec'])
                (resources/f'{i}.yaml').write_text(yaml.safe_dump(obj))
            if not unique:
                rows[source]=dict(evaluated=0,violations=[]);continue
            failures=[]
            # Avoid collapsing distinct authored variants of the same resource.
            for path in resources.glob('*.yaml'):
                one=work/'one';one.mkdir(exist_ok=True)
                (one/'resource.yaml').write_bytes(path.read_bytes())
                failures += evaluate_resources(kyverno_bin=args.kyverno,repo_root=ROOT,policy_dir=policies,exception_files=[],resource_dir=one)
            rows[source]=dict(evaluated=len(unique),violations=[dict(kind=k[0],namespace=k[1],name=k[2],rules=sorted(v)) for k,v in sorted(group(failures).items())])
        result['exceptions'][name]=rows
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({name:{source:len(row['violations']) for source,row in rows.items()} for name,rows in result['exceptions'].items()},indent=2))


def group(failures):
    rows={}
    for f in failures: rows.setdefault((f.kind,f.namespace,f.name),set()).add(f.rule)
    return rows


if __name__=='__main__':main()
