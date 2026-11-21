"""Focused qualification. Reuse unchanged 596-case validation evidence."""
import copy
import json
import os
import traceback
from rehearse import EVIDENCE, RESULTS, apply, delete, get, k, load, namespace, ready, record, wait, install
from generation import Flux, OUTPUTS, freeze, thaw, queue_drained, snapshot
from diagnostics import Diagnostics
from inventory import summarize


def inventory():
    objects={kind:get(kind) for kind in ['namespaces','limitranges','resourcequotas','networkpolicies.networking.k8s.io','updaterequests.kyverno.io']}
    summary=summarize(objects)
    record('empty-state-inventory', **summary)
    assert summary['status']=='verified', summary


def correct(ns):
    rows=snapshot(ns)
    legacy=load('infra/kyverno/policies/platform-namespace-bootstrap.yaml')[0]
    expected=[r['generate']['data']['spec'] for r in legacy['spec']['rules']]
    for actual,want in zip(rows,expected):
        for key,value in want.items():
            if actual['spec'].get(key)!=value:
                return False
    return not rows[2]['spec'].get('ingress') and not rows[2]['spec'].get('egress')


def restart(diag,stage):
    diag.snapshot(stage+'-before-restart')
    k('rollout','restart','deployment/kyverno-background-controller','-n','kyverno')
    k('rollout','status','deployment/kyverno-background-controller','-n','kyverno','--timeout=90s',timeout=100)
    diag.snapshot(stage+'-before-quota-drift')
    k('patch','resourcequota','namespace-defaults','-n',diag.ns,'--type=merge','-p','{"spec":{"hard":{"pods":"21"}}}')
    try:
        wait(stage+' quota reconciliation',lambda:correct(diag.ns))
    finally:
        diag.snapshot(stage+'-after-quota-drift')
    record('generator-restart-drift',status='verified',stage=stage,namespace=diag.ns)


def empty_cutover(flux,ns):
    legacy=load('infra/kyverno/policies/platform-namespace-bootstrap.yaml')[0]
    gp=load('migration/kyverno/policies/generator.yaml')[0]
    flux.commit([legacy],'empty-legacy-active');ready(legacy)
    locks=freeze()
    # Freeze must cover CREATE and UPDATE. Nothing has enrolled yet.
    probe=get('namespaces',ns)
    probe['metadata']['labels']['homelab.cooked.beer/bootstrap']='enabled'
    from rehearse import admission
    denied=admission(probe,'UPDATE',check_policy=False)
    assert not denied['allowed'] and 'rehearsal-freeze-bootstrap' in denied['stderr'],denied
    inventory()
    orphan=copy.deepcopy(legacy)
    for rule in orphan['spec']['rules']:
        rule['generate']['orphanDownstreamOnPolicyDelete']=True
    flux.commit([orphan],'empty-orphan-before-prune')
    assert all(r['generate']['orphanDownstreamOnPolicyDelete'] for r in get('clusterpolicies','platform-namespace-bootstrap')['spec']['rules'])
    inventory()
    flux.commit([],'empty-retire-legacy')
    assert not get('clusterpolicies')['items']
    wait('legacy queue drained',queue_drained)
    k('rollout','restart','deployment/kyverno-background-controller','-n','kyverno')
    k('rollout','status','deployment/kyverno-background-controller','-n','kyverno','--timeout=90s',timeout=100)
    inventory()
    flux.commit([gp],'empty-cel-active');ready(gp)
    inventory()
    thaw(locks,ns)
    record('empty-state-cutover',status='verified',freeze_operations=['CREATE','UPDATE'])


def enroll(ns):
    trigger=get('namespace',ns)
    trigger['metadata']['labels']['homelab.cooked.beer/bootstrap']='enabled'
    apply(trigger)
    wait('three exact bootstrap outputs',lambda:correct(ns))


