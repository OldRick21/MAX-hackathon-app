"""Machine API экземпляра: /api/v1/internal/service/{service_id}/... (CORE_API_SPEC.md §5, §7).

Вызывающий — backend своего экземпляра с machine token; scope проверен зависимостью маршрута.
Чужой service_id — 404 (существование чужих экземпляров не раскрывается). Изменения ролей,
назначений и manifest идут под блокировкой вуза, требуют If-Match с strong ETag последнего
чтения (нет — 428, устарел — 412) и пишутся в журнал с request_id и machine credential.
"""
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from database.tables import Membership, RoleAssignment, ServiceInstance, ServiceRole, StudyGroup, StudyGroupMember
from platform_core import catalog, registry
from platform_core.concurrency import check_limit, compute_etag, decode_cursor, encode_cursor, is_uuid, require_if_match
from platform_core.errors import DomainError, not_found, protected, validation
from services.institution_admin import Result, _body, assignment_view, role_view
from settings.config import settings


@dataclass
class MachineContext:
    service_id: str
    institution_id: str
    credential_id: Optional[str]
    request_id: Optional[str]

    @classmethod
    def of(cls, claims, request_id: Optional[str]) -> "MachineContext":
        return cls(claims["service_id"], claims["institution_id"], claims.get("credential_id"), request_id)

    def audit(self, db: Session, action: str, target_type: str, target_id: str, details: Optional[dict] = None,
              outcome: str = "success", error_code: Optional[str] = None):
        return registry.audit(db, scope="institution", action=action, actor_user_id=None, actor_kind="service",
                              institution_id=self.institution_id, target_type=target_type, target_id=target_id,
                              details=details, outcome=outcome, error_code=error_code, request_id=self.request_id,
                              machine_credential_id=self.credential_id)


def _base(ctx: MachineContext) -> str:
    return f"/api/v1/internal/service/{ctx.service_id}"


def own_service(db: Session, ctx: MachineContext, service_id: str) -> ServiceInstance:
    """Только свой экземпляр; чужой или несуществующий — одинаковый 404."""
    if not is_uuid(service_id) or service_id != ctx.service_id:
        raise not_found("Экземпляр сервиса не найден")
    service = db.get(ServiceInstance, service_id)
    if not service or service.deleted_at is not None or service.institution_id != ctx.institution_id:
        raise not_found("Экземпляр сервиса не найден")
    return service


def _writable(service: ServiceInstance, what: str) -> None:
    if service.protected:
        raise protected(f"{what} администрирования задаёт платформа", status=403)


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------

def manifest_view(service: ServiceInstance) -> dict:
    return service.manifest or {"titles": {"ru": service.service_type}, "menus": []}


def get_manifest(db: Session, ctx: MachineContext, service_id: str) -> Result:
    view = manifest_view(own_service(db, ctx, service_id))
    return Result(view, etag=compute_etag(view))


def replace_manifest(db: Session, ctx: MachineContext, service_id: str, payload, if_match: Optional[str]) -> Result:
    """Замена имени и меню. Проверка If-Match и запись — под одной блокировкой вуза."""
    service = own_service(db, ctx, service_id)
    registry.lock_institution(db, ctx.institution_id)
    db.refresh(service)
    require_if_match(if_match, compute_etag(manifest_view(service)))
    _writable(service, "Меню")
    manifest = catalog.check_manifest(payload, service.service_type, service.supported_profiles or [])
    if service.enabled and not manifest["menus"]:
        raise DomainError(409, "MANIFEST_REQUIRED", "У включённого сервиса должен остаться хотя бы один пункт меню")
    service.manifest = manifest
    ctx.audit(db, "service.manifest.publish", "service", service.id, {"menus": [m["id"] for m in manifest["menus"]]})
    db.commit()
    view = manifest_view(service)
    return Result(view, etag=compute_etag(view))


# --------------------------------------------------------------------------
# Участники и группы (чтение)
# --------------------------------------------------------------------------

