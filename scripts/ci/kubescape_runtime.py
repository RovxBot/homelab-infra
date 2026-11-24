"""Static contract for the reviewed eight-node runtime cohort; no cluster writes."""
from copy import deepcopy
import json

from security_native_exporter_preflight import EXPORTER

IMAGE = 'quay.io/kubescape/node-agent:v0.3.310@sha256:99152dbefd07e524418f682882ad300cb74821aaa4e42e6477c42a9fa4686e60'
NODES = ["metal0","metal2","metal3","metal4","metal5","metal6","metal7","metal8"]
NAMESPACES = ['homepage', 'memos', 'kubescape-runtime-test']
RULES = ['R0001', 'R0004', 'R0007', 'R1000', 'R1002']
CAPS = ['SYS_ADMIN', 'SYS_PTRACE', 'NET_ADMIN', 'SYSLOG', 'SYS_RESOURCE', 'IPC_LOCK', 'NET_RAW']
BUDGET = {'requests': {'cpu': '100m', 'memory': '180Mi'}, 'limits': {'cpu': '500m', 'memory': '1400Mi'}}
SECURITY = {'runAsUser': 0, 'privileged': False, 'allowPrivilegeEscalation': False,
            'capabilities': {'add': CAPS, 'drop': ['ALL']}, 'seccompProfile': {'type': 'Unconfined'}}


def affinity(node=None):
    term = {'matchFields': [{'key': 'metadata.name', 'operator': 'In', 'values': [node]}]} if node else {
        'matchExpressions': [{'key': 'kubernetes.io/hostname', 'operator': 'In', 'values': NODES}]}
    return {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [term]}}}


def binding_spec():
    return {'namespaceSelector': {'matchExpressions': [{'key': 'kubernetes.io/metadata.name',
             'operator': 'In', 'values': NAMESPACES}]}, 'rules': [{'ruleID': x} for x in RULES]}


def check_values(values):
    assert values['global']['extraCaCertificates'] == {
        'enabled': True, 'secretName': 'security-receiver-ca'
    }, 'only the documented receiver CA Secret may be added to node-agent'
    assert values['global']['apiAuth']['enabled'] is True
    agent = values['nodeAgent']
    assert agent['image']['tag'] == IMAGE.split(':', 1)[1]
    assert agent['nodeSelector'] == {'kubernetes.io/os': 'linux'}
    assert agent['resources'] == BUDGET
    assert agent['env'] == [{'name': 'INCLUDENAMESPACES', 'value': ','.join(NAMESPACES)}]
    assert values['alertCRD'] == {'installDefault': True, 'scopeClustered': True, 'scopeNamespaced': False}
    assert agent['config']['stdoutExporter'] is True
    assert agent['config']['extra'] == {'hostMonitoringEnabled': False}
    assert not agent['config'].get('skipKernelVersionCheck', False)


def findings(resource):
    try:
        assert resource['kind'] == 'DaemonSet' and resource['metadata']['name'] == 'node-agent'
        assert resource['metadata']['namespace'] == 'kubescape'
        t = resource['spec']['template']; p = t['spec']; c, = p['containers']
        assert t['metadata']['labels']['homelab.cooked.beer/owner'] == 'sam'
        assert p['nodeSelector'] == {'kubernetes.io/os': 'linux'} and p['affinity'] == affinity()
        assert p['serviceAccountName'] == 'node-agent' and p['hostPID'] is True
        assert not p.get('hostNetwork') and not p.get('hostIPC')
        assert not p.get('initContainers') and not p.get('ephemeralContainers')
        assert c['image'] == IMAGE and c['resources'] == BUDGET and c['securityContext'] == SECURITY
        assert not c.get('command') and not c.get('args')
        assert all(c[k]['httpGet']['path'] == path for k, path in
                   [('livenessProbe', '/livez'), ('readinessProbe', '/readyz'), ('startupProbe', '/readyz')])
        assert next(e['value'] for e in c['env'] if e['name'] == 'INCLUDENAMESPACES') == ','.join(NAMESPACES)
        return []
    except (AssertionError, KeyError, ValueError):
        return ['DaemonSet/node-agent: runtime canary contract differs']


def post_render(expected):
    key = ('apps/v1', 'DaemonSet', 'kubescape', 'node-agent')
    if key not in expected:
        return  # Retained scanner-only post-render unit fixtures.
    role = expected[('rbac.authorization.k8s.io/v1', 'ClusterRole', None, 'node-agent')]
    assert role['rules'][0]['resources'] == ['nodes','nodes/proxy','services','endpoints','namespaces','configmaps']
    role['rules'][0]['resources'].remove('nodes/proxy')
    role['rules'].append({'apiGroups': [''], 'resources': ['nodes/proxy'], 'resourceNames': NODES, 'verbs': ['get']})
    agent = expected[key]
    agent['spec']['template']['spec']['containers'][0]['securityContext'] = deepcopy(SECURITY)
    agent['spec']['template']['spec']['affinity'] = affinity()
    expected[('kubescape.io/v1', 'RuntimeRuleAlertBinding', None, 'all-rules-all-pods')]['spec'] = binding_spec()


