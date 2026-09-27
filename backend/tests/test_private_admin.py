import os
import time
import requests
from requests.auth import HTTPBasicAuth

BASE_URL = "http://127.0.0.1:8000"


def run():
    print("\n[Suite 3] Запуск тестов Private Administration...")
    ts = int(time.time())
    admin_name = f"ivan_admin_{ts}"
    student_name = f"petr_ext_{ts}"

    # 1. Вход администратора
    r = requests.post(f"{BASE_URL}/api/v1/auth/token", json={"username": admin_name})
    admin_tokens = r.json()
    admin_core_h = {"Authorization": f"Bearer {admin_tokens['access_token']}"}

    r_inst = requests.get(f"{BASE_URL}/api/v1/institution", headers=admin_core_h)
    inst_id = r_inst.json()["items"][0]["id"]

    # 2. Machine token сервиса администрирования через его binding
    services = requests.get(f"{BASE_URL}/api/v1/institution/{inst_id}/service?profile=admin", headers=admin_core_h).json()["items"]
    admin_srv_id = next(s["id"] for s in services if s["service_type"] == "administration")
    binding = requests.get(f"{BASE_URL}/api/v1/internal/provisioning/bindings/{admin_srv_id}",
                           headers={"Authorization": f"Bearer {os.environ['ADMINISTRATION_PROVISIONING_TOKEN']}"}).json()
    r = requests.post(
        f"{BASE_URL}/api/v1/internal/auth/token",
        auth=HTTPBasicAuth(binding["client_id"], binding["client_secret"]),
        json={"grant_type": "client_credentials"}
    )
    machine_token = r.json()["access_token"]

    # 3. Выпуск Service Session под профилем admin (Actor Token)
    r_sess = requests.post(
        f"{BASE_URL}/api/v1/institution/{inst_id}/service/{admin_srv_id}/session",
        json={"profile": "admin"},
        headers=admin_core_h
    )
    actor_jwt = r_sess.json()["access_token"]
    private_h = {
        "Authorization": f"Bearer {machine_token}",
        "X-Actor-Token": actor_jwt
    }
    print("  ✓ Admin dual-token context established")

    # 4. Регистрация нового внешнего пользователя
    r_new = requests.post(f"{BASE_URL}/api/v1/auth/token", json={"username": student_name})
    new_user_id = requests.get(
        f"{BASE_URL}/api/v1/auth/me",
        headers={"Authorization": f"Bearer {r_new.json()['access_token']}"}
    ).json()["id"]

    # 5. Добавление участника в ВУЗ
    member_payload = {"user_id": new_user_id, "profiles": ["student"]}
    r = requests.post(f"{BASE_URL}/api/v1/institution/{inst_id}/internal/members", json=member_payload, headers=private_h)
    assert r.status_code == 201
    print("  ✓ Add member to institution OK")

    # 6. Проверка дубликата (409)
    r = requests.post(f"{BASE_URL}/api/v1/institution/{inst_id}/internal/members", json=member_payload, headers=private_h)
    assert r.status_code == 409
    print("  ✓ Duplicate membership rejected (409)")

    # 8. Защита ingress без X-Actor-Token (404)
    r_pub = requests.get(f"{BASE_URL}/api/v1/institution/{inst_id}/internal", headers={"Authorization": f"Bearer {machine_token}"})
    assert r_pub.status_code == 404
    print("  ✓ Public ingress masking without Actor token (404) OK")