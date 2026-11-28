#!/usr/bin/env python3
"""Prepare an encrypted, offline owner bundle; never calls Entra, Cloudflare or Zabbix.

The native API plan is review material: resolve @ references with actual native
object IDs before making attended API calls, or enter the same values in the UI.
No runtime reconciler, client secret or custom authentication service is used.
"""
import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[2]
HOST = 'zabbix.cooked.beer'
URL = 'https://' + HOST
SHARED_TUNNEL = '19dd24d6-5e63-4ba3-a23c-fbebc7312e3f'
CLAIMS = {
    'username': 'http://schemas.microsoft.com/identity/claims/objectidentifier',
    'groups': 'http://schemas.microsoft.com/ws/2008/06/identity/claims/groups',
    'given_name': 'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname',
    'surname': 'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname',
}
# Exact 8.0.0 CRoleHelper enum. Creation defaults existing permissions to enabled
# even when default_access=0 (that flag also governs future elements). Explicitly
# enumerate denies as well as allows; verify the effective role after creation.
UI_USER = [
    'monitoring.dashboard', 'monitoring.problems', 'monitoring.hosts', 'monitoring.latest_data', 'monitoring.maps',
    'apm.traces', 'apm.metrics', 'apm.logs', 'services.services', 'services.sla_report',
    'inventory.overview', 'inventory.hosts', 'reports.availability_report', 'reports.top_triggers',
]
UI_ADMIN = [
    'monitoring.discovery', 'services.sla', 'reports.scheduled_reports', 'reports.system_info', 'reports.notifications',
    'configuration.template_groups', 'configuration.host_groups', 'configuration.templates', 'configuration.hosts',
    'configuration.maintenance', 'configuration.discovery', 'configuration.trigger_actions',
    'configuration.service_actions', 'configuration.discovery_actions', 'configuration.autoregistration_actions',
    'configuration.internal_actions',
]
ACTIONS_USER = [
    'edit_dashboards', 'edit_maps', 'acknowledge_problems', 'suppress_problems', 'close_problems', 'change_severity',
    'add_problem_comments', 'execute_scripts', 'manage_api_tokens', 'invoke_execute_now',
    'change_problem_ranking', 'edit_own_media',
]
ACTIONS_ADMIN = ['edit_maintenance', 'manage_scheduled_reports', 'manage_sla', 'select_server_for_monitoring']


def object_id(value):
    parsed = uuid.UUID(value)
    if parsed.int == 0 or str(parsed) != value:
        raise ValueError('Expected a canonical, nonzero object ID')
    return value


def roles():
    monitoring = ['monitoring.dashboard', 'monitoring.problems', 'monitoring.hosts',
                  'monitoring.latest_data', 'monitoring.maps']
    result = {}
    for kind, rank, user_type in [('admin', '10', 2), ('operator', '20', 1), ('viewer', '30', 1)]:
        ui = monitoring + (['configuration.hosts', 'configuration.maintenance'] if kind == 'admin' else [])
        actions = [] if kind == 'viewer' else ['acknowledge_problems', 'add_problem_comments']
        if kind == 'admin':
            actions += ['edit_maintenance', 'change_severity', 'suppress_problems', 'close_problems']
        result[kind] = {
            # Native equal-type overlap sorts by name ASC. Keep operator before
            # viewer, or a viewer mapping could suppress operator permissions.
            'name': f'Homelab {rank} {kind.title()}', 'type': user_type,
            'rules': {'ui.default_access': 0,
                      'ui': [{'name': n, 'status': int(n in ui)}
                             for n in UI_USER + (UI_ADMIN if kind == 'admin' else [])],
                      'modules.default_access': 0, 'api.access': 0,
                      'actions.default_access': 0,
                      'actions': [{'name': n, 'status': int(n in actions)}
                                  for n in ACTIONS_USER + (ACTIONS_ADMIN if kind == 'admin' else [])],
                      'services.read.mode': 0, 'services.write.mode': 0},
        }
    return result


