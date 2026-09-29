"""Три независимые проверки private API вуза (CORE_API_SPEC.md §2.3).

1. Сетевая граница: публичный Nginx отвечает 404 на весь префикс; сюда доходят
   только запросы из внутренней сети (workload administration).
2. Machine JWT активного credential экземпляра administration этого вуза со
   scope institution:manage.
3. X-Actor-Token — service access человека в том же экземпляре administration,
   профиль admin, живые корневая и сервисная сессии, живое членство.
   Permissions считаются заново по БД на каждой операции; роли по умолчанию нет.
"""
from dataclasses import dataclass, field
from typing import List, Optional

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from database.create_tables import get_db
from database.tables import Institution
from auth.dependencies import bearer_scheme
from platform_core import catalog, ratelimit, registry
from platform_core.concurrency import is_uuid
from platform_core.errors import DomainError
import jwt
from auth.state import machine_state, service_state
from auth.security import security


@dataclass
class ActorContext:
    institution_id: str
    admin_service_id: str
    actor_id: str
    roles: List[str]
    permissions: List[str]
    credential_id: str
    request_id: str
    facade_request_id: Optional[str] = None
    extra: dict = field(default_factory=dict)

    @property
    def is_owner(self) -> bool:
        return catalog.OWNER_ROLE in self.roles

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise DomainError(403, "FORBIDDEN", f"Нужно разрешение {permission}")

    def audit(self, db: Session, action: str, target_type: str, target_id: str, details: Optional[dict] = None,
              outcome: str = "success", error_code: Optional[str] = None):
        return registry.audit(
            db, scope="institution", action=action, actor_user_id=self.actor_id, actor_kind="admin",
            institution_id=self.institution_id, target_type=target_type, target_id=target_id,
            details=details, outcome=outcome, error_code=error_code, request_id=self.request_id,
            facade_request_id=self.facade_request_id, machine_credential_id=self.credential_id,
        )


def _claim(payload, name):
    return getattr(payload, name, None) or payload.get(name)


def private_actor(
    institution_id: str,
    request: Request,
    auth: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    x_actor_token: Optional[str] = Header(None, alias="X-Actor-Token"),
    x_facade_request_id: Optional[str] = Header(None, alias="X-Facade-Request-ID"),
    db: Session = Depends(get_db),
) -> ActorContext:
    not_found = DomainError(404, "RESOURCE_NOT_FOUND", "Ресурс не найден")
    if not auth or not x_actor_token or not is_uuid(institution_id):
        raise not_found

    # --- 2. Machine credential administration этого вуза ---
    try:
        machine = security.decode(auth.credentials, "machine_access")
    except jwt.PyJWTError:
        raise DomainError(401, "UNAUTHENTICATED", "Machine token недействителен")
    if _claim(machine, "token_use") != "machine_access" or _claim(machine, "aud") != "core-internal":
        raise DomainError(401, "UNAUTHENTICATED", "Нужен machine token")
    if "institution:manage" not in (_claim(machine, "scopes") or []):
        raise not_found
    if _claim(machine, "institution_id") != institution_id:
        raise not_found
    admin_service = registry.admin_service_of(db, institution_id)
    if not admin_service or _claim(machine, "service_id") != admin_service.id:
        raise not_found
    machine_context = machine_state(db, machine, require_active=False)
    if not machine_context:
        raise DomainError(401, 'UNAUTHENTICATED', 'Machine credential or institution inactive')
    credential, _ = machine_context
    ratelimit.hit("credential", credential.id)
    institution = db.get(Institution, institution_id)
    if institution.status != 'active':
        raise DomainError(409, 'RESOURCE_INACTIVE', 'Institution is not active')

    # --- 3. Actor: человек в этом экземпляре administration ---
    try:
        actor = security.decode(x_actor_token, "service_access", admin_service.id)
    except jwt.PyJWTError:
        raise DomainError(401, "UNAUTHENTICATED", "Сессия администратора недействительна")
    user_id = actor.sub
    if (_claim(actor, "token_use") != "service_access" or _claim(actor, "aud") != f"service:{admin_service.id}"
            or _claim(actor, "institution_id") != institution_id or _claim(actor, "service_id") != admin_service.id):
        raise DomainError(401, "UNAUTHENTICATED", "Нужна сессия сервиса администрирования этого вуза")
    if _claim(actor, "profile") != "admin":
        raise DomainError(403, "FORBIDDEN", "Управление доступно только в профиле администратора")

    if not service_state(db, actor):
        raise DomainError(401, 'UNAUTHENTICATED', 'Actor session or membership inactive')

    roles = registry.assigned_roles(db, admin_service.id, user_id, "admin")
    permissions = registry.permissions_for(db, admin_service.id, roles, "admin")
    request_id = getattr(request.state, "request_id", None)
    return ActorContext(
        institution_id=institution_id, admin_service_id=admin_service.id, actor_id=user_id, roles=roles,
        permissions=permissions, credential_id=credential.id, request_id=request_id,
        facade_request_id=x_facade_request_id if is_uuid(x_facade_request_id) else None,
    )
