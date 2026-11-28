"""Offline security contracts; live Entra/Cloudflare acceptance is separate."""
import base64
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('access', ROOT / 'scripts/zabbix/build_access_bundle.py')
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)


class AccessContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        p = Path(cls.temp.name)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(p / 'key'), '-out', str(p / 'cert'), '-days', '2',
                        '-subj', '/CN=Disposable-unit-fixture'], check=True, capture_output=True)
        cls.cert = (p / 'cert').read_text()
        cls.owner = {'tenant_id': str(uuid.uuid4()),
                     'group_object_ids': {k: str(uuid.uuid4()) for k in ['admin', 'operator', 'viewer']},
                     'cloudflare': {'team_name': 'unit-fixture', 'audience': 'a' * 64,
                                    'tunnel_credentials': {'AccountTag': 'b' * 32,
                                        'TunnelID': str(uuid.uuid4()),
                                        'TunnelSecret': base64.b64encode(b'x' * 32).decode()}}}
        cls.bundle = access.build(cls.owner, cls.cert)
        cls.plan = json.loads(cls.bundle['stringData']['native-plan.json'])['operations']

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_exact_group_ids_with_no_fallback_or_super_admin(self):
        self.assertEqual(self.plan[0], {'method': 'usergroup.update', 'params': {
            'usrgrpid': '@group.Guests', 'gui_access': 3, 'users_status': 1}})
        directory = next(op['params'] for op in self.plan if op['method'] == 'userdirectory.create')
        self.assertEqual({p['name'] for p in directory['provision_groups']}, set(self.owner['group_object_ids'].values()))
        self.assertEqual(directory['username_attribute'], access.CLAIMS['username'])
        self.assertEqual(directory['group_name'], access.CLAIMS['groups'])
        self.assertEqual(directory['scim_status'], 0)
        self.assertNotIn('*', str(directory['provision_groups']))
        self.assertEqual([r['type'] for r in access.roles().values()], [2, 1, 1])
        auth = self.plan[-1]['params']
        self.assertEqual(auth['disabled_usrgrpid'], '@group.disabled')
        self.assertEqual((auth['authentication_type'], auth['http_auth_enabled']), (0, 0))

    def test_overlapping_user_roles_choose_operator_before_viewer(self):
        # Native 8.0 sorts type DESC then name ASC; runtime fixtures are retained
        # separately. This guards the names whose order is security-relevant.
        roles = access.roles()
        self.assertLess(roles['operator']['name'], roles['viewer']['name'])
        self.assertGreater(roles['admin']['type'], roles['operator']['type'])

    def test_explicit_rules_cover_every_native_current_permission(self):
        fixture = json.loads((ROOT / 'scripts/tests/fixtures/zabbix/role-permissions-8.0.0.json').read_text())
        self.assertEqual(fixture['nativeVersion'], '8.0.0')
        # Captured via native role.get, independently of the plan builder. Missing
        # entries inherit enabled creation defaults in 8.0.0 despite the new
        # default_access=0. Both full explicit coverage and default flags matter.
        for role in access.roles().values():
            native = fixture['permissionsByType'][str(role['type'])]
            for field in ['ui', 'actions']:
                self.assertEqual(sorted(r['name'] for r in role['rules'][field]), native[field])

    def test_roles_deny_api_scripts_and_new_permissions_by_default(self):
        for role in access.roles().values():
            rules = role['rules']
            for field in ['ui.default_access', 'modules.default_access', 'api.access', 'actions.default_access']:
                self.assertEqual(rules[field], 0)
            actions = {a['name']: a['status'] for a in rules['actions']}
            self.assertEqual(actions['execute_scripts'], 0)
            self.assertEqual(actions['manage_api_tokens'], 0)
            self.assertEqual(actions['invoke_execute_now'], 0)
            ui = {u['name']: u['status'] for u in rules['ui']}
            self.assertEqual(ui['services.services'], 0)
            self.assertEqual(ui['apm.traces'], 0)
        self.assertTrue(all(a['status'] == 0 for a in access.roles()['viewer']['rules']['actions']))
        self.assertEqual({a['name'] for a in access.roles()['operator']['rules']['actions'] if a['status']},
                         {'acknowledge_problems', 'add_problem_comments'})

    def test_host_permissions_are_explicit_and_deprovisioned_group_disabled(self):
        groups = [op['params'] for op in self.plan if op['method'] == 'usergroup.create']
        self.assertEqual([g['hostgroup_rights'] for g in groups[:3]], [
            [{'id': '@hostgroup.Applications', 'permission': 3}],
            [{'id': '@hostgroup.Applications', 'permission': 3}],
            [{'id': '@hostgroup.Applications', 'permission': 2}]])
        self.assertEqual((groups[-1]['gui_access'], groups[-1]['users_status']), (3, 1))

    def test_both_response_and_assertion_must_be_signed(self):
        directory = next(op['params'] for op in self.plan if op['method'] == 'userdirectory.create')
        self.assertEqual((directory['sign_messages'], directory['sign_assertions']), (1, 1))
        self.assertEqual((directory['sign_authn_requests'], directory['encrypt_assertions']), (0, 0))
        self.assertEqual(directory['slo_url'], '')
        patch = yaml.safe_load((ROOT / 'infra/zabbix-access/frontend.yaml').read_text())
        env = {e['name']: e['value'] for e in patch['spec']['template']['spec']['containers'][0]['env']}
        settings = json.loads(env['ZBX_SSO_SETTINGS'])
        self.assertTrue(settings['strict'])
        self.assertFalse(settings['use_proxy_headers'])
        self.assertEqual(settings['baseurl'], 'https://zabbix.cooked.beer/')
        self.assertEqual(env['ZBX_CERT_STORAGE'], 'file')

    def test_connector_validates_access_jwt_for_all_paths(self):
        config = yaml.safe_load(self.bundle['stringData']['config.yaml'])
        ingress = config['ingress']
        self.assertEqual(len(ingress), 2)
        self.assertEqual(ingress[0]['service'], 'http://127.0.0.1:8081')
        self.assertNotIn('path', ingress[0])
        self.assertEqual(ingress[0]['originRequest']['access'], {
            'required': True, 'teamName': 'unit-fixture', 'audTag': ['a' * 64]})
        self.assertEqual(ingress[1], {'service': 'http_status:404'})

    def test_invalid_or_ambiguous_identity_and_access_inputs_fail_closed(self):
        mutations = [
            lambda o: o['group_object_ids'].update(admin='*'),
            lambda o: o['group_object_ids'].update(admin=o['group_object_ids']['viewer']),
            lambda o: o.update(tenant_id='00000000-0000-0000-0000-000000000000'),
            lambda o: o['cloudflare'].update(team_name='example.com/escape'),
            lambda o: o['cloudflare'].update(audience=''),
            lambda o: o['cloudflare']['tunnel_credentials'].update(TunnelID=access.SHARED_TUNNEL),
            lambda o: o['cloudflare']['tunnel_credentials'].update(TunnelSecret='bad'),
        ]
        for mutate in mutations:
            owner = copy.deepcopy(self.owner)
            mutate(owner)
            with self.subTest(owner_field_validation=True), self.assertRaises(ValueError):
                access.build(owner, self.cert)

    def test_malformed_or_private_key_certificate_is_rejected(self):
        for cert in ['', 'not a certificate', self.cert * 2, self.cert + '\nPRIVATE KEY']:
            with self.assertRaises(ValueError):
                access.build(self.owner, cert)

    def test_overlay_is_staged_and_keeps_frontend_on_loopback(self):
        self.assertEqual(yaml.safe_load((ROOT / 'clusters/home/infrastructure/zabbix-kustomization.yaml').read_text())['spec']['path'], './infra/zabbix')
        self.assertNotIn('zabbix-access', (ROOT / 'infra/cloudflared/config.yaml').read_text())
        config = (ROOT / 'infra/zabbix-access/nginx.conf').read_text()
        self.assertIn('listen 127.0.0.1:8080;', config)
        self.assertIn('listen 127.0.0.1:8081;', config)
        self.assertNotIn('listen-ipv6', config)
        self.assertIn('if ($arg_reconnect != 1) { return 302 https://zabbix.cooked.beer/index_sso.php; }', config)
        self.assertIn('if ($arg_enter != "") { return 403; }', config)
        self.assertIn('api_jsonrpc|api_scim|setup', config)
        params = (ROOT / 'infra/zabbix-access/fastcgi_params').read_text()
        self.assertIn('HTTPS $zabbix_https', params)
        self.assertNotIn('$http_x_forwarded', params)

    def test_certificate_and_tunnel_credentials_are_separate_projections(self):
        patch = yaml.safe_load((ROOT / 'infra/zabbix-access/frontend.yaml').read_text())
        volumes = patch['spec']['template']['spec']['volumes']
        self.assertEqual([i['key'] for i in volumes[0]['secret']['items']], ['idp.crt'])
        self.assertEqual({i['key'] for i in volumes[1]['secret']['items']}, {'config.yaml', 'credentials.json'})
        sidecar = patch['spec']['template']['spec']['containers'][1]
        self.assertTrue(sidecar['securityContext']['readOnlyRootFilesystem'])
        self.assertEqual(sidecar['securityContext']['capabilities']['drop'], ['ALL'])


if __name__ == '__main__':
    unittest.main()