def build(owner, certificate):
    tenant = object_id(owner['tenant_id'])
    ids = owner['group_object_ids']
    if set(ids) != {'admin', 'operator', 'viewer'}:
        raise ValueError('Exactly three explicit group mappings are required')
    if len({object_id(v) for v in ids.values()}) != 3:
        raise ValueError('Group object IDs must be distinct')
    cf = owner['cloudflare']
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', cf['team_name']):
        raise ValueError('Invalid Cloudflare team name')
    if not re.fullmatch(r'[a-f0-9]{64}', cf['audience']):
        raise ValueError('Invalid application audience tag')
    credentials = cf['tunnel_credentials']
    tunnel = object_id(credentials['TunnelID'])
    if tunnel == SHARED_TUNNEL:
        raise ValueError('A dedicated tunnel is required for the loopback origin')
    if not re.fullmatch(r'[a-f0-9]{32}', credentials['AccountTag']):
        raise ValueError('Invalid tunnel account tag')
    if len(base64.b64decode(credentials['TunnelSecret'], validate=True)) != 32:
        raise ValueError('Invalid tunnel credential')
    # Validate PEM structure/current validity without logging certificate input.
    check = subprocess.run(['openssl', 'x509', '-noout', '-checkend', '86400', '-startdate'],
                           input=certificate, capture_output=True, text=True, check=False, timeout=10)
    if check.returncode or 'PRIVATE KEY' in certificate or certificate.count('BEGIN CERTIFICATE') != 1:
        raise ValueError('Expected one currently valid IdP signing certificate (>24h remaining)')
    start = re.search(r'notBefore=(.*)', check.stdout)
    if not start or datetime.strptime(start[1], '%b %d %H:%M:%S %Y %Z').replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
        raise ValueError('IdP signing certificate is not yet valid')

    config = {
        'tunnel': tunnel, 'credentials-file': '/etc/cloudflared/credentials.json',
        'metrics': '0.0.0.0:2001',
        'ingress': [{'hostname': HOST, 'service': 'http://127.0.0.1:8081',
                     'originRequest': {'httpHostHeader': HOST, 'access': {
                         'required': True, 'teamName': cf['team_name'], 'audTag': [cf['audience']]}}},
                    {'service': 'http_status:404'}],
    }
    plan = [{'method': 'usergroup.update', 'params': {
        'usrgrpid': '@group.Guests', 'gui_access': 3, 'users_status': 1}}]
    plan += [{'method': 'role.create', 'ref': 'role.' + k, 'params': v} for k, v in roles().items()]
    for kind in ids:
        plan.append({'method': 'usergroup.create', 'ref': 'group.' + kind, 'params': {
            'name': 'Homelab SAML ' + kind.title(), 'gui_access': 0,
            'hostgroup_rights': [{'id': '@hostgroup.Applications', 'permission': 2 if kind == 'viewer' else 3}],
        }})
    plan.append({'method': 'usergroup.create', 'ref': 'group.disabled', 'params': {
        'name': 'Homelab SAML Deprovisioned', 'gui_access': 3, 'users_status': 1}})
    plan.append({'method': 'userdirectory.create', 'ref': 'directory.saml', 'params': {
        'name': 'Homelab Entra SAML', 'idp_type': 2, 'provision_status': 1,
        'idp_entityid': f'https://sts.windows.net/{tenant}/',
        'sso_url': f'https://login.microsoftonline.com/{tenant}/saml2', 'slo_url': '',
        'sp_entityid': URL, 'username_attribute': CLAIMS['username'],
        'nameid_format': 'urn:oasis:names:tc:SAML:2.0:nameid-format:persistent',
        'group_name': CLAIMS['groups'], 'user_username': CLAIMS['given_name'], 'user_lastname': CLAIMS['surname'],
        'sign_messages': 1, 'sign_assertions': 1, 'sign_authn_requests': 0,
        'sign_logout_requests': 0, 'sign_logout_responses': 0,
        'encrypt_nameid': 0, 'encrypt_assertions': 0, 'scim_status': 0,
        'provision_groups': [{'name': ids[k], 'roleid': '@role.' + k,
                              'user_groups': [{'usrgrpid': '@group.' + k}]} for k in ids],
    }})
    plan.append({'method': 'authentication.update', 'params': {
        'authentication_type': 0, 'http_auth_enabled': 0, 'saml_auth_enabled': 1,
        'saml_jit_status': 1, 'saml_case_sensitive': 1, 'disabled_usrgrpid': '@group.disabled'}})
    return {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'zabbix-access', 'namespace': 'zabbix'},
            'type': 'Opaque', 'stringData': {
                'config.yaml': yaml.safe_dump(config, sort_keys=False),
                'credentials.json': json.dumps(credentials), 'idp.crt': certificate,
                'native-plan.json': json.dumps({'version': '8.0.0', 'referenceResolution':
                    'Review only: resolve @ references to IDs from native create/get results; never send placeholders.',
                    'operations': plan}, indent=2) + '\n'}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; review/rotate explicitly')
    try:
        owner = json.loads(args.owner_input.read_text())
        bundle = build(owner, Path(owner['idp_certificate_file']).read_text())
        encrypted = subprocess.run(['sops', '--encrypt', '--input-type', 'yaml', '--output-type', 'yaml',
                                    '--filename-override', str(ROOT / 'secrets/zabbix-access.enc.yaml'), '/dev/stdin'],
                                   input=yaml.safe_dump(bundle, sort_keys=False), text=True,
                                   capture_output=True, cwd=ROOT, check=True).stdout
        result = yaml.safe_load(encrypted)
        if (not result.get('sops') or set(result.get('stringData', {})) != set(bundle['stringData'])
                or not all(v.startswith('ENC[AES256_GCM,') for v in result['stringData'].values())):
            raise ValueError('Missing SOPS encryption')
        with args.output.open('x') as output:
            output.write(encrypted)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        parser.exit(1, 'Invalid owner input, certificate or SOPS operation; no plaintext output written.\n')
    print('Encrypted owner bundle prepared; no external services contacted; activation remains gated.')


if __name__ == '__main__':
    main()
