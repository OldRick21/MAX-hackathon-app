"""Операции оператора платформы: общие для manage.py и операторской веб-панели.

Функции не коммитят сами — вызывающий код решает, где граница транзакции.
"""
from sqlalchemy.orm import Session

from database.tables import Membership, PlatformRole, PlatformStaff, User
from platform_core import registry
from platform_core.errors import DomainError

def grant_staff(db: Session, user_id: str, granted_by: str) -> bool:
    """Выдаёт права поддержки платформы. False — права уже были."""
    if not db.get(User, user_id):
        raise DomainError(404, "RESOURCE_NOT_FOUND", "Пользователь не найден: он должен войти через MAX")
    if db.get(PlatformStaff, user_id):
        return False
    db.add(PlatformStaff(user_id=user_id, role=PlatformRole.SUPPORT.value, granted_by=granted_by))
    registry.audit(db, scope="platform", action="staff.grant", actor_user_id=None, actor_kind="operator",
                   target_type="user", target_id=user_id, details={"role": PlatformRole.SUPPORT.value})
    return True


def revoke_staff(db: Session, user_id: str) -> bool:
    staff = db.get(PlatformStaff, user_id)
    if not staff:
        return False
    db.delete(staff)
    registry.audit(db, scope="platform", action="staff.revoke", actor_user_id=None, actor_kind="operator",
                   target_type="user", target_id=user_id)
    return True


def delete_user(db: Session, user_id: str) -> None:
    """Удаляет пользователя, не состоящего ни в одном вузе.

    Вместе с ним каскадом уходят сессии (токены сразу перестают действовать), аватар,
    заявки на вступление и заявки на подключение вуза. Следующий вход через MAX создаст
    нового пользователя с новым UUID.
    """
    user = db.get(User, user_id)
    if not user:
        raise DomainError(404, "RESOURCE_NOT_FOUND", "Пользователь не найден")
    if db.query(Membership).filter(Membership.user_id == user_id).first():
        raise DomainError(409, "USER_HAS_MEMBERSHIPS", "Пользователь состоит в вузе. Сначала удалите его из всех вузов")
    if db.get(PlatformStaff, user_id):
        raise DomainError(409, "USER_IS_STAFF", "Сначала отзовите права поддержки платформы")
    db.delete(user)
    registry.audit(db, scope="platform", action="user.delete", actor_user_id=None, actor_kind="operator",
                   target_type="user", target_id=user_id)


# Названия сервисов вуза по умолчанию при автоподключении (scripts/connect-services.sh).
DEFAULT_TITLES = {"schedule": "Расписание", "people": "Люди", "coursework": "Курсовые работы"}


def connect_service(db: Session, institution_id: str, code: str, host: str, port: int) -> dict:
    """Подключает сервис вуза к ядру на https://host:port и выдаёт ему ключ.

    administration — задаёт адреса защищённого экземпляра вуза; любой другой код — одобряет хост,
    регистрирует свой сервис custom.<code> (или меняет адреса существующего, выключая его).
    Прежние ключи сервиса отзываются: действует только выданный сейчас. Возвращает строки .env.
    """
    from database.tables import InstitutionLocalHost, ServiceCredential, ServiceInstance
    from platform_core import catalog
    from settings.config import settings
    from database.base import utc_now

    registry.lock_institution(db, institution_id)
    host = catalog.normalize_hostname(host)
    if not 1 <= int(port) <= 65535:
        raise DomainError(422, "VALIDATION_ERROR", "Порт 1..65535")
    origin = host if int(port) == 443 else f"{host}:{int(port)}"
    api_url = catalog.check_api_url(f"https://{origin}/api/v1", None)
    client_url = catalog.check_origin(f"https://{origin}", None)
    if code == "administration":
        service = registry.admin_service_of(db, institution_id) or registry.create_admin_instance(db, institution_id)
        service.api_base_url, service.client_base_url = api_url, client_url
    else:
        service_type = catalog.custom_code(code)
        if not db.get(InstitutionLocalHost, (institution_id, host)):
            db.add(InstitutionLocalHost(institution_id=institution_id, hostname=host, approved_by=None))
        service = db.query(ServiceInstance).filter(ServiceInstance.institution_id == institution_id,
                                                   ServiceInstance.service_type == service_type).first()
        if not service:
            service = registry.create_local_instance(db, institution_id, service_type, api_url, client_url,
                                                     {"ru": DEFAULT_TITLES.get(code, code)}, list(catalog.PROFILES))
        elif (service.api_base_url, service.client_base_url) != (api_url, client_url):
            service.api_base_url, service.client_base_url, service.enabled = api_url, client_url, False
            registry.revoke_service_sessions(db, service_id=service.id)
    now = utc_now()
    for old in db.query(ServiceCredential).filter(ServiceCredential.service_id == service.id,
                                                  ServiceCredential.revoked_at.is_(None)):
        old.revoked_at = now
    credential, secret = registry.issue_credential(db, service)
    registry.audit(db, scope="institution", action="service.connect", actor_user_id=None, actor_kind="operator",
                   institution_id=institution_id, target_type="service", target_id=service.id,
                   details={"service_type": service.service_type, "api_base_url": api_url})
    core = settings.JWT_ISSUER.rstrip("/")
    return {"service_id": service.id, "env": {
        "CORE_URL": core, "SHELL_ORIGIN": core, "SERVICE_CLIENT_ID": credential.client_id,
        "SERVICE_CLIENT_SECRET": secret, "SERVICE_API_BASE_URL": api_url, "SERVICE_CLIENT_BASE_URL": client_url}}


def enable_service(db: Session, institution_id: str, code: str) -> bool:
    """Включает сервис вуза, когда он опубликовал меню. False — меню ещё нет."""
    from database.tables import ServiceInstance
    from platform_core import catalog
    service_type = "administration" if code == "administration" else catalog.custom_code(code)
    service = db.query(ServiceInstance).filter(ServiceInstance.institution_id == institution_id,
                                               ServiceInstance.service_type == service_type).first()
    if not service:
        raise DomainError(404, "RESOURCE_NOT_FOUND", "Сервис не подключён")
    if not (service.manifest or {}).get("menus"):
        return False
    if not service.enabled:
        service.enabled = True
        registry.audit(db, scope="institution", action="service.update", actor_user_id=None, actor_kind="operator",
                       institution_id=institution_id, target_type="service", target_id=service.id,
                       details={"after": {"enabled": True}})
    return True
