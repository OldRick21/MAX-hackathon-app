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

from tests.keys import issue_key, register_service  # noqa: E402

import manage  # noqa: E402
from database.create_tables import session_local  # noqa: E402
from database.tables import JoinRequest, Membership, PlatformStaff, ServiceInstance, StudyGroupMember  # noqa: E402
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
        binding = issue_key(admin_service)
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

        # Имя из регистрации доступно сервису «Люди» через machine API ядра.
        from services.service_registry import ServiceRegistry
        with session_local() as db:
            claims = {'service_id': a_admin, 'institution_id': a}
            listed = ServiceRegistry.list_service_members(a_admin, claims, db)['items']
            self.assertEqual({m['user_id']: m['display_name'] for m in listed}[user_id], 'Иван Петров')
            self.assertEqual(ServiceRegistry.get_service_user_profiles(a_admin, user_id, claims, db)['display_name'],
                             'Иван Петров')

        # Аватар: свой загружает сам, видят участники общих вузов, посторонние — нет.
        png = b'\x89PNG\r\n\x1a\n' + b'0' * 64
        self.assertEqual(self.c.put('/api/v1/users/me/avatar', headers={**user, 'Content-Type': 'image/png'},
                                    content=b'not an image').status_code, 422)
        self.assertEqual(self.c.put('/api/v1/users/me/avatar', headers={**user, 'Content-Type': 'image/gif'},
                                    content=png).status_code, 422)
        self.assertEqual(self.c.put('/api/v1/users/me/avatar', headers={**user, 'Content-Type': 'image/png'},
                                    content=b'\x89PNG\r\n\x1a\n' + b'0' * 600_000).status_code, 413)
        saved = self.c.put('/api/v1/users/me/avatar', headers={**user, 'Content-Type': 'image/png'}, content=png)
        self.assertEqual(saved.status_code, 200, saved.text)
        mine = self.c.get('/api/v1/users/me/avatar', headers=user)
        self.assertEqual((mine.status_code, mine.content, mine.headers['content-type']), (200, png, 'image/png'))
        self.assertEqual(self.c.get('/api/v1/users/me/avatar', headers={**user, 'If-None-Match': mine.headers['ETag']}).status_code, 304)
        self.assertEqual(self.c.get(f'/api/v1/users/{user_id}/avatar', headers=owner).status_code, 200)  # владелец в том же вузе
        stranger_id, stranger = self.login('stranger')
        self.assertEqual(self.c.get(f'/api/v1/users/{user_id}/avatar', headers=stranger).status_code, 404)
        # Удаление из вуза стирает заявки в этот вуз: при повторном добавлении имя не вернётся,
        # а новая дата членства заставит «Людей» завести новую анкету.
        with session_local() as db:
            since = ServiceRegistry.get_service_user_profiles(a_admin, user_id, claims, db)['member_since']
        tag = self.c.get(f'/api/v1/institution/{a}/internal/members/{user_id}', headers=pa).headers['ETag']
        self.assertEqual(self.c.delete(f'/api/v1/institution/{a}/internal/members/{user_id}',
                                       headers={**pa, 'If-Match': tag}).status_code, 204)
        self.assertEqual(self.c.post(f'/api/v1/institution/{a}/internal/members', headers=pa,
                                     json={'user_id': user_id, 'profiles': ['student']}).status_code, 201)
        with session_local() as db:
            again = ServiceRegistry.get_service_user_profiles(a_admin, user_id, claims, db)
            self.assertNotEqual(again['member_since'], since)
            # Остались только заявки в другие вузы: имя берётся из них, а не из удалённой.
            self.assertEqual(db.query(JoinRequest).filter_by(institution_id=a, user_id=user_id).count(), 0)

        self.assertEqual(self.c.delete('/api/v1/users/me/avatar', headers=user).status_code, 204)
        self.assertEqual(self.c.get('/api/v1/users/me/avatar', headers=user).status_code, 404)


if __name__ == '__main__':
    unittest.main()
