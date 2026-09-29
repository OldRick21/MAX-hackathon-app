"""Поддержка платформы: заявки вузов, статус вуза, allowlist хостов, первый владелец.

Права поддержки хранятся в platform_staff и не зависят от членства в вузах.
Администратор вуза этих операций не видит; поддержка, в свою очередь, не
получает прав внутри вуза (не читает участников и не назначает роли), кроме
контролируемого назначения первого владельца вузу без владельцев.
"""
from dataclasses import dataclass
from urllib.parse import urlsplit
from typing import Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from database.tables import (
    ApplicationStatus,
    AuditEvent,
    IdempotencyRecord,
    Institution,
    InstitutionApplication,
    InstitutionLocalHost,
    InstitutionStatus,
    Membership,
    PlatformStaff,
    PurgedInstance,
    ServiceInstance,
    User,
    utc_now,
)
from auth.models import RefreshUse, ServiceSession
from platform_core import catalog, registry
from platform_core.concurrency import (
    check_limit,
    compute_etag,
    decode_cursor,
    encode_cursor,
    is_uuid,
    require_if_match,
)
from platform_core.errors import DomainError, not_found, validation
from services.institution_admin import Result, _body
from settings.config import settings


@dataclass
class StaffContext:
    user_id: str
    role: str
    request_id: Optional[str]

    def audit(self, db: Session, action: str, target_type: str, target_id: str, details: Optional[dict] = None,
              institution_id: Optional[str] = None, outcome: str = "success", error_code: Optional[str] = None):
        return registry.audit(db, scope="platform", action=action, actor_user_id=self.user_id,
                              actor_kind="platform_support", institution_id=institution_id,
                              target_type=target_type, target_id=target_id, details=details,
                              outcome=outcome, error_code=error_code, request_id=self.request_id)


# --------------------------------------------------------------------------
# Представления
# --------------------------------------------------------------------------

def application_view(app: InstitutionApplication, include_contact: bool = True) -> dict:
    view = {
        "id": app.id,
        "applicant_user_id": app.applicant_user_id,
        "titles": app.titles,
        "default_locale": app.default_locale,
        "comment": app.comment or "",
        "status": app.status,
        "decision_reason": app.decision_reason,
        "institution_id": app.institution_id,
        "created_at": registry.iso(app.created_at),
        "reviewed_at": registry.iso(app.reviewed_at),
    }
    if include_contact:
        view["contact"] = app.contact
    return view


def institution_core_view(db: Session, inst: Institution) -> dict:
    hosts = sorted(registry.approved_hosts(db, inst.id))
    return {"id": inst.id, "titles": inst.titles, "default_locale": inst.default_locale, "status": inst.status,
            "local_hosts": hosts, "created_at": registry.iso(inst.created_at)}


def institution_view(db: Session, inst: Institution) -> dict:
    view = institution_core_view(db, inst)
    admin = registry.admin_service_of(db, inst.id)
    view["owners_count"] = len(registry.owner_ids(db, inst.id, admin.id)) if admin else 0
    view["members_count"] = db.query(Membership).filter(Membership.institution_id == inst.id).count()
    return view


def _etag(db: Session, inst: Institution) -> str:
    return compute_etag(institution_core_view(db, inst))


# --------------------------------------------------------------------------
# Пользователь: заявки на подключение вуза
# --------------------------------------------------------------------------

def platform_me(db: Session, user: User) -> Result:
    staff = db.get(PlatformStaff, user.id)
    return Result({"user_id": user.id, "roles": [staff.role] if staff else []})


def _text(value, path: str, min_len: int, max_len: int) -> str:
    if not isinstance(value, str) or not min_len <= len(value.strip()) <= max_len:
        raise validation(f"Строка от {min_len} до {max_len} символов", path)
    return value.strip()


