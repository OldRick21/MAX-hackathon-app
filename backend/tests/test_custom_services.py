"""Свой сервис вуза (custom.<код>) по CORE_API_SPEC.md §7: регистрация, ключ, самостоятельная
публикация роли и меню, включение, появление в каталоге пользователя.

    cd backend && python -m unittest tests.test_custom_services -v
"""
import os
import tempfile
import unittest
import uuid
from urllib.parse import urlsplit

_tmp = tempfile.TemporaryDirectory()
os.environ.update(
    DATABASE_URL=f'sqlite:///{_tmp.name}/core.db', JWT_ISSUER='https://core.test',
    JWT_KEYRING_PATH=f'{_tmp.name}/keys.json', CURSOR_SECRET_KEY='custom-secret-' * 4, MAX_BOT_TOKEN='',
    ALLOW_DEV_LOGIN='true', SEED_DEMO_DATA='false', ALLOW_FAKE_REDIS='true', RATE_LIMIT_LOGIN='0', RATE_LIMIT_REFRESH='0', RATE_LIMIT_MACHINE_EXCHANGE='0', RATE_LIMIT_USER='0', RATE_LIMIT_CREDENTIAL='0', REDIS_PORT='1',
    CLOUD_BINDING_KEY='b' * 48, ADMINISTRATION_PROVISIONING_TOKEN='p' * 48, SERVICE_CONFIG_DIR='',
    SCHEDULE_PROVISIONING_TOKEN='s' * 48, USER_PROFILE_PROVISIONING_TOKEN='u' * 48,
)

from testing_consent import TestClient  # noqa: E402

from tests.keys import issue_key, register_service  # noqa: E402

from database.create_tables import session_local  # noqa: E402
from database.tables import AuditEvent, InstitutionLocalHost, Membership, PlatformStaff, RoleAssignment, ServiceInstance, ServiceRole  # noqa: E402
from main import app  # noqa: E402

MENU = {'id': 'books', 'titles': {'ru': 'Книги'}, 'entrypoint_path': '/books', 'profiles': ['student'],
        'required_permissions': [], 'order': 0}


