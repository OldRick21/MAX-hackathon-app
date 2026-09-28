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
PROV = 's' * 48
os.environ.update(
    DATABASE_URL=f'sqlite:///{_tmp.name}/core.db', JWT_ISSUER='https://core.test',
    JWT_KEYRING_PATH=f'{_tmp.name}/keys.json', CURSOR_SECRET_KEY='schedule-secret-' * 4, MAX_BOT_TOKEN='',
    ALLOW_DEV_LOGIN='true', SEED_DEMO_DATA='false', ALLOW_FAKE_REDIS='true', REDIS_PORT='1',
    CLOUD_BINDING_KEY='b' * 48, ADMINISTRATION_PROVISIONING_TOKEN='p' * 48,
    SCHEDULE_PROVISIONING_TOKEN=PROV, SERVICE_CONFIG_DIR='',
    SCHEDULE_API_BASE_URL='https://shell.test/schedule/api/v1', SCHEDULE_CLIENT_BASE_URL='https://shell.test',
    SCHEDULE_DB=f'{_tmp.name}/schedule.db',
)

from fastapi.testclient import TestClient  # noqa: E402

import manage  # noqa: E402
from database.create_tables import session_local  # noqa: E402
from database.tables import Membership, PlatformStaff, ServiceInstance  # noqa: E402
from main import app as core_app  # noqa: E402