def lifecycle(flux,diag):
    ns=diag.ns
    k('annotate','namespace',ns,'--overwrite','rehearsal.cooked.beer/trigger=still-eligible')
    wait('eligible trigger update retains output',lambda:correct(ns))
    for kind,name in OUTPUTS:
        k('delete',kind,name,'-n',ns,'--wait=true')
        wait('recreate deleted '+kind,lambda:correct(ns))
    diag.snapshot('before-label-removal')
    k('label','namespace',ns,'homelab.cooked.beer/bootstrap-')
    wait('ineligible trigger removes downstreams',lambda:all(not get(kind,ns=ns)['items'] for kind,_ in OUTPUTS))
    diag.snapshot('after-label-removal')
    enroll(ns)
    # Default orphan=false semantics must be observed, not inferred from logs.
    flux.commit([],'delete-cel-policy-with-orphan-false')
    wait('policy deletion removes downstreams',lambda:all(not get(kind,ns=ns)['items'] for kind,_ in OUTPUTS))
    diag.snapshot('after-policy-delete')
    gp=load('migration/kyverno/policies/generator.yaml')[0]
    flux.commit([gp],'restore-cel-for-trigger-delete');ready(gp)
    k('annotate','namespace',ns,'--overwrite','rehearsal.cooked.beer/trigger=recreate')
    wait('restore outputs',lambda:correct(ns))
    delete(namespace(ns))
    wait('deleted trigger absent',lambda:not any(n['metadata']['name']==ns for n in get('namespaces')['items']))
    diag.snapshot('after-trigger-delete')
    record('generator-lifecycle',status='verified',namespace=ns)


def rollback_empty(flux,ns):
    # This rollback is valid only while the production inventory is empty.
    # Never delete or unenrol real namespaces to make the predicate pass.
    apply(namespace(ns))
    locks=freeze()
    wait('rollback queued work drains',queue_drained)
    # Completed URs may be retained briefly; empty path requires ZERO URs.
    wait('rollback inventory becomes empty',lambda:not get('updaterequests.kyverno.io')['items'])
    inventory()
    flux.commit([],'empty-rollback-remove-cel')
    install('1.19.2')
    inventory()
    legacy=load('infra/kyverno/policies/platform-namespace-bootstrap.yaml')[0]
    flux.commit([legacy],'empty-rollback-restore-legacy');ready(legacy)
    inventory()
    thaw(locks,ns)
    enroll(ns)
    record('empty-state-rollback',status='verified',version='1.19.2',new_trigger_outputs=snapshot(ns))


def main():
    scenario=os.environ['REHEARSAL_SCENARIO']
    if scenario=='mutation':
        from mutation import run as mutation
        install('1.19.2')
        mutation('1.19.2','legacy');mutation('1.19.2','cel')
        install(os.environ['KYVERNO_TARGET_VERSION'])
        mutation(os.environ['KYVERNO_TARGET_VERSION'],'cel')
        record('scenario',status='verified',scenario=scenario)
        return
    ns='generation-existing' if scenario=='adopted' else 'generation-'+scenario
    apply(namespace(ns))
    install('1.19.2' if scenario!='fresh-target' else os.environ.get('KYVERNO_TARGET_VERSION','1.20.0'))
    diag=Diagnostics(ns);diag.start()
    traffic=None
    try:
        if scenario=='adopted':
            from generation import before_upgrade
            state=before_upgrade()
            assert state['passed'],state
            diag.snapshot('before-upgrade')
            install(os.environ.get('KYVERNO_TARGET_VERSION','1.20.0'))
            restart(diag,'adopted-target')
        else:
            with Flux() as flux:
                if scenario=='empty-upgraded':
                    empty_cutover(flux,ns)
                else:
                    gp=load('migration/kyverno/policies/generator.yaml')[0]
                    flux.commit([gp],'fresh-target-cel');ready(gp)
                from traffic import Traffic
                traffic=Traffic(ns);traffic.prepare()
                enroll(ns)
                traffic.start()
                diag.snapshot('initial-output')
                restart(diag,'fresh-baseline' if scenario=='empty-upgraded' else 'fresh-target')
                if scenario=='empty-upgraded':
                    diag.snapshot('before-upgrade')
                    install(os.environ.get('KYVERNO_TARGET_VERSION','1.20.0'))
                    restart(diag,'upgraded-target')
                traffic.stop();traffic=None
                lifecycle(flux,diag)
                if scenario=='fresh-target':
                    rollback_empty(flux,ns)
        record('scenario',status='verified',scenario=scenario)
    except Exception as error:
        diag.snapshot('failure')
        record('scenario',status='failed',scenario=scenario,error=str(error))
        traceback.print_exc()
    finally:
        if traffic and hasattr(traffic,'thread'):
            traffic.stop()
        diag.snapshot('final');diag.stop()
    assert not any(r.get('status')=='failed' for r in RESULTS), 'Qualification failed; inspect complete snapshots and watches'