def get_user_profiles(db: Session, ctx: MachineContext, service_id: str, user_id: str) -> Result:
    from services.join_requests import display_name
    service = own_service(db, ctx, service_id)
    member = db.get(Membership, (service.institution_id, user_id)) if is_uuid(user_id) else None
    if not member:
        raise not_found("Участник не найден в этом вузе")
    return Result({
        "user_id": user_id,
        "institution_id": service.institution_id,
        "profiles": list(member.profiles or []),
        "groups": registry.user_group_ids(db, service.institution_id, user_id),
        # Имя из регистрации: сервис «Люди» показывает его, пока анкета не заполнена.
        "display_name": display_name(db, service.institution_id, user_id),
        # Анкета, записанная при прошлом членстве, у «Людей» считается удалённой.
        "member_since": member.created_at.isoformat(),
    })


def list_members(db: Session, ctx: MachineContext, service_id: str) -> Result:
    from services.join_requests import member_names
    service = own_service(db, ctx, service_id)
    names = member_names(db, service.institution_id)
    rows = db.query(Membership).filter(Membership.institution_id == service.institution_id) \
        .order_by(Membership.user_id).all()
    return Result({"items": [{"user_id": m.user_id, "profiles": list(m.profiles or []),
                              "display_name": names.get(m.user_id), "member_since": m.created_at.isoformat()}
                             for m in rows], "next_cursor": None})


def list_groups(db: Session, ctx: MachineContext, service_id: str) -> Result:
    service = own_service(db, ctx, service_id)
    groups = db.query(StudyGroup).filter(StudyGroup.institution_id == service.institution_id) \
        .order_by(StudyGroup.name_key).all()
    return Result({"items": [{"id": g.id, "name": g.name} for g in groups], "next_cursor": None})


def get_group_members(db: Session, ctx: MachineContext, service_id: str, group_id: str) -> Result:
    service = own_service(db, ctx, service_id)
    group = db.get(StudyGroup, group_id) if is_uuid(group_id) else None
    if not group or group.institution_id != service.institution_id:
        raise not_found("Группа не найдена")
    members = db.query(StudyGroupMember).filter(StudyGroupMember.group_id == group.id).all()
    return Result({"group_id": group.id, "name": group.name, "user_ids": sorted(m.user_id for m in members)})


# --------------------------------------------------------------------------
# Роли
# --------------------------------------------------------------------------

def list_roles(db: Session, ctx: MachineContext, service_id: str, limit, cursor) -> Result:
    service = own_service(db, ctx, service_id)
    limit = check_limit(limit)
    scope = {"kind": "machine_roles", "service": service.id}
    after = decode_cursor(settings.CURSOR_SECRET_KEY, scope, cursor)
    query = db.query(ServiceRole).filter(ServiceRole.service_id == service.id)
    if after is not None:
        query = query.filter(ServiceRole.code > after)
    rows = query.order_by(ServiceRole.code.asc()).limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return Result({"items": [role_view(r) for r in rows],
                   "next_cursor": encode_cursor(settings.CURSOR_SECRET_KEY, scope, rows[-1].code) if more and rows else None})


def _role(db: Session, service: ServiceInstance, code: str) -> ServiceRole:
    role = db.get(ServiceRole, (service.id, code)) if isinstance(code, str) and catalog.CODE_RE.match(code) else None
    if not role:
        raise not_found("Роль не найдена")
    return role


def _holders(db: Session, service_id: str, code: str):
    return [row for row in db.query(RoleAssignment).filter(RoleAssignment.service_id == service_id).all()
            if code in (row.roles or [])]


def get_role(db: Session, ctx: MachineContext, service_id: str, code: str) -> Result:
    view = role_view(_role(db, own_service(db, ctx, service_id), code))
    return Result(view, etag=compute_etag(view))


def create_role(db: Session, ctx: MachineContext, service_id: str, payload) -> Result:
    service = own_service(db, ctx, service_id)
    registry.lock_institution(db, ctx.institution_id)
    _writable(service, "Роли")
    role = catalog.check_role_input(payload, service.service_type, service.supported_profiles or [])
    if db.get(ServiceRole, (service.id, role["code"])):
        raise DomainError(409, "ROLE_ALREADY_EXISTS", "Роль с таким кодом уже есть в этом сервисе")
    row = ServiceRole(service_id=service.id, system=False, **role)
    db.add(row)
    db.flush()
    ctx.audit(db, "role.create", "role", f"{service.id}:{row.code}", role)
    db.commit()
    view = role_view(row)
    return Result(view, status=201, etag=compute_etag(view), location=f"{_base(ctx)}/roles/{row.code}")


