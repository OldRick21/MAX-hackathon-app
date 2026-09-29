"""Run in isolation: python -m unittest tests.test_privacy -v."""
import os
import tempfile
import unittest
import uuid
from datetime import timedelta

_tmp = tempfile.TemporaryDirectory()
os.environ.update(DATABASE_URL=f'sqlite:///{_tmp.name}/core.db', JWT_ISSUER='https://core.test',
    JWT_KEYRING_PATH=f'{_tmp.name}/keys.json', CURSOR_SECRET_KEY='test-' * 10,
    ALLOW_DEV_LOGIN='true', SEED_DEMO_DATA='false', ALLOW_FAKE_REDIS='true', REDIS_PORT='1',
    RATE_LIMIT_LOGIN='0', RATE_LIMIT_USER='0', RATE_LIMIT_CREDENTIAL='0', RATE_LIMIT_REFRESH='0',
    RATE_LIMIT_MACHINE_EXCHANGE='0', SERVICE_CONFIG_DIR='', CLOUD_BINDING_KEY='b' * 48,
    ADMINISTRATION_PROVISIONING_TOKEN='a' * 48, SCHEDULE_PROVISIONING_TOKEN='s' * 48,
    USER_PROFILE_PROVISIONING_TOKEN='u' * 48)
from fastapi.testclient import TestClient
from main import app
from database.create_tables import session_local, engine
from database.tables import User, Institution, Membership, ServiceInstance, ServiceCredential, utc_now
from auth.security import hash_password
from privacy import Consent, Erasure, ErasureTask, Challenge, VERSION, housekeeping

class PrivacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = TestClient(app)
        cls.client = cls.ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        engine.dispose()
        _tmp.cleanup()

    def login(self, name=None):
        name = name or uuid.uuid4().hex
        challenge = self.client.post('/api/v1/privacy/challenge').json()
        r = self.client.post('/api/v1/auth/token', json={'username': name,
            'consent_version': challenge['version'], 'consent_challenge': challenge['challenge']})
        self.assertEqual(r.status_code, 200, r.text)
        pair = r.json()
        headers = {'Authorization': 'Bearer ' + pair['access_token']}
        uid = self.client.get('/api/v1/auth/me', headers=headers).json()['id']
        return name, uid, headers, pair

    def service(self, uid):
        with session_local() as db:
            iid, sid, cid = (str(uuid.uuid4()) for _ in range(3))
            db.add(Institution(id=iid, titles={'ru': 'Test'}, status='active'))
            db.flush()
            db.add(Membership(user_id=uid, institution_id=iid, profiles=['student']))
            db.add(ServiceInstance(id=sid, institution_id=iid, service_type='user-profile',
                api_base_url='https://service.test/api/v1', client_base_url='https://service.test',
                deployment='local', supported_profiles=['student']))
            db.flush()
            db.add(ServiceCredential(client_id=cid, service_id=sid, hashed_secret=hash_password('secret')))
            db.commit()
        result = self.client.post('/api/v1/internal/auth/token', auth=(cid, 'secret'), json={'grant_type': 'client_credentials'})
        self.assertEqual(result.status_code, 200, result.text)
        return iid, sid, {'Authorization': 'Bearer ' + result.json()['access_token']}

    def test_no_consent_leaves_no_user_including_max_nested_savepoint(self):
        for payload in ({'username': 'no-consent'}, {'max_user_id': '999112233'}):
            result = self.client.post('/api/v1/auth/token', json=payload)
            self.assertEqual(result.status_code, 403, result.text)
        with session_local() as db:
            self.assertIsNone(db.query(User).filter_by(username='no-consent').first())
            self.assertIsNone(db.query(User).filter_by(max_user_id='999112233').first())

    def test_document_evidence_and_challenge_cannot_be_replayed(self):
        challenge = self.client.post('/api/v1/privacy/challenge').json()
        body = {'username': uuid.uuid4().hex, 'consent_version': VERSION,
                'consent_challenge': challenge['challenge']}
        self.assertEqual(self.client.post('/api/v1/auth/token', json=body).status_code, 200)
        body['username'] = uuid.uuid4().hex
        self.assertEqual(self.client.post('/api/v1/auth/token', json=body).status_code, 409)
        with session_local() as db:
            self.assertIsNone(db.query(User).filter_by(username=body['username']).first())

    def test_withdraw_all_sessions_scoped_tasks_retry_and_new_identity(self):
        name, uid, headers, pair = self.login()
        second = self.client.post('/api/v1/auth/token', json={'username': name}).json()
        iid, sid, machine = self.service(uid)
        result = self.client.post(f'/api/v1/institution/{iid}/service/{sid}/session',
            headers=headers, json={'profile': 'student'})
        self.assertEqual(result.status_code, 201, result.text)
        service_pair = result.json()
        receipt = self.client.post('/api/v1/privacy/withdraw', headers=headers)
        self.assertEqual(receipt.status_code, 200, receipt.text)
        receipt_headers = {'Authorization': 'Bearer ' + receipt.json()['receipt']}
        for p in (pair, second):
            self.assertEqual(self.client.post('/api/v1/auth/refresh', json={'refresh_token': p['refresh_token']}).status_code, 401)
        self.assertFalse(self.client.post('/api/v1/internal/auth/introspect', headers=machine,
                        json={'token': service_pair['access_token']}).json()['active'])
        self.assertEqual(self.client.get('/api/v1/privacy/erasure-status', headers=receipt_headers).json()['status'], 'pending')
        task = self.client.get('/api/v1/internal/privacy/tasks', headers=machine).json()['items'][0]
        _, other_uid, _, _ = self.login()
        _, _, other_machine = self.service(other_uid)
        path = f"/api/v1/internal/privacy/tasks/{task['task_id']}/complete"
        self.assertEqual(self.client.post(path, headers=other_machine).status_code, 404)
        for _ in range(2):
            self.assertEqual(self.client.post(path, headers=machine).status_code, 200)
        self.assertEqual(self.client.get('/api/v1/privacy/erasure-status', headers=receipt_headers).json()['status'], 'completed')
        self.assertEqual(self.client.post('/api/v1/auth/token', json={'username': name}).status_code, 403)
        _, new_uid, _, _ = self.login(name)
        self.assertNotEqual(uid, new_uid)
        with session_local() as db:
            self.assertIsNone(db.get(User, uid))
            self.assertIsNotNone(db.get(Consent, uid).withdrawn_at)

    def test_retention_never_discards_pending_tasks(self):
        _, uid, headers, _ = self.login()
        self.service(uid)
        self.client.post('/api/v1/privacy/withdraw', headers=headers)
        with session_local() as db:
            job = db.query(Erasure).filter_by(subject_id=uid).one()
            job.created_at = utc_now() - timedelta(days=90)
            db.commit()
        housekeeping()
        with session_local() as db:
            self.assertIsNotNone(db.query(Erasure).filter_by(subject_id=uid).first())
            self.assertIsNotNone(db.query(ErasureTask).filter_by(subject_id=uid).first())

    def test_request_registered(self):
        _, _, headers, _ = self.login()
        r = self.client.post('/api/v1/privacy/requests', headers=headers, json={'message': 'Предоставьте мои данные'})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()['status'], 'pending')