def bare_institution(db, titles):
    """Вуз, как в базе до автоустановки облачных сервисов: только администрирование."""
    from platform_core import registry
    inst = registry.provision_institution(db, titles, 'ru')
    for service in db.query(ServiceInstance).filter(ServiceInstance.institution_id == inst.id,
                                                    ServiceInstance.service_type.in_(['schedule', 'user-profile'])):
        db.delete(service)
    db.flush()
    return inst


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

        # CSP оболочки разрешает iframe только зарегистрированных включённых сервисов.
        csp = self.c.get('/api/v1/internal/shell-csp').headers['Content-Security-Policy']
        frame_src = next(d for d in csp.split('; ') if d.startswith('frame-src'))
        self.assertIn(urlsplit(service['client_base_url']).netloc, frame_src)
        self.assertNotIn('*', frame_src)

        # Меню фильтруются для всех профилей одинаково: у admin нет своего меню — сервиса нет в списке,
        # но headless-сессия по-прежнему доступна (CORE_API_SPEC.md §6 п.4–5).
        admin_items = self.c.get(f'/api/v1/institution/{inst}/service', params={'profile': 'admin'}, headers=owner).json()['items']
        self.assertNotIn(service['id'], [x['id'] for x in admin_items])
        opened = self.c.post(f'/api/v1/institution/{inst}/service/{service["id"]}/session', json={'profile': 'admin'}, headers=owner)
        self.assertEqual(opened.status_code, 201, opened.text)


    def test_startup_gives_admins_every_service(self):
        from platform_core import registry
        with session_local() as db:
            inst = bare_institution(db, {'ru': 'Старый вуз'})
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
            inst = bare_institution(db, {'ru': 'Вуз с облаком'})
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
            # Тот же UUID, облачное размещение; ключ binding перевыпущен, прежний отозван.
            self.assertEqual((migrated.service_type, migrated.deployment), ('schedule', 'cloud'))
            self.assertTrue(db.get(CloudBinding, legacy.id).active)
            self.assertNotEqual(db.get(CloudBinding, legacy.id).credential_id, cred.id)
            self.assertIsNotNone(db.get(ServiceCredential, cred.id).revoked_at)

    def test_cloud_install_and_runner_provisioning(self):
        from platform_core import registry
        from services import platform_ops
        with session_local() as db:
            inst = bare_institution(db, {'ru': 'Облако'}).id
            db.commit()
            service, created = platform_ops.install_cloud_service(db, inst, 'schedule')
            again, created_again = platform_ops.install_cloud_service(db, inst, 'schedule')
            db.commit()
            sid = service.id
            self.assertEqual((created, created_again, again.id), (True, False, sid))
            self.assertEqual((service.deployment, service.enabled), ('cloud', True))
        # Раннер типа видит свои экземпляры с ключами; чужой токен — нет.
        listed = self.c.get('/api/v1/internal/provisioning/instances', headers={'Authorization': 'Bearer ' + 's' * 48})
        self.assertEqual(listed.status_code, 200, listed.text)
        mine = next(i for i in listed.json()['items'] if i['service_id'] == sid)
        self.assertEqual(mine['api_base_url'], f'https://shell.platform.example/schedule/{sid}/api/v1')
        token = self.c.post('/api/v1/internal/auth/token', auth=(mine['client_id'], mine['client_secret']),
                            json={'grant_type': 'client_credentials'}).json()
        self.assertEqual((token['service_id'], token['institution_id']), (sid, inst))
        admin_list = self.c.get('/api/v1/internal/provisioning/instances', headers={'Authorization': 'Bearer ' + 'p' * 48}).json()
        self.assertNotIn(sid, [i['service_id'] for i in admin_list['items']])
        self.assertTrue(all(i['service_type'] == 'administration' for i in admin_list['items']))
        self.assertEqual(self.c.get('/api/v1/internal/provisioning/instances', headers={'Authorization': 'Bearer nope'}).status_code, 404)

    def test_local_hosting_moves_to_cloud(self):
        from database.tables import CloudBinding, ServiceCredential
        from platform_core import registry
        with session_local() as db:
            inst = bare_institution(db, {'ru': 'Был контейнер'}).id
            old = registry.create_local_instance(db, inst, 'user-profile', 'https://people.university.ru/api/v1',
                                                 'https://people.university.ru', None, ['admin', 'student'])
            cred, _ = registry.issue_credential(db, old)
            old_id, cred_id = old.id, cred.id
            db.commit()
            registry.ensure_platform_invariants(db)
            db.commit()
            moved = db.get(ServiceInstance, old_id)
            self.assertEqual((moved.deployment, moved.api_base_url), ('cloud', f'https://shell.platform.example/people/{old_id}/api/v1'))
            self.assertIsNotNone(db.get(ServiceCredential, cred_id).revoked_at)  # ключ контейнера вуза отозван
            self.assertTrue(db.get(CloudBinding, old_id).active)
    def test_intermediate_custom_types_become_contract_types(self):
        from platform_core import registry
        with session_local() as db:
            inst = bare_institution(db, {'ru': 'Промежуточный'})
            ids = {}
            for old in ('custom.schedule', 'custom.people', 'custom.coursework'):
                ids[old] = registry.create_local_instance(db, inst.id, old, 'https://x.university.ru/api/v1',
                                                          'https://x.university.ru', {'ru': old}, ['admin']).id
            db.commit()
            registry.ensure_platform_invariants(db)
            db.commit()
            got = {old: db.get(ServiceInstance, sid).service_type for old, sid in ids.items()}
        self.assertEqual(got, {'custom.schedule': 'schedule', 'custom.people': 'user-profile', 'custom.coursework': 'coursework'})

    def test_service_narrowing_role_profiles_drops_assignments(self):
        from database.tables import RoleAssignment, ServiceRole
        from platform_core import registry
        user, _ = self.login('teacher')
        with session_local() as db:
            inst = bare_institution(db, {'ru': 'Сужение ролей'}).id
            svc = registry.create_local_instance(db, inst, 'schedule', 'https://s.university.ru/api/v1', 'https://s.university.ru',
                                                 None, ['admin', 'teacher', 'student'])
            svc_id = svc.id
            db.add(ServiceRole(service_id=svc_id, code='schedule_editor', titles={'ru': 'Р'}, allowed_profiles=['admin', 'teacher'],
                               permissions=['schedule.write'], system=False))
            db.add(Membership(institution_id=inst, user_id=user, profiles=['teacher']))
            db.add(RoleAssignment(service_id=svc_id, user_id=user, profile='teacher', roles=['schedule_editor']))
            key, secret = registry.issue_credential(db, db.get(ServiceInstance, svc_id))
            client_id = key.client_id
            db.commit()
        token = self.c.post('/api/v1/internal/auth/token', auth=(client_id, secret), json={'grant_type': 'client_credentials'}).json()
        m = {'Authorization': 'Bearer ' + token['access_token']}
        role_url = f'/api/v1/internal/service/{svc_id}/roles/schedule_editor'
        assign_url = f'/api/v1/internal/service/{svc_id}/users/{user}/profiles/teacher/roles'
        # Без If-Match — 428; сужение профилей при назначении в исключаемом профиле — 409 ROLE_IN_USE.
        self.assertEqual(self.c.patch(role_url, headers=m, json={'allowed_profiles': ['admin']}).status_code, 428)
        tag = self.c.get(role_url, headers=m).headers['ETag']
        r = self.c.patch(role_url, headers={**m, 'If-Match': tag}, json={'allowed_profiles': ['admin']})
        self.assertEqual((r.status_code, r.json()['error']['code']), (409, 'ROLE_IN_USE'))
        r = self.c.delete(role_url, headers={**m, 'If-Match': tag})
        self.assertEqual((r.status_code, r.json()['error']['code']), (409, 'ROLE_IN_USE'))
        # Сервис снимает назначение (If-Match набора), затем сужает роль и удаляет её.
        current = self.c.get(assign_url, headers=m)
        cleared = self.c.put(assign_url, headers={**m, 'If-Match': current.headers['ETag']}, json={'roles': []})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(self.c.put(assign_url, headers={**m, 'If-Match': current.headers['ETag']}, json={'roles': []}).status_code, 412)
        r = self.c.patch(role_url, headers={**m, 'If-Match': tag}, json={'allowed_profiles': ['admin']})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.delete(role_url, headers={**m, 'If-Match': r.headers['ETag']}).status_code, 204)
        with session_local() as db:
            actions = [e.action for e in db.query(AuditEvent).filter(AuditEvent.institution_id == inst)]
            self.assertTrue({'assignments.replace', 'role.update', 'role.delete'} <= set(actions), actions)
            self.assertTrue(all(e.machine_credential_id for e in db.query(AuditEvent).filter(
                AuditEvent.institution_id == inst, AuditEvent.actor_kind == 'service')))

    def test_widgets_service_and_admin_layers(self):
        """Виджеты: объявление в manifest, включение сервисом и администратором, права, список оболочки."""
        from platform_core import registry
        owner_id, owner = self.login('w_owner')
        student_id, student = self.login('w_student')
        teacher_id, teacher = self.login('w_teacher')
        with session_local() as db:
            inst = bare_institution(db, {'ru': 'Вуз виджетов'}).id
            registry.assign_owner(db, inst, owner_id)
            db.add(Membership(institution_id=inst, user_id=student_id, profiles=['student']))
            db.add(Membership(institution_id=inst, user_id=teacher_id, profiles=['teacher', 'admin']))
            svc = registry.create_local_instance(db, inst, 'schedule', 'https://s.university.ru/api/v1', 'https://s.university.ru',
                                                 None, ['admin', 'teacher', 'student'])
            svc.enabled = True
            sid = svc.id
            key, secret = registry.issue_credential(db, svc)
            client_id = key.client_id
            db.commit()
        token = self.c.post('/api/v1/internal/auth/token', auth=(client_id, secret), json={'grant_type': 'client_credentials'}).json()
        m = {'Authorization': 'Bearer ' + token['access_token']}
        base = f'/api/v1/internal/service/{sid}'
        menu = lambda i, profiles, perms: {'id': i, 'titles': {'ru': i}, 'entrypoint_path': '/schedule', 'profiles': profiles,
                                           'required_permissions': perms, 'order': 0}
        widget = lambda i, profiles, perms, open_menu: {
            'id': i, 'titles': {'ru': 'Сегодня'}, 'kind': 'events', 'size': 'wide', 'profiles': profiles,
            'required_permissions': perms, 'data_path': f'/schedule/widgets/{i}', 'open_menu': open_menu, 'order': 0}
        manifest = {'titles': {'ru': 'Расписание'},
                    'menus': [menu('schedule', ['student', 'teacher'], []), menu('schedule_admin', ['admin'], ['schedule.read_all'])],
                    'widgets': [widget('today', ['student', 'teacher'], [], 'schedule'),
                                widget('today_admin', ['admin'], ['schedule.read_all'], 'schedule_admin')]}
        tag = self.c.get(f'{base}/manifest', headers=m).headers['ETag']
        bad = {**manifest, 'widgets': [widget('x', ['admin'], [], 'schedule')]}  # профиль вне меню open_menu
        self.assertEqual(self.c.put(f'{base}/manifest', headers={**m, 'If-Match': tag}, json=bad).status_code, 422)
        r = self.c.put(f'{base}/manifest', headers={**m, 'If-Match': tag}, json=manifest)
        self.assertEqual(r.status_code, 200, r.text)

        def home(h, profile):
            r = self.c.get(f'/api/v1/institution/{inst}/widgets', params={'profile': profile}, headers=h)
            self.assertEqual(r.status_code, 200, r.text)
            return [w['widget_id'] for w in r.json()['items']]
        self.assertEqual(home(student, 'student'), ['today'])
        self.assertEqual(home(teacher, 'teacher'), ['today'])
        self.assertEqual(home(teacher, 'admin'), [])  # нет schedule.read_all
        item = self.c.get(f'/api/v1/institution/{inst}/widgets', params={'profile': 'student'}, headers=student).json()['items'][0]
        self.assertEqual((item['data_url'], item['open_menu'], item['kind']),
                         ('https://s.university.ru/api/v1/schedule/widgets/today', 'schedule', 'events'))

        # Уровень сервиса: выключить у преподавателей.
        listed = self.c.get(f'{base}/widgets', headers=m)
        self.assertEqual(listed.json()['items'][0]['visibility'], {'student': True, 'teacher': True})
        vis = f'{base}/widgets/today/visibility'
        self.assertEqual(self.c.put(vis, headers=m, json={'visibility': {'student': True, 'teacher': False}}).status_code, 428)
        self.assertEqual(self.c.put(vis, headers={**m, 'If-Match': listed.headers['ETag']},
                                    json={'visibility': {'student': True}}).status_code, 422)
        r = self.c.put(vis, headers={**m, 'If-Match': listed.headers['ETag']}, json={'visibility': {'student': True, 'teacher': False}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(home(teacher, 'teacher'), [])
        self.assertEqual(home(student, 'student'), ['today'])
        # Повторная публикация manifest выключение не сбрасывает.
        tag = self.c.get(f'{base}/manifest', headers=m).headers['ETag']
        self.assertEqual(self.c.put(f'{base}/manifest', headers={**m, 'If-Match': tag}, json=manifest).status_code, 200)
        self.assertEqual(home(teacher, 'teacher'), [])

        # Уровень администратора вуза (private API): выключить у студентов.
        from services import institution_admin
        from auth.authorization import ActorContext
        with session_local() as db:
            admin_sid = registry.admin_service_of(db, inst).id
            ctx = ActorContext(institution_id=inst, admin_service_id=admin_sid, actor_id=owner_id, roles=['owner'],
                               permissions=['services.read', 'services.manage'], credential_id=None, request_id=None)
            listed = institution_admin.list_widgets(db, ctx, sid)
            today = listed.body['items'][0]
            self.assertEqual((today['service_visibility'], today['visibility']),
                             ({'student': True, 'teacher': False}, {'student': True, 'teacher': True}))
            institution_admin.set_widget_visibility(db, ctx, sid, 'today', {'visibility': {'student': False, 'teacher': True}},
                                                    listed.etag)
        self.assertEqual(home(student, 'student'), [])
        self.assertEqual(home(teacher, 'teacher'), [])  # выключен сервисом — администратор не включит в обход

        # Роль с schedule.read_all открывает виджет администратора.
        with session_local() as db:
            db.add(ServiceRole(service_id=sid, code='editor', titles={'ru': 'Р'}, allowed_profiles=['admin'],
                               permissions=['schedule.read_all'], system=False))
            db.add(RoleAssignment(service_id=sid, user_id=teacher_id, profile='admin', roles=['editor']))
            db.commit()
        self.assertEqual(home(teacher, 'admin'), ['today_admin'])
        # Выключенный сервис — виджетов нет.
        with session_local() as db:
            db.get(ServiceInstance, sid).enabled = False
            db.commit()
        self.assertEqual(home(teacher, 'admin'), [])

    def test_rate_limits_and_auth_before_body(self):
        from platform_core import ratelimit
        from settings.config import settings
        # Тело не разбирается до авторизации: без токена — 401, с токеном и битым JSON — 400.
        _, headers = self.login('limits')
        bad = {'Content-Type': 'application/json'}
        url = '/api/v1/institution/00000000-0000-4000-8000-000000000001/service/00000000-0000-4000-8000-000000000002/session'
        self.assertEqual(self.c.post(url, content=b'{bad', headers=bad).status_code, 401)
        self.assertEqual(self.c.post(url, content=b'{bad', headers={**bad, **headers}).status_code, 400)
        self.assertEqual(self.c.post('/api/v1/internal/auth/token', content=b'{bad', headers=bad).status_code, 401)

        saved = (settings.RATE_LIMIT_LOGIN, settings.RATE_LIMIT_USER)
        settings.RATE_LIMIT_LOGIN, settings.RATE_LIMIT_USER = 2, 3
        ratelimit.reset()
        try:
            # Вход: лимит на IP, ответ одинаковый для любых учётных данных, с Retry-After.
            codes = [self.c.post('/api/v1/auth/token', json={'username': f'rl{i}'}, headers={'X-Real-IP': '203.0.113.9'})
                     for i in range(3)]
            self.assertEqual([r.status_code for r in codes], [200, 200, 429])
            self.assertEqual(codes[2].json()['error']['code'], 'RATE_LIMITED')
            self.assertTrue(1 <= int(codes[2].headers['Retry-After']) <= settings.RATE_LIMIT_WINDOW_SECONDS)
            other_ip = self.c.post('/api/v1/auth/token', json={'username': 'rl9'}, headers={'X-Real-IP': '203.0.113.10'})
            self.assertEqual(other_ip.status_code, 200)
            # Прочие запросы: лимит на проверенного пользователя.
            me = [self.c.get('/api/v1/auth/me', headers=headers).status_code for _ in range(4)]
            self.assertEqual(me, [200, 200, 200, 429])
        finally:
            settings.RATE_LIMIT_LOGIN, settings.RATE_LIMIT_USER = saved
            ratelimit.reset()


if __name__ == '__main__':
    unittest.main()