def submit_application(db: Session, user: User, payload, request_id: Optional[str]) -> Result:
    body = _body(payload, {"titles", "default_locale", "contact", "comment"}, {"titles", "contact"})
    titles = catalog.check_localized(body["titles"], "titles")
    locale = catalog.check_locale(body.get("default_locale", "ru"))
    contact = _text(body["contact"], "contact", 3, 200)
    comment = _text(body.get("comment", ""), "comment", 0, 1000)
    pending = db.query(InstitutionApplication).filter(
        InstitutionApplication.applicant_user_id == user.id,
        InstitutionApplication.status == ApplicationStatus.PENDING.value).count()
    if pending >= settings.MAX_PENDING_APPLICATIONS_PER_USER:
        raise DomainError(409, "APPLICATION_LIMIT", "Слишком много заявок на рассмотрении. Дождитесь решения")
    app = InstitutionApplication(applicant_user_id=user.id, titles=titles, default_locale=locale,
                                 contact=contact, comment=comment)
    db.add(app)
    db.flush()
    registry.audit(db, scope="platform", action="application.submit", actor_user_id=user.id, actor_kind="user",
                   target_type="application", target_id=app.id, details={"titles": titles}, request_id=request_id)
    db.commit()
    return Result(application_view(app), status=201, location=f"/api/v1/institution-applications/{app.id}")


def my_applications(db: Session, user: User) -> Result:
    rows = db.query(InstitutionApplication).filter(InstitutionApplication.applicant_user_id == user.id) \
        .order_by(InstitutionApplication.created_at.desc()).limit(50).all()
    return Result({"items": [application_view(a) for a in rows], "next_cursor": None})


def withdraw_application(db: Session, user: User, application_id: str, request_id: Optional[str]) -> Result:
    app = _lock_application(db, application_id)
    if app.applicant_user_id != user.id:
        raise not_found("Заявка не найдена")
    _ensure_pending(app)
    app.status = ApplicationStatus.WITHDRAWN.value
    app.updated_at = utc_now()
    registry.audit(db, scope="platform", action="application.withdraw", actor_user_id=user.id, actor_kind="user",
                   target_type="application", target_id=app.id, request_id=request_id)
    db.commit()
    return Result(application_view(app))


# --------------------------------------------------------------------------
# Поддержка: рассмотрение заявок
# --------------------------------------------------------------------------

def _lock_application(db: Session, application_id: str) -> InstitutionApplication:
    if not is_uuid(application_id):
        raise not_found("Заявка не найдена")
    db.execute(update(InstitutionApplication).where(InstitutionApplication.id == application_id)
               .values(status=InstitutionApplication.status))
    app = db.get(InstitutionApplication, application_id)
    if not app:
        raise not_found("Заявка не найдена")
    return app


def _ensure_pending(app: InstitutionApplication) -> None:
    if app.status != ApplicationStatus.PENDING.value:
        raise DomainError(409, "APPLICATION_NOT_PENDING", "Заявка уже рассмотрена или отозвана")


def list_applications(db: Session, staff: StaffContext, status: Optional[str], limit, cursor) -> Result:
    limit = check_limit(limit)
    if status is not None and status not in {s.value for s in ApplicationStatus}:
        raise validation("status: pending, approved, rejected или withdrawn", "status")
    scope = {"kind": "applications", "actor": staff.user_id, "status": status}
    after = decode_cursor(settings.CURSOR_SECRET_KEY, scope, cursor)
    query = db.query(InstitutionApplication)
    if status:
        query = query.filter(InstitutionApplication.status == status)
    if after is not None:
        query = query.filter(InstitutionApplication.id > after)
    rows = query.order_by(InstitutionApplication.id.asc()).limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return Result({"items": [application_view(a) for a in rows],
                   "next_cursor": encode_cursor(settings.CURSOR_SECRET_KEY, scope, rows[-1].id) if more else None})


def get_application(db: Session, staff: StaffContext, application_id: str) -> Result:
    if not is_uuid(application_id):
        raise not_found("Заявка не найдена")
    app = db.get(InstitutionApplication, application_id)
    if not app:
        raise not_found("Заявка не найдена")
    return Result(application_view(app))


