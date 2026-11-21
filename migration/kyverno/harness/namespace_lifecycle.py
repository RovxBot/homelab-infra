"""Flux namespace lifecycle and native safeguards on an isolated enforcing CNI."""
import copy
import json
import shutil
import sys
import time
import yaml
from rehearse import ROOT, EVIDENCE, apply, admission, delete, get, k, load, namespace, ready, record, wait
from inventory import OUTPUTS, summarize

FLUX_USER = 'system:serviceaccount:flux-system:kustomize-controller'
NS = 'namespace-example'
PRUNE = 'kustomize.toolkit.fluxcd.io/prune'
DECOMMISSION = 'homelab.cooked.beer/decommission'


def inventory(stage):
    objects = {kind: get(kind) for kind in ['namespaces', *OUTPUTS, 'updaterequests.kyverno.io']}
    assert all(isinstance(v.get('items'), list) and not v.get('metadata', {}).get('continue') for v in objects.values())
    summary = summarize(objects)
    record('empty-state-inventory', stage=stage, **summary)
    assert summary['status'] == 'verified', summary


def empty_retirement(flux):
    legacy = load('infra/kyverno/policies/platform-namespace-bootstrap.yaml')[0]
    freeze = load('migration/kyverno/activation/freeze.yaml')
    flux.commit([legacy], 'legacy-generator-active'); ready(legacy)
    flux.commit([legacy, *freeze], 'freeze-before-empty-retirement')
    apply(namespace('freeze-update-probe'))
    for operation in ['CREATE', 'UPDATE']:
        probe = namespace('freeze-create-probe') if operation == 'CREATE' else get('namespace', 'freeze-update-probe')
        probe['metadata']['labels']['homelab.cooked.beer/bootstrap'] = 'enabled'
        wait('native freeze '+operation, lambda: not admission(probe, operation, check_policy=False)['allowed'])
        result = admission(probe, operation, check_policy=False)
        assert 'freeze-namespace-bootstrap' in result['stderr'], result
    assert admission(namespace('freeze-unrelated'), check_policy=False)['allowed']
    inventory('proven-freeze')
    orphan = copy.deepcopy(legacy)
    for rule in orphan['spec']['rules']:
        rule['generate']['orphanDownstreamOnPolicyDelete'] = True
    flux.commit([orphan, *freeze], 'observe-orphan-before-prune')
    assert all(r['generate']['orphanDownstreamOnPolicyDelete'] for r in get('clusterpolicy', legacy['metadata']['name'])['spec']['rules'])
    inventory('observed-orphan')
    flux.commit(freeze, 'retire-empty-legacy-generator')
    assert not k('get','clusterpolicy',legacy['metadata']['name'],'--ignore-not-found','-o','name').stdout.strip()
    k('rollout','restart','deployment/kyverno-background-controller','-n','kyverno')
    k('rollout','status','deployment/kyverno-background-controller','-n','kyverno','--timeout=90s',timeout=100)
    inventory('legacy-absent-no-cleanup-or-queued-work')
    # A real empty-state Git rollback is tested before any namespace enrolls.
    flux.commit([legacy, *freeze], 'rollback-empty-retirement'); ready(legacy)
    inventory('rollback-original-generator-restored-under-freeze')
    flux.commit([orphan, *freeze], 'rollback-reobserve-orphan')
    assert all(r['generate']['orphanDownstreamOnPolicyDelete'] for r in get('clusterpolicy', legacy['metadata']['name'])['spec']['rules'])
    inventory('rollback-orphan')
    flux.commit(freeze, 'retire-again-after-empty-rollback')
    assert not k('get','clusterpolicy',legacy['metadata']['name'],'--ignore-not-found','-o','name').stdout.strip()
    inventory('retirement-complete')
    guards = load('infra/namespace-defaults/admission.yaml')
    flux.commit([*freeze, *guards], 'install-reviewed-onboarding-safeguards')
    for guard in guards:
        if guard['kind'] == 'ValidatingAdmissionPolicy':
            wait('type check '+guard['metadata']['name'], lambda: 'typeChecking' in get(guard['kind'],guard['metadata']['name']).get('status',{}))
            live_guard = get(guard['kind'],guard['metadata']['name'])
            (EVIDENCE/('native-'+guard['metadata']['name']+'.json')).write_text(json.dumps(live_guard,indent=2)+'\n')
            assert not live_guard['status']['typeChecking'].get('expressionWarnings'), live_guard['status']
    inventory('safeguards-ready-before-thaw')
    flux.commit(guards, 'replace-freeze-with-gitops-contract')
    record('legacy-generator-retirement', status='verified', ownership_transfer=False, cel_generator_activated=False, empty_rollback='verified')
    return guards


def bundle():
    return list(yaml.safe_load_all(k('kustomize','--load-restrictor=LoadRestrictionsNone',str(ROOT/'migration/kyverno/namespace-example/namespace')).stdout))


