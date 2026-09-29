"""Каркас шаблона запускается и соблюдает правила SDK на заглушке ядра.

    cd docs/services/sdk/service-template && python -m unittest discover -s tests -t . -v
"""
import os
import unittest
import uuid
from unittest.mock import patch

os.environ.update(CORE_URL='https://core.test', SHELL_ORIGIN='https://shell.test', SERVICE_CLIENT_ID='cid',
                  SERVICE_CLIENT_SECRET='secret', SERVICE_API_BASE_URL='https://svc.university.ru/api/v1',
                  SERVICE_CLIENT_BASE_URL='https://svc.university.ru')
import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main as m, sdk  # noqa: E402

INST, SERVICE, USER = (str(uuid.uuid4()) for _ in range(3))


def token(service=SERVICE, profile='student'):
    claims = {'service_id': service, 'sub': USER, 'aud': f'service:{service}', 'token_use': 'service_access',
              'profile': profile, 'sid': 's', 'parent_sid': 'p'}
    return {'Authorization': 'Bearer ' + jwt.encode(claims, 'x' * 32)}


class Template(unittest.TestCase):
    def setUp(self):
        info = {'active': True, 'service_id': SERVICE, 'institution_id': INST, 'sub': USER, 'profile': 'student',
                'session_id': 's', 'parent_session_id': 'p', 'roles': [], 'permissions': []}
        for p in (patch.object(m.core, 'binding', return_value=sdk.Binding(INST, SERVICE)),
                  patch.object(m.core, 'introspect', return_value=info),
                  patch.object(m.core, 'manifest', return_value=(m.MANIFEST, '"v1"')),
                  patch.object(sdk, 'onboard', lambda *a: None)):
            p.start()
            self.addCleanup(p.stop)
        self.c = TestClient(m.app).__enter__()

    def test_connection_and_rules(self):
        self.assertEqual(self.c.get('/api/v1/health').json()['status'], 'ok')
        self.assertEqual(self.c.get('/api/v1/whoami').status_code, 401)
        self.assertEqual(self.c.get('/api/v1/whoami', headers=token(service=str(uuid.uuid4()))).status_code, 404)
        me = self.c.get('/api/v1/whoami', headers=token())
        self.assertEqual((me.status_code, me.json()['user_id'], me.headers['cache-control']), (200, USER, 'no-store'))
        view = self.c.get('/api/v1/service', headers=token()).json()
        self.assertEqual((view['service_type'], [x['id'] for x in view['menus']]), ('custom.my-service', ['main']))

    def test_home_widget(self):
        self.assertEqual(self.c.get('/api/v1/widgets/summary').status_code, 401)
        data = self.c.get('/api/v1/widgets/summary', headers=token()).json()
        self.assertEqual((data['kind'], data['items'][0]['subtitle']), ('list', 'Профиль: student'))
        widget = m.MANIFEST['widgets'][0]
        self.assertEqual((widget['kind'], widget['data_path'], widget['open_menu']), ('list', '/widgets/summary', 'main'))

    def test_client_page_for_iframe(self):
        page = self.c.get('/app')
        self.assertIn('frame-ancestors https://shell.test', page.headers['content-security-policy'])
        self.assertIn('&quot;shell_origin&quot;: &quot;https://shell.test&quot;', page.text)
        self.assertNotIn('__BOOT__', page.text)
        self.assertEqual(self.c.get('/assets/sdk-client.js').status_code, 200)
        self.assertEqual(self.c.get('/assets/..%2Fapp%2Fmain.py').status_code, 404)

    def test_core_down_is_503(self):
        with patch.object(m.core, 'introspect', side_effect=sdk.CoreUnavailable('down')):
            self.assertEqual(self.c.get('/api/v1/whoami', headers=token()).status_code, 503)

    def test_cors_only_for_shell(self):
        ok = self.c.options('/api/v1/whoami', headers={'Origin': 'https://shell.test', 'Access-Control-Request-Method': 'GET'})
        self.assertEqual(ok.headers.get('access-control-allow-origin'), 'https://shell.test')
        bad = self.c.options('/api/v1/whoami', headers={'Origin': 'https://evil.test', 'Access-Control-Request-Method': 'GET'})
        self.assertNotEqual(bad.headers.get('access-control-allow-origin'), 'https://evil.test')


if __name__ == '__main__':
    unittest.main()