def approve_application(db: Session, staff: StaffContext, application_id: str, payload) -> Result:
    body = _body(payload if payload is not None else {}, {"titles", "default_locale"})
    app = _lock_application(db, application_id)
    _ensure_pending(app)
    if app.applicant_user_id == staff.user_id:
        raise DomainError(403, "FORBIDDEN", "Собственную заявку должен рассмотреть другой сотрудник поддержки")
    if not db.get(User, app.applicant_user_id):
        raise DomainError(409, "APPLICATION_NOT_PENDING", "Аккаунт заявителя больше не существует")
    titles = catalog.check_localized(body["titles"], "titles") if "titles" in body else app.titles
    locale = catalog.check_locale(body["default_locale"]) if "default_locale" in body else app.default_locale

    # Одна транзакция: вуз, защищённый administration, системные роли, binding,
    # членство заявителя с профилем admin и ролью owner, решение по заявке.
    inst = registry.provision_institution(db, titles, locale)
    registry.assign_owner(db, inst.id, app.applicant_user_id)
    app.status = ApplicationStatus.APPROVED.value
    app.institution_id = inst.id
    app.reviewed_by = staff.user_id
    app.reviewed_at = utc_now()
    app.updated_at = app.reviewed_at
    staff.audit(db, "application.approve", "application", app.id, {"institution_id": inst.id}, inst.id)
    registry.audit(db, scope="institution", action="institution.provision", actor_user_id=staff.user_id,
                   actor_kind="platform_support", institution_id=inst.id, target_type="institution",
                   target_id=inst.id, details={"application_id": app.id, "initial_owner": app.applicant_user_id},
                   request_id=staff.request_id)
    db.commit()
    return Result(application_view(app))


def reject_application(db: Session, staff: StaffContext, application_id: str, payload) -> Result:
    body = _body(payload, {"reason"}, {"reason"})
    reason = _text(body["reason"], "reason", 3, 1000)
    app = _lock_application(db, application_id)
    _ensure_pending(app)
    app.status = ApplicationStatus.REJECTED.value
    app.decision_reason = reason
    app.reviewed_by = staff.user_id
    app.reviewed_at = utc_now()
    app.updated_at = app.reviewed_at
    staff.audit(db, "application.reject", "application", app.id, {"reason": reason})
    db.commit()
    return Result(application_view(app))


# --------------------------------------------------------------------------
# Поддержка: вузы
# --------------------------------------------------------------------------

def _institution(db: Session, institution_id: str) -> Institution:
    if not is_uuid(institution_id):
        raise not_found("Вуз не найден")
    inst = db.get(Institution, institution_id)
    if not inst:
        raise not_found("Вуз не найден")
    return inst


def list_institutions(db: Session, staff: StaffContext, limit, cursor) -> Result:
    limit = check_limit(limit)
    scope = {"kind": "platform-institutions", "actor": staff.user_id}
    after = decode_cursor(settings.CURSOR_SECRET_KEY, scope, cursor)
    query = db.query(Institution)
    if after is not None:
        query = query.filter(Institution.id > after)
    rows = query.order_by(Institution.id.asc()).limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return Result({"items": [institution_view(db, i) for i in rows],
                   "next_cursor": encode_cursor(settings.CURSOR_SECRET_KEY, scope, rows[-1].id) if more else None})


def get_institution(db: Session, staff: StaffContext, institution_id: str) -> Result:
    inst = _institution(db, institution_id)
    return Result(institution_view(db, inst), etag=_etag(db, inst))


