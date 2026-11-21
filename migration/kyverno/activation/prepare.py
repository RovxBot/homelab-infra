#!/usr/bin/env python3
"""Generate review-only sequential Git patches. Never contact Kubernetes or apply them."""
import copy
import difflib
import json
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
state={}


def read(path):
    return state.get(path,(ROOT/path).read_text() if (ROOT/path).exists() else '')


def dump(doc):return yaml.safe_dump(doc,sort_keys=False)


def stage(name,changes):
    patch=''
    for path,new in changes.items():
        old=read(path)
        patch+=''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/'+path if old else '/dev/null',tofile='b/'+path))
        state[path]=new
    (OUT/(name+'.patch')).write_text(patch)


# Protect namespace UIDs before moving Flux inventories. Existing policies remain.
protect={}
for path in ['apps/gitea-runners/namespaces.yaml','apps/gitea-runners/wowdecomp/resources.yaml']:
    parts=read(path).split('\n---\n')
    for i,part in enumerate(parts):
        doc=yaml.safe_load(part)
        if doc['kind']=='Namespace' and 'kustomize.toolkit.fluxcd.io/prune' not in doc['metadata'].get('annotations',{}):
            parts[i]=part.replace('  annotations:\n','  annotations:\n    kustomize.toolkit.fluxcd.io/prune: disabled\n',1)
    protect[path]='\n---\n'.join(parts)
runner_paths=tuple(protect)
p='infra/kyverno/helmrelease.yaml';hr=yaml.safe_load(read(p))
hr['spec']['values']['admissionController']['container']['lifecycle']={'preStop':{'sleep':{'seconds':10}}}
protect[p]=dump(hr)
stage('00-protect-runner-namespaces-and-admission-drain',protect)

changes={};namespaces=[]
for path in runner_paths:
    parts=read(path).split('\n---\n')
    namespaces += [yaml.safe_load(part) for part in parts if yaml.safe_load(part)['kind']=='Namespace']
    changes[path]='\n---\n'.join(part for part in parts if yaml.safe_load(part)['kind']!='Namespace')
changes['infra/kyverno/runner-namespaces.yaml']=yaml.safe_dump_all(namespaces,sort_keys=False)
p='infra/kyverno/kustomization.yaml';doc=yaml.safe_load(read(p));doc['resources'].append('runner-namespaces.yaml');changes[p]=dump(doc)
cel=['workloads','namespace','flux','runners','governance-native','mutation','reporting-rbac']
paths=['../../migration/kyverno/policies/'+name+'.yaml' for name in cel]+['../../migration/kyverno/exceptions/exceptions.yaml']
p='infra/kyverno-policies/kustomization.yaml';aggregate=yaml.safe_load(read(p));aggregate['resources']+=paths;changes[p]=dump(aggregate)
p='clusters/home/infrastructure/kyverno-policies-kustomization.yaml';ks=yaml.safe_load(read(p))
policies=[]
for name in ['workloads','namespace','flux','runners','mutation']:
    policies+=list(yaml.safe_load_all((ROOT/'migration/kyverno/policies'/f'{name}.yaml').read_text()))
ks['spec']['healthChecks']=[dict(apiVersion=d['apiVersion'],kind=d['kind'],name=d['metadata']['name'],**({'namespace':d['metadata']['namespace']} if d['metadata'].get('namespace') else {})) for d in policies]
ks['spec']['healthCheckExprs']=[dict(apiVersion='policies.kyverno.io/v1',kind=kind,
    current="has(status.conditionStatus) && status.conditionStatus.ready && status.conditionStatus.conditions.all(c, c.status == 'True')") for kind in ['ValidatingPolicy','NamespacedValidatingPolicy','MutatingPolicy']]
changes[p]=dump(ks)
p='clusters/home/apps/gitea-runners-kustomization.yaml';app=yaml.safe_load(read(p));app['spec']['dependsOn'].append({'name':'kyverno-policies'});changes[p]=dump(app)
stage('01-overlap-cel-and-reporting',changes)

p='infra/kyverno-policies/kustomization.yaml';aggregate=yaml.safe_load(read(p));freeze='../../migration/kyverno/activation/freeze.yaml';aggregate['resources'].append(freeze);stage('02a-freeze', {p:dump(aggregate)})
p='infra/kyverno/policies/kustomization.yaml';old=yaml.safe_load(read(p))
old['patches']=[dict(target=dict(kind='ClusterPolicy',name='platform-namespace-bootstrap'),patch=yaml.safe_dump([dict(op='add',path=f'/spec/rules/{i}/generate/orphanDownstreamOnPolicyDelete',value=True) for i in range(3)]))]
stage('02b-orphan-legacy-before-retirement',{p:dump(old)})
old['resources'].remove('platform-namespace-bootstrap.yaml');old.pop('patches')
stage('02c-retire-empty-legacy-generator',{p:dump(old)})
p='infra/kyverno-policies/kustomization.yaml';aggregate=yaml.safe_load(read(p));aggregate['resources'].append('../namespace-defaults/admission.yaml')
stage('02d-qualify-flux-onboarding-contract',{p:dump(aggregate)})
aggregate['resources'].remove(freeze);stage('02e-thaw',{p:dump(aggregate)})
aggregate['resources']=[r for r in aggregate['resources'] if r not in ['../kyverno/policies','../kyverno/exceptions']]
changes={p:dump(aggregate)}
for p in ['apps/gitea-runners/kustomization.yaml','apps/gitea-runners/wowdecomp/kustomization.yaml']:
    doc=yaml.safe_load(read(p));doc['resources'].remove('job-security.yaml');changes[p]=dump(doc)
stage('03-retire-legacy-after-acceptance',changes)

p='infra/kyverno/helmrelease.yaml';hr=yaml.safe_load(read(p));hr['spec']['chart']['spec']['version']='3.10.0'
hr['spec']['values']['upgrade']={'legacyPolicyCheck':{'enabled':True,'image':{'registry':'ghcr.io'}},'acknowledgeLegacyPoliciesNotEnforced':False}
changes={p:dump(hr)}
p='renovate.json';renovate=json.loads(read(p));renovate['packageRules']=[rule for rule in renovate['packageRules'] if not (rule.get('matchPackageNames')==['kyverno'] and rule.get('allowedVersions')=='<3.10.0')];changes[p]=json.dumps(renovate,indent=2)+'\n'
p='.github/tests/renovate-policy.test.cjs';changes[p]=read(p).replace("assert.equal(kyverno.allowedVersions, '<3.10.0');","assert.equal(kyverno.allowedVersions, undefined);")
p='docs/runbooks/renovate-operations.md';changes[p]=read(p).replace('Kyverno remains `<3.10.0`;','Kyverno uses the proposed chart/application compatibility gate with a separate high-risk PR and no automerge;')
stage('04-qualified-upgrade-and-renovate',changes)
print('Prepared review patches only; no active entrypoints changed.')
