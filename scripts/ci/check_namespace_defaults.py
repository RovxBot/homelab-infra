#!/usr/bin/env python3
"""Validate rendered, explicitly enrolled namespace bundles and Flux consumers."""
import argparse
from pathlib import Path
import sys
import json
import subprocess
import yaml

ROOT = Path(__file__).resolve().parents[2]
LABEL = 'homelab.cooked.beer/bootstrap'
CONTRACT = 'homelab.cooked.beer/namespace-contract'
DECOMMISSION = 'homelab.cooked.beer/decommission'
PRUNE = 'kustomize.toolkit.fluxcd.io/prune'
RESERVED = {'default', 'flux-system', 'kube-node-lease', 'kube-public', 'kube-system', 'kyverno'}


def expected(root=ROOT):
    old = yaml.safe_load((root/'infra/kyverno/policies/platform-namespace-bootstrap.yaml').read_text())
    return {(r['generate']['kind'], r['generate']['name']): r['generate']['data']['spec'] for r in old['spec']['rules']}


def check_bundle(docs, root=ROOT):
    errors = []
    namespaces = [d for d in docs if d['kind'] == 'Namespace' and (d['metadata'].get('labels', {}).get(LABEL) == 'enabled' or d['metadata'].get('annotations', {}).get(CONTRACT) == 'flux-v1')]
    wants = expected(root)
    for ns in namespaces:
        meta = ns['metadata']; name = meta['name']; labels = meta.get('labels', {}); annotations = meta.get('annotations', {})
        if labels.get(LABEL) != 'enabled':
            errors.append(f'{name}: contract intent requires the bootstrap marker')
        if name in RESERVED or name == 'namespace-placeholder':
            errors.append(f'{name}: reserved or unsubstituted namespace')
        if labels.get('app.kubernetes.io/managed-by') != 'Flux' or annotations.get(CONTRACT) != 'flux-v1':
            errors.append(f'{name}: label-only enrollment is unsupported')
        keys = ['homelab.cooked.beer/owner'] + ['pod-security.kubernetes.io/'+k for k in ['enforce', 'enforce-version', 'audit', 'audit-version', 'warn', 'warn-version']]
        if any(not labels.get(key) for key in keys):
            errors.append(f'{name}: missing owner/PSA labels')
        retiring = annotations.get(DECOMMISSION) == 'approved'
        if not retiring and annotations.get(PRUNE) != 'disabled':
            errors.append(f'{name}: namespace must be prune protected')
        for (kind, output), spec in wants.items():
            found = [d for d in docs if d['kind'] == kind and d['metadata']['name'] == output and d['metadata'].get('namespace') == name]
            if len(found) != 1:
                errors.append(f'{name}: requires exactly one {kind}/{output} in the same bundle')
                continue
            obj = found[0]; m = obj['metadata']
            if obj.get('spec') != spec:
                errors.append(f'{name}: {kind} differs from legacy specification')
            if m.get('labels', {}).get('app.kubernetes.io/managed-by') != 'Flux' or m.get('labels', {}).get(LABEL) != 'enabled':
                errors.append(f'{name}: {kind} lacks Flux contract labels')
            if not retiring and m.get('annotations', {}).get(PRUNE) != 'disabled':
                errors.append(f'{name}: {kind} must be prune protected')
        for obj in [ns] + [d for d in docs if d['metadata'].get('namespace') == name]:
            options = obj['metadata'].get('annotations', {})
            if options.get('kustomize.toolkit.fluxcd.io/ssa') in {'Ignore','IfNotPresent'} or options.get('kustomize.toolkit.fluxcd.io/reconcile') == 'disabled':
                errors.append(f'{name}: Flux must continuously reconcile every bundle object')
    # Outputs cannot be enrolled without their Namespace in this reconciliation.
    enrolled = {d['metadata']['name'] for d in namespaces}
    for obj in docs:
        if obj['kind'] != 'Namespace' and obj['metadata'].get('labels', {}).get(LABEL) == 'enabled' and (obj['kind'], obj['metadata']['name']) in wants:
            if obj['metadata'].get('namespace') not in enrolled:
                errors.append('Bootstrap output without its enrolled Namespace')
    return errors