def delete_institution(db: Session, staff: StaffContext, institution_id: str, payload) -> Result:
    """Полное удаление вуза оператором: подтверждение — точное русское название вуза.

    Удаляются членства, группы, заявки, экземпляры сервисов с ролями, ключами и сессиями.
    Удаляются также история refresh сессий сервисов вуза и заявка на его подключение.
    Облачные экземпляры попадают в список очистки: раннеры останавливают процессы вуза и стирают
    их данные. Локальные сервисы работают на серверах вуза — их ключи отзываются удалением.
    Журнал платформы сохраняет запись об удалении.
    """
    body = _body(payload, {"confirm_title"}, {"confirm_title"})
    inst = registry.lock_institution(db, _institution(db, institution_id).id)
    title = (inst.titles or {}).get("ru", "")
    if not isinstance(body["confirm_title"], str) or body["confirm_title"].strip() != title:
        raise validation("Для подтверждения введите точное название вуза", "confirm_title")
    services = db.query(ServiceInstance).filter(ServiceInstance.institution_id == inst.id).all()
    for service in services:
        if service.deployment == "cloud" and not db.get(PurgedInstance, service.id):
            db.add(PurgedInstance(service_id=service.id, service_type=service.service_type, institution_id=inst.id))
    registry.revoke_service_sessions(db, institution_id=inst.id)
    # История refresh сессий сервисов вуза (хеши токенов) — без внешнего ключа, удаляется явно.
    sessions = db.query(ServiceSession.id).filter(ServiceSession.institution_id == inst.id)
    db.query(RefreshUse).filter(RefreshUse.session_id.in_(sessions.scalar_subquery())).delete(synchronize_session=False)
    # Заявка на подключение этого вуза (название, контакт, заявитель).
    db.query(InstitutionApplication).filter(InstitutionApplication.institution_id == inst.id).delete(synchronize_session=False)
    db.query(IdempotencyRecord).filter(IdempotencyRecord.institution_id == inst.id).delete(synchronize_session=False)
    members = db.query(Membership).filter(Membership.institution_id == inst.id).count()
    staff.audit(db, "institution.delete", "institution", inst.id,
                {"titles": inst.titles, "members": members,
                 "services": [{"id": s.id, "service_type": s.service_type, "deployment": s.deployment} for s in services]},
                inst.id)
    db.flush()
    # Каскад в БД: членства, группы, заявки, экземпляры, роли, ключи, привязки, сессии.
    db.query(Institution).filter(Institution.id == inst.id).delete(synchronize_session=False)
    db.expunge_all()
    db.info['export_services'] = True  # файлы настроек сервисов пересобираются после commit
    return Result(status=204)


def set_status(db: Session, staff: StaffContext, institution_id: str, payload, if_match: Optional[str]) -> Result:
    body = _body(payload, {"status"}, {"status"})
    new_status = body["status"]
    if new_status not in (InstitutionStatus.ACTIVE.value, InstitutionStatus.SUSPENDED.value):
        raise validation("Статус можно сменить на active или suspended", "status")
    inst = registry.lock_institution(db, _institution(db, institution_id).id)
    require_if_match(if_match, _etag(db, inst))
    old = inst.status
    if old != new_status:
        inst.status = new_status
        if new_status == InstitutionStatus.SUSPENDED.value:
            # Приостановка сразу завершает все сервисные сессии вуза.
            registry.revoke_service_sessions(db, institution_id=inst.id)
        elif not registry.admin_service_of(db, inst.id):
            registry.create_admin_instance(db, inst.id)
        staff.audit(db, "institution.status", "institution", inst.id, {"before": old, "after": new_status}, inst.id)
        registry.audit(db, scope="institution", action="institution.status", actor_user_id=staff.user_id,
                       actor_kind="platform_support", institution_id=inst.id, target_type="institution",
                       target_id=inst.id, details={"before": old, "after": new_status}, request_id=staff.request_id)
    db.commit()
    return Result(institution_view(db, inst), etag=_etag(db, inst))