def update_role(db: Session, ctx: MachineContext, service_id: str, code: str, payload, if_match: Optional[str]) -> Result:
    service = own_service(db, ctx, service_id)
    registry.lock_institution(db, ctx.institution_id)
    role = _role(db, service, code)
    before = role_view(role)
    require_if_match(if_match, compute_etag(before))
    if service.protected or role.system:
        raise protected("Системную роль нельзя изменить", status=403)
    changes = catalog.check_role_patch(payload, service.service_type, service.supported_profiles or [])
    if "allowed_profiles" in changes:
        narrowed = set(before["allowed_profiles"]) - set(changes["allowed_profiles"])
        if any(row.profile in narrowed for row in _holders(db, service.id, code)):
            raise DomainError(409, "ROLE_IN_USE", "Роль назначена в профиле, который исключается. Сначала снимите назначения")
    for key, value in changes.items():
        setattr(role, key, value)
    after = role_view(role)
    ctx.audit(db, "role.update", "role", f"{service.id}:{code}",
              {"before": {k: before[k] for k in changes}, "after": changes})
    db.commit()
    return Result(after, etag=compute_etag(after))


def delete_role(db: Session, ctx: MachineContext, service_id: str, code: str, if_match: Optional[str]) -> Result:
    service = own_service(db, ctx, service_id)
    registry.lock_institution(db, ctx.institution_id)
    role = _role(db, service, code)
    require_if_match(if_match, compute_etag(role_view(role)))
    if service.protected or role.system:
        raise protected("Системную роль нельзя удалить", status=403)
    if _holders(db, service.id, code):
        raise DomainError(409, "ROLE_IN_USE", "Роль назначена участникам. Сначала снимите назначения")
    db.delete(role)
    ctx.audit(db, "role.delete", "role", f"{service.id}:{code}")
    db.commit()
    return Result(status=204)


# --------------------------------------------------------------------------
# Назначения
# --------------------------------------------------------------------------

def _assignment_target(db: Session, ctx: MachineContext, service_id: str, user_id: str, profile: str) -> ServiceInstance:
    service = own_service(db, ctx, service_id)
    if profile not in catalog.PROFILES:
        raise validation("profile: admin, teacher или student", "profile")
    member = db.get(Membership, (service.institution_id, user_id)) if is_uuid(user_id) else None
    if not member or profile not in (member.profiles or []):
        raise not_found("У участника нет этого профиля в вузе")
    if profile not in (service.supported_profiles or []):
        raise not_found("Сервис не поддерживает этот профиль")
    return service


def get_assignments(db: Session, ctx: MachineContext, service_id: str, user_id: str, profile: str) -> Result:
    service = _assignment_target(db, ctx, service_id, user_id, profile)
    view = assignment_view(db, service, user_id, profile)
    return Result(view, etag=compute_etag(view))


def replace_assignments(db: Session, ctx: MachineContext, service_id: str, user_id: str, profile: str, payload,
                        if_match: Optional[str]) -> Result:
    service = _assignment_target(db, ctx, service_id, user_id, profile)
    registry.lock_institution(db, ctx.institution_id)
    before = assignment_view(db, service, user_id, profile)
    require_if_match(if_match, compute_etag(before))
    # Назначения administration меняет только owner через private API.
    _writable(service, "Назначения")
    codes = catalog.check_role_codes(_body(payload, {"roles"}, {"roles"})["roles"])
    for i, code in enumerate(codes):
        role = db.get(ServiceRole, (service.id, code))
        if not role or profile not in (role.allowed_profiles or []):
            raise DomainError(422, "INVALID_ROLE_ASSIGNMENT",
                              f"Роль '{code}' не существует в этом сервисе или недоступна профилю {profile}",
                              [{"path": f"roles[{i}]", "message": "unknown role or profile not allowed"}])
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
