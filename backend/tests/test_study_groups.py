"""Учебные группы в ядре: ведёт администратор вуза, читают участники и сервисы.

    cd backend && python -m unittest tests.test_study_groups -v
"""
import io
import json
import os
import tempfile
import unittest
import uuid

_tmp = tempfile.TemporaryDirectory()
os.environ.update(
    DATABASE_URL=f'sqlite:///{_tmp.name}/core.db', JWT_ISSUER='https://core.test',
    JWT_KEYRING_PATH=f'{_tmp.name}/keys.json', CURSOR_SECRET_KEY='groups-secret-' * 4, MAX_BOT_TOKEN='',
    ALLOW_DEV_LOGIN='true', SEED_DEMO_DATA='false', ALLOW_FAKE_REDIS='true', RATE_LIMIT_LOGIN='0', RATE_LIMIT_REFRESH='0', RATE_LIMIT_MACHINE_EXCHANGE='0', RATE_LIMIT_USER='0', RATE_LIMIT_CREDENTIAL='0', REDIS_PORT='1',
    CLOUD_BINDING_KEY='b' * 48, ADMINISTRATION_PROVISIONING_TOKEN='p' * 48, SCHEDULE_PROVISIONING_TOKEN='s' * 48,
    SERVICE_CONFIG_DIR='',
)

from fastapi.testclient import TestClient  # noqa: E402

from tests.keys import issue_key, register_service  # noqa: E402

import manage  # noqa: E402
from database.create_tables import session_local  # noqa: E402
from database.tables import Membership, PlatformStaff, RoleAssignment, ServiceInstance  # noqa: E402
from platform_core import registry  # noqa: E402
from main import app  # noqa: E402


