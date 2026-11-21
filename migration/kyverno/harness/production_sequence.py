"""Overlap -> observed legacy prune -> Flux Helm upgrade -> compatible rollback."""
import base64
import json
import threading
import time
import yaml
from rehearse import ROOT, WORK, EVIDENCE, apply, get, install, k, load, namespace, ready, record, run, smoke, wait


def policies(engine):
    if engine == 'legacy':
        paths = ['infra/kyverno/policies/'+name+'.yaml' for name in ['platform-pod-standards','platform-namespace-standards','platform-flux-guardrails','longhorn-csi-registrar-lifecycle','policy-exception-accountability']]
        paths += ['apps/gitea-runners/job-security.yaml','apps/gitea-runners/wowdecomp/job-security.yaml']
        paths += [str(p.relative_to(ROOT)) for p in sorted((ROOT/'infra/kyverno/exceptions').glob('*.yaml')) if p.name!='kustomization.yaml']
    else:
        paths = ['migration/kyverno/policies/'+name+'.yaml' for name in ['workloads','namespace','flux','runners','governance-native','mutation','reporting-rbac']]
        paths += ['migration/kyverno/exceptions/exceptions.yaml']
    return [d for path in paths for d in load(path)]


def check_policies(docs):
    # Exception rule refs and match unions are unordered. Preserve every
    # member/duplicate/field while comparing full scope.
    def canonical(value):
        if isinstance(value,dict):return {k:canonical(v) for k,v in value.items()}
        if isinstance(value,list):return sorted((canonical(v) for v in value),key=lambda v:json.dumps(v,sort_keys=True))
        return value
    for doc in docs:
        if doc['kind'] in ['ClusterPolicy','Policy','ValidatingPolicy','NamespacedValidatingPolicy','MutatingPolicy']:
            ready(doc)
        if doc['kind']=='PolicyException':
            live=get(doc['kind']+'.'+doc['apiVersion'].split('/')[0],doc['metadata']['name'],doc['metadata']['namespace'])
            expected=dict(doc['spec'])
            if doc['apiVersion'].startswith('policies.kyverno.io/'):
                # The actual CRD defaults this reporting field on admission.
                # Keep scope and every other field exact; reject other modes.
                expected.setdefault('reportResult','skip')
            assert canonical(live['spec'])==canonical(expected), (doc['apiVersion'],doc['metadata']['name'],live['spec'],expected)
            assert live['metadata']['annotations']==doc['metadata']['annotations'], doc['metadata']['name']


