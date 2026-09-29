from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, HTTPBasic
from sqlalchemy.orm import Session
from database.create_tables import get_db
from auth.security import security
import jwt
from auth.state import core_state, machine_state
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
    return payload


class RequireMachineScope:
    def __init__(self, required_scope: str):
        self.required_scope = required_scope

    def __call__(self, machine_token=Depends(get_current_machine_token)):
        if self.required_scope not in (machine_token.get("scopes") or []):
            raise DomainError(403, "FORBIDDEN", f"Нужен scope {self.required_scope}")
        return machine_token
