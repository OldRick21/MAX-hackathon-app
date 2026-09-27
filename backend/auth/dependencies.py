from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, HTTPBasic
from sqlalchemy.orm import Session
from database.create_tables import get_db
from database.tables import User, CoreSession, ServiceCredential
from auth.security import security
from auth.revocations import revoked_tokens_redis

bearer_scheme = HTTPBearer(auto_error=False)
basic_scheme = HTTPBasic(auto_error=False)

def get_current_core_session(
    auth: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db)
):
    if not auth:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try:
        payload = security._decode_token(auth.credentials)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    data = payload.data if hasattr(payload, "data") and isinstance(payload.data, dict) else {}
    token_use = getattr(payload, "token_use", None) or data.get("token_use")
    session_id = getattr(payload, "session_id", None) or data.get("session_id")
    user_id = payload.sub

    if token_use != "core_access" or not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token use")

    if revoked_tokens_redis.exists(f"core_session_revoked:{session_id}"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session terminated")

    core_session = db.query(CoreSession).filter(CoreSession.id == session_id).first()
    if not core_session or core_session.is_revoked:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Core session inactive")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return user, core_session

def _claim(payload, name):
    return getattr(payload, name, None) or payload.get(name)


def get_current_machine_token(
    auth: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    """Machine JWT своего экземпляра. Credential проверяется по БД на каждом запросе,
    поэтому отзыв credential сразу блокирует и уже выданные JWT."""
    if not auth:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Machine authentication required")
    try:
        payload = security._decode_token(auth.credentials)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid machine token")

    if _claim(payload, "token_use") != "machine_access" or _claim(payload, "aud") != "core-internal":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid machine token")
    credential_id = _claim(payload, "credential_id")
    credential = db.get(ServiceCredential, credential_id) if credential_id else None
    if not credential or credential.revoked_at is not None or credential.service_id != _claim(payload, "service_id"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Machine credential revoked")
    return payload


class RequireMachineScope:
    def __init__(self, required_scope: str):
        self.required_scope = required_scope

    def __call__(self, machine_token=Depends(get_current_machine_token)):
        if self.required_scope not in (machine_token.get("scopes") or []):
            raise HTTPException(status_code=403, detail=f"Scope '{self.required_scope}' required")
        return machine_token