def check_graph(entries, root=ROOT):
    errors = []; enrolled = {}; owners = {}
    for ks, docs in entries:
        errors += check_bundle(docs, root)
        for obj in docs:
            if obj['kind'] != 'Namespace' or obj['metadata'].get('labels', {}).get(LABEL) != 'enabled':
                continue
            ns = obj['metadata']['name']; meta = ks['metadata']; spec = ks['spec']
            if ns in enrolled:
                errors.append(f'{ns}: multiple namespace bundle owners')
            enrolled[ns] = obj; owners[ns] = meta['name']
            if meta['name'] != 'namespace-'+ns or meta.get('namespace') != 'flux-system' or meta.get('labels', {}).get('homelab.cooked.beer/namespace') != ns:
                errors.append(f'{ns}: namespace Kustomization identity differs')
            if spec.get('wait') is not True or spec.get('prune') is not True or spec.get('deletionPolicy') != 'Orphan' or spec.get('suspend'):
                errors.append(f'{ns}: namespace readiness/prune/orphan contract differs')
            if any(spec.get(key) for key in ['postBuild','patches','images','components','namePrefix','nameSuffix','targetNamespace','serviceAccountName','kubeConfig','healthCheckExprs']):
                errors.append(f'{ns}: put bundle transformations in the reviewed Kustomize source')
            if any(d['kind'] not in {'Namespace','LimitRange','ResourceQuota','NetworkPolicy'} for d in docs):
                errors.append(f'{ns}: bundle must contain defaults only')
            if len(docs) != 4:
                errors.append(f'{ns}: namespace bundle must contain exactly four resources')
    for ks, docs in entries:
        deps = {d['name'] for d in ks['spec'].get('dependsOn', []) if d.get('namespace', 'flux-system') == 'flux-system'}
        for obj in docs:
            if obj['kind']=='Namespace' and obj['metadata']['name'] in enrolled and ks['metadata']['name']!=owners[obj['metadata']['name']]:
                errors.append(f'{obj["metadata"]["name"]}: competing Namespace declaration outside its bundle')
            ns = obj['metadata'].get('namespace', '')
            # HelmRelease can select a different target namespace.
            if obj['kind'] == 'HelmRelease':
                ns = obj.get('spec', {}).get('targetNamespace', ns)
            if ns not in enrolled or ks['metadata']['name'] == owners[ns]:
                continue
            if (obj['kind'],obj['metadata']['name']) in expected(root):
                errors.append(f'{ns}: competing default resource outside its bundle')
            if enrolled[ns]['metadata'].get('annotations', {}).get(DECOMMISSION) == 'approved':
                errors.append(f'{ns}: remove consumers before decommissioning')
            if owners[ns] not in deps:
                errors.append(f'{ns}: {ks["metadata"]["name"]} must depend on {owners[ns]}')
            if any(d['name'] == owners[ns] and d.get('readyExpr') for d in ks['spec'].get('dependsOn', [])):
                errors.append(f'{ns}: namespace dependency must retain the built-in readiness check')
    return sorted(set(errors))


def production_entries(root):
    from kustomize_inputs import production_entries as discover_production
    return discover_production(root)