def replace_local_hosts(db: Session, staff: StaffContext, institution_id: str, payload, if_match: Optional[str]) -> Result:
    body = _body(payload, {"hostnames"}, {"hostnames"})
    values = body["hostnames"]
    if not isinstance(values, list) or len(values) > 20:
        raise validation("hostnames — список до 20 имён", "hostnames")
    hosts = []
    for i, value in enumerate(values):
        try:
            host = catalog.normalize_hostname(value if isinstance(value, str) else "")
        except DomainError as error:
            raise validation(error.message, f"hostnames[{i}]")
        if host not in hosts:
            hosts.append(host)
    inst = registry.lock_institution(db, _institution(db, institution_id).id)
    require_if_match(if_match, _etag(db, inst))
    before = sorted(registry.approved_hosts(db, inst.id))
    db.query(InstitutionLocalHost).filter(InstitutionLocalHost.institution_id == inst.id).delete(synchronize_session=False)
    for host in hosts:
        db.add(InstitutionLocalHost(institution_id=inst.id, hostname=host, approved_by=staff.user_id))
    db.flush()
    # Экземпляры на хостах, у которых отозвано одобрение, выключаются. Администрирование — нет:
    # его адреса задаёт оператор, и выключить его нельзя.
    disabled = []
    allowed = set(hosts)
    for service in db.query(ServiceInstance).filter(ServiceInstance.institution_id == inst.id,
                                                    ServiceInstance.protected.is_(False)).all():
        used = {(urlsplit(u).hostname or "").lower() for u in (service.api_base_url, service.client_base_url)}
        if not used <= allowed and service.enabled:
            service.enabled = False
            registry.revoke_service_sessions(db, service_id=service.id)
            disabled.append(service.id)
    details = {"before": before, "after": sorted(hosts), "disabled_services": disabled}
    staff.audit(db, "institution.local_hosts", "institution", inst.id, details, inst.id)
    registry.audit(db, scope="institution", action="institution.local_hosts", actor_user_id=staff.user_id,
                   actor_kind="platform_support", institution_id=inst.id, target_type="institution",
                   target_id=inst.id, details=details, request_id=staff.request_id)
    db.commit()
    return Result(institution_view(db, inst), etag=_etag(db, inst))


def assign_initial_owner(db: Session, staff: StaffContext, institution_id: str, payload) -> Result:
    """Восстановление: назначить владельца вузу, у которого владельцев нет.

    Для вуза с владельцем операция запрещена — управление ролями внутри вуза
    остаётся за его владельцами, поддержка не может «подсадить» себе доступ.
    """
    body = _body(payload, {"user_id"}, {"user_id"})
    user_id = body["user_id"]
    if not is_uuid(user_id):
        raise validation("user_id должен быть UUID", "user_id")
    inst = registry.lock_institution(db, _institution(db, institution_id).id)
    if user_id == staff.user_id:
        raise DomainError(403, "FORBIDDEN", "Нельзя назначить владельцем себя")
    if not db.get(User, user_id):
        raise DomainError(404, "RESOURCE_NOT_FOUND", "Пользователь не найден: он должен войти через MAX")
    admin = registry.admin_service_of(db, inst.id)
    if admin and registry.owner_ids(db, inst.id, admin.id):
        raise DomainError(409, "OWNER_EXISTS", "У вуза уже есть владелец. Роли назначает он")
    registry.assign_owner(db, inst.id, user_id)
    staff.audit(db, "institution.initial_owner", "member", user_id, {"institution_id": inst.id}, inst.id)
    registry.audit(db, scope="institution", action="owner.initial_assign", actor_user_id=staff.user_id,
                   actor_kind="platform_support", institution_id=inst.id, target_type="member",
                   target_id=user_id, request_id=staff.request_id)
    db.commit()
    return Result(institution_view(db, inst), etag=_etag(db, inst))


def list_platform_audit(db: Session, staff: StaffContext, limit, cursor) -> Result:
    limit = check_limit(limit)
    scope = {"kind": "platform-audit", "actor": staff.user_id}
    before_id = decode_cursor(settings.CURSOR_SECRET_KEY, scope, cursor)
    query = db.query(AuditEvent).filter(AuditEvent.scope == "platform")
    if before_id is not None:
        query = query.filter(AuditEvent.id < int(before_id))
    rows = query.order_by(AuditEvent.id.desc()).limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return Result({"items": [registry.audit_view(r) for r in rows],
                   "next_cursor": encode_cursor(settings.CURSOR_SECRET_KEY, scope, rows[-1].id) if more else None})
