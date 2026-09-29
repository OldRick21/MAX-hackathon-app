from typing import Any, Optional

from fastapi import HTTPException, APIRouter, Body, Depends, Header, Query, Request
from sqlalchemy.orm import Session
from database.create_tables import get_db
from database.tables import Institution, Membership, ServiceInstance, StudyGroup, StudyGroupMember
from auth.authorization import ActorContext
from auth.dependencies import get_current_core_session
from platform_core import registry
from platform_core.errors import DomainError
from routes.private_admin import respond
from services import institution_admin

router = APIRouter()


def service_card(db: Session, service: ServiceInstance, user_id: str, profile: str) -> dict:
    """ServiceView для shell: актуальные роли/permissions пользователя и меню, отфильтрованные по ним.

    Меню показывается, если оно объявлено для профиля и у профиля есть все его required_permissions:
    иначе shell открывал бы раздел, в котором сервис ответит 403 (CORE_API_SPEC.md §6 п.4).
    Администратор не исключение: доступ к сервису ему дают роли этого сервиса.
    """
    roles = registry.assigned_roles(db, service.id, user_id, profile)
    permissions = registry.permissions_for(db, service.id, roles, profile)
    manifest = service.manifest or {}
    ordered = sorted(manifest.get("menus", []), key=lambda m: (m.get("order", 0), m.get("id", "")))
    chosen = [m for m in ordered if profile in [p.lower() for p in m.get("profiles", [])]
              and set(m.get("required_permissions") or []) <= set(permissions)]
    menus = [{
        "id": m["id"],
        "display_name": m.get("titles", {}).get("ru", m["id"]),
        "locale": "ru",
        "entrypoint_path": m.get("entrypoint_path", "/"),
        "order": m.get("order", 0)
    } for m in chosen]
    return {
        "id": service.id,
        "institution_id": service.institution_id,
        "service_type": service.service_type,
        "deployment": service.deployment,
        "display_name": manifest.get("titles", {}).get("ru", service.service_type),
        "locale": "ru",
        "api_base_url": service.api_base_url,
        "client_base_url": service.client_base_url,
        "profile": profile,
        "roles": roles,
        "permissions": permissions,
        "menus": menus
    }

