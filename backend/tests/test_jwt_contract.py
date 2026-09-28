"""JWT contract regressions; run this module in its own process (isolated settings)."""
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

_tmp = tempfile.TemporaryDirectory()
os.environ.update(DATABASE_URL=f'sqlite:///{_tmp.name}/jwt.db', JWT_ISSUER='https://core.test',
                  JWT_KEYRING_PATH=f'{_tmp.name}/keys.json', CURSOR_SECRET_KEY='c' * 48,
                  ALLOW_DEV_LOGIN='true', SEED_DEMO_DATA='true', CLOUD_BINDING_KEY='b' * 48,
                  ADMINISTRATION_PROVISIONING_TOKEN='p' * 48, SERVICE_CONFIG_DIR='')

import jwt
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from main import app
from auth.security import security, timestamp, COMMON, EXTRA
from auth.keys import KeyRing, KeyStoreUnavailable
from auth.models import RefreshUse
from database.create_tables import session_local, engine
from database.tables import CoreSession, ServiceSession, ServiceCredential, Institution, Membership, utc_now

IID = '46bf3278-29fa-4d0a-a711-e4a6a324ed31'
SID = 'b4c51283-fbdf-44a2-91e8-7f3d07f52e1a'
CLIENT = '22370780-1c30-4de9-959e-b474475274c7'
BASE = f'/api/v1/institution/{IID}/service/{SID}/session'


def claims(token):
    return jwt.decode(token, options={'verify_signature': False})


class JWTContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = TestClient(app)
        cls.c = cls.context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)
        engine.dispose()
        _tmp.cleanup()

    def setUp(self):
        self.core = self.login()
        self.uid = claims(self.core['access_token'])['sub']
        self.headers = {'Authorization': 'Bearer ' + self.core['access_token']}
        with session_local() as db:
            db.add(Membership(institution_id=IID, user_id=self.uid, profiles=['student', 'teacher', 'admin']))
            db.commit()

    def login(self):
        response = self.c.post('/api/v1/auth/token', json={'max_user_id': str(uuid4().int)[:30]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def child(self, profile='student'):
        response = self.c.post(BASE, headers=self.headers, json={'profile': profile})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(response.headers['Location'].endswith(response.json()['session_id']))
        return response.json()

    def machine(self):
        response = self.c.post('/api/v1/internal/auth/token', auth=(CLIENT, 'service_super_secret_key_123'),
                               json={'grant_type': 'client_credentials'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def introspect(self, token, machine=None):
        machine = machine or self.machine()
        return self.c.post('/api/v1/internal/auth/introspect', json={'token': token},
                           headers={'Authorization': 'Bearer ' + machine['access_token']})

    def refresh(self, pair, child=False):
        return self.c.post(BASE + '/refresh' if child else '/api/v1/auth/refresh',
                           json={'refresh_token': pair['refresh_token']})

    def test_all_claims_public_signature_and_key_separation(self):
        child, machine = self.child(), self.machine()
        jwks = self.c.get('/api/v1/internal/auth/jwks')
        self.assertEqual(jwks.headers['Cache-Control'], 'public, max-age=300')
        keys = {k['kid']: k for k in jwks.json()['keys']}
        for key in keys.values():
            self.assertEqual(set(key), {'kty', 'use', 'alg', 'kid', 'n', 'e'})
        for pair, prefix in [(self.core, 'core'), (child, 'service'), (machine, 'machine')]:
            for field in ['access_token'] + ([] if prefix == 'machine' else ['refresh_token']):
                token, kind = pair[field], prefix + '_' + field.split('_')[0]
                data, header = claims(token), jwt.get_unverified_header(token)
                self.assertTrue((COMMON | EXTRA[kind]) <= data.keys())
                self.assertNotIn('session_id', data)
                self.assertNotIn('parent_session_id', data)
                self.assertEqual(data['iss'], 'https://core.test')
                if field == 'access_token':
                    jwt.decode(token, jwt.PyJWK.from_dict(keys[header['kid']]).key, algorithms=['RS256'],
                               audience=data['aud'], issuer='https://core.test')
                else:
                    self.assertNotIn(header['kid'], keys)
                    self.assertEqual(data['aud'], 'core-refresh' if prefix == 'core' else 'core-service-refresh')
        self.assertNotIn('credential_id', machine)

    def test_strict_claim_validation_and_wrong_key(self):
        original = claims(self.core['access_token'])
        variants = [{k: v for k, v in original.items() if k != missing} for missing in COMMON | EXTRA['core_access']]
        variants += [dict(original, **change) for change in (
            {'aud': ['core-api']}, {'aud': 'core-refresh'}, {'iss': 'https://wrong.test'},
            {'sub': str(uuid4())}, {'sid': str(uuid4())}, {'jti': 'bad'}, {'iat': True},
            {'nbf': original['iat'] + 60}, {'exp': original['iat'] - 31},
            {'token_use': 'core_refresh'}, {'exp': original['iat'] + 601})]
        for data in variants:
            token = security.keys.sign(data, 'access')
            with self.subTest(data=data):
                self.assertEqual(self.c.get('/api/v1/auth/me', headers={'Authorization': 'Bearer ' + token}).status_code, 401)
        for token in [security.keys.sign(original, 'refresh'),
                      jwt.encode(original, 'fake-secret-' * 8, algorithm='HS256', headers={'kid': str(uuid4())})]:
            self.assertEqual(self.c.get('/api/v1/auth/me', headers={'Authorization': 'Bearer ' + token}).status_code, 401)

    def test_refresh_absolute_expiry_hash_history_and_reuse_cascade(self):
        child = self.child()
        unrelated = self.login()
        old = claims(self.core['refresh_token'])
        renewed = self.refresh(self.core)
        self.assertEqual(renewed.status_code, 200, renewed.text)
        new = claims(renewed.json()['refresh_token'])
        self.assertEqual((new['exp'], new['family_id'], new['sid']), (old['exp'], old['family_id'], old['sid']))
        self.assertNotEqual(new['jti'], old['jti'])
        with session_local() as db:
            row = db.get(RefreshUse, old['jti'])
            self.assertIsNotNone(row.used_at)
            self.assertEqual(len(row.token_hash), 64)
            self.assertEqual(timestamp(row.expires_at), old['exp'] + 30)
        self.assertEqual(self.refresh(self.core).status_code, 401)
        self.assertEqual(self.refresh(renewed.json()).status_code, 401)
        self.assertFalse(self.introspect(child['access_token']).json()['active'])
        self.assertEqual(self.c.get('/api/v1/auth/me', headers=self.headers).status_code, 401)
        self.assertEqual(self.refresh(unrelated).status_code, 200)
        with session_local() as db:
            self.assertTrue(db.get(ServiceSession, child['session_id']).is_revoked)

    def test_simultaneous_refresh_one_winner_then_family_revoked(self):
        barrier = Barrier(2)
        def refresh():
            barrier.wait(timeout=5)
            return self.refresh(self.core)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: refresh(), range(2)))
        self.assertEqual(sorted(r.status_code for r in results), [200, 401], [r.text for r in results])
        winner = next(r.json() for r in results if r.status_code == 200)
        self.assertEqual(self.refresh(winner).status_code, 401)

    def test_service_reuse_only_revokes_one_child(self):
        student, teacher = self.child(), self.child('teacher')
        old = claims(student['refresh_token'])
        renewed = self.refresh(student, True)
        self.assertEqual(renewed.status_code, 200, renewed.text)
        new = claims(renewed.json()['refresh_token'])
        self.assertEqual((new['exp'], new['family_id']), (old['exp'], old['family_id']))
        self.assertEqual(self.refresh(student, True).status_code, 401)
        self.assertFalse(self.introspect(renewed.json()['access_token']).json()['active'])
        self.assertTrue(self.introspect(teacher['access_token']).json()['active'])
        self.assertEqual(self.refresh(self.core).status_code, 200)

    def test_child_and_access_deadlines_bound_to_parent(self):
        with session_local() as db:
            parent = db.get(CoreSession, self.core['session_id'])
            parent.expires_at = utc_now().replace(microsecond=0) + timedelta(seconds=120)
            # Mint a matching short-lived core pair within this transaction.
            from auth.service import pair
            short = pair(db, parent)
            db.commit()
        self.headers = {'Authorization': 'Bearer ' + short['access_token']}
        child = self.child()
        parent_exp = claims(short['refresh_token'])['exp']
        self.assertLessEqual(child['refresh_expires_in'], 120)
        for field in ('access_token', 'refresh_token'):
            self.assertEqual(claims(child[field])['exp'], parent_exp)

    def test_logout_only_accepts_core_refresh_and_is_idempotent(self):
        child = self.child()
        for token in (self.core['access_token'], child['refresh_token'], 'garbage'):
            self.assertEqual(self.c.post('/api/v1/auth/logout', json={'refresh_token': token}).status_code, 204)
            self.assertEqual(self.c.get('/api/v1/auth/me', headers=self.headers).status_code, 200)
        for _ in range(2):
            self.assertEqual(self.c.post('/api/v1/auth/logout', json={'refresh_token': self.core['refresh_token']}).status_code, 204)
        self.assertFalse(self.introspect(child['access_token']).json()['active'])
        self.assertEqual(self.c.get('/api/v1/auth/me', headers=self.headers).status_code, 401)

    def test_delete_binds_complete_path(self):
        child = self.child()
        for wrong in (BASE.replace(IID, str(uuid4())), BASE.replace(SID, str(uuid4()))):
            self.assertEqual(self.c.delete(wrong + '/' + child['session_id'], headers=self.headers).status_code, 404)
        self.assertTrue(self.introspect(child['access_token']).json()['active'])
        self.assertEqual(self.c.delete(BASE + '/' + child['session_id'], headers=self.headers).status_code, 204)
        self.assertFalse(self.introspect(child['access_token']).json()['active'])

    def test_introspection_type_tenant_and_live_membership(self):
        child, machine = self.child(), self.machine()
        for token in (self.core['access_token'], self.core['refresh_token'], child['refresh_token'], machine['access_token'], 'bad'):
            self.assertEqual(self.introspect(token, machine).json(), {'active': False})
        for field in ('sub', 'sid', 'parent_sid', 'institution_id', 'service_id'):
            data = dict(claims(child['access_token']), **{field: str(uuid4())})
            self.assertEqual(self.introspect(security.keys.sign(data, 'access'), machine).json(), {'active': False})
        with session_local() as db:
            db.get(Membership, (IID, self.uid)).profiles = ['teacher']
            db.commit()
        self.assertFalse(self.introspect(child['access_token'], machine).json()['active'])
        self.assertEqual(self.refresh(child, True).status_code, 401)

    def test_machine_correlations_revocation_and_institution_status(self):
        machine, child = self.machine(), self.child()
        for field in ('sub', 'credential_id', 'institution_id', 'service_id'):
            data = dict(claims(machine['access_token']), **{field: str(uuid4())})
            self.assertEqual(self.introspect(child['access_token'], {'access_token': security.keys.sign(data, 'access')}).status_code, 401)
        with session_local() as db:
            credential = db.query(ServiceCredential).filter_by(client_id=CLIENT).one()
            credential.revoked_at = utc_now()
            db.commit()
        try:
            self.assertEqual(self.introspect(child['access_token'], machine).status_code, 401)
        finally:
            with session_local() as db:
                db.query(ServiceCredential).filter_by(client_id=CLIENT).update({'revoked_at': None})
                db.commit()
        with session_local() as db:
            db.get(Institution, IID).status = 'suspended'
            db.commit()
        try:
            self.assertEqual(self.introspect(child['access_token'], machine).status_code, 401)
            response = self.c.post('/api/v1/internal/auth/token', auth=(CLIENT, 'service_super_secret_key_123'),
                                   json={'grant_type': 'client_credentials'})
            self.assertEqual(response.status_code, 409)
        finally:
            with session_local() as db:
                db.get(Institution, IID).status = 'active'
                db.commit()

    def test_storage_failure_is_503_and_refresh_rolls_back(self):
        with patch('auth.service.lock_core', side_effect=OperationalError('query', {}, Exception('offline'))):
            self.assertEqual(self.c.post('/api/v1/auth/logout', json={'refresh_token': self.core['refresh_token']}).status_code, 503)
        with patch.object(KeyRing, 'sign', side_effect=KeyStoreUnavailable('offline')):
            self.assertEqual(self.refresh(self.core).status_code, 503)
        self.assertEqual(self.refresh(self.core).status_code, 200)

    def test_unknown_or_modified_refresh_does_not_revoke(self):
        original = claims(self.core['refresh_token'])
        for change in ({'jti': str(uuid4())}, {'sub': str(uuid4())}, {'family_id': str(uuid4())},
                       {'exp': original['exp'] - 1}):
            token = security.keys.sign(dict(original, **change), 'refresh')
            self.assertEqual(self.c.post('/api/v1/auth/refresh', json={'refresh_token': token}).status_code, 401)
        self.assertEqual(self.refresh(self.core).status_code, 200)

    def test_service_refresh_concurrency(self):
        child = self.child()
        barrier = Barrier(2)
        def refresh():
            barrier.wait(timeout=5)
            return self.refresh(child, True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: refresh(), range(2)))
        self.assertEqual(sorted(r.status_code for r in results), [200, 401], [r.text for r in results])
        self.assertFalse(self.introspect(child['access_token']).json()['active'])
        self.assertEqual(self.c.get('/api/v1/auth/me', headers=self.headers).status_code, 200)

    def test_permissions_recomputed_on_refresh_and_introspection(self):
        machine = self.machine()
        headers = {'Authorization': 'Bearer ' + machine['access_token']}
        role = 'reader_' + uuid4().hex[:8]
        base = f'/api/v1/internal/service/{SID}'
        created = self.c.post(base + '/roles', headers=headers, json={
            'code': role, 'titles': {'ru': 'Reader'}, 'allowed_profiles': ['student'], 'permissions': []})
        self.assertEqual(created.status_code, 201, created.text)
        assigned = self.c.put(f'{base}/users/{self.uid}/profiles/student/roles', headers=headers, json={'roles': [role]})
        self.assertEqual(assigned.status_code, 200, assigned.text)
        child = self.child()
        self.assertEqual(claims(child['access_token'])['permissions'], [])
        updated = self.c.patch(base + '/roles/' + role, headers=headers, json={'permissions': ['people.manage']})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(self.introspect(child['access_token'], machine).json()['permissions'], ['people.manage'])
        renewed = self.refresh(child, True)
        self.assertEqual(renewed.status_code, 200, renewed.text)
        self.assertEqual(claims(renewed.json()['access_token'])['permissions'], ['people.manage'])

    def test_expired_session_not_revived_by_jwt_skew(self):
        child = self.child()
        with session_local() as db:
            db.get(CoreSession, self.core['session_id']).expires_at = utc_now() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.c.get('/api/v1/auth/me', headers=self.headers).status_code, 401)
        self.assertEqual(self.refresh(self.core).status_code, 401)
        self.assertEqual(self.refresh(child, True).status_code, 401)
        self.assertFalse(self.introspect(child['access_token']).json()['active'])

    def test_key_rotation_warmup_retention_restart(self):
        ring = KeyRing(f'{_tmp.name}/rotation-{uuid4()}.json')
        ring.initialize()
        old_access = ring.jwks()['keys'][0]['kid']
        old_refresh = next(r['kid'] for r in ring.status() if r['purpose'] == 'refresh')
        now = int(time.time())
        with patch('auth.keys.time.time', return_value=now):
            kid = ring.prepare('access')
            refresh = ring.prepare('refresh')
            self.assertIn(kid, {k['kid'] for k in ring.jwks()['keys']})
            with self.assertRaises(ValueError):
                ring.activate(kid)
        with patch('auth.keys.time.time', return_value=now + 300):
            ring.activate(kid)
            ring.activate(refresh)
            token = ring.sign({'sub': 'test'}, 'access')
            self.assertEqual(jwt.get_unverified_header(token)['kid'], kid)
            restarted = KeyRing(ring.path)
            restarted.initialize()
            self.assertEqual(restarted.jwks(), ring.jwks())
        with patch('auth.keys.time.time', return_value=now + 1230):
            self.assertNotIn(old_access, ring.prune())
        with patch('auth.keys.time.time', return_value=now + 1231):
            self.assertIn(old_access, ring.prune())
            ring.verification_key(old_refresh, 'refresh')
        with patch('auth.keys.time.time', return_value=now + 300 + 604831):
            self.assertIn(old_refresh, ring.prune())


if __name__ == '__main__':
    unittest.main()
