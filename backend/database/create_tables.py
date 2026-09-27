from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from settings.config import settings
from settings.security import hash_password
from database.tables import (
    table_class,
    Institution,
    ServiceInstance,
    InstitutionStatus,
    ServiceTypeCode,
    DeploymentType,
    ServiceCredential,
    ServiceRole
)

engine = create_engine(settings.DATABASE_URL)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def create_tables():
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
                    service_type=ServiceTypeCode.USER_PROFILE.value,
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
                    service_type=ServiceTypeCode.SCHEDULE.value,
                    deployment=DeploymentType.CLOUD.value,
                    enabled=True,
                    protected=False,
                    api_base_url="https://schedule-api.platform.example/api/v1",
                    client_base_url="https://schedule-ui.platform.example",
                    supported_profiles=["student", "teacher", "admin"],
                    manifest={"titles": {"ru": "Расписание занятий", "en": "Schedule"}, "menus": []}
                ),
                ServiceInstance(
                    id="d6e73405-bdf0-46c4-930a-9f5f29a7403c",
                    institution_id=demo_inst.id,
                    service_type=ServiceTypeCode.ADMINISTRATION.value,
                    deployment=DeploymentType.CLOUD.value,
                    enabled=True,
                    protected=True,
                    api_base_url="https://admin-api.platform.example/api/v1",
                    client_base_url="https://admin-ui.platform.example",
                    supported_profiles=["admin"],
                    manifest={"titles": {"ru": "Панель администрирования", "en": "Administration"}, "menus": []}
                )
            ]
            db.add_all(services)
            db.flush()

            # Учетные данные для сервиса профилей
            cred_profile = ServiceCredential(
                id="a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
                client_id="user_profile_service_client",
                service_id=services[0].id,
                hashed_secret=hash_password("service_super_secret_key_123")
            )
            # Учетные данные для сервиса администрирования
            cred_admin = ServiceCredential(
                id="f2e3d4c5-b6a7-8901-2345-6789abcdef01",
                client_id="admin_service_client",
                service_id=services[2].id,
                hashed_secret=hash_password("admin_service_super_secret_123")
            )
            db.add_all([cred_profile, cred_admin])

            # Системная роль администратора ВУЗа со всеми правами
            admin_system_role = ServiceRole(
                service_id=services[2].id,
                code="admin_owner",
                titles={"ru": "Администратор ВУЗа", "en": "Institution Owner"},
                allowed_profiles=["admin"],
                permissions=[
                    "institution.read",
                    "institution.update",
                    "members.read",
                    "members.manage",
                    "services.read",
                    "services.manage",
                    "credentials.manage",
                    "roles.manage"
                ],
                system=True
            )
            db.add(admin_system_role)
            db.commit()
    finally:
        db.close()

def get_db():
    db = session_local()
    try:
        yield db
    finally:
        db.close()