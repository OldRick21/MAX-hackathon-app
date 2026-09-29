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
    from privacy import erase_user
    erase_user(db, user)
    registry.audit(db, scope="platform", action="user.delete", actor_user_id=None, actor_kind="operator",
                   target_type="user", target_id=user_id)


def install_cloud_service(db: Session, institution_id: str, service_type: str) -> tuple:
    """Ставит вузу облачный сервис (schedule, user-profile) или приводит существующий к облаку.

    Повторный вызов безопасен. Возвращает (экземпляр, создан ли).
    """
    from database.tables import ServiceInstance
    from platform_core import catalog
    if service_type not in catalog.CLOUD_INSTALLABLE:
        raise DomainError(422, "VALIDATION_ERROR", "Облачно устанавливаются schedule и user-profile")
    registry.lock_institution(db, institution_id)
    service = db.query(ServiceInstance).filter(ServiceInstance.institution_id == institution_id,
                                               ServiceInstance.service_type == service_type,
                                               ServiceInstance.deleted_at.is_(None)).first()
    if service:
        registry.make_cloud(db, service)
        return service, False
    service = registry.create_cloud_instance(db, institution_id, service_type)
    registry.audit(db, scope="institution", action="service.install", actor_user_id=None, actor_kind="operator",
                   institution_id=institution_id, target_type="service", target_id=service.id,
                   details={"service_type": service_type, "deployment": "cloud"})
    return service, True
