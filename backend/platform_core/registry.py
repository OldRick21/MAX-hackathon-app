"""Операции реестра ядра, общие для private API, поддержки платформы и bootstrap.

Все функции работают внутри транзакции вызывающего кода и не делают commit,
кроме явно помеченных. Так проверка инварианта и изменение попадают в одну
транзакцию (CORE_API_SPEC.md §9).
"""
import logging
import secrets
from datetime import timedelta, timezone
from typing import Iterable, List, Optional, Set, Tuple

from sqlalchemy import update
from sqlalchemy.orm import Session

from database.tables import (
    AuditEvent,
    CloudBinding,
    Institution,
    InstitutionLocalHost,
    InstitutionStatus,
    Membership,
    RoleAssignment,
    ServiceCredential,
    ServiceInstance,
    ServiceRole,
    ServiceSession,
    StudyGroup,
    StudyGroupMember,
    generate_uuid,
    utc_now,
)
from platform_core import catalog
from platform_core.errors import DomainError
from settings.config import settings

logger = logging.getLogger("uvicorn")


# --------------------------------------------------------------------------
# Блокировки и сериализация
# --------------------------------------------------------------------------

def lock_institution(db: Session, institution_id: str) -> Institution:
    """Берёт блокировку записи вуза до конца транзакции.

    Пустой UPDATE — переносимый способ: в PostgreSQL это row lock, в SQLite —
    блокировка записи всей БД. Параллельные изменения owners одного вуза
    выполняются строго последовательно.
    """
    db.execute(update(Institution).where(Institution.id == institution_id).values(status=Institution.status))
    inst = db.get(Institution, institution_id)
    if not inst:
        raise DomainError(404, "RESOURCE_NOT_FOUND", "Вуз не найден")
    return inst


# --------------------------------------------------------------------------
# RBAC
# --------------------------------------------------------------------------

def admin_service_of(db: Session, institution_id: str) -> Optional[ServiceInstance]:
    return db.query(ServiceInstance).filter(
        ServiceInstance.institution_id == institution_id,
        ServiceInstance.service_type == "administration",
    ).first()


def assigned_roles(db: Session, service_id: str, user_id: str, profile: str) -> List[str]:
    row = db.get(RoleAssignment, (service_id, user_id, profile))
    return list(row.roles or []) if row else []


def permissions_for(db: Session, service_id: str, roles: Iterable[str]) -> List[str]:
    codes = list(roles)
    if not codes:
        return []
    perms: Set[str] = set()
    for role in db.query(ServiceRole).filter(ServiceRole.service_id == service_id, ServiceRole.code.in_(codes)).all():
        perms.update(role.permissions or [])
    return sorted(perms)


def has_profile(membership: Optional[Membership], profile: str) -> bool:
    return bool(membership) and profile in (membership.profiles or [])


def owner_ids(db: Session, institution_id: str, admin_service_id: str) -> Set[str]:
    """Действующие owners: роль owner в administration при живом профиле admin."""
    result = set()
    rows = db.query(RoleAssignment).filter(RoleAssignment.service_id == admin_service_id,
                                           RoleAssignment.profile == "admin").all()
    for row in rows:
        if catalog.OWNER_ROLE not in (row.roles or []):
            continue
        member = db.get(Membership, (institution_id, row.user_id))
        if has_profile(member, "admin"):
            result.add(row.user_id)
    return result


def ensure_not_last_owner(db: Session, institution_id: str, admin_service_id: str, user_id: str) -> None:
    owners = owner_ids(db, institution_id, admin_service_id)
    if user_id in owners and len(owners) == 1:
        raise DomainError(409, "LAST_OWNER",
                          "Нельзя лишить вуз последнего владельца. Сначала назначьте другого владельца")


# --------------------------------------------------------------------------
# Отзыв сессий
# --------------------------------------------------------------------------

