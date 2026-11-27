#!/usr/bin/env python3
"""Render retained roots and validate combined production references/ownership."""
import argparse
from collections import defaultdict
import copy
import json
from pathlib import Path
import subprocess
import sys

from check_secret_refs import ALLOWLIST_NAMES, production_refs_from_doc
from kustomize_inputs import InputError, production_entries, render_roots

# Kubernetes API scope contracts, keyed by group/kind, never inferred from a
# document's namespace. Rendered CRDs extend these and must agree on scope.
SCOPE_GROUPS = {
    ('', 'Cluster'): 'Namespace Node PersistentVolume',
    ('', 'Namespaced'): 'ConfigMap Secret Service ServiceAccount Pod PersistentVolumeClaim LimitRange ResourceQuota ReplicationController Endpoints',
    ('apps', 'Namespaced'): 'Deployment StatefulSet DaemonSet ReplicaSet ControllerRevision',
    ('batch', 'Namespaced'): 'Job CronJob',
    ('rbac.authorization.k8s.io', 'Cluster'): 'ClusterRole ClusterRoleBinding',
    ('rbac.authorization.k8s.io', 'Namespaced'): 'Role RoleBinding',
    ('networking.k8s.io', 'Namespaced'): 'Ingress NetworkPolicy',
    ('networking.k8s.io', 'Cluster'): 'IngressClass',
    ('storage.k8s.io', 'Cluster'): 'StorageClass CSIDriver CSINode VolumeAttachment',
    ('node.k8s.io', 'Cluster'): 'RuntimeClass',
    ('scheduling.k8s.io', 'Cluster'): 'PriorityClass',
    ('policy', 'Namespaced'): 'PodDisruptionBudget',
    ('autoscaling', 'Namespaced'): 'HorizontalPodAutoscaler',
    ('apiextensions.k8s.io', 'Cluster'): 'CustomResourceDefinition',
    ('apiregistration.k8s.io', 'Cluster'): 'APIService',
    ('admissionregistration.k8s.io', 'Cluster'): 'ValidatingAdmissionPolicy ValidatingAdmissionPolicyBinding MutatingWebhookConfiguration ValidatingWebhookConfiguration',
    # Helm-installed CRDs absent from Flux source renders. Verified against:
    # longhorn/longhorn v1.13.0 chart/templates/crds.yaml
    ('longhorn.io', 'Namespaced'): 'BackupTarget Node RecurringJob Setting',
    # kubescape-operator 1.42.0 charts/kubescape-alert-crd/crds/runtime-rule-binding.crd.yaml
    ('kubescape.io', 'Cluster'): 'RuntimeRuleAlertBinding',
    # kyverno/kyverno v1.20.0 config/crds/policies.kyverno.io/*.yaml
    ('policies.kyverno.io', 'Cluster'): 'ValidatingPolicy MutatingPolicy',
    ('policies.kyverno.io', 'Namespaced'): 'NamespacedValidatingPolicy PolicyException',
}
WOTLK = ('', 'Namespace', '', 'wotlk')
WOTLK_OWNERS = {('flux-system', 'infrastructure'), ('flux-system', 'apps-wotlk')}


def scopes_for(documents):
    scopes = {(group, kind): scope for (group, scope), kinds in SCOPE_GROUPS.items() for kind in kinds.split()}
    for doc in documents:
        if doc['apiVersion'].startswith('apiextensions.k8s.io/') and doc['kind'] == 'CustomResourceDefinition':
            spec = doc['spec']
            key = (spec['group'], spec['names']['kind'])
            scope = spec['scope']
            if scope not in {'Namespaced', 'Cluster'} or (key in scopes and scopes[key] != scope):
                raise InputError(f'Invalid or conflicting resource scope: {key}')
            scopes[key] = scope
    return scopes


