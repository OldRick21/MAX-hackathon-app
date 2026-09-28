"""ETag/If-Match, подписанные курсоры, idempotency-хеш и секреты cloud bindings.

Правила — CORE_API_SPEC.md §3 и CLOUD_RUNTIME_SPEC.md §2. Модуль без внешних
зависимостей.
"""
import base64
import hashlib
import hmac
import json
import re
import time
from typing import Optional, Tuple

from platform_core.errors import DomainError

ETAG_RE = re.compile(r'^"[^"\\]+"$')
CURSOR_TTL_SECONDS = 15 * 60


def canonical_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def compute_etag(representation) -> str:
    """Strong ETag по содержимому представления ресурса.

    Отдельный счётчик revision не нужен: любое изменение видимых полей меняет
    хеш, а одинаковое содержимое даёт одинаковую версию.
    """
    return '"' + hashlib.sha256(canonical_json(representation)).hexdigest()[:40] + '"'


def require_if_match(header: Optional[str], current_etag: str) -> None:
    if header is None or header == "":
        raise DomainError(428, "PRECONDITION_REQUIRED",
                          "Нужен заголовок If-Match с ETag из последнего чтения ресурса")
    value = header.strip()
    if value == "*" or "," in value or value.startswith("W/") or not ETAG_RE.match(value) or len(value) > 128:
        raise DomainError(400, "BAD_REQUEST", "If-Match должен содержать ровно один strong ETag")
    if not hmac.compare_digest(value, current_etag):
        raise DomainError(412, "PRECONDITION_FAILED",
                          "Ресурс изменился после чтения. Обновите данные и повторите действие")


# --------------------------------------------------------------------------
# Курсоры: непрозрачные, подписанные, привязаны к области и живут 15 минут
# --------------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def encode_cursor(key: str, scope: dict, after, now: Optional[int] = None) -> str:
    payload = {"a": after, "s": hashlib.sha256(canonical_json(scope)).hexdigest()[:32],
               "e": int(now if now is not None else time.time()) + CURSOR_TTL_SECONDS}
    body = _b64(canonical_json(payload))
    sig = _b64(hmac.new(key.encode("utf-8"), b"cursor:" + body.encode("ascii"), hashlib.sha256).digest()[:24])
    return f"{body}.{sig}"


def decode_cursor(key: str, scope: dict, cursor: Optional[str], now: Optional[int] = None):
    if cursor is None:
        return None
    invalid = DomainError(400, "INVALID_CURSOR", "Курсор недействителен или устарел. Начните с первой страницы")
    if not isinstance(cursor, str) or not 0 < len(cursor) <= 2048 or cursor.count(".") != 1:
        raise invalid
    body, sig = cursor.split(".")
    expected = _b64(hmac.new(key.encode("utf-8"), b"cursor:" + body.encode("ascii"), hashlib.sha256).digest()[:24])
    if not hmac.compare_digest(sig, expected):
        raise invalid
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError):
        raise invalid
    if payload.get("s") != hashlib.sha256(canonical_json(scope)).hexdigest()[:32]:
        raise invalid
    if int(payload.get("e", 0)) < int(now if now is not None else time.time()):
        raise invalid
    return payload.get("a")


def check_limit(limit) -> int:
    if limit is None:
        return 50
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise DomainError(422, "VALIDATION_ERROR", "limit должен быть от 1 до 100",
                          [{"path": "limit", "message": "1..100"}])
    return limit


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def is_uuid(value) -> bool:
    return isinstance(value, str) and bool(UUID_RE.match(value))


def request_hash(body) -> str:
    return hashlib.sha256(canonical_json(body)).hexdigest()


def check_idempotency_key(value: Optional[str]) -> str:
    if not value:
        raise DomainError(422, "VALIDATION_ERROR", "Нужен заголовок Idempotency-Key (UUID)",
                          [{"path": "Idempotency-Key", "message": "required"}])
    value = value.strip().lower()
    if not is_uuid(value):
        raise DomainError(422, "VALIDATION_ERROR", "Idempotency-Key должен быть UUID",
                          [{"path": "Idempotency-Key", "message": "UUID"}])
    return value


# --------------------------------------------------------------------------
# Секреты cloud bindings
# --------------------------------------------------------------------------

def constant_time_token_match(presented: Optional[str], expected: Optional[str]) -> bool:
    if not presented or not expected or len(expected) < 32:
        return False
    a = hashlib.sha256(presented.encode("utf-8")).digest()
    b = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(a, b)


def split_bearer(header: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not header or " " not in header:
        return None, None
    scheme, _, token = header.partition(" ")
    return scheme.lower(), token.strip()
