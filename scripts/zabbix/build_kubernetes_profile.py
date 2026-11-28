#!/usr/bin/env python3
"""Build a native, manually imported cluster-state profile; never calls Zabbix.

Input is the byte-exact AGPL-3.0 Zabbix 8.0.0 export from SOURCE. Download it
separately, then pass --source. Output is a native configuration.import file.
No credentials, API access, collector or runtime reconciliation is involved.
"""
import argparse
import hashlib
import json
from pathlib import Path
import uuid

import yaml

SOURCE = ('https://raw.githubusercontent.com/zabbix/zabbix/'
          'c81e276e505924eaa115d0aa4bb81c388233f924/'
          'templates/app/kubernetes_cluster_http/template_kubernetes_cluster.yaml')
SHA256 = '381387062c667528a5b87865dc4bd1d1f96a668ebf19accc0648b80496d8d629'
NAMES = {'Kubernetes Cluster by HTTP': 'Homelab Kubernetes Cluster state by HTTP',
         'Kubernetes Node by HTTP': 'Homelab Kubernetes Node inventory by HTTP'}


class NativeDumper(yaml.SafeDumper):
    pass


def native_string(dumper, value):
    return dumper.represent_scalar('tag:yaml.org,2002:str', value,
                                   style='|' if '\n' in value else None)


NativeDumper.add_representer(str, native_string)


def serialize(data):
    # Zabbix's YAML reader rejects some PyYAML folded single-quoted JS strings.
    # Keep single-line scalars together and multiline vendor scripts literal.
    return yaml.dump(data, Dumper=NativeDumper, sort_keys=False,
                     allow_unicode=True, width=1_000_000)


def collection_trigger(item, name, http_status=False):
    """One native availability problem per source, including missing samples.

    These items save EVERY one-minute poll (no discard/heartbeat). History must
    be nonzero: nodata() cannot monitor a master with history disabled. A new or
    re-enabled item/server gets Zabbix's normal nodata startup grace. Recovery
    needs three recent samples, not old healthy values preceding an outage.
    """
    path = '/Kubernetes Cluster by HTTP/' + item['key']
    expression = f'nodata({path},5m)=1'
    recovery = f'count({path},3m)>=3 and nodata({path},90s)=0'
    if http_status:
        expression += f' or last({path})<>200'
        recovery = (f'count({path},3m,"eq",200)>=3 and '
                    f'count({path},3m,"ne",200)=0 and nodata({path},90s)=0')
    return {
        'uuid': uuid.uuid5(uuid.NAMESPACE_URL, 'homelab-collection:' + item['key']).hex,
        'name': name, 'expression': expression,
        'recovery_mode': 'RECOVERY_EXPRESSION', 'recovery_expression': recovery,
        'priority': 'AVERAGE' if http_status else 'WARNING',
        'description': ('One-minute polls; missing data for five minutes indicates collection failure. '
                        'Recovery requires three samples in three minutes, latest within 90 seconds. '
                        + ('Every HTTP status is collected; only 200 is healthy. TLS/connection errors '
                           'stop updates and are detected by nodata().' if http_status else
                           'HTTP non-200, TLS/connection errors or missing node inventory stop updates. '
                           'Last saved inventory is not current while this problem is open.')),
        'tags': [{'tag': 'scope', 'value': 'availability'}],
    }