def check_live(objects, root=ROOT):
    """Detect retained bundles whose Flux owner or inventory no longer reconciles."""
    errors = []
    docs = objects['namespaces']['items'][:]
    for resource in ['limitranges', 'resourcequotas', 'networkpolicies']:
        docs += objects[resource]['items']
    enrolled = {d['metadata']['name']: d for d in docs if d['kind'] == 'Namespace'
                and (d['metadata'].get('labels', {}).get(LABEL) == 'enabled'
                     or d['metadata'].get('annotations', {}).get(CONTRACT) == 'flux-v1')}
    owners = {(d['metadata'].get('namespace'), d['metadata']['name']): d
              for d in objects['kustomizations']['items']}
    for name, ns in enrolled.items():
        bundle = [ns] + [d for d in docs if d['metadata'].get('namespace') == name
                         and (d['kind'], d['metadata']['name']) in expected(root)]
        errors += check_bundle(bundle, root)
        owner_name = 'namespace-' + name
        owner = owners.get(('flux-system', owner_name))
        if owner is None:
            errors.append(f'{name}: orphaned bundle; Flux Kustomization {owner_name} is absent')
            continue
        errors += check_graph([(owner, bundle)], root)
        status = owner.get('status', {})
        ready = any(c.get('type') == 'Ready' and c.get('status') == 'True'
                    and c.get('observedGeneration') == owner['metadata']['generation']
                    for c in status.get('conditions', []))
        if not ready or status.get('observedGeneration') != owner['metadata']['generation']:
            errors.append(f'{name}: Flux bundle owner is not Ready at its current generation')
        inventory = {entry['id'] for entry in status.get('inventory', {}).get('entries', [])}
        for obj in bundle:
            meta = obj['metadata']; group = obj['apiVersion'].split('/')[0] if '/' in obj['apiVersion'] else ''
            identity = '_'.join([meta.get('namespace', ''), meta['name'], group, obj['kind']])
            labels = meta.get('labels', {})
            if labels.get('kustomize.toolkit.fluxcd.io/name') != owner_name or labels.get('kustomize.toolkit.fluxcd.io/namespace') != 'flux-system' or identity not in inventory:
                errors.append(f'{name}: {obj["kind"]}/{meta["name"]} is orphaned from the expected Flux inventory')
    for obj in docs:
        if obj['kind'] != 'Namespace' and (obj['kind'], obj['metadata']['name']) in expected(root) and obj['metadata'].get('labels', {}).get(LABEL) == 'enabled' and obj['metadata'].get('namespace') not in enrolled:
            errors.append(f'{obj["metadata"].get("namespace")}: orphaned default without an enrolled Namespace')
    return sorted(set(errors))


def collect_live():
    objects = {}
    for resource in ['namespaces', 'limitranges', 'resourcequotas', 'networkpolicies', 'kustomizations']:
        result = subprocess.run(['kubectl', '--request-timeout=20s', 'get', resource, '-A', '-o', 'json'],
                                text=True, capture_output=True, check=True, timeout=30)
        data = json.loads(result.stdout)
        if not isinstance(data.get('items'), list) or data.get('metadata', {}).get('continue'):
            raise ValueError('Incomplete live inventory: ' + resource)
        objects[resource] = data
    return objects


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=ROOT)
    parser.add_argument('--live', action='store_true', help='read-only live orphan, ownership, readiness and default-spec check')
    args = parser.parse_args(); root = args.repo_root.resolve()
    if args.live:
        try:
            errors = check_live(collect_live(), root)
        except (subprocess.SubprocessError, ValueError, KeyError) as error:
            parser.exit(1, f'Live namespace collection unavailable: {error}\n')
        if errors:
            parser.exit(1, '\n'.join(errors)+'\n')
        print('verified live namespace bundles: no orphaned resources or inactive Flux owners')
        return
    sys.path.insert(0, str(root/'scripts/ci'))
    from kyverno_gate import render_kustomization
    entries = production_entries(root)
    example = root/'scripts/ci/kyverno/fixtures/namespace-example/flux.yaml'
    samples = [(ks, render_kustomization(root, ks['spec']['path'])) for ks in yaml.safe_load_all(example.read_text())]
    errors = check_graph(entries, root) + check_graph(samples, root)
    if errors:
        parser.exit(1, '\n'.join(errors)+'\n')
    print(f'verified namespace bundle contracts and dependencies across {len(entries)} production reconciliations and the isolated example')


if __name__ == '__main__':
    main()
