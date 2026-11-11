import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx2 as httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.providers.azure import AzureProvider
from fastmcp import settings
from mcp.server.auth.provider import OAuthClientInformationFull
from key_value.aio.stores.memory import MemoryStore

from diagnostics import Diagnostics
from server import CHATGPT_CALLBACK, PUBLIC_BASE, DiagnosticControls, RestrictedAzureProvider, StrictAzureJWTVerifier, make_app


@pytest.fixture
def auth():
    return RestrictedAzureProvider(
        client_id='00000000-0000-0000-0000-000000000001', client_secret='local-test-only',
        tenant_id='00000000-0000-0000-0000-000000000002', allowed_user_ids={'operator'},
        required_scopes=['diagnostics.read'], base_url=PUBLIC_BASE,
        allowed_client_redirect_uris=[CHATGPT_CALLBACK], client_storage=MemoryStore(),
        jwt_signing_key='local-test-signing-material-not-for-deployment', enable_cimd=False)


def access_token(**changes):
    claims = {'tid': '00000000-0000-0000-0000-000000000002', 'oid': 'operator', 'roles': ['Diagnostics.Reader']}
    claims.update(changes)
    return AccessToken(token='test-access', client_id='test-client', scopes=['diagnostics.read'],
                       expires_at=int(time.time()) + 600, claims=claims)


@pytest.fixture
def app(auth):
    return make_app(auth, Diagnostics(allowed_namespaces={'*'}, client=AsyncMock()))


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['initialize', 'tools/list', 'tools/call'])
async def test_unauthenticated_operations_fail_before_protocol(app, method):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://git.cooked.beer') as client:
        response = await client.post('/kubernetes/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': {}})
        assert response.status_code == 401
        assert '/.well-known/oauth-protected-resource/kubernetes/mcp' in response.headers['www-authenticate']


@pytest.mark.asyncio
async def test_discovery_has_distinct_resource_issuer_pkce_and_stable_callback(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://git.cooked.beer') as client:
        resource = (await client.get('/.well-known/oauth-protected-resource/kubernetes/mcp')).json()
        assert resource['resource'] == PUBLIC_BASE + '/mcp'
        assert resource['authorization_servers'] == [PUBLIC_BASE]
        metadata = (await client.get('/.well-known/oauth-authorization-server/kubernetes')).json()
        assert metadata['issuer'] == PUBLIC_BASE
        assert metadata['authorization_endpoint'] == PUBLIC_BASE + '/authorize'
        assert metadata['token_endpoint'] == PUBLIC_BASE + '/token'
        assert metadata['authorization_response_iss_parameter_supported'] is True
        assert 'S256' in metadata['code_challenge_methods_supported']
        assert metadata['registration_endpoint'] == PUBLIC_BASE + '/register'


@pytest.mark.asyncio
async def test_dcr_rejects_unregistered_callbacks(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://git.cooked.beer') as client:
        valid = {'redirect_uris': [CHATGPT_CALLBACK], 'grant_types': ['authorization_code', 'refresh_token'],
                 'response_types': ['code'], 'token_endpoint_auth_method': 'none', 'scope': 'diagnostics.read'}
        response = await client.post('/kubernetes/register', json=valid)
        assert response.status_code == 201, response.text
        for callback in ['https://evil.example/callback', CHATGPT_CALLBACK + '/extra',
                         'http://chatgpt.com/connector_platform_oauth_redirect']:
            denied = await client.post('/kubernetes/register', json={**valid, 'redirect_uris': [callback]})
            assert denied.status_code in (400, 403), denied.text


@pytest.mark.asyncio
async def test_oauth_requires_pkce_and_binds_consent(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://git.cooked.beer') as client:
        registered = await client.post('/kubernetes/register', json={
            'redirect_uris': [CHATGPT_CALLBACK], 'grant_types': ['authorization_code', 'refresh_token'],
            'response_types': ['code'], 'token_endpoint_auth_method': 'none', 'scope': 'diagnostics.read'})
        params = {'client_id': registered.json()['client_id'], 'redirect_uri': CHATGPT_CALLBACK,
                  'response_type': 'code', 'scope': 'diagnostics.read', 'resource': PUBLIC_BASE + '/mcp', 'state': 'local-test-state'}
        denied = await client.get('/kubernetes/authorize', params=params)
        assert denied.status_code in (302, 303, 400)
        assert 'error' in (denied.headers.get('location', '') + denied.text)
        response = await client.get('/kubernetes/authorize', params={**params,
            'code_challenge': 'a' * 43, 'code_challenge_method': 'S256'})
        assert response.status_code in (302, 303), response.text
        consent = await client.get(response.headers['location'])
        assert consent.status_code == 200
        assert 'consent' in consent.text.lower()
        assert client.cookies, 'Consent must be bound to the initiating browser'


@pytest.mark.asyncio
async def test_invalid_token_never_reaches_initialize(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://git.cooked.beer') as client:
        response = await client.post('/kubernetes/mcp', headers={'Authorization': 'Bearer invalid'},
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}})
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_missing_scope_returns_forbidden(app):
    token = access_token().model_copy(update={'scopes': ['unrelated']})
    with patch.object(AzureProvider, 'load_access_token', AsyncMock(return_value=token)):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://git.cooked.beer') as client:
            response = await client.post('/kubernetes/mcp', headers={'Authorization': 'Bearer local-test'},
                json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}})
            assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize('claims', [{'tid': 'another-tenant'}, {'oid': 'another-user'}, {'roles': []}])
