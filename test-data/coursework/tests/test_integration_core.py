"""Подключение курсовых (тип coursework, контейнер вуза) против живого ядра.

Регистрация → ключ → сервис сам создаёт роль и публикует меню → включение → загрузка работы.

    cd test-data/coursework && PYTHONPATH=../../backend python -m unittest tests.test_integration_core -v
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
os.environ.update(
    DATABASE_URL=f'sqlite:///{_tmp.name}/core.db', JWT_ISSUER='https://core.test',
    JWT_KEYRING_PATH=f'{_tmp.name}/keys.json', CURSOR_SECRET_KEY='coursework-secret-' * 4, MAX_BOT_TOKEN='',
    ALLOW_DEV_LOGIN='true', SEED_DEMO_DATA='false', ALLOW_FAKE_REDIS='true', REDIS_PORT='1',
    SERVICE_CONFIG_DIR='',
    SERVICE_DATA=f'{_tmp.name}/coursework', CORE_URL='http://core.test', SERVICE_CLIENT_ID='pending',
    SERVICE_CLIENT_SECRET='pending', SERVICE_API_BASE_URL='https://coursework.university.ru/api/v1',
    SERVICE_CLIENT_BASE_URL='https://coursework.university.ru', SHELL_ORIGIN='https://shell.test',
)

from testing_consent import TestClient  # noqa: E402

from database.create_tables import session_local  # noqa: E402
from database.tables import InstitutionLocalHost, Membership, PlatformStaff, ServiceInstance  # noqa: E402
from main import app as core_app  # noqa: E402
from platform_core import registry  # noqa: E402

from app import main as coursework, sdk  # noqa: E402

PDF = b'%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\nstartxref\n9\n%%EOF\n'


class CoreTransport:
    def __init__(self, client):
        self.client = client

    def request(self, method, url, **kwargs):
        kwargs.pop('timeout', None)
        kwargs.pop('allow_redirects', None)
        return self.client.request(method, url.replace('http://core.test', ''), **kwargs)


class CourseworkAgainstCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core_ctx = TestClient(core_app)
        cls.core = cls.core_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.core_ctx.__exit__(None, None, None)
        from database.create_tables import engine
        engine.dispose()
        _tmp.cleanup()

    def login(self, name):
        pair = self.core.post('/api/v1/auth/token', json={'username': f'{name}_{uuid.uuid4().hex[:6]}'}).json()
        headers = {'Authorization': 'Bearer ' + pair['access_token']}
        return self.core.get('/api/v1/auth/me', headers=headers).json()['id'], headers

    def service_session(self, inst_id, service_id, core_headers, profile):
        created = self.core.post(f'/api/v1/institution/{inst_id}/service/{service_id}/session',
                                 json={'profile': profile}, headers=core_headers)
        self.assertEqual(created.status_code, 201, created.text)
        return {'Authorization': 'Bearer ' + created.json()['access_token']}

    def test_admin_connects_local_service(self):
        owner_id, owner_core = self.login('owner')
        support_id, support_core = self.login('support')
        teacher_id, teacher_core = self.login('teacher')
        student_id, student_core = self.login('student')
        with session_local() as db:
            db.add(PlatformStaff(user_id=support_id, role='platform_support', granted_by='test'))
            db.commit()
        app_id = self.core.post('/api/v1/institution-applications', headers=owner_core,
                                json={'titles': {'ru': 'Университет «Курсовые»'}, 'contact': 'r@example.ru'}).json()['id']
        inst_id = self.core.post(f'/api/v1/platform/applications/{app_id}/approve',
                                 headers=support_core, json={}).json()['institution_id']
        with session_local() as db:
            # Поддержка платформы одобряет хост вуза (в интерфейсе — «Одобренные хосты»).
            db.add(InstitutionLocalHost(institution_id=inst_id, hostname='coursework.university.ru', approved_by=support_id))
            db.add(Membership(institution_id=inst_id, user_id=teacher_id, profiles=['teacher']))
            db.add(Membership(institution_id=inst_id, user_id=student_id, profiles=['student']))
            admin_service_id = db.query(ServiceInstance).filter_by(institution_id=inst_id,
                                                                   service_type='administration').one().id
            db.commit()

        # Администратор через фасад administration: регистрация и ключ.
        admin_core = self.service_session(inst_id, admin_service_id, owner_core, 'admin')
        # Ключ контейнера администрирования вуза выдаёт оператор.
        with session_local() as db:
            credential, secret = registry.issue_credential(db, db.get(ServiceInstance, admin_service_id))
            binding = {'client_id': credential.client_id, 'client_secret': secret}
            db.commit()
        machine = self.core.post('/api/v1/internal/auth/token', auth=(binding['client_id'], binding['client_secret']),
                                 json={'grant_type': 'client_credentials'}).json()
        private = {'Authorization': 'Bearer ' + machine['access_token'],
                   'X-Actor-Token': admin_core['Authorization'].removeprefix('Bearer ')}
        base = f'/api/v1/institution/{inst_id}/internal'
        created = self.core.post(f'{base}/services', headers={**private, 'Idempotency-Key': str(uuid.uuid4())},
                                 json={'service_type': 'coursework', 'deployment': 'local',
                                       'api_base_url': 'https://coursework.university.ru/api/v1',
                                       'client_base_url': 'https://coursework.university.ru'})
        self.assertEqual(created.status_code, 201, created.text)
        service_id = created.json()['id']
        self.assertFalse(created.json()['enabled'])
        key = self.core.post(f'{base}/services/{service_id}/credentials', headers=private)
        self.assertEqual(key.status_code, 201, key.text)

        # Вуз вписывает ключ в секреты сервиса; сервис сам создаёт роль и публикует меню.
        settings = sdk.Settings('http://core.test', 'https://shell.test', key.json()['credential']['client_id'],
                                key.json()['client_secret'], 'https://coursework.university.ru/api/v1',
                                'https://coursework.university.ru')
        # Маршруты уже связаны с клиентом ядра при импорте — перенастраиваем его, а не подменяем.
        coursework.core.s = settings
        coursework.core.http = CoreTransport(self.core)
        coursework.core._token = coursework.core._binding = None
        sdk.onboard(coursework.core, coursework.MANIFEST, coursework.ROLES, coursework.state)
        self.assertEqual(coursework.state['onboarding'], 'ready')
        with session_local() as db:
            service = db.get(ServiceInstance, service_id)
            self.assertEqual([m['id'] for m in service.manifest['menus']], ['coursework', 'coursework_admin'])
        roles = self.core.get(f'{base}/services/{service_id}/roles', headers=private).json()['items']
        self.assertEqual([r['code'] for r in roles], ['coursework_manager'])
        sdk.onboard(coursework.core, coursework.MANIFEST, coursework.ROLES, coursework.state)  # без дублей
        blind = coursework.core.machine('PUT', f'/api/v1/internal/service/{service_id}/manifest', json=coursework.MANIFEST)
        self.assertEqual(blind.status_code, 428)  # CORE_API_SPEC §7: замена manifest только с If-Match

        # Пока сервис выключен, пользователи в него не попадают.
        self.assertEqual(self.core.post(f'/api/v1/institution/{inst_id}/service/{service_id}/session',
                                        json={'profile': 'student'}, headers=student_core).status_code, 409)
        tag = self.core.get(f'{base}/services/{service_id}', headers=private).headers['ETag']
        enabled = self.core.patch(f'{base}/services/{service_id}', headers={**private, 'If-Match': tag},
                                  json={'enabled': True})
        self.assertEqual(enabled.status_code, 200, enabled.text)

        with TestClient(coursework.app) as svc:
            student_h = self.service_session(inst_id, service_id, student_core, 'student')
            teacher_h = self.service_session(inst_id, service_id, teacher_core, 'teacher')
            view = svc.get('/api/v1/service', headers=student_h)
            self.assertEqual((view.status_code, [m['id'] for m in view.json()['menus']]), (200, ['coursework']))
            bad = svc.post('/api/v1/coursework/submissions', headers={**student_h, 'Idempotency-Key': str(uuid.uuid4())},
                           data={'title': 'Работа', 'teacher_id': student_id},
                           files={'file': ('w.pdf', PDF, 'application/pdf')})
            self.assertEqual(bad.json()['error']['code'], 'INVALID_REFERENCE')
            work = svc.post('/api/v1/coursework/submissions', headers={**student_h, 'Idempotency-Key': str(uuid.uuid4())},
                            data={'title': 'Работа', 'teacher_id': teacher_id},
                            files={'file': ('w.pdf', PDF, 'application/pdf')})
            self.assertEqual(work.status_code, 201, work.text)
            listed = svc.get('/api/v1/coursework/submissions', headers=teacher_h).json()['items']
            self.assertEqual([x['id'] for x in listed], [work.json()['id']])


if __name__ == '__main__':
    unittest.main()
