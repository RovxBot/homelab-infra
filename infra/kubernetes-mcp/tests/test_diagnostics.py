import json
from datetime import datetime, timezone

import httpx2 as httpx
import pytest
import yaml
from pydantic import ValidationError

import diagnostics as module
from diagnostics import Diagnostics, LogQuery, Query, Resource, redact, summarize


@pytest.fixture
def api(tmp_path, monkeypatch):
    token = tmp_path / 'token'
    token.write_text('test-service-identity')
    monkeypatch.setattr(module, 'TOKEN', token)
    def make(handler, namespaces={'*'}):
        return Diagnostics(allowed_namespaces=namespaces,
                           client=httpx.AsyncClient(base_url='https://kubernetes.default.svc',
                                                    transport=httpx.MockTransport(handler)))
    return make


@pytest.mark.parametrize('values', [
    {'namespace': '../secrets'}, {'namespace': 'a/b'}, {'name': 'pods?watch=true'},
    {'limit': 201}, {'limit': True}, {'limit': 0}, {'labels': 'app=x\n'},
    {'labels': 'a' * 257}, {'continue_token': 'x&watch=1'}, {'url': 'https://evil.test'},
])
def test_untrusted_queries_are_rejected(values):
    with pytest.raises(ValidationError):
        Query(**values)


@pytest.mark.parametrize('resource', ['secrets', 'configmaps', 'pods/exec', '../proxy', 'https://evil.test'])
def test_arbitrary_resources_cannot_be_selected(resource):
    with pytest.raises(ValueError):
        Resource(resource)


def test_raw_pod_credentials_never_returned():
    pod = {'metadata': {'name': 'broken', 'namespace': 'test', 'annotations': {'password': 'hidden-value'}},
           'spec': {'containers': [{'name': 'app', 'image': 'example:v1',
                                    'env': [{'name': 'PASSWORD', 'value': 'hidden-value'}],
                                    'command': ['hidden-value'], 'args': ['hidden-value'],
                                    'readinessProbe': {'httpGet': {'path': '/hidden-value', 'httpHeaders': [{'value': 'hidden-value'}]}},
                                    'resources': {'requests': {'cpu': '10m', 'memory': '8Mi'}}}]},
           'status': {'phase': 'Pending', 'containerStatuses': [{'name': 'app', 'restartCount': 4,
                        'state': {'waiting': {'reason': 'CrashLoopBackOff', 'message': 'password=hidden-value'}}}]}}
    result = summarize(pod, Resource.pods)
    assert 'hidden-value' not in json.dumps(result)
    assert result['containerStatus'][0]['restartCount'] == 4
    assert result['containers'][0]['probes']['readinessProbe']['handler'] == 'httpGet'


def test_flux_failure_is_actionable_without_source_or_values():
    resource = {'metadata': {'name': 'broken-release', 'namespace': 'flux-system', 'generation': 3},
                'spec': {'values': {'password': 'never-return'}, 'url': 'https://token:never-return@source'},
                'status': {'observedGeneration': 2, 'conditions': [{'type': 'Ready', 'status': 'False',
                         'reason': 'BuildFailed', 'message': 'Missing deployment.yaml',
                         'lastTransitionTime': '2026-11-11T05:00:00Z'}], 'lastAttemptedRevision': 'main@sha1:example'}}
    result = summarize(resource, Resource.kustomizations)
    assert result['generationPending'] is True
    assert result['conditions'][0]['reason'] == 'BuildFailed'
    assert result['name'] == 'broken-release'
    assert 'never-return' not in json.dumps(result)


@pytest.mark.parametrize('value', [
    'password=sample-sensitive', 'token: sample-sensitive', '"client_secret": "sample-sensitive"',
    'Bearer sample-sensitive', 'https://sam:sample-sensitive@example.test',
    '-----BEGIN PRIVATE KEY-----\nsample-sensitive\n-----END PRIVATE KEY-----',
    'Cookie: sample-sensitive', 'ConnectionString=sample-sensitive',
])
def test_redaction(value):
    assert 'sample-sensitive' not in redact(value)


@pytest.mark.asyncio
async def test_namespaces_are_enforced_before_network(api):
    def fail(request):
        raise AssertionError('Unauthorized request reached API')
    client = api(fail, {'media'})
    for ns in [None, 'gitea']:
        with pytest.raises(PermissionError):
            await client.resources(Resource.pods, Query(namespace=ns))


