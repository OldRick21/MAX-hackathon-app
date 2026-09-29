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
os.environ.update(CORE_URL='https://core.test', SHELL_ORIGIN='https://shell.test', SERVICE_CLIENT_ID='client',
                  SERVICE_CLIENT_SECRET='t' * 48, SERVICE_API_BASE_URL='https://svc.test/api/v1',
                  SERVICE_CLIENT_BASE_URL='https://svc.test')
import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main as m  # noqa: E402

I = '44444444-4444-4444-8444-444444444444'
ADMIN, EDITOR, T1, T2, ST1, ST2 = (str(uuid.UUID(int=n)) for n in range(1, 7))
PROFILES = {ADMIN: ['admin'], EDITOR: ['admin'], T1: ['teacher'], T2: ['teacher'], ST1: ['student'], ST2: ['student']}
EDITOR_PERMS = ['schedule.read_all', 'schedule.write']  # роль schedule_editor (контракт §1)
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
        for p in (patch.object(m.onboarding, 'start'),
                  patch.object(m.core, 'binding', side_effect=lambda sid=None, fresh=False: binding(sid)),
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

    def test_cors_only_for_shell(self):
        # Экраны рисует оболочка на своём origin и обращается к сервису напрямую.
        pre = {'Access-Control-Request-Method': 'PUT', 'Access-Control-Request-Headers': 'authorization,if-match'}
        ok = self.c.options('/api/v1/schedule/events', headers={'Origin': 'https://shell.test', **pre})
        self.assertEqual(ok.headers.get('access-control-allow-origin'), 'https://shell.test')
        bad = self.c.options('/api/v1/schedule/events', headers={'Origin': 'https://evil.test', **pre})
        self.assertIsNone(bad.headers.get('access-control-allow-origin'))

    def test_groups_come_from_core(self):
        teacher, student, loner = self.as_(T1, 'teacher'), self.as_(ST1, 'student'), self.as_(ST2, 'student')
        plain_admin, editor = self.as_(ADMIN, 'admin'), self.as_(EDITOR, 'admin', EDITOR_PERMS)
        names = lambda h: [g['name'] for g in self.c.get('/api/v1/schedule/groups', headers=h).json()['items']]
        # Контракт: студент — только своя группа, без группы — пустой список; преподаватель и редактор — все.
        by_id = [g['name'] for g in sorted(GROUPS, key=lambda g: g['id'])]  # контракт: порядок id ASC
        self.assertEqual(names(teacher), by_id)
        self.assertEqual(names(editor), by_id)
        self.assertEqual(names(student), ['ИВТ-21'])
        self.assertEqual(names(loner), [])
        self.assertEqual(self.c.get(f'/api/v1/schedule/groups/{GB}', headers=student).status_code, 404)
        self.assertEqual(self.c.get('/api/v1/schedule/groups', headers=plain_admin).status_code, 403)
        # Группы ведёт ядро: изменяющих операций над группами в сервисе нет.
        self.assertEqual(self.c.post('/api/v1/schedule/groups', headers=editor, json={'name': 'x'}).status_code, 405)

    def test_contract_access(self):
        editor = self.as_(EDITOR, 'admin', EDITOR_PERMS)
        writer_only = self.as_(EDITOR, 'admin', ['schedule.write'])
        t1, t2 = self.as_(T1, 'teacher'), self.as_(T2, 'teacher')
        st1, st2 = self.as_(ST1, 'student'), self.as_(ST2, 'student')
        plain_admin = self.as_(ADMIN, 'admin')

        # §4.1 Студент без группы — пустое расписание, не ошибка.
        self.assertEqual(self.week(st2).json(), {'items': [], 'next_cursor': None})
        # Писать может admin с schedule.write; преподаватель без роли, студент и admin без роли — нет.
        for who in (t1, st1, plain_admin, self.as_(ST1, 'student', EDITOR_PERMS)):
            self.assertEqual(self.post(who, self.event([GA], [T1])).status_code, 403)
        # Отличие от контракта: преподаватель с включённой правкой пишет и видит все занятия.
        teacher_editor = self.as_(T2, 'teacher', EDITOR_PERMS)
        by_teacher = self.post(teacher_editor, self.event([GB], [T1], start='2026-10-02T06:00:00Z', end='2026-10-02T07:00:00Z'))
        self.assertEqual(by_teacher.status_code, 201, by_teacher.text)
        self.assertEqual(self.c.delete(f'/api/v1/schedule/events/{by_teacher.json()["id"]}',
                                       headers={**teacher_editor, 'If-Match': by_teacher.headers['etag']}).status_code, 204)
        self.assertEqual(self.post(editor, self.event([GA], [T1, ST1])).json()['error']['code'], 'INVALID_REFERENCE')
        self.assertEqual(self.post(editor, self.event([GA], [T1], start='2026-09-28T08:00:00Z')).json()['error']['code'],
                         'INVALID_TIME_RANGE')
        self.assertEqual(self.post(editor, self.event([str(uuid.uuid4())], [T1])).json()['error']['code'], 'INVALID_REFERENCE')
        created = self.post(editor, self.event([GA], [T1]))
        self.assertEqual(created.status_code, 201, created.text)
        event = created.json()
        other = self.post(writer_only, self.event([GB], [T2], start='2026-09-29T06:00:00+03:00',
                                                  end='2026-09-29T07:30:00+03:00')).json()
        self.assertEqual(other['starts_at'], '2026-09-29T03:00:00Z')

        # §4.1–4.2 Видимость: студент — своя группа, преподаватель — свои, редактор — все; фильтр не расширяет.
        self.assertEqual([e['id'] for e in self.week(st1).json()['items']], [event['id']])
        self.assertEqual([e['id'] for e in self.week(t1).json()['items']], [event['id']])
        self.assertEqual(self.week(t1, teacher_id=T2).json()['items'], [])
        self.assertEqual(self.week(st1, group_id=GB).json()['items'], [])
        self.assertEqual(len(self.week(editor).json()['items']), 2)
        self.assertEqual([e['id'] for e in self.week(editor, group_id=GB).json()['items']], [other['id']])
        self.assertEqual(self.c.get(f'/api/v1/schedule/events/{other["id"]}', headers=st1).status_code, 404)
        self.assertEqual(self.c.get(f'/api/v1/schedule/events/{other["id"]}', headers=t1).status_code, 404)
        self.assertEqual(self.week(plain_admin).status_code, 403)
        # Write не подразумевает read: без schedule.read_all занятие для правки не найти.
        self.assertEqual(self.week(writer_only).status_code, 403)

        # Отмена видна студенту; устаревший ETag — 412.
        tag = created.headers['etag']
        cancelled = self.c.put(f'/api/v1/schedule/events/{event["id"]}', headers={**editor, 'If-Match': tag},
                               json=self.event([GA], [T1, T2], status='cancelled'))
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(self.week(st1).json()['items'][0]['status'], 'cancelled')
        self.assertEqual(self.c.put(f'/api/v1/schedule/events/{event["id"]}', headers={**editor, 'If-Match': tag},
                                    json=self.event([GA], [T1])).status_code, 412)
        self.assertEqual(self.c.put(f'/api/v1/schedule/events/{event["id"]}', headers={**t1, 'If-Match': tag},
                                    json=self.event([GA], [T1])).status_code, 403)

        # Студента перевели в другую группу в ядре — со следующего запроса он видит её занятия.
        MEMBERSHIP[ST1] = [GB]
        try:
            self.assertEqual([e['id'] for e in self.week(st1).json()['items']], [other['id']])
        finally:
            MEMBERSHIP[ST1] = [GA]

        other_tag = self.c.get(f'/api/v1/schedule/events/{other["id"]}', headers=editor).headers['etag']
        self.assertEqual(self.c.delete(f'/api/v1/schedule/events/{other["id"]}',
                                       headers={**editor, 'If-Match': other_tag}).status_code, 204)

    def test_home_widgets(self):
        """Виджеты главной: свои занятия дня, сводка администратора, права как в самом расписании."""
        from datetime import datetime, timedelta
        editor, t1, t2 = self.as_(EDITOR, 'admin', EDITOR_PERMS), self.as_(T1, 'teacher'), self.as_(T2, 'teacher')
        st1, st2, plain_admin = self.as_(ST1, 'student'), self.as_(ST2, 'student'), self.as_(ADMIN, 'admin')
        now = datetime.now(m.TIMEZONE)
        def at(day_shift, hour):
            local = datetime(now.year, now.month, now.day, hour, tzinfo=m.TIMEZONE) + timedelta(days=day_shift)
            return local.astimezone(m.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        self.assertEqual(self.post(editor, self.event([GA], [T1], at(0, 9), at(0, 10), title='Сегодня А')).status_code, 201)
        self.assertEqual(self.post(editor, self.event([GB], [T2], at(2, 9), at(2, 10), title='Послезавтра Б')).status_code, 201)
        self.assertEqual(self.post(editor, self.event([GA], [T1], at(0, 12), at(0, 13), title='Отменено',
                                                      status='cancelled')).status_code, 201)
        today = self.c.get('/api/v1/schedule/widgets/today', headers=st1).json()
        self.assertEqual((today['kind'], today['day'], [e['title'] for e in today['items']]),
                         ('events', now.date().isoformat(), ['Сегодня А', 'Отменено']))
        self.assertEqual(today['items'][1]['status'], 'cancelled')
        # У студента без группы занятий нет; преподаватель Б видит ближайший день со своей парой.
        self.assertEqual(self.c.get('/api/v1/schedule/widgets/today', headers=st2).json()['items'], [])
        t2_day = self.c.get('/api/v1/schedule/widgets/today', headers=t2).json()
        self.assertEqual((t2_day['day'], [e['title'] for e in t2_day['items']]),
                         ((now.date() + timedelta(days=2)).isoformat(), ['Послезавтра Б']))
        self.assertEqual(self.c.get('/api/v1/schedule/widgets/today', headers=editor).status_code, 403)
        # Администратор с ролью редактора: занятия дня по всему вузу (с группами) и отдельная сводка.
        day = self.c.get('/api/v1/schedule/widgets/today-admin', headers=editor).json()
        self.assertEqual((day['kind'], [e['title'] for e in day['items']]), ('events', ['Сегодня А', 'Отменено']))
        self.assertEqual(day['items'][0]['place'], '205 · ИВТ-21')
        self.assertNotIn('more', day)
        stat = self.c.get('/api/v1/schedule/widgets/today-stats', headers=editor).json()
        self.assertEqual((stat['kind'], stat['value'], stat['tone']), ('stat', 2, 'warning'))
        self.assertIn('отменено 1', stat['caption'])
        for path in ('today-admin', 'today-stats'):
            self.assertEqual(self.c.get(f'/api/v1/schedule/widgets/{path}', headers=plain_admin).status_code, 403)
            self.assertEqual(self.c.get(f'/api/v1/schedule/widgets/{path}', headers=t1).status_code, 403)
        # Больше 10 занятий — первые 10 и «ещё N».
        for i in range(10):
            self.post(editor, self.event([GB], [T2], at(0, 14), at(0, 15), title=f'Доп {i}'))
        day = self.c.get('/api/v1/schedule/widgets/today-admin', headers=editor).json()
        self.assertEqual((len(day['items']), day['more']), (10, 2))
        self.assertEqual({w['id'] for w in m.MANIFEST['widgets']}, {'today', 'today_admin', 'today_stats'})

    def test_import_one_format_many_file_types(self):
        """Одна таблица занятий в JSON, CSV из 1С (cp1251, «;»), TXT (табуляция), XML и XLSX даёт один результат."""
        import io, json, zipfile
        from xml.sax.saxutils import escape
        editor, teacher, student = self.as_(EDITOR, 'admin', EDITOR_PERMS), self.as_(T1, 'teacher'), self.as_(ST1, 'student')
        header = ['Дата', 'Начало', 'Конец', 'Дисциплина', 'Группы', 'Преподаватели', 'Аудитория', 'Комментарий', 'Статус']
        rows = [['01.10.2026', '08:30', '10:05', 'Матанализ', 'ИВТ-21', 'Пётр Преподов', '205', '', ''],
                ['01.10.2026', '10:15', '11:50', 'Физика', 'ИВТ-21; ИВТ-22', T1, '', 'Лекция', 'отменено']]
        def csv_bytes(table):
            import csv
            out = io.StringIO()
            csv.writer(out, delimiter=';').writerows(table)  # 1С/Excel берут ячейку со «;» в кавычки
            return out.getvalue().encode('cp1251')
        def xlsx():
            cells = lambda r, n: ''.join(f'<c r="{chr(65 + j)}{n}" t="inlineStr"><is><t>{escape(v)}</t></is></c>' for j, v in enumerate(r))
            sheet = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                     + ''.join(f'<row r="{n}">{cells(r, n)}</row>' for n, r in enumerate([header, *rows], 1)) + '</sheetData></worksheet>')
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as z:
                z.writestr('xl/worksheets/sheet1.xml', sheet)
            return buf.getvalue()
        files = {
            'расписание.json': json.dumps([dict(zip(header, r)) for r in rows], ensure_ascii=False).encode(),
            'выгрузка_1с.csv': csv_bytes([['Расписание на октябрь'], header, *rows]),
            'выгрузка.txt': '\n'.join('\t'.join(r) for r in [header, *rows]).encode('utf-8'),
            'расписание.xml': ('<Расписание>' + ''.join('<Занятие ' + ' '.join(f'{h}="{escape(v)}"' for h, v in zip(header, r)) + '/>'
                                                       for r in rows) + '</Расписание>').encode('utf-8'),
            'расписание.xlsx': xlsx(),
        }
        members = [{'user_id': T1, 'profiles': ['teacher'], 'display_name': 'Пётр Преподов'},
                   {'user_id': ST1, 'profiles': ['student'], 'display_name': 'Анна'}]
        with patch.object(m.core, 'members', return_value=members):
            imp = lambda h, name, data, dry=True: self.c.post('/api/v1/schedule/import', params={'name': name, 'dry_run': dry},
                                                              headers={**h, 'Content-Type': 'application/octet-stream'}, content=data)
            previews = {}
            for name, data in files.items():
                r = imp(editor, name, data)
                self.assertEqual(r.status_code, 200, (name, r.text))
                body = r.json()
                self.assertEqual((body['to_create'], body['errors'], body['created']), (2, [], 0), name)
                previews[name] = body['preview']
            first = next(iter(previews.values()))
            self.assertTrue(all(p == first for p in previews.values()), previews)
            self.assertEqual((first[0]['starts_at'], first[0]['teacher_ids'], first[1]['status'], sorted(first[1]['group_ids'])),
                             ('2026-10-01T05:30:00Z', [T1], 'cancelled', sorted([GA, GB])))
            # Загрузка — всё или ничего; повтор того же файла дублей не создаёт.
            r = imp(editor, 'выгрузка_1с.csv', files['выгрузка_1с.csv'], dry=False)
            self.assertEqual((r.status_code, r.json()['created']), (200, 2), r.text)
            again = imp(editor, 'расписание.xlsx', files['расписание.xlsx'], dry=False).json()
            self.assertEqual((again['created'], again['duplicates']), (0, 2))
            week = self.c.get('/api/v1/schedule/events', headers=teacher,
                              params={'from': '2026-10-01T00:00:00Z', 'to': '2026-10-02T00:00:00Z'}).json()['items']
            self.assertEqual([e['title'] for e in week], ['Матанализ', 'Физика'])
            # Ошибки по строкам: при загрузке ничего не записывается.
            bad = json.dumps([{'Дата': '32.10.2026', 'Начало': '08:30', 'Конец': '10:05', 'Дисциплина': 'X', 'Группы': 'ИВТ-99',
                               'Преподаватели': 'Никто'},
                              {'date': '2026-10-02', 'start': '12:00', 'end': '11:00', 'title': 'Y', 'groups': 'ИВТ-21',
                               'teachers': T1}]).encode()
            check = imp(editor, 'bad.json', bad).json()
            self.assertEqual([e['row'] for e in check['errors']], [1, 2])
            self.assertIn('ИВТ-99', check['errors'][0]['message'])
            self.assertIn('Никто', check['errors'][0]['message'])
            self.assertIn('раньше начала', check['errors'][1]['message'])
            r = imp(editor, 'bad.json', bad, dry=False)
            self.assertEqual((r.status_code, r.json()['error']['code']), (422, 'INVALID_IMPORT'))
            # Неподдерживаемые типы и права.
            self.assertEqual(imp(editor, 'old.xls', b'x').json()['error']['code'], 'INVALID_FILE')
            self.assertEqual(imp(editor, 'x.csv', b'a;b\n1;2').json()['error']['code'], 'INVALID_FILE')
            self.assertEqual(imp(student, 'расписание.json', files['расписание.json']).status_code, 403)
            self.assertEqual(imp(teacher, 'расписание.json', files['расписание.json']).status_code, 403)

    def test_client_page(self):
        page = self.c.get('/schedule')
        self.assertEqual(page.status_code, 200)
        self.assertIn('frame-ancestors https://shell.test', page.headers['content-security-policy'])
        self.assertNotIn('__BOOT__', page.text)
        self.assertEqual(self.c.get('/assets/sdk-client.js').status_code, 200)
        self.assertEqual(self.c.get('/assets/..%2Fapp%2Fmain.py').status_code, 404)

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
