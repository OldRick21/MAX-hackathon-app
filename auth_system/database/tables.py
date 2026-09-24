import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    DateTime,
    Boolean,
    ForeignKey,
    JSON,
    event,
    UniqueConstraint
)
from sqlalchemy.orm import relationship, declarative_base

table_class = declarative_base()

def generate_uuid() -> str:
    return str(uuid.uuid4())

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

# --- Перечисления по спецификации OpenAPI ---

class InstitutionStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class ProfileEnum(str, enum.Enum):
    ADMIN = "admin"
    TEACHER = "teacher"
    STUDENT = "student"


class ServiceTypeCode(str, enum.Enum):
    ADMINISTRATION = "administration"
    SCHEDULE = "schedule"
    USER_PROFILE = "user-profile"
    COURSEWORK = "coursework"

class DeploymentType(str, enum.Enum):
    CLOUD = "cloud"
    LOCAL = "local"

# --- Основные сущности ---

class User(table_class):
    """Глобальный пользователь платформы (компонент User в OpenAPI)."""
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    # Идентификатор пользователя в мессенджере Макс
    max_user_id = Column(String(32), unique=True, index=True, nullable=True)
    # username и password остаются для разработки / фоллбека
    username = Column(String(50), unique=True, index=True, nullable=True)
    email = Column(String(100), unique=True, index=True, nullable=True)
    hashed_password = Column(String(255), nullable=True)
    
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    # Связи
    memberships = relationship("Membership", back_populates="user", cascade="all, delete-orphan")
    core_sessions = relationship("CoreSession", back_populates="user", cascade="all, delete-orphan")

class Institution(table_class):
    """ВУЗ (компонент InstitutionView / AdminInstitution в OpenAPI)."""
    __tablename__ = "institutions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    # titles: {"ru": "МИФИ", "en": "MEPhI"}
    titles = Column(JSON, nullable=False)
    default_locale = Column(String(10), default="ru", nullable=False)
    status = Column(String(20), default=InstitutionStatus.ACTIVE.value, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    # Связи
    members = relationship("Membership", back_populates="institution", cascade="all, delete-orphan")
    services = relationship("ServiceInstance", back_populates="institution", cascade="all, delete-orphan")

class Membership(table_class):
    """
    Членство пользователя в ВУЗе с набором профилей.
    Компонент Membership в OpenAPI.
    """
    __tablename__ = "memberships"

    institution_id = Column(String(36), ForeignKey("institutions.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # Массив профилей в формате JSON: ["admin", "teacher", "student"]
    profiles = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    institution = relationship("Institution", back_populates="members")
    user = relationship("User", back_populates="memberships")

class ServiceInstance(table_class):
    """Экземпляр сервиса в рамках ВУЗа (компонент AdminService в OpenAPI)."""
    __tablename__ = "services"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    institution_id = Column(String(36), ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    service_type = Column(String(50), nullable=False)  # administration, schedule, user-profile, coursework
    deployment = Column(String(20), nullable=False)    # cloud, local
    enabled = Column(Boolean, default=True, nullable=False)
    protected = Column(Boolean, default=False, nullable=False)  # True для administration
    api_base_url = Column(String(2048), nullable=False)
    client_base_url = Column(String(2048), nullable=False)
    # Список профилей, с которыми сервис совместим: ["admin", "teacher", "student"]
    supported_profiles = Column(JSON, nullable=False, default=list)
    # Манифест меню и заголовков (ServiceManifest)
    manifest = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    institution = relationship("Institution", back_populates="services")
    roles = relationship("ServiceRole", back_populates="service", cascade="all, delete-orphan")
    credentials = relationship("ServiceCredential", back_populates="service", cascade="all, delete-orphan")

    __table_args__ = (
        # Один тип сервиса на вуз по спецификации
        UniqueConstraint("institution_id", "service_type", name="uq_institution_service_type"),
    )

# --- RBAC внутри сервисов ---

class ServiceRole(table_class):
    """Определение роли в сервисе (компонент Role в OpenAPI)."""
    __tablename__ = "service_roles"

    service_id = Column(String(36), ForeignKey("services.id", ondelete="CASCADE"), primary_key=True)
    code = Column(String(64), primary_key=True)  # например, "schedule_editor"
    titles = Column(JSON, nullable=False)         # LocalizedText {"ru": "...", "en": "..."}
    allowed_profiles = Column(JSON, nullable=False, default=list)  # ["admin", "teacher"]
    permissions = Column(JSON, nullable=False, default=list)       # ["schedule.write", "schedule.read"]
    system = Column(Boolean, default=False, nullable=False)

    service = relationship("ServiceInstance", back_populates="roles")

class RoleAssignment(table_class):
    """Назначенные роли участника в экземпляре (AssignmentSet в OpenAPI)."""
    __tablename__ = "role_assignments"

    service_id = Column(String(36), ForeignKey("services.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    profile = Column(String(20), primary_key=True)  # admin, teacher, student
    
    # Список назначенных кодов ролей: ["schedule_editor"]
    roles = Column(JSON, nullable=False, default=list)

    service = relationship("ServiceInstance")
    user = relationship("User")

# --- Сессии (Core и Service) ---

class CoreSession(table_class):
    """Основная сессия пользователя в ядре (CoreBearer)."""
    __tablename__ = "core_sessions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    current_refresh_jti = Column(String(64), nullable=False)
    is_revoked = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    user = relationship("User", back_populates="core_sessions")
    service_sessions = relationship("ServiceSession", back_populates="parent_session", cascade="all, delete-orphan")

class ServiceSession(table_class):
    """Сессия обращения к конкретному сервису (ServiceTokenPair)."""
    __tablename__ = "service_sessions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    parent_session_id = Column(String(36), ForeignKey("core_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    institution_id = Column(String(36), ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False)
    service_id = Column(String(36), ForeignKey("services.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    profile = Column(String(20), nullable=False)  # admin, teacher, student

    current_refresh_jti = Column(String(64), nullable=False)
    is_revoked = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    parent_session = relationship("CoreSession", back_populates="service_sessions")

# --- Machine Credentials для локальных сервисов ---

class ServiceCredential(table_class):
    """Credentials локального сервиса (Basic Auth: client_id + client_secret)."""
    __tablename__ = "service_credentials"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    client_id = Column(String(36), unique=True, nullable=False, default=generate_uuid)
    service_id = Column(String(36), ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True)
    hashed_secret = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    service = relationship("ServiceInstance", back_populates="credentials")