class StudyGroups(unittest.TestCase):
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

    def private(self, inst, admin_service, core_headers):
        actor = self.c.post(f'/api/v1/institution/{inst}/service/{admin_service}/session', json={'profile': 'admin'},
                            headers=core_headers).json()['access_token']
        binding = issue_key(admin_service)
        machine = self.c.post('/api/v1/internal/auth/token', auth=(binding['client_id'], binding['client_secret']),
                              json={'grant_type': 'client_credentials'}).json()['access_token']
        return {'Authorization': 'Bearer ' + machine, 'X-Actor-Token': actor}

    def test_groups_lifecycle(self):
        owner_id, owner = self.login('owner')
        support_id, support = self.login('support')
        helper_id, helper = self.login('helper')
        s1, s1_core = self.login('s1')
        s2, _ = self.login('s2')
        t1, t1_core = self.login('t1')
        with session_local() as db:
            db.add(PlatformStaff(user_id=support_id, role='platform_support', granted_by='test'))
            db.commit()
        app_id = self.c.post('/api/v1/institution-applications', headers=owner,
                             json={'titles': {'ru': 'Вуз'}, 'contact': 'r@example.ru'}).json()['id']
        inst = self.c.post(f'/api/v1/platform/applications/{app_id}/approve', headers=support, json={}).json()['institution_id']
        with session_local() as db:
            admin_service = db.query(ServiceInstance).filter_by(institution_id=inst, service_type='administration').one().id
            for uid, profiles in ((s1, ['student']), (s2, ['student']), (t1, ['teacher']), (helper_id, ['admin'])):
                db.add(Membership(institution_id=inst, user_id=uid, profiles=profiles))
            db.commit()
        p = self.private(inst, admin_service, owner)
        base = f'/api/v1/institution/{inst}/internal/groups'

        # Администратор без groups.manage группы не создаёт; владелец — да.
        with session_local() as db:
            db.add(RoleAssignment(service_id=admin_service, user_id=helper_id, profile='admin', roles=['technical_admin']))
            db.commit()
        tech = self.private(inst, admin_service, helper)
        self.assertEqual(self.c.post(base, headers=tech, json={'name': 'ИВТ-21'}).status_code, 403)
        created = self.c.post(base, headers=p, json={'name': 'ИВТ-21'})
        self.assertEqual(created.status_code, 201, created.text)
        group = created.json()['id']
        other = self.c.post(base, headers=p, json={'name': 'ИВТ-22'}).json()['id']
        self.assertEqual(self.c.post(base, headers=p, json={'name': ' ивт-21 '}).json()['error']['code'], 'GROUP_ALREADY_EXISTS')

        # Состав: только студенты вуза, один студент — одна группа, версия отдельная.
        tag = self.c.get(f'{base}/{group}/members', headers=p).headers['ETag']
        bad = self.c.put(f'{base}/{group}/members', headers={**p, 'If-Match': tag}, json={'user_ids': [t1]})
        self.assertEqual(bad.json()['error']['code'], 'INVALID_REFERENCE')
        ok = self.c.put(f'{base}/{group}/members', headers={**p, 'If-Match': tag}, json={'user_ids': [s1]})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(self.c.put(f'{base}/{group}/members', headers={**p, 'If-Match': tag},
                                    json={'user_ids': []}).status_code, 412)
        other_tag = self.c.get(f'{base}/{other}/members', headers=p).headers['ETag']
        clash = self.c.put(f'{base}/{other}/members', headers={**p, 'If-Match': other_tag}, json={'user_ids': [s1, s2]})
        self.assertEqual(clash.json()['error']['code'], 'STUDENT_ALREADY_GROUPED')

        # Приложение: студенту — только своя группа с составом (CORE_API_SPEC.md §7.1).
        mine = self.c.get(f'/api/v1/institution/{inst}/groups', params={'profile': 'student'}, headers=s1_core).json()
        self.assertEqual({g['name']: g['user_ids'] for g in mine['items']}, {'ИВТ-21': [s1]})
        self.assertEqual(mine['my_group_ids'], [group])
        self.assertFalse(mine['can_manage'])
        self.assertNotIn('students', mine)
        self.assertEqual(self.c.get(f'/api/v1/institution/{inst}/profiles', headers=s1_core).json()['groups'],
                         [{'id': group, 'name': 'ИВТ-21'}])
        teacher_view = self.c.get(f'/api/v1/institution/{inst}/groups', params={'profile': 'teacher'}, headers=t1_core).json()
        self.assertEqual({g['name']: g['user_ids'] for g in teacher_view['items']}, {'ИВТ-21': [s1], 'ИВТ-22': []})
        self.assertEqual(self.c.get(f'/api/v1/institution/{inst}/groups', params={'profile': 'teacher'},
                                    headers=s1_core).status_code, 403)

        # Сервис (свой сервис вуза custom.schedule): machine API и group_ids в introspection.
        schedule = register_service(inst, 'schedule', 'schedule.university.ru', manifest={'titles': {'ru': 'Расписание'}, 'menus': [{'id': 'schedule', 'titles': {'ru': 'Расписание'}, 'entrypoint_path': '/', 'profiles': ['student', 'teacher', 'admin'], 'required_permissions': [], 'order': 0}]})
        binding = issue_key(schedule)
        machine = self.c.post('/api/v1/internal/auth/token', auth=(binding['client_id'], binding['client_secret']),
                              json={'grant_type': 'client_credentials'}).json()
        self.assertIn('groups:read', machine['scopes'])
        m = {'Authorization': 'Bearer ' + machine['access_token']}
        listed = self.c.get(f'/api/v1/internal/service/{schedule}/groups', headers=m).json()['items']
        self.assertEqual([g['name'] for g in listed], ['ИВТ-21', 'ИВТ-22'])
        self.assertEqual(self.c.get(f'/api/v1/internal/service/{schedule}/groups/{group}/members', headers=m).json()['user_ids'], [s1])
        self.assertEqual(self.c.get(f'/api/v1/internal/service/{schedule}/users/{s1}/profiles', headers=m).json()['groups'], [group])
        session = self.c.post(f'/api/v1/institution/{inst}/service/{schedule}/session', json={'profile': 'student'},
                              headers=s1_core).json()['access_token']
        info = self.c.post('/api/v1/internal/auth/introspect', headers=m, json={'token': session}).json()
        self.assertEqual((info['active'], info['group_ids']), (True, [group]))

        # Снятие профиля student убирает из группы; непустую группу удалить нельзя, пустую — можно.
        gtag = self.c.get(f'{base}/{group}', headers=p).headers['ETag']
        self.assertEqual(self.c.delete(f'{base}/{group}', headers={**p, 'If-Match': gtag}).status_code, 409)
        mtag = self.c.get(f'/api/v1/institution/{inst}/internal/members/{s1}', headers=p).headers['ETag']
        self.assertEqual(self.c.put(f'/api/v1/institution/{inst}/internal/members/{s1}/profiles',
                                    headers={**p, 'If-Match': mtag}, json={'profiles': ['teacher']}).status_code, 200)
        self.assertEqual(self.c.get(f'{base}/{group}/members', headers=p).json()['user_ids'], [])
        self.assertEqual(self.c.delete(f'{base}/{group}', headers={**p, 'If-Match': gtag}).status_code, 204)

        # Назначение группы одним действием: перевод атомарный, снятие — group_id=null.
        members = f'/api/v1/institution/{inst}/internal/members'
        self.assertEqual(self.c.put(f'{members}/{s2}/group', headers=tech, json={'group_id': other}).status_code, 403)
        moved_now = self.c.put(f'{members}/{s2}/group', headers=p, json={'group_id': other})
        self.assertEqual(moved_now.json()['group'], {'id': other, 'name': 'ИВТ-22'})
        listed = {m['user_id']: m['group_id'] for m in self.c.get(members, headers=p).json()['items']}
        self.assertEqual((listed[s2], listed[t1]), (other, None))
        self.assertEqual(self.c.put(f'{members}/{t1}/group', headers=p, json={'group_id': other}).json()['error']['code'],
                         'INVALID_REFERENCE')
        self.assertIsNone(self.c.put(f'{members}/{s2}/group', headers=p, json={'group_id': None}).json()['group'])
        # Новый студент сразу в группе.
        s3, _ = self.login('s3')
        added = self.c.post(members, headers=p, json={'user_id': s3, 'profiles': ['student'], 'group_id': other})
        self.assertEqual(added.status_code, 201, added.text)
        self.assertEqual(self.c.get(f'{base}/{other}/members', headers=p).json()['user_ids'], [s3])

        # Перенос из расписания сохраняет UUID и идемпотентен.
        moved = str(uuid.uuid4())
        dump = json.dumps({'groups': [{'id': moved, 'institution_id': inst, 'name': 'Перенесённая', 'user_ids': [s2]}]})
        manage.import_groups(io.StringIO(dump))
        manage.import_groups(io.StringIO(dump))
        self.assertEqual(self.c.get(f'{base}/{moved}/members', headers=p).json()['user_ids'], [s2])


    def test_admins_manage_groups_others_view(self):
        owner_id, owner = self.login('owner')
        support_id, support = self.login('support')
        admin, admin_core = self.login('admin')
        plain, plain_core = self.login('plain')
        t1, t1_core = self.login('t1')
        s1, s1_core = self.login('s1')
        with session_local() as db:
            db.add(PlatformStaff(user_id=support_id, role='platform_support', granted_by='test'))
            db.commit()
        app_id = self.c.post('/api/v1/institution-applications', headers=owner,
                             json={'titles': {'ru': 'Вуз групп'}, 'contact': 'r@example.ru'}).json()['id']
        inst = self.c.post(f'/api/v1/platform/applications/{app_id}/approve', headers=support, json={}).json()['institution_id']
        with session_local() as db:
            for uid, profiles in ((s1, ['student']), (t1, ['teacher']), (admin, ['admin']), (plain, ['admin'])):
                db.add(Membership(institution_id=inst, user_id=uid, profiles=profiles))
            admin_service = registry.admin_service_of(db, inst).id
            db.add(RoleAssignment(service_id=admin_service, user_id=admin, profile='admin', roles=['membership_admin']))
            db.commit()
        base = f'/api/v1/institution/{inst}/groups'
        q = {'profile': 'admin'}

        # Группы ведёт администратор с groups.manage (membership_admin); профиль admin без роли только смотрит.
        view = self.c.get(base, params=q, headers=plain_core).json()
        self.assertFalse(view['can_manage'])
        self.assertEqual(self.c.post(base, params=q, headers=plain_core, json={'name': 'X'}).status_code, 403)
        view = self.c.get(base, params=q, headers=admin_core).json()
        self.assertTrue(view['can_manage'])
        self.assertEqual(view['students'], [s1])
        created = self.c.post(base, params=q, headers=admin_core, json={'name': 'ПИ-1'})
        self.assertEqual(created.status_code, 201, created.text)
        group = created.json()['id']
        item = next(g for g in self.c.get(base, params=q, headers=admin_core).json()['items'] if g['id'] == group)
        members = self.c.put(f'{base}/{group}/members', params=q, headers={**admin_core, 'If-Match': item['members_etag']},
                             json={'user_ids': [s1]})
        self.assertEqual(members.status_code, 200, members.text)
        renamed = self.c.patch(f'{base}/{group}', params=q, headers={**admin_core, 'If-Match': item['etag']},
                               json={'name': 'ПИ-11'})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(self.c.delete(f'{base}/{group}', params=q, headers={**admin_core, 'If-Match': renamed.headers['ETag']})
                         .json()['error']['code'], 'GROUP_IN_USE')

        # Студент и преподаватель видят группу и состав, но не правят.
        for uid_core, profile in ((s1_core, 'student'), (t1_core, 'teacher')):
            view = self.c.get(base, params={'profile': profile}, headers=uid_core).json()
            self.assertEqual(view['items'], [{'id': group, 'name': 'ПИ-11', 'user_ids': [s1]}])
            self.assertFalse(view['can_manage'])
            self.assertEqual(self.c.post(base, params={'profile': profile}, headers=uid_core, json={'name': 'X'}).status_code, 403)


if __name__ == '__main__':
    unittest.main()
