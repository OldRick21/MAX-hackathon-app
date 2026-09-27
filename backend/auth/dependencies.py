from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, HTTPBasic
from sqlalchemy.orm import Session
from database.create_tables import get_db
from auth.security import security
import jwt
from auth.state import core_state, machine_state

bearer_scheme = HTTPBearer(auto_error=False)
basic_scheme = HTTPBasic(auto_error=False)

def get_current_core_session(
    auth: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db)
):
    if not auth:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try:
        payload = security.decode(auth.credentials, 'core_access')
    except jwt.PyJWTError:
        raise HTTPException(401, 'Invalid token')
    state = core_state(db, payload)
    if not state:
        raise HTTPException(401, 'Core session inactive')
    return state


def get_current_machine_token(
    auth: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    """Machine JWT своего экземпляра. Credential проверяется по БД на каждом запросе,
    поэтому отзыв credential сразу блокирует и уже выданные JWT."""
    if not auth:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Machine authentication required")
    try:
        payload = security.decode(auth.credentials, 'machine_access')
    except jwt.PyJWTError:
        raise HTTPException(401, 'Invalid machine token')
    if not machine_state(db, payload):
        raise HTTPException(401, 'Machine credential or institution inactive')
    return payload


class RequireMachineScope:
    def __init__(self, required_scope: str):
        self.required_scope = required_scope

    def __call__(self, machine_token=Depends(get_current_machine_token)):
        if self.required_scope not in (machine_token.get("scopes") or []):
            raise HTTPException(status_code=403, detail=f"Scope '{self.required_scope}' required")
        return machine_token
