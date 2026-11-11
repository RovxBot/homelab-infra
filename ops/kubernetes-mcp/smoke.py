#!/usr/bin/env python3
"""Check the live endpoint; print pass/fail/counts, never tokens or raw evidence."""
import argparse
import json
import pathlib
import subprocess
import urllib.error
import urllib.request

BASE = 'https://git.cooked.beer'
MCP = BASE + '/kubernetes/mcp'


def request(url, data=None, token=None):
    headers = {'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json',
               'User-Agent': 'cooked-kubernetes-mcp-smoke/1.0'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data else None, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, {key.lower(): value for key, value in response.headers.items()}, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, {key.lower(): value for key, value in error.headers.items()}, {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token-file', type=pathlib.Path, help='Private file with a user OAuth access token; never pass token text')
    parser.add_argument('--rbac', action='store_true', help='Use current admin context to test service identity with impersonation')
    args = parser.parse_args()
    for method in ['initialize', 'tools/list', 'tools/call']:
        status, headers, _ = request(MCP, {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': {}})
        assert status == 401, (method, status)
        assert 'oauth-protected-resource/kubernetes/mcp' in headers.get('www-authenticate', '')
        print(method + ' without credentials: verified 401')
    for path in ['/.well-known/oauth-protected-resource/kubernetes/mcp', '/.well-known/oauth-authorization-server/kubernetes']:
        status, _, metadata = request(BASE + path)
        assert status == 200
        assert metadata.get('resource', metadata.get('issuer')) in [MCP, BASE + '/kubernetes']
    print('OAuth protected resource and authorization discovery: verified')
    if args.rbac:
        identity = '--as=system:serviceaccount:diagnostics:kubernetes-mcp'
        checks = [('get', 'pods', True), ('get', 'pods/log', True), ('get', 'secrets', False),
                  ('get', 'configmaps', False), ('create', 'pods/exec', False),
                  ('create', 'pods/attach', False), ('create', 'pods/portforward', False),
                  ('get', 'nodes/proxy', False), ('create', 'serviceaccounts/token', False),
                  ('patch', 'deployments.apps', False), ('delete', 'pods', False), ('watch', 'pods', False)]
        for verb, resource, expected in checks:
            response = subprocess.run(['kubectl', 'auth', 'can-i', verb, resource, '-n', 'diagnostics', identity],
                                      text=True, capture_output=True, timeout=20)
            assert response.stdout.strip() == ('yes' if expected else 'no'), (verb, resource)
            print('RBAC', verb, resource, ': verified', 'allowed' if expected else 'denied')
    if args.token_file:
        assert args.token_file.stat().st_mode & 0o077 == 0, 'OAuth token file must be private (0600)'
        token = args.token_file.read_text().strip()
        def rpc(method, params):
            status, _, body = request(MCP, {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}, token)
            assert status == 200 and 'error' not in body, (method, status)
            assert not body.get('result', {}).get('isError'), method
            return body['result']
        rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                           'clientInfo': {'name': 'private-smoke-test', 'version': '1'}})
        tools = rpc('tools/list', {})['tools']
        assert len(tools) == 5
        print('Authorized initialization and tool discovery: verified')
        for resource, namespace in [('pods', None), ('nodes', None), ('kustomizations', 'flux-system'),
                                     ('pod_metrics', 'diagnostics'), ('longhorn_volumes', 'longhorn-system')]:
            result = rpc('tools/call', {'name': 'list_resources', 'arguments': {'resource': resource, 'namespace': namespace, 'limit': 20}})
            evidence = result.get('structuredContent') or json.loads(result['content'][0]['text'])
            print('Read', resource, ':', 'unavailable' if evidence.get('available') is False else 'verified; items=' + str(len(evidence.get('items', []))))


if __name__ == '__main__':
    main()
