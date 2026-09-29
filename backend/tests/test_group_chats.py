"""Ссылки на чаты групп: отдельная роль администратора и bot-only чтение."""
import os
import tempfile
import unittest
import uuid
from pathlib import Path

_tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2] / '.test-tmp')
os.environ.update(
    DATABASE_URL=f'sqlite:///{_tmp.name}/core.db', JWT_ISSUER='https://core.test',
    JWT_KEYRING_PATH=f'{_tmp.name}/keys.json', CURSOR_SECRET_KEY='chat-cursor-secret-' * 3,
    MAX_BOT_TOKEN='', BOT_CORE_TOKEN='bot-core-secret-' * 4,
    ALLOW_DEV_LOGIN='true', SEED_DEMO_DATA='false', ALLOW_FAKE_REDIS='true',
    RATE_LIMIT_LOGIN='0', RATE_LIMIT_REFRESH='0', RATE_LIMIT_MACHINE_EXCHANGE='0',
    RATE_LIMIT_USER='0', RATE_LIMIT_CREDENTIAL='0', REDIS_PORT='1',
    CLOUD_BINDING_KEY='b' * 48, ADMINISTRATION_PROVISIONING_TOKEN='p' * 48,
    SCHEDULE_PROVISIONING_TOKEN='s' * 48, USER_PROFILE_PROVISIONING_TOKEN='u' * 48,
    SERVICE_CONFIG_DIR='',
)

from testing_consent import TestClient  # noqa: E402
from tests.keys import issue_key  # noqa: E402
from database.create_tables import session_local  # noqa: E402
from database.tables import AuditEvent, Membership, PlatformStaff, RoleAssignment, ServiceInstance, User  # noqa: E402
from main import app  # noqa: E402


class GroupChats(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = TestClient(app)
        cls.c = cls.ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        from database.create_tables import engine
        engine.dispose()
        _tmp.cleanup()

    def login(self, name, max_user_id=None):
        pair = self.c.post('/api/v1/auth/token', json={'username': f'{name}_{uuid.uuid4().hex[:6]}'}).json()
        headers = {'Authorization': 'Bearer ' + pair['access_token']}
        user_id = self.c.get('/api/v1/auth/me', headers=headers).json()['id']
        if max_user_id:
            with session_local() as db:
                db.get(User, user_id).max_user_id = str(max_user_id)
                db.commit()
        return user_id, headers

    def private(self, institution_id, service_id, core_headers):
        actor = self.c.post(f'/api/v1/institution/{institution_id}/service/{service_id}/session',
                            json={'profile': 'admin'}, headers=core_headers).json()['access_token']
        binding = issue_key(service_id)
        machine = self.c.post('/api/v1/internal/auth/token',
                              auth=(binding['client_id'], binding['client_secret']),
                              json={'grant_type': 'client_credentials'}).json()['access_token']
        return {'Authorization': 'Bearer ' + machine, 'X-Actor-Token': actor}

    def test_admin_and_bot_flow(self):
        owner_id, owner = self.login('owner')
        support_id, support = self.login('support')
        creator_id, creator = self.login('creator')
        student_id, student = self.login('student', 700001)
        outsider_id, _ = self.login('outsider', 700002)
        with session_local() as db:
            db.add(PlatformStaff(user_id=support_id, role='platform_support', granted_by='test'))
            db.commit()
        application = self.c.post('/api/v1/institution-applications', headers=owner,
                                  json={'titles': {'ru': 'Тестовый вуз'}, 'contact': 'test@example.ru'}).json()['id']
        institution = self.c.post(f'/api/v1/platform/applications/{application}/approve',
                                  headers=support, json={}).json()['institution_id']
        with session_local() as db:
            service = db.query(ServiceInstance).filter_by(
                institution_id=institution, service_type='administration').one()
            admin_service = service.id
            db.add_all([
                Membership(institution_id=institution, user_id=creator_id, profiles=['admin']),
                Membership(institution_id=institution, user_id=student_id, profiles=['student']),
                RoleAssignment(service_id=admin_service, user_id=creator_id, profile='admin', roles=['chat_creator']),
            ])
            db.commit()

        owner_private = self.private(institution, admin_service, owner)
        creator_private = self.private(institution, admin_service, creator)
        groups = f'/api/v1/institution/{institution}/internal/groups'
        group = self.c.post(groups, headers=owner_private, json={'name': 'ИВТ-42'}).json()['id']
        self.c.put(f'/api/v1/institution/{institution}/internal/members/{student_id}/group',
                   headers=owner_private, json={'group_id': group})

        # Создатель чатов видит названия групп, но не участников и не может менять группы.
        listed = self.c.get(f'/api/v1/institution/{institution}/internal/group-chats',
                            headers=creator_private)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()['items'][0]['chat_url'], None)
        self.assertEqual(self.c.get(f'{groups}/{group}/members', headers=creator_private).status_code, 403)
        self.assertEqual(self.c.post(groups, headers=creator_private, json={'name': 'Нельзя'}).status_code, 403)

        chat_path = f'{groups}/{group}/chat'
        current = self.c.get(chat_path, headers=creator_private)
        self.assertEqual(self.c.put(chat_path, headers=creator_private,
                                    json={'url': 'https://max.ru/join/no-etag'}).status_code, 428)
        bad = self.c.put(chat_path, headers={**creator_private, 'If-Match': current.headers['ETag']},
                         json={'url': 'http://max.ru/join/insecure'})
        self.assertEqual(bad.status_code, 422)
        url = 'https://max.ru/join/secret-value'
        saved = self.c.put(chat_path, headers={**creator_private, 'If-Match': current.headers['ETag']},
                           json={'url': url})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()['chat_url'], url)
        public_group = self.c.get(f'/api/v1/institution/{institution}/groups',
                                  params={'profile': 'student'}, headers=student).json()['items'][0]
        self.assertNotIn('chat_url', public_group)
        self.assertEqual(self.c.get(f'{groups}/{uuid.uuid4()}/chat', headers=creator_private).status_code, 404)

        # В аудит попадает факт изменения, но не сама секретоподобная ссылка.
        with session_local() as db:
            event = db.query(AuditEvent).filter(AuditEvent.action == 'group_chat_link.update').order_by(
                AuditEvent.id.desc()).first()
            self.assertIsNotNone(event)
            self.assertNotIn(url, str(event.details))

        bot_headers = {'Authorization': 'Bearer ' + os.environ['BOT_CORE_TOKEN']}
        endpoint = '/api/v1/internal/bot/group-chats/resolve'
        self.assertEqual(self.c.post(endpoint, headers={'Authorization': 'Bearer wrong'},
                                    json={'max_user_id': '700001'}).status_code, 401)
        self.assertEqual(self.c.post(endpoint, headers={'Authorization': 'Bearer wrong'},
                                    content=b'{broken').status_code, 401)
        self.assertEqual(self.c.post(endpoint, headers=bot_headers,
                                    json={'max_user_id': '700002'}).json(), {'institutions': []})
        resolved = self.c.post(endpoint, headers=bot_headers, json={'max_user_id': '700001'})
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertEqual(resolved.json()['institutions'][0]['groups'], [
            {'id': group, 'name': 'ИВТ-42', 'chat_url': url},
        ])

        # Очистка ссылки немедленно убирает чат из ответа боту.
        cleared = self.c.put(chat_path, headers={**creator_private, 'If-Match': saved.headers['ETag']},
                             json={'url': None})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(self.c.post(endpoint, headers=bot_headers,
                                    json={'max_user_id': '700001'}).json(), {'institutions': []})


if __name__ == '__main__':
    unittest.main()
