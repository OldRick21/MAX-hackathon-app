"""Каталог типов сервисов платформы и проверки по контракту ядра.

Источник правил: CORE_API_SPEC.md §2.1, §7 и спецификации сервисов в
docs/services/*/SPEC.md. Модуль не зависит от FastAPI/SQLAlchemy, чтобы проверки
можно было тестировать отдельно (tests/test_platform_core.py).
"""
import copy
import ipaddress
import re
from typing import Dict, Iterable, List, Optional, Set
from urllib.parse import urlsplit

from platform_core.errors import DomainError, validation

PROFILES = ("admin", "teacher", "student")
LOCALES = ("ru", "en")
CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
ENTRYPOINT_RE = re.compile(r"^/(?!/)[A-Za-z0-9_./-]*$")
HOST_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")

ADMIN_PERMISSIONS = [
    "institution.read",
    "institution.update",
    "members.read",
    "members.manage",
    "groups.manage",
    "services.read",
    "services.manage",
    "roles.manage",
    "credentials.manage",
]

# Системные роли administration (CORE_API_SPEC.md §2.3). Неизменяемые.
ADMIN_SYSTEM_ROLES = [
    {
        "code": "owner",
        "titles": {"ru": "Владелец", "en": "Owner"},
        "allowed_profiles": ["admin"],
        "permissions": list(ADMIN_PERMISSIONS),
    },
    {
        "code": "technical_admin",
        "titles": {"ru": "Технический администратор", "en": "Technical administrator"},
        "allowed_profiles": ["admin"],
        "permissions": ["institution.read", "services.read", "services.manage", "credentials.manage"],
    },
    {
        "code": "membership_admin",
        "titles": {"ru": "Администратор участников", "en": "Membership administrator"},
        "allowed_profiles": ["admin"],
        "permissions": ["institution.read", "members.read", "members.manage", "groups.manage"],
    },
]
OWNER_ROLE = "owner"
LEGACY_OWNER_ROLE = "admin_owner"

SERVICE_TYPES: Dict[str, dict] = {
    "administration": {
        "code": "administration",
        "deployment": "local",
        "titles": {"ru": "Администрирование", "en": "Administration"},
        "supported_profiles": ["admin"],
        "permission_codes": list(ADMIN_PERMISSIONS),
        "protected": True,
        "menus": [
            {"id": "administration", "titles": {"ru": "Администрирование", "en": "Administration"},
             "entrypoint_path": "/admin", "profiles": ["admin"], "required_permissions": [], "order": 0},
        ],
        "initial_roles": ADMIN_SYSTEM_ROLES,
        "system_roles": True,
    },
}

# Остальные сервисы вуза ядру заранее не известны: вуз регистрирует их как свои сервисы
# (custom.<код>) — расписание, «Люди», курсовые и любые другие. Каждый работает в своём
# контейнере вуза на одобренном хосте, получает ключ в админке и сам публикует меню и роли
# через machine API (CORE_API_SPEC.md §7). Права такого сервиса живут в пространстве <код>.*,
# поэтому он не может выдать себе права другого сервиса или администрирования.
# Администрирование — единственный тип, который знает ядро: его роли — права на private API ядра.
CUSTOM_RE = re.compile(r"^custom\.([a-z][a-z0-9-]{1,39})$")


class PermissionNamespace:
    """Словарь прав своего сервиса: любые коды вида '<prefix><имя>'."""

    def __init__(self, prefix: str):
        self.prefix = prefix

    def __contains__(self, code) -> bool:
        return isinstance(code, str) and code.startswith(self.prefix) and len(code) > len(self.prefix)

    def __iter__(self):
        return iter(())


def is_custom(code) -> bool:
    return isinstance(code, str) and bool(CUSTOM_RE.match(code))


def custom_code(slug) -> str:
    code = f"custom.{slug}" if isinstance(slug, str) else ""
    if not is_custom(code):
        raise validation("Код сервиса: латиница в нижнем регистре, цифры и -, 2–40 символов, с буквы", "code")
    return code


def service_type(code: str) -> dict:
    item = SERVICE_TYPES.get(code)
    if item:
        return item
    match = CUSTOM_RE.match(code) if isinstance(code, str) else None
    if match:
        return {
            "code": code, "deployment": "local", "titles": {"ru": "Свой сервис", "en": "Custom service"},
            "supported_profiles": list(PROFILES), "permission_codes": PermissionNamespace(f"{match.group(1)}."),
            "protected": False, "menus": [], "initial_roles": [], "system_roles": False,
        }
    raise validation("Неизвестный тип сервиса", "service_type")


def public_service_types() -> List[dict]:
    """ServiceTypeList: встроенный тип один — администрирование; остальные сервисы — свои (custom.<код>)."""
    keys = ("code", "deployment", "titles", "supported_profiles", "permission_codes", "protected")
    return [{k: copy.deepcopy(t[k]) for k in keys} for t in SERVICE_TYPES.values()]


