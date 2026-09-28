"""Команды оператора платформы для первоначальной настройки.

Всё это (и больше) делается в операторской веб-панели (operator_panel, порт 8445).
Команды остаются для автоматизации и аварийного доступа без браузера.

    python manage.py grant-platform-role USER_UUID
    python manage.py revoke-platform-role USER_UUID
    python manage.py assign-owner INSTITUTION_UUID USER_UUID
    python manage.py import-groups < groups.json
    python manage.py list-institutions
    python manage.py ensure-invariants

Пользователь должен хотя бы раз войти через MAX: UUID показан в приложении.
"""
import argparse
import sys
from uuid import UUID

from database.create_tables import create_tables, session_local
from database.tables import Institution, User
from platform_core import registry


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise SystemExit(f"Некорректный UUID: {value}")


def grant(user_id: str) -> None:
    from platform_core.errors import DomainError
    from services.platform_ops import grant_staff
    with session_local() as db:
        try:
            granted = grant_staff(db, user_id, "operator-cli")
        except DomainError:
            raise SystemExit("Пользователь не найден. Он должен войти через MAX; изменений нет.")
        db.commit()
    print("Права поддержки платформы выданы. Пользователю нужно заново открыть приложение." if granted
          else "Пользователь уже в поддержке платформы.")


def revoke(user_id: str) -> None:
    from services.platform_ops import revoke_staff
    with session_local() as db:
        revoked = revoke_staff(db, user_id)
        db.commit()
    print("Права поддержки платформы отозваны." if revoked else "Пользователь не в поддержке платформы.")


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


def import_groups(stream) -> None:
    """Перенос учебных групп из сервиса расписания в ядро с сохранением UUID.

    Вход — JSON из `python -m app.export_groups` в контейнере schedule:
    {"groups": [{"id", "institution_id", "name", "user_ids": [...]}]}. Повторный запуск
    безопасен: существующие группы и уже распределённые студенты пропускаются.
    """
    import json
    from database.tables import Membership, StudyGroup, StudyGroupMember
    data = json.load(stream)
    created = added = skipped = 0
    with session_local() as db:
        for item in data.get("groups", []):
            gid, inst, name = _uuid(item["id"]), _uuid(item["institution_id"]), str(item["name"]).strip()[:100]
            if not db.get(Institution, inst):
                print(f"Пропуск {name}: вуз {inst} не найден")
                skipped += 1
                continue
            group = db.get(StudyGroup, gid)
            if not group:
                if db.query(StudyGroup).filter_by(institution_id=inst, name_key=name.casefold()).first():
                    print(f"Пропуск {name}: в вузе уже есть группа с таким названием")
                    skipped += 1
                    continue
                group = StudyGroup(id=gid, institution_id=inst, name=name, name_key=name.casefold())
                db.add(group)
                db.flush()
                created += 1
            for uid in item.get("user_ids", []):
                member = db.get(Membership, (inst, uid))
                if not member or "student" not in (member.profiles or []) or db.get(StudyGroupMember, (inst, uid)):
                    skipped += 1
                    continue
                db.add(StudyGroupMember(institution_id=inst, user_id=uid, group_id=gid))
                added += 1
            group.members_revision += 1
        db.commit()
    print(f"Группы перенесены: создано {created}, студентов добавлено {added}, пропущено {skipped}.")


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
    sub.add_parser("import-groups", help="JSON групп из расписания на stdin")
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
    elif args.command == "import-groups":
        import_groups(sys.stdin)
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
