"""Bounded Kubernetes reads. No caller-controlled URLs, verbs, or raw objects."""
from __future__ import annotations

import json
import re
import ssl
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, Field

TOKEN = Path('/var/run/secrets/kubernetes.io/serviceaccount/token')
CA = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
MAX_RESULT = 96 * 1024
MAX_BACKEND = 4 * 1024 * 1024
NAME = r'[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?'
NAMESPACE = r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?'


class ReadError(ValueError):
    """A deliberately public, credential-free diagnostic failure."""


class Resource(StrEnum):
    namespaces = 'namespaces'
    nodes = 'nodes'
    pods = 'pods'
    deployments = 'deployments'
    statefulsets = 'statefulsets'
    daemonsets = 'daemonsets'
    replicasets = 'replicasets'
    services = 'services'
    endpointslices = 'endpointslices'
    pvcs = 'pvcs'
    pvs = 'pvs'
    jobs = 'jobs'
    cronjobs = 'cronjobs'
    kustomizations = 'kustomizations'
    helmreleases = 'helmreleases'
    gitrepositories = 'gitrepositories'
    helmrepositories = 'helmrepositories'
    ocirepositories = 'ocirepositories'
    helmcharts = 'helmcharts'
    longhorn_volumes = 'longhorn_volumes'
    longhorn_nodes = 'longhorn_nodes'
    node_metrics = 'node_metrics'
    pod_metrics = 'pod_metrics'
    events = 'events'


# prefix, plural, namespaced. This table is the complete REST allowlist.
ROUTES = {
    Resource.namespaces: ('/api/v1', 'namespaces', False),
    Resource.nodes: ('/api/v1', 'nodes', False),
    Resource.pods: ('/api/v1', 'pods', True),
    Resource.services: ('/api/v1', 'services', True),
    Resource.pvcs: ('/api/v1', 'persistentvolumeclaims', True),
    Resource.pvs: ('/api/v1', 'persistentvolumes', False),
    Resource.events: ('/api/v1', 'events', True),
    **{r: ('/apis/apps/v1', r.value, True) for r in (
        Resource.deployments, Resource.statefulsets, Resource.daemonsets, Resource.replicasets)},
    **{r: ('/apis/batch/v1', r.value, True) for r in (Resource.jobs, Resource.cronjobs)},
    Resource.endpointslices: ('/apis/discovery.k8s.io/v1', 'endpointslices', True),
    Resource.kustomizations: ('/apis/kustomize.toolkit.fluxcd.io/v1', 'kustomizations', True),
    Resource.helmreleases: ('/apis/helm.toolkit.fluxcd.io/v2', 'helmreleases', True),
    **{r: ('/apis/source.toolkit.fluxcd.io/v1', r.value, True) for r in (
        Resource.gitrepositories, Resource.helmrepositories, Resource.ocirepositories, Resource.helmcharts)},
    Resource.longhorn_volumes: ('/apis/longhorn.io/v1beta2', 'volumes', True),
    Resource.longhorn_nodes: ('/apis/longhorn.io/v1beta2', 'nodes', True),
    Resource.node_metrics: ('/apis/metrics.k8s.io/v1beta1', 'nodes', False),
    Resource.pod_metrics: ('/apis/metrics.k8s.io/v1beta1', 'pods', True),
}