def revoke_service_sessions(db: Session, *, institution_id: Optional[str] = None, service_id: Optional[str] = None,
                            user_id: Optional[str] = None, profiles: Optional[Iterable[str]] = None) -> int:
    query = update(ServiceSession).where(ServiceSession.is_revoked == False)  # noqa: E712
    if institution_id:
        query = query.where(ServiceSession.institution_id == institution_id)
    if service_id:
        query = query.where(ServiceSession.service_id == service_id)
    if user_id:
        query = query.where(ServiceSession.user_id == user_id)
    if profiles is not None:
        profiles = list(profiles)
        if not profiles:
            return 0
        query = query.where(ServiceSession.profile.in_(profiles))
    result = db.execute(query.values(is_revoked=True).execution_options(synchronize_session=False))
    return result.rowcount or 0


def drop_assignments(db: Session, institution_id: str, user_id: str, profiles: Optional[Iterable[str]] = None) -> None:
    service_ids = [row[0] for row in db.query(ServiceInstance.id).filter(ServiceInstance.institution_id == institution_id)]
    if not service_ids:
        return
    query = db.query(RoleAssignment).filter(RoleAssignment.service_id.in_(service_ids), RoleAssignment.user_id == user_id)
    if profiles is not None:
        query = query.filter(RoleAssignment.profile.in_(list(profiles)))
    query.delete(synchronize_session=False)


# --------------------------------------------------------------------------
# Аудит
# --------------------------------------------------------------------------

def drop_group_membership(db: Session, institution_id: str, user_id: str) -> None:
    """Участник без профиля student не может оставаться в учебной группе."""
    row = db.get(StudyGroupMember, (institution_id, user_id))
    if row:
        group = db.get(StudyGroup, row.group_id)
        if group:
            group.members_revision += 1
        db.delete(row)


def user_group_ids(db: Session, institution_id: str, user_id: str) -> List[str]:
    """Группы пользователя в вузе (в MVP — не больше одной)."""
    return [m.group_id for m in db.query(StudyGroupMember).filter(StudyGroupMember.institution_id == institution_id,
                                                                   StudyGroupMember.user_id == user_id)]


def audit(db: Session, *, scope: str, action: str, actor_user_id: Optional[str], actor_kind: str,
          institution_id: Optional[str] = None, target_type: Optional[str] = None, target_id: Optional[str] = None,
          outcome: str = "success", error_code: Optional[str] = None, details: Optional[dict] = None,
          request_id: Optional[str] = None, facade_request_id: Optional[str] = None,
          machine_credential_id: Optional[str] = None) -> AuditEvent:
    event = AuditEvent(
        scope=scope, action=action, actor_user_id=actor_user_id, actor_kind=actor_kind,
        institution_id=institution_id, target_type=target_type, target_id=target_id, outcome=outcome,
        error_code=error_code, details=details or {}, request_id=request_id,
        facade_request_id=facade_request_id, machine_credential_id=machine_credential_id,
    )
    db.add(event)
    return event


def audit_view(event: AuditEvent) -> dict:
    return {
        "id": event.id,
        "created_at": iso(event.created_at),
        "scope": event.scope,
        "institution_id": event.institution_id,
        "actor_user_id": event.actor_user_id,
        "actor_kind": event.actor_kind,
        "action": event.action,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "outcome": event.outcome,
        "error_code": event.error_code,
        "details": event.details or {},
        "request_id": event.request_id,
        "facade_request_id": event.facade_request_id,
    }


