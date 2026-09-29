from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session
from database.create_tables import get_db
from database.tables import Institution, Membership, ServiceInstance, StudyGroup, StudyGroupMember
from auth.authorization import ActorContext
from auth.dependencies import get_current_core_session, json_body
from platform_core import registry
from platform_core.concurrency import check_limit, decode_cursor, encode_cursor
from platform_core.errors import DomainError
from settings.config import settings
from routes.private_admin import respond
from services import institution_admin

router = APIRouter()

Profile = Literal["admin", "teacher", "student"]
Locale = Literal["ru", "en"]
PROFILE_QUERY = Query(..., description="Выбранный профиль: admin, teacher или student")
LOCALE_QUERY = Query("ru", description="Язык строк: точный перевод или ru")


def localized(titles: Optional[dict], locale: str, fallback: str) -> tuple:
    """Строка на запрошенном языке или на ru; возвращает (текст, фактический locale) (CORE_API_SPEC.md §3)."""
    titles = titles or {}
    for code in (locale, "ru"):
        if isinstance(titles.get(code), str) and titles[code]:
            return titles[code], code
    return fallback, "ru"


def paged(kind: str, user_id: str, scope: dict, rows: list, key, limit, cursor) -> dict:
    """Keyset-страница по UUID; курсор привязан к actor, фильтрам и locale."""
    limit = check_limit(limit)
    scope = {"kind": kind, "actor": user_id, **scope}
    after = decode_cursor(settings.CURSOR_SECRET_KEY, scope, cursor)
    rows = sorted((r for r in rows if after is None or key(r) > after), key=key)
    page, more = rows[:limit], len(rows) > limit
    return {"items": page, "next_cursor": encode_cursor(settings.CURSOR_SECRET_KEY, scope, key(page[-1])) if more else None}


def service_card(db: Session, service: ServiceInstance, user_id: str, profile: str, locale: str = "ru") -> dict:
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
    menus = []
    for m in chosen:
        name, used = localized(m.get("titles"), locale, m["id"])
        menus.append({"id": m["id"], "display_name": name, "locale": used,
                      "entrypoint_path": m.get("entrypoint_path", "/"), "order": m.get("order", 0)})
    name, used = localized(manifest.get("titles"), locale, service.service_type)
    return {
        "id": service.id,
        "institution_id": service.institution_id,
        "service_type": service.service_type,
        "deployment": service.deployment,
        "display_name": name,
        "locale": used,
        "api_base_url": service.api_base_url,
        "client_base_url": service.client_base_url,
        "profile": profile,
        "roles": roles,
        "permissions": permissions,
        "menus": menus
    }

def institution_card(m: Membership, locale: str) -> dict:
    inst = m.institution
    name, used = localized(inst.titles, locale, "Вуз")
    return {"id": inst.id, "display_name": name, "locale": used, "default_locale": inst.default_locale,
            "status": inst.status, "profiles": list(m.profiles or [])}


@router.get("/api/v1/institution")
def list_my_institutions(locale: Locale = LOCALE_QUERY, limit: Optional[int] = Query(None),
                         cursor: Optional[str] = Query(None), session_data=Depends(get_current_core_session),
                         db: Session = Depends(get_db)):
    """Свои вузы и профили в них, по страницам (UUID по возрастанию)."""
    user, _ = session_data
    rows = db.query(Membership).filter(Membership.user_id == user.id).all()
    page = paged("institutions", user.id, {"locale": locale}, rows, lambda m: m.institution_id, limit, cursor)
    return {"items": [institution_card(m, locale) for m in page["items"]], "next_cursor": page["next_cursor"]}


