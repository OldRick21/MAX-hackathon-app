"""Isolated MAX login tests: python -m unittest discover -s tests -p test_max_integration.py."""
import os
import tempfile
import unittest
import time
import json
import hashlib
import hmac
from urllib.parse import urlencode

_tmp = tempfile.TemporaryDirectory()
os.environ.update(DATABASE_URL=f"sqlite:///{_tmp.name}/test.db", JWT_ISSUER="https://core.test", JWT_KEYRING_PATH=f"{_tmp.name}/keys.json", CURSOR_SECRET_KEY="test-secret-" * 6,
                  MAX_BOT_TOKEN="integration-test-token", ALLOW_DEV_LOGIN="false",
                  CLOUD_BINDING_KEY="b" * 48, ADMINISTRATION_PROVISIONING_TOKEN="p" * 48,
                  SCHEDULE_PROVISIONING_TOKEN="s" * 48, USER_PROFILE_PROVISIONING_TOKEN="u" * 48,
                  SEED_DEMO_DATA="false", ALLOW_FAKE_REDIS="true", RATE_LIMIT_LOGIN="0", RATE_LIMIT_REFRESH="0", RATE_LIMIT_MACHINE_EXCHANGE="0", RATE_LIMIT_USER="0", RATE_LIMIT_CREDENTIAL="0", REDIS_PORT="1")
from fastapi.testclient import TestClient
from main import app


def signed(**changes):
    data = {"auth_date": str(int(time.time())), "user": json.dumps({"id": 123456789}), "query_id": "test-query"}
    data.update(changes)
    key = hmac.new(b"WebAppData", b"integration-test-token", hashlib.sha256).digest()
    data["hash"] = hmac.new(key, "\n".join(f"{k}={v}" for k, v in sorted(data.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


class MaxIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = TestClient(app)
        cls.client = cls.context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)
        from database.create_tables import engine
        engine.dispose()
        _tmp.cleanup()

    def test_verified_identity_refresh_logout(self):
        result = self.client.post("/api/v1/auth/token", json={"initData": signed()})
        self.assertEqual(result.status_code, 200, result.text)
        pair = result.json()
        headers = {"Authorization": "Bearer " + pair["access_token"]}
        me = self.client.get("/api/v1/auth/me", headers=headers).json()
        self.assertEqual(me["max_user_id"], "123456789")
        again = self.client.post("/api/v1/auth/token", json={"initData": signed()}).json()
        again_me = self.client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + again["access_token"]}).json()
        self.assertEqual(me["id"], again_me["id"])
        self.assertEqual(self.client.get("/api/v1/institution", headers=headers).json()["items"], [])
        self.assertEqual(self.client.get("/api/v1/institution/00000000-0000-0000-0000-000000000001", headers=headers).status_code, 404)
        renewed = self.client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
        self.assertEqual(renewed.status_code, 200)
        self.assertEqual(self.client.post("/api/v1/auth/logout", json={"refresh_token": renewed.json()["refresh_token"]}).status_code, 204)
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=headers).status_code, 401)

    def test_unverified_and_malformed_data_rejected(self):
        for body in [{"max_user_id": "123456789"}, {"username": "ivan_admin"},
                     {"initData": signed(), "username": "admin"}, {}, {"initData": "x" * 16385}, {"initData": 5}]:
            # Форма тела нарушает схему (только initData, 1..16384) — 422.
            self.assertEqual(self.client.post("/api/v1/auth/token", json=body).status_code, 422, body)
        for data in [signed() + "&hash=duplicate", signed(auth_date="bad"),
                     signed(auth_date=str(int(time.time()) - 400)), signed(auth_date=str(int(time.time()) + 120)),
                     signed(user='{"id":null}'), signed(user='{"id":true}'), signed(user='bad'),
                     signed().replace("123456789", "123456788"), "a=%FF&hash=" + "0" * 64, "не query"]:
            # Любая ошибка содержимого — 401 INVALID_INIT_DATA.
            r = self.client.post("/api/v1/auth/token", json={"initData": data})
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (401, "INVALID_INIT_DATA"), data)