def redact(text: str) -> str:
    """Defense in depth; arbitrary sensitive prose cannot be reliably detected."""
    patterns = [
        (r'(?is)-----BEGIN [^-]*(?:PRIVATE KEY|CERTIFICATE)-----.*?-----END [^-]+-----', '[REDACTED PEM]'),
        (r'(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+', '[REDACTED AUTH]'),
        (r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[REDACTED JWT]'),
        (r'(?i)(\b(?:password|passwd|pwd|token|secret|client_secret|api[_-]?key|access[_-]?key|authorization|cookie|connectionstring)\b["\x27]?\s*[:=]\s*)(?:"[^"\n]*"|\x27[^\x27\n]*\x27|[^\s,;}]+)', r'\1[REDACTED]'),
        (r'(?i)(https?://)[^/@\s]+:[^/@\s]+@', r'\1[REDACTED]@'),
        (r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|AKIA[A-Z0-9]{16})\b', '[REDACTED TOKEN]'),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


def clean(value):
    if isinstance(value, str):
        return redact(value)[:2048]
    if isinstance(value, list):
        return [clean(v) for v in value[:100]]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    return value


class Query(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    namespace: str | None = Field(default=None, max_length=63, pattern='^' + NAMESPACE + '$')
    name: str | None = Field(default=None, max_length=253, pattern='^' + NAME + '$')
    labels: str = Field(default='', max_length=256, pattern=r'^[a-zA-Z0-9_./=,!() -]*$')
    limit: int = Field(default=60, ge=1, le=200)
    continue_token: str = Field(default='', max_length=8192, pattern=r'^[A-Za-z0-9_+/=-]*$')


class LogQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    namespace: str = Field(pattern='^' + NAMESPACE + '$', max_length=63)
    pod: str = Field(pattern='^' + NAME + '$', max_length=253)
    container: str = Field(pattern='^' + NAMESPACE + '$', max_length=63)
    previous: bool = False
    since_seconds: int = Field(default=3600, ge=1, le=86400)
    tail: int = Field(default=200, ge=1, le=1000)
    byte_limit: int = Field(default=32768, ge=256, le=65536)
    timestamps: bool = True


def pick(data: dict, *keys) -> dict:
    return {k: data[k] for k in keys if k in data}


def conditions(data) -> list:
    return [pick(c, 'type', 'status', 'reason', 'message', 'lastTransitionTime', 'observedGeneration')
            for c in (data or [])[:30] if isinstance(c, dict)]


def container_status(c: dict) -> dict:
    out = pick(c, 'name', 'ready', 'started', 'restartCount', 'image')
    for key in ('state', 'lastState'):
        out[key] = {state: pick(detail, 'reason', 'message', 'exitCode', 'signal', 'startedAt', 'finishedAt')
                    for state, detail in c.get(key, {}).items() if isinstance(detail, dict)}
    return out


def containers(spec: dict) -> list:
    out = []
    for category in ('initContainers', 'containers', 'ephemeralContainers'):
        for c in spec.get(category, [])[:30]:
            # Never return env, args, commands, lifecycle commands, or HTTP headers.
            probes = {}
            for key in ('startupProbe', 'readinessProbe', 'livenessProbe'):
                if key in c:
                    probe = c[key]
                    probes[key] = {**pick(probe, 'initialDelaySeconds', 'periodSeconds', 'timeoutSeconds', 'failureThreshold'),
                                   'handler': next((p for p in ('exec', 'httpGet', 'tcpSocket', 'grpc') if p in probe), 'unknown')}
            out.append({**pick(c, 'name', 'image', 'resources'), 'category': category, 'probes': probes})
    return out


def summarize(obj: dict, resource: Resource) -> dict:
    meta, spec, status = (obj.get(k, {}) for k in ('metadata', 'spec', 'status'))
    out = {'kind': obj.get('kind', resource.value),
           **pick(meta, 'name', 'namespace', 'creationTimestamp', 'generation'),
           'conditions': conditions(status.get('conditions'))}
    if resource == Resource.pods:
        out.update(pick(status, 'phase', 'reason', 'message', 'qosClass', 'startTime'))
        out.update(pick(spec, 'nodeName', 'priorityClassName', 'restartPolicy', 'schedulerName'))
        out['containers'] = containers(spec)
        out['containerStatus'] = [container_status(c) for key in (
            'initContainerStatuses', 'containerStatuses', 'ephemeralContainerStatuses') for c in status.get(key, [])[:30]]
        out['owners'] = [pick(o, 'kind', 'name') for o in meta.get('ownerReferences', [])[:10]]
        out['volumes'] = [{'name': v.get('name'), 'claimName': v['persistentVolumeClaim'].get('claimName')}
                          for v in spec.get('volumes', []) if 'persistentVolumeClaim' in v][:30]
    elif resource == Resource.nodes:
        out.update(pick(status, 'capacity', 'allocatable'))
        out['nodeInfo'] = pick(status.get('nodeInfo', {}), 'kubeletVersion', 'osImage', 'containerRuntimeVersion', 'architecture')
        out.update(pick(spec, 'unschedulable', 'taints'))
    elif resource in (Resource.deployments, Resource.statefulsets, Resource.daemonsets,
                      Resource.replicasets, Resource.jobs, Resource.cronjobs):
        out['desired'] = pick(spec, 'replicas', 'suspend', 'schedule', 'completions', 'parallelism')
        out['status'] = pick(status, 'observedGeneration', 'replicas', 'updatedReplicas', 'readyReplicas',
                             'availableReplicas', 'unavailableReplicas', 'currentReplicas', 'desiredNumberScheduled',
                             'currentNumberScheduled', 'numberReady', 'numberUnavailable', 'updatedNumberScheduled',
                             'active', 'succeeded', 'failed', 'lastScheduleTime', 'completionTime')
        template = spec.get('jobTemplate', {}).get('spec', {}).get('template', {}) if resource == Resource.cronjobs else spec.get('template', {})
        out['containers'] = containers(template.get('spec', {}))
    elif resource == Resource.services:
        out['spec'] = pick(spec, 'type', 'ports', 'selector', 'clusterIP', 'externalTrafficPolicy')
    elif resource == Resource.endpointslices:
        out.update(pick(obj, 'addressType', 'ports'))
        out['service'] = meta.get('labels', {}).get('kubernetes.io/service-name')
        out['endpoints'] = [{**pick(e, 'addresses', 'conditions', 'nodeName'),
                             'target': pick(e.get('targetRef', {}), 'kind', 'name', 'namespace')}
                            for e in obj.get('endpoints', [])[:100]]
    elif resource in (Resource.pvcs, Resource.pvs):
        out['spec'] = pick(spec, 'storageClassName', 'accessModes', 'volumeMode', 'volumeName', 'capacity', 'persistentVolumeReclaimPolicy', 'resources')
        out['claimRef'] = pick(spec.get('claimRef', {}), 'namespace', 'name')
        out['status'] = pick(status, 'phase', 'capacity', 'accessModes', 'reason', 'message')
    elif resource in (Resource.kustomizations, Resource.helmreleases, Resource.gitrepositories,
                      Resource.helmrepositories, Resource.ocirepositories, Resource.helmcharts):
        out['spec'] = pick(spec, 'suspend', 'interval', 'timeout', 'path', 'dependsOn')
        out['sourceRef'] = pick(spec.get('sourceRef', {}), 'kind', 'name', 'namespace')
        out['status'] = pick(status, 'observedGeneration', 'lastAppliedRevision', 'lastAttemptedRevision', 'lastHandledReconcileAt', 'failures', 'installFailures', 'upgradeFailures')
        out['artifact'] = pick(status.get('artifact', {}), 'revision', 'lastUpdateTime', 'digest')
        out['generationPending'] = status.get('observedGeneration') != meta.get('generation')
    elif resource == Resource.longhorn_volumes:
        out['spec'] = pick(spec, 'size', 'numberOfReplicas', 'nodeID', 'engineImage', 'accessMode', 'dataLocality', 'disableFrontend')
        out['status'] = pick(status, 'state', 'robustness', 'currentNodeID', 'pendingNodeID', 'actualSize', 'lastDegradedAt', 'lastBackupAt', 'restoreRequired', 'expansionRequired')
        out['kubernetesStatus'] = pick(status.get('kubernetesStatus', {}), 'namespace', 'pvcName', 'pvName', 'pvStatus')
    elif resource == Resource.longhorn_nodes:
        out['allowScheduling'] = spec.get('allowScheduling')
        out['disks'] = {name: {**pick(d, 'storageAvailable', 'storageMaximum', 'storageScheduled'),
                               'conditions': conditions(d.get('conditions'))}
                        for name, d in list(status.get('diskStatus', {}).items())[:30]}
    elif resource in (Resource.node_metrics, Resource.pod_metrics):
        out.update(pick(obj, 'timestamp', 'window', 'usage'))
        out['containers'] = [pick(c, 'name', 'usage') for c in obj.get('containers', [])[:30]]
    elif resource == Resource.events:
        out.update(pick(obj, 'type', 'reason', 'message', 'count', 'firstTimestamp', 'lastTimestamp', 'eventTime'))
        out['source'] = pick(obj.get('source', {}), 'component', 'host')
        out['involvedObject'] = pick(obj.get('involvedObject', {}), 'kind', 'name', 'namespace')
    else:
        out.update(pick(status, 'phase'))
    return clean(out)


class Diagnostics:
    def __init__(self, *, allowed_namespaces: set[str], client=None):
        if not allowed_namespaces:
            raise ValueError('Namespace authorization must be explicit')
        self.allowed_namespaces = allowed_namespaces
        self.client = client or httpx.AsyncClient(
            base_url='https://kubernetes.default.svc', verify=ssl.create_default_context(cafile=CA),
            timeout=12, follow_redirects=False, trust_env=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4))

    def authorize_namespace(self, namespace, namespaced=True):
        if namespaced and '*' not in self.allowed_namespaces and namespace not in self.allowed_namespaces:
            raise PermissionError('A permitted namespace is required; cross-namespace reads are disabled')

    def path(self, resource: Resource, query: Query):
        prefix, plural, scoped = ROUTES[resource]
        self.authorize_namespace(query.namespace, scoped)
        if query.namespace and not scoped:
            raise ReadError('This resource is cluster-scoped')
        path = prefix
        if scoped and query.namespace:
            path += '/namespaces/' + query.namespace
        path += '/' + plural
        if query.name:
            if scoped and not query.namespace:
                raise ReadError('Named reads require a namespace')
            path += '/' + query.name
        return path

    async def get(self, path: str, params: dict, cap=MAX_BACKEND, text=False):
        try:
            return await self._get(path, params, cap=cap, text=text)
        except (httpx.TimeoutException, httpx.NetworkError):
            return {'available': False, 'message': 'Kubernetes API timed out or could not be reached; retry a focused query'}

    async def _get(self, path: str, params: dict, cap=MAX_BACKEND, text=False):
        # Only private methods build paths. Callers cannot supply paths through MCP.
        headers = {'Authorization': 'Bearer ' + TOKEN.read_text().strip()}
        async with self.client.stream('GET', path, params=params, headers=headers) as response:
            if response.status_code != 200:
                # Never echo the upstream body (may contain URLs or credentials).
                if response.status_code in (403, 404, 400, 503):
                    return {'available': False, 'http_status': response.status_code,
                            'message': 'Kubernetes read unavailable; check resource, RBAC, or component readiness'}
                raise RuntimeError('Kubernetes read failed: HTTP ' + str(response.status_code))
            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > cap:
                    raise ReadError('Backend response exceeds cap; narrow namespace/labels or page size')
                data.extend(chunk)
            return data.decode('utf-8', errors='replace') if text else json.loads(data)

    async def resources(self, resource: Resource, query: Query, extra_params=None):
        params = {'limit': query.limit, 'labelSelector': query.labels}
        if query.continue_token:
            params['continue'] = query.continue_token
        params.update(extra_params or {})
        raw = await self.get(self.path(resource, query), params)
        if raw.get('available') is False:
            return raw
        objects = raw.get('items', [raw] if query.name else [])
        if len(objects) > 200:
            raise ReadError('Backend ignored page limit; use a narrower query')
        if resource == Resource.namespaces and '*' not in self.allowed_namespaces:
            objects = [o for o in objects if o.get('metadata', {}).get('name') in self.allowed_namespaces]
        items, size = [], 0
        for obj in objects:
            item = summarize(obj, resource)
            encoded_size = len(json.dumps(item, ensure_ascii=False).encode())
            if size + encoded_size > MAX_RESULT - 12288:
                # Fail rather than discard objects behind a continuation token.
                raise ReadError('Output cap reached; reduce limit or narrow selectors')
            items.append(item)
            size += encoded_size
        return {'source': 'kubernetes/' + resource.value, 'observed_at': datetime.now(timezone.utc).isoformat(),
                'items': items, 'continue_token': raw.get('metadata', {}).get('continue', '')[:8192]}

    async def logs(self, query: LogQuery):
        self.authorize_namespace(query.namespace)
        path = '/api/v1/namespaces/' + query.namespace + '/pods/' + query.pod + '/log'
        params = {'container': query.container, 'previous': str(query.previous).lower(),
                  'sinceSeconds': query.since_seconds, 'tailLines': query.tail,
                  'limitBytes': query.byte_limit, 'timestamps': str(query.timestamps).lower()}
        body = await self.get(path, params, cap=query.byte_limit + 4, text=True)
        if isinstance(body, dict):
            return body
        safe = redact(body).encode()[:query.byte_limit].decode('utf-8', errors='ignore')
        return {'source': 'kubernetes/pods/log', 'namespace': query.namespace, 'pod': query.pod,
                'container': query.container, 'previous': query.previous,
                'observed_at': datetime.now(timezone.utc).isoformat(), 'logs': safe,
                'possibly_truncated': len(body.encode()) >= query.byte_limit,
                'retention': 'Live pod logs and at most one previous container instance; no historical log archive'}

    async def events(self, query: Query, *, warning_only=False, involved_kind=None,
                     involved_name=None, since_seconds=3600):
        # Validate object filters using the same DNS constraints as named reads.
        if involved_name:
            Query(name=involved_name)
        if involved_kind and not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,62}', involved_kind):
            raise ReadError('Invalid involved kind')
        selectors = []
        for key, value in [('type', 'Warning' if warning_only else None),
                           ('involvedObject.kind', involved_kind), ('involvedObject.name', involved_name)]:
            if value:
                selectors.append(key + '=' + value)
        result = await self.resources(Resource.events, query, {'fieldSelector': ','.join(selectors)})
        if result.get('available') is False:
            return result
        cutoff = datetime.now(timezone.utc).timestamp() - since_seconds
        def stamp(event):
            value = event.get('lastTimestamp') or event.get('eventTime') or event.get('creationTimestamp')
            try:
                return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
            except (ValueError, TypeError, AttributeError):
                return 0
        result['items'] = sorted((e for e in result['items'] if stamp(e) >= cutoff), key=stamp, reverse=True)
        result['ordering'] = 'Newest first within this API page; follow continue_token for additional pages'
        return result