@router.get("/api/v1/institution")
def list_my_institutions(session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    """Получить список своих ВУЗов и назначенные профили."""
    user, _ = session_data
    memberships = db.query(Membership).filter(Membership.user_id == user.id).all()

    items = []
    for m in memberships:
        inst = m.institution
        items.append({
            "id": inst.id,
            "display_name": inst.titles.get("ru", "ВУЗ"),
            "locale": "ru",
            "default_locale": inst.default_locale,
            "status": inst.status,
            "profiles": m.profiles or []
        })

    return {"items": items, "next_cursor": None}


@router.get("/api/v1/institution/{institution_id}/profiles")
def list_my_profiles(institution_id: str, session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    """Получить свои профили в конкретном ВУЗе."""
    user, _ = session_data
    membership = db.query(Membership).filter(
        Membership.institution_id == institution_id,
        Membership.user_id == user.id
    ).first()

    profiles = membership.profiles if membership else []
    return {
        "user_id": user.id,
        "institution_id": institution_id,
        "profiles": profiles,
        "groups": own_groups(db, institution_id, user.id) if membership else [],
    }


def own_groups(db: Session, institution_id: str, user_id: str) -> list:
    ids = registry.user_group_ids(db, institution_id, user_id)
    return [{"id": g.id, "name": g.name} for g in db.query(StudyGroup).filter(StudyGroup.id.in_(ids))] if ids else []


def _member(db: Session, institution_id: str, user_id: str, profile: str) -> Membership:
    membership = db.get(Membership, (institution_id, user_id))
    if not membership or profile not in (membership.profiles or []):
        raise HTTPException(status_code=403, detail="FORBIDDEN: Profile not assigned")
    return membership


def manages_groups(db: Session, institution_id: str, user_id: str, profile: str) -> bool:
    """Группы ведёт администратор с правом groups.manage в администрировании (owner, membership_admin)."""
    if profile != "admin":
        return False
    admin = registry.admin_service_of(db, institution_id)
    if not admin:
        return False
    roles = registry.assigned_roles(db, admin.id, user_id, "admin")
    return "groups.manage" in registry.permissions_for(db, admin.id, roles, "admin")


@router.get("/api/v1/institution/{institution_id}/groups")
def list_my_groups(
    institution_id: str,
    profile: str = Query(..., description="Выбранный профиль"),
    session_data=Depends(get_current_core_session),
    db: Session = Depends(get_db)
):
    """Учебные группы для интерфейса (CORE_API_SPEC.md §7.1): студенту — только своя группа,
    преподавателю и администратору — все группы вуза с составом.

    Тому, кто ведёт группы (groups.manage), дополнительно: can_manage, ETag групп и список студентов вуза для выбора состава.
    """
    user, _ = session_data
    _member(db, institution_id, user.id, profile)
    mine = registry.user_group_ids(db, institution_id, user.id)
    query = db.query(StudyGroup).filter(StudyGroup.institution_id == institution_id)
    if profile == "student":
        query = query.filter(StudyGroup.id.in_(mine))
    groups = query.order_by(StudyGroup.name_key).all()
    members = {}
    for row in db.query(StudyGroupMember).filter(StudyGroupMember.institution_id == institution_id):
        members.setdefault(row.group_id, []).append(row.user_id)
    manage = manages_groups(db, institution_id, user.id, profile)
    items = []
    for g in groups:
        item = {"id": g.id, "name": g.name, "user_ids": sorted(members.get(g.id, []))}
        if manage:
            item.update(etag=institution_admin._group_tag(g), members_etag=institution_admin._members_tag(g))
        items.append(item)
    body = {"items": items, "next_cursor": None, "can_manage": manage,
            "my_group_ids": mine}
    if manage:
        body["students"] = sorted(m.user_id for m in db.query(Membership).filter(Membership.institution_id == institution_id)
                                  if "student" in (m.profiles or []))
    return body


def _group_actor(db: Session, request: Request, institution_id: str, user_id: str, profile: str) -> ActorContext:
    _member(db, institution_id, user_id, profile)
    institution = db.get(Institution, institution_id)
    if not institution or institution.status != "active":
        raise DomainError(409, "RESOURCE_INACTIVE", "Вуз не активен")
    if not manages_groups(db, institution_id, user_id, profile):
        raise DomainError(403, "FORBIDDEN", "Группы ведёт администратор вуза")
    admin = registry.admin_service_of(db, institution_id)
    return ActorContext(institution_id=institution_id, admin_service_id=admin.id if admin else "", actor_id=user_id,
                        roles=[], permissions=["groups.manage", "members.read"], credential_id=None,
                        request_id=getattr(request.state, "request_id", None))


def _group_change(db: Session, ctx: ActorContext, fn):
    try:
        return respond(fn())
    except DomainError:
        db.rollback()
        raise


@router.post("/api/v1/institution/{institution_id}/groups")
def create_group(institution_id: str, request: Request, profile: str = Query(...), payload: Any = Body(None),
                 session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    ctx = _group_actor(db, request, institution_id, session_data[0].id, profile)
    return _group_change(db, ctx, lambda: institution_admin.create_group(db, ctx, payload))


@router.patch("/api/v1/institution/{institution_id}/groups/{group_id}")
def rename_group(institution_id: str, group_id: str, request: Request, profile: str = Query(...), payload: Any = Body(None),
                 if_match: Optional[str] = Header(None, alias="If-Match"),
                 session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    ctx = _group_actor(db, request, institution_id, session_data[0].id, profile)
    return _group_change(db, ctx, lambda: institution_admin.rename_group(db, ctx, group_id, payload, if_match))


@router.delete("/api/v1/institution/{institution_id}/groups/{group_id}")
def delete_group(institution_id: str, group_id: str, request: Request, profile: str = Query(...),
                 if_match: Optional[str] = Header(None, alias="If-Match"),
                 session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    ctx = _group_actor(db, request, institution_id, session_data[0].id, profile)
    return _group_change(db, ctx, lambda: institution_admin.delete_group(db, ctx, group_id, if_match))


@router.put("/api/v1/institution/{institution_id}/groups/{group_id}/members")
def replace_group_members(institution_id: str, group_id: str, request: Request, profile: str = Query(...),
                          payload: Any = Body(None), if_match: Optional[str] = Header(None, alias="If-Match"),
                          session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    ctx = _group_actor(db, request, institution_id, session_data[0].id, profile)
    return _group_change(db, ctx, lambda: institution_admin.replace_group_members(db, ctx, group_id, payload, if_match))


@router.get("/api/v1/institution/{institution_id}/service")
def list_my_services(
    institution_id: str,
    profile: str = Query(..., description="Выбранный профиль"),
    session_data=Depends(get_current_core_session),
    db: Session = Depends(get_db)
):
    """Список сервисов и доступных меню для выбранного профиля."""
    user, _ = session_data
    profile_norm = profile.lower()

    # Проверка членства
    membership = db.query(Membership).filter(
        Membership.institution_id == institution_id,
        Membership.user_id == user.id
    ).first()

    if not membership or profile_norm not in [p.lower() for p in (membership.profiles or [])]:
        return {"items": [], "next_cursor": None}
    if membership.institution.status != "active":
        # Сервисы доступны только у active-вуза (CORE_API_SPEC.md §2.1).
        return {"items": [], "next_cursor": None}

    services = db.query(ServiceInstance).filter(
        ServiceInstance.institution_id == institution_id,
        ServiceInstance.enabled == True
    ).all()

    cards = [service_card(db, s, user.id, profile_norm) for s in services
             if profile_norm in [p.lower() for p in s.supported_profiles]]
    # В списке только экземпляры с доступными профилю меню; headless-сессия по-прежнему доступна по id.
    items = [c for c in cards if c["menus"]]

    return {"items": items, "next_cursor": None}


@router.get("/api/v1/institution/{institution_id}")
def get_my_institution(
    institution_id: str,
    session_data=Depends(get_current_core_session),
    db: Session = Depends(get_db)
):
    """Получить информацию о доступном ВУЗе."""
    user, _ = session_data
    membership = db.query(Membership).filter(
        Membership.institution_id == institution_id,
        Membership.user_id == user.id
    ).first()

    if not membership:
        raise HTTPException(status_code=404, detail="RESOURCE_NOT_FOUND: Institution not found or not accessible")

    inst = membership.institution
    return {
        "id": inst.id,
        "display_name": inst.titles.get("ru", "ВУЗ"),
        "locale": "ru",
        "default_locale": inst.default_locale,
        "status": inst.status,
        "profiles": membership.profiles or []
    }


@router.get("/api/v1/institution/{institution_id}/service/{service_id}")
def get_my_service(
    institution_id: str,
    service_id: str,
    profile: str = Query(..., description="Выбранный профиль"),
    session_data=Depends(get_current_core_session),
    db: Session = Depends(get_db)
):
    """Получить карточку конкретного сервиса."""
    user, _ = session_data
    profile_norm = profile.lower()

    membership = db.query(Membership).filter(
        Membership.institution_id == institution_id,
        Membership.user_id == user.id
    ).first()

    if not membership or profile_norm not in [p.lower() for p in (membership.profiles or [])]:
        raise HTTPException(status_code=403, detail="FORBIDDEN: Profile not assigned")
    if membership.institution.status != "active":
        raise HTTPException(status_code=409, detail="RESOURCE_INACTIVE: Institution is not active")

    service = db.query(ServiceInstance).filter(
        ServiceInstance.id == service_id,
        ServiceInstance.institution_id == institution_id
    ).first()

    if not service:
        raise HTTPException(status_code=404, detail="RESOURCE_NOT_FOUND: Service not found")
    if not service.enabled:
        raise HTTPException(status_code=409, detail="RESOURCE_INACTIVE: Service is disabled")
    if profile_norm not in [p.lower() for p in service.supported_profiles]:
        raise HTTPException(status_code=404, detail="RESOURCE_NOT_FOUND: Profile not supported by service")

    return service_card(db, service, user.id, profile_norm)
