#!/usr/bin/env python3
"""Real admission and controller lifecycle rehearsal, confined to one kind context."""
import copy
import json
import os
import pathlib
import subprocess
import time
import traceback
import yaml

ROOT=pathlib.Path(__file__).resolve().parents[3]
WORK=ROOT/'.work/cel'
EVIDENCE=WORK/'evidence'
CONTEXT='kind-kyverno-cel-rehearsal'
RESULTS=[]
OWNER='homelab.cooked.beer/owner'

def run(args, data=None, check=True, timeout=90):
    p=subprocess.run(args,input=data,text=True,capture_output=True,timeout=timeout)
    if check and p.returncode: raise RuntimeError(f'{args}: {p.stdout}\n{p.stderr}')
    return p

def k(*args,data=None,check=True,timeout=90):
    return run(['kubectl','--context',CONTEXT,'--request-timeout=30s',*args],data,check,timeout)

def apply(doc): return k('apply','--server-side','--force-conflicts','-f','-',data=yaml.safe_dump_all(doc if isinstance(doc,list) else [doc]))
def delete(doc): return k('delete','--wait=true','--timeout=45s','--ignore-not-found=true','-f','-',data=yaml.safe_dump_all(doc if isinstance(doc,list) else [doc]))
def get(kind,name='',ns=''):
    args=['get',kind]+([name] if name else [])+(['-n',ns] if ns else ['-A'])+['-o','json']
    return json.loads(k(*args).stdout)
def record(name,**details):
    row=dict(name=name,**details); RESULTS.append(row); (EVIDENCE/'results.json').write_text(json.dumps(RESULTS,indent=2)+'\n'); print(json.dumps(row),flush=True)
def wait(label,fn,seconds=75):
    end=time.monotonic()+seconds; last=None
    while time.monotonic()<end:
        try:
            last=fn()
            if last: return last
        except (RuntimeError,KeyError,TypeError) as e: last=str(e)
        time.sleep(2)
    raise AssertionError(f'{label}: deadline exceeded; last={last}')

def ready(p):
    def check():
        obj=get(p['kind'],p['metadata']['name'],p['metadata'].get('namespace',''))
        return any(c['type']=='Ready' and c['status']=='True' for c in obj.get('status',{}).get('conditions',[]))
    wait(p['metadata']['name'],check)

def labels():
    return {OWNER:'test',**{'pod-security.kubernetes.io/'+k:v for k,v in {'enforce':'privileged','enforce-version':'v1.34','audit':'restricted','audit-version':'v1.34','warn':'restricted','warn-version':'v1.34'}.items()}}
def namespace(name): return dict(apiVersion='v1',kind='Namespace',metadata=dict(name=name,labels=labels()))
def load(path): return list(yaml.safe_load_all((ROOT/path).read_text()))
def install(version):
    chart={'1.19.2':'3.9.2','1.20.0':'3.10.0'}[version]
    chartfile=WORK/f'kyverno-{chart}.tgz'
    run(['curl','-fsSL','--connect-timeout','15','--max-time','120',f'https://kyverno.github.io/kyverno/kyverno-{chart}.tgz','-o',str(chartfile)])
    import hashlib
    digest=hashlib.sha256(chartfile.read_bytes()).hexdigest()
    if version=='1.20.0': assert digest=='55961392dc0821eb367323f0e9585c061dd4407ef7ada921bd56ea1642c2a301'
    values=load('infra/kyverno/helmrelease.yaml')[0]['spec']['values']
    values['admissionController']['replicas']=1
    values['features']['backgroundScan']={'backgroundScanInterval':'20s'}
    # Additional isolated permissions are scoped to these three output kinds.
    values['backgroundController']['rbac']={'clusterRole':{'extraResources':[{'apiGroups':[''],'resources':['limitranges','resourcequotas'],'verbs':['get','list','watch','create','update','patch','delete']},{'apiGroups':['networking.k8s.io'],'resources':['networkpolicies'],'verbs':['get','list','watch','create','update','patch','delete']}]}}
    if version=='1.20.0':
        for component,image in [('admissionController','kyverno'),('backgroundController','background-controller'),('cleanupController','cleanup-controller'),('reportsController','reports-controller')]:
            sig=json.loads((WORK/f'trust/{image}.signature.json').read_text())
            target=values[component]['container']['image'] if component=='admissionController' else values[component]['image']
            target['digest']=sig[0]['critical']['image']['docker-manifest-digest']
    vp=WORK/'values.yaml'; vp.write_text(yaml.safe_dump(values))
    run(['helm','upgrade','--install','kyverno',str(chartfile),'--namespace','kyverno','--create-namespace','--kube-context',CONTEXT,'--values',str(vp),'--wait','--timeout','4m'],timeout=270)
    record('install',status='verified',version=version,chart=chart,chart_sha256=digest)

