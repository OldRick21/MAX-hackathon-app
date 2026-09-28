"""Сквозной прогон «Люди» против живого ядра: реальные JWT, machine-обмен, introspection.

Транспорт сервиса замкнут на ядро в этом же процессе, поэтому порты и ожидания не нужны,
но проверяются настоящие токены, scopes, членство и манифест.

    cd services/user-profile && PYTHONPATH=../../backend python -m unittest tests.test_integration_core -v
"""
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / 'backend'
sys.path.insert(0, str(BACKEND))

_tmp = tempfile.TemporaryDirectory()
PROV = 'u' * 48
os.environ.update(
    DATABASE_URL=f'sqlite:///{_tmp.name}/core.db', JWT_ISSUER='https://core.test',
    JWT_KEYRING_PATH=f'{_tmp.name}/keys.json', CURSOR_SECRET_KEY='people-secret-' * 4, MAX_BOT_TOKEN='',
    ALLOW_DEV_LOGIN='true', SEED_DEMO_DATA='false', ALLOW_FAKE_REDIS='true', REDIS_PORT='1',
    CLOUD_BINDING_KEY='b' * 48, ADMINISTRATION_PROVISIONING_TOKEN='p' * 48,
    USER_PROFILE_PROVISIONING_TOKEN=PROV, SERVICE_CONFIG_DIR='',
    USER_PROFILE_API_BASE_URL='https://shell.test/people/api/v1',
    USER_PROFILE_CLIENT_BASE_URL='https://shell.test',
    PROFILE_DB=f'{_tmp.name}/profiles.db',
)

from fastapi.testclient import TestClient  # noqa: E402

import manage  # noqa: E402
from database.create_tables import session_local  # noqa: E402
from database.tables import Membership, PlatformStaff, ServiceInstance  # noqa: E402
from main import app as core_app  # noqa: E402

from app import main as people  # noqa: E402
from app.core_client import CoreClient  # noqa: E402


class CoreTransport:
    """requests-совместимая сессия поверх TestClient ядра."""

    def __init__(self, client):
        self.client = client

    def request(self, method, url, **kwargs):
        kwargs.pop('timeout', None)
        kwargs.pop('allow_redirects', None)
        return self.client.request(method, url.replace('http://core.test', ''), **kwargs)


class PeopleAgainstCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core_ctx = TestClient(core_app)
        cls.core = cls.core_ctx.__enter__()
        people.core = CoreClient('http://core.test', PROV, session=CoreTransport(cls.core))
        cls.people_ctx = TestClient(people.app)
        cls.people = cls.people_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.people_ctx.__exit__(None, None, None)
        cls.core_ctx.__exit__(None, None, None)
        from database.create_tables import engine
        engine.dispose()
        _tmp.cleanup()

    def login(self, name):
        pair = self.core.post('/api/v1/auth/token', json={'username': f'{name}_{uuid.uuid4().hex[:6]}'}).json()
        headers = {'Authorization': 'Bearer ' + pair['access_token']}
        self.refresh[name] = pair['refresh_token']
        return self.core.get('/api/v1/auth/me', headers=headers).json()['id'], headers

    def service_session(self, inst_id, service_id, core_headers, profile):
        created = self.core.post(f'/api/v1/institution/{inst_id}/service/{service_id}/session',
                                 json={'profile': profile}, headers=core_headers)
        self.assertEqual(created.status_code, 201, created.text)
        return {'Authorization': 'Bearer ' + created.json()['access_token']}

    def test_end_to_end(self):
        self.refresh = {}
        owner_id, owner_core = self.login('owner')
        support_id, support_core = self.login('support')
        teacher_id, teacher_core = self.login('teacher')

        with session_local() as db:
            db.add(PlatformStaff(user_id=support_id, role='platform_support', granted_by='test'))
            db.commit()
        created = self.core.post('/api/v1/institution-applications', headers=owner_core,
                                 json={'titles': {'ru': 'Университет «Люди»'}, 'contact': 'rector@example.ru'})
        app_id = created.json()['id']
        inst_id = self.core.post(f'/api/v1/platform/applications/{app_id}/approve',
                                 headers=support_core, json={}).json()['institution_id']

        # Операторская установка сервиса вузу — та же команда, что в README.
        manage.install_people(inst_id)
        with session_local() as db:
            people_id = db.query(ServiceInstance).filter_by(institution_id=inst_id,
                                                            service_type='user-profile').one().id
            admin_service_id = db.query(ServiceInstance).filter_by(institution_id=inst_id,
                                                                   service_type='administration').one().id
            db.add(Membership(institution_id=inst_id, user_id=teacher_id, profiles=['teacher']))
            db.commit()

        admin_h = self.service_session(inst_id, people_id, owner_core, 'admin')
        teacher_h = self.service_session(inst_id, people_id, teacher_core, 'teacher')

        # ServiceView собран из binding и живого манифеста.
        view = self.people.get('/api/v1/service', headers=teacher_h)
        self.assertEqual(view.status_code, 200, view.text)
        view = view.json()
        self.assertEqual((view['id'], view['institution_id'], view['service_type']), (people_id, inst_id, 'user-profile'))
        self.assertEqual(view['api_base_url'], 'https://shell.test/people/api/v1')
        self.assertEqual([m['id'] for m in view['menus']], ['home', 'users'])

        # Виртуальная карточка, сохранение, каталог.
        empty = self.people.get('/api/v1/profile/me', headers=teacher_h)
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertIsNone(empty.json()['display_name'])
        saved = self.people.patch('/api/v1/profile/me', headers={**teacher_h, 'If-Match': empty.headers['etag']},
                                  json={'display_name': 'Мария Смирнова', 'about': 'Кафедра 42'})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.people.patch('/api/v1/profile/me',
                                           headers={**teacher_h, 'If-Match': empty.headers['etag']},
                                           json={'display_name': 'Другая'}).status_code, 412)
        listed = self.people.get('/api/v1/profile/users', headers=admin_h).json()['items']
        self.assertEqual([x['user_id'] for x in listed], [teacher_id])

        # Должность ставит только admin с profiles.manage; роль назначает владелец.
        card = self.people.get(f'/api/v1/profile/users/{teacher_id}', headers=admin_h)
        academic = {'position': 'Доцент', 'academic_degree': 'к.т.н.'}
        self.assertEqual(self.people.patch(f'/api/v1/profile/users/{teacher_id}',
                                           headers={**admin_h, 'If-Match': card.headers['etag']},
                                           json=academic).status_code, 403)
        roles_path = f'/api/v1/institution/{inst_id}/internal/services/{people_id}/users/{owner_id}/profiles/admin/roles'
        machine, actor = self.admin_headers(inst_id, admin_service_id, owner_core)
        etag = self.core.get(roles_path, headers={**machine, **actor}).headers['ETag']
        granted = self.core.put(roles_path, headers={**machine, **actor, 'If-Match': etag},
                                json={'roles': ['profile_editor']})
        self.assertEqual(granted.status_code, 200, granted.text)
        self.assertEqual(granted.json()['permissions'], ['profiles.manage'])

        # Права берутся из ядра онлайн: прежний токен уже видит новое право.
        updated = self.people.patch(f'/api/v1/profile/users/{teacher_id}',
                                    headers={**admin_h, 'If-Match': card.headers['etag']}, json=academic)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual((updated.json()['position'], updated.json()['academic_degree']), ('Доцент', 'к.т.н.'))
        self.assertEqual(self.people.get('/api/v1/profile/users?q=доцент', headers=admin_h).json()['items'][0]['user_id'],
                         teacher_id)

        # Токен другого сервиса того же вуза к «Людям» не подходит: чужой binding — 404,
        # а не 503 (SDK SPEC §4.2 п.2), иначе чужой токен объявлял бы сервис недоступным.
        foreign = self.service_session(inst_id, admin_service_id, owner_core, 'admin')
        rejected = self.people.get('/api/v1/profile/me', headers=foreign)
        self.assertEqual((rejected.status_code, rejected.json()['error']['code']), (404, 'RESOURCE_NOT_FOUND'))

        # Снятое членство закрывает доступ и убирает карточку из каталога.
        with session_local() as db:
            db.delete(db.get(Membership, (inst_id, teacher_id)))
            db.commit()
        self.assertEqual(self.people.get(f'/api/v1/profile/users/{teacher_id}', headers=admin_h).status_code, 404)
        self.assertEqual(self.people.get('/api/v1/profile/users', headers=admin_h).json()['items'], [])
        # Своя же сессия падает раньше карточки: снятый профиль гасит introspection → 401
        # на следующем online-check (SDK SPEC §4.2), без offline-окна.
        self.assertEqual(self.people.get('/api/v1/profile/me', headers=teacher_h).status_code, 401)

        # Выход из сессии ядра гасит сервисную сессию.
        logout = self.core.post('/api/v1/auth/logout', headers=owner_core,
                                json={'refresh_token': self.refresh['owner']})
        self.assertEqual(logout.status_code, 204, logout.text)
        self.assertEqual(self.people.get('/api/v1/profile/me', headers=admin_h).status_code, 401)

    def admin_headers(self, inst_id, admin_service_id, core_headers):
        """Machine + actor заголовки private API: так владелец назначает роли."""
        actor = self.service_session(inst_id, admin_service_id, core_headers, 'admin')
        binding = self.core.get(f'/api/v1/internal/provisioning/bindings/{admin_service_id}',
                                headers={'Authorization': 'Bearer ' + 'p' * 48}).json()
        machine = self.core.post('/api/v1/internal/auth/token',
                                 auth=(binding['client_id'], binding['client_secret']),
                                 json={'grant_type': 'client_credentials'}).json()
        return ({'Authorization': 'Bearer ' + machine['access_token']},
                {'X-Actor-Token': actor['Authorization'].removeprefix('Bearer ')})

    def test_startup_repairs_stale_addresses(self):
        # Экземпляр, созданный до настройки адресов, чинится при запуске ядра без install-people.
        from platform_core import registry
        with session_local() as db:
            inst = registry.provision_institution(db, {'ru': 'Старый вуз'}, 'ru')
            service = registry.create_cloud_instance(db, inst.id, 'user-profile')
            service.api_base_url = 'https://profiles.platform.example/api/v1'
            service.client_base_url = 'https://profiles.platform.example'
            service.enabled = False
            db.commit()
            service_id = service.id
        with session_local() as db:
            registry.ensure_platform_invariants(db)
            db.commit()
        with session_local() as db:
            service = db.get(ServiceInstance, service_id)
            self.assertEqual((service.api_base_url, service.client_base_url),
                             ('https://shell.test/people/api/v1', 'https://shell.test'))
            self.assertFalse(service.enabled)


if __name__ == '__main__':
    unittest.main()
