import hashlib
import hmac
import json
import time
import urllib.parse
from platform_core.errors import DomainError


def invalid(message: str) -> DomainError:
    return DomainError(401, "INVALID_INIT_DATA", message)

def validate_max_init_data(init_data_raw: str, bot_token: str) -> dict:
    """
    Валидирует WebApp.initData мессенджера Макс по HMAC-SHA256.
    Проверяет целостность данных и свежесть auth_date (до 300 секунд).
    """
    try:
        pairs = urllib.parse.parse_qsl(init_data_raw, keep_blank_values=True, strict_parsing=True,
                                       encoding="utf-8", errors="strict")
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("Duplicate parameters")
        parsed_data = dict(pairs)
    except Exception:
        raise invalid("Malformed query string")

    received_hash = parsed_data.pop("hash", None)
    if not received_hash or len(received_hash) != 64 or any(c not in "0123456789abcdef" for c in received_hash):
        raise invalid("Hash missing")

    # 1. Проверка времени жизни auth_date (до 300 с, skew 30 с)
    try:
        auth_date = int(parsed_data.get("auth_date", 0))
    except (ValueError, TypeError):
        raise invalid("Invalid auth_date")
    now = int(time.time())
    if auth_date == 0 or (now - auth_date) > 300 or (auth_date - now) > 30:
        raise invalid("auth_date expired or invalid")

    # 2. Формирование data_check_string (ключи по алфавиту через \n)
    sorted_items = sorted(parsed_data.items())
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted_items)

    # 3. Вычисление HMAC-SHA256
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise invalid("HMAC mismatch")

    # 4. Извлечение пользователя
    user_raw = parsed_data.get("user")
    if not user_raw:
        raise invalid("user payload missing")

    try:
        user = json.loads(user_raw)
    except (ValueError, TypeError):
        raise invalid("Invalid user")
    if not isinstance(user, dict) or type(user.get("id")) is not int or user["id"] <= 0:
        raise invalid("Invalid user ID")
    return user
