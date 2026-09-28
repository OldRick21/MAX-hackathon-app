from fastapi import HTTPException, APIRouter, Depends, Query
from sqlalchemy.orm import Session
from database.create_tables import get_db
from database.tables import Membership, ServiceInstance, StudyGroup, StudyGroupMember
from auth.dependencies import get_current_core_session
from platform_core import registry

router = APIRouter()


def service_card(db: Session, service: ServiceInstance, user_id: str, profile: str) -> dict:
    """ServiceView для shell: актуальные роли/permissions пользователя и меню, отфильтрованные по ним.

    Меню с required_permissions показывается, только если у профиля есть все права: иначе
    shell открывал бы раздел, в котором сервис ответит 403, и не видел бы прав на запись.
    """
    roles = registry.assigned_roles(db, service.id, user_id, profile)
    permissions = registry.permissions_for(db, service.id, roles)
    manifest = service.manifest or {}
    menus = []
    for m in sorted(manifest.get("menus", []), key=lambda m: (m.get("order", 0), m.get("id", ""))):
        if (profile in [p.lower() for p in m.get("profiles", [])]
                and set(m.get("required_permissions") or []) <= set(permissions)):
            menus.append({
                "id": m["id"],
                "display_name": m.get("titles", {}).get("ru", m["id"]),
                "locale": "ru",
                "entrypoint_path": m.get("entrypoint_path", "/"),
                "order": m.get("order", 0)
            })
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


@router.get("/api/v1/institution/{institution_id}/groups")
def list_my_groups(
    institution_id: str,
    profile: str = Query(..., description="Выбранный профиль"),
    session_data=Depends(get_current_core_session),
    db: Session = Depends(get_db)
):
    """Учебные группы для интерфейса: студенту — своя группа, преподавателю и админу — все с составом."""
    user, _ = session_data
    membership = db.get(Membership, (institution_id, user.id))
    if not membership or profile not in (membership.profiles or []):
        raise HTTPException(status_code=403, detail="FORBIDDEN: Profile not assigned")
    if profile == "student":
        return {"items": own_groups(db, institution_id, user.id), "next_cursor": None}
    groups = db.query(StudyGroup).filter(StudyGroup.institution_id == institution_id).order_by(StudyGroup.name_key).all()
    members = {}
    for row in db.query(StudyGroupMember).filter(StudyGroupMember.institution_id == institution_id):
        members.setdefault(row.group_id, []).append(row.user_id)
    return {"items": [{"id": g.id, "name": g.name, "user_ids": sorted(members.get(g.id, []))} for g in groups],
            "next_cursor": None}


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

    items = [service_card(db, s, user.id, profile_norm) for s in services
             if profile_norm in [p.lower() for p in s.supported_profiles]]

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