def admission(doc,operation='CREATE',check_policy=True,user=None):
    args=['create' if operation=='CREATE' else 'replace','--dry-run=server','-f','-','-o','json']
    if user: args+=['--as',user]
    p=k(*args,data=yaml.safe_dump(doc),check=False)
    allowed=p.returncode==0
    if not allowed and check_policy and not any(x in p.stderr for x in ['kyverno','policy-exception-accountability']):
        raise AssertionError(f'Non-policy denial: {p.stderr}')
    if not allowed and any(x in p.stderr.lower() for x in ['failed calling webhook','no endpoints','context deadline','invalid expression','undeclared reference','no such key']):
        raise AssertionError(f'Infrastructure/evaluation error is not a policy denial: {p.stderr}')
    return dict(allowed=allowed,warning='Warning:' in p.stderr,stderr=p.stderr)

def smoke():
    for name in ['gitea-jobs','wowdecomp-ci-jobs','cel-tests','longhorn-system','gpu-operator','wireguard','wotlk','home-assistant','cloudflared','homepage']:
        apply(namespace(name))
    # Compile/admit every draft validation document before behavioral tests.
    for path in ['namespace.yaml','workloads.yaml','runners.yaml','flux.yaml']:
        docs=load('migration/kyverno/policies/'+path)
        for p in docs:
            apply(p); ready(p)
            record('candidate-policy-ready',status='verified',policy=p['metadata']['name'])
        delete(docs)

def governance(version):
    old=load('infra/kyverno/policies/policy-exception-accountability.yaml')[0]
    if version=='1.19.2': apply(old); ready(old)
    native=load('migration/kyverno/policies/governance-native.yaml')
    for mode in (['original','native'] if version=='1.19.2' else ['native']):
        if mode=='native':
            apply(native)
            time.sleep(3)
        for group,api in [('legacy','kyverno.io/v2'),('cel','policies.kyverno.io/v1')]:
            if version=='1.20.0' and group=='legacy': continue
            base=load('infra/kyverno/exceptions/home-assistant.yaml')[0] if group=='legacy' else load('migration/kyverno/exceptions/exceptions.yaml')[1]
            base=copy.deepcopy(base); base['apiVersion']=api; base['metadata']['name']='accountability-probe'
            cases=[('valid',base)]
            for field,container in [(OWNER,'labels'),('kyverno.cooked.beer/reason','annotations'),('kyverno.cooked.beer/expires-at','annotations'),('github.cooked.beer/issue-create','annotations')]:
                for mutation in ['missing','empty']:
                    d=copy.deepcopy(base)
                    if mutation=='missing': d['metadata'][container].pop(field,None)
                    else: d['metadata'][container][field]=''
                    cases.append((field+'-'+mutation,d))
            for name,doc in cases:
                result=admission(doc)
                expected=mode=='original' or name=='valid'
                assert result['allowed']==expected,(version,mode,group,name,result)
                record('governance-create',status='verified',version=version,mode=mode,api=api,case=name,**result)
            # A valid stored object exercises update admission, not just dry-run CREATE.
            apply(base)
            stored=get('policyexceptions.'+api.split('/')[0],base['metadata']['name'],'kyverno')
            for name,doc in cases:
                d=copy.deepcopy(stored); d['metadata']['labels']=doc['metadata']['labels']; d['metadata']['annotations']=doc['metadata']['annotations']
                result=admission(d,'UPDATE'); expected=mode=='original' or name=='valid'
                assert result['allowed']==expected,(version,mode,group,name,result)
                record('governance-update',status='verified',version=version,mode=mode,api=api,case=name,**result)
            delete(base)
        if mode=='native': delete(native)
    if version=='1.19.2': delete(old)

def main():
    assert os.environ.get('REHEARSAL_CONTEXT')==CONTEXT
    config=json.loads(k('config','view','--minify','-o','json').stdout)
    endpoint=config['clusters'][0]['cluster']['server']
    assert endpoint.startswith('https://127.0.0.1:'),endpoint
    assert get('nodes')['items'][0]['status']['nodeInfo']['kubeletVersion']=='v1.34.0'
    assert not get('gitrepositories.source.toolkit.fluxcd.io')['items']
    install('1.19.2')
    smoke()
    governance('1.19.2')
    from equivalence import suite
    suite('1.19.2','legacy')
    suite('1.19.2','cel')
    from generation import before_upgrade
    generation_state=before_upgrade()
    record('integration-baseline',status='verified')
    if os.environ.get('CANDIDATE_TRUST')!='success':
        record('candidate-upgrade',status='not run',reason='artifact trust failed'); return
    install('1.20.0')
    smoke()
    governance('1.20.0')
    suite('1.20.0','cel')
    if generation_state['passed']:
        from generation import after_upgrade
        after_upgrade(generation_state)

if __name__=='__main__':
    import sys
    sys.modules['rehearse']=sys.modules[__name__]
    try: main()
    except Exception as e:
        record('rehearsal',status='failed',error=str(e)); traceback.print_exc(); raise
