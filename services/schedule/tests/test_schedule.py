"""Сервис расписания против заглушки ядра: доступ по профилям, группы из ядра, занятия, версии, idempotency.

    cd services/schedule && python -m unittest discover -s tests -t . -v
"""
import os
import sqlite3
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

_tmp = tempfile.TemporaryDirectory()
os.environ['SCHEDULE_DB'] = _tmp.name + '/schedule.db'
os.environ['SCHEDULE_PROVISIONING_TOKEN'] = 't' * 48
import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main as m  # noqa: E402

I = '44444444-4444-4444-8444-444444444444'
ADMIN, EDITOR, T1, T2, ST1, ST2 = (str(uuid.UUID(int=n)) for n in range(1, 7))
PROFILES = {ADMIN: ['admin'], EDITOR: ['admin'], T1: ['teacher'], T2: ['teacher'], ST1: ['student'], ST2: ['student']}
EDITOR_PERMS = ['schedule.read_all', 'schedule.write']
GA, GB = str(uuid.uuid4()), str(uuid.uuid4())
GROUPS = [{'id': GA, 'name': 'ИВТ-21'}, {'id': GB, 'name': 'ИВТ-22'}]
# Группы студентов ведёт ядро и сообщает их в introspection.
MEMBERSHIP = {ST1: [GA]}


def binding(service_id):
    return SimpleNamespace(service_id=service_id, institution_id=I, api_base_url='https://shell.test/schedule/api/v1',
                           client_base_url='https://shell.test')


