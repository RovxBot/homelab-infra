#!/usr/bin/env python3
"""Provision the dedicated Entra identity; encrypt credentials directly to SOPS.

Only the signed-in operator is assigned Diagnostics.Reader. Existing applications
must match our single-tenant identity and exact upstream callback before reuse.
"""
import argparse
import datetime as dt
import json
import pathlib
import secrets
import subprocess
import uuid

import yaml

BASE = 'https://git.cooked.beer/kubernetes'
TARGET = pathlib.Path('secrets/kubernetes-mcp-oauth.enc.yaml')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sops', default='sops')
    parser.add_argument('--az-python', help='Python interpreter of an existing Azure CLI installation')
    parser.add_argument('--rotate', action='store_true', help='Append a client credential and rotate the MCP signing key')
    args = parser.parse_args()
    prefix = [args.az_python, '-m', 'azure.cli'] if args.az_python else ['az']

    def az(*values):
        raw = subprocess.check_output([*prefix, *values, '-o', 'json'])
        return json.loads(raw) if raw.strip() else None

    def graph(method, path, body=None):
        values = ['rest', '--method', method, '--url', 'https://graph.microsoft.com/v1.0/' + path]
        if body is not None:
            values += ['--body', json.dumps(body)]
        return az(*values)

    account = az('account', 'show')
    me = az('ad', 'signed-in-user', 'show')
    apps = az('ad', 'app', 'list', '--filter', "displayName eq 'Cooked Kubernetes MCP'")
    if len(apps) > 1:
        raise SystemExit('Ambiguous Entra application; refusing to select one')
    if apps:
        app = apps[0]
    else:
        app = graph('POST', 'applications', {
            'displayName': 'Cooked Kubernetes MCP', 'signInAudience': 'AzureADMyOrg',
            'web': {'redirectUris': [BASE + '/auth/callback'],
                    'implicitGrantSettings': {'enableIdTokenIssuance': False, 'enableAccessTokenIssuance': False}},
            'api': {'requestedAccessTokenVersion': 2, 'oauth2PermissionScopes': [{
                'id': str(uuid.uuid4()), 'value': 'diagnostics.read', 'type': 'Admin', 'isEnabled': True,
                'adminConsentDisplayName': 'Read cooked-k8s diagnostic evidence',
                'adminConsentDescription': 'Read bounded Kubernetes workload state, events and logs; no changes or Secret access.'}]},
            'appRoles': [{'id': str(uuid.uuid4()), 'value': 'Diagnostics.Reader',
                          'displayName': 'Kubernetes diagnostics reader', 'isEnabled': True,
                          'allowedMemberTypes': ['User'],
                          'description': 'Read cooked-k8s diagnostic evidence through MCP'}]})
        scope = app['api']['oauth2PermissionScopes'][0]
        graph('PATCH', 'applications/' + app['id'], {
            'identifierUris': ['api://' + app['appId']],
            'requiredResourceAccess': [{'resourceAppId': app['appId'],
                                        'resourceAccess': [{'id': scope['id'], 'type': 'Scope'}]}]})
        app = az('ad', 'app', 'show', '--id', app['id'])
    if (app['signInAudience'] != 'AzureADMyOrg' or
            app.get('web', {}).get('redirectUris') != [BASE + '/auth/callback'] or
            app.get('api', {}).get('requestedAccessTokenVersion') != 2):
        raise SystemExit('Existing application has unexpected trust/callback/token configuration')
    if not any(s['value'] == 'diagnostics.read' and s['isEnabled'] for s in app['api']['oauth2PermissionScopes']):
        raise SystemExit('Dedicated diagnostics scope is missing')
    principals = az('ad', 'sp', 'list', '--filter', "appId eq '" + app['appId'] + "'")
    principal = principals[0] if principals else graph('POST', 'servicePrincipals', {
        'appId': app['appId'], 'appRoleAssignmentRequired': True})
    graph('PATCH', 'servicePrincipals/' + principal['id'], {'appRoleAssignmentRequired': True})
    role = next(r for r in app['appRoles'] if r['value'] == 'Diagnostics.Reader' and r['isEnabled'])
    assignments = graph('GET', 'servicePrincipals/' + principal['id'] + '/appRoleAssignedTo')['value']
    if not any(a['principalId'] == me['id'] and a['appRoleId'] == role['id'] for a in assignments):
        graph('POST', 'servicePrincipals/' + principal['id'] + '/appRoleAssignedTo', {
            'principalId': me['id'], 'resourceId': principal['id'], 'appRoleId': role['id']})
    grants = graph('GET', 'servicePrincipals/' + principal['id'] + '/oauth2PermissionGrants')['value']
    if not any(g.get('principalId') == me['id'] and g['resourceId'] == principal['id']
               and 'diagnostics.read' in g['scope'].split() for g in grants):
        graph('POST', 'oauth2PermissionGrants', {
            'clientId': principal['id'], 'resourceId': principal['id'], 'consentType': 'Principal',
            'principalId': me['id'], 'scope': 'diagnostics.read'})
    if not TARGET.exists() or args.rotate:
        credential = graph('POST', 'applications/' + app['id'] + '/addPassword', {
            'passwordCredential': {'displayName': 'kubernetes-mcp-sops',
                                   'endDateTime': (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=365)).isoformat()}})
        document = {'apiVersion': 'v1', 'kind': 'Secret',
                    'metadata': {'name': 'kubernetes-mcp-oauth', 'namespace': 'diagnostics'}, 'type': 'Opaque',
                    'stringData': {'client-secret': credential['secretText'], 'signing-key': secrets.token_urlsafe(64),
                                   'config.json': json.dumps({'client_id': app['appId'], 'tenant_id': account['tenantId'],
                                                              'allowed_user_ids': [me['id']]})}}
        ciphertext = subprocess.check_output([
            args.sops, '--encrypt', '--input-type', 'yaml', '--output-type', 'yaml',
            '--filename-override', str(TARGET), '/dev/stdin'], input=yaml.safe_dump(document).encode())
        TARGET.write_bytes(ciphertext)
        print('Credential encrypted; expires', credential['endDateTime'], '; key ID', credential['keyId'])
    print(json.dumps({'application_id': app['appId'], 'callback': BASE + '/auth/callback',
                      'secret_file': str(TARGET), 'assigned_operator': me['userPrincipalName']}))


if __name__ == '__main__':
    main()
