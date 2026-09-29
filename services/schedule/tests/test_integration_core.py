"""Сквозной прогон расписания против живого ядра: реальные JWT, machine-обмен, introspection, профили.

    cd services/schedule && PYTHONPATH=../../backend python -m unittest tests.test_integration_core -v
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
HOST = 'schedule.university.ru'
API, CLIENT = f'https://{HOST}/api/v1', f'https://{HOST}'
os.environ.update(
    DATABASE_URL=f'sqlite:///{_tmp.name}/core.db', JWT_ISSUER='https://core.test',
    JWT_KEYRING_PATH=f'{_tmp.name}/keys.json', CURSOR_SECRET_KEY='schedule-secret-' * 4, MAX_BOT_TOKEN='',
    ALLOW_DEV_LOGIN='true', SEED_DEMO_DATA='false', ALLOW_FAKE_REDIS='true', REDIS_PORT='1', SERVICE_CONFIG_DIR='',
    # .env сервиса: строки «Выдать ключ»; настоящий ключ подставляется в тесте после регистрации.
    CORE_URL='http://core.test', SHELL_ORIGIN='https://shell.test', SERVICE_CLIENT_ID='pending',
    SERVICE_CLIENT_SECRET='pending', SERVICE_API_BASE_URL=API, SERVICE_CLIENT_BASE_URL=CLIENT,
    SCHEDULE_DB=f'{_tmp.name}/schedule.db',
)

from testing_consent import TestClient  # noqa: E402

from database.create_tables import session_local  # noqa: E402
from database.base import utc_now  # noqa: E402
from database.tables import InstitutionLocalHost, Membership, PlatformStaff, RoleAssignment, ServiceInstance, ServiceRole  # noqa: E402
from platform_core import registry  # noqa: E402
from main import app as core_app  # noqa: E402

from app import main as schedule  # noqa: E402
from app import onboarding  # noqa: E402
from app.core_client import CoreClient  # noqa: E402


class CoreTransport:
    """requests-совместимая сессия поверх TestClient ядра."""

    def __init__(self, client):
        self.client = client

    def request(self, method, url, **kwargs):
        kwargs.pop('timeout', None)
        kwargs.pop('allow_redirects', None)
        return self.client.request(method, url.replace('http://core.test', ''), **kwargs)


class ScheduleAgainstCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core_ctx = TestClient(core_app)
        cls.core = cls.core_ctx.__enter__()
        cls.transport = CoreTransport(cls.core)
        # Публикацию меню и ролей тест запускает сам, когда у сервиса появится ключ.
        cls.start = onboarding.start
        onboarding.start = lambda *args, **kwargs: None
        cls.svc_ctx = TestClient(schedule.app)
        cls.svc = cls.svc_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        onboarding.start = cls.start
        cls.svc_ctx.__exit__(None, None, None)
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

    def admin_headers(self, inst_id, admin_service_id, core_headers):
        actor = self.service_session(inst_id, admin_service_id, core_headers, 'admin')
        binding = self.issue_key(admin_service_id)
        machine = self.core.post('/api/v1/internal/auth/token', auth=(binding['client_id'], binding['client_secret']),
                                 json={'grant_type': 'client_credentials'}).json()
        return ({'Authorization': 'Bearer ' + machine['access_token']},
                {'X-Actor-Token': actor['Authorization'].removeprefix('Bearer ')})

    def issue_key(self, service_id):
        with session_local() as db:
            credential, secret = registry.issue_credential(db, db.get(ServiceInstance, service_id))
            db.commit()
            return {'client_id': credential.client_id, 'client_secret': secret}

    def test_end_to_end(self):
        owner_id, owner_core = self.login('owner')
        support_id, support_core = self.login('support')
        teacher_id, teacher_core = self.login('teacher')
        student_id, student_core = self.login('student')

        with session_local() as db:
            db.add(PlatformStaff(user_id=support_id, role='platform_support', granted_by='test'))
            db.commit()
        app_id = self.core.post('/api/v1/institution-applications', headers=owner_core,
                                json={'titles': {'ru': 'Университет «Расписание»'}, 'contact': 'rector@example.ru'}).json()['id']
        inst_id = self.core.post(f'/api/v1/platform/applications/{app_id}/approve',
                                 headers=support_core, json={}).json()['institution_id']

        # Расписание — свой сервис вуза: одобренный хост, регистрация, ключ; меню и роль публикует сам сервис.
        with session_local() as db:
            db.add(InstitutionLocalHost(institution_id=inst_id, hostname=HOST, approved_by=support_id))
            # Облачный экземпляр, созданный вместе с вузом, удаляется: тест подключает сервис локально.
            auto = db.query(ServiceInstance).filter_by(institution_id=inst_id, service_type='schedule', deleted_at=None).one()
            auto.deleted_at, auto.enabled = utc_now(), False
            db.flush()
            schedule_id = registry.create_local_instance(db, inst_id, 'schedule', API, CLIENT, {'ru': 'Расписание'},
                                                         ['student', 'teacher', 'admin']).id
            admin_service_id = db.query(ServiceInstance).filter_by(institution_id=inst_id,
                                                                   service_type='administration').one().id
            db.commit()
        key = self.issue_key(schedule_id)
        schedule.core = CoreClient('http://core.test', key['client_id'], key['client_secret'], API, CLIENT,
                                   session=self.transport)
        onboarding.sync(schedule.core, schedule.MANIFEST, schedule.ROLES, schedule.RETIRED_ROLES, schedule.RENAMED_ROLES)
        with session_local() as db:
            service = db.get(ServiceInstance, schedule_id)
            # Меню и роль — по контракту (SPEC §1).
            self.assertEqual([m['id'] for m in service.manifest['menus']], ['schedule', 'schedule_admin'])
            # Одна роль «Редактор расписания» для администраторов и преподавателей.
            self.assertEqual({r.code: (r.allowed_profiles, r.permissions) for r in service.roles},
                             {'schedule_editor': (['admin', 'teacher'], ['schedule.read_all', 'schedule.write'])})
            service.enabled = True
            db.add(Membership(institution_id=inst_id, user_id=teacher_id, profiles=['teacher']))
            db.add(Membership(institution_id=inst_id, user_id=student_id, profiles=['student']))
            # База прежней версии: отдельная роль teacher_editor, назначенная преподавателю.
            db.add(ServiceRole(service_id=schedule_id, code='teacher_editor', titles={'ru': 'Редактирование расписания'},
                               allowed_profiles=['teacher'], permissions=['schedule.read_all', 'schedule.write'], system=False))
            db.add(RoleAssignment(service_id=schedule_id, user_id=teacher_id, profile='teacher', roles=['teacher_editor']))
            db.commit()
        # Повторный запуск сервиса переносит назначение на schedule_editor и удаляет старую роль.
        onboarding.sync(schedule.core, schedule.MANIFEST, schedule.ROLES, schedule.RETIRED_ROLES, schedule.RENAMED_ROLES)
        with session_local() as db:
            self.assertEqual({r.code for r in db.query(ServiceRole).filter_by(service_id=schedule_id)}, {'schedule_editor'})
            self.assertEqual(db.get(RoleAssignment, (schedule_id, teacher_id, 'teacher')).roles, ['schedule_editor'])
            db.get(RoleAssignment, (schedule_id, teacher_id, 'teacher')).roles = []
            db.commit()

        teacher_h = self.service_session(inst_id, schedule_id, teacher_core, 'teacher')
        student_h = self.service_session(inst_id, schedule_id, student_core, 'student')
        admin_h = self.service_session(inst_id, schedule_id, owner_core, 'admin')

        view = self.svc.get('/api/v1/service', headers=teacher_h)
        self.assertEqual(view.status_code, 200, view.text)
        self.assertEqual((view.json()['service_type'], view.json()['api_base_url']), ('schedule', API))
        self.assertEqual([m['id'] for m in view.json()['menus']], ['schedule'])

        # Admin без роли расписания не видит (контракт); роль даёт schedule_admin и права.
        self.assertEqual(self.svc.get('/api/v1/schedule/groups', headers=admin_h).status_code, 403)
        machine, actor = self.admin_headers(inst_id, admin_service_id, owner_core)
        private = {**machine, **actor}
        roles_path = f'/api/v1/institution/{inst_id}/internal/services/{schedule_id}/users/{owner_id}/profiles/admin/roles'
        etag = self.core.get(roles_path, headers=private).headers['ETag']
        granted = self.core.put(roles_path, headers={**private, 'If-Match': etag}, json={'roles': ['schedule_editor']})
        self.assertEqual(granted.status_code, 200, granted.text)
        teacher_roles = f'/api/v1/institution/{inst_id}/internal/services/{schedule_id}/users/{teacher_id}/profiles/teacher/roles'
        tag = self.core.get(teacher_roles, headers=private).headers['ETag']
        refused = self.core.put(teacher_roles, headers={**private, 'If-Match': tag}, json={'roles': ['teacher_editor']})
        self.assertEqual(refused.status_code, 422, refused.text)  # прежней роли больше нет

        # Группы ведёт ядро (отличие от контракта); расписание их только читает.
        base = f'/api/v1/institution/{inst_id}/internal/groups'
        group = self.core.post(base, headers=private, json={'name': 'ИВТ-21'})
        self.assertEqual(group.status_code, 201, group.text)
        group_id = group.json()['id']
        tag = self.core.get(f'{base}/{group_id}/members', headers=private).headers['ETag']
        self.assertEqual(self.core.put(f'{base}/{group_id}/members', headers={**private, 'If-Match': tag},
                                       json={'user_ids': [student_id]}).status_code, 200)
        self.assertEqual(self.svc.get('/api/v1/schedule/groups', headers=student_h).json()['items'],
                         [{'id': group_id, 'name': 'ИВТ-21'}])

        # Занятия пишет admin с schedule.write; преподаватель — нет.
        event = {'title': 'Базы данных', 'starts_at': '2026-09-28T06:00:00Z', 'ends_at': '2026-09-28T07:30:00Z',
                 'group_ids': [group_id], 'teacher_ids': [teacher_id], 'location': '301', 'description': '',
                 'status': 'scheduled'}
        denied = self.svc.post('/api/v1/schedule/events', headers={**teacher_h, 'Idempotency-Key': str(uuid.uuid4())},
                               json=event)
        self.assertEqual(denied.status_code, 403, denied.text)
        # Администратор включает преподавателю правку расписания — и выключает обратно.
        tag = self.core.get(teacher_roles, headers=private).headers['ETag']
        on = self.core.put(teacher_roles, headers={**private, 'If-Match': tag}, json={'roles': ['schedule_editor']})
        self.assertEqual(on.status_code, 200, on.text)
        by_teacher = self.svc.post('/api/v1/schedule/events', headers={**teacher_h, 'Idempotency-Key': str(uuid.uuid4())},
                                   json={**event, 'starts_at': '2026-10-01T06:00:00Z', 'ends_at': '2026-10-01T07:00:00Z'})
        self.assertEqual(by_teacher.status_code, 201, by_teacher.text)
        self.assertEqual(self.svc.delete(f'/api/v1/schedule/events/{by_teacher.json()["id"]}',
                                         headers={**teacher_h, 'If-Match': by_teacher.headers['etag']}).status_code, 204)
        off = self.core.put(teacher_roles, headers={**private, 'If-Match': on.headers['ETag']}, json={'roles': []})
        self.assertEqual(off.status_code, 200, off.text)
        self.assertEqual(self.svc.post('/api/v1/schedule/events', headers={**teacher_h, 'Idempotency-Key': str(uuid.uuid4())},
                                       json=event).status_code, 403)
        editor_h = admin_h
        created = self.svc.post('/api/v1/schedule/events', headers={**editor_h, 'Idempotency-Key': str(uuid.uuid4())},
                                json=event)
        self.assertEqual(created.status_code, 201, created.text)
        week = {'from': '2026-09-28T00:00:00Z', 'to': '2026-10-05T00:00:00Z'}
        for who in (student_h, teacher_h):
            seen = self.svc.get('/api/v1/schedule/events', headers=who, params=week).json()['items']
            self.assertEqual([e['id'] for e in seen], [created.json()['id']])

        # Студент в преподаватели не годится — проверка идёт через ядро.
        bad = self.svc.post('/api/v1/schedule/events', headers={**editor_h, 'Idempotency-Key': str(uuid.uuid4())},
                            json={**event, 'teacher_ids': [teacher_id, student_id]})
        self.assertEqual(bad.json()['error']['code'], 'INVALID_REFERENCE')

        # Снятое членство студента гасит его сессию.
        with session_local() as db:
            db.delete(db.get(Membership, (inst_id, student_id)))
            db.commit()
        self.assertEqual(self.svc.get('/api/v1/schedule/events', headers=student_h, params=week).status_code, 401)


if __name__ == '__main__':
    unittest.main()
