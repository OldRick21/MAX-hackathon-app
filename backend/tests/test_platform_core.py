"""Проверки без БД и HTTP: python -m unittest tests.test_platform_core"""
import unittest

from platform_core import catalog
from platform_core.concurrency import (
    check_idempotency_key,
    compute_etag,
    constant_time_token_match,
    decode_cursor,
    encode_cursor,
    require_if_match,
)
from platform_core.errors import DomainError

KEY = "k" * 40


class CatalogTest(unittest.TestCase):
    def test_only_administration_is_built_in(self):
        # Расписание, «Люди», курсовые — свои сервисы вуза custom.<код>; ядро знает только администрирование.
        types = catalog.public_service_types()
        self.assertEqual([t["code"] for t in types], ["administration"])
        for legacy in ("schedule", "user-profile", "coursework"):
            with self.assertRaises(DomainError):
                catalog.service_type(legacy)
        admin = catalog.service_type("administration")
        self.assertTrue(admin["protected"])
        self.assertEqual(admin["supported_profiles"], ["admin"])
        roles = {r["code"]: set(r["permissions"]) for r in admin["initial_roles"]}
        self.assertEqual(roles["owner"], set(catalog.ADMIN_PERMISSIONS))
        self.assertNotIn("roles.manage", roles["technical_admin"])
        self.assertEqual(roles["membership_admin"], {"institution.read", "members.read", "members.manage", "groups.manage"})
        self.assertEqual(catalog.default_manifest("administration")["menus"][0]["entrypoint_path"], "/admin")

    def test_urls(self):
        ok = {"coursework.university.ru"}
        self.assertEqual(catalog.check_origin("https://coursework.university.ru", ok), "https://coursework.university.ru")
        self.assertEqual(catalog.check_api_url("https://coursework.university.ru/api/v1/", ok),
                         "https://coursework.university.ru/api/v1")
        bad = ["http://coursework.university.ru", "https://coursework.university.ru/", "https://user@coursework.university.ru",
               "https://127.0.0.1", "https://localhost", "https://coursework.university.ru?x=1",
               "https://coursework.university.ru#a", "https://other.university.ru", "https://svc.internal",
               "https://coursework.university.ru/%2e%2e"]
        for url in bad:
            with self.subTest(url=url), self.assertRaises(DomainError):
                catalog.check_origin(url, ok)
        with self.assertRaises(DomainError):
            catalog.check_api_url("https://coursework.university.ru/a/../b", ok)

    def test_manifest(self):
        menu = {"id": "coursework", "titles": {"ru": "Курсовые"}, "entrypoint_path": "/coursework",
                "profiles": ["student", "teacher"], "required_permissions": [], "order": 0}
        result = catalog.check_manifest({"titles": {"ru": "Курсовые"}, "menus": [menu]}, "custom.coursework",
                                        ["admin", "teacher", "student"])
        self.assertEqual(result["menus"][0]["id"], "coursework")
        for change in [{"entrypoint_path": "//evil"}, {"entrypoint_path": "/a/../b"}, {"entrypoint_path": "/a?b"},
                       {"profiles": ["guest"]}, {"required_permissions": ["schedule.write"]}, {"order": -1},
                       {"extra": 1}]:
            broken = {**menu, **change}
            with self.subTest(change=change), self.assertRaises(DomainError):
                catalog.check_manifest({"titles": {"ru": "x"}, "menus": [broken]}, "custom.coursework",
                                       ["admin", "teacher", "student"])
        with self.assertRaises(DomainError):
            catalog.check_manifest({"titles": {"ru": "x"}, "menus": [menu, menu]}, "custom.coursework",
                                   ["admin", "teacher", "student"])

    def test_roles(self):
        role = catalog.check_role_input({"code": "editor", "titles": {"ru": "Редактор"}, "allowed_profiles": ["admin"],
                                         "permissions": ["schedule.write"]}, "custom.schedule", ["admin", "teacher", "student"])
        self.assertEqual(role["permissions"], ["schedule.write"])
        with self.assertRaises(DomainError):
            catalog.check_role_input({"code": "x", "titles": {"ru": "x"}, "allowed_profiles": ["admin"],
                                      "permissions": ["members.manage"]}, "custom.schedule", ["admin"])
        with self.assertRaises(DomainError):
            catalog.check_role_patch({}, "custom.schedule", ["admin"])
        with self.assertRaises(DomainError):
            catalog.check_profiles(["admin", "admin"])
        self.assertEqual(catalog.check_profiles(["student", "admin"]), ["student", "admin"])


class ConcurrencyTest(unittest.TestCase):
    def test_etag_if_match(self):
        tag = compute_etag({"a": 1, "b": [1, 2]})
        self.assertEqual(tag, compute_etag({"b": [1, 2], "a": 1}))
        require_if_match(tag, tag)
        for header, status in [(None, 428), ("*", 400), (f"{tag}, {tag}", 400), ('W/"x"', 400), ('"other"', 412)]:
            with self.subTest(header=header):
                with self.assertRaises(DomainError) as ctx:
                    require_if_match(header, tag)
                self.assertEqual(ctx.exception.status, status)

    def test_cursor_bound_to_scope_and_time(self):
        scope = {"kind": "members", "actor": "a"}
        cursor = encode_cursor(KEY, scope, "u-1", now=1000)
        self.assertEqual(decode_cursor(KEY, scope, cursor, now=1100), "u-1")
        for other_scope, now, value in [({"kind": "members", "actor": "b"}, 1100, cursor),
                                        (scope, 1000 + 16 * 60, cursor), (scope, 1100, cursor[:-2] + "xx"),
                                        (scope, 1100, "garbage")]:
            with self.assertRaises(DomainError) as ctx:
                decode_cursor(KEY, other_scope, value, now=now)
            self.assertEqual(ctx.exception.code, "INVALID_CURSOR")

    def test_idempotency_key_and_secrets(self):
        self.assertEqual(check_idempotency_key("A2B3C4D5-0000-4000-8000-000000000000"),
                         "a2b3c4d5-0000-4000-8000-000000000000")
        with self.assertRaises(DomainError):
            check_idempotency_key("not-a-uuid")
        self.assertTrue(constant_time_token_match("t" * 40, "t" * 40))
        self.assertFalse(constant_time_token_match("t" * 40, "u" * 40))
        self.assertFalse(constant_time_token_match("short", "short"))


if __name__ == "__main__":
    unittest.main()
