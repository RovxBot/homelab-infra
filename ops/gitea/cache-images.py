#!/usr/bin/env python3
"""Pre-pull recovery images on every Linux/amd64 node; no host mounts.

Run with the repository's Kubernetes context. Delete this temporary DaemonSet
after every pod is Ready. Node image garbage collection can still remove a
cache; retain independent image exports as well.
"""
import json
import pathlib
import re
import subprocess

root = pathlib.Path(__file__).resolve().parents[2]
images = []
for path in ['apps/gitea/database.yaml', 'apps/gitea/mirroring.yaml',
             'apps/gitea/backups.yaml', 'apps/gitea-runners/runner.yaml',
             'apps/gitea-runners/config.yaml']:
    for image in re.findall(r'(?:docker\.gitea\.com|ghcr\.io|postgres:)[A-Za-z0-9_./:@-]+',
                            (root / path).read_text()):
        if '@sha256:' in image and image not in images:
            images.append(image)
# Same pin as the chart's rootless image override.
images.append('docker.gitea.com/gitea:28.2.0-rootless@sha256:94807314454f6525994a3b76286ba12bc7e7f8cd14f61d9e67431767f8103306')
security = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
            'capabilities': {'drop': ['ALL']}}
containers = [{'name': 'image-' + str(index), 'image': image,
               'imagePullPolicy': 'IfNotPresent', 'command': ['/bin/sh', '-c', 'true'],
               'securityContext': security,
               'resources': {'requests': {'cpu': '10m', 'memory': '16Mi'},
                             'limits': {'cpu': '100m', 'memory': '64Mi'}}}
              for index, image in enumerate(images)]
containers[-1]['command'] = ['/bin/sh', '-c', 'sleep 86400']
labels = {'app': 'gitea-image-cache', 'homelab.cooked.beer/owner': 'sam'}
manifest = {'apiVersion': 'apps/v1', 'kind': 'DaemonSet',
    'metadata': {'name': 'gitea-image-cache', 'namespace': 'gitea-runners', 'labels': labels},
    'spec': {'selector': {'matchLabels': {'app': 'gitea-image-cache'}},
             'template': {'metadata': {'labels': labels}, 'spec': {
                 'automountServiceAccountToken': False, 'enableServiceLinks': False,
                 'nodeSelector': {'kubernetes.io/os': 'linux', 'kubernetes.io/arch': 'amd64'},
                 'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000,
                                     'seccompProfile': {'type': 'RuntimeDefault'}},
                 'initContainers': containers[:-1], 'containers': containers[-1:]}}}}
subprocess.run(['kubectl', 'apply', '-f', '-'], input=json.dumps(manifest).encode(), check=True)
print('Pre-pulling', len(images), 'pinned images. Inspect: kubectl -n gitea-runners get ds gitea-image-cache')
