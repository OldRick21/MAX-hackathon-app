"""Private API управления вузом: /api/v1/institution/{institution_id}/internal/...

Каждая функция получает уже проверенный ActorContext, повторно проверяет
permission и принадлежность ресурса вузу, выполняет изменение в одной
транзакции с проверкой инвариантов и записью в журнал.
"""
from dataclasses import dataclass, field
from typing import Callable, Optional

from sqlalchemy.orm import Session

from database.tables import (
    StudyGroup,
    StudyGroupMember,
    AuditEvent,
    IdempotencyRecord,
    Institution,
    Membership,
    RoleAssignment,
    ServiceCredential,
    ServiceInstance,
    ServiceRole,
    User,
    generate_uuid,
    utc_now,
)
from auth.authorization import ActorContext
from platform_core import catalog, registry
from platform_core.concurrency import (
    check_idempotency_key,
    check_limit,
    compute_etag,
    decode_cursor,
    encode_cursor,
    is_uuid,
    request_hash,
    require_if_match,
)
from platform_core.errors import DomainError, not_found, protected, validation
from settings.config import settings
from auth.security import hash_password


@dataclass
class Result:
    body: Optional[dict] = None
    status: int = 200
    etag: Optional[str] = None
    location: Optional[str] = None
    headers: dict = field(default_factory=dict)


def _base(ctx: ActorContext) -> str:
    return f"/api/v1/institution/{ctx.institution_id}/internal"


# --------------------------------------------------------------------------
# Представления
# --------------------------------------------------------------------------

def institution_view(inst: Institution) -> dict:
    return {"id": inst.id, "titles": inst.titles, "default_locale": inst.default_locale, "status": inst.status}


def member_group_map(db: Session, institution_id: str) -> dict:
    return {r.user_id: r.group_id for r in db.query(StudyGroupMember).filter(StudyGroupMember.institution_id == institution_id)}


def member_view(m: Membership) -> dict:
    return {"institution_id": m.institution_id, "user_id": m.user_id, "profiles": list(m.profiles or []),
            "created_at": registry.iso(m.created_at)}


def service_view(s: ServiceInstance) -> dict:
    return {
        "id": s.id, "institution_id": s.institution_id, "service_type": s.service_type,
        "deployment": s.deployment, "enabled": bool(s.enabled), "protected": bool(s.protected),
        "api_base_url": s.api_base_url, "client_base_url": s.client_base_url,
        "supported_profiles": list(s.supported_profiles or []),
        "manifest": s.manifest or {"titles": {"ru": s.service_type}, "menus": []},
        "created_at": registry.iso(s.created_at),
    }


def role_view(r: ServiceRole) -> dict:
    return {"service_id": r.service_id, "code": r.code, "titles": r.titles,
            "allowed_profiles": list(r.allowed_profiles or []), "permissions": list(r.permissions or []),
            "system": bool(r.system)}


def credential_view(c: ServiceCredential) -> dict:
    return {"id": c.id, "client_id": c.client_id, "service_id": c.service_id,
            "created_at": registry.iso(c.created_at), "revoked_at": registry.iso(c.revoked_at)}


def assignment_view(db: Session, service: ServiceInstance, user_id: str, profile: str) -> dict:
    roles = registry.assigned_roles(db, service.id, user_id, profile)
    return {"institution_id": service.institution_id, "service_id": service.id, "user_id": user_id,
            "profile": profile, "roles": roles, "permissions": registry.permissions_for(db, service.id, roles)}


# --------------------------------------------------------------------------
# Общие проверки
# --------------------------------------------------------------------------

def _uuid(value: str, what: str) -> str:
    if not is_uuid(value):
        raise not_found(f"{what} не найден")
    return value


def _service(db: Session, ctx: ActorContext, service_id: str) -> ServiceInstance:
    _uuid(service_id, "Сервис")
    service = db.get(ServiceInstance, service_id)
    if not service or service.institution_id != ctx.institution_id:
        raise not_found("Экземпляр сервиса не найден в этом вузе")
    return service


def _member(db: Session, ctx: ActorContext, user_id: str) -> Membership:
    _uuid(user_id, "Участник")
    member = db.get(Membership, (ctx.institution_id, user_id))
    if not member:
        raise not_found("Участник не найден в этом вузе")
    return member


def _body(payload, allowed: set, required: set = frozenset(), min_fields: int = 0) -> dict:
    if not isinstance(payload, dict):
        raise validation("Тело запроса должно быть JSON-объектом", "body")
    unknown = set(payload) - allowed
    if unknown:
        raise validation(f"Неизвестные поля: {', '.join(sorted(unknown))}", sorted(unknown)[0])
    missing = required - set(payload)
    if missing:
        raise validation(f"Обязательные поля: {', '.join(sorted(missing))}", sorted(missing)[0])
    if len(payload) < min_fields:
        raise validation("Нужно передать хотя бы одно поле", "body")
    return payload


