import json
from typing import Any

from fastapi import Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, HTTPBasic
from sqlalchemy.orm import Session
from database.create_tables import get_db
from auth.security import security
import jwt
from auth.state import core_state, machine_state
from platform_core import ratelimit
from platform_core.errors import DomainError

bearer_scheme = HTTPBearer(auto_error=False)
basic_scheme = HTTPBasic(auto_error=False)

def get_current_core_session(
    auth: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db)
):
    if not auth:
        raise DomainError(401, "UNAUTHENTICATED", "Нужна авторизация")
    try:
        payload = security.decode(auth.credentials, 'core_access')
    except jwt.PyJWTError:
        raise DomainError(401, "UNAUTHENTICATED", "Токен недействителен")
    state = core_state(db, payload)
    if not state:
        raise DomainError(401, "UNAUTHENTICATED", "Сессия завершена")
    ratelimit.hit("user", state[0].id)
    return state


def get_current_machine_token(
    auth: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    """Machine JWT своего экземпляра. Credential проверяется по БД на каждом запросе,
    поэтому отзыв credential сразу блокирует и уже выданные JWT."""
    if not auth:
        raise DomainError(401, "UNAUTHENTICATED", "Нужен machine token")
    try:
        payload = security.decode(auth.credentials, 'machine_access')
    except jwt.PyJWTError:
        raise DomainError(401, "UNAUTHENTICATED", "Machine token недействителен")
    if not machine_state(db, payload):
        raise DomainError(401, "UNAUTHENTICATED", "Ключ сервиса отозван или вуз неактивен")
    ratelimit.hit("credential", payload["credential_id"])
    return payload


class RequireMachineScope:
    def __init__(self, required_scope: str):
        self.required_scope = required_scope

    def __call__(self, machine_token=Depends(get_current_machine_token)):
        if self.required_scope not in (machine_token.get("scopes") or []):
            raise DomainError(403, "FORBIDDEN", f"Нужен scope {self.required_scope}")
        return machine_token


async def json_body(request: Request) -> Any:
    """Тело запроса JSON. Объявляется последним параметром маршрута: FastAPI решает зависимости
    по порядку, поэтому авторизация проверяется до разбора тела (CORE_API_SPEC.md §3)."""
    raw = await request.body()
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise DomainError(400, "BAD_REQUEST", "Некорректный JSON")


def validated(model, payload: Any):
    """Проверка тела по pydantic-модели уже после авторизации; ошибки — 422 VALIDATION_ERROR."""
    from pydantic import ValidationError
    try:
        return model.model_validate(payload if payload is not None else {})
    except ValidationError as error:
        details = [{"path": ".".join(str(x) for x in e.get("loc", ())) or "body", "message": e.get("msg", "invalid")}
                   for e in error.errors()]
        raise DomainError(422, "VALIDATION_ERROR", "Тело запроса не соответствует схеме", details[:100])
