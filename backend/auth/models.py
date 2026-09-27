from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from database.base import table_class, generate_uuid, utc_now

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