def _page(ctx: ActorContext, kind: str, query, key_column, key_of: Callable, view: Callable, limit, cursor,
          extra_scope: Optional[dict] = None) -> Result:
    limit = check_limit(limit)
    scope = {"kind": kind, "institution": ctx.institution_id, "actor": ctx.actor_id,
             "permissions": ctx.permissions, **(extra_scope or {})}
    after = decode_cursor(settings.CURSOR_SECRET_KEY, scope, cursor)
    if after is not None:
        query = query.filter(key_column > after)
    rows = query.order_by(key_column.asc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(settings.CURSOR_SECRET_KEY, scope, key_of(rows[-1])) if has_more and rows else None
    return Result({"items": [view(r) for r in rows], "next_cursor": next_cursor})


def _deny_owner_changes(db: Session, ctx: ActorContext, target_user_id: str) -> None:
    """Изменять членство владельца может только владелец: иначе membership_admin
    мог бы снимать owners и фактически захватить управление вузом."""
    if target_user_id != ctx.actor_id and not ctx.is_owner and \
            target_user_id in registry.owner_ids(db, ctx.institution_id, ctx.admin_service_id):
        raise DomainError(403, "FORBIDDEN", "Изменять участника с ролью владельца может только владелец")


# --------------------------------------------------------------------------
# Вуз и каталог типов
# --------------------------------------------------------------------------

def get_institution(db: Session, ctx: ActorContext) -> Result:
    ctx.require("institution.read")
    inst = db.get(Institution, ctx.institution_id)
    view = institution_view(inst)
    return Result(view, etag=compute_etag(view))


def patch_institution(db: Session, ctx: ActorContext, payload, if_match: Optional[str]) -> Result:
    ctx.require("institution.update")
    body = _body(payload, {"titles", "default_locale"}, min_fields=1)
    changes = {}
    if "titles" in body:
        changes["titles"] = catalog.check_localized(body["titles"], "titles")
    if "default_locale" in body:
        changes["default_locale"] = catalog.check_locale(body["default_locale"])
    inst = registry.lock_institution(db, ctx.institution_id)
    before = institution_view(inst)
    require_if_match(if_match, compute_etag(before))
    for key, value in changes.items():
        setattr(inst, key, value)
    after = institution_view(inst)
    ctx.audit(db, "institution.update", "institution", inst.id,
              {"before": {k: before[k] for k in changes}, "after": changes})
    db.commit()
    return Result(after, etag=compute_etag(after))


def list_service_types(ctx: ActorContext) -> Result:
    ctx.require("institution.read")
    return Result({"items": catalog.public_service_types()})


# --------------------------------------------------------------------------
# Участники
# --------------------------------------------------------------------------

def list_members(db: Session, ctx: ActorContext, limit, cursor) -> Result:
    ctx.require("members.read")
    query = db.query(Membership).filter(Membership.institution_id == ctx.institution_id)
    groups = member_group_map(db, ctx.institution_id)
    from services.join_requests import member_names
    names = member_names(db, ctx.institution_id)
    return _page(ctx, "members", query, Membership.user_id, lambda m: m.user_id,
                 lambda m: {**member_view(m), "group_id": groups.get(m.user_id), "full_name": names.get(m.user_id)},
                 limit, cursor)


def get_member(db: Session, ctx: ActorContext, user_id: str) -> Result:
    ctx.require("members.read")
    member = _member(db, ctx, user_id)
    view = member_view(member)
    # Версия участника — по профилям, без группы: группа меняется отдельной операцией.
    group = db.get(StudyGroupMember, (ctx.institution_id, user_id))
    return Result({**view, "group_id": group.group_id if group else None}, etag=compute_etag(view))


def add_member(db: Session, ctx: ActorContext, payload) -> Result:
    ctx.require("members.manage")
    body = _body(payload, {"user_id", "profiles", "group_id"}, {"user_id", "profiles"})
    user_id = body["user_id"]
    if not is_uuid(user_id):
        raise validation("user_id должен быть UUID", "user_id")
    profiles = catalog.check_profiles(body["profiles"])
    registry.lock_institution(db, ctx.institution_id)
    if not db.get(User, user_id):
        raise DomainError(404, "RESOURCE_NOT_FOUND",
                          "Пользователь не найден. Он должен хотя бы раз войти в приложение через MAX")
    if db.get(Membership, (ctx.institution_id, user_id)):
        raise DomainError(409, "MEMBERSHIP_ALREADY_EXISTS", "Пользователь уже состоит в этом вузе")
    group = None
    if body.get("group_id") is not None:
        # Группу при добавлении может задать только тот, кто ведёт группы, и только студенту.
        ctx.require("groups.manage")
        if "student" not in profiles:
            raise validation("Группа назначается только студенту", "group_id")
        group = _group(db, ctx, body["group_id"])
    member = Membership(institution_id=ctx.institution_id, user_id=user_id, profiles=profiles, created_at=utc_now())
    db.add(member)
    db.flush()
    if group:
        db.add(StudyGroupMember(institution_id=ctx.institution_id, user_id=user_id, group_id=group.id))
        group.members_revision += 1
    ctx.audit(db, "member.add", "member", user_id, {"profiles": profiles, "group_id": group.id if group else None})
    db.commit()
    view = member_view(member)
    return Result(view, status=201, etag=compute_etag(view), location=f"{_base(ctx)}/members/{user_id}")


def replace_profiles(db: Session, ctx: ActorContext, user_id: str, payload, if_match: Optional[str]) -> Result:
    ctx.require("members.manage")
    body = _body(payload, {"profiles"}, {"profiles"})
    profiles = catalog.check_profiles(body["profiles"])
    registry.lock_institution(db, ctx.institution_id)
    member = _member(db, ctx, user_id)
    before = member_view(member)
    require_if_match(if_match, compute_etag(before))
    _deny_owner_changes(db, ctx, user_id)
    removed = [p for p in before["profiles"] if p not in profiles]
    if "admin" in removed:
        registry.ensure_not_last_owner(db, ctx.institution_id, ctx.admin_service_id, user_id)
    member.profiles = profiles
    if removed:
        # Снятие профиля отзывает его роли и сервисные сессии в той же транзакции.
        registry.drop_assignments(db, ctx.institution_id, user_id, removed)
        registry.revoke_service_sessions(db, institution_id=ctx.institution_id, user_id=user_id, profiles=removed)
        if "student" in removed:
            registry.drop_group_membership(db, ctx.institution_id, user_id)
    ctx.audit(db, "member.profiles.replace", "member", user_id,
              {"before": before["profiles"], "after": profiles})
    db.commit()
    view = member_view(member)
    return Result(view, etag=compute_etag(view))


def remove_member(db: Session, ctx: ActorContext, user_id: str, if_match: Optional[str]) -> Result:
    ctx.require("members.manage")
    registry.lock_institution(db, ctx.institution_id)
    member = _member(db, ctx, user_id)
    before = member_view(member)
    require_if_match(if_match, compute_etag(before))
    _deny_owner_changes(db, ctx, user_id)
    registry.ensure_not_last_owner(db, ctx.institution_id, ctx.admin_service_id, user_id)
    registry.drop_assignments(db, ctx.institution_id, user_id)
    registry.revoke_service_sessions(db, institution_id=ctx.institution_id, user_id=user_id)
    registry.drop_group_membership(db, ctx.institution_id, user_id)
    from database.tables import JoinRequest
    db.query(JoinRequest).filter(JoinRequest.institution_id == ctx.institution_id,
                                 JoinRequest.user_id == user_id).delete(synchronize_session=False)
    db.delete(member)
    ctx.audit(db, "member.remove", "member", user_id, {"profiles": before["profiles"]})
    db.commit()
    return Result(status=204)


# --------------------------------------------------------------------------
# Учебные группы: ведёт администратор вуза (groups.manage), читают участники и сервисы
# --------------------------------------------------------------------------

def group_view(g: StudyGroup) -> dict:
    return {"id": g.id, "name": g.name}


def _group(db: Session, ctx: ActorContext, group_id: str) -> StudyGroup:
    group = db.get(StudyGroup, group_id) if is_uuid(group_id) else None
    if not group or group.institution_id != ctx.institution_id:
        raise not_found("Группа не найдена")
    return group


def _group_tag(g: StudyGroup) -> str:
    return compute_etag({"group": g.id, "revision": g.revision})


def _members_tag(g: StudyGroup) -> str:
    return compute_etag({"group_members": g.id, "revision": g.members_revision})


def _group_name(payload) -> tuple:
    body = _body(payload, {"name"}, {"name"})
    name = body["name"].strip() if isinstance(body["name"], str) else ""
    if not name or len(name) > 100:
        raise validation("Название группы — от 1 до 100 символов", "name")
    return name, name.casefold()


def _name_taken(db: Session, ctx: ActorContext, key: str, except_id: Optional[str] = None) -> None:
    query = db.query(StudyGroup).filter(StudyGroup.institution_id == ctx.institution_id, StudyGroup.name_key == key)
    if except_id:
        query = query.filter(StudyGroup.id != except_id)
    if query.first():
        raise DomainError(409, "GROUP_ALREADY_EXISTS", "Группа с таким названием уже есть")


def list_groups(db: Session, ctx: ActorContext, limit, cursor) -> Result:
    ctx.require("members.read")
    query = db.query(StudyGroup).filter(StudyGroup.institution_id == ctx.institution_id)
    return _page(ctx, "groups", query, StudyGroup.id, lambda g: g.id, group_view, limit, cursor)


def get_group(db: Session, ctx: ActorContext, group_id: str) -> Result:
    ctx.require("members.read")
    group = _group(db, ctx, group_id)
    return Result(group_view(group), etag=_group_tag(group))


def create_group(db: Session, ctx: ActorContext, payload) -> Result:
    ctx.require("groups.manage")
    name, key = _group_name(payload)
    registry.lock_institution(db, ctx.institution_id)
    _name_taken(db, ctx, key)
    group = StudyGroup(id=generate_uuid(), institution_id=ctx.institution_id, name=name, name_key=key)
    db.add(group)
    db.flush()
    ctx.audit(db, "group.create", "group", group.id, {"name": name})
    db.commit()
    return Result(group_view(group), status=201, etag=_group_tag(group), location=f"{_base(ctx)}/groups/{group.id}")


def rename_group(db: Session, ctx: ActorContext, group_id: str, payload, if_match: Optional[str]) -> Result:
    ctx.require("groups.manage")
    name, key = _group_name(payload)
    registry.lock_institution(db, ctx.institution_id)
    group = _group(db, ctx, group_id)
    require_if_match(if_match, _group_tag(group))
    _name_taken(db, ctx, key, group.id)
    before = group.name
    group.name, group.name_key, group.revision = name, key, group.revision + 1
    ctx.audit(db, "group.rename", "group", group.id, {"before": before, "after": name})
    db.commit()
    return Result(group_view(group), etag=_group_tag(group))


def delete_group(db: Session, ctx: ActorContext, group_id: str, if_match: Optional[str]) -> Result:
    """Удаляется только пустая группа. Ссылки сервисов (например, занятия) ядро не видит:
    сервис покажет такую группу как удалённую."""
    ctx.require("groups.manage")
    registry.lock_institution(db, ctx.institution_id)
    group = _group(db, ctx, group_id)
    require_if_match(if_match, _group_tag(group))
    if db.query(StudyGroupMember).filter(StudyGroupMember.group_id == group.id).first():
        raise DomainError(409, "GROUP_IN_USE", "В группе есть студенты. Сначала уберите их")
    ctx.audit(db, "group.delete", "group", group.id, {"name": group.name})
    db.delete(group)
    db.commit()
    return Result(status=204)


def group_members(db: Session, group: StudyGroup) -> list:
    return sorted(m.user_id for m in db.query(StudyGroupMember).filter(StudyGroupMember.group_id == group.id))


def get_group_members(db: Session, ctx: ActorContext, group_id: str) -> Result:
    ctx.require("members.read")
    group = _group(db, ctx, group_id)
    return Result({"group_id": group.id, "user_ids": group_members(db, group)}, etag=_members_tag(group))


def set_member_group(db: Session, ctx: ActorContext, user_id: str, payload) -> Result:
    """Назначить студенту группу или снять её (group_id=null) одним действием.

    Перевод атомарный: старая и новая группы меняются в одной транзакции, без промежутка
    «без группы», как было бы при двух заменах состава.
    """
    ctx.require("groups.manage")
    body = _body(payload, {"group_id"}, {"group_id"})
    registry.lock_institution(db, ctx.institution_id)
    member = _member(db, ctx, user_id)
    target = _group(db, ctx, body["group_id"]) if body["group_id"] is not None else None
    if target and "student" not in (member.profiles or []):
        raise DomainError(422, "INVALID_REFERENCE", "Группа назначается только студенту",
                          [{"path": "group_id", "message": "У участника нет профиля «Студент»"}])
    current = db.get(StudyGroupMember, (ctx.institution_id, user_id))
    before = current.group_id if current else None
    if before != (target.id if target else None):
        if current:
            old = db.get(StudyGroup, current.group_id)
            if old:
                old.members_revision += 1
            db.delete(current)
            db.flush()
        if target:
            db.add(StudyGroupMember(institution_id=ctx.institution_id, user_id=user_id, group_id=target.id))
            target.members_revision += 1
        ctx.audit(db, "member.group.set", "member", user_id, {"before": before, "after": target.id if target else None})
        db.commit()
    return Result({"user_id": user_id, "group": group_view(target) if target else None})


def replace_group_members(db: Session, ctx: ActorContext, group_id: str, payload, if_match: Optional[str]) -> Result:
    """Атомарная замена состава: до 500 студентов вуза, каждый — максимум в одной группе."""
    ctx.require("groups.manage")
    body = _body(payload, {"user_ids"}, {"user_ids"})
    user_ids = body["user_ids"]
    if not isinstance(user_ids, list) or len(user_ids) > 500 or not all(is_uuid(u) for u in user_ids) \
            or len(set(user_ids)) != len(user_ids):
        raise validation("user_ids — до 500 разных UUID", "user_ids")
    registry.lock_institution(db, ctx.institution_id)
    group = _group(db, ctx, group_id)
    require_if_match(if_match, _members_tag(group))
    not_students = []
    for i, uid in enumerate(user_ids):
        member = db.get(Membership, (ctx.institution_id, uid))
        if not member or "student" not in (member.profiles or []):
            not_students.append({"path": f"user_ids[{i}]", "message": f"{uid} — не студент этого вуза"})
    if not_students:
        raise DomainError(422, "INVALID_REFERENCE", "В группу можно добавить только студентов вуза", not_students)
    taken = db.query(StudyGroupMember).filter(StudyGroupMember.institution_id == ctx.institution_id,
                                              StudyGroupMember.user_id.in_(user_ids),
                                              StudyGroupMember.group_id != group.id).all() if user_ids else []
    if taken:
        raise DomainError(409, "STUDENT_ALREADY_GROUPED", "Студент уже состоит в другой группе. Сначала уберите его оттуда",
                          [{"path": "user_ids", "message": t.user_id} for t in taken])
    before = group_members(db, group)
    db.query(StudyGroupMember).filter(StudyGroupMember.group_id == group.id).delete(synchronize_session=False)
    for uid in user_ids:
        db.add(StudyGroupMember(institution_id=ctx.institution_id, user_id=uid, group_id=group.id))
    group.members_revision += 1
    ctx.audit(db, "group.members.replace", "group", group.id,
              {"added": sorted(set(user_ids) - set(before)), "removed": sorted(set(before) - set(user_ids))})
    db.commit()
    return Result({"group_id": group.id, "user_ids": sorted(user_ids)}, etag=_members_tag(group))


# --------------------------------------------------------------------------
# Экземпляры сервисов
# --------------------------------------------------------------------------

def list_services(db: Session, ctx: ActorContext, limit, cursor) -> Result:
    ctx.require("services.read")
    query = db.query(ServiceInstance).filter(ServiceInstance.institution_id == ctx.institution_id)
    return _page(ctx, "services", query, ServiceInstance.id, lambda s: s.id, service_view, limit, cursor)


def get_service(db: Session, ctx: ActorContext, service_id: str) -> Result:
    ctx.require("services.read")
    view = service_view(_service(db, ctx, service_id))
    return Result(view, etag=compute_etag(view))


def _replay(db: Session, ctx: ActorContext, record: IdempotencyRecord) -> Result:
    service = db.get(ServiceInstance, record.resource_id) if record.resource_id else None
    if not service or service.institution_id != ctx.institution_id:
        raise not_found("Созданный по этому ключу экземпляр уже удалён")
    view = service_view(service)
    return Result(view, status=201, etag=compute_etag(view), location=f"{_base(ctx)}/services/{service.id}")


def install_service(db: Session, ctx: ActorContext, payload, idempotency_key: Optional[str]) -> Result:
    ctx.require("services.manage")
    key = check_idempotency_key(idempotency_key)
    if not isinstance(payload, dict):
        raise validation("Тело запроса должно быть JSON-объектом", "body")
    path = f"{_base(ctx)}/services"
    digest = request_hash(payload)
    registry.lock_institution(db, ctx.institution_id)

    now = utc_now()
    record = db.query(IdempotencyRecord).filter(
        IdempotencyRecord.actor_user_id == ctx.actor_id, IdempotencyRecord.institution_id == ctx.institution_id,
        IdempotencyRecord.method == "POST", IdempotencyRecord.path == path, IdempotencyRecord.key == key,
    ).first()
    if record:
        expires = record.expires_at if record.expires_at.tzinfo else record.expires_at.replace(tzinfo=now.tzinfo)
        if expires <= now:
            db.delete(record)
            db.flush()
        elif record.request_hash != digest:
            raise DomainError(409, "IDEMPOTENCY_CONFLICT", "Ключ уже использован с другим телом запроса")
        else:
            return _replay(db, ctx, record)

    deployment = payload.get("deployment")
    service_type = payload.get("service_type")
    approved = registry.approved_hosts(db, ctx.institution_id)
    if deployment == "cloud":
        _body(payload, {"service_type", "deployment"}, {"service_type", "deployment"})
        if service_type not in catalog.CLOUD_INSTALLABLE:
            raise validation("Через интерфейс устанавливаются облачные типы schedule и user-profile", "service_type")
    elif deployment == "local" and catalog.is_custom(service_type):
        # Свой сервис вуза: код, название и профили задаёт администратор, меню и роли
        # публикует сам сервис через machine API после выдачи ключа.
        _body(payload, {"service_type", "deployment", "api_base_url", "client_base_url", "titles", "supported_profiles"},
              {"service_type", "deployment", "api_base_url", "client_base_url", "titles", "supported_profiles"})
        titles = catalog.check_localized(payload["titles"], "titles")
        profiles = catalog.check_profiles(payload["supported_profiles"], "supported_profiles")
        # Администраторы видят любой сервис вуза: профиль admin входит всегда.
        if "admin" not in profiles:
            profiles = [*profiles, "admin"]
        api_url = catalog.check_api_url(payload["api_base_url"], approved)
        client_url = catalog.check_origin(payload["client_base_url"], approved)
    elif deployment == "local":
        _body(payload, {"service_type", "deployment", "api_base_url", "client_base_url"},
              {"service_type", "deployment", "api_base_url", "client_base_url"})
        if service_type not in catalog.LOCAL_INSTALLABLE:
            raise validation("Локально устанавливаются coursework и свои сервисы custom.<код>", "service_type")
        api_url = catalog.check_api_url(payload["api_base_url"], approved)
        client_url = catalog.check_origin(payload["client_base_url"], approved)
        titles, profiles = None, None
    else:
        raise validation("deployment: cloud или local", "deployment")

    if db.query(ServiceInstance).filter(ServiceInstance.institution_id == ctx.institution_id,
                                        ServiceInstance.service_type == service_type).first():
        raise DomainError(409, "SERVICE_ALREADY_EXISTS", "Сервис с таким кодом уже подключён в вузе")

    if deployment == "cloud":
        service = registry.create_cloud_instance(db, ctx.institution_id, service_type)
    else:
        service = registry.create_local_instance(db, ctx.institution_id, service_type, api_url, client_url,
                                                 titles, profiles)
    view = service_view(service)
    db.add(IdempotencyRecord(actor_user_id=ctx.actor_id, institution_id=ctx.institution_id, method="POST",
                             path=path, key=key, request_hash=digest, resource_id=service.id,
                             response={"id": service.id}, expires_at=now + registry.IDEMPOTENCY_TTL))
    ctx.audit(db, "service.install", "service", service.id,
              {"service_type": service_type, "deployment": deployment,
               "api_base_url": service.api_base_url, "client_base_url": service.client_base_url})
    db.commit()
    return Result(view, status=201, etag=compute_etag(view), location=f"{path}/{service.id}")


def patch_service(db: Session, ctx: ActorContext, service_id: str, payload, if_match: Optional[str]) -> Result:
    ctx.require("services.manage")
    body = _body(payload, {"enabled", "api_base_url", "client_base_url"}, min_fields=1)
    if "enabled" in body and not isinstance(body["enabled"], bool):
        raise validation("enabled — true или false", "enabled")
    registry.lock_institution(db, ctx.institution_id)
    service = _service(db, ctx, service_id)
    before = service_view(service)
    require_if_match(if_match, compute_etag(before))
    if service.protected:
        raise protected("Сервис администрирования нельзя отключить или перенастроить")

    url_change = "api_base_url" in body or "client_base_url" in body
    changes = {}
    if url_change:
        if service.deployment != "local":
            raise validation("Адреса облачного сервиса задаёт платформа", "api_base_url")
        if body.get("enabled") is True:
            raise validation("Смена адреса выключает экземпляр; включите его отдельным запросом", "enabled")
        approved = registry.approved_hosts(db, ctx.institution_id)
        if "api_base_url" in body:
            changes["api_base_url"] = catalog.check_api_url(body["api_base_url"], approved)
        if "client_base_url" in body:
            changes["client_base_url"] = catalog.check_origin(body["client_base_url"], approved)
        changes["enabled"] = False
    elif "enabled" in body:
        changes["enabled"] = body["enabled"]

    if changes.get("enabled") is True and not (service.manifest or {}).get("menus"):
        raise DomainError(409, "MANIFEST_REQUIRED",
                          "Нельзя включить сервис без меню: backend сервиса ещё не опубликовал manifest")
    for key, value in changes.items():
        setattr(service, key, value)
    if changes.get("enabled") is False:
        registry.revoke_service_sessions(db, service_id=service.id)
    after = service_view(service)
    ctx.audit(db, "service.update", "service", service.id,
              {"before": {k: before[k] for k in changes}, "after": changes})
    db.commit()
    return Result(after, etag=compute_etag(after))


def uninstall_service(db: Session, ctx: ActorContext, service_id: str, if_match: Optional[str]) -> Result:
    ctx.require("services.manage")
    registry.lock_institution(db, ctx.institution_id)
    service = _service(db, ctx, service_id)
    before = service_view(service)
    require_if_match(if_match, compute_etag(before))
    if service.protected or service.service_type == "administration":
        raise protected("Сервис администрирования предоставляется вузу по умолчанию и не может быть удалён")
    now = utc_now()
    for cred in service.credentials:
        if cred.revoked_at is None:
            cred.revoked_at = now
    registry.revoke_service_sessions(db, service_id=service.id)
    ctx.audit(db, "service.uninstall", "service", service.id,
              {"service_type": service.service_type, "deployment": service.deployment})
    # Каскад удаляет роли, назначения, сессии, credentials и binding. UUID не
    # переиспользуется (uuid4), слот типа освобождается; журнал сохраняется.
    db.delete(service)
    db.commit()
    return Result(status=204)


def replace_manifest(db: Session, ctx: ActorContext, service_id: str, payload, if_match: Optional[str]) -> Result:
    """Расширение контракта: manifest local-экземпляра до публикации backend вуза.

    Manifest облачных типов задаёт платформа; у administration он защищён.
    """
    ctx.require("services.manage")
    registry.lock_institution(db, ctx.institution_id)
    service = _service(db, ctx, service_id)
    before = service_view(service)
    require_if_match(if_match, compute_etag(before))
    if service.deployment != "local":
        raise protected("Manifest облачного сервиса задаёт платформа")
    manifest = catalog.check_manifest(payload, service.service_type, service.supported_profiles or [])
    if service.enabled and not manifest["menus"]:
        raise DomainError(409, "MANIFEST_REQUIRED", "У включённого сервиса должен остаться хотя бы один пункт меню")
    service.manifest = manifest
    after = service_view(service)
    ctx.audit(db, "service.manifest.replace", "service", service.id, {"menus": [m["id"] for m in manifest["menus"]]})
    db.commit()
    return Result(after, etag=compute_etag(after))


# --------------------------------------------------------------------------
# Роли и назначения
# --------------------------------------------------------------------------

def list_roles(db: Session, ctx: ActorContext, service_id: str, limit, cursor) -> Result:
    ctx.require("services.read")
    service = _service(db, ctx, service_id)
    query = db.query(ServiceRole).filter(ServiceRole.service_id == service.id)
    return _page(ctx, "roles", query, ServiceRole.code, lambda r: r.code, role_view, limit, cursor,
                 {"service": service.id})


def _role(db: Session, service: ServiceInstance, code: str) -> ServiceRole:
    if not isinstance(code, str) or not catalog.CODE_RE.match(code):
        raise not_found("Роль не найдена")
    role = db.get(ServiceRole, (service.id, code))
    if not role:
        raise not_found("Роль не найдена")
    return role


def get_role(db: Session, ctx: ActorContext, service_id: str, code: str) -> Result:
    ctx.require("services.read")
    view = role_view(_role(db, _service(db, ctx, service_id), code))
    return Result(view, etag=compute_etag(view))


def _role_holders(db: Session, service_id: str, code: str):
    return [row for row in db.query(RoleAssignment).filter(RoleAssignment.service_id == service_id).all()
            if code in (row.roles or [])]


def create_role(db: Session, ctx: ActorContext, service_id: str, payload) -> Result:
    ctx.require("roles.manage")
    registry.lock_institution(db, ctx.institution_id)
    service = _service(db, ctx, service_id)
    if service.protected:
        raise protected("Роли администрирования системные и не изменяются", status=403)
    role = catalog.check_role_input(payload, service.service_type, service.supported_profiles or [])
    if db.get(ServiceRole, (service.id, role["code"])):
        raise DomainError(409, "ROLE_ALREADY_EXISTS", "Роль с таким кодом уже есть в этом сервисе")
    row = ServiceRole(service_id=service.id, system=False, **role)
    db.add(row)
    db.flush()
    ctx.audit(db, "role.create", "role", f"{service.id}:{role['code']}", role)
    db.commit()
    view = role_view(row)
    return Result(view, status=201, etag=compute_etag(view),
                  location=f"{_base(ctx)}/services/{service.id}/roles/{row.code}")


def patch_role(db: Session, ctx: ActorContext, service_id: str, code: str, payload, if_match: Optional[str]) -> Result:
    ctx.require("roles.manage")
    registry.lock_institution(db, ctx.institution_id)
    service = _service(db, ctx, service_id)
    role = _role(db, service, code)
    before = role_view(role)
    require_if_match(if_match, compute_etag(before))
    if service.protected or role.system:
        raise protected("Системную роль нельзя изменить", status=403)
    changes = catalog.check_role_patch(payload, service.service_type, service.supported_profiles or [])
    if "allowed_profiles" in changes:
        narrowed = set(before["allowed_profiles"]) - set(changes["allowed_profiles"])
        if any(row.profile in narrowed for row in _role_holders(db, service.id, code)):
            raise DomainError(409, "ROLE_IN_USE", "Роль назначена в профиле, который вы хотите исключить")
    for key, value in changes.items():
        setattr(role, key, value)
    after = role_view(role)
    ctx.audit(db, "role.update", "role", f"{service.id}:{code}",
              {"before": {k: before[k] for k in changes}, "after": changes})
    db.commit()
    return Result(after, etag=compute_etag(after))


def delete_role(db: Session, ctx: ActorContext, service_id: str, code: str, if_match: Optional[str]) -> Result:
    ctx.require("roles.manage")
    registry.lock_institution(db, ctx.institution_id)
    service = _service(db, ctx, service_id)
    role = _role(db, service, code)
    require_if_match(if_match, compute_etag(role_view(role)))
    if service.protected or role.system:
        raise protected("Системную роль нельзя удалить", status=403)
    if _role_holders(db, service.id, code):
        raise DomainError(409, "ROLE_IN_USE", "Сначала снимите роль со всех участников")
    db.delete(role)
    ctx.audit(db, "role.delete", "role", f"{service.id}:{code}")
    db.commit()
    return Result(status=204)


def _assignment_target(db: Session, ctx: ActorContext, service_id: str, user_id: str, profile: str):
    service = _service(db, ctx, service_id)
    if profile not in catalog.PROFILES:
        raise not_found("Профиль не найден")
    member = _member(db, ctx, user_id)
    if profile not in (member.profiles or []):
        raise not_found("У участника нет этого профиля")
    if profile not in (service.supported_profiles or []):
        raise not_found("Сервис не поддерживает этот профиль")
    return service


def get_assignments(db: Session, ctx: ActorContext, service_id: str, user_id: str, profile: str) -> Result:
    ctx.require("roles.manage")
    service = _assignment_target(db, ctx, service_id, user_id, profile)
    view = assignment_view(db, service, user_id, profile)
    return Result(view, etag=compute_etag(view))


def replace_assignments(db: Session, ctx: ActorContext, service_id: str, user_id: str, profile: str, payload,
                        if_match: Optional[str]) -> Result:
    ctx.require("roles.manage")
    body = _body(payload, {"roles"}, {"roles"})
    codes = catalog.check_role_codes(body["roles"])
    registry.lock_institution(db, ctx.institution_id)
    service = _assignment_target(db, ctx, service_id, user_id, profile)
    before = assignment_view(db, service, user_id, profile)
    require_if_match(if_match, compute_etag(before))
    if service.id == ctx.admin_service_id and not ctx.is_owner:
        # roles.manage сам по себе не даёт назначать роли administration.
        raise DomainError(403, "FORBIDDEN", "Роли администрирования назначает только владелец вуза")
    for i, code in enumerate(codes):
        role = db.get(ServiceRole, (service.id, code))
        if not role:
            raise DomainError(422, "INVALID_ROLE_ASSIGNMENT", f"Роли '{code}' нет в этом сервисе",
                              [{"path": f"roles[{i}]", "message": "unknown role"}])
        if profile not in (role.allowed_profiles or []):
            raise DomainError(422, "INVALID_ROLE_ASSIGNMENT", f"Роль '{code}' недоступна профилю {profile}",
                              [{"path": f"roles[{i}]", "message": "profile not allowed"}])
    if service.id == ctx.admin_service_id and catalog.OWNER_ROLE in before["roles"] and catalog.OWNER_ROLE not in codes:
        registry.ensure_not_last_owner(db, ctx.institution_id, ctx.admin_service_id, user_id)
    row = db.get(RoleAssignment, (service.id, user_id, profile))
    if codes:
        if row:
            row.roles = codes
        else:
            db.add(RoleAssignment(service_id=service.id, user_id=user_id, profile=profile, roles=codes))
    elif row:
        db.delete(row)
    db.flush()
    after = assignment_view(db, service, user_id, profile)
    ctx.audit(db, "assignments.replace", "assignment", f"{service.id}:{user_id}:{profile}",
              {"service_type": service.service_type, "before": before["roles"], "after": codes})
    db.commit()
    return Result(after, etag=compute_etag(after))


# --------------------------------------------------------------------------
# Credentials local
# --------------------------------------------------------------------------

def _local_only(service: ServiceInstance) -> None:
    if service.deployment != "local":
        raise protected("Credentials облачного сервиса выдаёт только платформа", status=403)


def list_credentials(db: Session, ctx: ActorContext, service_id: str, limit, cursor) -> Result:
    ctx.require("credentials.manage")
    service = _service(db, ctx, service_id)
    _local_only(service)
    query = db.query(ServiceCredential).filter(ServiceCredential.service_id == service.id)
    return _page(ctx, "credentials", query, ServiceCredential.id, lambda c: c.id, credential_view, limit, cursor,
                 {"service": service.id})


def issue_credential(db: Session, ctx: ActorContext, service_id: str) -> Result:
    ctx.require("credentials.manage")
    registry.lock_institution(db, ctx.institution_id)
    service = _service(db, ctx, service_id)
    _local_only(service)
    active = db.query(ServiceCredential).filter(ServiceCredential.service_id == service.id,
                                                ServiceCredential.revoked_at.is_(None)).count()
    if active >= 2:
        raise DomainError(409, "CREDENTIAL_LIMIT", "Не более двух активных credentials. Отзовите старый")
    secret = registry.new_local_secret()
    cred = ServiceCredential(id=generate_uuid(), client_id=generate_uuid(), service_id=service.id,
                             hashed_secret=hash_password(secret), created_at=utc_now())
    db.add(cred)
    db.flush()
    ctx.audit(db, "credential.issue", "credential", cred.id, {"service_id": service.id, "client_id": cred.client_id})
    db.commit()
    return Result({"credential": credential_view(cred), "client_secret": secret}, status=201,
                  location=f"{_base(ctx)}/services/{service.id}/credentials/{cred.id}")


def revoke_credential(db: Session, ctx: ActorContext, service_id: str, credential_id: str) -> Result:
    ctx.require("credentials.manage")
    registry.lock_institution(db, ctx.institution_id)
    service = _service(db, ctx, service_id)
    _local_only(service)
    _uuid(credential_id, "Credential")
    cred = db.get(ServiceCredential, credential_id)
    if not cred or cred.service_id != service.id:
        raise not_found("Credential не найден")
    if cred.revoked_at is None:
        cred.revoked_at = utc_now()
        ctx.audit(db, "credential.revoke", "credential", cred.id, {"service_id": service.id})
    db.commit()
    return Result(status=204)


# --------------------------------------------------------------------------
# Журнал
# --------------------------------------------------------------------------

def list_audit(db: Session, ctx: ActorContext, limit, cursor) -> Result:
    """Расширение контракта: журнал административных изменений вуза, новые сверху."""
    ctx.require("institution.read")
    limit = check_limit(limit)
    scope = {"kind": "audit", "institution": ctx.institution_id, "actor": ctx.actor_id}
    before_id = decode_cursor(settings.CURSOR_SECRET_KEY, scope, cursor)
    query = db.query(AuditEvent).filter(AuditEvent.scope == "institution",
                                        AuditEvent.institution_id == ctx.institution_id)
    if before_id is not None:
        query = query.filter(AuditEvent.id < int(before_id))
    rows = query.order_by(AuditEvent.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return Result({"items": [registry.audit_view(r) for r in rows],
                   "next_cursor": encode_cursor(settings.CURSOR_SECRET_KEY, scope, rows[-1].id) if has_more else None})
