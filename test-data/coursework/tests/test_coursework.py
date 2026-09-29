"""Курсовые (свой сервис на SDK) против заглушки ядра: права, состояния, версии, файлы, idempotency, очистка.

    cd test-data/coursework && python -m unittest discover -s tests -t . -v
"""
import os
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

_tmp = tempfile.TemporaryDirectory()
os.environ.update(SERVICE_DATA=_tmp.name, CORE_URL='https://core.test', SERVICE_CLIENT_ID='cid',
                  SERVICE_CLIENT_SECRET='s' * 48, SERVICE_API_BASE_URL='https://cw.university.ru/api/v1',
                  SERVICE_CLIENT_BASE_URL='https://cw.university.ru', SHELL_ORIGIN='https://shell.test')
import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main as m, sdk  # noqa: E402

INST = str(uuid.uuid4())
ST1, ST2, T1, T2, ADMIN, DUAL = (str(uuid.UUID(int=n)) for n in range(1, 7))
PROFILES = {ST1: ['student'], ST2: ['student'], T1: ['teacher'], T2: ['teacher'], ADMIN: ['admin'],
            DUAL: ['student', 'teacher']}


def pdf(text='v1'):
    return (b'%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n% ' + text.encode() +
            b'\ntrailer << /Root 1 0 R >>\nstartxref\n9\n%%EOF\n')


