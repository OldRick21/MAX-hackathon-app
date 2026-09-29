import asyncio
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
def load(path):
    spec = importlib.util.spec_from_file_location('privacy_test_module', ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

Guard = load('services/privacy_protocol.py').PrivacyGuard

class Protocol(unittest.IsolatedAsyncioTestCase):
    async def test_retry_after_lost_ack_and_fail_closed(self):
        applied, served = [], []
        task = {'subject_id': str(uuid.uuid4()), 'institution_id': 'i',
                'task_id': str(uuid.uuid4()), 'action': 'restrict_and_erase'}
        async def app(scope, receive, send):
            served.append(True)
        guard = Guard(app, lambda *args: applied.append(args), lambda: None)
        guard.institution_id, guard.service_id = 'i', 's'
        responses = []
        async def send(message): responses.append(message)
        async def receive(): return {'type': 'http.request', 'body': b''}
        scope = {'type': 'http', 'path': '/api/v1/profile/users', 'method': 'GET', 'headers': []}
        def transport(method, path):
            if method == 'GET': return {'items': [task]}
            raise ConnectionError('lost ack')
        guard.request = transport
        await guard(scope, receive, send)
        self.assertEqual(responses[0]['status'], 503)
        self.assertEqual(served, [])
        self.assertEqual(len(applied), 1)
        guard.request = lambda method, path: {'items': [task]} if method == 'GET' else {'status': 'completed'}
        await guard(scope, receive, send)
        self.assertEqual(len(applied), 2)
        self.assertEqual(served, [True])

    async def test_cleanup_waits_for_inflight_write_and_precedes_next_read(self):
        started, finish = asyncio.Event(), asyncio.Event()
        events, tasks = [], []
        calls = 0
        async def app(scope, receive, send):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await finish.wait()
                events.append('write')
            else:
                events.append('read')
        guard = Guard(app, lambda *args: events.append('erase'), lambda: None)
        guard.institution_id, guard.service_id = 'i', 's'
        guard.request = lambda method, path: {'items': list(tasks)} if method == 'GET' else tasks.clear()
        async def send(message): pass
        async def receive(): return {'type': 'http.request', 'body': b''}
        scope = {'type': 'http', 'path': '/api/v1/data', 'method': 'POST'}
        first = asyncio.create_task(guard(scope, receive, send))
        await started.wait()
        tasks.append({'subject_id': str(uuid.uuid4()), 'institution_id': 'i',
                      'task_id': str(uuid.uuid4()), 'action': 'restrict_and_erase'})
        second = asyncio.create_task(guard(scope, receive, send))
        finish.set()
        await asyncio.gather(first, second)
        self.assertEqual(events, ['write', 'erase', 'read'])

class Cleanup(unittest.TestCase):
    def test_vendored_sdk_copies_match(self):
        reference = (ROOT / 'services/privacy_protocol.py').read_bytes()
        for path in ['services/user-profile/app', 'services/schedule/app', 'services/administration/app', 'test-data/coursework/app']:
            self.assertEqual((ROOT / path / 'privacy_protocol.py').read_bytes(), reference, path)

    def test_profile_cleanup_is_tenant_scoped_and_repeatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            @contextmanager
            def database():
                db = sqlite3.connect(Path(tmp) / 'profiles.db')
                try: yield db
                finally: db.close()
            with database() as db:
                db.execute('CREATE TABLE cards (institution_id TEXT, service_id TEXT, user_id TEXT, display_name TEXT)')
                db.executemany('INSERT INTO cards VALUES (?,?,?,?)', [('i','s','u','Name'), ('other','s','u','Other')])
                db.commit()
            fake = ModuleType('app.main')
            fake.database = database
            with patch.dict(sys.modules, {'app': ModuleType('app'), 'app.main': fake}):
                erase = load('services/user-profile/app/privacy_cleanup.py').erase
                erase('u', 'i', 's'); erase('u', 'i', 's')
            with database() as db:
                self.assertEqual(db.execute('SELECT display_name FROM cards').fetchall(), [('Other',)])

    def test_coursework_removes_accepted_metadata_and_all_file_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); files = root / 'files'; temp = root / 'tmp'
            temp.mkdir(); directory = files / 'i' / 's' / 'submission'; directory.mkdir(parents=True)
            (directory / '1-old.pdf').write_bytes(b'old personal data')
            (directory / '2-current.pdf').write_bytes(b'new personal data')
            @contextmanager
            def database():
                db = sqlite3.connect(root / 'coursework.db'); db.row_factory = sqlite3.Row
                try: yield db
                finally: db.close()
            with database() as db:
                db.executescript('CREATE TABLE submissions (id TEXT, institution_id TEXT, service_id TEXT, student_id TEXT, teacher_id TEXT, reviewer_id TEXT, file_key TEXT); CREATE TABLE idempotency (institution_id TEXT, service_id TEXT);')
                db.execute('INSERT INTO submissions VALUES (?,?,?,?,?,?,?)', ('submission','i','s','u','teacher','teacher','i/s/submission/2-current.pdf'))
                db.commit()
            fake = ModuleType('app.main'); fake.database = database; fake.FILES = files; fake.TMP = temp; fake.cleanup_once = lambda: None
            with patch.dict(sys.modules, {'app': ModuleType('app'), 'app.main': fake}):
                erase = load('test-data/coursework/app/privacy_cleanup.py').erase
                erase('u', 'i', 's'); erase('u', 'i', 's')
            self.assertEqual(list(files.rglob('*.pdf')), [])
            with database() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0], 0)
