"""Compare actual dry-run admission objects with the legacy Longhorn mutation."""
import copy
import json
import time
from rehearse import EVIDENCE, apply, delete, load, k, ready, record, namespace


def cases():
    images = load('infra/kyverno/policies/longhorn-csi-registrar-lifecycle.yaml')[0]['spec']['rules'][0]['preconditions']['all'][1]['value']
    base = dict(apiVersion='apps/v1', kind='DaemonSet', metadata=dict(name='longhorn-csi-plugin', namespace='longhorn-system'), spec=dict(selector=dict(matchLabels=dict(app='registrar-test')), template=dict(metadata=dict(labels=dict(app='registrar-test')), spec=dict(nodeSelector={'rehearsal.invalid/never': 'true'}, containers=[dict(name='node-driver-registrar', image=images[0], lifecycle=dict(preStop=dict(exec=dict(command=['/bin/sh', '-c', 'rm -f /registration/socket'])), postStart=dict(exec=dict(command=['/keep-this-hook'])))), dict(name='sidecar', image='registry.k8s.io/pause:3.10', lifecycle=dict(preStop=dict(exec=dict(command=['/bin/sh', '-c', 'keep']))))]))))
    rows = []
    for image in images:
        obj = copy.deepcopy(base)
        obj['spec']['template']['spec']['containers'][0]['image'] = image
        rows.append((image, obj, True, False))
    def add(name, edit, expected=False, malformed=False):
        obj = copy.deepcopy(base)
        edit(obj)
        rows.append((name, obj, expected, malformed))
    add('other-namespace', lambda o: o['metadata'].update(namespace='cel-tests'))
    add('other-name', lambda o: o['metadata'].update(name='unrelated-daemonset'))
    add('other-container', lambda o: o['spec']['template']['spec']['containers'][0].update(name='other'))
    add('registrar-at-index-one', lambda o: o['spec']['template']['spec']['containers'].reverse())
    add('unsupported-image', lambda o: o['spec']['template']['spec']['containers'][0].update(image='longhornio/csi-node-driver-registrar:v2.19.0'))
    add('digest-image', lambda o: o['spec']['template']['spec']['containers'][0].update(image=images[0]+'@sha256:'+'a'*64))
    for field in ['lifecycle', 'preStop', 'exec', 'command']:
        def remove(o, field=field):
            c = o['spec']['template']['spec']['containers'][0]
            target = {'lifecycle': c, 'preStop': c['lifecycle'], 'exec': c['lifecycle']['preStop'], 'command': c['lifecycle']['preStop']['exec']}[field]
            target.pop(field)
        add('missing-'+field, remove, malformed=field in ['exec', 'command'])
    add('empty-command', lambda o: o['spec']['template']['spec']['containers'][0]['lifecycle']['preStop']['exec'].update(command=[]), malformed=True)
    add('non-shell', lambda o: o['spec']['template']['spec']['containers'][0]['lifecycle']['preStop']['exec'].update(command=['/registrar']))
    add('string-command', lambda o: o['spec']['template']['spec']['containers'][0]['lifecycle']['preStop']['exec'].update(command='bad'), malformed=True)
    add('empty-containers', lambda o: o['spec']['template']['spec'].update(containers=[]), malformed=True)
    add('null-lifecycle', lambda o: o['spec']['template']['spec']['containers'][0].update(lifecycle=None))
    return rows


def admission_object(doc, operation):
    for attempt in range(4):
        p = k('create' if operation == 'CREATE' else 'replace', '--dry-run=server', '-f', '-', '-o', 'json', data=json.dumps(doc), check=False)
        if operation != 'UPDATE' or '(Conflict)' not in p.stderr or not p.returncode or attempt == 3:
            break
        # DaemonSet status writes can race the probe. Refresh only the version;
        # keep the exact intended spec and never retry an admission denial.
        current = json.loads(k('get',doc['kind'],doc['metadata']['name'],'-n',doc['metadata']['namespace'],'-o','json').stdout)
        doc = copy.deepcopy(doc)
        doc['metadata']['resourceVersion'] = current['metadata']['resourceVersion']
        time.sleep(0.1)
    assert '(Conflict)' not in p.stderr, 'Optimistic concurrency failure is not a schema or policy denial: '+p.stderr
    assert not any(s in p.stderr.lower() for s in ['failed calling webhook', 'no such key', 'invalid expression', 'context deadline', 'no endpoints']), p.stderr
    return {'allowed': p.returncode == 0, 'object': json.loads(p.stdout) if p.returncode == 0 else None, 'stderr': p.stderr}


def run(version, engine):
    for ns in ['longhorn-system', 'cel-tests']:
        apply(namespace(ns))
    policy = load('infra/kyverno/policies/longhorn-csi-registrar-lifecycle.yaml' if engine == 'legacy' else 'migration/kyverno/policies/mutation.yaml')[0]
    apply(policy)
    ready(policy)
    rows = []
    try:
        for name, obj, expected, malformed in cases():
            # UPDATE uses a valid stored object with no hook; probes remain dry-run.
            seed = copy.deepcopy(obj)
            seed['spec']['template']['spec']['containers'] = [dict(name='node-driver-registrar', image='registry.k8s.io/pause:3.10')]
            apply(seed)
            live = json.loads(k('get', 'daemonset', obj['metadata']['name'], '-n', obj['metadata']['namespace'], '-o', 'json').stdout)
            for operation in ['CREATE', 'UPDATE']:
                probe = copy.deepcopy(obj)
                if operation == 'CREATE':
                    delete(seed)
                else:
                    apply(seed)
                    live = json.loads(k('get', 'daemonset', obj['metadata']['name'], '-n', obj['metadata']['namespace'], '-o', 'json').stdout)
                    probe['metadata']['resourceVersion'] = live['metadata']['resourceVersion']
                result = admission_object(probe, operation)
                assert result['allowed'] != malformed, (name, operation, result)
                template = result['object']['spec']['template']['spec'] if result['allowed'] else None
                if template:
                    containers = template['containers']
                    intended = copy.deepcopy(obj['spec']['template']['spec']['containers'])
                    if expected:
                        intended[0]['lifecycle'].pop('preStop')
                    # Compare authored fields recursively; apiserver defaults are allowed.
                    def subset(a, b):
                        if isinstance(a, dict):
                            return all((k not in b or b[k] is None or b[k] == {}) if v is None else k in b and subset(v, b[k]) for k, v in a.items())
                        if isinstance(a, list):
                            return len(a) == len(b) and all(subset(x,y) for x,y in zip(a,b))
                        return a == b
                    assert subset(intended, containers), (name, result)
                    assert (not containers[0].get('lifecycle', {}).get('preStop')) == expected or not expected, (name, result)
                rows.append(dict(case=name, operation=operation, allowed=result['allowed'], template=template, stderr=result['stderr']))
            delete(seed)
    finally:
        delete(policy)
    (EVIDENCE / f'mutation-{version}-{engine}.json').write_text(json.dumps(rows, indent=2)+'\n')
    # Compare the full server-defaulted PodSpec, including every lifecycle hook.
    baseline = EVIDENCE / 'mutation-1.19.2-legacy.json'
    if engine == 'cel':
        old = json.loads(baseline.read_text())
        assert [(r['case'],r['operation'],r['allowed'],r['template']) for r in rows] == [(r['case'],r['operation'],r['allowed'],r['template']) for r in old]
    record('longhorn-mutation', status='verified', version=version, engine=engine, cases=len(rows))