def iso(value) -> Optional[str]:
    """RFC 3339 UTC с Z. SQLite возвращает naive datetime: значения пишутся в UTC."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0).isoformat() + "Z"


# --------------------------------------------------------------------------
# Экземпляры, роли, bindings
# --------------------------------------------------------------------------

def approved_hosts(db: Session, institution_id: str) -> Set[str]:
    return {row.hostname for row in db.query(InstitutionLocalHost).filter(InstitutionLocalHost.institution_id == institution_id)}


def create_initial_roles(db: Session, service: ServiceInstance) -> None:
    t = catalog.service_type(service.service_type)
    for role in t["initial_roles"]:
        if db.get(ServiceRole, (service.id, role["code"])):
            continue
        db.add(ServiceRole(service_id=service.id, code=role["code"], titles=dict(role["titles"]),
                           allowed_profiles=list(role["allowed_profiles"]), permissions=list(role["permissions"]),
                           system=bool(t["system_roles"])))


def create_admin_instance(db: Session, institution_id: str) -> ServiceInstance:
    """Администрирование вуза: создаётся вместе с вузом, как и любой сервис — со своим контейнером.

    Адреса по умолчанию берутся из настроек, оператор меняет их в пульте; ключ процесса
    выдаёт оператор («Выдать ключ» в карточке сервиса).
    """
    t = catalog.service_type("administration")
    service = ServiceInstance(
        id=generate_uuid(), institution_id=institution_id, service_type="administration", deployment="local",
        enabled=True, protected=True, api_base_url=settings.ADMINISTRATION_API_BASE_URL,
        client_base_url=settings.ADMINISTRATION_CLIENT_BASE_URL,
        supported_profiles=list(t["supported_profiles"]), manifest=catalog.default_manifest("administration"),
    )
    db.add(service)
    db.flush()
    create_initial_roles(db, service)
    return service


def create_local_instance(db: Session, institution_id: str, service_type: str, api_url: str, client_url: str,
                          titles: Optional[dict] = None, profiles: Optional[List[str]] = None) -> ServiceInstance:
    """Local создаётся выключенным с пустым меню; своему сервису название и профили задаёт вуз."""
    t = catalog.service_type(service_type)
    service = ServiceInstance(
        id=generate_uuid(), institution_id=institution_id, service_type=service_type, deployment="local",
        enabled=False, protected=False, api_base_url=api_url, client_base_url=client_url,
        supported_profiles=list(profiles or t["supported_profiles"]),
        manifest={"titles": dict(titles or t["titles"]), "menus": []},
    )
    db.add(service)
    db.flush()
    create_initial_roles(db, service)
    return service


def sync_admin_instance(db: Session, service: ServiceInstance) -> None:
    """Приводит manifest и системные роли administration к каталогу; адреса задаёт оператор."""
    t = catalog.service_type("administration")
    service.deployment = "local"
    service.enabled = True
    service.protected = True
    service.supported_profiles = list(t["supported_profiles"])
    service.manifest = catalog.default_manifest("administration")
    for role in t["initial_roles"]:
        existing = db.get(ServiceRole, (service.id, role["code"]))
        if not existing:
            db.add(ServiceRole(service_id=service.id, code=role["code"], titles=dict(role["titles"]),
                               allowed_profiles=list(role["allowed_profiles"]),
                               permissions=list(role["permissions"]), system=True))
        else:
            existing.titles = dict(role["titles"])
            existing.allowed_profiles = list(role["allowed_profiles"])
            existing.permissions = list(role["permissions"])
            existing.system = True
    # Прежний прототип создавал роль admin_owner и выдавал её по умолчанию.
    legacy = db.get(ServiceRole, (service.id, catalog.LEGACY_OWNER_ROLE))
    if legacy:
        for row in db.query(RoleAssignment).filter(RoleAssignment.service_id == service.id).all():
            roles = list(row.roles or [])
            if catalog.LEGACY_OWNER_ROLE in roles:
                roles = [r for r in roles if r != catalog.LEGACY_OWNER_ROLE]
                if catalog.OWNER_ROLE not in roles:
                    roles.append(catalog.OWNER_ROLE)
                row.roles = roles
        db.delete(legacy)


# Прежние встроенные типы: теперь это свои сервисы вуза (custom.<код>) со своими контейнерами.
LEGACY_TYPES = {"schedule": "custom.schedule", "user-profile": "custom.people", "coursework": "custom.coursework"}


def provision_institution(db: Session, titles: dict, default_locale: str, status: str = InstitutionStatus.ACTIVE.value) -> Institution:
    inst = Institution(id=generate_uuid(), titles=titles, default_locale=default_locale, status=status)
    db.add(inst)
    db.flush()
    create_admin_instance(db, inst.id)
    return inst


def assign_owner(db: Session, institution_id: str, user_id: str) -> None:
    """Контролируемое назначение владельца: membership + профиль admin + роль owner."""
    admin = admin_service_of(db, institution_id)
    if not admin:
        admin = create_admin_instance(db, institution_id)
    member = db.get(Membership, (institution_id, user_id))
    if not member:
        db.add(Membership(institution_id=institution_id, user_id=user_id, profiles=["admin"]))
    elif "admin" not in (member.profiles or []):
        member.profiles = [*(member.profiles or []), "admin"]
    row = db.get(RoleAssignment, (admin.id, user_id, "admin"))
    if not row:
        db.add(RoleAssignment(service_id=admin.id, user_id=user_id, profile="admin", roles=[catalog.OWNER_ROLE]))
    elif catalog.OWNER_ROLE not in (row.roles or []):
        row.roles = [*(row.roles or []), catalog.OWNER_ROLE]


def ensure_platform_invariants(db: Session) -> None:
    """Выполняется при запуске ядра и командами оператора. Идемпотентна.

    - у каждого вуза есть защищённый экземпляр administration с manifest и ролями из каталога;
    - прежние встроенные типы (расписание, «Люди», курсовые) становятся своими сервисами вуза:
      тип custom.<код>, deployment local; меню и роли они публикуют сами;
    - ключи прежней облачной выдачи (cloud bindings) отзываются: ключ сервису выдаётся в карточке;
    - профиль admin есть у каждого сервиса: администраторы видят любой сервис вуза.
    """
    for inst in db.query(Institution).all():
        admin = admin_service_of(db, inst.id)
        if not admin:
            create_admin_instance(db, inst.id)
        else:
            sync_admin_instance(db, admin)
    for service in db.query(ServiceInstance).filter(ServiceInstance.service_type.in_(list(LEGACY_TYPES))).all():
        target = LEGACY_TYPES[service.service_type]
        if db.query(ServiceInstance).filter(ServiceInstance.institution_id == service.institution_id,
                                            ServiceInstance.service_type == target).first():
            logger.warning("%s %s: %s already exists, legacy instance left as is", service.service_type, service.id, target)
            continue
        logger.info("%s %s -> %s", service.service_type, service.id, target)
        service.service_type = target
        service.deployment = "local"
    for service in db.query(ServiceInstance).filter(ServiceInstance.deployment == "cloud").all():
        service.deployment = "local"
    now = utc_now()
    for binding in db.query(CloudBinding).filter(CloudBinding.active.is_(True)).all():
        binding.active = False
        credential = db.get(ServiceCredential, binding.credential_id)
        if credential and credential.revoked_at is None:
            credential.revoked_at = now
    for service in db.query(ServiceInstance).all():
        if "admin" not in (service.supported_profiles or []):
            logger.info("%s %s: admin profile added", service.service_type, service.id)
            service.supported_profiles = [*(service.supported_profiles or []), "admin"]
    db.flush()


def new_local_secret() -> str:
    return secrets.token_urlsafe(32)


def issue_credential(db: Session, service: ServiceInstance) -> Tuple[ServiceCredential, str]:
    """Новый ключ процесса сервиса (client_id + секрет). Секрет возвращается один раз, хранится хэш."""
    from auth.security import hash_password
    secret = new_local_secret()
    credential = ServiceCredential(id=generate_uuid(), client_id=generate_uuid(), service_id=service.id,
                                   hashed_secret=hash_password(secret), created_at=utc_now())
    db.add(credential)
    db.flush()
    return credential, secret


IDEMPOTENCY_TTL = timedelta(hours=24)
