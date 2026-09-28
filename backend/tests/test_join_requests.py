"""Заявки на вступление: пользователь выбирает вузы и группы, администратор одобряет или отклоняет.

    cd backend && python -m unittest tests.test_join_requests -v
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
    JWT_KEYRING_PATH=f'{_tmp.name}/keys.json', CURSOR_SECRET_KEY='join-secret-' * 4, MAX_BOT_TOKEN='',
    ALLOW_DEV_LOGIN='true', SEED_DEMO_DATA='false', ALLOW_FAKE_REDIS='true', REDIS_PORT='1',
    CLOUD_BINDING_KEY='b' * 48, ADMINISTRATION_PROVISIONING_TOKEN='p' * 48, SCHEDULE_PROVISIONING_TOKEN='s' * 48,
    SERVICE_CONFIG_DIR='',
)

from fastapi.testclient import TestClient  # noqa: E402

import manage  # noqa: E402
from database.create_tables import session_local  # noqa: E402
from database.tables import Membership, PlatformStaff, ServiceInstance, StudyGroupMember  # noqa: E402
from main import app  # noqa: E402


class JoinRequests(unittest.TestCase):
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
        binding = self.c.get(f'/api/v1/internal/provisioning/bindings/{admin_service}',
                             headers={'Authorization': 'Bearer ' + 'p' * 48}).json()
        machine = self.c.post('/api/v1/internal/auth/token', auth=(binding['client_id'], binding['client_secret']),
                              json={'grant_type': 'client_credentials'}).json()['access_token']
        return {'Authorization': 'Bearer ' + machine, 'X-Actor-Token': actor}

    def institution(self, owner, support, title):
        app_id = self.c.post('/api/v1/institution-applications', headers=owner,
                             json={'titles': {'ru': title}, 'contact': 'r@example.ru'}).json()['id']
        inst = self.c.post(f'/api/v1/platform/applications/{app_id}/approve', headers=support, json={}).json()['institution_id']
        with session_local() as db:
            admin_service = db.query(ServiceInstance).filter_by(institution_id=inst, service_type='administration').one().id
        return inst, admin_service

    def test_join_flow(self):
        owner_id, owner = self.login('owner')
        support_id, support = self.login('support')
        user_id, user = self.login('newbie')
        with session_local() as db:
            db.add(PlatformStaff(user_id=support_id, role='platform_support', granted_by='test'))
            db.commit()
        a, a_admin = self.institution(owner, support, 'Альфа')
        b, b_admin = self.institution(owner, support, 'Бета')
        pa, pb = self.private(a, a_admin, owner), self.private(b, b_admin, owner)
        group = self.c.post(f'/api/v1/institution/{a}/internal/groups', headers=pa, json={'name': 'ИВТ-21'}).json()['id']
        foreign = self.c.post(f'/api/v1/institution/{b}/internal/groups', headers=pb, json={'name': 'Б-1'}).json()['id']

        options = self.c.get('/api/v1/join/institutions', headers=user).json()['items']
        by_id = {o['id']: o for o in options}
        self.assertEqual(by_id[a]['groups'], [{'id': group, 'name': 'ИВТ-21'}])

        url = '/api/v1/join-requests'
        # Группа студента — только существующая группа этого вуза.
        for bad in ({'institution_id': a, 'profile': 'student'},
                    {'institution_id': a, 'profile': 'student', 'group_id': foreign},
                    {'institution_id': a, 'profile': 'teacher', 'group_id': group},
                    {'institution_id': a, 'profile': 'admin'}):
            r = self.c.post(url, headers=user, json={'full_name': 'Иван', 'items': [bad]})
            self.assertEqual(r.status_code, 422, (bad, r.text))
        r = self.c.post(url, headers=user, json={'full_name': '', 'items': [{'institution_id': b, 'profile': 'teacher'}]})
        self.assertEqual(r.status_code, 422)

        # Несколько вузов одной заявкой.
        r = self.c.post(url, headers=user, json={'full_name': '  Иван   Петров ', 'items': [
            {'institution_id': a, 'profile': 'student', 'group_id': group},
            {'institution_id': b, 'profile': 'teacher'}]})
        self.assertEqual(r.status_code, 201, r.text)
        created = {i['institution_id']: i for i in r.json()['items']}
        self.assertEqual(created[a]['full_name'], 'Иван Петров')
        self.assertEqual(created[a]['group_name'], 'ИВТ-21')
        again = self.c.post(url, headers=user, json={'full_name': 'Иван', 'items': [{'institution_id': b, 'profile': 'teacher'}]})
        self.assertEqual(again.json()['error']['code'], 'JOIN_REQUEST_PENDING')
        self.assertEqual(self.c.get('/api/v1/institution', headers=user).json()['items'], [])

        # Администратор видит карточку с введёнными данными; чужой вуз — нет.
        cards = self.c.get(f'/api/v1/institution/{a}/internal/join-requests?status=pending', headers=pa).json()['items']
        self.assertEqual([(c['full_name'], c['profile'], c['group_name']) for c in cards], [('Иван Петров', 'student', 'ИВТ-21')])
        self.assertEqual(self.c.post(f'/api/v1/institution/{a}/internal/join-requests/{created[b]["id"]}/approve',
                                     headers=pa).status_code, 404)

        ok = self.c.post(f'/api/v1/institution/{a}/internal/join-requests/{created[a]["id"]}/approve', headers=pa)
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(self.c.post(f'/api/v1/institution/{a}/internal/join-requests/{created[a]["id"]}/approve',
                                     headers=pa).json()['error']['code'], 'INVALID_STATE')
        no = self.c.post(f'/api/v1/institution/{b}/internal/join-requests/{created[b]["id"]}/reject', headers=pb,
                         json={'reason': 'Не работает у нас'})
        self.assertEqual(no.json()['status'], 'rejected')

        mine = self.c.get('/api/v1/institution', headers=user).json()['items']
        self.assertEqual([(i['id'], i['profiles']) for i in mine], [(a, ['student'])])
        with session_local() as db:
            self.assertEqual(db.get(StudyGroupMember, (a, user_id)).group_id, group)
        members = self.c.get(f'/api/v1/institution/{a}/internal/members', headers=pa).json()['items']
        self.assertEqual({m['user_id']: m['full_name'] for m in members}[user_id], 'Иван Петров')
        statuses = {r['institution_id']: (r['status'], r['decision_reason'])
                    for r in self.c.get(url, headers=user).json()['items']}
        self.assertEqual(statuses, {a: ('approved', None), b: ('rejected', 'Не работает у нас')})

        # Второй профиль в том же вузе — новой заявкой; отзыв своей заявки.
        r = self.c.post(url, headers=user, json={'full_name': 'Иван Петров', 'items': [{'institution_id': a, 'profile': 'teacher'}]})
        self.assertEqual(r.status_code, 201, r.text)
        rid = r.json()['items'][0]['id']
        self.assertEqual(self.c.post(f'{url}/{rid}/withdraw', headers=user).json()['status'], 'withdrawn')
        dup = self.c.post(url, headers=user, json={'full_name': 'И', 'items': [{'institution_id': a, 'profile': 'student', 'group_id': group}]})
        self.assertEqual(dup.json()['error']['code'], 'MEMBERSHIP_ALREADY_EXISTS')


if __name__ == '__main__':
    unittest.main()