def snapshots():
    return [get(kind, name, NS) for kind, name in OUTPUTS.items()]


def exact():
    old = load('infra/kyverno/policies/platform-namespace-bootstrap.yaml')[0]
    rows = snapshots()
    return all(obj['spec'] == rule['generate']['data']['spec'] for obj, rule in zip(rows, old['spec']['rules']))


def safeguards():
    docs = bundle()
    sys.path.insert(0, str(ROOT/'scripts/ci'))
    from check_namespace_defaults import check_bundle
    assert not check_bundle(docs)
    for i in range(4):
        assert check_bundle(docs[:i]+docs[i+1:]), i
    ns = copy.deepcopy(next(d for d in docs if d['kind']=='Namespace'))
    ns['metadata']['labels'].update({'kustomize.toolkit.fluxcd.io/name':'namespace-'+NS,'kustomize.toolkit.fluxcd.io/namespace':'flux-system'})
    for user in [None, FLUX_USER]:
        probe = copy.deepcopy(ns)
        probe['metadata']['annotations'].pop('homelab.cooked.beer/namespace-contract')
        result = admission(probe, user=user, check_policy=False)
        assert not result['allowed'] and 'flux-namespace-enrollment' in result['stderr'], result
    for field in ['homelab.cooked.beer/owner','pod-security.kubernetes.io/enforce']:
        probe = copy.deepcopy(ns); probe['metadata']['labels'].pop(field)
        result = admission(probe, user=FLUX_USER, check_policy=False)
        assert not result['allowed'] and 'flux-namespace-enrollment' in result['stderr'], result
    record('namespace-invalid-bundles', status='verified', checks=['each partial bundle rejected by CI','label-only rejected for humans and Flux','missing owner/PSA rejected'])


def write_bundle(flux, decommission=False, protected=True, empty=False, malformed=False):
    target = flux.repo/'migration/kyverno/namespace-example/namespace'
    target.mkdir(parents=True, exist_ok=True)
    docs = bundle()
    for obj in docs:
        if decommission and obj['kind']=='Namespace':
            obj['metadata']['annotations'][DECOMMISSION] = 'approved'
        if not protected:
            obj['metadata']['annotations'].pop(PRUNE)
        if malformed and obj['kind']=='ResourceQuota':
            obj['spec']['hard']['pods'] = 'twenty'
    # Exercise the actual reusable base + namespace transformation for normal
    # onboarding; flattened fixtures are only used for intentional invalid or
    # explicitly reviewed decommission/prune stages.
    if not (decommission or empty or malformed):
        base = flux.repo/'infra/namespace-defaults/base'
        base.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT/'infra/namespace-defaults/base', base, dirs_exist_ok=True)
        shutil.copy2(ROOT/'migration/kyverno/namespace-example/namespace/kustomization.yaml', target/'kustomization.yaml')
    else:
        (target/'resources.yaml').write_text(yaml.safe_dump_all(docs))
        (target/'kustomization.yaml').write_text(yaml.safe_dump(dict(apiVersion='kustomize.config.k8s.io/v1beta1',kind='Kustomization',resources=[] if empty else ['resources.yaml'])))


