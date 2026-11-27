#!/usr/bin/env python3
"""Immutable #2299 render comparison: BASELINE_CHECKOUT CANDIDATE_CHECKOUT OUTPUT_JSON.

Uses Kustomize 5.5.0 and the candidate production-graph helpers. No live API
requests, Secret decryption, resource payload export or cluster mutations.
"""
import copy,hashlib,json,re,sys
from pathlib import Path
baseline=Path(sys.argv[1]).resolve();candidate=Path(sys.argv[2]).resolve();output=Path(sys.argv[3])
sys.path.insert(0,str(candidate/'scripts/ci'))
from kustomize_inputs import production_entries,render_roots
from check_production_graph import normalized_resource,scopes_for

def render(root):
 rs=render_roots(root);es=production_entries(root,rs);scopes=scopes_for(d for _,ds in es for d in ds)
 return {(k['metadata']['name'],normalized_resource(k,d,scopes)[0]):normalized_resource(k,d,scopes)[1] for k,ds in es for d in ds}
b=render(baseline);a=render(candidate)
removed=set(b)-set(a);added=set(a)-set(b);common=set(a)&set(b)
rotations=[];names={}
for old in sorted(removed):
 if old[1][1]!='ConfigMap' or old[0]=='apps-openbrew':continue
 matches=[k for k in added if k[0]==old[0] and k[1][:3]==old[1][:3] and k[1][3].rsplit('-',1)[0]==old[1][3].rsplit('-',1)[0]]
 assert len(matches)==1,(old,matches)
 new=matches[0];names[old[1][3]]=new[1][3];rotations.append((old,new))
logical=removed-{x for x,_ in rotations}
assert len(rotations)==6 and set(k for _,k in rotations)==added
assert len(logical)==15 and all(k[0]=='apps-openbrew' or k[1][2]=='openbrew' or k[1][3]=='apps-openbrew' for k in logical)
def rename(d):
 if isinstance(d,dict):return {k:rename(v) for k,v in d.items()}
 if isinstance(d,list):return [rename(v) for v in d]
 return names.get(d,d) if isinstance(d,str) else d

def paths(b,a,p=''):
 if b==a:return []
 if isinstance(a,dict) and isinstance(b,dict):
  return [s for k in sorted(set(b)|set(a)) for s in paths(b.get(k),a.get(k),p+'/'+k)]
 if isinstance(a,list) and isinstance(b,list) and len(a)==len(b):return [s for i,(x,y) in enumerate(zip(b,a)) for s in paths(x,y,p+'/'+str(i))]
 return [p]
def strip_namespace(x):
 if isinstance(x,dict):return {k:strip_namespace(v) for k,v in x.items()}
 if isinstance(x,list):return [strip_namespace(v) for v in x if v!='openbrew']
 if isinstance(x,str):return re.sub(r'^ +-[ ]+openbrew\n', '', x.replace(',openbrew,',','), flags=re.M)
 return x
modified=[];unchanged=[]
for k in sorted(common):
 if b[k]==a[k]:unchanged.append(k);continue
 expected=rename(b[k]);kind=k[1][1];name=k[1][3]
 if kind=='HelmRelease' and name=='gatus':expected['spec']['values']['config']['endpoints']=[e for e in expected['spec']['values']['config']['endpoints'] if e['name'] not in ('openbrew','openbrew-auth')]
 elif kind=='HelmRelease' and name=='kubescape' or kind=='ValidatingPolicy' and name=='kubescape-runtime-boundary':expected=strip_namespace(expected)
 elif kind=='Deployment' and name=='security-receiver':expected['spec']['template']['spec']['containers'][0]['image']='ghcr.io/rovxbot/security-receiver@sha256:1e3e1ccdf9757b3935db1fb67c054e289405a3c0764a004cb8c0f4e9b5c0f12a'
 assert expected==a[k],(k,paths(expected,a[k]))
 modified.append({'owner':k[0],'identity':k[1],'changedPaths':paths(b[k],a[k])})
allowed_data={'homepage-app-config':{'services.yaml'},'cloudflared-config':{'config.yaml'},'security-incident-publisher-code':{'publisher.py'},'kubescape-assessment-code':{'runner.py'},'kubescape-assessment-config':{'config.json'},'security-receiver-config':{'config.json'}}
rotation_rows=[]
for old,new in rotations:
 expected=rename(b[old]);keys={k for k in set(expected['data'])|set(a[new]['data']) if expected['data'].get(k)!=a[new]['data'].get(k)}
 assert keys==allowed_data[old[1][3].rsplit('-',1)[0]],(old,keys)
 for key in keys:expected['data'][key]=a[new]['data'][key]
 assert expected==a[new],(old,paths(expected,a[new]))
 rotation_rows.append({'owner':old[0],'beforeIdentity':old[1],'afterIdentity':new[1],'changedDataKeys':sorted(keys)})
assert len(modified)==12 and len(unchanged)==536,(len(modified),len(unchanged))
hashdoc=lambda d:hashlib.sha256(json.dumps(d,sort_keys=True,separators=(',',':')).encode()).hexdigest()
result={'status':'verified','baseline':'1c6123472e4bad4f1024c560f6b36843cdb76300','logicalRemovals':[{'owner':k[0],'identity':k[1],'disposition':'retained-prune-disabled' if k[1][1] in ('Namespace','PersistentVolumeClaim') else 'Flux-prune'} for k in sorted(logical)],'generatedConfigMapRotations':rotation_rows,'modifiedResources':modified,'unchangedResourceCount':len(unchanged),'unchangedResourceHashes':[{'owner':k[0],'identity':k[1],'sha256':hashdoc(a[k])} for k in sorted(unchanged)],'unexpectedAddedIdentities':[],'unexpectedRemovedIdentities':[],'unexpectedDocumentChanges':[]}
output.write_text(json.dumps(result,indent=2)+'\n')
print({k:v for k,v in result.items() if k not in ('logicalRemovals','generatedConfigMapRotations','modifiedResources','unchangedResourceHashes')})
print('verified: 15 logical removals, 6 ConfigMap rotations, 12 modified consumers/contracts; 536 canonical document hashes unchanged')
