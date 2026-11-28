#!/usr/bin/env python3
"""Review #2348's exact GitOps delta: BASELINE_CHECKOUT CANDIDATE_CHECKOUT OUTPUT_JSON.

Kustomize 5.5.0; offline renders only. No Secret decryption or payload export.
"""
import hashlib
import json
from pathlib import Path
import sys

baseline, candidate, output = map(Path, sys.argv[1:])
sys.path.insert(0, str(candidate.resolve() / 'scripts/ci'))
from kustomize_inputs import production_entries, render_roots
from check_production_graph import normalized_resource, scopes_for, validate


def render(root):
    roots = render_roots(root.resolve())
    entries = production_entries(root.resolve(), roots)
    errors, receipt = validate(entries)
    assert not errors, errors
    scopes = scopes_for(doc for _, docs in entries for doc in docs)
    docs = {(owner['metadata']['name'], normalized_resource(owner, doc, scopes)[0]):
            normalized_resource(owner, doc, scopes)[1]
            for owner, resources in entries for doc in resources}
    return docs, {'roots': sorted(roots), 'ownerCount': receipt['ownerCount']}


before, bmeta = render(baseline)
after, ameta = render(candidate)
removed, added = set(before) - set(after), set(after) - set(before)
rotations, names = [], {}
for old in sorted(removed):
    if old[1][1] != 'ConfigMap' or old[0] == 'apps-mop':
        continue
    matches = [new for new in added if new[0] == old[0] and new[1][:3] == old[1][:3]
               and new[1][3].rsplit('-', 1)[0] == old[1][3].rsplit('-', 1)[0]]
    assert len(matches) == 1, old
    new, = matches
    rotations.append((old, new))
    names[old[1][3]] = new[1][3]
logical = removed - {old for old, _ in rotations}
assert set(new for _, new in rotations) == added
assert all(owner == 'apps-mop' or identity[2] == 'mop'
           or identity[3] in ('apps-mop', 'runtime-skyfire-mariadb') for owner, identity in logical)
assert len(logical) == 20 and len(rotations) == 3, (len(logical), len(rotations))


def rename(value):
    if isinstance(value, dict):
        return {key: rename(item) for key, item in value.items()}
    if isinstance(value, list):
        return [rename(item) for item in value]
    return names.get(value, value) if isinstance(value, str) else value


def remove_namespace(value):
    if isinstance(value, dict):
        return {key: remove_namespace(item) for key, item in value.items()}
    if isinstance(value, list):
        return [remove_namespace(item) for item in value if item != 'mop']
    if isinstance(value, str):
        return value.replace(',mop,', ',').replace(',mop"', '"')
    return value


modified, unchanged = [], []
for key in sorted(set(before) & set(after)):
    if before[key] == after[key]:
        unchanged.append(key)
        continue
    expected = rename(before[key])
    if key[1][1:] in (('HelmRelease', 'kubescape', 'kubescape'),
                       ('ValidatingPolicy', '', 'kubescape-runtime-boundary')):
        expected = remove_namespace(expected)
        # The namespace env value is an unquoted YAML scalar after parsing.
        if key[1][1] == 'HelmRelease':
            env = expected['spec']['values']['nodeAgent']['env']
            env[0]['value'] = env[0]['value'].removesuffix(',mop')
    if key[1][1:3] == ('Deployment', 'kubescape') and key[1][3] == 'security-receiver':
        expected['spec']['template']['spec']['containers'][0]['image'] = (
            'ghcr.io/rovxbot/security-receiver@sha256:5ca6be935f42c5253913eb199b9cda8cb073f729a49f7e7ecdcee6f0868f340e')
    assert expected == after[key], ('Unexpected document change', key)
    modified.append(key)

rotation_rows = []
for old, new in rotations:
    expected = rename(before[old])
    prefix = old[1][3].rsplit('-', 1)[0]
    if prefix == 'security-incident-publisher-code':
        expected['data']['publisher.py'] = expected['data']['publisher.py'].replace(
            '                     "mop/StatefulSet/skyfire-mariadb",\n', '')
    elif prefix == 'kubescape-assessment-config':
        config = json.loads(expected['data']['config.json'])
        config['postureWorkloads'] = [w for w in config['postureWorkloads'] if not w.startswith('mop/')]
        config['postureLifecycle']['activeNamespaces'].remove('mop')
        assert config == json.loads(after[new]['data']['config.json'])
        expected['data']['config.json'] = after[new]['data']['config.json']
    elif prefix == 'security-receiver-config':
        config = json.loads(expected['data']['config.json'])
        config['namespaces'].remove('mop')
        assert config == json.loads(after[new]['data']['config.json'])
        expected['data']['config.json'] = after[new]['data']['config.json']
    else:
        raise AssertionError(('Unexpected ConfigMap rotation', old))
    assert expected == after[new], ('Unexpected ConfigMap change', old)
    rotation_rows.append({'owner': old[0], 'beforeIdentity': old[1], 'afterIdentity': new[1]})

# Every WotLK document, Zabbix/Cilium/Kyverno owner and unrelated app is unchanged.
assert all((key[0] not in {'apps-wotlk', 'zabbix', 'cilium', 'kyverno-policies'}
            or key == ('kyverno-policies', ('policies.kyverno.io', 'ValidatingPolicy', '', 'kubescape-runtime-boundary')))
           and not (key[0].startswith('apps-') and key[0] != 'apps-mop') for key in modified), modified
assert all(identity[2] not in {'mop', 'openbrew'} and identity[3] != 'apps-mop' for _, identity in after)
assert len(modified) == 9 and len(unchanged) == 535
result = {
    'status': 'verified', 'baseline': '51d5da36550347fadfc230da6d06263a0be48c0e',
    'baselineComposition': bmeta, 'candidateComposition': ameta,
    'logicalRemovals': [{'owner': owner, 'identity': identity} for owner, identity in sorted(logical)],
    'generatedConfigMapRotations': rotation_rows,
    'modifiedResources': [{'owner': owner, 'identity': identity} for owner, identity in modified],
    'unchangedResourceCount': len(unchanged),
    'unchangedResourceHashes': [{'owner': owner, 'identity': identity,
        'sha256': hashlib.sha256(json.dumps(after[(owner, identity)], sort_keys=True,
                                           separators=(',', ':')).encode()).hexdigest()}
        for owner, identity in unchanged],
    'unexpectedChanges': [],
}
serialized = json.dumps(result, indent=2)
start = serialized.index('  \"unchangedResourceHashes\": [')
end = serialized.index('\n  ],', start) + len('\n  ]')
compact = '  \"unchangedResourceHashes\": [\n' + ',\n'.join(
    '    ' + json.dumps(row, separators=(',', ':')) for row in result['unchangedResourceHashes']) + '\n  ]'
output.write_text(serialized[:start] + compact + serialized[end:] + '\n')
print(f'verified: {len(logical)} removals, {len(rotations)} ConfigMap rotations, '
      f'{len(modified)} changed consumers/contracts, {len(unchanged)} unchanged owner/documents')