def default_manifest(code: str) -> dict:
    t = service_type(code)
    return {"titles": copy.deepcopy(t["titles"]), "menus": copy.deepcopy(t["menus"])}


# --------------------------------------------------------------------------
# Простые значения
# --------------------------------------------------------------------------

def check_code(value, path: str) -> str:
    if not isinstance(value, str) or not CODE_RE.match(value):
        raise validation("Код должен соответствовать ^[a-z][a-z0-9_.-]{0,63}$", path)
    return value


def check_localized(value, path: str) -> dict:
    if not isinstance(value, dict) or set(value) - {"ru", "en"} or "ru" not in value:
        raise validation("Нужен объект {ru, en?}; ru обязателен", path)
    result = {}
    for key, text in value.items():
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 200:
            raise validation("Название должно быть непустой строкой до 200 символов", f"{path}.{key}")
        result[key] = text.strip()
    return result


def check_locale(value, path: str = "default_locale") -> str:
    if value not in LOCALES:
        raise validation("Допустимые языки: ru, en", path)
    return value


def check_profiles(values, path: str = "profiles", allowed: Iterable[str] = PROFILES,
                   allow_empty: bool = False) -> List[str]:
    if not isinstance(values, list) or (not values and not allow_empty):
        raise validation("Нужен непустой список профилей", path)
    allowed_set = set(allowed)
    result: List[str] = []
    for i, p in enumerate(values):
        if p not in PROFILES:
            raise validation("Допустимые профили: admin, teacher, student", f"{path}[{i}]")
        if p not in allowed_set:
            raise validation(f"Профиль '{p}' не поддерживается этим сервисом", f"{path}[{i}]")
        if p in result:
            raise validation("Профили не должны повторяться", f"{path}[{i}]")
        result.append(p)
    return result


def check_permissions(values, vocabulary: Iterable[str], path: str = "permissions") -> List[str]:
    if not isinstance(values, list) or len(values) > 128:
        raise validation("Нужен список permissions (до 128)", path)
    vocab = vocabulary if isinstance(vocabulary, PermissionNamespace) else set(vocabulary)
    result: List[str] = []
    for i, code in enumerate(values):
        check_code(code, f"{path}[{i}]")
        if code not in vocab:
            if isinstance(vocab, PermissionNamespace):
                raise validation(f"Права этого сервиса должны начинаться с '{vocab.prefix}'", f"{path}[{i}]")
            raise validation(f"Permission '{code}' не входит в словарь типа сервиса", f"{path}[{i}]")
        if code in result:
            raise validation("Permissions не должны повторяться", f"{path}[{i}]")
        result.append(code)
    return result


# --------------------------------------------------------------------------
# URL local-экземпляров (CORE_API_SPEC.md §7)
# --------------------------------------------------------------------------

def normalize_hostname(value: str) -> str:
    host = (value or "").strip().lower().rstrip(".")
    if not host or len(host) > 253:
        raise validation("Некорректное имя хоста", "hostname")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        raise validation("IP-адреса не допускаются, нужен DNS hostname", "hostname")
    labels = host.split(".")
    if len(labels) < 2 or not all(HOST_LABEL_RE.match(l) for l in labels) or labels[-1].isdigit():
        raise validation("Нужен полный DNS hostname, например coursework.university.ru", "hostname")
    if host == "localhost" or host.endswith(".localhost") or labels[-1] in ("local", "internal", "example", "test", "invalid"):
        raise validation("Служебные и локальные домены не допускаются", "hostname")
    return host


def _check_https(url, path: str, origin_only: bool, approved_hosts: Optional[Set[str]]) -> str:
    if not isinstance(url, str) or len(url) > 2048 or not url.startswith("https://"):
        raise validation("Нужен адрес https://", path)
    parts = urlsplit(url)
    if parts.username or parts.password or "@" in parts.netloc:
        raise validation("Адрес не должен содержать учётные данные", path)
    if parts.query or parts.fragment or "?" in url or "#" in url:
        raise validation("Адрес не должен содержать query или fragment", path)
    if "\\" in url or "%" in url:
        raise validation("Адрес содержит недопустимые символы", path)
    try:
        port = parts.port
    except ValueError:
        raise validation("Некорректный порт", path)
    host = normalize_hostname(parts.hostname or "")
    if origin_only and parts.path not in ("",):
        raise validation("Нужен только origin: https://host без пути и завершающего /", path)
    if not origin_only:
        segments = parts.path.split("/")
        if any(s in (".", "..") for s in segments) or "//" in parts.path:
            raise validation("Путь API содержит недопустимые сегменты", path)
    if approved_hosts is not None and host not in approved_hosts:
        raise DomainError(422, "VALIDATION_ERROR",
                          f"Хост {host} не одобрен платформой для этого вуза",
                          [{"path": path, "message": "Хост отсутствует в списке, одобренном поддержкой платформы"}])
    netloc = host if port is None else f"{host}:{port}"
    if origin_only:
        return f"https://{netloc}"
    return f"https://{netloc}{parts.path.rstrip('/') if parts.path != '/' else ''}"