def normalized_resource(owner, doc, scopes):
    meta = doc['metadata']
    api = doc['apiVersion']
    group = api.split('/', 1)[0] if '/' in api else ''
    kind = doc['kind']
    scope = scopes.get((group, kind))
    if scope is None:
        raise InputError(f'Unknown resource scope: {group}/{kind}; qualify its CRD contract')
    ns = '' if scope == 'Cluster' else owner['spec'].get('targetNamespace') or meta.get('namespace') or 'default'
    desired = copy.deepcopy(doc)
    if ns:
        desired['metadata']['namespace'] = ns
    return (group, kind, ns, meta['name']), desired


def validate(entries):
    scopes = scopes_for(doc for _, docs in entries for doc in docs)
    occurrences = defaultdict(list)
    refs = []
    declared = set()
    owners = set()
    errors = []
    for ks, docs in entries:
        owner = (ks['metadata'].get('namespace', 'default'), ks['metadata']['name'])
        if owner in owners:
            errors.append(f'Duplicate Flux owner entry: {owner}')
        owners.add(owner)
        for doc in docs:
            identity, desired = normalized_resource(ks, doc, scopes)
            occurrences[identity].append((owner, desired))
            group, kind, ns, name = identity
            if group == '' and kind == 'Secret':
                declared.add((ns, name))
            for ref in production_refs_from_doc(desired, ns or None):
                refs.append((owner, identity, ref))
    for identity, copies in sorted(occurrences.items()):
        membership = [owner for owner, _ in copies]
        if len(membership) != len(set(membership)):
            errors.append(f'Duplicate resource occurrence: {identity}; owners={membership}')
        if any(doc != copies[0][1] for _, doc in copies[1:]):
            errors.append(f'Conflicting desired documents: {identity}; owners={membership}')
        if identity == WOTLK:
            if len(copies) != 2 or set(membership) != WOTLK_OWNERS:
                errors.append(f'Namespace/wotlk requires exact reviewed owners; owners={membership}')
        elif len(set(membership)) > 1:
            errors.append(f'Unexpected multiple ownership: {identity}; owners={membership}')
    if WOTLK not in occurrences:
        errors.append('Missing Namespace/wotlk and its reviewed owners')
    for owner, identity, (ns, name) in refs:
        if name not in ALLOWLIST_NAMES and (ns, name) not in declared:
            errors.append(f'Unresolved Secret {ns or "<no-namespace>"}/{name}; owner={owner}; resource={identity}')
    receipt = {
        'owners': [{'namespace': ks['metadata'].get('namespace', 'default'), 'name': ks['metadata']['name'],
                    'path': ks['spec']['path'], 'resources': len(docs), 'dependsOn': ks['spec'].get('dependsOn', [])}
                   for ks, docs in entries],
        'ownerCount': len(owners),
        'dependencyEdges': sum(len(ks['spec'].get('dependsOn', [])) for ks, _ in entries),
        'ownerResourceEntries': sum(len(docs) for _, docs in entries),
        'distinctResourceIdentities': len(occurrences),
        'duplicateIdentities': [{'identity': identity, 'owners': [owner for owner, _ in copies]}
                                for identity, copies in sorted(occurrences.items()) if len(copies) > 1],
        'secretReferenceCount': len(refs),
        'allowlistedReferences': [{'owner': owner, 'resource': identity, 'namespace': ns, 'name': name}
                                 for owner, identity, (ns, name) in refs if name in ALLOWLIST_NAMES],
    }
    return sorted(set(errors)), receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        rendered = render_roots(args.repo_root.resolve())
        entries = production_entries(args.repo_root.resolve(), rendered)
        errors, receipt = validate(entries)
        receipt['roots'] = list(rendered)
        receipt['rootCount'] = len(rendered)
        print(json.dumps(receipt, indent=2))  # identities/counts only; no resource payloads
        for error in errors:
            print(error, file=sys.stderr)
        return int(bool(errors))
    except InputError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        # YAML/renderer exceptions can embed Secret contents. Keep failures safe
        # even for malformed inputs; unit tests inspect specific safe exceptions.
        print('Production graph collection failed; check source paths, resource documents and scope contracts (payload suppressed).', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
