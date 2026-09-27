"""Команды оператора платформы для первоначальной настройки.

Повседневные операции (заявки вузов, участники, роли, сервисы) выполняются в
интерфейсе. Здесь только то, что по определению нельзя сделать из интерфейса:
назначить первого сотрудника поддержки и восстановить владельца, если доступа
к поддержке ещё нет.

    python manage.py grant-platform-role USER_UUID
    python manage.py revoke-platform-role USER_UUID
    python manage.py assign-owner INSTITUTION_UUID USER_UUID
    python manage.py list-institutions
    python manage.py ensure-invariants

Пользователь должен хотя бы раз войти через MAX: UUID показан в приложении.
"""
import argparse
import sys
from uuid import UUID

from database.create_tables import create_tables, session_local
from database.tables import Institution, PlatformRole, PlatformStaff, User
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
