from typing import Optional
from fastapi import Depends, HTTPException, Header, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, HTTPBasic
from sqlalchemy.orm import Session
from database.create_tables import get_db
from database.tables import User, CoreSession, ServiceInstance, Membership, RoleAssignment, ServiceRole, ServiceTypeCode
from settings.security import security
from settings.redis_client import revoked_tokens_redis

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

def get_current_machine_token(auth: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    if not auth:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Machine authentication required")
    try:
        payload = security._decode_token(auth.credentials)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid machine token")

    data = payload.data if hasattr(payload, "data") and isinstance(payload.data, dict) else {}
    token_use = getattr(payload, "token_use", None) or data.get("token_use")
    aud = getattr(payload, "aud", None) or data.get("aud")

    if token_use != "machine_access" or aud != "core-internal":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid machine token")
    return payload

class RequireMachineScope:
    def __init__(self, required_scope: str):
        self.required_scope = required_scope

    def __call__(self, machine_token=Depends(get_current_machine_token)):
        scopes = getattr(machine_token, "scopes", None) or machine_token.get("scopes", [])
        if self.required_scope not in scopes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Scope '{self.required_scope}' required")
        return machine_token

class RequirePrivateAdmin:
    """
    Проверяет двойную аутентификацию (MachineBearer + X-Actor-Token)
    и актуальный permission администратора из БД.
    """
    def __init__(self, required_permission: str):
        self.required_permission = required_permission

    def __call__(
        self,
        institution_id: str,
        auth: HTTPAuthorizationCredentials = Depends(bearer_scheme),
        x_actor_token: Optional[str] = Header(None, alias="X-Actor-Token"),
        db: Session = Depends(get_db)
    ):
        # 1. Проверка Machine Token (должен принадлежать сервису администрирования этого ВУЗа)
        if not auth or not x_actor_token:
            # Спецификация: на публичном ingress при отсутствии заголовков отдаем 404
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")

        try:
            m_payload = security._decode_token(auth.credentials)
            a_payload = security._decode_token(x_actor_token)
        except Exception:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid tokens")

        m_inst = getattr(m_payload, "institution_id", None) or m_payload.get("institution_id")
        m_srv_id = getattr(m_payload, "service_id", None) or m_payload.get("service_id")
        m_use = getattr(m_payload, "token_use", None) or m_payload.get("token_use")

        if m_use != "machine_access" or m_inst != institution_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: Machine mismatch")

        # Проверяем, что машинный токен принадлежит именно сервису типа administration
        admin_service = db.query(ServiceInstance).filter(
            ServiceInstance.id == m_srv_id,
            ServiceInstance.institution_id == institution_id,
            ServiceInstance.service_type == ServiceTypeCode.ADMINISTRATION.value
        ).first()
        if not admin_service:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: Not administration service")

        # 2. Проверка Actor Token (человек с профилем admin)
        a_use = getattr(a_payload, "token_use", None) or a_payload.get("token_use")
        a_inst = getattr(a_payload, "institution_id", None) or a_payload.get("institution_id")
        a_profile = getattr(a_payload, "profile", None) or a_payload.get("profile")
        parent_sid = getattr(a_payload, "parent_session_id", None) or a_payload.get("parent_session_id")
        actor_user_id = a_payload.sub

        if a_use != "service_access" or a_inst != institution_id or a_profile != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: Admin actor token required")

        if parent_sid and revoked_tokens_redis.exists(f"core_session_revoked:{parent_sid}"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Actor session terminated")

        # 3. Проверка актуального членства в БД
        membership = db.query(Membership).filter(
            Membership.institution_id == institution_id,
            Membership.user_id == actor_user_id
        ).first()
        if not membership or "admin" not in [p.lower() for p in (membership.profiles or [])]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Actor is not an admin of this institution")

        # 4. Проверка прав (permissions) из БД
        assignments = db.query(RoleAssignment).filter(
            RoleAssignment.service_id == admin_service.id,
            RoleAssignment.user_id == actor_user_id,
            RoleAssignment.profile == "admin"
        ).first()

        roles_to_check = assignments.roles if assignments and assignments.roles else ["admin_owner"]
        roles_in_db = db.query(ServiceRole).filter(
            ServiceRole.service_id == admin_service.id,
            ServiceRole.code.in_(roles_to_check)
        ).all()

        actor_permissions = set()
        for r in roles_in_db:
            actor_permissions.update(r.permissions or [])

        if self.required_permission not in actor_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: Actor missing required permission '{self.required_permission}'"
            )

        return {"actor_id": actor_user_id, "institution_id": institution_id, "service_id": admin_service.id}