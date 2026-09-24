import time
import requests

BASE_URL = "http://127.0.0.1:8000"


def run():
    print("\n[Suite 4] Запуск тестов Compliance & Lifecycle...")

    # 1. Health check
    r = requests.get(f"{BASE_URL}/api/v1/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert "X-Request-ID" in r.headers
    assert r.headers["Cache-Control"] == "no-store"
    print("  ✓ Health check & headers (X-Request-ID, Cache-Control) OK")

    # 2. Формат ошибки ErrorResponse
    r = requests.get(f"{BASE_URL}/api/v1/auth/me")
    assert r.status_code == 401
    err = r.json().get("error", {})
    assert err.get("code") == "UNAUTHENTICATED" and "request_id" in err
    print(f"  ✓ Standard ErrorResponse structure verified (code: {err.get('code')})")

    # 3. Карточка конкретного ВУЗа
    ts = int(time.time())
    r_auth = requests.post(f"{BASE_URL}/api/v1/auth/token", json={"username": f"ivan_comp_{ts}"})
    user_h = {"Authorization": f"Bearer {r_auth.json()['access_token']}"}

    r_inst = requests.get(f"{BASE_URL}/api/v1/institution", headers=user_h)
    inst_id = r_inst.json()["items"][0]["id"]
    r_single = requests.get(f"{BASE_URL}/api/v1/institution/{inst_id}", headers=user_h)
    assert r_single.status_code == 200
    print(f"  ✓ Single institution card: '{r_single.json()['display_name']}'")

    # 4. Ротация и явный отзыв сессии сервиса
    r_srv = requests.get(f"{BASE_URL}/api/v1/institution/{inst_id}/service?profile=student", headers=user_h)
    srv_id = r_srv.json()["items"][0]["id"]

    r_sess = requests.post(
        f"{BASE_URL}/api/v1/institution/{inst_id}/service/{srv_id}/session",
        json={"profile": "student"},
        headers=user_h
    )
    srv_refresh = r_sess.json()["refresh_token"]
    srv_sid = r_sess.json()["session_id"]

    r_rot = requests.post(
        f"{BASE_URL}/api/v1/institution/{inst_id}/service/{srv_id}/session/refresh",
        json={"refresh_token": srv_refresh}
    )
    assert r_rot.status_code == 200
    print("  ✓ Service session rotation OK")

    r_del = requests.delete(
        f"{BASE_URL}/api/v1/institution/{inst_id}/service/{srv_id}/session/{srv_sid}",
        headers=user_h
    )
    assert r_del.status_code == 204
    print("  ✓ Service session explicit revocation (204) OK")