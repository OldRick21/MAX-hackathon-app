"""Сервис расписания против заглушки ядра: доступ по профилям, группы, занятия, версии, idempotency.

    cd services/schedule && python -m unittest discover -s tests -t . -v
"""
import os
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

_tmp = tempfile.TemporaryDirectory()
os.environ['SCHEDULE_DB'] = _tmp.name + '/schedule.db'
os.environ['SCHEDULE_PROVISIONING_TOKEN'] = 't' * 48
import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main as m  # noqa: E402

I = '44444444-4444-4444-8444-444444444444'
ADMIN, EDITOR, T1, T2, ST1, ST2, NOBODY = (str(uuid.UUID(int=n)) for n in range(1, 8))
PROFILES = {ADMIN: ['admin'], EDITOR: ['admin'], T1: ['teacher'], T2: ['teacher'],
            ST1: ['student'], ST2: ['student'], NOBODY: []}
EDITOR_PERMS = ['schedule.read_all', 'schedule.write', 'groups.manage']


def binding(service_id):
    return SimpleNamespace(service_id=service_id, institution_id=I, api_base_url='https://shell.test/schedule/api/v1',
                           client_base_url='https://shell.test')


class Schedule(unittest.TestCase):
    def setUp(self):
        self.actors = {}
        # Своя пара (вуз, экземпляр) на каждый тест: база общая на модуль.
        self.service = str(uuid.uuid4())
        patches = [
            patch.object(m.core, 'binding', side_effect=lambda sid, fresh=False: binding(sid)),
            patch.object(m.core, 'introspect', side_effect=self.introspect),
            patch.object(m.core, 'profiles', side_effect=lambda b, uid: PROFILES.get(uid, [])),
        ]
        for p in patches:
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
                'permissions': permissions}

    def as_(self, user, profile, permissions=(), service=None):
        service = service or self.service
        claims = {'service_id': service, 'sub': user, 'aud': 'service:' + service, 'token_use': 'service_access',
                  'profile': profile, 'sid': 'sid', 'parent_sid': 'psid'}
        token = jwt.encode(claims, 'x' * 32)
        self.actors[token] = (profile, list(permissions))
        return {'Authorization': 'Bearer ' + token}

    # --- помощники ---

    def post(self, path, h, body, key=None):
        return self.c.post(path, headers={**h, 'Idempotency-Key': key or str(uuid.uuid4())}, json=body)

    def group(self, h, name):
        r = self.post('/api/v1/schedule/groups', h, {'name': name})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()['id']

    def set_students(self, h, group_id, user_ids):
        etag = self.c.get(f'/api/v1/schedule/groups/{group_id}/students', headers=h).headers['etag']
        return self.c.put(f'/api/v1/schedule/groups/{group_id}/students', headers={**h, 'If-Match': etag},
                          json={'user_ids': user_ids})

    @staticmethod
    def event(groups, teachers, start='2026-09-28T06:00:00Z', end='2026-09-28T07:30:00Z', **extra):
        return {'title': 'Матанализ', 'starts_at': start, 'ends_at': end, 'group_ids': groups, 'teacher_ids': teachers,
                'location': '205', 'description': '', 'status': 'scheduled', **extra}

    def week(self, h, **filters):
        params = {'from': '2026-09-28T00:00:00Z', 'to': '2026-10-05T00:00:00Z', **filters}
        return self.c.get('/api/v1/schedule/events', headers=h, params=params)

    # --- сценарии ---

    def test_groups_and_students(self):
        editor = self.as_(EDITOR, 'admin', EDITOR_PERMS)
        plain_admin = self.as_(ADMIN, 'admin')
        teacher = self.as_(T1, 'teacher')
        student = self.as_(ST1, 'student')

        self.assertEqual(self.post('/api/v1/schedule/groups', plain_admin, {'name': 'ИВТ-1'}).status_code, 403)
        self.assertEqual(self.post('/api/v1/schedule/groups', teacher, {'name': 'ИВТ-1'}).status_code, 403)
        a = self.group(editor, 'ИВТ-1')
        b = self.group(editor, 'ИВТ-2')
        dup = self.post('/api/v1/schedule/groups', editor, {'name': '  ивт-1 '})
        self.assertEqual((dup.status_code, dup.json()['error']['code']), (409, 'GROUP_ALREADY_EXISTS'))

        # Состав: только студенты, один студент — одна группа.
        bad = self.set_students(editor, a, [ST1, T1])
        self.assertEqual((bad.status_code, bad.json()['error']['code']), (422, 'INVALID_REFERENCE'))
        self.assertEqual(self.set_students(editor, a, [ST1]).status_code, 200)
        clash = self.set_students(editor, b, [ST1, ST2])
        self.assertEqual((clash.status_code, clash.json()['error']['code']), (409, 'STUDENT_ALREADY_GROUPED'))

        # Устаревший ETag состава — 412, без If-Match — 428.
        stale = self.c.get(f'/api/v1/schedule/groups/{b}/students', headers=editor).headers['etag']
        self.assertEqual(self.set_students(editor, b, [ST2]).status_code, 200)
        self.assertEqual(self.c.put(f'/api/v1/schedule/groups/{b}/students', headers={**editor, 'If-Match': stale},
                                    json={'user_ids': []}).status_code, 412)
        self.assertEqual(self.c.put(f'/api/v1/schedule/groups/{b}/students', headers=editor,
                                    json={'user_ids': []}).status_code, 428)

        # Видимость групп: преподаватель — все, студент — только своя, admin без роли — 403.
        self.assertEqual(len(self.c.get('/api/v1/schedule/groups', headers=teacher).json()['items']), 2)
        self.assertEqual([g['id'] for g in self.c.get('/api/v1/schedule/groups', headers=student).json()['items']], [a])
        self.assertEqual(self.c.get(f'/api/v1/schedule/groups/{b}', headers=student).status_code, 404)
        self.assertEqual(self.c.get('/api/v1/schedule/groups', headers=plain_admin).status_code, 403)

        # Непустую группу удалить нельзя; после очистки — можно.
        g = self.c.get(f'/api/v1/schedule/groups/{a}', headers=editor)
        busy = self.c.delete(f'/api/v1/schedule/groups/{a}', headers={**editor, 'If-Match': g.headers['etag']})
        self.assertEqual((busy.status_code, busy.json()['error']['code']), (409, 'GROUP_IN_USE'))
        self.assertEqual(self.set_students(editor, a, []).status_code, 200)
        renamed = self.c.patch(f'/api/v1/schedule/groups/{a}', headers={**editor, 'If-Match': g.headers['etag']},
                               json={'name': 'ИВТ-11'})
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(self.c.delete(f'/api/v1/schedule/groups/{a}',
                                       headers={**editor, 'If-Match': g.headers['etag']}).status_code, 412)
        self.assertEqual(self.c.delete(f'/api/v1/schedule/groups/{a}',
                                       headers={**editor, 'If-Match': renamed.headers['etag']}).status_code, 204)

    def test_concurrent_grouping(self):
        editor = self.as_(EDITOR, 'admin', EDITOR_PERMS)
        a, b = self.group(editor, 'А'), self.group(editor, 'Б')
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda g: self.set_students(editor, g, [ST1]), [a, b]))
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])

    def test_teachers_write_own_events(self):
        editor = self.as_(EDITOR, 'admin', EDITOR_PERMS)
        t1, t2 = self.as_(T1, 'teacher'), self.as_(T2, 'teacher')
        st1, st2 = self.as_(ST1, 'student'), self.as_(ST2, 'student')
        a, b = self.group(editor, 'А'), self.group(editor, 'Б')
        self.set_students(editor, a, [ST1])

        # Студент без группы — пустое расписание, не ошибка.
        self.assertEqual(self.week(st2).json(), {'items': [], 'next_cursor': None})

        # Преподаватель создаёт занятие только с собой среди преподавателей.
        foreign = self.post('/api/v1/schedule/events', t1, self.event([a], [T2]))
        self.assertEqual(foreign.status_code, 422)
        self.assertEqual(self.post('/api/v1/schedule/events', st1, self.event([a], [T1])).status_code, 403)
        wrong = self.post('/api/v1/schedule/events', t1, self.event([a], [T1, ST1]))
        self.assertEqual((wrong.status_code, wrong.json()['error']['code']), (422, 'INVALID_REFERENCE'))
        backwards = self.post('/api/v1/schedule/events', t1, self.event([a], [T1], start='2026-09-28T08:00:00Z'))
        self.assertEqual((backwards.status_code, backwards.json()['error']['code']), (422, 'INVALID_TIME_RANGE'))
        missing = self.post('/api/v1/schedule/events', t1, self.event([str(uuid.uuid4())], [T1]))
        self.assertEqual((missing.status_code, missing.json()['error']['code']), (422, 'INVALID_REFERENCE'))

        created = self.post('/api/v1/schedule/events', t1, self.event([a], [T1]))
        self.assertEqual(created.status_code, 201, created.text)
        event = created.json()
        other = self.post('/api/v1/schedule/events', t2, self.event([b], [T2], start='2026-09-29T06:00:00+03:00',
                                                                     end='2026-09-29T07:30:00+03:00')).json()
        self.assertEqual(other['starts_at'], '2026-09-29T03:00:00Z')

        # Видимость: студент — своя группа, преподаватель — свои, редактор — все; фильтр не расширяет.
        self.assertEqual([e['id'] for e in self.week(st1).json()['items']], [event['id']])
        self.assertEqual([e['id'] for e in self.week(t1).json()['items']], [event['id']])
        self.assertEqual(self.week(t1, teacher_id=T2).json()['items'], [])
        self.assertEqual(len(self.week(editor).json()['items']), 2)
        self.assertEqual([e['id'] for e in self.week(editor, group_id=b).json()['items']], [other['id']])
        self.assertEqual(self.c.get(f'/api/v1/schedule/events/{other["id"]}', headers=t1).status_code, 404)
        self.assertEqual(self.c.get(f'/api/v1/schedule/events/{other["id"]}', headers=st1).status_code, 404)

        # Чужое занятие преподаватель не меняет и не удаляет: 404.
        other_tag = self.c.get(f'/api/v1/schedule/events/{other["id"]}', headers=editor).headers['etag']
        self.assertEqual(self.c.put(f'/api/v1/schedule/events/{other["id"]}', headers={**t1, 'If-Match': other_tag},
                                    json=self.event([b], [T1])).status_code, 404)
        self.assertEqual(self.c.delete(f'/api/v1/schedule/events/{other["id"]}',
                                       headers={**t1, 'If-Match': other_tag}).status_code, 404)

        # Своё — отменяет; отменённое видно студенту; устаревший ETag — 412.
        tag = created.headers['etag']
        cancelled = self.c.put(f'/api/v1/schedule/events/{event["id"]}', headers={**t1, 'If-Match': tag},
                               json=self.event([a], [T1, T2], status='cancelled'))
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(self.week(st1).json()['items'][0]['status'], 'cancelled')
        self.assertEqual(self.c.put(f'/api/v1/schedule/events/{event["id"]}', headers={**t1, 'If-Match': tag},
                                    json=self.event([a], [T1])).status_code, 412)
        # Соведущий T2 теперь тоже видит и может править занятие.
        self.assertEqual(len(self.week(t2).json()['items']), 2)

        # Используемую занятием группу удалить нельзя.
        g = self.c.get(f'/api/v1/schedule/groups/{b}', headers=editor)
        self.assertEqual(self.c.delete(f'/api/v1/schedule/groups/{b}',
                                       headers={**editor, 'If-Match': g.headers['etag']}).status_code, 409)

        # Редактор расписания правит и удаляет любое занятие.
        self.assertEqual(self.c.delete(f'/api/v1/schedule/events/{other["id"]}',
                                       headers={**editor, 'If-Match': other_tag}).status_code, 204)
        self.assertEqual(self.c.get(f'/api/v1/schedule/events/{other["id"]}', headers=editor).status_code, 404)

    def test_idempotency_range_and_paging(self):
        editor = self.as_(EDITOR, 'admin', EDITOR_PERMS)
        a = self.group(editor, 'А')
        key = str(uuid.uuid4())
        first = self.post('/api/v1/schedule/events', editor, self.event([a], [T1]), key)
        again = self.post('/api/v1/schedule/events', editor, self.event([a], [T1]), key)
        self.assertEqual((first.status_code, again.status_code), (201, 201))
        self.assertEqual(first.json()['id'], again.json()['id'])
        changed = self.post('/api/v1/schedule/events', editor, self.event([a], [T2]), key)
        self.assertEqual((changed.status_code, changed.json()['error']['code']), (409, 'IDEMPOTENCY_CONFLICT'))
        self.assertEqual(self.c.post('/api/v1/schedule/events', headers=editor, json=self.event([a], [T1])).status_code, 400)

        too_long = self.c.get('/api/v1/schedule/events', headers=editor,
                              params={'from': '2026-09-01T00:00:00Z', 'to': '2026-10-05T00:00:00Z'})
        self.assertEqual(too_long.json()['error']['code'], 'INVALID_TIME_RANGE')

        for day in range(1, 5):
            self.post('/api/v1/schedule/events', editor,
                      self.event([a], [T1], start=f'2026-09-2{day}T06:00:00Z', end=f'2026-09-2{day}T07:00:00Z'))
        seen, cursor = [], None
        while True:
            params = {'from': '2026-09-20T00:00:00Z', 'to': '2026-10-01T00:00:00Z', 'limit': 2}
            if cursor:
                params['cursor'] = cursor
            page = self.c.get('/api/v1/schedule/events', headers=editor, params=params).json()
            seen += [e['starts_at'] for e in page['items']]
            cursor = page['next_cursor']
            if not cursor:
                break
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(len(seen), 5)
        # Курсор другого пользователя не принимается.
        stolen = self.c.get('/api/v1/schedule/events', headers=self.as_(T1, 'teacher'),
                            params={'from': '2026-09-20T00:00:00Z', 'to': '2026-10-01T00:00:00Z', 'limit': 2,
                                    'cursor': self.c.get('/api/v1/schedule/events', headers=editor, params={
                                        'from': '2026-09-20T00:00:00Z', 'to': '2026-10-01T00:00:00Z', 'limit': 2,
                                    }).json()['next_cursor']})
        self.assertEqual(stolen.json()['error']['code'], 'INVALID_CURSOR')

    def test_tenants_are_isolated(self):
        editor = self.as_(EDITOR, 'admin', EDITOR_PERMS)
        other_editor = self.as_(EDITOR, 'admin', EDITOR_PERMS, service=str(uuid.uuid4()))
        a = self.group(editor, 'А')
        # Та же группа из чужого экземпляра не видна и не годится для занятий.
        self.assertEqual(self.c.get(f'/api/v1/schedule/groups/{a}', headers=other_editor).status_code, 404)
        r = self.post('/api/v1/schedule/events', other_editor, self.event([a], [T1]))
        self.assertEqual(r.json()['error']['code'], 'INVALID_REFERENCE')

    def test_core_down_and_session_checks(self):
        self.assertEqual(self.c.get('/api/v1/schedule/groups').status_code, 401)
        teacher = self.as_(T1, 'teacher')
        with patch.object(m.core, 'introspect', side_effect=m.CoreUnavailable('offline')):
            self.assertEqual(self.c.get('/api/v1/schedule/groups', headers=teacher).status_code, 503)
        with patch.object(m.core, 'binding', side_effect=m.BindingMissing(self.service)):
            self.assertEqual(self.c.get('/api/v1/schedule/groups', headers=teacher).status_code, 404)


if __name__ == '__main__':
    unittest.main()
