import sys
import time
import requests

from tests import (
    test_core_auth,
    test_service_rbac,
    test_private_admin,
    test_compliance
)

BASE_URL = "http://127.0.0.1:8000"


def check_server():
    try:
        r = requests.get(f"{BASE_URL}/api/v1/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def main():
    print("=" * 65)
    print("   ПЛАТФОРМА «ВУЗЫ РОССИИ» — ЗАПУСК ВСЕХ ТЕСТОВ (OPENAPI 3.1.1)")
    print("=" * 65)

    if not check_server():
        print(f"\n[ОШИБКА] Сервер не отвечает по адресу {BASE_URL}!")
        print("Пожалуйста, запустите сервер перед тестированием:")
        print("  uvicorn main:app --reload\n")
        sys.exit(1)

    suites = [
        ("Core Auth & Token Pair Lifecycle", test_core_auth.run),
        ("Service RBAC & Domain Isolation", test_service_rbac.run),
        ("Private Administration & Dual Auth", test_private_admin.run),
        ("Contract Compliance & Service Sessions", test_compliance.run),
    ]

    total_start = time.time()
    passed = 0

    for name, run_fn in suites:
        start = time.time()
        try:
            run_fn()
            duration = round(time.time() - start, 2)
            print(f"--> [УСПЕХ] {name} ({duration}s)")
            passed += 1
        except Exception as e:
            duration = round(time.time() - start, 2)
            print(f"--> [ПРОВАЛ] {name} ({duration}s): {e}")
            break

    total_duration = round(time.time() - total_start, 2)
    print("\n" + "=" * 65)
    if passed == len(suites):
        print(f"   ИТОГ: ВСЕ ТЕСТЫ ПРОЙДЕНЫ ({passed}/{len(suites)}) ЗА {total_duration}s")
    else:
        print(f"   ИТОГ: ТЕСТЫ ЗАВЕРШИЛИСЬ С ОШИБКОЙ ({passed}/{len(suites)})")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()