@router.get("/api/v1/institution/{institution_id}/profiles")
def list_my_profiles(institution_id: str, session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    """Получить свои профили в конкретном ВУЗе."""
    user, _ = session_data
    membership = db.query(Membership).filter(
        Membership.institution_id == institution_id,
        Membership.user_id == user.id
    ).first()

    if not membership:
        raise DomainError(404, "RESOURCE_NOT_FOUND", "Вуз не найден")
    return {
        "user_id": user.id,
        "institution_id": institution_id,
        "profiles": list(membership.profiles or []),
        "groups": own_groups(db, institution_id, user.id),
    }


def own_groups(db: Session, institution_id: str, user_id: str) -> list:
    ids = registry.user_group_ids(db, institution_id, user_id)
    return [{"id": g.id, "name": g.name} for g in db.query(StudyGroup).filter(StudyGroup.id.in_(ids))] if ids else []


def _member(db: Session, institution_id: str, user_id: str, profile: str) -> Membership:
    """Чужой вуз — 404 (не раскрываем), нет выбранного профиля — 403 (CORE_API_SPEC.md §6)."""
    membership = db.get(Membership, (institution_id, user_id))
    if not membership:
        raise DomainError(404, "RESOURCE_NOT_FOUND", "Вуз не найден")
    if profile not in (membership.profiles or []):
        raise DomainError(403, "FORBIDDEN", "У вас нет этого профиля в вузе")
    return membership


def _active(membership: Membership) -> None:
    if membership.institution.status != "active":
        raise DomainError(409, "RESOURCE_INACTIVE", "Вуз не активен")


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
    profile: Profile = PROFILE_QUERY,
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
def create_group(institution_id: str, request: Request, profile: Profile = PROFILE_QUERY, session_data=Depends(get_current_core_session), db: Session = Depends(get_db),
                 payload: Any = Depends(json_body)):
    ctx = _group_actor(db, request, institution_id, session_data[0].id, profile)
    return _group_change(db, ctx, lambda: institution_admin.create_group(db, ctx, payload))


@router.patch("/api/v1/institution/{institution_id}/groups/{group_id}")
def rename_group(institution_id: str, group_id: str, request: Request, profile: Profile = PROFILE_QUERY, if_match: Optional[str] = Header(None, alias="If-Match"),
                 session_data=Depends(get_current_core_session), db: Session = Depends(get_db),
                 payload: Any = Depends(json_body)):
    ctx = _group_actor(db, request, institution_id, session_data[0].id, profile)
    return _group_change(db, ctx, lambda: institution_admin.rename_group(db, ctx, group_id, payload, if_match))


@router.delete("/api/v1/institution/{institution_id}/groups/{group_id}")
def delete_group(institution_id: str, group_id: str, request: Request, profile: Profile = PROFILE_QUERY,
                 if_match: Optional[str] = Header(None, alias="If-Match"),
                 session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    ctx = _group_actor(db, request, institution_id, session_data[0].id, profile)
    return _group_change(db, ctx, lambda: institution_admin.delete_group(db, ctx, group_id, if_match))


@router.put("/api/v1/institution/{institution_id}/groups/{group_id}/members")
def replace_group_members(institution_id: str, group_id: str, request: Request, profile: Profile = PROFILE_QUERY,
                          if_match: Optional[str] = Header(None, alias="If-Match"),
                          session_data=Depends(get_current_core_session), db: Session = Depends(get_db),
                          payload: Any = Depends(json_body)):
    ctx = _group_actor(db, request, institution_id, session_data[0].id, profile)
    return _group_change(db, ctx, lambda: institution_admin.replace_group_members(db, ctx, group_id, payload, if_match))


@router.get("/api/v1/institution/{institution_id}/service")
def list_my_services(institution_id: str, profile: Profile = PROFILE_QUERY, locale: Locale = LOCALE_QUERY,
                     limit: Optional[int] = Query(None), cursor: Optional[str] = Query(None),
                     session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    """Включённые экземпляры с доступными профилю меню, по страницам (UUID по возрастанию)."""
    user, _ = session_data
    _active(_member(db, institution_id, user.id, profile))
    services = db.query(ServiceInstance).filter(ServiceInstance.institution_id == institution_id,
                                                ServiceInstance.enabled == True).all()  # noqa: E712
    cards = [service_card(db, s, user.id, profile, locale) for s in services if profile in (s.supported_profiles or [])]
    # В списке только экземпляры с доступными профилю меню; headless-сессия по-прежнему доступна по id.
    visible = [c for c in cards if c["menus"]]
    return paged("services", user.id, {"institution": institution_id, "profile": profile, "locale": locale},
                 visible, lambda c: c["id"], limit, cursor)


@router.get("/api/v1/institution/{institution_id}")
def get_my_institution(institution_id: str, locale: Locale = LOCALE_QUERY,
                       session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    """Карточка своего вуза; чужой — 404."""
    user, _ = session_data
    membership = db.get(Membership, (institution_id, user.id))
    if not membership:
        raise DomainError(404, "RESOURCE_NOT_FOUND", "Вуз не найден")
    return institution_card(membership, locale)


@router.get("/api/v1/institution/{institution_id}/service/{service_id}")
def get_my_service(institution_id: str, service_id: str, profile: Profile = PROFILE_QUERY, locale: Locale = LOCALE_QUERY,
                   session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    """Карточка экземпляра: не поддерживает профиль или нет в вузе — 404, нет профиля у пользователя — 403."""
    user, _ = session_data
    _active(_member(db, institution_id, user.id, profile))
    service = db.get(ServiceInstance, service_id)
    if not service or service.institution_id != institution_id or profile not in (service.supported_profiles or []):
        raise DomainError(404, "RESOURCE_NOT_FOUND", "Сервис не найден")
    if not service.enabled:
        raise DomainError(409, "RESOURCE_INACTIVE", "Сервис отключён")
    return service_card(db, service, user.id, profile, locale)
