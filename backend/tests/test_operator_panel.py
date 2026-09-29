"""Операторская веб-панель: вход, защита, операции платформы и управление вузом.

    cd backend && python -m unittest tests.test_operator_panel -v
"""
import os
import tempfile
import unittest
import uuid

_tmp = tempfile.TemporaryDirectory()
os.environ.update(
    DATABASE_URL=f'sqlite:///{_tmp.name}/core.db', JWT_ISSUER='https://core.test',
    JWT_KEYRING_PATH=f'{_tmp.name}/keys.json', CURSOR_SECRET_KEY='operator-secret-' * 4, MAX_BOT_TOKEN='',
    ALLOW_DEV_LOGIN='true', SEED_DEMO_DATA='false', ALLOW_FAKE_REDIS='true', RATE_LIMIT_LOGIN='0', RATE_LIMIT_REFRESH='0', RATE_LIMIT_MACHINE_EXCHANGE='0', RATE_LIMIT_USER='0', RATE_LIMIT_CREDENTIAL='0', REDIS_PORT='1',
    CLOUD_BINDING_KEY='b' * 48, ADMINISTRATION_PROVISIONING_TOKEN='p' * 48, SCHEDULE_PROVISIONING_TOKEN='s' * 48,
    SERVICE_CONFIG_DIR='', OPERATOR_PASSWORD='correct horse battery', OPERATOR_COOKIE_SECURE='false',
    OPERATOR_HEALTH_TARGETS='{}',
)

from testing_consent import TestClient  # noqa: E402

from database.create_tables import session_local  # noqa: E402
from database.tables import AuditEvent, PlatformStaff  # noqa: E402
from main import app as core_app  # noqa: E402
from operator_panel.app import app as operator_app, _failures  # noqa: E402

W = {'X-Operator': '1'}


class OperatorPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core_ctx = TestClient(core_app)
        cls.core = cls.core_ctx.__enter__()  # создаёт таблицы
        cls.op = TestClient(operator_app)

    @classmethod
    def tearDownClass(cls):
        cls.core_ctx.__exit__(None, None, None)
        from database.create_tables import engine
        engine.dispose()
        _tmp.cleanup()

    def login_user(self, name):
        pair = self.core.post('/api/v1/auth/token', json={'username': f'{name}_{uuid.uuid4().hex[:6]}'}).json()
        headers = {'Authorization': 'Bearer ' + pair['access_token']}
        return self.core.get('/api/v1/auth/me', headers=headers).json()['id'], headers

    def signed_in(self):
        client = TestClient(operator_app)
        self.assertEqual(client.post('/api/login', json={'password': 'correct horse battery'}).status_code, 200)
        return client

    def test_login_and_guards(self):
        anon = TestClient(operator_app)
        self.assertEqual(anon.get('/').status_code, 200)
        self.assertIn("frame-ancestors 'none'", anon.get('/').headers['Content-Security-Policy'])
        self.assertEqual(anon.get('/api/overview').status_code, 401)
        self.assertFalse(anon.get('/api/session').json()['authenticated'])
        _failures.clear()
        for _ in range(5):
            self.assertEqual(anon.post('/api/login', json={'password': 'nope'}).status_code, 401)
        self.assertEqual(anon.post('/api/login', json={'password': 'correct horse battery'}).status_code, 429)
        _failures.clear()

        op = self.signed_in()
        self.assertTrue(op.get('/api/session').json()['authenticated'])
        # Изменения без X-Operator отклоняются (защита от CSRF).
        self.assertEqual(op.post('/api/tools/ensure-invariants').status_code, 403)
        self.assertEqual(op.post('/api/tools/ensure-invariants', headers=W).status_code, 200)
        # Модулей нет (папки OPERATOR_PLUGINS_DIR нет) — пульт работает, список модулей пуст.
        self.assertEqual(op.get('/api/plugins').json(), {'items': []})
        # Подделанная cookie не проходит.
        forged = TestClient(operator_app, cookies={'operator_session': '9999999999.x.AAAA'})
        self.assertEqual(forged.get('/api/overview').status_code, 401)
        op.post('/api/logout', headers=W)

    def test_delete_user_without_institutions(self):
        op = self.signed_in()
        lonely, lonely_h = self.login_user('lonely')
        member, _ = self.login_user('member')
        staff, _ = self.login_user('staffer')
        inst = op.post('/api/institutions', headers=W, json={'titles': {'ru': 'Удаляемый тест'}, 'owner_user_id': member})
        self.assertEqual(inst.status_code, 201, inst.text)
        self.assertEqual(op.post(f'/api/users/{staff}/staff', headers=W).status_code, 200)

        # Состоит в вузе или работает в поддержке — не удаляется.
        self.assertEqual(op.delete(f'/api/users/{member}', headers=W).json()['error']['code'], 'USER_HAS_MEMBERSHIPS')
        self.assertEqual(op.delete(f'/api/users/{staff}', headers=W).json()['error']['code'], 'USER_IS_STAFF')
        self.assertEqual(op.delete(f'/api/users/{uuid.uuid4()}', headers=W).status_code, 404)
        self.assertEqual(op.delete(f'/api/users/{lonely}').status_code, 403)  # без заголовка панели

        # Без вузов — удаляется, токены сразу перестают действовать.
        self.assertEqual(self.core.get('/api/v1/auth/me', headers=lonely_h).status_code, 200)
        deleted = op.delete(f'/api/users/{lonely}', headers=W)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(self.core.get('/api/v1/auth/me', headers=lonely_h).status_code, 401)
        self.assertNotIn(lonely, [u['id'] for u in op.get('/api/users').json()['items']])
        self.assertEqual(op.delete(f'/api/users/{lonely}', headers=W).status_code, 404)
        with session_local() as db:
            self.assertTrue(db.query(AuditEvent).filter_by(action='user.delete', target_id=lonely).first())

    def test_platform_and_institution_management(self):
        op = self.signed_in()
        applicant, applicant_h = self.login_user('rector')
        student, student_h = self.login_user('student')
        support, _ = self.login_user('support')

        # Заявка вуза из приложения одобряется только в панели.
        app_id = self.core.post('/api/v1/institution-applications', headers=applicant_h,
                                json={'titles': {'ru': 'Политех'}, 'contact': 'r@example.ru'}).json()['id']
        pending = op.get('/api/applications?status=pending').json()['items']
        self.assertIn(app_id, [a['id'] for a in pending])
        approved = op.post(f'/api/applications/{app_id}/approve', headers=W, json={'titles': {'ru': 'Политех СПб'}})
        self.assertEqual(approved.status_code, 200, approved.text)
        inst = approved.json()['institution_id']
        self.assertEqual(op.post(f'/api/applications/{app_id}/approve', headers=W).json()['error']['code'],
                         'APPLICATION_NOT_PENDING')

        # Вуз напрямую, с владельцем. У вуза сразу есть администрирование, «Люди» и расписание.
        created = op.post('/api/institutions', headers=W, json={'titles': {'ru': 'Горный'}, 'owner_user_id': support})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(sorted(s['service_type'] for s in created.json()['services']),
                         ['administration', 'schedule', 'user-profile'])
        names = {i['id']: i['titles']['ru'] for i in op.get('/api/institutions').json()['items']}
        self.assertEqual(names[inst], 'Политех СПб')

        # Управление вузом: группы, заявка на вступление, участники, роли.
        base = f'/api/institutions/{inst}/manage'
        group = op.post(f'{base}/groups', headers=W, json={'name': 'ИВТ-1'}).json()['id']
        request = self.core.post('/api/v1/join-requests', headers=student_h, json={
            'full_name': 'Мария Ильина', 'items': [{'institution_id': inst, 'profile': 'student', 'group_id': group}]})
        self.assertEqual(request.status_code, 201, request.text)
        overview = op.get('/api/overview').json()
        self.assertEqual(overview['counts']['pending_join_requests'], 1)
        rid = request.json()['items'][0]['id']
        self.assertEqual(op.post(f'{base}/join-requests/{rid}/approve', headers=W).status_code, 200)
        members = {m['user_id']: m for m in op.get(f'{base}/members').json()['items']}
        self.assertEqual(members[student]['group_id'], group)
        self.assertEqual(members[student]['full_name'], 'Мария Ильина')

        member = op.get(f'{base}/members/{student}')
        changed = op.put(f'{base}/members/{student}/profiles', headers={**W, 'If-Match': member.headers['ETag']},
                         json={'profiles': ['student', 'teacher']})
        self.assertEqual(changed.status_code, 200, changed.text)
        admin_service = next(s for s in op.get(f'{base}/services').json()['items'] if s['service_type'] == 'administration')
        path = f'{base}/services/{admin_service["id"]}/users/{applicant}/profiles/admin/roles'
        tag = op.get(path).headers['ETag']
        # Последнего владельца снять нельзя даже оператору.
        self.assertEqual(op.put(path, headers={**W, 'If-Match': tag}, json={'roles': []}).json()['error']['code'],
                         'LAST_OWNER')
        self.assertEqual(op.post(f'/api/institutions/{inst}/owners', headers=W, json={'user_id': support}).status_code, 200)

        # Статус вуза и сервисы платформы.
        tag = op.get(f'/api/institutions/{inst}').headers['ETag']
        self.assertEqual(op.patch(f'/api/institutions/{inst}/status', headers={**W, 'If-Match': tag},
                                  json={'status': 'suspended'}).json()['status'], 'suspended')
        # Администрирование облачное: адреса и ключ задаёт платформа, вручную не меняются.
        admin = next(x for x in op.get(f'{base}/services').json()['items'] if x['service_type'] == 'administration')
        self.assertEqual((admin['deployment'], admin['api_base_url']),
                         ('cloud', f"https://admin.platform.example/{admin['id']}/api/v1"))
        self.assertEqual(op.post(f"{base}/services/{admin['id']}/credentials", headers=W).status_code, 403)
        etag = op.get(f"{base}/services/{admin['id']}").headers['ETag']
        self.assertEqual(op.patch(f"{base}/services/{admin['id']}", headers={**W, 'If-Match': etag}, json={'enabled': False})
                         .json()['error']['code'], 'PROTECTED_RESOURCE')
        self.assertEqual(op.get(f'{base}/nonsense').status_code, 404)

        # Пользователи: поиск по имени и права поддержки.
        found = op.get('/api/users?q=ильина').json()['items']
        self.assertEqual([u['id'] for u in found], [student])
        self.assertTrue(op.post(f'/api/users/{student}/staff', headers=W).json()['changed'])
        with session_local() as db:
            self.assertIsNotNone(db.get(PlatformStaff, student))
            self.assertTrue(db.query(AuditEvent).filter_by(actor_kind='operator', action='join_request.approve').count())
        self.assertTrue(op.delete(f'/api/users/{student}/staff', headers=W).json()['changed'])
        self.assertTrue(op.get(f'/api/audit?institution_id={inst}').json()['items'])


    def test_delete_institution_removes_everything(self):
        """Удаление вуза: в ядре не остаётся ни одной строки вуза и его сервисов, раннеры получают purge."""
        from sqlalchemy import inspect, text
        from database.create_tables import engine
        from database.tables import (CloudBinding, IdempotencyRecord, InstitutionLocalHost, JoinRequest, Membership,
                                     PurgedInstance, RoleAssignment, ServiceInstance, ServiceRole, StudyGroup,
                                     StudyGroupMember)
        from auth.models import ServiceCredential, ServiceSession
        op = self.signed_in()
        owner, owner_h = self.login_user('del_owner')
        student, student_h = self.login_user('del_student')
        other_owner, _ = self.login_user('keep_owner')
        inst = op.post('/api/institutions', headers=W, json={'titles': {'ru': 'Удаляемый вуз'}, 'owner_user_id': owner}).json()['id']
        keep = op.post('/api/institutions', headers=W, json={'titles': {'ru': 'Остающийся вуз'}, 'owner_user_id': other_owner}).json()['id']

        # Вуз сразу получает администрирование, «Людей» и расписание.
        services = {s['service_type']: s for s in op.get(f'/api/institutions/{inst}').json()['services']}
        self.assertEqual(set(services), {'administration', 'schedule', 'user-profile'})
        self.assertTrue(all(s['deployment'] == 'cloud' and s['enabled'] for s in services.values()))

        # Наполняем вуз: студент, группа, заявка, роль с назначением, сессия сервиса, хост, idempotency.
        with session_local() as db:
            db.add(Membership(institution_id=inst, user_id=student, profiles=['student']))
            group = StudyGroup(institution_id=inst, name='ИВТ-1', name_key='ивт-1')
            db.add(group)
            db.flush()
            db.add(StudyGroupMember(institution_id=inst, user_id=student, group_id=group.id))
            db.add(JoinRequest(institution_id=inst, user_id=other_owner, profile='student', full_name='Гость', status='pending'))
            sched = services['schedule']['id']
            db.add(ServiceRole(service_id=sched, code='viewer', titles={'ru': 'П'}, allowed_profiles=['student'],
                               permissions=['schedule.read_all'], system=False))
            db.add(RoleAssignment(service_id=sched, user_id=student, profile='student', roles=['viewer']))
            db.add(InstitutionLocalHost(institution_id=inst, hostname='cw.example.ru', approved_by='test'))
            db.commit()
        session = self.core.post(f'/api/v1/institution/{inst}/service/{sched}/session', headers=student_h,
                                 json={'profile': 'student'})
        self.assertEqual(session.status_code, 201, session.text)
        renewed = self.core.post(f'/api/v1/institution/{inst}/service/{sched}/session/refresh',
                                 json={'refresh_token': session.json()['refresh_token']})
        self.assertEqual(renewed.status_code, 200, renewed.text)
        session_id = session.json()['session_id']
        from database.tables import InstitutionApplication
        from auth.models import RefreshUse
        with session_local() as db:
            db.add(InstitutionApplication(applicant_user_id=owner, titles={'ru': 'Удаляемый вуз'}, default_locale='ru',
                                          contact='r@example.ru', status='approved', institution_id=inst))
            db.commit()
            self.assertEqual(db.query(RefreshUse).filter_by(session_id=session_id).count(), 2)

        def leftovers():
            with session_local() as db:
                sids = [r[0] for r in db.query(ServiceInstance.id).filter(ServiceInstance.institution_id == inst)]
                counts = {
                    'services': len(sids),
                    'memberships': db.query(Membership).filter_by(institution_id=inst).count(),
                    'groups': db.query(StudyGroup).filter_by(institution_id=inst).count(),
                    'group_members': db.query(StudyGroupMember).filter_by(institution_id=inst).count(),
                    'join_requests': db.query(JoinRequest).filter_by(institution_id=inst).count(),
                    'local_hosts': db.query(InstitutionLocalHost).filter_by(institution_id=inst).count(),
                    'idempotency': db.query(IdempotencyRecord).filter_by(institution_id=inst).count(),
                    'service_sessions': db.query(ServiceSession).filter_by(institution_id=inst).count(),
                    'roles': db.query(ServiceRole).filter(ServiceRole.service_id.in_(all_sids)).count(),
                    'assignments': db.query(RoleAssignment).filter(RoleAssignment.service_id.in_(all_sids)).count(),
                    'credentials': db.query(ServiceCredential).filter(ServiceCredential.service_id.in_(all_sids)).count(),
                    'bindings': db.query(CloudBinding).filter(CloudBinding.service_id.in_(all_sids)).count(),
                }
            return {k: v for k, v in counts.items() if v}
        all_sids = [s['id'] for s in services.values()]
        self.assertEqual(set(leftovers()) >= {'services', 'memberships', 'groups', 'group_members', 'join_requests',
                                               'local_hosts', 'service_sessions', 'roles', 'assignments',
                                               'credentials', 'bindings'}, True, leftovers())

        # Подтверждение — точное название; неверное ничего не удаляет.
        wrong = op.request('DELETE', f'/api/institutions/{inst}', headers=W, json={'confirm_title': 'Другой'})
        self.assertEqual(wrong.status_code, 422)
        self.assertEqual(op.request('DELETE', f'/api/institutions/{inst}', json={'confirm_title': 'Удаляемый вуз'}).status_code, 403)
        deleted = op.request('DELETE', f'/api/institutions/{inst}', headers=W, json={'confirm_title': 'Удаляемый вуз'})
        self.assertEqual(deleted.status_code, 204, deleted.text)

        # В ядре не осталось ничего от вуза; в том числе по прямой проверке всех таблиц с institution_id/service_id.
        self.assertEqual(leftovers(), {})
        with session_local() as db:
            self.assertEqual(db.query(RefreshUse).filter_by(session_id=session_id).count(), 0)  # история refresh
            self.assertEqual(db.query(InstitutionApplication).filter_by(institution_id=inst).count(), 0)
        with engine.connect() as conn:
            for table in inspect(engine).get_table_names():
                cols = {c['name'] for c in inspect(engine).get_columns(table)}
                if table in ('audit_events', 'purged_instances', 'privacy_disclosures', 'privacy_tasks'):
                    continue  # history and unacknowledged cleanup survive institution deletion
                if 'institution_id' in cols:
                    n = conn.execute(text(f'SELECT count(*) FROM {table} WHERE institution_id = :i'), {'i': inst}).scalar()
                    self.assertEqual(n, 0, table)
                if 'service_id' in cols:
                    q = f"SELECT count(*) FROM {table} WHERE service_id IN ({','.join(repr(x) for x in all_sids)})"
                    self.assertEqual(conn.execute(text(q)).scalar(), 0, table)
            self.assertEqual(conn.execute(text('PRAGMA foreign_key_check')).fetchall(), [])

        # Раннеры получают экземпляры вуза в purge и больше не получают их ключей.
        for token, sid in (('p' * 48, services['administration']['id']), ('s' * 48, sched)):
            listed = self.core.get('/api/v1/internal/provisioning/instances', headers={'Authorization': 'Bearer ' + token}).json()
            self.assertIn(sid, listed['purge'])
            self.assertNotIn(sid, [i['service_id'] for i in listed['items']])
        with session_local() as db:
            self.assertEqual({r.service_id for r in db.query(PurgedInstance).filter_by(institution_id=inst)}, set(all_sids))
            self.assertTrue(db.query(AuditEvent).filter_by(action='institution.delete', target_id=inst).first())

        # Выданные токены больше не действуют; вуз недоступен; пользователи и другой вуз целы.
        self.assertEqual(self.core.get(f'/api/v1/institution/{inst}', headers=owner_h).status_code, 404)
        self.assertEqual(self.core.get('/api/v1/auth/me', headers=student_h).status_code, 200)
        self.assertEqual(op.get(f'/api/institutions/{inst}').status_code, 404)
        self.assertEqual(op.get(f'/api/institutions/{keep}').status_code, 200)
        self.assertEqual(len(op.get(f'/api/institutions/{keep}').json()['services']), 3)

    def test_health_checks_every_enabled_service(self):
        from unittest.mock import patch
        from operator_panel import app as panel
        from platform_core import registry
        with session_local() as db:
            inst = registry.provision_institution(db, {'ru': 'Проверочный'}, 'ru')
            svc = registry.create_local_instance(db, inst.id, 'custom.schedule', 'https://schedule.check.ru/api/v1',
                                                 'https://schedule.check.ru', {'ru': 'Своё расписание'}, ['student', 'admin'])
            svc.enabled = True
            off = registry.create_local_instance(db, inst.id, 'custom.people', 'https://people.check.ru/api/v1',
                                                 'https://people.check.ru', {'ru': 'Люди'}, ['admin'])
            db.commit()
            admin_id = registry.admin_service_of(db, inst.id).id
        with patch.dict(os.environ, {'OPERATOR_HEALTH_TARGETS': ''}):
            targets = panel._health_targets()
        self.assertEqual(targets[next(k for k in targets if k.endswith('Своё расписание'))], 'https://schedule.check.ru/api/v1/health')
        self.assertIn('Ядро', targets)
        self.assertNotIn('https://people.check.ru/api/v1/health', targets.values())  # выключенный не проверяется
        # Облачные — у раннера по внутренней сети, без пробуждения процесса.
        self.assertIn(f'http://administration:8000/health?service_id={admin_id}', targets.values())
        self.assertTrue(any(v.startswith('http://schedule:8000/health?service_id=') for v in targets.values()))

if __name__ == '__main__':
    unittest.main()