@pytest.mark.asyncio
async def test_paginated_api_request_uses_only_service_identity(api):
    def handler(request):
        assert request.method == 'GET'
        assert request.url.path == '/api/v1/namespaces/media/pods'
        assert request.url.params['limit'] == '2'
        assert request.url.params['labelSelector'] == 'app in (sonarr,radarr)'
        assert request.headers['Authorization'] == 'Bearer test-service-identity'
        return httpx.Response(200, json={'items': [{'metadata': {'name': 'sonarr', 'namespace': 'media'},
                                                  'status': {'phase': 'Running'}}],
                                         'metadata': {'continue': 'next-page'}})
    result = await api(handler).resources(Resource.pods, Query(namespace='media', labels='app in (sonarr,radarr)', limit=2))
    assert result['items'][0]['phase'] == 'Running'
    assert result['continue_token'] == 'next-page'


@pytest.mark.asyncio
async def test_previous_logs_are_bounded_timestamped_and_redacted(api):
    def handler(request):
        assert request.url.path.endswith('/pods/broken/log')
        assert dict(request.url.params) == {'container': 'app', 'previous': 'true', 'sinceSeconds': '600',
                                             'tailLines': '20', 'limitBytes': '1024', 'timestamps': 'true'}
        return httpx.Response(200, text='2026-11-11T05:30:00Z failed password=sample-sensitive\n')
    result = await api(handler).logs(LogQuery(namespace='media', pod='broken', container='app',
                                    previous=True, since_seconds=600, tail=20, byte_limit=1024))
    assert result['previous'] is True
    assert 'sample-sensitive' not in result['logs']
    assert len(result['logs'].encode()) <= 1024


@pytest.mark.asyncio
async def test_oversized_backend_is_rejected(api):
    with pytest.raises(ValueError, match='exceeds cap'):
        await api(lambda r: httpx.Response(200, text='x' * 2000)).logs(
            LogQuery(namespace='media', pod='x', container='app', byte_limit=256))


@pytest.mark.asyncio
async def test_output_cap_never_silently_skips_page_members(api):
    pods = [{'metadata': {'name': 'pod-' + str(i)}, 'status': {'message': 'é' * 2048}}
            for i in range(100)]
    with pytest.raises(ValueError, match='Output cap'):
        await api(lambda r: httpx.Response(200, json={'items': pods})).resources(Resource.pods, Query())


@pytest.mark.asyncio
async def test_events_filter_sort_and_drop_old_evidence(api):
    now = datetime.now(timezone.utc).isoformat()
    def handler(request):
        assert request.url.params['fieldSelector'] == 'type=Warning,involvedObject.kind=Pod,involvedObject.name=broken'
        return httpx.Response(200, json={'items': [
            {'metadata': {'name': 'old'}, 'lastTimestamp': '2000-01-01T00:00:00Z'},
            {'metadata': {'name': 'new', 'namespace': 'media'}, 'lastTimestamp': now,
             'type': 'Warning', 'reason': 'BackOff', 'message': 'Back-off restarting failed container'}]})
    result = await api(handler).events(Query(namespace='media'), warning_only=True,
                                      involved_kind='Pod', involved_name='broken', since_seconds=600)
    assert [i['name'] for i in result['items']] == ['new']
    assert result['items'][0]['reason'] == 'BackOff'


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [400, 403, 404, 503])
async def test_api_outages_return_safe_unavailable_evidence(api, status):
    result = await api(lambda r: httpx.Response(status, text='token=do-not-echo')).resources(Resource.pod_metrics, Query())
    assert result['available'] is False
    assert result['http_status'] == status
    assert 'do-not-echo' not in json.dumps(result)


@pytest.mark.asyncio
async def test_token_rotation_read_on_every_call(api, tmp_path):
    seen = []
    client = api(lambda r: (seen.append(r.headers['Authorization']) or httpx.Response(200, json={'items': []})))
    await client.resources(Resource.pods, Query())
    module.TOKEN.write_text('rotated-service-identity')
    await client.resources(Resource.pods, Query())
    assert seen == ['Bearer test-service-identity', 'Bearer rotated-service-identity']


def test_rbac_has_no_secret_shell_watch_or_mutation():
    from pathlib import Path
    docs = list(yaml.safe_load_all((Path(__file__).resolve().parents[1] / 'rbac.yaml').read_text()))
    role = next(d for d in docs if d['kind'] == 'ClusterRole')
    for rule in role['rules']:
        assert set(rule['verbs']) <= {'get', 'list'}
        assert not set(rule['resources']) & {'*', 'secrets', 'configmaps', 'pods/exec', 'pods/attach', 'pods/portforward', 'nodes/proxy', 'serviceaccounts/token'}
    assert next(d for d in docs if d['kind'] == 'ServiceAccount')['automountServiceAccountToken'] is False
