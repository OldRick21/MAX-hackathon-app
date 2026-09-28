"""Операции оператора платформы: общие для manage.py и операторской веб-панели.

Функции не коммитят сами — вызывающий код решает, где граница транзакции.
"""
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from database.tables import CloudBinding, Membership, PlatformRole, PlatformStaff, ServiceInstance, User
from platform_core import registry
from platform_core.errors import DomainError

CLOUD_INSTALLS = {
    "user-profile": ("Сервис «Люди»", "Роль «Редактор анкет» (profile_editor) назначает владелец вуза в админке."),
    "schedule": ("Сервис расписания",
                 "Преподаватели задают свои занятия без отдельной роли. Роль «Редактор расписания» "
                 "(schedule_editor: всё расписание) назначает владелец вуза в админке. Группы — в «Администрирование» → «Группы»."),
}


def install_cloud_service(db: Session, institution_id: str, service_type: str,
                          actor_kind: str = "operator") -> Tuple[ServiceInstance, bool, Optional[CloudBinding]]:
    """Ставит облачный сервис вузу и приводит его настройки к текущим адресам. Повторный вызов безопасен."""
    if service_type not in CLOUD_INSTALLS:
        raise DomainError(422, "VALIDATION_ERROR", "Облачные сервисы: user-profile или schedule")
    registry.lock_institution(db, institution_id)
    service = db.query(ServiceInstance).filter_by(institution_id=institution_id, service_type=service_type).first()
    created = service is None
    if created:
        # create_cloud_instance уже добавила роли и binding; flush делает их
        # видимыми для повторных ensure_* ниже, иначе появится второй binding.
        service = registry.create_cloud_instance(db, institution_id, service_type)
        db.flush()
    elif service.deployment != "cloud":
        raise DomainError(409, "INVALID_STATE", "Существующий экземпляр не облачный")
    # Адреса, манифест и профили задаёт платформа, а не администратор вуза.
    registry.sync_cloud_instance(db, service)
    service.enabled = True
    binding = registry.ensure_cloud_binding(db, service)
    registry.audit(db, scope="institution", action="service.install" if created else "service.update",
                   actor_user_id=None, actor_kind=actor_kind, institution_id=institution_id,
                   target_type="service", target_id=service.id,
                   details={"service_type": service_type, "deployment": "cloud",
                            "api_base_url": service.api_base_url, "client_base_url": service.client_base_url})
    return service, created, binding


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