async def test_valid_signature_does_not_bypass_identity_authorization(auth, claims):
    with patch.object(AzureProvider, 'load_access_token', AsyncMock(return_value=access_token(**claims))):
        assert await auth.load_access_token('valid-but-not-authorized') is None


@pytest.mark.asyncio
async def test_authorized_identity_is_accepted(auth):
    with patch.object(AzureProvider, 'load_access_token', AsyncMock(return_value=access_token())):
        assert (await auth.load_access_token('valid')).claims['oid'] == 'operator'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [{'exp': int(time.time()) - 300}, {'aud': 'gitea'}, {'iss': 'https://evil.example'},
                                      {'nbf': int(time.time()) + 300}, {'scp': 'other-scope'}])
async def test_jwt_verifier_fails_closed(change):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    verifier = StrictAzureJWTVerifier(public_key=public, issuer='https://login.example/tenant/v2.0', audience='dedicated-diagnostics',
                           algorithm='RS256', required_scopes=['diagnostics.read'])
    claims = {'iss': 'https://login.example/tenant/v2.0', 'aud': 'dedicated-diagnostics', 'sub': 'operator',
               'exp': int(time.time()) + 600, 'iat': int(time.time()), 'scp': 'diagnostics.read'}
    assert await verifier.verify_token(jwt.encode(claims, private, algorithm='RS256'))
    assert await verifier.verify_token(jwt.encode({**claims, **change}, private, algorithm='RS256')) is None