def run(flux, during=None):
    guards = empty_retirement(flux)
    safeguards()
    nsks, appks = load('migration/kyverno/namespace-example/flux.yaml')
    for ks in [nsks, appks]:
        ks['spec']['sourceRef']['name'] = 'rehearsal'
    workload = flux.repo/'migration/kyverno/namespace-example/workloads'
    shutil.copytree(ROOT/'migration/kyverno/namespace-example/workloads', workload)
    write_bundle(flux, malformed=True)
    flux.publish('malformed-bundle-blocks-workloads')
    flux.reconcile(nsks, 'malformed-bundle', expect_ready=False)
    flux.reconcile(appks, 'blocked-workloads', expect_ready=False)
    wait('malformed bundle rejected by Flux', lambda: any(c['type']=='Ready' and c['status']=='False' for c in get('kustomization',nsks['metadata']['name'],'flux-system').get('status',{}).get('conditions',[])))
    time.sleep(6)
    assert not k('get','configmap','workload-readiness-marker','-n',NS,'--ignore-not-found','-o','name').stdout.strip()
    write_bundle(flux)
    flux.publish('complete-reusable-namespace-bundle')
    flux.reconcile(nsks, 'complete-bundle')
    wait('exact legacy specifications', exact)
    live = get('namespace',NS)
    uid = live['metadata']['uid']
    rows = snapshots()
    assert all(any(f['manager']=='kustomize-controller' for f in d['metadata']['managedFields']) for d in [live, *rows])
    assert all(not d['metadata'].get('ownerReferences') for d in rows)
    (EVIDENCE/'flux-owned-defaults.json').write_text(json.dumps(rows,indent=2)+'\n')
    flux.reconcile(appks, 'workloads-after-complete-bundle')
    assert get('configmap','workload-readiness-marker',NS)
    k('rollout','status','deployment/readiness-probe','-n',NS,'--timeout=75s')
    record('namespace-creation-and-dependencies', status='verified', exact_specs=True, namespace_uid=uid, flux_owned=True)
    for obj in rows:
        bad = copy.deepcopy(obj); bad['spec'] = {}
        result = admission(bad,'UPDATE',check_policy=False)
        assert not result['allowed'], result
    # Preventive spec guards and actual metadata drift correction are distinct.
    def drift():
        for kind, name in OUTPUTS.items():
            k('annotate',kind,name,'-n',NS,'--overwrite',PRUNE+'=drift')
    def repaired():
        return exact() and all(d['metadata'].get('annotations',{}).get(PRUNE)=='disabled' for d in snapshots())
    drift(); wait('periodic Flux drift correction',repaired)
    for controller in ['source-controller','kustomize-controller']:
        k('rollout','restart','deployment/'+controller,'-n','flux-system')
        k('rollout','status','deployment/'+controller,'-n','flux-system','--timeout=90s',timeout=100)
    drift(); wait('Flux restart recovery without namespace event',repaired)
    assert get('namespace',NS)['metadata']['uid']==uid
    record('namespace-drift-and-flux-restart',status='verified',spec_drift='denied by native admission',metadata_drift='corrected from Git',bound_seconds=75)
    # An explicit isolated allow policy proves connectivity before removal.
    allow = dict(apiVersion='networking.k8s.io/v1',kind='NetworkPolicy',metadata=dict(name='positive-control',namespace=NS),spec=dict(podSelector={},policyTypes=['Ingress','Egress'],ingress=[{}],egress=[{}]))
    apply(allow)
    from traffic import Traffic
    traffic = Traffic(NS); traffic.prepare(); delete(allow); traffic.start()
    try:
        if during:
            during(flux, guards)
        else:
            k('rollout','restart','deployment/kustomize-controller','-n','flux-system')
            k('rollout','status','deployment/kustomize-controller','-n','flux-system','--timeout=90s',timeout=100)
        wait('post-restart exact defaults',exact)
    finally:
        traffic.stop()
    # Steady-state deletion is denied; approved isolated deletion is repaired.
    denied = k('delete','resourcequota','namespace-defaults','-n',NS,'--dry-run=server',check=False)
    assert denied.returncode and 'flux-default-resourcequota' in denied.stderr
    write_bundle(flux,decommission=True); flux.publish('reviewed-resource-deletion-rehearsal'); flux.reconcile(nsks,'deletion-approved')
    for kind,name in OUTPUTS.items():
        k('delete',kind,name,'-n',NS,'--as',FLUX_USER,'--wait=true')
        wait('Flux recreates '+kind,exact)
    write_bundle(flux); flux.publish('restore-steady-state'); flux.reconcile(nsks,'restore-steady-state')
    # Accidental Git removal and Kustomization deletion must preserve defaults.
    before = [d['metadata']['uid'] for d in snapshots()]
    write_bundle(flux,empty=True); flux.publish('accidental-source-removal'); flux.reconcile(nsks,'prune-protection')
    assert exact() and before == [d['metadata']['uid'] for d in snapshots()]
    k('delete','kustomization',nsks['metadata']['name'],'-n','flux-system','--wait=true')
    assert exact() and get('namespace',NS)['metadata']['uid']==uid
    write_bundle(flux); flux.publish('rollback-source-removal'); flux.reconcile(nsks,'rollback-source-removal')
    assert before == [d['metadata']['uid'] for d in snapshots()]
    record('namespace-prune-and-rollback',status='verified',resource_deletion_recreated=True,orphan_on_kustomization_delete=True,protected_uids_unchanged=True)
    # Remove consumers and observe emptiness before enabling destructive pruning.
    (workload/'kustomization.yaml').write_text(yaml.safe_dump(dict(apiVersion='kustomize.config.k8s.io/v1beta1',kind='Kustomization',resources=[])))
    flux.publish('decommission-remove-workloads'); flux.reconcile(appks,'decommission-workloads-pruned')
    k('delete','pods','--all','-n',NS,'--wait=true','--timeout=45s')
    assert not get('pods',ns=NS)['items'] and not get('persistentvolumeclaims',ns=NS)['items']
    assert not get('configmaps',ns=NS)['items'] or not k('get','configmap','workload-readiness-marker','-n',NS,'--ignore-not-found','-o','name').stdout.strip()
    write_bundle(flux,decommission=True,protected=False); flux.publish('reviewed-decommission'); flux.reconcile(nsks,'reviewed-decommission')
    write_bundle(flux,empty=True); flux.publish('decommission-prune'); flux.reconcile(nsks,'decommission-prune')
    wait('namespace decommission complete',lambda:not k('get','namespace',NS,'--ignore-not-found','-o','name').stdout.strip())
    record('namespace-decommission',status='verified',consumers_removed_first=True,pvc_inventory_empty=True,namespace_pruned=True)
