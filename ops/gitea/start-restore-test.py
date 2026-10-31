#!/usr/bin/env python3
"""Create a disposable database/Git restore test on a new retained PVC.

Requires kubectl access to gitea. Does not read Secret values or mount production
PVCs. Test resources remain available for inspection; remove them explicitly
after saving the evidence. Retain storage needs a separate reviewed cleanup.
"""
import datetime as dt
import json
import pathlib
import subprocess

name = 'gitea-restore-test-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H%M%S')
labels = {'homelab.cooked.beer/owner': 'sam', 'app': 'gitea-restore-test'}
metadata = {'name': name, 'namespace': 'gitea', 'labels': labels}
directory = pathlib.Path(__file__).resolve().parent
backup_image = 'ghcr.io/rovxbot/gitea-backup@sha256:7386c7c4cf2040a6f8863d7f56ddccd13a7c268a350249d2023830b940569308'
toolchain_image = 'ghcr.io/rovxbot/gitea-linux@sha256:19eac8b2b2bb63bb2dd50578ab4267725e8935d42e9a9ccca5ff0214e2a1329b'
security = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
            'capabilities': {'drop': ['ALL']}}
mounts = [{'name': 'restore', 'mountPath': '/restore'},
          {'name': 'scripts', 'mountPath': '/scripts', 'readOnly': True},
          {'name': 'tmp', 'mountPath': '/tmp'}]
resources = {'requests': {'cpu': '100m', 'memory': '256Mi'},
             'limits': {'cpu': '1', 'memory': '2Gi'}}
objects = [
    {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata,
     'data': {p: (directory / p).read_text() for p in ['restore-test.sh', 'verify-restored-git.py']}},
    {'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim',
     'metadata': metadata | {'annotations': {'kustomize.toolkit.fluxcd.io/prune': 'disabled'}},
     'spec': {'accessModes': ['ReadWriteOnce'], 'storageClassName': 'longhorn-gitea-retain',
              'resources': {'requests': {'storage': '10Gi'}}}},
    {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': metadata,
     'spec': {'activeDeadlineSeconds': 1800, 'backoffLimit': 0,
              'template': {'metadata': {'labels': labels}, 'spec': {
                  'restartPolicy': 'Never', 'automountServiceAccountToken': False,
                  'enableServiceLinks': False,
                  'securityContext': {'runAsNonRoot': True, 'runAsUser': 999,
                                      'runAsGroup': 999, 'fsGroup': 1000,
                                      'seccompProfile': {'type': 'RuntimeDefault'}},
                  'initContainers': [{'name': 'restore', 'image': backup_image,
                                      'command': ['/bin/bash', '/scripts/restore-test.sh'],
                                      'envFrom': [{'secretRef': {'name': 'gitea-backup'}}],
                                      'securityContext': security, 'resources': resources,
                                      'volumeMounts': mounts}],
                  'containers': [{'name': 'verify', 'image': toolchain_image,
                                  'command': ['python3', '/scripts/verify-restored-git.py'],
                                  'env': [{'name': 'HOME', 'value': '/tmp'}],
                                  'securityContext': security, 'resources': resources,
                                  'volumeMounts': mounts}],
                  'volumes': [{'name': 'restore', 'persistentVolumeClaim': {'claimName': name}},
                              {'name': 'scripts', 'configMap': {'name': name}},
                              {'name': 'tmp', 'emptyDir': {'sizeLimit': '256Mi'}}]}}}}]
subprocess.run(['kubectl', 'create', '-f', '-'], input=json.dumps({
    'apiVersion': 'v1', 'kind': 'List', 'items': objects}).encode(), check=True)
print('Inspect: kubectl -n gitea logs job/' + name + ' -c restore')
print('Verify: kubectl -n gitea logs job/' + name + ' -c verify')
