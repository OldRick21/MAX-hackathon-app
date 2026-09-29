"""Machine API своего экземпляра (CORE_API_SPEC.md §5, §7). Логика — services/service_registry.py."""
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from auth.dependencies import RequireMachineScope, json_body
from database.create_tables import get_db
from platform_core.errors import DomainError
from routes.private_admin import respond
from services import service_registry as svc

router = APIRouter()
BASE = "/api/v1/internal/service/{service_id}"


def machine(scope: str):
    """Scope проверяется до разбора тела и до If-Match; контекст — из проверенного machine token."""
    def dependency(request: Request, claims=Depends(RequireMachineScope(scope))) -> svc.MachineContext:
        return svc.MachineContext.of(claims, getattr(request.state, "request_id", None))
    return dependency


def mutate(db: Session, ctx: svc.MachineContext, action: str, target: str, fn: Callable[[], svc.Result]):
    """Изменение; отказ по правам или инварианту тоже попадает в журнал."""
    try:
        return respond(fn())
    except DomainError as error:
        db.rollback()
        if error.status in (403, 409, 412):
            ctx.audit(db, action, "request", target, {"message": error.message}, outcome="denied", error_code=error.code)
            db.commit()
        raise


@router.get(BASE + "/manifest")
def get_own_manifest(service_id: str, ctx=Depends(machine("manifest:write")), db: Session = Depends(get_db)):
    """Текущий manifest и его ETag (даже пустой)."""
    return respond(svc.get_manifest(db, ctx, service_id))


@router.put(BASE + "/manifest")
def replace_own_manifest(service_id: str, ctx=Depends(machine("manifest:write")),
                         if_match: Optional[str] = Header(None, alias="If-Match"), db: Session = Depends(get_db),
                         payload: Any = Depends(json_body)):
    """Заменить имя и меню: нет If-Match — 428, устаревший — 412."""
    return mutate(db, ctx, "service.manifest.publish", service_id,
                  lambda: svc.replace_manifest(db, ctx, service_id, payload, if_match))


@router.get(BASE + "/users/{user_id}/profiles")
def get_service_user_profiles(service_id: str, user_id: str, ctx=Depends(machine("profiles:read")),
                              db: Session = Depends(get_db)):
    """Профили участника в вузе своего экземпляра."""
    return respond(svc.get_user_profiles(db, ctx, service_id, user_id))


@router.get(BASE + "/members")
def list_service_members(service_id: str, ctx=Depends(machine("profiles:read")), db: Session = Depends(get_db)):
    """Участники вуза своего экземпляра: профили и имя из регистрации (расширение контракта)."""
    return respond(svc.list_members(db, ctx, service_id))


@router.get(BASE + "/groups")
def list_institution_groups(service_id: str, ctx=Depends(machine("groups:read")), db: Session = Depends(get_db)):
    """Учебные группы вуза своего экземпляра (§7.1)."""
    return respond(svc.list_groups(db, ctx, service_id))


@router.get(BASE + "/groups/{group_id}/members")
def get_institution_group_members(service_id: str, group_id: str, ctx=Depends(machine("groups:read")),
                                  db: Session = Depends(get_db)):
    """Состав учебной группы вуза своего экземпляра (§7.1)."""
    return respond(svc.get_group_members(db, ctx, service_id, group_id))


@router.get(BASE + "/roles")
def list_own_roles(service_id: str, limit: Optional[int] = Query(None), cursor: Optional[str] = Query(None),
                   ctx=Depends(machine("roles:read")), db: Session = Depends(get_db)):
    """Определения ролей своего экземпляра, по страницам (limit/cursor)."""
    return respond(svc.list_roles(db, ctx, service_id, limit, cursor))


@router.post(BASE + "/roles", status_code=201)
def create_own_role(service_id: str, ctx=Depends(machine("roles:write")), db: Session = Depends(get_db),
                    payload: Any = Depends(json_body)):
    """Создать роль своего экземпляра: 201 с Location и ETag."""
    return mutate(db, ctx, "role.create", service_id, lambda: svc.create_role(db, ctx, service_id, payload))


@router.get(BASE + "/roles/{role_code}")
def get_own_role(service_id: str, role_code: str, ctx=Depends(machine("roles:read")), db: Session = Depends(get_db)):
    """Определение роли и её ETag."""
    return respond(svc.get_role(db, ctx, service_id, role_code))


@router.patch(BASE + "/roles/{role_code}")
def update_own_role(service_id: str, role_code: str, ctx=Depends(machine("roles:write")), if_match: Optional[str] = Header(None, alias="If-Match"), db: Session = Depends(get_db),
                    payload: Any = Depends(json_body)):
    """Изменить имя, профили или права роли; сужение профилей при назначениях — 409 ROLE_IN_USE."""
    return mutate(db, ctx, "role.update", f"{service_id}:{role_code}",
                  lambda: svc.update_role(db, ctx, service_id, role_code, payload, if_match))


@router.delete(BASE + "/roles/{role_code}", status_code=204)
def delete_own_role(service_id: str, role_code: str, ctx=Depends(machine("roles:write")),
                    if_match: Optional[str] = Header(None, alias="If-Match"), db: Session = Depends(get_db)):
    """Удалить роль; назначенную — 409 ROLE_IN_USE (сначала снять назначения)."""
    return mutate(db, ctx, "role.delete", f"{service_id}:{role_code}",
                  lambda: svc.delete_role(db, ctx, service_id, role_code, if_match))


@router.get(BASE + "/users/{user_id}/profiles/{profile}/roles")
def get_own_assignments(service_id: str, user_id: str, profile: str, ctx=Depends(machine("assignments:read")),
                        db: Session = Depends(get_db)):
    """Назначенные роли, эффективные permissions и ETag набора."""
    return respond(svc.get_assignments(db, ctx, service_id, user_id, profile))


@router.put(BASE + "/users/{user_id}/profiles/{profile}/roles")
def replace_own_assignments(service_id: str, user_id: str, profile: str, ctx=Depends(machine("assignments:write")),
                            if_match: Optional[str] = Header(None, alias="If-Match"),
                            db: Session = Depends(get_db), payload: Any = Depends(json_body)):
    """Атомарно заменить набор ролей участника в профиле (If-Match обязателен)."""
    return mutate(db, ctx, "assignments.replace", f"{service_id}:{user_id}:{profile}",
                  lambda: svc.replace_assignments(db, ctx, service_id, user_id, profile, payload, if_match))