class Coursework(unittest.TestCase):
    def setUp(self):
        self.actors = {}
        # Свой экземпляр на каждый тест: база и том общие на модуль.
        self.service = str(uuid.uuid4())
        binding = sdk.Binding(INST, self.service)
        patches = [
            patch.object(m.core, 'binding', return_value=binding),
            patch.object(m.core, 'introspect', side_effect=self.introspect),
            patch.object(m.core, 'profiles', side_effect=lambda uid: PROFILES.get(uid, [])),
            # Онбординг проверяется отдельно; здесь поток не должен ходить в сеть.
            patch.object(sdk, 'onboard', lambda *a: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        m.limiter = m.Limiter()
        self.ctx = TestClient(m.app)
        self.c = self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)

    def introspect(self, token):
        claims = jwt.decode(token, options={'verify_signature': False})
        profile, permissions = self.actors[token]
        return {'active': True, 'service_id': claims['service_id'], 'institution_id': INST, 'sub': claims['sub'],
                'profile': profile, 'session_id': 'sid', 'parent_session_id': 'psid', 'roles': [],
                'permissions': permissions}

    def as_(self, user, profile, permissions=(), service=None):
        service = service or self.service
        claims = {'service_id': service, 'sub': user, 'aud': 'service:' + service, 'token_use': 'service_access',
                  'profile': profile, 'sid': 'sid', 'parent_sid': 'psid', 'jti': str(uuid.uuid4())}
        token = jwt.encode(claims, 'x' * 32)
        self.actors[token] = (profile, list(permissions))
        return {'Authorization': 'Bearer ' + token}

    def upload(self, h, title='Анализ TLS', teacher=T1, data=None, key=None, name='../работа.pdf', mime='application/pdf'):
        return self.c.post('/api/v1/coursework/submissions',
                           headers={**h, 'Idempotency-Key': key or str(uuid.uuid4())},
                           data={'title': title, 'teacher_id': teacher},
                           files={'file': (name, data if data is not None else pdf(), mime)})

    def test_lifecycle_and_access(self):
        st1, st2 = self.as_(ST1, 'student'), self.as_(ST2, 'student')
        t1, t2 = self.as_(T1, 'teacher'), self.as_(T2, 'teacher')
        manager = self.as_(ADMIN, 'admin', ['coursework.manage'])
        plain_admin = self.as_(ADMIN, 'admin')

        self.assertEqual(self.upload(t1, teacher=T2).status_code, 403)
        self.assertEqual(self.upload(st1, teacher=ST2).json()['error']['code'], 'INVALID_REFERENCE')
        created = self.upload(st1)
        self.assertEqual(created.status_code, 201, created.text)
        work = created.json()
        self.assertEqual((work['status'], work['version'], work['student_id'], work['review']), ('submitted', 1, ST1, None))
        self.assertEqual(work['file']['original_name'], 'работа.pdf')
        path = f'/api/v1/coursework/submissions/{work["id"]}'

        # Видимость: автор, назначенный преподаватель и менеджер курсовых; чужим — 404.
        self.assertEqual(self.c.get(path, headers=st2).status_code, 404)
        self.assertEqual(self.c.get(path, headers=t2).status_code, 404)
        self.assertEqual(self.c.get(path, headers=t1).status_code, 200)
        self.assertEqual(self.c.get(path, headers=manager).status_code, 200)
        # Контракт: admin без coursework.manage работ не видит.
        self.assertEqual(self.c.get(path, headers=plain_admin).status_code, 404)
        self.assertEqual(self.c.get('/api/v1/coursework/submissions', headers=plain_admin).status_code, 403)
        listing = self.c.get('/api/v1/coursework/submissions', headers=t1).json()
        self.assertEqual(set(listing), {'items', 'next_cursor'})  # имён (people) в контракте нет
        self.assertEqual(len(self.c.get('/api/v1/coursework/submissions', headers=t1).json()['items']), 1)
        self.assertEqual(self.c.get('/api/v1/coursework/submissions', headers=t2).json()['items'], [])

        # Скачивание: только с доступом, attachment + nosniff, те же байты.
        file = self.c.get(path + '/file', headers=t1)
        self.assertEqual(file.content, pdf())
        self.assertTrue(file.headers['content-disposition'].startswith('attachment'))
        self.assertEqual(file.headers['x-content-type-options'], 'nosniff')
        self.assertEqual(self.c.get(path + '/file', headers=st2).status_code, 404)

        # Менеджер и чужой преподаватель не проверяют; назначенный — да.
        tag = self.c.get(path, headers=t1).headers['etag']
        verdict = {'decision': 'changes_requested', 'comment': 'Добавьте источники'}
        self.assertEqual(self.c.put(path + '/review', headers={**manager, 'If-Match': tag}, json=verdict).status_code, 403)
        self.assertEqual(self.c.put(path + '/review', headers={**t2, 'If-Match': tag}, json=verdict).status_code, 404)
        reviewed = self.c.put(path + '/review', headers={**t1, 'If-Match': tag}, json=verdict)
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertEqual(reviewed.json()['review']['reviewer_id'], T1)
        self.assertEqual(self.c.put(path + '/review', headers={**t1, 'If-Match': reviewed.headers['etag']},
                                    json=verdict).json()['error']['code'], 'INVALID_STATE')

        # Новая версия файла сбрасывает отзыв; старый ETag — 412.
        v2 = self.c.put(path + '/file', headers={**st1, 'If-Match': reviewed.headers['etag']},
                        files={'file': ('v2.pdf', pdf('v2'), 'application/pdf')})
        self.assertEqual(v2.status_code, 200, v2.text)
        self.assertEqual((v2.json()['version'], v2.json()['status'], v2.json()['review']), (2, 'submitted', None))
        self.assertEqual(self.c.put(path + '/review', headers={**t1, 'If-Match': reviewed.headers['etag']},
                                    json=verdict).status_code, 412)
        accepted = self.c.put(path + '/review', headers={**t1, 'If-Match': v2.headers['etag']},
                              json={'decision': 'accepted', 'comment': ''})
        self.assertEqual(accepted.json()['status'], 'accepted')

        # Принятую автор не меняет и не удаляет; менеджер удаляет.
        tag = accepted.headers['etag']
        self.assertEqual(self.c.put(path + '/file', headers={**st1, 'If-Match': tag},
                                    files={'file': ('v3.pdf', pdf('v3'), 'application/pdf')}).status_code, 409)
        self.assertEqual(self.c.delete(path, headers={**st1, 'If-Match': tag}).status_code, 409)
        self.assertEqual(self.c.delete(path, headers={**manager, 'If-Match': tag}).status_code, 204)
        self.assertEqual(self.c.get(path, headers=st1).status_code, 404)

    def test_self_review_forbidden(self):
        dual_student = self.as_(DUAL, 'student')
        dual_teacher = self.as_(DUAL, 'teacher')
        self.assertEqual(self.upload(dual_student, teacher=DUAL).json()['error']['code'], 'INVALID_REFERENCE')
        # Даже если бы работа существовала, самопроверка запрещена правилом «автор ≠ проверяющий».
        work = self.upload(dual_student, teacher=T1).json()
        tag = self.c.get(f'/api/v1/coursework/submissions/{work["id"]}', headers=dual_student).headers['etag']
        self.assertEqual(self.c.put(f'/api/v1/coursework/submissions/{work["id"]}/review',
                                    headers={**dual_teacher, 'If-Match': tag},
                                    json={'decision': 'accepted', 'comment': ''}).status_code, 404)

    def test_upload_validation_and_idempotency(self):
        st = self.as_(ST2, 'student')
        self.assertEqual(self.upload(st, data=b'not a pdf').status_code, 415)
        self.assertEqual(self.upload(st, mime='text/plain').status_code, 415)
        self.assertEqual(self.upload(st, data=b'').status_code, 415)
        huge = b'%PDF-1.4\n' + b'0' * (m.MAX_FILE + 10) + b'\nstartxref\n0\n%%EOF'
        self.assertEqual(self.upload(st, data=huge).status_code, 413)
        extra = self.c.post('/api/v1/coursework/submissions', headers={**st, 'Idempotency-Key': str(uuid.uuid4())},
                            data={'title': 'x', 'teacher_id': T1, 'student_id': ST1},
                            files={'file': ('a.pdf', pdf(), 'application/pdf')})
        self.assertEqual(extra.status_code, 422)
        self.assertFalse(list(m.TMP.glob('*.part')), 'временные файлы отклонённых загрузок удаляются')

        key = str(uuid.uuid4())
        first, again = self.upload(st, key=key), self.upload(st, key=key)
        self.assertEqual((first.status_code, again.status_code), (201, 201))
        self.assertEqual(first.json()['id'], again.json()['id'])
        other = self.upload(st, key=key, data=pdf('other'))
        self.assertEqual(other.json()['error']['code'], 'IDEMPOTENCY_CONFLICT')

    def test_concurrent_review_and_replace(self):
        st, t1 = self.as_(ST1, 'student'), self.as_(T1, 'teacher')
        work = self.upload(st).json()
        path = f'/api/v1/coursework/submissions/{work["id"]}'
        tag = self.c.get(path, headers=st).headers['etag']
        review = lambda: self.c.put(path + '/review', headers={**t1, 'If-Match': tag},
                                    json={'decision': 'accepted', 'comment': ''})
        replace = lambda: self.c.put(path + '/file', headers={**st, 'If-Match': tag},
                                     files={'file': ('n.pdf', pdf('new'), 'application/pdf')})
        with ThreadPoolExecutor(2) as pool:
            results = [f.result() for f in [pool.submit(review), pool.submit(replace)]]
        self.assertEqual(sorted(r.status_code for r in results), [200, 412])

    def test_rate_limit(self):
        st = self.as_(ST1, 'student')
        codes = [self.upload(st).status_code for _ in range(m.UPLOADS_PER_MINUTE + 1)]
        self.assertEqual(codes[-1], 429)
        self.assertTrue(all(c == 201 for c in codes[:-1]))

    def test_cleanup_keeps_current_file(self):
        st = self.as_(ST2, 'student')
        work = self.upload(st).json()
        path = f'/api/v1/coursework/submissions/{work["id"]}'
        tag = self.c.get(path, headers=st).headers['etag']
        self.c.put(path + '/file', headers={**st, 'If-Match': tag}, files={'file': ('n.pdf', pdf('n'), 'application/pdf')})
        stale = m.TMP / 'dead.part'
        stale.write_bytes(b'x')
        os.utime(stale, (time.time() - 3600, time.time() - 3600))
        with m.database() as db:
            db.execute('UPDATE cleanup SET not_before=0')
        m.cleanup_once()
        self.assertFalse(stale.exists())
        self.assertEqual(self.c.get(path + '/file', headers=st).content, pdf('n'))
        self.assertEqual(len([p for p in Path(m.FILES).rglob('*.pdf') if work['id'] in str(p)]), 1)

    def test_foreign_service_and_core_down(self):
        foreign = self.as_(ST1, 'student', service=str(uuid.uuid4()))
        self.assertEqual(self.c.get('/api/v1/coursework/submissions', headers=foreign).status_code, 404)
        self.assertEqual(self.c.get('/api/v1/coursework/submissions').status_code, 401)
        with patch.object(m.core, 'introspect', side_effect=sdk.CoreUnavailable('offline')):
            self.assertEqual(self.c.get('/api/v1/coursework/submissions', headers=self.as_(ST1, 'student')).status_code, 503)

    def test_cors_for_shell(self):
        pre = self.c.options('/api/v1/coursework/submissions', headers={
            'Origin': 'https://shell.test', 'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'authorization,idempotency-key'})
        self.assertEqual(pre.headers.get('access-control-allow-origin'), 'https://shell.test')
        evil = self.c.options('/api/v1/coursework/submissions', headers={
            'Origin': 'https://evil.test', 'Access-Control-Request-Method': 'GET'})
        self.assertNotEqual(evil.headers.get('access-control-allow-origin'), 'https://evil.test')


    def test_home_widgets(self):
        """Виджеты главной: своя работа студента, очередь преподавателя, сводка менеджера — с правами списка."""
        st1, st2, t1, t2 = self.as_(ST1, 'student'), self.as_(ST2, 'student'), self.as_(T1, 'teacher'), self.as_(T2, 'teacher')
        manager, plain_admin = self.as_(ADMIN, 'admin', ['coursework.manage']), self.as_(ADMIN, 'admin')
        self.assertEqual(self.upload(st1, title='Анализ TLS').status_code, 201)
        self.assertEqual(self.upload(st2, title='Чужая работа', teacher=T2).status_code, 201)
        mine = self.c.get('/api/v1/coursework/widgets/my-work', headers=st1).json()
        self.assertEqual((mine['kind'], mine['total'], [i['title'] for i in mine['items']], mine['items'][0]['badge']),
                         ('list', 1, ['Анализ TLS'], 'на проверке'))
        queue = self.c.get('/api/v1/coursework/widgets/to-review', headers=t1).json()
        self.assertEqual((queue['kind'], queue['value'], queue['unit'], queue['tone']), ('stat', 1, 'работа', 'warning'))
        self.assertEqual(self.c.get('/api/v1/coursework/widgets/all', headers=manager).json()['value'], 2)
        for path, h in (('my-work', t1), ('to-review', st1), ('all', plain_admin), ('all', t2)):
            self.assertEqual(self.c.get(f'/api/v1/coursework/widgets/{path}', headers=h).status_code, 403, path)
        self.assertEqual([w['id'] for w in m.MANIFEST['widgets']], ['my_work', 'to_review', 'all_works'])

    def test_no_non_contract_endpoints(self):
        student = self.as_(ST1, 'student')
        for path in ('/api/v1/coursework/me', '/api/v1/coursework/teachers'):
            self.assertIn(self.c.get(path, headers=student).status_code, (404, 405))

    def test_client_page(self):
        page = self.c.get('/coursework')
        self.assertIn('frame-ancestors https://shell.test', page.headers['content-security-policy'])
        self.assertNotIn('__BOOT__', page.text)
        self.assertEqual(self.c.get('/assets/app.js').status_code, 200)


if __name__ == '__main__':
    unittest.main()

# Unit suites stub the core privacy transport, just like token introspection.
def setUpModule():
    from unittest.mock import patch
    stub = patch('app.privacy_protocol.PrivacyGuard.synchronize', return_value=None)
    stub.start()
    unittest.addModuleCleanup(stub.stop)

