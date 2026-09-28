"""Сквозной сценарий администрирования в ядре (TestClient, временная SQLite, FakeRedis).

    cd backend && python -m unittest tests.test_administration_flow -v
"""
import os
import tempfile
import unittest
import uuid

_tmp = tempfile.TemporaryDirectory()
os.environ.update(
    DATABASE_URL=f"sqlite:///{_tmp.name}/admin.db", JWT_ISSUER="https://core.test", JWT_KEYRING_PATH=f"{_tmp.name}/keys.json", CURSOR_SECRET_KEY="flow-secret-" * 6, MAX_BOT_TOKEN="",
    ALLOW_DEV_LOGIN="true", SEED_DEMO_DATA="false", ALLOW_FAKE_REDIS="true", REDIS_PORT="1",
    CLOUD_BINDING_KEY="b" * 48, ADMINISTRATION_PROVISIONING_TOKEN="p" * 48,
)
from fastapi.testclient import TestClient  # noqa: E402

from tests.keys import issue_key  # noqa: E402

from database.create_tables import session_local  # noqa: E402
from database.tables import PlatformStaff  # noqa: E402
from main import app  # noqa: E402



class AdministrationFlow(unittest.TestCase):
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

    # --- помощники ---

    def login(self, name):
        pair = self.c.post("/api/v1/auth/token", json={"username": f"{name}_{uuid.uuid4().hex[:6]}"}).json()
        headers = {"Authorization": "Bearer " + pair["access_token"]}
        user_id = self.c.get("/api/v1/auth/me", headers=headers).json()["id"]
        return user_id, headers

    def admin_context(self, inst_id, core_headers):
        services = self.c.get(f"/api/v1/institution/{inst_id}/service?profile=admin", headers=core_headers).json()["items"]
        admin = next(s for s in services if s["service_type"] == "administration")
        self.assertEqual(admin["menus"][0]["entrypoint_path"], "/admin")
        actor = self.c.post(f"/api/v1/institution/{inst_id}/service/{admin['id']}/session",
                            json={"profile": "admin"}, headers=core_headers)
        self.assertEqual(actor.status_code, 201, actor.text)
        b = issue_key(admin["id"])  # ключ контейнера администрирования выдаёт оператор
        machine = self.c.post("/api/v1/internal/auth/token", auth=(b["client_id"], b["client_secret"]),
                              json={"grant_type": "client_credentials"}).json()
        self.assertIn("institution:manage", machine["scopes"])
        self.assertNotIn("assignments:write", machine["scopes"])
        return admin["id"], {"Authorization": "Bearer " + machine["access_token"],
                             "X-Actor-Token": actor.json()["access_token"]}

    def etag(self, path, headers):
        r = self.c.get(path, headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        return r.headers["ETag"]

    # --- сценарий ---

    def test_full_flow(self):
        owner_id, owner_core = self.login("owner")
        support_id, support_core = self.login("support")
        other_id, other_core = self.login("other")

        # Поддержка платформы назначается только оператором.
        self.assertEqual(self.c.get("/api/v1/platform/applications", headers=support_core).status_code, 403)
        with session_local() as db:
            db.add(PlatformStaff(user_id=support_id, role="platform_support", granted_by="test"))
            db.commit()
        self.assertEqual(self.c.get("/api/v1/platform/me", headers=support_core).json()["roles"], ["platform_support"])

        # Заявка и одобрение.
        app_resp = self.c.post("/api/v1/institution-applications", headers=owner_core,
                               json={"titles": {"ru": "Тестовый университет"}, "contact": "rector@example.ru"})
        self.assertEqual(app_resp.status_code, 201, app_resp.text)
        app_id = app_resp.json()["id"]
        self.assertEqual(self.c.post(f"/api/v1/platform/applications/{app_id}/approve", headers=owner_core,
                                     json={}).status_code, 403)
        approved = self.c.post(f"/api/v1/platform/applications/{app_id}/approve", headers=support_core, json={})
        self.assertEqual(approved.status_code, 200, approved.text)
        inst_id = approved.json()["institution_id"]
        self.assertEqual(self.c.post(f"/api/v1/platform/applications/{app_id}/reject", headers=support_core,
                                     json={"reason": "late"}).status_code, 409)

        # Заявитель — владелец.
        admin_id, owner_h = self.admin_context(inst_id, owner_core)
        base = f"/api/v1/institution/{inst_id}/internal"
        self.assertEqual(self.c.get(base, headers={"Authorization": owner_h["Authorization"]}).status_code, 404)
        inst_etag = self.etag(base, owner_h)
        self.assertEqual(self.c.patch(base, headers=owner_h, json={"default_locale": "en"}).status_code, 428)
        self.assertEqual(self.c.patch(base, headers={**owner_h, "If-Match": '"stale"'},
                                      json={"default_locale": "en"}).status_code, 412)
        r = self.c.patch(base, headers={**owner_h, "If-Match": inst_etag}, json={"default_locale": "en"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.patch(base, headers={**owner_h, "If-Match": r.headers["ETag"]},
                                      json={"status": "suspended"}).status_code, 422)

        # Участники.
        r = self.c.post(f"{base}/members", headers=owner_h, json={"user_id": other_id, "profiles": ["student", "admin"]})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.c.post(f"{base}/members", headers=owner_h,
                                     json={"user_id": other_id, "profiles": ["student"]}).json()["error"]["code"],
                         "MEMBERSHIP_ALREADY_EXISTS")

        # Регрессия: профиль admin без роли не даёт прав.
        _, other_h = self.admin_context(inst_id, other_core)
        denied = self.c.get(f"{base}/members", headers=other_h)
        self.assertEqual(denied.status_code, 403, denied.text)

        # owner назначает membership_admin; тот не может назначать роли administration и трогать owner.
        roles_path = f"{base}/services/{admin_id}/users/{other_id}/profiles/admin/roles"
        r = self.c.put(roles_path, headers={**owner_h, "If-Match": self.etag(roles_path, owner_h)},
                       json={"roles": ["membership_admin"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(sorted(r.json()["permissions"]), ["groups.manage", "institution.read", "members.manage", "members.read"])
        self.assertEqual(self.c.get(f"{base}/members", headers=other_h).status_code, 200)
        self.assertEqual(self.c.get(roles_path, headers=other_h).status_code, 403)
        owner_member = f"{base}/members/{owner_id}"
        r = self.c.delete(owner_member, headers={**other_h, "If-Match": self.etag(owner_member, other_h)})
        self.assertEqual(r.status_code, 403, r.text)

        # Последний владелец защищён.
        own_roles = f"{base}/services/{admin_id}/users/{owner_id}/profiles/admin/roles"
        r = self.c.put(own_roles, headers={**owner_h, "If-Match": self.etag(own_roles, owner_h)}, json={"roles": []})
        self.assertEqual(r.json()["error"]["code"], "LAST_OWNER")
        r = self.c.put(f"{owner_member}/profiles", headers={**owner_h, "If-Match": self.etag(owner_member, owner_h)},
                       json={"profiles": ["teacher"]})
        self.assertEqual(r.json()["error"]["code"], "LAST_OWNER")

        # Сервисы: administration защищён, установка идемпотентна.
        admin_path = f"{base}/services/{admin_id}"
        r = self.c.delete(admin_path, headers={**owner_h, "If-Match": self.etag(admin_path, owner_h)})
        self.assertEqual(r.json()["error"]["code"], "PROTECTED_RESOURCE")
        r = self.c.patch(admin_path, headers={**owner_h, "If-Match": self.etag(admin_path, owner_h)},
                         json={"enabled": False})
        self.assertEqual(r.json()["error"]["code"], "PROTECTED_RESOURCE")
        # Все сервисы вуза — свои (custom.<код>) на одобренных хостах; облачной установки нет.
        cloud = self.c.post(f"{base}/services", headers={**owner_h, "Idempotency-Key": str(uuid.uuid4())},
                            json={"service_type": "schedule", "deployment": "cloud"})
        self.assertEqual(cloud.status_code, 422, cloud.text)
        local = {"service_type": "custom.schedule", "deployment": "local", "titles": {"ru": "Расписание"},
                 "supported_profiles": ["student", "teacher"],
                 "api_base_url": "https://schedule.university.ru/api/v1", "client_base_url": "https://schedule.university.ru"}
        r = self.c.post(f"{base}/services", headers={**owner_h, "Idempotency-Key": str(uuid.uuid4())}, json=local)
        self.assertEqual(r.status_code, 422, r.text)  # хост не одобрен
        inst_path = f"/api/v1/platform/institutions/{inst_id}"
        r = self.c.put(f"{inst_path}/local-hosts", headers={**support_core, "If-Match": self.etag(inst_path, support_core)},
                       json={"hostnames": ["schedule.university.ru"]})
        self.assertEqual(r.status_code, 200, r.text)
        key = str(uuid.uuid4())
        first = self.c.post(f"{base}/services", headers={**owner_h, "Idempotency-Key": key}, json=local)
        self.assertEqual(first.status_code, 201, first.text)
        again = self.c.post(f"{base}/services", headers={**owner_h, "Idempotency-Key": key}, json=local)
        self.assertEqual(again.json()["id"], first.json()["id"])
        self.assertEqual(self.c.post(f"{base}/services", headers={**owner_h, "Idempotency-Key": str(uuid.uuid4())},
                                     json=local).json()["error"]["code"], "SERVICE_ALREADY_EXISTS")
        schedule_id = first.json()["id"]
        self.assertFalse(first.json()["enabled"])
        self.assertEqual(first.json()["supported_profiles"], ["student", "teacher", "admin"])
        # Ролей и меню у нового сервиса нет: их публикует сам сервис после выдачи ключа.
        self.assertEqual(self.c.get(f"{base}/services/{schedule_id}/roles", headers=owner_h).json()["items"], [])
        sched_path = f"{base}/services/{schedule_id}"
        r = self.c.patch(sched_path, headers={**owner_h, "If-Match": self.etag(sched_path, owner_h)}, json={"enabled": True})
        self.assertEqual(r.json()["error"]["code"], "MANIFEST_REQUIRED")
        cred = self.c.post(f"{sched_path}/credentials", headers=owner_h)
        self.assertEqual(cred.status_code, 201, cred.text)
        self.assertEqual(len(cred.json()["client_secret"]), 43)
        self.assertEqual(self.c.delete(f"{sched_path}/credentials/{cred.json()['credential']['id']}",
                                       headers=owner_h).status_code, 204)
        # Ключ администрирования выдаёт только оператор платформы.
        self.assertEqual(self.c.post(f"{base}/services/{admin_id}/credentials", headers=owner_h).status_code, 403)

        # Роли сервиса: создание и назначение.
        r = self.c.post(f"{base}/services/{schedule_id}/roles", headers=owner_h,
                        json={"code": "viewer", "titles": {"ru": "Просмотр"}, "allowed_profiles": ["admin"],
                              "permissions": ["schedule.read_all"]})
        self.assertEqual(r.status_code, 201, r.text)
        r = self.c.post(f"{base}/services/{schedule_id}/roles", headers=owner_h,
                        json={"code": "bad", "titles": {"ru": "x"}, "allowed_profiles": ["admin"],
                              "permissions": ["members.manage"]})
        self.assertEqual(r.status_code, 422)
        other_sched = f"{base}/services/{schedule_id}/users/{other_id}/profiles/student/roles"
        r = self.c.put(other_sched, headers={**owner_h, "If-Match": self.etag(other_sched, owner_h)},
                       json={"roles": ["viewer"]})
        self.assertEqual(r.json()["error"]["code"], "INVALID_ROLE_ASSIGNMENT")

        # Журнал содержит изменения и отказы.
        audit = self.c.get(f"{base}/audit", headers=owner_h).json()["items"]
        actions = {e["action"] for e in audit}
        self.assertTrue({"institution.provision", "member.add", "service.install", "assignments.replace"} <= actions)
        self.assertTrue(any(e["outcome"] == "denied" for e in audit))

        # Приостановка вуза поддержкой.
        r = self.c.patch(inst_path, headers={**support_core, "If-Match": self.etag(inst_path, support_core)},
                         json={"status": "suspended"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.get(base, headers=owner_h).json()["error"]["code"], "RESOURCE_INACTIVE")
        self.assertEqual(self.c.post(f"/api/v1/institution/{inst_id}/service/{admin_id}/session",
                                     json={"profile": "admin"}, headers=owner_core).status_code, 409)


if __name__ == "__main__":
    unittest.main()