@pytest.mark.asyncio
async def test_authenticated_initialize_tools_and_call(app):
    with patch.object(AzureProvider, 'load_access_token', AsyncMock(return_value=access_token())):
        async with app.app.router.lifespan_context(app.app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://git.cooked.beer',
                    headers={'Authorization': 'Bearer local-test', 'Accept': 'application/json, text/event-stream'}) as client:
                result = await client.post('/kubernetes/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                    'params': {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}})
                assert result.status_code == 200, result.text
                assert 'serverInfo' in result.json()['result']
                listed = await client.post('/kubernetes/mcp', json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}})
                tools = listed.json()['result']['tools']
                assert {t['name'] for t in tools} == {'list_resources', 'inspect_resource', 'get_pod_logs', 'get_events', 'diagnostic_capabilities'}
                assert all(t['annotations']['readOnlyHint'] for t in tools)
                for name, arguments in [('diagnostic_capabilities', {}), ('list_resources', {'resource': 'secrets'})]:
                    response = await client.post('/kubernetes/mcp', json={'jsonrpc': '2.0', 'id': 3,
                        'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})
                    result = response.json()['result']
                    assert bool(result.get('isError')) == (name == 'list_resources'), result


@pytest.mark.asyncio
async def test_http_body_and_global_rate_limits(app):
    app.per_minute = 2
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://git.cooked.beer') as client:
        assert (await client.post('/kubernetes/register', content=b'x' * 40000)).status_code == 413
        await client.get('/.well-known/oauth-authorization-server/kubernetes')
        response = await client.get('/.well-known/oauth-authorization-server/kubernetes')
        assert response.status_code == 429
        assert response.headers['retry-after'] == '60'
        assert (await client.get('/healthz')).status_code == 200


@pytest.mark.asyncio
async def test_registration_has_independent_rate_limit(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://git.cooked.beer') as client:
        metadata = {'redirect_uris': [CHATGPT_CALLBACK], 'token_endpoint_auth_method': 'none'}
        for _ in range(10):
            assert (await client.post('/kubernetes/register', json=metadata)).status_code == 201
        assert (await client.post('/kubernetes/register', json=metadata)).status_code == 429
        assert (await client.get('/.well-known/oauth-authorization-server/kubernetes')).status_code == 200


@pytest.mark.asyncio
async def test_encrypted_json_state_survives_provider_replacement(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'home', tmp_path)
    config = dict(client_id='00000000-0000-0000-0000-000000000001', client_secret='local-test-only',
        tenant_id='00000000-0000-0000-0000-000000000002', allowed_user_ids={'operator'},
        required_scopes=['diagnostics.read'], base_url=PUBLIC_BASE,
        allowed_client_redirect_uris=[CHATGPT_CALLBACK],
        jwt_signing_key='local-test-signing-material-not-for-deployment', enable_cimd=False)
    first = RestrictedAzureProvider(**config)
    await first.register_client(OAuthClientInformationFull(client_id='persistent-client',
        redirect_uris=[CHATGPT_CALLBACK], client_name='private-client-state-marker',
        token_endpoint_auth_method='none'))
    replacement = RestrictedAzureProvider(**config)
    client = await replacement.get_client('persistent-client')
    assert client and client.client_name == 'private-client-state-marker'
    files = list(tmp_path.rglob('*.json'))
    assert files
    assert all(b'private-client-state-marker' not in f.read_bytes() for f in files)


@pytest.mark.asyncio
async def test_tool_rate_limit_and_audit_never_include_body(caplog):
    controls = DiagnosticControls(limit=1)
    context = SimpleNamespace(message=SimpleNamespace(name='get_pod_logs', arguments={
        'namespace': 'media', 'pod': 'broken', 'container': 'app', 'body': 'do-not-log-this'}))
    with patch('server.get_access_token', return_value=access_token()), caplog.at_level('INFO', logger='diagnostics.audit'):
        assert await controls.on_call_tool(context, AsyncMock(return_value={'logs': 'do-not-log-this'}))
        with pytest.raises(Exception, match='call limit'):
            await controls.on_call_tool(context, AsyncMock())
    assert 'do-not-log-this' not in caplog.text
    assert 'rate_limited' in caplog.text
    assert 'operator' in caplog.text


@pytest.mark.asyncio
async def test_concurrency_ceiling():
    controls = DiagnosticControls(concurrency=2)
    running = peak = 0
    async def action(context):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return True
    context = SimpleNamespace(message=SimpleNamespace(name='inspect_resource', arguments={}))
    with patch('server.get_access_token', return_value=access_token()):
        await asyncio.gather(*(controls.on_call_tool(context, action) for _ in range(8)))
    assert peak == 2
