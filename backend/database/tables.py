import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    Text,
    Integer,
    DateTime,
    Boolean,
    ForeignKey,
    JSON,
    Index,
    LargeBinary,
    UniqueConstraint
)
from sqlalchemy.orm import relationship, declarative_base

from database.base import table_class, generate_uuid, utc_now
from auth.models import User, CoreSession, ServiceSession, ServiceCredential

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


class StudyGroup(table_class):
    """Учебная группа вуза — сущность платформы, как членство и профиль.

    Ведёт администратор вуза (groups.manage); сервисы читают группы через machine API
    (scope groups:read) и introspection (group_ids). Ссылки на группы в данных сервисов
    хранятся как UUID без FK на ядро.
    """
    __tablename__ = "study_groups"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    institution_id = Column(String(36), ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    # trim + casefold: «ИВТ-21» и « ивт-21 » — одна группа
    name_key = Column(String(100), nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    members_revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (UniqueConstraint("institution_id", "name_key", name="uq_study_group_name"),)


class StudyGroupMember(table_class):
    """Студент в группе. PK (вуз, пользователь): в MVP студент состоит максимум в одной группе вуза."""
    __tablename__ = "study_group_members"

    institution_id = Column(String(36), ForeignKey("institutions.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    group_id = Column(String(36), ForeignKey("study_groups.id", ondelete="CASCADE"), nullable=False, index=True)


class UserAvatar(table_class):
    """Аватар пользователя платформы: один на человека во всех вузах.

    Загружает сам пользователь (из MAX фото не берётся). Картинка уже уменьшена
    клиентом до квадрата; ядро проверяет формат по сигнатуре и размер.
    """
    __tablename__ = "user_avatars"

    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    content_type = Column(String(20), nullable=False)
    data = Column(LargeBinary, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class JoinRequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class JoinRequest(table_class):
    """Заявка пользователя на вступление в вуз. Членство появляется только после одобрения
    администратором вуза с правом members.manage. На вуз — не больше одной ожидающей заявки."""
    __tablename__ = "join_requests"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    institution_id = Column(String(36), ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    full_name = Column(String(200), nullable=False)
    profile = Column(String(20), nullable=False)  # student | teacher
    # Группа, которую выбрал студент. Удаление группы не удаляет заявку.
    group_id = Column(String(36), ForeignKey("study_groups.id", ondelete="SET NULL"), nullable=True)
    group_name = Column(String(100), nullable=True)  # название на момент подачи — для карточки
    status = Column(String(20), nullable=False, default=JoinRequestStatus.PENDING.value, index=True)
    decision_reason = Column(Text, nullable=True)
    reviewed_by = Column(String(36), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


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



# --- Machine Credentials для локальных сервисов ---



# --- Платформа: поддержка, заявки вузов, bindings, аудит ---

class PlatformRole(str, enum.Enum):
    SUPPORT = "platform_support"


class ApplicationStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class PlatformStaff(table_class):
    """Сотрудник поддержки платформы. Права не связаны с членством в вузах.

    Первую запись создаёт оператор командой `python manage.py grant-platform-role`.
    """
    __tablename__ = "platform_staff"

    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role = Column(String(32), nullable=False, default=PlatformRole.SUPPORT.value)
    granted_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class InstitutionApplication(table_class):
    """Заявка на подключение вуза. Вуз создаётся только после одобрения поддержкой."""
    __tablename__ = "institution_applications"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    applicant_user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    titles = Column(JSON, nullable=False)
    default_locale = Column(String(10), nullable=False, default="ru")
    contact = Column(String(200), nullable=False)
    comment = Column(Text, nullable=False, default="")
    status = Column(String(20), nullable=False, default=ApplicationStatus.PENDING.value, index=True)
    decision_reason = Column(Text, nullable=True)
    reviewed_by = Column(String(36), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    institution_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class InstitutionLocalHost(table_class):
    """DNS hostname, одобренный поддержкой для local-экземпляров вуза (CORE_API_SPEC.md §7)."""
    __tablename__ = "institution_local_hosts"

    institution_id = Column(String(36), ForeignKey("institutions.id", ondelete="CASCADE"), primary_key=True)
    hostname = Column(String(253), primary_key=True)
    approved_by = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class CloudBinding(table_class):
    """Привязка облачного экземпляра к credential (CLOUD_RUNTIME_SPEC.md §2).

    Секрет не хранится: он выводится из CLOUD_BINDING_KEY и credential_id.
    """
    __tablename__ = "cloud_bindings"

    service_id = Column(String(36), ForeignKey("services.id", ondelete="CASCADE"), primary_key=True)
    institution_id = Column(String(36), nullable=False, index=True)
    service_type = Column(String(50), nullable=False, index=True)
    credential_id = Column(String(36), nullable=False)
    client_id = Column(String(36), nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    active = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class IdempotencyRecord(table_class):
    """Результат создания по Idempotency-Key на 24 часа (CORE_API_SPEC.md §3)."""
    __tablename__ = "idempotency_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    actor_user_id = Column(String(36), nullable=False)
    institution_id = Column(String(36), nullable=False)
    method = Column(String(10), nullable=False)
    path = Column(String(512), nullable=False)
    key = Column(String(36), nullable=False)
    request_hash = Column(String(64), nullable=False)
    resource_id = Column(String(36), nullable=True)
    response = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("actor_user_id", "institution_id", "method", "path", "key", name="uq_idempotency_scope"),
    )


class AuditEvent(table_class):
    """Журнал административных изменений. Без токенов, секретов и анкет.

    Внешних ключей нет намеренно: запись переживает удаление экземпляра/вуза.
    """
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    scope = Column(String(20), nullable=False)          # institution | platform
    institution_id = Column(String(36), nullable=True)
    actor_user_id = Column(String(36), nullable=True)
    actor_kind = Column(String(20), nullable=False)     # admin | platform_support | operator | system
    machine_credential_id = Column(String(36), nullable=True)
    request_id = Column(String(36), nullable=True)
    facade_request_id = Column(String(36), nullable=True)
    action = Column(String(64), nullable=False)
    target_type = Column(String(32), nullable=True)
    target_id = Column(String(128), nullable=True)
    outcome = Column(String(20), nullable=False)        # success | denied
    error_code = Column(String(40), nullable=True)
    details = Column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_audit_institution_id", "institution_id", "id"),
        Index("ix_audit_scope_id", "scope", "id"),
    )
