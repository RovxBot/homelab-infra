"""Read-only Kubernetes source with verified TLS, rotating credentials and bounds."""
from dataclasses import dataclass
from pathlib import Path
import ssl
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import (Request, build_opener, HTTPSHandler, HTTPRedirectHandler,
                            ProxyHandler)

from .adapters import decode
from .model import Deployment, Rejected, digest, fingerprint, identity, require, stamp, timestamp

CORE = '/api/v1'
APPS = '/apis/apps/v1'
STORAGE = '/apis/spdx.softwarecomposition.kubescape.io/v1beta1'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Rejected('api_redirect_refused')


class KubernetesAPI:
    def __init__(self, origin, ca_file, token_file):
        parsed = urlsplit(origin)
        require(parsed.scheme == 'https' and parsed.hostname and not parsed.username
                and not parsed.password and parsed.path in ('', '/')
                and not parsed.query and not parsed.fragment, 'api_origin_invalid')
        self.origin, self.token_file = origin.rstrip('/'), Path(token_file)
        self.opener = build_opener(ProxyHandler({}), NoRedirect(),
                                  HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))

    def raw(self, path, *, limit=4 * 1024 * 1024):
        require(path.startswith(('/api/', '/apis/')) and not path.startswith('//'), 'api_path_invalid')
        try:
            # Reopen projected credentials each request, including post-read checks.
            with self.token_file.open('rb') as source:
                token = source.read(16385).decode('ascii').strip()
            require(0 < len(token) <= 16384 and '\n' not in token and '\r' not in token, 'api_token_invalid')
            req = Request(self.origin + path, headers={'Authorization': 'Bearer ' + token,
                          'Accept': 'application/json', 'Accept-Encoding': 'identity'}, method='GET')
            with self.opener.open(req, timeout=10) as response:
                require(response.status == 200 and response.headers.get('Content-Encoding', 'identity')
                        == 'identity', 'api_response_invalid')
                data = response.read(limit + 1)
            require(len(data) <= limit, 'api_response_limit')
            return data
        except (OSError, URLError, HTTPError, UnicodeError, ValueError) as exc:
            if isinstance(exc, Rejected):
                raise
            raise Rejected('api_unavailable') from None

    def get(self, path):
        return decode(self.raw(path), 4 * 1024 * 1024)


def resource(base, namespace, kind, name=None):
    identity(namespace)
    require(kind in ('pods', 'replicasets', 'deployments', 'daemonsets', 'vulnerabilitymanifests',
                     'vulnerabilitymanifestsummaries', 'sbomsyfts'), 'resource_unsupported')
    return f'{base}/namespaces/{namespace}/{kind}' + ('/' + identity(name) if name else '')


def items(api, path):
    result = api.get(path)
    require(not result.get('metadata', {}).get('continue'), 'inventory_incomplete')
    require(isinstance(result['items'], list) and len(result['items']) <= 200, 'inventory_limit')
    return result['items']


def owner(obj, kind, uid):
    refs = [r for r in obj['metadata'].get('ownerReferences', []) if r.get('controller') is True]
    return len(refs) == 1 and refs[0]['kind'] == kind and refs[0]['uid'] == uid


@dataclass(frozen=True)
class Inventory:
    deployment: Deployment
    pods: tuple
    running_ref: str
    signature: str


class InventoryReader:
    """Stage B qualifies ordinary Deployment containers only; other coverage is explicit."""
    def __init__(self, api):
        self.api = api

    def read(self, w, now):
        require(w.kind == 'Deployment', 'inventory_unsupported')
        try:
            obj = self.api.get(resource(APPS, w.namespace, 'deployments', w.name))
            meta, spec, status = obj['metadata'], obj['spec'], obj['status']
            require(meta['namespace'] == w.namespace and meta['name'] == w.name
                    and not meta.get('deletionTimestamp'), 'inventory_missing')
            generation, replicas = meta['generation'], spec.get('replicas', 1)
            require(type(replicas) is int and 0 < replicas <= 100
                    and status['observedGeneration'] == generation
                    and all(status.get(k, 0) == replicas for k in
                            ('replicas', 'readyReplicas', 'updatedReplicas', 'availableReplicas')),
                    'inventory_incomplete')
            selected = []
            for pod in items(self.api, resource(CORE, w.namespace, 'pods')):
                refs = [r for r in pod['metadata'].get('ownerReferences', []) if r.get('controller') is True]
                if len(refs) != 1 or refs[0]['kind'] != 'ReplicaSet':
                    continue
                rs = self.api.get(resource(APPS, w.namespace, 'replicasets', refs[0]['name']))
                require(rs['metadata']['uid'] == refs[0]['uid'], 'inventory_changed')
                if not owner(rs, 'Deployment', meta['uid']):
                    continue
                require(pod['metadata']['namespace'] == w.namespace
                        and not pod['metadata'].get('deletionTimestamp')
                        and pod['status']['phase'] == 'Running', 'inventory_incomplete')
                states = [s for s in pod['status']['containerStatuses'] if s['name'] == w.container]
                require(len(states) == 1 and states[0].get('ready') is True
                        and 'running' in states[0]['state'], 'inventory_incomplete')
                state = states[0]
                ref = state['imageID'].removeprefix('docker-pullable://')
                require('@' in ref, 'inventory_unsupported')
                digest(ref.rsplit('@', 1)[-1])
                selected.append(dict(name=pod['metadata']['name'], uid=pod['metadata']['uid'],
                    node=pod['spec']['nodeName'], container_id=state['containerID'],
                    started=state['state']['running']['startedAt'], ref=ref))
            require(len(selected) == replicas and len({p['ref'] for p in selected}) == 1,
                    'inventory_incomplete')
            selected.sort(key=lambda p: p['uid'])
            ref = selected[0]['ref']
            deployed = max([meta['creationTimestamp']] + [p['started'] for p in selected], key=timestamp)
            deployment = Deployment(w, ref.rsplit('@', 1)[-1], generation, stamp(now), deployed, meta['uid'])
            return Inventory(deployment, tuple(selected), ref,
                             fingerprint((meta['uid'], generation, spec, selected)))
        except (KeyError, TypeError, IndexError, AttributeError):
            raise Rejected('inventory_incomplete') from None


def log_path(namespace, pod, since=None):
    query = {'container': 'node-agent', 'timestamps': 'true', 'limitBytes': 4 * 1024 * 1024 + 1}
    if since:
        query['sinceTime'] = since
    return resource(CORE, namespace, 'pods', pod) + '/log?' + urlencode(query)
