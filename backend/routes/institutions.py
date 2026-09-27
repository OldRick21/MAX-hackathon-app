from fastapi import HTTPException, APIRouter, Depends, Query
from sqlalchemy.orm import Session
from database.create_tables import get_db
from database.tables import Membership, ServiceInstance
from auth.dependencies import get_current_core_session

router = APIRouter()

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
        "profiles": profiles
    }


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

    items = []
    for s in services:
        if profile_norm in [p.lower() for p in s.supported_profiles]:
            # Фильтрация меню манифеста по профилю
            menus = []
            manifest = s.manifest or {}
            for m in manifest.get("menus", []):
                if profile_norm in [p.lower() for p in m.get("profiles", [])]:
                    menus.append({
                        "id": m["id"],
                        "display_name": m.get("titles", {}).get("ru", m["id"]),
                        "locale": "ru",
                        "entrypoint_path": m.get("entrypoint_path", "/"),
                        "order": m.get("order", 0)
                    })

            items.append({
                "id": s.id,
                "institution_id": s.institution_id,
                "service_type": s.service_type,
                "deployment": s.deployment,
                "display_name": manifest.get("titles", {}).get("ru", s.service_type),
                "locale": "ru",
                "api_base_url": s.api_base_url,
                "client_base_url": s.client_base_url,
                "profile": profile_norm,
                "roles": [],
                "permissions": [],
                "menus": menus
            })

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

    manifest = service.manifest or {}
    menus = []
    for m in manifest.get("menus", []):
        if profile_norm in [p.lower() for p in m.get("profiles", [])]:
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
        "profile": profile_norm,
        "roles": [],
        "permissions": [],
        "menus": menus
    }

