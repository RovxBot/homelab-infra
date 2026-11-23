#!/usr/bin/env python3
"""Prepare local, reviewable source pins and exact-name RBAC. Kubernetes GET only.

Run after an operator-approved rollout. This does not apply manifests, create a
PR, read logs, run the acceptance action or change the pinned DaemonSet UID.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from security_collector.model import Rejected, Workload, require, stamp
from security_collector.runtime import RuntimeLogReader
from security_collector.sources import APPS, CORE, items, owner, resource


class OperatorAPI:
    def __init__(self, context):
        self.context = context

    def get(self, path):
        try:
            result = subprocess.run(['kubectl', '--context', self.context, '--request-timeout=15s',
                'get', '--raw', path], check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
            require(len(result.stdout) <= 4 * 1024 * 1024, 'inventory_limit')
            return json.loads(result.stdout)
        except (subprocess.SubprocessError, ValueError, OSError):
            raise Rejected('operator_api_unavailable') from None


def reviewed_pins(config, api):
    result = deepcopy(config)
    agents = result['agents']
    require(0 < len(agents) <= 2 and len({a['node'] for a in agents}) == len(agents), 'runtime_scope_invalid')
    pods = items(api, resource(CORE, 'kubescape', 'pods'))
    proof = []
    for agent in agents:
        candidates = [p for p in pods if p['spec'].get('nodeName') == agent['node']
            and owner(p, 'DaemonSet', agent['daemonset_uid']) and not p['metadata'].get('deletionTimestamp')]
        require(len(candidates) == 1, 'agent_candidates_ambiguous_or_missing')
        agent.update(pod_name=candidates[0]['metadata']['name'], pod_uid=candidates[0]['metadata']['uid'])
        # Same production verifier; two independent reads include owner/SA/image checks.
        reader = RuntimeLogReader(api, None, [Workload(**result['workloads'][0])], **agent)
        before = reader.agent()
        identity = dict(reader.verified_identity)
        require(reader.agent() == before and reader.verified_identity == identity, 'agent_changed_during_read')
        proof.append(identity)
    return result, proof


def scoped_roles(config, roles):
    result = deepcopy(roles)
    role, = [d for d in result if d['kind'] == 'Role' and d['metadata']['namespace'] == 'kubescape'
              and d['metadata']['name'] == 'security-collector']
    rule, = [r for r in role['rules'] if 'pods/log' in r['resources']]
    require(rule['verbs'] == ['get'] and rule['resources'] == ['pods', 'pods/log'], 'log_role_invalid')
    rule['resourceNames'] = sorted(a['pod_name'] for a in config['agents'])
    require(len(set(rule['resourceNames'])) == len(config['agents']), 'agent_configuration_invalid')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', required=True, help='Explicit operator context for cooked-k8s')
    parser.add_argument('--proof', type=Path, required=True, help='Retain reviewed API identity observations')
    args = parser.parse_args()
    config_path = ROOT / 'infra/kubescape/collector/collector.json'
    role_path = config_path.with_name('rbac.yaml')
    config, proof = reviewed_pins(json.loads(config_path.read_text()), OperatorAPI(args.context))
    roles = scoped_roles(config, list(yaml.safe_load_all(role_path.read_text())))
    # No writes occur until all configured nodes pass. Output is still only a local diff.
    args.proof.write_text(json.dumps(dict(observed_at=stamp(datetime.now(timezone.utc)), sources=proof), indent=2) + '\n')
    config_path.write_text(json.dumps(config, indent=2) + '\n')
    role_path.write_text(yaml.safe_dump_all(roles, sort_keys=False))
    print('Prepared local source pins and exact-name RBAC; review and submit through GitOps.')


if __name__ == '__main__':
    try:
        main()
    except (Rejected, KeyError, TypeError, OSError):
        sys.exit('Enrollment refused: missing, ambiguous, changed or unverified source; no cluster writes attempted.')