class Schedule(unittest.TestCase):
    def setUp(self):
        self.actors = {}
        self.service = str(uuid.uuid4())
        for p in (patch.object(m.core, 'binding', side_effect=lambda sid, fresh=False: binding(sid)),
                  patch.object(m.core, 'introspect', side_effect=self.introspect),
                  patch.object(m.core, 'profiles', side_effect=lambda b, uid: PROFILES.get(uid, [])),
                  patch.object(m.core, 'groups', side_effect=lambda b: list(GROUPS))):
            p.start()
            self.addCleanup(p.stop)
        self.ctx = TestClient(m.app)
        self.c = self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)

    def introspect(self, b, token):
        claims = jwt.decode(token, options={'verify_signature': False})
        profile, permissions = self.actors[token]
        return {'active': True, 'service_id': claims['service_id'], 'institution_id': I, 'sub': claims['sub'],
                'profile': profile, 'session_id': 'sid', 'parent_session_id': 'psid', 'roles': [],
                'permissions': permissions, 'group_ids': MEMBERSHIP.get(claims['sub'], [])}

    def as_(self, user, profile, permissions=()):
        claims = {'service_id': self.service, 'sub': user, 'aud': 'service:' + self.service, 'token_use': 'service_access',
                  'profile': profile, 'sid': 'sid', 'parent_sid': 'psid', 'jti': str(uuid.uuid4())}
        token = jwt.encode(claims, 'x' * 32)
        self.actors[token] = (profile, list(permissions))
        return {'Authorization': 'Bearer ' + token}

    def post(self, h, body, key=None):
        return self.c.post('/api/v1/schedule/events', headers={**h, 'Idempotency-Key': key or str(uuid.uuid4())}, json=body)

    @staticmethod
    def event(groups, teachers, start='2026-09-28T06:00:00Z', end='2026-09-28T07:30:00Z', **extra):
        return {'title': 'Матанализ', 'starts_at': start, 'ends_at': end, 'group_ids': groups, 'teacher_ids': teachers,
                'location': '205', 'description': '', 'status': 'scheduled', **extra}

    def week(self, h, **filters):
        params = {'from': '2026-09-28T00:00:00Z', 'to': '2026-10-05T00:00:00Z', **filters}
        return self.c.get('/api/v1/schedule/events', headers=h, params=params)

    def test_groups_come_from_core(self):
        teacher, student, loner = self.as_(T1, 'teacher'), self.as_(ST1, 'student'), self.as_(ST2, 'student')
        plain_admin = self.as_(ADMIN, 'admin')
        # Все группы видны любому участнику: просмотр — часть сервиса, а не право.
        for who in (teacher, student, loner, plain_admin):
            self.assertEqual([g['name'] for g in self.c.get('/api/v1/schedule/groups', headers=who).json()['items']],
                             ['ИВТ-21', 'ИВТ-22'])
        self.assertEqual(self.c.get(f'/api/v1/schedule/groups/{GB}', headers=student).status_code, 200)
        # Управления группами в сервисе нет: их ведёт ядро (роль «Редактор групп»).
        self.assertEqual(self.c.post('/api/v1/schedule/groups', headers=teacher, json={'name': 'x'}).status_code, 405)

    def test_only_editors_write_everyone_reads(self):
        editor = self.as_(EDITOR, 'admin', EDITOR_PERMS)
        teacher_editor = self.as_(T1, 'teacher', EDITOR_PERMS)
        t1, t2 = self.as_(T1, 'teacher'), self.as_(T2, 'teacher')
        st1, st2 = self.as_(ST1, 'student'), self.as_(ST2, 'student')
        plain_admin = self.as_(ADMIN, 'admin')

        # Студент без группы — пустое расписание, не ошибка.
        self.assertEqual(self.week(st2).json(), {'items': [], 'next_cursor': None})

        # Пишет только администратор с ролью «Редактор расписания». Преподаватель только смотрит —
        # даже если право каким-то образом оказалось у профиля teacher.
        for who in (t1, teacher_editor, st1, plain_admin):
            self.assertEqual(self.post(who, self.event([GA], [T1])).status_code, 403)
        self.assertEqual(self.post(editor, self.event([GA], [T1, ST1])).json()['error']['code'], 'INVALID_REFERENCE')
        self.assertEqual(self.post(editor, self.event([GA], [T1], start='2026-09-28T08:00:00Z')).json()['error']['code'],
                         'INVALID_TIME_RANGE')
        self.assertEqual(self.post(editor, self.event([str(uuid.uuid4())], [T1])).json()['error']['code'],
                         'INVALID_REFERENCE')

        created = self.post(editor, self.event([GA], [T1]))
        self.assertEqual(created.status_code, 201, created.text)
        event = created.json()
        other = self.post(editor, self.event([GB], [T2], start='2026-09-29T06:00:00+03:00',
                                             end='2026-09-29T07:30:00+03:00')).json()
        self.assertEqual(other['starts_at'], '2026-09-29T03:00:00Z')

        # По умолчанию — «своё»; фильтром любой участник смотрит любую группу или преподавателя.
        self.assertEqual([e['id'] for e in self.week(st1).json()['items']], [event['id']])
        self.assertEqual([e['id'] for e in self.week(t1).json()['items']], [event['id']])
        self.assertEqual([e['id'] for e in self.week(st1, group_id=GB).json()['items']], [other['id']])
        self.assertEqual([e['id'] for e in self.week(st2, group_id=GA).json()['items']], [event['id']])
        self.assertEqual([e['id'] for e in self.week(t1, teacher_id=T2).json()['items']], [other['id']])
        self.assertEqual(len(self.week(plain_admin).json()['items']), 2)
        self.assertEqual(len(self.week(editor).json()['items']), 2)
        self.assertEqual(self.c.get(f'/api/v1/schedule/events/{other["id"]}', headers=st1).status_code, 200)

        # Своё занятие преподаватель без роли не меняет.
        tag = created.headers['etag']
        self.assertEqual(self.c.put(f'/api/v1/schedule/events/{event["id"]}', headers={**t1, 'If-Match': tag},
                                    json=self.event([GA], [T1], status='cancelled')).status_code, 403)
        self.assertEqual(self.c.delete(f'/api/v1/schedule/events/{event["id"]}', headers={**t2, 'If-Match': tag}).status_code, 403)

        self.assertEqual(self.c.put(f'/api/v1/schedule/events/{event["id"]}', headers={**teacher_editor, 'If-Match': tag},
                                    json=self.event([GA], [T1])).status_code, 403)

        # Редактор отменяет; отменённое видно студенту; устаревший ETag — 412.
        cancelled = self.c.put(f'/api/v1/schedule/events/{event["id"]}', headers={**editor, 'If-Match': tag},
                               json=self.event([GA], [T1, T2], status='cancelled'))
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(self.week(st1).json()['items'][0]['status'], 'cancelled')
        self.assertEqual(self.c.put(f'/api/v1/schedule/events/{event["id"]}', headers={**editor, 'If-Match': tag},
                                    json=self.event([GA], [T1])).status_code, 412)

        # Студента перевели в другую группу в ядре — со следующего запроса он видит её занятия.
        MEMBERSHIP[ST1] = [GB]
        try:
            self.assertEqual([e['id'] for e in self.week(st1).json()['items']], [other['id']])
        finally:
            MEMBERSHIP[ST1] = [GA]

        other_tag = self.c.get(f'/api/v1/schedule/events/{other["id"]}', headers=editor).headers['etag']
        self.assertEqual(self.c.delete(f'/api/v1/schedule/events/{other["id"]}',
                                       headers={**editor, 'If-Match': other_tag}).status_code, 204)

    def test_idempotency_range_and_paging(self):
        editor = self.as_(EDITOR, 'admin', EDITOR_PERMS)
        key = str(uuid.uuid4())
        first, again = self.post(editor, self.event([GA], [T1]), key), self.post(editor, self.event([GA], [T1]), key)
        self.assertEqual((first.status_code, again.status_code, first.json()['id']), (201, 201, again.json()['id']))
        self.assertEqual(self.post(editor, self.event([GA], [T2]), key).json()['error']['code'], 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.c.post('/api/v1/schedule/events', headers=editor, json=self.event([GA], [T1])).status_code, 400)
        too_long = self.c.get('/api/v1/schedule/events', headers=editor,
                              params={'from': '2026-09-01T00:00:00Z', 'to': '2026-10-05T00:00:00Z'})
        self.assertEqual(too_long.json()['error']['code'], 'INVALID_TIME_RANGE')
        for day in range(1, 5):
            self.post(editor, self.event([GA], [T1], start=f'2026-09-2{day}T06:00:00Z', end=f'2026-09-2{day}T07:00:00Z'))
        seen, cursor = [], None
        while True:
            params = {'from': '2026-09-20T00:00:00Z', 'to': '2026-10-01T00:00:00Z', 'limit': 2, **({'cursor': cursor} if cursor else {})}
            page = self.c.get('/api/v1/schedule/events', headers=editor, params=params).json()
            seen += [e['starts_at'] for e in page['items']]
            cursor = page['next_cursor']
            if not cursor:
                break
        self.assertEqual((seen, len(seen)), (sorted(seen), 5))

    def test_core_down_and_session_checks(self):
        self.assertEqual(self.c.get('/api/v1/schedule/groups').status_code, 401)
        teacher = self.as_(T1, 'teacher')
        with patch.object(m.core, 'introspect', side_effect=m.CoreUnavailable('offline')):
            self.assertEqual(self.c.get('/api/v1/schedule/groups', headers=teacher).status_code, 503)
        with patch.object(m.core, 'groups', side_effect=m.CoreUnavailable('offline')):
            self.assertEqual(self.c.get('/api/v1/schedule/groups', headers=teacher).status_code, 503)
        with patch.object(m.core, 'binding', side_effect=m.BindingMissing(self.service)):
            self.assertEqual(self.c.get('/api/v1/schedule/groups', headers=teacher).status_code, 404)


