import hashlib
import hmac
import json
import os
import time
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


auth_router = APIRouter(prefix="/auth", tags=["auth"])


class AuthTokenRequest(BaseModel):
    initData: str

'''
Внутренняя функция для проверки initData.

initData содержит стартовые параметры из Макса в URL-кодировке.
Один из них - параметр hash - является кодом аутентификации сообщения,
считается на секретном токене бота.

Функция проверяет корректность этого кода у заданной строки initData
'''
def verify_init_data(init_data: str, bot_token: str) -> bool:
    try:
        params = {}
        for pair in init_data.split("&"):
            key, separator, value = pair.partition("=")
            if not separator or not key or key in params:
                return False
            params[key] = unquote(value, encoding="utf-8", errors="strict")

        received_hash = params.pop("hash", "")
        if len(received_hash) != 64:
            return False
        received_hash = bytes.fromhex(received_hash)

        check_string = "\n".join(f"{key}={params[key]}" for key in sorted(params))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected_hash = hmac.new(secret, check_string.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected_hash, received_hash):
            return False

        return abs(time.time() - int(params["auth_date"])) <= 3600
    except (KeyError, ValueError, UnicodeError):
        return False

'''
'''
def parse_init_data(init_data: str) -> dict[str, object]:
    params = {}
    for pair in init_data.split("&"):
        key, _, value = pair.partition("=")
        params[key] = unquote(value, encoding="utf-8", errors="strict")

    data: dict[str, object] = dict(params)
    for key in ("user", "chat"):
        if key in params:
            data[key] = json.loads(params[key])
    data["auth_date"] = int(params["auth_date"])
    return data


'''
Метод API для входа.

Принимает initData - проверяет подпись и парсит.
Если подпись не прошла, HTTP 401.

Проверяем, известен ли пользователь с таким max user.id

Далее логика get or create - если уже существует такой пользователь, выдаёт его токены(access и refresh)
Иначе - создаёт пользователя
'''
@auth_router.post("/token")
async def auth_token(request: AuthTokenRequest):
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise HTTPException(status_code=503, detail="BOT_TOKEN is not configured")
    if not verify_init_data(request.initData, bot_token):
        raise HTTPException(status_code=401, detail="Invalid initData")
    parsed_init_data = parse_init_data(request.initData)


'''
Метод API для обновления access-токена.
'''
@auth_router.post("/refresh")
async def auth_refresh():
	pass


'''
Метод API для отзыва access-токена.
'''
@auth_router.post("/logout")
async def logout():
    pass
