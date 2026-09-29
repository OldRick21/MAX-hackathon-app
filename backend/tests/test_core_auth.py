import time
import requests
from requests.auth import HTTPBasicAuth

BASE_URL = "http://127.0.0.1:8000"


def run():
    print("\n[Suite 1] Запуск тестов Core Auth & Service Sessions...")
    ts = int(time.time())
    username = f"ivan_core_{ts}"

    # 1. Вход / регистрация пользователя
    r = requests.post(f"{BASE_URL}/api/v1/auth/token", json={"username": username})
    assert r.status_code == 200, f"Token request failed: {r.text}"
    tokens = r.json()
    user_access = tokens["access_token"]
    user_refresh = tokens["refresh_token"]
    user_headers = {"Authorization": f"Bearer {user_access}"}
    print("  ✓ Core login & registration OK")

    # 2. Получение данных профиля
    r = requests.get(f"{BASE_URL}/api/v1/auth/me", headers=user_headers)
    assert r.status_code == 200
    print("  ✓ GET /auth/me OK")

    # 3. Список ВУЗов и сервисов
    r = requests.get(f"{BASE_URL}/api/v1/institution", headers=user_headers)
    assert r.status_code == 200
    inst_id = r.json()["items"][0]["id"]

    r = requests.get(f"{BASE_URL}/api/v1/institution/{inst_id}/service?profile=student", headers=user_headers)
    assert r.status_code == 200
    srv_id = r.json()["items"][0]["id"]
    print("  ✓ Available institutions and services fetched")

    # 4. Выпуск сервисной сессии под профилем student
    r = requests.post(
        f"{BASE_URL}/api/v1/institution/{inst_id}/service/{srv_id}/session",
        json={"profile": "student"},
        headers=user_headers
    )
    assert r.status_code == 201
    srv_access = r.json()["access_token"]
    print("  ✓ Service Session issued (profile: student)")

    # 5. Получение Machine Token микросервисом
    r = requests.post(
        f"{BASE_URL}/api/v1/internal/auth/token",
        auth=HTTPBasicAuth("22370780-1c30-4de9-959e-b474475274c7", "service_super_secret_key_123"),
        json={"grant_type": "client_credentials"}
    )
    assert r.status_code == 200
    machine_token = r.json()["access_token"]
    m_headers = {"Authorization": f"Bearer {machine_token}"}
    print("  ✓ Machine Token exchange OK")

    # 6. JWKS
    r = requests.get(f"{BASE_URL}/api/v1/internal/auth/jwks")
    assert r.status_code == 200 and len(r.json()["keys"]) > 0
    print("  ✓ JWKS keys verified")

    # 7. Интроспекция активного токена
    r = requests.post(f"{BASE_URL}/api/v1/internal/auth/introspect", headers=m_headers, json={"token": srv_access})
    assert r.status_code == 200 and r.json().get("active") is True
    print("  ✓ Live introspection: active=True")

    # 8. Ротация токена ядра
    r = requests.post(f"{BASE_URL}/api/v1/auth/refresh", json={"refresh_token": user_refresh})
    assert r.status_code == 200
    new_refresh = r.json()["refresh_token"]
    print("  ✓ Core Token rotation OK")

    # 9. Logout и проверка каскадной инвалидации
    r = requests.post(f"{BASE_URL}/api/v1/auth/logout", json={"refresh_token": new_refresh})
    assert r.status_code == 204

    r_intro = requests.post(f"{BASE_URL}/api/v1/internal/auth/introspect", headers=m_headers, json={"token": srv_access})
    assert r_intro.status_code == 200 and r_intro.json().get("active") is False
    print("  ✓ Cascade revocation on logout OK (active=False)")