"""Свой сервис вуза (custom.<код>) по CORE_API_SPEC.md §7: регистрация, ключ, самостоятельная
публикация роли и меню, включение, появление в каталоге пользователя.

    cd backend && python -m unittest tests.test_custom_services -v
"""
import os
import tempfile
import unittest
import uuid

_tmp = tempfile.TemporaryDirectory()
os.environ.update(
    DATABASE_URL=f'sqlite:///{_tmp.name}/core.db', JWT_ISSUER='https://core.test',
    JWT_KEYRING_PATH=f'{_tmp.name}/keys.json', CURSOR_SECRET_KEY='custom-secret-' * 4, MAX_BOT_TOKEN='',
    ALLOW_DEV_LOGIN='true', SEED_DEMO_DATA='false', ALLOW_FAKE_REDIS='true', REDIS_PORT='1',
    CLOUD_BINDING_KEY='b' * 48, ADMINISTRATION_PROVISIONING_TOKEN='p' * 48, SERVICE_CONFIG_DIR='',
)

from fastapi.testclient import TestClient  # noqa: E402

from tests.keys import issue_key, register_service  # noqa: E402

from database.create_tables import session_local  # noqa: E402
from database.tables import InstitutionLocalHost, Membership, PlatformStaff, ServiceInstance  # noqa: E402
from main import app  # noqa: E402

MENU = {'id': 'books', 'titles': {'ru': 'Книги'}, 'entrypoint_path': '/books', 'profiles': ['student'],
        'required_permissions': [], 'order': 0}


