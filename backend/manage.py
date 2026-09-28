"""Команды оператора платформы для первоначальной настройки.

Повседневные операции (заявки вузов, участники, роли, сервисы) выполняются в
интерфейсе. Здесь только то, что по определению нельзя сделать из интерфейса:
назначить первого сотрудника поддержки и восстановить владельца, если доступа
к поддержке ещё нет.

    python manage.py grant-platform-role USER_UUID
    python manage.py revoke-platform-role USER_UUID
    python manage.py assign-owner INSTITUTION_UUID USER_UUID
    python manage.py install-people INSTITUTION_UUID
    python manage.py install-schedule INSTITUTION_UUID
    python manage.py list-institutions
    python manage.py ensure-invariants

Пользователь должен хотя бы раз войти через MAX: UUID показан в приложении.
"""
import argparse
import sys
from uuid import UUID

from database.create_tables import create_tables, session_local
from database.tables import Institution, PlatformRole, PlatformStaff, ServiceInstance, User
from platform_core import registry


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise SystemExit(f"Некорректный UUID: {value}")


def grant(user_id: str) -> None:
    with session_local() as db:
        if not db.get(User, user_id):
            raise SystemExit("Пользователь не найден. Он должен войти через MAX; изменений нет.")
        if db.get(PlatformStaff, user_id):
            print("Пользователь уже в поддержке платформы.")
            return
        db.add(PlatformStaff(user_id=user_id, role=PlatformRole.SUPPORT.value, granted_by="operator-cli"))
        registry.audit(db, scope="platform", action="staff.grant", actor_user_id=None, actor_kind="operator",
                       target_type="user", target_id=user_id, details={"role": PlatformRole.SUPPORT.value})
        db.commit()
    print("Права поддержки платформы выданы. Пользователю нужно заново открыть приложение.")


def revoke(user_id: str) -> None:
    with session_local() as db:
        staff = db.get(PlatformStaff, user_id)
        if not staff:
            print("Пользователь не в поддержке платформы.")
            return
        db.delete(staff)
        registry.audit(db, scope="platform", action="staff.revoke", actor_user_id=None, actor_kind="operator",
                       target_type="user", target_id=user_id)
        db.commit()
    print("Права поддержки платформы отозваны.")


def assign_owner(institution_id: str, user_id: str) -> None:
    with session_local() as db:
        registry.lock_institution(db, institution_id)
        if not db.get(User, user_id):
            raise SystemExit("Пользователь не найден. Он должен войти через MAX; изменений нет.")
        admin = registry.admin_service_of(db, institution_id)
        if admin and registry.owner_ids(db, institution_id, admin.id):
            raise SystemExit("У вуза уже есть владелец. Роли назначаются в интерфейсе администрирования.")
        registry.assign_owner(db, institution_id, user_id)
        registry.audit(db, scope="institution", action="owner.initial_assign", actor_user_id=None,
                       actor_kind="operator", institution_id=institution_id, target_type="member", target_id=user_id)
        db.commit()
    print("Владелец назначен: профиль admin и роль owner в сервисе администрирования.")


CLOUD_INSTALLS = {
    "user-profile": ("Сервис «Люди»", "Роль «Редактор анкет» (profile_editor) назначает владелец вуза в админке."),
    "schedule": ("Сервис расписания",
                 "Преподаватели задают свои занятия без отдельной роли. Роль «Редактор расписания» "
                 "(schedule_editor: группы и всё расписание) назначает владелец вуза в админке."),
}


def install_cloud(institution_id: str, service_type: str) -> None:
    """Ставит облачный сервис вузу и приводит его настройки к текущим адресам.

    Владелец вуза может сделать то же в админке. Команда нужна, когда сервис
    подключают до появления владельца или когда после переезда platform-адресов
    нужно починить существующий экземпляр. Повторный запуск безопасен.
    """
    from platform_core.errors import DomainError

    title, hint = CLOUD_INSTALLS[service_type]
    with session_local() as db:
        try:
            registry.lock_institution(db, institution_id)
        except DomainError:
            raise SystemExit("Вуз не найден; изменений нет.")
        service = db.query(ServiceInstance).filter_by(
            institution_id=institution_id, service_type=service_type).first()
        created = service is None
        if created:
            # create_cloud_instance уже добавила роли и binding; flush делает их
            # видимыми для повторных ensure_* ниже, иначе появится второй binding.
            service = registry.create_cloud_instance(db, institution_id, service_type)
            db.flush()
        elif service.deployment != "cloud":
            raise SystemExit("Существующий экземпляр не облачный; изменений нет.")
        # Адреса, манифест и профили задаёт платформа, а не администратор вуза.
        registry.sync_cloud_instance(db, service)
        service.enabled = True
        binding = registry.ensure_cloud_binding(db, service)
        registry.audit(db, scope="institution", action="service.install" if created else "service.update",
                       actor_user_id=None, actor_kind="operator", institution_id=institution_id,
                       target_type="service", target_id=service.id,
                       details={"service_type": service_type, "deployment": "cloud",
                                "api_base_url": service.api_base_url, "client_base_url": service.client_base_url})
        db.commit()
        service_id = service.id
    print(f"{title} {'установлен' if created else 'обновлён'}: {service_id}")
    if binding is None:
        print("CLOUD_BINDING_KEY не задан: процесс сервиса вернёт 503, пока binding не выдан.")
    # Настройки выгружаются, чтобы services/connected не ждал перезапуска backend.
    from platform_core.service_files import export_services
    try:
        export_services(session_local)
    except OSError as error:
        print(f"Не удалось выгрузить настройки ({error}); запустите manage.py export-services.")
    print(hint)


def install_people(institution_id: str) -> None:
    install_cloud(institution_id, "user-profile")


def install_schedule(institution_id: str) -> None:
    install_cloud(institution_id, "schedule")


def list_institutions() -> None:
    with session_local() as db:
        for inst in db.query(Institution).order_by(Institution.created_at).all():
            admin = registry.admin_service_of(db, inst.id)
            owners = len(registry.owner_ids(db, inst.id, admin.id)) if admin else 0
            print(f"{inst.id}  {inst.status:<9}  owners={owners}  {inst.titles.get('ru')}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("grant-platform-role").add_argument("user_id")
    sub.add_parser("revoke-platform-role").add_argument("user_id")
    owner = sub.add_parser("assign-owner")
    owner.add_argument("institution_id")
    owner.add_argument("user_id")
    sub.add_parser("install-people").add_argument("institution_id")
    sub.add_parser("install-schedule").add_argument("institution_id")
    sub.add_parser("list-institutions")
    sub.add_parser("ensure-invariants")
    sub.add_parser("export-services")
    args = parser.parse_args(argv)

    create_tables()  # создаёт новые таблицы и применяет инварианты
    if args.command == "grant-platform-role":
        grant(_uuid(args.user_id))
    elif args.command == "revoke-platform-role":
        revoke(_uuid(args.user_id))
    elif args.command == "assign-owner":
        assign_owner(_uuid(args.institution_id), _uuid(args.user_id))
    elif args.command == "install-people":
        install_people(_uuid(args.institution_id))
    elif args.command == "install-schedule":
        install_schedule(_uuid(args.institution_id))
    elif args.command == "list-institutions":
        list_institutions()
    elif args.command == "export-services":
        from platform_core.service_files import export_services
        export_services(session_local)
        print("Настройки сервисов выгружены.")
    elif args.command == "ensure-invariants":
        print("Инварианты платформы проверены.")


if __name__ == "__main__":
    main(sys.argv[1:])
