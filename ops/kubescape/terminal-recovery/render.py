#!/usr/bin/env python3
"""Render the manual maintenance holder with exact reviewed source; no API calls."""
import hashlib
import json
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[3]
OPS = ROOT / 'ops/kubescape'


def render():
    data = {p.name: p.read_text() for p in (
        OPS / 'recover-terminal-posture.py', OPS / 'export-posture-6b2a9db3.py',
        ROOT / 'infra/kubescape/recurring/runner.py', ROOT / 'infra/kubescape/recurring/client-bundle.json')}
    name = 'kubescape-terminal-recovery-code-' + hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:12]
    code = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': name, 'namespace': 'kubescape'}, 'immutable': True, 'data': data}
    job = yaml.safe_load(Path(__file__).with_name('job.yaml').read_text())
    next(v for v in job['spec']['template']['spec']['volumes'] if v['name'] == 'code')['configMap']['name'] = name
    return [code, job]


if __name__ == '__main__':
    yaml.safe_dump_all(render(), sys.stdout, sort_keys=False)