def check_api_url(url, approved_hosts: Optional[Set[str]] = None, path: str = "api_base_url") -> str:
    return _check_https(url, path, False, approved_hosts)


def check_origin(url, approved_hosts: Optional[Set[str]] = None, path: str = "client_base_url") -> str:
    return _check_https(url, path, True, approved_hosts)


# --------------------------------------------------------------------------
# Manifest и роли
# --------------------------------------------------------------------------

def check_entrypoint(value, path: str) -> str:
    if not isinstance(value, str) or len(value) > 1024 or not ENTRYPOINT_RE.match(value):
        raise validation("Путь меню: абсолютный путь без query, fragment, % и \\", path)
    if any(seg in (".", "..") for seg in value.split("/")):
        raise validation("Путь меню не может содержать . или ..", path)
    return value


def check_manifest(manifest, type_code: str, supported_profiles: Iterable[str]) -> dict:
    if not isinstance(manifest, dict) or set(manifest) - {"titles", "menus"} or not {"titles", "menus"} <= set(manifest):
        raise validation("Manifest должен содержать только titles и menus", "manifest")
    t = service_type(type_code)
    titles = check_localized(manifest["titles"], "titles")
    menus_in = manifest["menus"]
    if not isinstance(menus_in, list) or len(menus_in) > 32:
        raise validation("menus — список до 32 элементов", "menus")
    menus: List[dict] = []
    seen: Set[str] = set()
    required = {"id", "titles", "entrypoint_path", "profiles", "required_permissions", "order"}
    for i, m in enumerate(menus_in):
        p = f"menus[{i}]"
        if not isinstance(m, dict) or set(m) != required:
            raise validation("Пункт меню: id, titles, entrypoint_path, profiles, required_permissions, order", p)
        menu_id = check_code(m["id"], f"{p}.id")
        if menu_id in seen:
            raise validation("ID меню должны быть уникальны", f"{p}.id")
        seen.add(menu_id)
        order = m["order"]
        if not isinstance(order, int) or isinstance(order, bool) or not 0 <= order <= 10000:
            raise validation("order — целое 0..10000", f"{p}.order")
        menus.append({
            "id": menu_id,
            "titles": check_localized(m["titles"], f"{p}.titles"),
            "entrypoint_path": check_entrypoint(m["entrypoint_path"], f"{p}.entrypoint_path"),
            "profiles": check_profiles(m["profiles"], f"{p}.profiles", supported_profiles),
            "required_permissions": check_permissions(m["required_permissions"], t["permission_codes"],
                                                      f"{p}.required_permissions"),
            "order": order,
        })
    return {"titles": titles, "menus": menus}


ROLE_FIELDS = {"code", "titles", "allowed_profiles", "permissions"}


def check_role_input(body, type_code: str, supported_profiles: Iterable[str]) -> dict:
    if not isinstance(body, dict) or set(body) != ROLE_FIELDS:
        raise validation("Роль: code, titles, allowed_profiles, permissions", "body")
    t = service_type(type_code)
    return {
        "code": check_code(body["code"], "code"),
        "titles": check_localized(body["titles"], "titles"),
        "allowed_profiles": check_profiles(body["allowed_profiles"], "allowed_profiles", supported_profiles),
        "permissions": check_permissions(body["permissions"], t["permission_codes"]),
    }


def check_role_patch(body, type_code: str, supported_profiles: Iterable[str]) -> dict:
    if not isinstance(body, dict) or not body or set(body) - (ROLE_FIELDS - {"code"}):
        raise validation("Изменяются только titles, allowed_profiles, permissions (минимум одно поле)", "body")
    t = service_type(type_code)
    result = {}
    if "titles" in body:
        result["titles"] = check_localized(body["titles"], "titles")
    if "allowed_profiles" in body:
        result["allowed_profiles"] = check_profiles(body["allowed_profiles"], "allowed_profiles", supported_profiles)
    if "permissions" in body:
        result["permissions"] = check_permissions(body["permissions"], t["permission_codes"])
    return result


def check_role_codes(values, path: str = "roles") -> List[str]:
    if not isinstance(values, list) or len(values) > 32:
        raise validation("roles — список кодов (до 32)", path)
    result: List[str] = []
    for i, code in enumerate(values):
        check_code(code, f"{path}[{i}]")
        if code in result:
            raise validation("Коды ролей не должны повторяться", f"{path}[{i}]")
        result.append(code)
    return result
