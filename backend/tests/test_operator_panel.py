"""Операторская веб-панель: вход, защита, операции платформы и управление вузом.

    cd backend && python -m unittest tests.test_operator_panel -v
"""
import os
import tempfile
import unittest
import uuid

_tmp = tempfile.TemporaryDirectory()
os.environ.update(
    DATABASE_URL=f'sqlite:///{_tmp.name}/core.db', JWT_ISSUER='https://core.test',
    JWT_KEYRING_PATH=f'{_tmp.name}/keys.json', CURSOR_SECRET_KEY='operator-secret-' * 4, MAX_BOT_TOKEN='',
    ALLOW_DEV_LOGIN='true', SEED_DEMO_DATA='false', ALLOW_FAKE_REDIS='true', REDIS_PORT='1',
    CLOUD_BINDING_KEY='b' * 48, ADMINISTRATION_PROVISIONING_TOKEN='p' * 48, SCHEDULE_PROVISIONING_TOKEN='s' * 48,
    SERVICE_CONFIG_DIR='', OPERATOR_PASSWORD='correct horse battery', OPERATOR_COOKIE_SECURE='false',
    OPERATOR_HEALTH_TARGETS='{}',
)

from fastapi.testclient import TestClient  # noqa: E402

from database.create_tables import session_local  # noqa: E402
from database.tables import AuditEvent, PlatformStaff  # noqa: E402
from main import app as core_app  # noqa: E402
from operator_panel.app import app as operator_app, _failures  # noqa: E402

W = {'X-Operator': '1'}


class OperatorPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core_ctx = TestClient(core_app)
        cls.core = cls.core_ctx.__enter__()  # создаёт таблицы
        cls.op = TestClient(operator_app)

    @classmethod
    def tearDownClass(cls):
        cls.core_ctx.__exit__(None, None, None)
        from database.create_tables import engine
        engine.dispose()
        _tmp.cleanup()

    def login_user(self, name):
        pair = self.core.post('/api/v1/auth/token', json={'username': f'{name}_{uuid.uuid4().hex[:6]}'}).json()
        headers = {'Authorization': 'Bearer ' + pair['access_token']}
        return self.core.get('/api/v1/auth/me', headers=headers).json()['id'], headers

    def signed_in(self):
        client = TestClient(operator_app)
        self.assertEqual(client.post('/api/login', json={'password': 'correct horse battery'}).status_code, 200)
        return client

    def test_login_and_guards(self):
        anon = TestClient(operator_app)
        self.assertEqual(anon.get('/').status_code, 200)
        self.assertIn("frame-ancestors 'none'", anon.get('/').headers['Content-Security-Policy'])
        self.assertEqual(anon.get('/api/overview').status_code, 401)
        self.assertFalse(anon.get('/api/session').json()['authenticated'])
        _failures.clear()
        for _ in range(5):
            self.assertEqual(anon.post('/api/login', json={'password': 'nope'}).status_code, 401)
        self.assertEqual(anon.post('/api/login', json={'password': 'correct horse battery'}).status_code, 429)
        _failures.clear()

        op = self.signed_in()
        self.assertTrue(op.get('/api/session').json()['authenticated'])
        # Изменения без X-Operator отклоняются (защита от CSRF).
        self.assertEqual(op.post('/api/tools/ensure-invariants').status_code, 403)
        self.assertEqual(op.post('/api/tools/ensure-invariants', headers=W).status_code, 200)
        # Подделанная cookie не проходит.
        forged = TestClient(operator_app, cookies={'operator_session': '9999999999.x.AAAA'})
        self.assertEqual(forged.get('/api/overview').status_code, 401)
        op.post('/api/logout', headers=W)

    def test_platform_and_institution_management(self):
        op = self.signed_in()
        applicant, applicant_h = self.login_user('rector')
        student, student_h = self.login_user('student')
        support, _ = self.login_user('support')

        # Заявка вуза из приложения одобряется только в панели.
        app_id = self.core.post('/api/v1/institution-applications', headers=applicant_h,
                                json={'titles': {'ru': 'Политех'}, 'contact': 'r@example.ru'}).json()['id']
        pending = op.get('/api/applications?status=pending').json()['items']
        self.assertIn(app_id, [a['id'] for a in pending])
        approved = op.post(f'/api/applications/{app_id}/approve', headers=W, json={'titles': {'ru': 'Политех СПб'}})
        self.assertEqual(approved.status_code, 200, approved.text)
        inst = approved.json()['institution_id']
        self.assertEqual(op.post(f'/api/applications/{app_id}/approve', headers=W).json()['error']['code'],
                         'APPLICATION_NOT_PENDING')

        # Вуз напрямую, с владельцем и сервисами платформы.
        created = op.post('/api/institutions', headers=W, json={'titles': {'ru': 'Горный'}, 'owner_user_id': support,
                                                                'services': ['schedule', 'user-profile']})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(sorted(s['service_type'] for s in created.json()['services']),
                         ['administration', 'schedule', 'user-profile'])
        names = {i['id']: i['titles']['ru'] for i in op.get('/api/institutions').json()['items']}
        self.assertEqual(names[inst], 'Политех СПб')

        # Управление вузом: группы, заявка на вступление, участники, роли.
        base = f'/api/institutions/{inst}/manage'
        group = op.post(f'{base}/groups', headers=W, json={'name': 'ИВТ-1'}).json()['id']
        request = self.core.post('/api/v1/join-requests', headers=student_h, json={
            'full_name': 'Мария Ильина', 'items': [{'institution_id': inst, 'profile': 'student', 'group_id': group}]})
        self.assertEqual(request.status_code, 201, request.text)
        overview = op.get('/api/overview').json()
        self.assertEqual(overview['counts']['pending_join_requests'], 1)
        rid = request.json()['items'][0]['id']
        self.assertEqual(op.post(f'{base}/join-requests/{rid}/approve', headers=W).status_code, 200)
        members = {m['user_id']: m for m in op.get(f'{base}/members').json()['items']}
        self.assertEqual(members[student]['group_id'], group)
        self.assertEqual(members[student]['full_name'], 'Мария Ильина')

        member = op.get(f'{base}/members/{student}')
        changed = op.put(f'{base}/members/{student}/profiles', headers={**W, 'If-Match': member.headers['ETag']},
                         json={'profiles': ['student', 'teacher']})
        self.assertEqual(changed.status_code, 200, changed.text)
        admin_service = next(s for s in op.get(f'{base}/services').json()['items'] if s['service_type'] == 'administration')
        path = f'{base}/services/{admin_service["id"]}/users/{applicant}/profiles/admin/roles'
        tag = op.get(path).headers['ETag']
        # Последнего владельца снять нельзя даже оператору.
        self.assertEqual(op.put(path, headers={**W, 'If-Match': tag}, json={'roles': []}).json()['error']['code'],
                         'LAST_OWNER')
        self.assertEqual(op.post(f'/api/institutions/{inst}/owners', headers=W, json={'user_id': support}).status_code, 200)

        # Статус вуза и сервисы платформы.
        tag = op.get(f'/api/institutions/{inst}').headers['ETag']
        self.assertEqual(op.patch(f'/api/institutions/{inst}/status', headers={**W, 'If-Match': tag},
                                  json={'status': 'suspended'}).json()['status'], 'suspended')
        self.assertTrue(op.post(f'/api/institutions/{inst}/cloud/schedule', headers=W).json()['created'])
        self.assertEqual(op.get(f'{base}/nonsense').status_code, 404)

        # Пользователи: поиск по имени и права поддержки.
        found = op.get('/api/users?q=ильина').json()['items']
        self.assertEqual([u['id'] for u in found], [student])
        self.assertTrue(op.post(f'/api/users/{student}/staff', headers=W).json()['changed'])
        with session_local() as db:
            self.assertIsNotNone(db.get(PlatformStaff, student))
            self.assertTrue(db.query(AuditEvent).filter_by(actor_kind='operator', action='join_request.approve').count())
        self.assertTrue(op.delete(f'/api/users/{student}/staff', headers=W).json()['changed'])
        self.assertTrue(op.get(f'/api/audit?institution_id={inst}').json()['items'])


if __name__ == '__main__':
    unittest.main()