from app import main as schedule  # noqa: E402
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
        schedule.core = CoreClient('http://core.test', PROV, session=CoreTransport(cls.core))
        cls.svc_ctx = TestClient(schedule.app)
        cls.svc = cls.svc_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
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
        binding = self.core.get(f'/api/v1/internal/provisioning/bindings/{admin_service_id}',
                                headers={'Authorization': 'Bearer ' + 'p' * 48}).json()
        machine = self.core.post('/api/v1/internal/auth/token', auth=(binding['client_id'], binding['client_secret']),
                                 json={'grant_type': 'client_credentials'}).json()
        return ({'Authorization': 'Bearer ' + machine['access_token']},
                {'X-Actor-Token': actor['Authorization'].removeprefix('Bearer ')})

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

        manage.install_schedule(inst_id)
        with session_local() as db:
            schedule_id = db.query(ServiceInstance).filter_by(institution_id=inst_id, service_type='schedule').one().id
            admin_service_id = db.query(ServiceInstance).filter_by(institution_id=inst_id,
                                                                   service_type='administration').one().id
            db.add(Membership(institution_id=inst_id, user_id=teacher_id, profiles=['teacher']))
            db.add(Membership(institution_id=inst_id, user_id=student_id, profiles=['student']))
            db.commit()

        admin_h = self.service_session(inst_id, schedule_id, owner_core, 'admin')
        teacher_h = self.service_session(inst_id, schedule_id, teacher_core, 'teacher')
        student_h = self.service_session(inst_id, schedule_id, student_core, 'student')

        view = self.svc.get('/api/v1/service', headers=teacher_h)
        self.assertEqual(view.status_code, 200, view.text)
        self.assertEqual(view.json()['api_base_url'], 'https://shell.test/schedule/api/v1')
        self.assertEqual([m['id'] for m in view.json()['menus']], ['schedule'])

        def catalog():
            items = self.core.get(f'/api/v1/institution/{inst_id}/service', params={'profile': 'admin'},
                                  headers=owner_core).json()['items']
            return next(x for x in items if x['id'] == schedule_id)
        # Расписание видно администратору без ролей; ролей для admin в расписании нет вовсе.
        self.assertEqual(([m['id'] for m in catalog()['menus']], catalog()['permissions']), (['schedule'], []))
        roles_path = f'/api/v1/institution/{inst_id}/internal/services/{schedule_id}/users/{owner_id}/profiles/admin/roles'
        machine, actor = self.admin_headers(inst_id, admin_service_id, owner_core)
        private = {**machine, **actor}
        etag = self.core.get(roles_path, headers=private).headers['ETag']
        refused = self.core.put(roles_path, headers={**private, 'If-Match': etag}, json={'roles': ['schedule_editor']})
        self.assertIn(refused.status_code, (409, 422), refused.text)

        # Группы ведёт администрирование ядра; расписание их только читает.
        base = f'/api/v1/institution/{inst_id}/internal/groups'
        group = self.core.post(base, headers=private, json={'name': 'ИВТ-21'})
        self.assertEqual(group.status_code, 201, group.text)
        group_id = group.json()['id']
        tag = self.core.get(f'{base}/{group_id}/members', headers=private).headers['ETag']
        self.assertEqual(self.core.put(f'{base}/{group_id}/members', headers={**private, 'If-Match': tag},
                                       json={'user_ids': [student_id]}).status_code, 200)
        self.assertEqual(self.svc.get('/api/v1/schedule/groups', headers=student_h).json()['items'],
                         [{'id': group_id, 'name': 'ИВТ-21'}])

        # Преподаватель только смотрит: занятия не ставит, и роль редактора ему не назначить.
        event = {'title': 'Базы данных', 'starts_at': '2026-09-28T06:00:00Z', 'ends_at': '2026-09-28T07:30:00Z',
                 'group_ids': [group_id], 'teacher_ids': [teacher_id], 'location': '301', 'description': '',
                 'status': 'scheduled'}
        denied = self.svc.post('/api/v1/schedule/events', headers={**teacher_h, 'Idempotency-Key': str(uuid.uuid4())},
                               json=event)
        self.assertEqual(denied.status_code, 403, denied.text)
        teacher_roles = f'/api/v1/institution/{inst_id}/internal/services/{schedule_id}/users/{teacher_id}/profiles/teacher/roles'
        tag = self.core.get(teacher_roles, headers=private).headers['ETag']
        # Администратор включает преподавателю редактирование — тот ставит занятие.
        granted = self.core.put(teacher_roles, headers={**private, 'If-Match': tag}, json={'roles': ['schedule_editor']})
        self.assertEqual(granted.status_code, 200, granted.text)
        editor_h = teacher_h
        by_admin = self.svc.post('/api/v1/schedule/events', headers={**self.service_session(inst_id, schedule_id, owner_core, 'admin'),
                                                                   'Idempotency-Key': str(uuid.uuid4())},
                                 json={**event, 'starts_at': '2026-10-01T06:00:00Z', 'ends_at': '2026-10-01T07:00:00Z'})
        self.assertEqual(by_admin.status_code, 201, by_admin.text)
        created = self.svc.post('/api/v1/schedule/events', headers={**editor_h, 'Idempotency-Key': str(uuid.uuid4())},
                                json=event)
        self.assertEqual(created.status_code, 201, created.text)
        week = {'from': '2026-09-28T00:00:00Z', 'to': '2026-10-05T00:00:00Z'}
        seen = self.svc.get('/api/v1/schedule/events', headers=student_h, params=week).json()['items']
        self.assertEqual([e['id'] for e in seen], [created.json()['id'], by_admin.json()['id']])

        # Студент в преподаватели не годится — проверка идёт через ядро.
        bad = self.svc.post('/api/v1/schedule/events', headers={**editor_h, 'Idempotency-Key': str(uuid.uuid4())},
                            json={**event, 'teacher_ids': [teacher_id, student_id]})
        self.assertEqual(bad.json()['error']['code'], 'INVALID_REFERENCE')

        # Снятое членство студента гасит его сессию.
        with session_local() as db:
            db.delete(db.get(Membership, (inst_id, student_id)))
            db.commit()
        self.assertEqual(self.svc.get('/api/v1/schedule/events', headers=student_h, params=week).status_code, 401)

    def test_startup_turns_demo_stub_into_schedule(self):
        # Прежняя заглушка (адрес 8443, меню home только для студентов) становится настоящим расписанием.
        from platform_core import registry
        with session_local() as db:
            inst = registry.provision_institution(db, {'ru': 'Демо'}, 'ru')
            db.add(ServiceInstance(id=str(uuid.uuid4()), institution_id=inst.id, service_type='schedule',
                                   deployment='cloud', enabled=True, protected=False,
                                   client_base_url='https://195.133.197.144:8443',
                                   api_base_url='https://195.133.197.144:8443/api/v1', supported_profiles=['student'],
                                   manifest={'titles': {'ru': 'Расписание — демо'}, 'menus': [
                                       {'id': 'home', 'titles': {'ru': 'Расписание'}, 'entrypoint_path': '/',
                                        'profiles': ['student'], 'required_permissions': [], 'order': 0}]}))
            db.commit()
            inst_id = inst.id
        with session_local() as db:
            registry.ensure_platform_invariants(db)
            db.commit()
        with session_local() as db:
            service = db.query(ServiceInstance).filter_by(institution_id=inst_id, service_type='schedule').one()
            self.assertEqual(service.api_base_url, 'https://shell.test/schedule/api/v1')
            self.assertEqual(sorted(service.supported_profiles), ['admin', 'student', 'teacher'])
            self.assertEqual([m['id'] for m in service.manifest['menus']], ['schedule'])
            binding = self.core.get(f'/api/v1/internal/provisioning/bindings/{service.id}',
                                    headers={'Authorization': 'Bearer ' + PROV})
            self.assertEqual(binding.status_code, 200, binding.text)


if __name__ == '__main__':
    unittest.main()