class Migration(unittest.TestCase):
    def test_old_database_keeps_events_and_exports_groups(self):
        """Том со старой схемой: занятия сохраняются, группы выгружаются для переноса в ядро."""
        path = _tmp.name + '/old.db'
        db = sqlite3.connect(path)
        db.executescript('''
            CREATE TABLE groups (institution_id TEXT, service_id TEXT, id TEXT, name TEXT, name_key TEXT,
                revision INTEGER, students_revision INTEGER, PRIMARY KEY (institution_id, service_id, id));
            CREATE TABLE group_students (institution_id TEXT, service_id TEXT, group_id TEXT, user_id TEXT,
                PRIMARY KEY (institution_id, service_id, user_id),
                FOREIGN KEY (institution_id, service_id, group_id) REFERENCES groups (institution_id, service_id, id));
            CREATE TABLE events (institution_id TEXT NOT NULL, service_id TEXT NOT NULL, id TEXT NOT NULL,
                title TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, location TEXT NOT NULL,
                description TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (institution_id, service_id, id));
            CREATE TABLE event_groups (institution_id TEXT NOT NULL, service_id TEXT NOT NULL, event_id TEXT NOT NULL,
                group_id TEXT NOT NULL, pos INTEGER NOT NULL, PRIMARY KEY (institution_id, service_id, event_id, group_id),
                FOREIGN KEY (institution_id, service_id, event_id) REFERENCES events (institution_id, service_id, id) ON DELETE CASCADE,
                FOREIGN KEY (institution_id, service_id, group_id) REFERENCES groups (institution_id, service_id, id));
            INSERT INTO groups VALUES ('i', 's', 'g1', 'ИВТ-21', 'ивт-21', 1, 1);
            INSERT INTO group_students VALUES ('i', 's', 'g1', 'u1');
            INSERT INTO events VALUES ('i', 's', 'e1', 'Лекция', '2026-09-28T06:00:00Z', '2026-09-28T07:00:00Z', '', '', 'scheduled', 1);
            INSERT INTO event_groups VALUES ('i', 's', 'e1', 'g1', 0);
        ''')
        db.commit()
        db.close()
        from app import export_groups
        with patch.object(m, 'DB', path), patch.object(export_groups, 'DB', path):
            m.prepare()
            m.prepare()  # повторный старт ничего не ломает
            with m.database() as db:
                fks = db.execute("PRAGMA foreign_key_list('event_groups')").fetchall()
                self.assertFalse(any(fk['table'] == 'groups' for fk in fks))
                self.assertEqual(db.execute('SELECT group_id FROM event_groups').fetchall()[0][0], 'g1')
                # Занятие с группой ядра, которой нет в старой таблице, теперь сохраняется.
                db.execute("INSERT INTO event_groups VALUES ('i', 's', 'e1', 'core-group', 1)")
            self.assertEqual(export_groups.export(),
                             {'groups': [{'id': 'g1', 'institution_id': 'i', 'name': 'ИВТ-21', 'user_ids': ['u1']}]})


if __name__ == '__main__':
    unittest.main()
