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
            if (node or key in {'kube.resource.metrics', 'kube.secret.count', 'kube.configmap.count', 'kube.endpoint.count'}
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
            elif key == 'kube.state.metrics':
                # Native HTTP agent, not a modified collection script. Never send
                # the Kubernetes bearer token to an exporter or discover Pod IPs.
                for field in ('params', 'parameters'):
                    item.pop(field, None)
                item.update(type='HTTP_AGENT', url='{$KUBE.STATE.METRICS.URL}',
                            verify_peer='YES', verify_host='YES', status_codes='200',
                            follow_redirects='NO',
                            description='TLS-verified kube-state-metrics. No Authorization header.')
                changes.append({'key': key, 'transport': 'native HTTPS agent; no credentials'})
            elif key in {'kube.get.livez', 'kube.get.readyz'}:
                item.update(verify_peer='YES', verify_host='YES', status_codes='200',
                            follow_redirects='NO')
        for discovery in template.get('discovery_rules', []):
            if node:
                discovery['status'] = 'DISABLED'
                for trigger in discovery.get('trigger_prototypes', []):
                    trigger['status'] = 'DISABLED'
        if not node:
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
            return {k: (uuid.uuid5(uuid.NAMESPACE_URL, 'homelab-zabbix-2343:' + v).hex
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
    args.output.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    args.report.write_text(json.dumps({'source': SOURCE, 'sha256': SHA256, 'changes': changes}, indent=2) + '\n')
    print(f'Native profile written; {len(changes)} explicit item changes; no API calls')


if __name__ == '__main__':
    main()