class CustomServices(unittest.TestCase):
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

    def login(self, name):
        pair = self.c.post('/api/v1/auth/token', json={'username': f'{name}_{uuid.uuid4().hex[:6]}'}).json()
        headers = {'Authorization': 'Bearer ' + pair['access_token']}
        return self.c.get('/api/v1/auth/me', headers=headers).json()['id'], headers

    def test_register_any_local_service(self):
        owner_id, owner = self.login('owner')
        support_id, support = self.login('support')
        student_id, student = self.login('student')
        with session_local() as db:
            db.add(PlatformStaff(user_id=support_id, role='platform_support', granted_by='test'))
            db.commit()
        app_id = self.c.post('/api/v1/institution-applications', headers=owner,
                             json={'titles': {'ru': 'Вуз'}, 'contact': 'r@example.ru'}).json()['id']
        inst = self.c.post(f'/api/v1/platform/applications/{app_id}/approve', headers=support, json={}).json()['institution_id']
        with session_local() as db:
            db.add(InstitutionLocalHost(institution_id=inst, hostname='library.university.ru', approved_by=support_id))
            db.add(Membership(institution_id=inst, user_id=student_id, profiles=['student']))
            admin_service = db.query(ServiceInstance).filter_by(institution_id=inst, service_type='administration').one().id
            db.commit()

        actor = self.c.post(f'/api/v1/institution/{inst}/service/{admin_service}/session', json={'profile': 'admin'},
                            headers=owner).json()['access_token']
        binding = issue_key(admin_service)
        machine = self.c.post('/api/v1/internal/auth/token', auth=(binding['client_id'], binding['client_secret']),
                              json={'grant_type': 'client_credentials'}).json()['access_token']
        private = {'Authorization': 'Bearer ' + machine, 'X-Actor-Token': actor}
        base = f'/api/v1/institution/{inst}/internal'
        body = {'service_type': 'custom.library', 'deployment': 'local', 'titles': {'ru': 'Библиотека'},
                'supported_profiles': ['student', 'teacher'], 'api_base_url': 'https://library.university.ru/api/v1',
                'client_base_url': 'https://library.university.ru'}

        # Неодобренный хост и кривой код отклоняются.
        bad_host = self.c.post(f'{base}/services', headers={**private, 'Idempotency-Key': str(uuid.uuid4())},
                               json={**body, 'api_base_url': 'https://evil.example.ru/api/v1'})
        self.assertEqual(bad_host.status_code, 422)
        bad_code = self.c.post(f'{base}/services', headers={**private, 'Idempotency-Key': str(uuid.uuid4())},
                               json={**body, 'service_type': 'custom.Ab'})
        self.assertEqual(bad_code.status_code, 422)

        created = self.c.post(f'{base}/services', headers={**private, 'Idempotency-Key': str(uuid.uuid4())}, json=body)
        self.assertEqual(created.status_code, 201, created.text)
        service = created.json()
        self.assertEqual((service['enabled'], service['manifest']['titles'], service['supported_profiles']),
                         (False, {'ru': 'Библиотека'}, ['student', 'teacher', 'admin']))  # admin — всегда
        dup = self.c.post(f'{base}/services', headers={**private, 'Idempotency-Key': str(uuid.uuid4())}, json=body)
        self.assertEqual(dup.status_code, 409)
        # Второй свой сервис с другим кодом — можно.
        other = self.c.post(f'{base}/services', headers={**private, 'Idempotency-Key': str(uuid.uuid4())},
                            json={**body, 'service_type': 'custom.dorm', 'titles': {'ru': 'Общежитие'}})
        self.assertEqual(other.status_code, 201, other.text)

        key = self.c.post(f'{base}/services/{service["id"]}/credentials', headers=private).json()
        token = self.c.post('/api/v1/internal/auth/token', json={'grant_type': 'client_credentials'},
                            auth=(key['credential']['client_id'], key['client_secret'])).json()['access_token']
        svc = {'Authorization': 'Bearer ' + token}
        own = f'/api/v1/internal/service/{service["id"]}'

        # Сервис создаёт роль только со своими правами.
        foreign = self.c.post(f'{own}/roles', headers=svc, json={'code': 'hacker', 'titles': {'ru': 'x'},
                                                                  'allowed_profiles': ['student'],
                                                                  'permissions': ['schedule.write']})
        self.assertEqual(foreign.status_code, 422, foreign.text)
        role = self.c.post(f'{own}/roles', headers=svc, json={'code': 'librarian', 'titles': {'ru': 'Библиотекарь'},
                                                               'allowed_profiles': ['teacher'],
                                                               'permissions': ['library.manage']})
        self.assertEqual(role.status_code, 201, role.text)

        # Меню публикуется с If-Match; профили только из заданных вузом.
        current = self.c.get(f'{own}/manifest', headers=svc)
        manifest = {'titles': {'ru': 'Библиотека'}, 'menus': [MENU]}
        self.assertEqual(self.c.put(f'{own}/manifest', headers=svc, json=manifest).status_code, 428)
        wrong = {'titles': {'ru': 'Библиотека'}, 'menus': [{**MENU, 'profiles': ['guest']}]}
        self.assertEqual(self.c.put(f'{own}/manifest', headers={**svc, 'If-Match': current.headers['etag']},
                                    json=wrong).status_code, 422)
        published = self.c.put(f'{own}/manifest', headers={**svc, 'If-Match': current.headers['etag']}, json=manifest)
        self.assertEqual(published.status_code, 200, published.text)

        # До включения студент сервиса не видит; после — видит его меню.
        def catalog():
            return self.c.get(f'/api/v1/institution/{inst}/service', params={'profile': 'student'},
                              headers=student).json()['items']
        self.assertNotIn(service['id'], [x['id'] for x in catalog()])
        tag = self.c.get(f'{base}/services/{service["id"]}', headers=private).headers['ETag']
        enabled = self.c.patch(f'{base}/services/{service["id"]}', headers={**private, 'If-Match': tag}, json={'enabled': True})
        self.assertEqual(enabled.status_code, 200, enabled.text)
        card = next(x for x in catalog() if x['id'] == service['id'])
        self.assertEqual((card['service_type'], card['display_name'], [m['id'] for m in card['menus']]),
                         ('custom.library', 'Библиотека', ['books']))

        # Администратор видит любой сервис вуза, даже без меню для admin и без ролей.
        admin_items = self.c.get(f'/api/v1/institution/{inst}/service', params={'profile': 'admin'}, headers=owner).json()['items']
        admin_card = next(x for x in admin_items if x['id'] == service['id'])
        self.assertEqual(([m['id'] for m in admin_card['menus']], admin_card['permissions']), (['books'], []))
        opened = self.c.post(f'/api/v1/institution/{inst}/service/{service["id"]}/session', json={'profile': 'admin'}, headers=owner)
        self.assertEqual(opened.status_code, 201, opened.text)


    def test_startup_gives_admins_every_service(self):
        from platform_core import registry
        with session_local() as db:
            inst = registry.provision_institution(db, {'ru': 'Старый вуз'}, 'ru')
            old = ServiceInstance(id=str(uuid.uuid4()), institution_id=inst.id, service_type='custom.old', deployment='local',
                                  enabled=True, protected=False, api_base_url='https://old.example.ru/api/v1',
                                  client_base_url='https://old.example.ru', supported_profiles=['student'], manifest={})
            db.add(old)
            db.commit()
            registry.ensure_platform_invariants(db)
            db.commit()
            self.assertEqual(db.get(ServiceInstance, old.id).supported_profiles, ['student', 'admin'])

    def test_startup_turns_old_hosting_into_institution_services(self):
        from database.tables import CloudBinding, ServiceCredential
        from platform_core import registry
        with session_local() as db:
            inst = registry.provision_institution(db, {'ru': 'Вуз с облаком'}, 'ru')
            legacy = ServiceInstance(id=str(uuid.uuid4()), institution_id=inst.id, service_type='schedule', deployment='cloud',
                                     enabled=True, protected=False, api_base_url='https://195.133.197.144/schedule/api/v1',
                                     client_base_url='https://195.133.197.144', supported_profiles=['student', 'teacher', 'admin'],
                                     manifest={'titles': {'ru': 'Расписание'}, 'menus': []})
            db.add(legacy)
            db.flush()
            cred, _ = registry.issue_credential(db, legacy)
            db.add(CloudBinding(service_id=legacy.id, institution_id=inst.id, service_type='schedule',
                                credential_id=cred.id, client_id=cred.client_id, revision=1, active=True))
            db.commit()
            registry.ensure_platform_invariants(db)
            db.commit()
            migrated = db.get(ServiceInstance, legacy.id)
            # Тот же UUID и тип контракта, но теперь это контейнер вуза (local); облачный ключ отозван.
            self.assertEqual((migrated.service_type, migrated.deployment), ('schedule', 'local'))
            self.assertFalse(db.get(CloudBinding, legacy.id).active)
            self.assertIsNotNone(db.get(ServiceCredential, cred.id).revoked_at)

    def test_connect_service_for_the_script(self):
        from database.tables import InstitutionLocalHost, ServiceCredential
        from platform_core import registry
        from services import platform_ops
        with session_local() as db:
            inst = registry.provision_institution(db, {'ru': 'Автоподключение'}, 'ru').id
            db.commit()
            first = platform_ops.connect_service(db, inst, 'schedule', 'schedule-x.1-2-3-4.sslip.io', 9445)
            db.commit()
            env = first['env']
            self.assertEqual(env['SERVICE_API_BASE_URL'], 'https://schedule-x.1-2-3-4.sslip.io:9445/api/v1')
            self.assertEqual(env['CORE_URL'], 'https://core.test')
            service = db.get(ServiceInstance, first['service_id'])
            self.assertEqual((service.service_type, service.enabled), ('schedule', False))
            self.assertIsNotNone(db.get(InstitutionLocalHost, (inst, 'schedule-x.1-2-3-4.sslip.io')))
            # Ключ рабочий: ядро меняет его на machine token этого сервиса.
            token = self.c.post('/api/v1/internal/auth/token', auth=(env['SERVICE_CLIENT_ID'], env['SERVICE_CLIENT_SECRET']),
                                json={'grant_type': 'client_credentials'}).json()
            self.assertEqual((token['service_id'], token['institution_id']), (service.id, inst))
            # Меню ещё нет — не включается; после публикации — включается.
            self.assertFalse(platform_ops.enable_service(db, inst, 'schedule'))
            service.manifest = {'titles': {'ru': 'Расписание'}, 'menus': [
                {'id': 'schedule', 'titles': {'ru': 'Расписание'}, 'entrypoint_path': '/', 'profiles': ['student'],
                 'required_permissions': [], 'order': 0}]}
            self.assertTrue(platform_ops.enable_service(db, inst, 'schedule'))
            db.commit()
            # Повторный запуск: тот же сервис, старый ключ отозван.
            again = platform_ops.connect_service(db, inst, 'schedule', 'schedule-x.1-2-3-4.sslip.io', 9445)
            db.commit()
            self.assertEqual(again['service_id'], service.id)
            old = db.query(ServiceCredential).filter_by(client_id=env['SERVICE_CLIENT_ID']).one()
            self.assertIsNotNone(old.revoked_at)
            self.assertTrue(db.get(ServiceInstance, service.id).enabled)  # адреса те же — не выключается
            # Администрирование: адреса защищённого экземпляра и ключ.
            admin = platform_ops.connect_service(db, inst, 'administration', 'admin-x.1-2-3-4.sslip.io', 9444)
            db.commit()
            self.assertEqual(db.get(ServiceInstance, admin['service_id']).client_base_url, 'https://admin-x.1-2-3-4.sslip.io:9444')
            self.assertTrue(platform_ops.enable_service(db, inst, 'administration'))

    def test_intermediate_custom_types_become_contract_types(self):
        from platform_core import registry
        with session_local() as db:
            inst = registry.provision_institution(db, {'ru': 'Промежуточный'}, 'ru')
            ids = {}
            for old in ('custom.schedule', 'custom.people', 'custom.coursework'):
                ids[old] = registry.create_local_instance(db, inst.id, old, 'https://x.university.ru/api/v1',
                                                          'https://x.university.ru', {'ru': old}, ['admin']).id
            db.commit()
            registry.ensure_platform_invariants(db)
            db.commit()
            got = {old: db.get(ServiceInstance, sid).service_type for old, sid in ids.items()}
        self.assertEqual(got, {'custom.schedule': 'schedule', 'custom.people': 'user-profile', 'custom.coursework': 'coursework'})

if __name__ == '__main__':
    unittest.main()
