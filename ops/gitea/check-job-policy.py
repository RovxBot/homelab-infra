#!/usr/bin/env python3
"""Exercise the actual Kyverno policy with allowed and dangerous job pods."""
import argparse
import copy
import pathlib
import subprocess
import tempfile

import yaml

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--kyverno')
parser.add_argument('--server', action='store_true', help='Verify actual Kubernetes admission without creating pods')
args = parser.parse_args()
if not args.server and not args.kyverno:
    parser.error('Specify --kyverno or --server')
root = pathlib.Path(__file__).resolve().parents[2]
config = yaml.safe_load((root / 'apps/gitea-runners/config.yaml').read_text())
pod = copy.deepcopy(config['kubernetes']['pod_template'])
pod.update(apiVersion='v1', kind='Pod')
pod['metadata'].update(name='policy-test', namespace='gitea-jobs')
pod['spec']['restartPolicy'] = 'Never'
pod['spec']['containers'][0].update(image=config['runner']['labels'][0].split('kubernetes://')[1], command=['sleep', '60'])
cases = [('restricted', pod, True)]
sanitizer = copy.deepcopy(pod)
sanitizer['metadata']['labels']['homelab.cooked.beer/sanitizer'] = 'true'
sanitizer['spec']['containers'][0]['securityContext']['capabilities']['add'] = ['SYS_PTRACE']
sanitizer['spec']['containers'][0]['securityContext'].update(runAsUser=0, runAsNonRoot=False)
cases.append(('sanitizer', sanitizer, True))
for name in ['privileged', 'root', 'host-pid', 'host-path', 'token', 'extra-capability',
             'unlabelled-ptrace', 'missing-drop-all', 'unconfined', 'sidecar', 'unapproved-image']:
    candidate = copy.deepcopy(sanitizer)
    spec = candidate['spec']
    context = spec['containers'][0]['securityContext']
    if name == 'privileged': context['privileged'] = True
    elif name == 'root':
        candidate = copy.deepcopy(pod)
        candidate['spec']['containers'][0]['securityContext']['runAsUser'] = 0
    elif name == 'host-pid': spec['hostPID'] = True
    elif name == 'host-path': spec['volumes'].append({'name': 'host', 'hostPath': {'path': '/'}})
    elif name == 'token': spec['automountServiceAccountToken'] = True
    elif name == 'extra-capability': context['capabilities']['add'] = ['SYS_ADMIN']
    elif name == 'unlabelled-ptrace': candidate['metadata']['labels'].pop('homelab.cooked.beer/sanitizer')
    elif name == 'missing-drop-all': context['capabilities'].pop('drop')
    elif name == 'unconfined': context['seccompProfile'] = {'type': 'Unconfined'}
    elif name == 'sidecar': spec['containers'].append(copy.deepcopy(spec['containers'][0]) | {'name': 'sidecar'})
    elif name == 'unapproved-image': spec['containers'][0]['image'] = 'ubuntu:24.04'
    cases.append((name, candidate, False))
with tempfile.TemporaryDirectory() as directory:
    path = pathlib.Path(directory) / 'pod.yaml'
    policy_path = root / 'apps/gitea-runners/job-security.yaml'
    for name, candidate, allowed in cases:
        path.write_text(yaml.safe_dump(candidate))
        command = ['kubectl', 'create', '--dry-run=server', '-f', str(path)] if args.server else [
            args.kyverno, 'apply', str(policy_path), '--resource', str(path),
            '--warn-no-pass', '--warn-exit-code', '2']
        result = subprocess.run(command, capture_output=True, text=True)
        if (result.returncode == 0) != allowed:
            raise SystemExit(name + ' returned an unexpected policy result:\n' + result.stdout + result.stderr)
        print(name + ': ' + ('allowed' if allowed else 'denied'))