def check_config(resources):
    maps = {r['metadata']['name']: r['data'] for r in resources if r['kind'] == 'ConfigMap'}
    cfg = json.loads(maps['node-agent']['config.json'])
    assert cfg['runtimeDetectionEnabled'] is True and cfg['applicationProfileServiceEnabled'] is True
    assert cfg['includeNamespaces'] == 'memos,homepage,koito'  # Runtime env override remains independent.
    assert cfg['exporters'] == {
        'stdoutExporter': True, 'alertManagerExporterUrls': [],
        'syslogExporterURL': '', 'httpExporterConfig': EXPORTER
    }, 'native HTTP exporter differs from the documented scoped contract'
    for k in ('hostMonitoringEnabled', 'hostSensorEnabled', 'sandboxSeccompEnabled', 'seccompServiceEnabled',
              'networkServiceEnabled', 'malwareDetectionEnabled', 'sbomGenerationEnabled', 'networkStreamingEnabled'):
        assert cfg[k] is False, k
    binding, = [r for r in resources if r['kind'] == 'RuntimeRuleAlertBinding']
    assert binding['spec'] == binding_spec()
    role = next(r for r in resources if r['kind'] == 'ClusterRole' and r['metadata']['name'] == 'node-agent')
    assert [r for r in role['rules'] if 'nodes/proxy' in r.get('resources', [])] == [
        {'apiGroups': [''], 'resources': ['nodes/proxy'], 'resourceNames': NODES, 'verbs': ['get']}]
    assert not any('*' in r.get('resources', []) or 'secrets' in r.get('resources', []) or 'delete' in r.get('verbs', []) for r in role['rules'])
    library, = [r for r in resources if r['kind'] == 'Rules']
    rules = {r['id']: r for r in library['spec']['rules']}
    assert all(rules[k]['enabled'] and not rules[k]['supportPolicy'] for k in RULES)
    agents = [r for r in resources if r['kind'] == 'DaemonSet']
    assert len(agents) == 1 and not findings(agents[0])