class RunnerContinuity:
    def __enter__(self):
        self.stop=threading.Event();self.samples=[]
        def probe():
            while not self.stop.is_set():
                for pool in ['core','wowdecomp']:
                    try:
                        p=run(['python3',str(ROOT/'ops/gitea/check-job-policy.py'),'--server','--pool',pool],check=False,timeout=120)
                        row=dict(at=time.time(),pool=pool,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
                    except Exception as error:
                        row=dict(at=time.time(),pool=pool,returncode=1,stdout='',stderr=str(error),collection='unavailable')
                    self.samples.append(row)
                    (EVIDENCE/'continuous-runner-admission.json').write_text(json.dumps(self.samples,indent=2)+'\n')
                    if row['returncode']:
                        return
                self.stop.wait(3)
        self.thread=threading.Thread(target=probe,daemon=True);self.thread.start()
        return self

    def checkpoint(self,stage):
        assert self.samples and all(r['returncode']==0 for r in self.samples), self.samples[-2:]
        record('continuous-runner-checkpoint',status='verified',stage=stage,samples=len(self.samples))

    def __exit__(self,*args):
        self.stop.set();self.thread.join(timeout=250)
        assert not self.thread.is_alive()
        self.checkpoint('complete')


def storage(stage):
    rows=[]
    for crd in get('customresourcedefinitions')['items']:
        spec=crd['spec']
        if spec['group'] not in ['policies.kyverno.io','kyverno.io']:
            continue
        versions=[dict(name=v['name'],served=v['served'],storage=v['storage']) for v in spec['versions']]
        stored=crd['status']['storedVersions']
        assert all(v in [x['name'] for x in versions] for v in stored)
        objects={}
        if spec['group']=='policies.kyverno.io':
            for v in versions:
                if v['served']:
                    raw=json.loads(k('get','--raw',f'/apis/{spec["group"]}/{v["name"]}/{spec["names"]["plural"]}').stdout)
                    assert not raw.get('metadata',{}).get('continue')
                    objects[v['name']]=sorted((d['metadata'].get('namespace',''),d['metadata']['name'],d['metadata']['uid']) for d in raw['items'])
            assert all(value==next(iter(objects.values())) for value in objects.values()), objects
        rows.append(dict(name=crd['metadata']['name'],uid=crd['metadata']['uid'],versions=versions,storedVersions=stored,objects=objects))
    (EVIDENCE/('storage-'+stage+'.json')).write_text(json.dumps(rows,indent=2)+'\n')
    return rows


def stored_policy(stage):
    # Read one public policy fixture from THIS disposable kind etcd only. Do not
    # export keys, credentials or unrelated objects. This proves encoding rather
    # than inferring migration from a CRD status field.
    key='/registry/policies.kyverno.io/validatingpolicies/platform-flux-guardrails'
    result=json.loads(k('exec','-n','kube-system','etcd-kyverno-cel-rehearsal-control-plane','--',
        'etcdctl','--endpoints=https://127.0.0.1:2379','--cacert=/etc/kubernetes/pki/etcd/ca.crt',
        '--cert=/etc/kubernetes/pki/etcd/healthcheck-client.crt','--key=/etc/kubernetes/pki/etcd/healthcheck-client.key',
        'get',key,'--write-out=json').stdout)
    assert result.get('count')==1,result.get('count')
    obj=json.loads(base64.b64decode(result['kvs'][0]['value']))
    record('actual-etcd-storage',status='verified',stage=stage,apiVersion=obj['apiVersion'],uid=obj['metadata']['uid'])
    return obj['apiVersion']


def seed_storage_history():
    before=stored_policy('baseline')
    crd=get('crd','validatingpolicies.policies.kyverno.io')
    assert any(v['name']=='v1' and v['served'] for v in crd['spec']['versions'])
    assert before!='policies.kyverno.io/v1'
    for version in crd['spec']['versions']:version['storage']=version['name']=='v1'
    crd.pop('status',None)
    apply(crd)
    k('annotate','validatingpolicy','platform-flux-guardrails','--overwrite','rehearsal.cooked.beer/storage-seed=v1')
    wait('historical storage fixture is actually encoded v1',lambda:stored_policy('seed')=='policies.kyverno.io/v1')
    record('storage-history-fixture',status='verified',method='isolated CRD spec.storage change and real API write; never a manual storedVersions status patch')


def check_chart_storage(version,rows):
    target=json.loads((WORK/'trust/target.json').read_text())
    chart=WORK/('trust/kyverno-'+target['chart']+'.tgz') if version==target['application'] else WORK/'kyverno-3.9.2.tgz'
    rendered=run(['helm','template','kyverno',str(chart),'--include-crds','--namespace','kyverno']).stdout
    expected={d['metadata']['name']:d for d in yaml.safe_load_all(rendered) if isinstance(d,dict) and d.get('kind')=='CustomResourceDefinition' and d['spec']['group']=='policies.kyverno.io'}
    versions={}
    for row in rows:
        if row['name'] not in expected:continue
        spec=expected[row['name']]['spec']
        desired={v['name']:(v['served'],v['storage']) for v in spec['versions']}
        actual={v['name']:(v['served'],v['storage']) for v in row['versions']}
        storage_version=next(name for name,flags in desired.items() if flags[1])
        assert actual==desired and row['storedVersions']==[storage_version],row
        versions[row['name']]=storage_version
    assert versions.keys()==expected.keys()
    record('chart-storage-contract',status='verified',version=version,storage_versions=versions)
    return versions


def hooks(version):
    release=json.loads(run(['helm','status','kyverno','-n','kyverno','--kube-context','kind-kyverno-cel-rehearsal','-o','json']).stdout)
    rows=[dict(name=h['name'],events=h.get('events'),last_run=h.get('last_run')) for h in release.get('hooks',[])]
    (EVIDENCE/('helm-hooks-'+version+'.json')).write_text(json.dumps(rows,indent=2)+'\n')
    assert any('migrate' in r['name'] and r.get('last_run',{}).get('phase')=='Succeeded' for r in rows), rows
    if version!='1.19.2':
        assert any('legacy' in r['name'] and r.get('last_run',{}).get('phase')=='Succeeded' for r in rows), rows
    record('helm-safety-and-migration-hooks',status='verified',version=version,hooks=rows)


def flux_release_evidence(version):
    target=json.loads((WORK/'trust/target.json').read_text())
    expected=(target['chart_sha256'] if version==target['application'] else 'f6fe7184544d5e2ee33a77808c09e67aa603ce6560cc197038fde31d727c861e')
    release=get('helmrelease','kyverno','kyverno')
    chart_ns,chart_name=release['status']['helmChart'].split('/')
    chart=get('helmchart',chart_name,chart_ns)
    assert chart['status']['artifact']['digest']=='sha256:'+expected, chart['status']
    (EVIDENCE/('flux-chart-'+version+'.json')).write_text(json.dumps(chart,indent=2)+'\n')
    replicas={}
    for role in ['admission','background','cleanup','reports']:
        deployment=get('deployment','kyverno-'+role+'-controller','kyverno')
        expected_replicas=2 if role=='admission' else 1
        assert deployment['spec']['replicas']==expected_replicas and deployment['status'].get('readyReplicas')==expected_replicas, deployment['status']
        if role=='admission':
            assert deployment['spec']['template']['spec']['containers'][0]['lifecycle']['preStop']=={'sleep':{'seconds':10}}
        replicas[role]=expected_replicas
    record('flux-release-artifact-and-readiness',status='verified',version=version,chart_sha256=expected,ready_replicas=replicas)


def mutation_probe(stage):
    from mutation import cases, admission_object
    doc=cases()[0][1]
    result=admission_object(doc,'CREATE')
    assert result['allowed'],result
    containers=result['object']['spec']['template']['spec']['containers']
    assert 'preStop' not in containers[0]['lifecycle'] and containers[0]['lifecycle']['postStart']['exec']['command']==['/keep-this-hook']
    assert containers[1]['lifecycle']['preStop']['exec']['command']==['/bin/sh','-c','keep']
    record('overlap-longhorn-mutation',status='verified',stage=stage)


def report_probe(stage):
    apply(namespace('cel-reporting'))
    doc=dict(apiVersion='v1',kind='ConfigMap',metadata=dict(name='report-'+stage,namespace='cel-reporting',labels={'kustomize.toolkit.fluxcd.io/name':'rehearsal'}),data=dict(probe='audit'))
    apply(doc)
    uid=get('configmap',doc['metadata']['name'],'cel-reporting')['metadata']['uid']
    def findings():
        return [r for report in get('policyreports',ns='cel-reporting')['items'] for r in report.get('results',[])
                if r.get('policy')=='platform-flux-guardrails' and r.get('result')=='fail'
                and (report.get('scope',{}).get('uid')==uid or any(resource.get('uid')==uid for resource in r.get('resources',[])))]
    rows=wait('actual report automation input '+stage,findings)
    (EVIDENCE/('report-'+stage+'.json')).write_text(json.dumps(rows,indent=2)+'\n')
    record('report-consumer-live-input',status='verified',stage=stage,rows=len(rows))


def during(flux, guards):
    import os
    from qualify import report_access
    smoke()
    # Controllers were installed from components only. Supply the committed
    # Namespace governance metadata before enabling the complete policy set.
    apply(load('clusters/home/flux-system/namespace-security.yaml'))
    apply(load('infra/kyverno/namespace.yaml'))
    apply(namespace('local-path-storage'))
    legacy=policies('legacy');cel=policies('cel')
    flux.commit([*guards,*legacy,*cel],'overlap-every-policy-and-exception')
    check_policies([*legacy,*cel])
    report_access('1.19.2')
    # Governance and full ephemeral-container contracts were rerun separately;
    # this test keeps both complete runner suites active through every phase.
    install('1.19.2',via_flux=True)
    flux_release_evidence('1.19.2')
    mutation_probe('overlap');report_probe('overlap')
    seed_storage_history()
    before=storage('before-upgrade')
    with RunnerContinuity() as runner:
        wait('continuous runner positive/negative probes',lambda:len(runner.samples)>=2)
        runner.checkpoint('overlap')
        flux.commit([*guards,*cel],'retire-legacy-separate-from-helm-upgrade')
        for kind in ['clusterpolicies.kyverno.io','policies.kyverno.io','cleanuppolicies.kyverno.io','clustercleanuppolicies.kyverno.io','policyexceptions.kyverno.io']:
            assert not get(kind)['items'],kind
        assert not get('updaterequests.kyverno.io')['items']
        check_policies(cel);mutation_probe('legacy-retired');runner.checkpoint('legacy-retired')
        version=os.environ['KYVERNO_TARGET_VERSION']
        install(version,via_flux=True);flux_release_evidence(version);hooks(version)
        after=storage('after-upgrade')
        target_storage=check_chart_storage(version,after)
        assert stored_policy('after-upgrade')=='policies.kyverno.io/'+target_storage['validatingpolicies.policies.kyverno.io']
        retained=lambda rows:{r['name']:(r['uid'],next(iter(r['objects'].values()),[])) for r in rows if r['name'].endswith('.policies.kyverno.io')}
        assert retained(before)==retained(after),'CRD/object identities changed across migration'
        check_policies(cel);mutation_probe('upgraded');report_access(version);report_probe('upgraded');runner.checkpoint('upgraded')
        # Downgrade only after retained schemas can serve every stored version.
        install('1.19.2',via_flux=True);flux_release_evidence('1.19.2');hooks('1.19.2')
        rollback=storage('after-supported-downgrade')
        rollback_storage=check_chart_storage('1.19.2',rollback)
        assert stored_policy('after-downgrade')=='policies.kyverno.io/'+rollback_storage['validatingpolicies.policies.kyverno.io']
        assert retained(after)==retained(rollback)
        check_policies(cel);mutation_probe('rollback');report_probe('rollback');runner.checkpoint('rollback')
        # Keep CEL and native enforcement while restoring the reviewed legacy
        # validators on 1.19.2. The retired generator is never reintroduced once
        # Flux namespaces exist.
        flux.commit([*guards,*legacy,*cel],'supported-policy-rollback-with-flux-defaults')
        check_policies([*legacy,*cel]);runner.checkpoint('legacy-restored')
        record('storage-upgrade-and-rollback',status='verified',storage_matches_verified_chart=True,actual_encoding_rewrite_verified=True,identities_preserved=True,original_legacy_check=True,cel_enforcement_retained=True,generator_restored=False)
