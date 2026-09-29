import logging

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from settings.config import settings
from auth.security import hash_password
from database.tables import (
    table_class,
    Institution,
    ServiceInstance,
    InstitutionStatus,
    DeploymentType,
    ServiceCredential,
)

log = logging.getLogger(__name__)

engine = create_engine(settings.DATABASE_URL)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    # Контейнер перезапускают в любой момент: WAL журналирует так, что прерванная
    # запись откатывается при следующем открытии файла, а busy_timeout ждёт чужую
    # запись вместо ошибки "database is locked" и ответа 503. synchronous остаётся
    # штатным (FULL): терять последние коммиты сессий и вузов при сбое питания нельзя.
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.close()

session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def migrate_service_tombstones():
    """Схема до логического удаления: колонка deleted_at и частичный уникальный индекс вместо
    UNIQUE(institution_id, service_type). В SQLite ограничение таблицы снимается только
    пересозданием таблицы: одна явная транзакция, foreign_keys=OFF (без каскадного удаления ролей,
    ключей и сессий), затем проверка ссылок."""
    from sqlalchemy import inspect
    from sqlalchemy.schema import CreateIndex, CreateTable
    if engine.dialect.name != "sqlite" or not inspect(engine).has_table("services"):
        return
    columns = {c["name"] for c in inspect(engine).get_columns("services")}
    raw = engine.raw_connection()
    try:
        db = raw.driver_connection
        ddl = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='services'").fetchone()[0] or ""
        if "deleted_at" in columns and "uq_institution_service_type" not in ddl:
            return
        log.warning("Migrating services table to logical deletion")
        table = ServiceInstance.__table__
        previous = db.isolation_level
        db.isolation_level = None  # транзакцией управляем сами: DDL в ней тоже откатывается
        db.execute("PRAGMA foreign_keys=OFF")
        try:
            db.execute("BEGIN IMMEDIATE")
            if "deleted_at" not in columns:
                db.execute("ALTER TABLE services ADD COLUMN deleted_at DATETIME")
            if "uq_institution_service_type" in ddl:
                # «Создать новую — скопировать — удалить старую — переименовать новую»: ссылки других
                # таблиц на services остаются по имени и указывают на новую таблицу.
                create = str(CreateTable(table).compile(engine)).replace("CREATE TABLE services ", "CREATE TABLE services_new ", 1)
                db.execute(create)
                names = ", ".join(c.name for c in table.columns)
                db.execute(f"INSERT INTO services_new ({names}) SELECT {names} FROM services")
                db.execute("DROP TABLE services")
                db.execute("ALTER TABLE services_new RENAME TO services")
                for index in table.indexes:
                    db.execute(str(CreateIndex(index).compile(engine, compile_kwargs={"literal_binds": True})))
            broken = db.execute("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise RuntimeError(f"foreign key check failed after migration: {broken[:5]}")
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        finally:
            db.execute("PRAGMA foreign_keys=ON")
            db.isolation_level = previous
    finally:
        raw.close()


def create_tables():
    from platform_core.registry import ensure_platform_invariants

    migrate_service_tombstones()
    table_class.metadata.create_all(bind=engine)
    db = session_local()
    try:
        if settings.SEED_DEMO_DATA and db.query(Institution).count() == 0:
            demo_inst = Institution(
                id="46bf3278-29fa-4d0a-a711-e4a6a324ed31",
                titles={"ru": "НИЯУ МИФИ", "en": "NRNU MEPhI"},
                default_locale="ru",
                status=InstitutionStatus.ACTIVE.value
            )
            db.add(demo_inst)
            db.flush()

            services = [
                ServiceInstance(
                    id="b4c51283-fbdf-44a2-91e8-7f3d07f52e1a",
                    institution_id=demo_inst.id,
                    service_type="user-profile",
                    deployment=DeploymentType.CLOUD.value,
                    enabled=True,
                    protected=False,
                    api_base_url="https://profiles-api.platform.example/api/v1",
                    client_base_url="https://profiles-ui.platform.example",
                    supported_profiles=["student", "teacher", "admin"],
                    manifest={
                        "titles": {"ru": "Профиль пользователя", "en": "User Profile"},
                        "menus": [
                            {
                                "id": "home",
                                "titles": {"ru": "Главная", "en": "Home"},
                                "entrypoint_path": "/home",
                                "profiles": ["student", "teacher", "admin"],
                                "required_permissions": [],
                                "order": 0
                            }
                        ]
                    }
                ),
                ServiceInstance(
                    id="c5d62394-acef-45b3-82f9-8e4e18f63f2b",
                    institution_id=demo_inst.id,
                    service_type="schedule",
                    deployment=DeploymentType.CLOUD.value,
                    enabled=True,
                    protected=False,
                    api_base_url="https://schedule-api.platform.example/api/v1",
                    client_base_url="https://schedule-ui.platform.example",
                    supported_profiles=["student", "teacher", "admin"],
                    manifest={"titles": {"ru": "Расписание занятий", "en": "Schedule"}, "menus": []}
                )
            ]
            db.add_all(services)
            db.flush()

            # Учетные данные для сервиса профилей
            cred_profile = ServiceCredential(
                id="a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
                client_id="22370780-1c30-4de9-959e-b474475274c7",
                service_id=services[0].id,
                hashed_secret=hash_password("service_super_secret_key_123")
            )
            db.add(cred_profile)

            db.commit()
        # Инварианты платформы: у каждого вуза защищённый administration с системными ролями,
        # прежние встроенные типы — свои сервисы вуза. Идемпотентно, безопасно при каждом запуске.
        ensure_platform_invariants(db)
        db.commit()
    finally:
        db.close()
    from platform_core.service_files import export_services
    try:
        export_services(session_local)
    except Exception:
        # Файлы — восстановимая проекция БД. Недоступный каталог не должен
        # оставлять контейнер в цикле перезапуска: ядро поднимается и без них.
        log.exception('Service settings export failed; rebuild with manage.py export-services')

def get_db():
    db = session_local()
    try:
        yield db
    finally:
        db.close()

from platform_core.service_files import install_export_hook
install_export_hook(session_local)