def normalize_rules(resources):
    # Kustomize 5.5 adds blank lines to folded CEL scalars. CEL ignores these;
    # compare every nonempty line exactly, preserving string literals and spaces.
    for r in resources.values():
        if r['kind'] == 'Rules':
            def visit(value):
                if isinstance(value, dict):
                    return {k: visit(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [visit(v) for v in value]
                if isinstance(value, str):
                    return '\n'.join(line for line in value.split('\n') if line.strip())
                return value
            r['spec'] = visit(r['spec'])


def check_boundary(kyverno, work, resources, root):
    """Exercise actual CEL admission for the agent and attempts to widen access."""
    import subprocess
    import yaml
    agent = next(r for r in resources if r['kind'] == 'DaemonSet' and r['metadata']['name'] == 'node-agent')
    pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {
        'name': 'agent-positive', 'namespace': 'kubescape', 'ownerReferences': [
            {'apiVersion': 'apps/v1', 'kind': 'DaemonSet', 'name': 'node-agent',
             'uid': '00000000-0000-0000-0000-000000000001', 'controller': True}]},
        'spec': deepcopy(agent['spec']['template']['spec'])}
    pod['spec']['affinity'] = affinity('metal6')
    # Prove admission accepts *every* specific node individually. Keep
    # metal6 as the canonical pre-existing Pod test; add seven new fixtures.
    allowed_node_pods = []
    for node in NODES:
        if node == 'metal6':
            continue
        instance = deepcopy(pod)
        instance['metadata']['name'] = f'{node}-positive'
        instance['spec']['affinity'] = affinity(node)
        instance['spec']['nodeName'] = node
        allowed_node_pods.append(instance)
    # The chart's documented extraCaCertificates uses Secret
    # security-receiver-ca with one receiver-ca.crt key. Helm 'lookup' hides
    # this mount during offline chart rendering, so prove the exact shape
    # under Kyverno CEL with a synthetic positive and denial fixtures.
    trusted_ca = deepcopy(pod)
    trusted_ca['metadata']['name'] = 'agent-receiver-ca-positive'
    trusted_ca['spec']['volumes'].append({
        'name': 'extra-ca-certificates',
        'secret': {'secretName': 'security-receiver-ca'}})
    trusted_ca['spec']['containers'][0]['volumeMounts'].append({
        'name': 'extra-ca-certificates', 'mountPath': '/etc/ssl/certs/receiver-ca.crt',
        'subPath': 'receiver-ca.crt', 'readOnly': True})
    fixtures = [agent, pod, *allowed_node_pods, trusted_ca]
    bound_ds = deepcopy(agent)
    bound_ds['metadata']['name'] = 'node-agent'
    bound_ds['spec']['template']['spec']['nodeName'] = 'metal3'
    # Check separately to keep report resource identities unambiguous.
    changes = {
        'wrong-node': lambda p: p['nodeSelector'].update({'kubernetes.io/hostname': 'metal7'}),
        'all-nodes': lambda p: p.pop('affinity'),
        'wrong-affinity': lambda p: p.update(affinity=affinity('metal1')),
        'unbounded-affinity': lambda p: p.update(affinity={'nodeAffinity': {}}),
        'bound-node': lambda p: p.update(nodeName='metal1'),
        'wrong-account': lambda p: p.update(serviceAccountName='default'),
        'wrong-image': lambda p: p['containers'][0].update(image='busybox:latest'),
        'extra-capability': lambda p: p['containers'][0]['securityContext']['capabilities']['add'].append('SYS_MODULE'),
        'privileged': lambda p: p['containers'][0]['securityContext'].update(privileged=True),
        'command': lambda p: p['containers'][0].update(command=['sh']),
        'injected-env': lambda p: p['containers'][0]['env'].append({'name': 'LD_PRELOAD', 'value': '/host/tmp/lib.so'}),
        'writable-root': lambda p: next(m for m in p['containers'][0]['volumeMounts'] if m['name'] == 'host').update(readOnly=False),
        'extra-hostpath': lambda p: p['volumes'].append({'name': 'extra', 'hostPath': {'path': '/etc'}}),
        'sidecar': lambda p: p['containers'].append(deepcopy(p['containers'][0])),
        'init': lambda p: p.update(initContainers=[deepcopy(p['containers'][0])]),
        'ephemeral': lambda p: p.update(ephemeralContainers=[deepcopy(p['containers'][0])]),
        'host-network': lambda p: p.update(hostNetwork=True),
    }
    for name, change in changes.items():
        doc = deepcopy(pod); doc['metadata']['name'] = name; change(doc['spec']); fixtures.append(doc)
    ca_attacks = {
        'ca-wrong-secret': lambda p: next(v for v in p['volumes'] if v['name'] == 'extra-ca-certificates')['secret'].update(secretName='arbitrary'),
        'ca-writable': lambda p: next(m for m in p['containers'][0]['volumeMounts'] if m['name'] == 'extra-ca-certificates').update(readOnly=False),
        'ca-other-path': lambda p: next(m for m in p['containers'][0]['volumeMounts'] if m['name'] == 'extra-ca-certificates').update(mountPath='/etc/credentials/receiver-ca.crt'),
    }
    for name, change in ca_attacks.items():
        doc = deepcopy(trusted_ca); doc['metadata']['name'] = name
        change(doc['spec']); fixtures.append(doc)
    ordinary = next(r for r in resources if r['kind'] == 'Deployment' and r['metadata']['name'] == 'kubescape')
    safe = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'scanner-positive', 'namespace': 'kubescape'},
            'spec': deepcopy(ordinary['spec']['template']['spec'])}
    fixtures.append(safe)
    for name, change in {
        'scanner-root': lambda p: p['containers'][0]['securityContext'].update(runAsUser=0),
        'scanner-hostpath': lambda p: p['volumes'].append({'name': 'extra', 'hostPath': {'path': '/'}}),
        'scanner-hostpid': lambda p: p.update(hostPID=True),
        'scanner-capability': lambda p: p['containers'][0]['securityContext']['capabilities'].update(add=['SYS_ADMIN']),
        'scanner-seccomp': lambda p: p['securityContext'].update(seccompProfile={'type': 'Unconfined'}),
    }.items():
        doc = deepcopy(safe); doc['metadata']['name'] = name; change(doc['spec']); fixtures.append(doc)
    path = work / 'runtime-admission-cases.yaml'; path.write_text(yaml.safe_dump_all(fixtures))
    run = subprocess.run([kyverno, 'apply', str(root / 'infra/kubescape/runtime/admission.yaml'),
                          '--resource', str(path), '--policy-report'], text=True, capture_output=True)
    output = run.stdout
    assert 'apiVersion:' in output, run.stderr[:400]
    (work / 'runtime-admission-report.yaml').write_text(output)
    report = yaml.safe_load(output[output.index('apiVersion:'):])
    assert report['summary'] == {'pass': 4 + len(NODES) - 1, 'fail': len(changes) + len(ca_attacks) + 5, 'error': 0, 'skip': 0, 'warn': 0}, report['summary']
    for result in report['results']:
        name = result['resources'][0]['name']
        expected_passes = {'node-agent', 'agent-positive', 'agent-receiver-ca-positive',
                           'scanner-positive'} | {f'{node}-positive' for node in NODES if node != 'metal6'}
        assert result['result'] == ('pass' if name in expected_passes else 'fail'), name
    bound_path = work / 'runtime-bound-daemonset.yaml'
    bound_path.write_text(yaml.safe_dump(bound_ds))
    denied = subprocess.run([kyverno, 'apply', str(root / 'infra/kubescape/runtime/admission.yaml'),
                             '--resource', str(bound_path), '--policy-report'], text=True, capture_output=True)
    bound_report = yaml.safe_load(denied.stdout[denied.stdout.index('apiVersion:'):])
    assert bound_report['summary'] == {'pass': 0, 'fail': 1, 'error': 0, 'skip': 0, 'warn': 0}
    print('verified: runtime CEL boundary allows all eight exact nodes/scanner and denies access-widening cases')