def build(raw):
    if hashlib.sha256(raw).hexdigest() != SHA256:
        raise ValueError('Input is not the qualified Zabbix 8.0.0 export')
    data = yaml.safe_load(raw)
    templates = data['zabbix_export']['templates']
    assert [t['template'] for t in templates] == list(NAMES)
    changes = []
    for template in templates:
        node = template['template'] == 'Kubernetes Node by HTTP'
        items = template['items']
        for discovery in template.get('discovery_rules', []):
            items = items + discovery.get('item_prototypes', [])
        disabled = set()
        for item in items:
            key = item['key']
            if (node or key in {'kube.resource.metrics', 'kube.state.metrics.check',
                               'kube.secret.count', 'kube.configmap.count', 'kube.endpoint.count'}
                    or item.get('master_item', {}).get('key') == 'kube.resource.metrics'
                    or item.get('type') == 'CALCULATED'):
                disabled.add(key)
        # Follow the vendor dependency graph, rather than leave enabled orphan items.
        while True:
            more = {i['key'] for i in items if i.get('master_item', {}).get('key') in disabled}
            if more <= disabled:
                break
            disabled |= more
        for item in items:
            key = item['key']
            if key in disabled:
                item['status'] = 'DISABLED'
                changes.append({'template': template['template'], 'key': key, 'status': 'disabled'})
                for trigger in item.get('triggers', []) + item.get('trigger_prototypes', []):
                    trigger['status'] = 'DISABLED'
                if key == 'kube.state.metrics.check':
                    item['description'] = ('Obsolete script JSON error check; disabled with its trigger. '
                                           'Native HTTP collection failures are detected on kube.state.metrics.')
            elif key == 'kube.pod.daemonset_count':
                # KSM 2.18 emits no *_labels series with our empty allowlist.
                # Creation time has one series per persisted DaemonSet, with
                # intrinsic namespace/daemonset labels; count series, not time.
                assert item['preprocessing'][0]['parameters'] == ['kube_daemonset_labels', 'function', 'count']
                item['preprocessing'][0]['parameters'][0] = 'kube_daemonset_created'
                changes.append({'key': key, 'metric': 'kube_daemonset_created; count objects without label collection'})
            elif key == 'kube.state.metrics':
                # Native HTTP agent, not a modified collection script. Never send
                # the Kubernetes bearer token to an exporter or discover Pod IPs.
                for field in ('params', 'parameters'):
                    item.pop(field, None)
                item.update(type='HTTP_AGENT', url='{$KUBE.STATE.METRICS.URL}',
                            verify_peer='YES', verify_host='YES', status_codes='200',
                            follow_redirects='NO', delay='1m', history='1h',
                            # Validate the expected inventory without transforming the
                            # Prometheus body consumed by native dependent items.
                            preprocessing=[{'type': 'MATCHES_REGEX', 'parameters': [
                                r'(?m)^kube_node_info\{[^\r\n]+\}[ \t]+1(?:\.0+)?[ \t]*$']}],
                            description=('TLS-verified kube-state-metrics. No Authorization header. '
                                         'Save every poll for freshness detection; require node inventory.'))
                item['triggers'] = [collection_trigger(
                    item, 'Kubernetes Cluster: State telemetry unavailable or stale')]
                changes.append({'key': key, 'transport': 'native HTTPS agent; no credentials'})
            elif key in {'kube.get.livez', 'kube.get.readyz'}:
                # Read status, not the response body (a 401/500 body is not
                # "ok"/"fail"). Empty status_codes accepts all HTTP responses;
                # redirects remain disabled and their 3xx status is unhealthy.
                item.update(verify_peer='YES', verify_host='YES', status_codes='',
                            follow_redirects='NO', delay='1m', history='1h',
                            retrieve_mode='HEADERS',
                            preprocessing=[{'type': 'REGEX', 'parameters': [
                                r'^HTTP/[0-9.]+[ \t]+([0-9]{3})\b', r'\1']}],
                            valuemap={'name': 'HTTP health status'})
                label = 'Livez' if key == 'kube.get.livez' else 'Readyz'
                item['triggers'] = [collection_trigger(
                    item, f'Kubernetes Cluster: {label} HTTP check failed or stale', http_status=True)]
                changes.append({'key': key, 'health': 'HTTP status + freshness; three-poll recovery'})
        for discovery in template.get('discovery_rules', []):
            if node:
                discovery['status'] = 'DISABLED'
                for trigger in discovery.get('trigger_prototypes', []):
                    trigger['status'] = 'DISABLED'
        if not node:
            template['valuemaps'].append({
                'uuid': uuid.uuid5(uuid.NAMESPACE_URL, 'homelab-http-health-status').hex,
                'name': 'HTTP health status',
                'mappings': [{'value': str(code), 'newvalue': label} for code, label in (
                    (200, 'ok'), (401, 'unauthenticated'), (403, 'forbidden'),
                    (500, 'unhealthy'), (503, 'unavailable'))],
            })
            template['macros'].append({'macro': '{$KUBE.STATE.METRICS.URL}',
                                      'value': 'https://zabbix-kube-state-metrics.zabbix.svc:8080/metrics'})
            next(m for m in template['macros'] if m['macro'] == '{$KUBE.NODE.NUMBER.THRESHOLD}')['value'] = '8'
            discovery = next(d for d in template['discovery_rules'] if d['key'] == 'kube.node.discovery')
            discovery['host_prototypes'][0]['host'] = 'cooked-k8s Node {#UUID}'
            discovery['host_prototypes'][0]['name'] = 'cooked-k8s Node {#NAME}'
        template['description'] = ('Local cluster-state profile derived from exact Zabbix 8.0.0. '
                                   'Usage/exporter checks disabled. See qualification for #2343.')
        template.pop('readme', None)
        template.pop('vendor', None)
        # Avoid vendor dashboards suggesting that disabled usage/Secret checks work.
        template.pop('dashboards', None)

    def remap(value):
        if isinstance(value, dict):
            # Native import requires UUIDv4. Keep deterministic identities for
            # repeat imports, with the required version/variant bits.
            return {k: (uuid.UUID(bytes=uuid.uuid5(
                            uuid.NAMESPACE_URL, 'homelab-zabbix-2343:' + v).bytes, version=4).hex
                        if k == 'uuid' else remap(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [remap(v) for v in value]
        if isinstance(value, str):
            for old, new in NAMES.items():
                value = value.replace(old, new)
        return value

    data['zabbix_export']['templates'] = remap(templates)
    for trigger in data['zabbix_export'].get('triggers', []):
        if '/Kubernetes Node by HTTP/' in trigger['expression']:
            trigger['status'] = 'DISABLED'
    data['zabbix_export']['triggers'] = remap(data['zabbix_export'].get('triggers', []))
    return data, changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help=SOURCE)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    data, changes = build(args.source.read_bytes())
    args.output.write_text(serialize(data))
    args.report.write_text(json.dumps({'source': SOURCE, 'sha256': SHA256, 'changes': changes}, indent=2) + '\n')
    print(f'Native profile written; {len(changes)} explicit item changes; no API calls')


if __name__ == '__main__':
    main()
