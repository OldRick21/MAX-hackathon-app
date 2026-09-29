import time
import requests
from requests.auth import HTTPBasicAuth

BASE_URL = "http://127.0.0.1:8000"


def run():
    print("\n[Suite 2] Запуск тестов Service RBAC & Isolation...")
    ts = int(time.time())

    # 1. Вход студента
    r = requests.post(f"{BASE_URL}/api/v1/auth/token", json={"username": f"ivan_rbac_{ts}"})
    user_id = requests.get(
        f"{BASE_URL}/api/v1/auth/me",
        headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    ).json()["id"]

    # 2. Получение Machine Token сервиса
    r = requests.post(
        f"{BASE_URL}/api/v1/internal/auth/token",
        auth=HTTPBasicAuth("22370780-1c30-4de9-959e-b474475274c7", "service_super_secret_key_123"),  # демо-ключ SEED_DEMO_DATA
        json={"grant_type": "client_credentials"}
    )
    machine_token = r.json()["access_token"]
    service_id = r.json()["service_id"]
    m_headers = {"Authorization": f"Bearer {machine_token}"}

    # 3. Чтение профилей участника
    r = requests.get(f"{BASE_URL}/api/v1/internal/service/{service_id}/users/{user_id}/profiles", headers=m_headers)
    assert r.status_code == 200 and "student" in r.json()["profiles"]
    print("  ✓ Read user profiles via machine OK")

    # 4. Создание роли
    role_code = f"editor_{ts % 10000}"
    new_role = {
        "code": role_code,
        "titles": {"ru": "Редактор", "en": "Editor"},
        "allowed_profiles": ["student", "teacher"],
        "permissions": ["profiles.manage"]
    }
    r = requests.post(f"{BASE_URL}/api/v1/internal/service/{service_id}/roles", json=new_role, headers=m_headers)
    assert r.status_code == 201
    print(f"  ✓ Service role '{role_code}' created")

    # 5. Назначение роли
    assign_url = f"{BASE_URL}/api/v1/internal/service/{service_id}/users/{user_id}/profiles/student/roles"
    r = requests.put(assign_url, json={"roles": [role_code]}, headers=m_headers)
    assert r.status_code == 428  # If-Match обязателен
    tag = requests.get(assign_url, headers=m_headers).headers["ETag"]
    r = requests.put(assign_url, json={"roles": [role_code]}, headers={**m_headers, "If-Match": tag})
    assert r.status_code == 200 and "profiles.manage" in r.json()["permissions"]
    print("  ✓ Role assigned and permissions calculated")

    # 6. Защита от удаления активной роли (409)
    role_url = f"{BASE_URL}/api/v1/internal/service/{service_id}/roles/{role_code}"
    role_tag = requests.get(role_url, headers=m_headers).headers["ETag"]
    r = requests.delete(role_url, headers={**m_headers, "If-Match": role_tag})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ROLE_IN_USE"
    print("  ✓ ROLE_IN_USE conflict check (409) OK")

    # 7. Снятие и удаление роли
    tag = requests.get(assign_url, headers=m_headers).headers["ETag"]
    requests.put(assign_url, json={"roles": []}, headers={**m_headers, "If-Match": tag})
    r = requests.delete(role_url, headers={**m_headers, "If-Match": role_tag})
    assert r.status_code == 204
    print("  ✓ Role deleted after unassignment (204) OK")

    # 8. Проверка изоляции чужого service_id (403)
    r = requests.get(
        f"{BASE_URL}/api/v1/internal/service/00000000-0000-0000-0000-000000000000/roles",
        headers=m_headers
    )
    assert r.status_code == 404  # чужой экземпляр не раскрывается
    print("  ✓ Cross-service isolation verified (404)")