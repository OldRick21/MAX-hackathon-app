import time
from datetime import timedelta
from typing import List
from fastapi import HTTPException
from sqlalchemy.orm import Session
from database.tables import (
    User,
    Institution,
    Membership,
    ServiceInstance,
    CoreSession,
    ServiceSession,
    ServiceRole,
    RoleAssignment,
    ProfileEnum,
    utc_now,
    generate_uuid
)
from auth.revocations import revoked_tokens_redis
from auth.security import hash_password, verify_password, security
from settings.config import settings

from auth.max_validation import validate_max_init_data

class AuthService:
    @staticmethod
    def _create_token(sub: str, token_use: str, aud: str, expiry: timedelta, custom_claims: dict) -> str:
        return security.create_token(
            uid=sub,
            token_use=token_use,
            aud=aud,
            expiry=expiry,
            custom_claims=custom_claims
        )


    @staticmethod
    def login_or_register(auth_data, db: Session):
        import random
        user = None

        if not settings.ALLOW_DEV_LOGIN and (auth_data.username or auth_data.max_user_id or auth_data.password):
            raise HTTPException(status_code=400, detail="Only MAX initData login is enabled")

        # 1. Dev-вход по username или max_user_id
        if auth_data.username:
            user = db.query(User).filter(User.username == auth_data.username).first()
            if not user:
                # Гарантируем уникальность max_user_id по спецификации (числа ^[1-9][0-9]*$)
                generated_max_id = auth_data.max_user_id or f"{int(time.time())}{random.randint(1000, 9999)}"
                user = User(
                    username=auth_data.username,
                    max_user_id=generated_max_id,
                    hashed_password=hash_password(auth_data.password or "secret")
                )
                db.add(user)
                db.flush()

                # Автоматически привязываем к демо-ВУЗу только dev-пользователей с префиксом ivan_
                if auth_data.username.startswith("ivan_"):
                    demo_inst = db.get(Institution, "46bf3278-29fa-4d0a-a711-e4a6a324ed31") or db.query(Institution).order_by(Institution.created_at).first()
                    if demo_inst:
                        profiles = [ProfileEnum.STUDENT.value, ProfileEnum.TEACHER.value]
                        if "admin" in auth_data.username.lower():
                            profiles.append(ProfileEnum.ADMIN.value)

                        membership = Membership(
                            institution_id=demo_inst.id,
                            user_id=user.id,
                            profiles=profiles
                        )
                        db.add(membership)
                        if ProfileEnum.ADMIN.value in profiles:
                            # Только dev-режим: тестовый администратор становится владельцем.
                            from platform_core.registry import assign_owner
                            db.flush()
                            assign_owner(db, demo_inst.id, user.id)
                db.commit()
            elif auth_data.password and not verify_password(auth_data.password, user.hashed_password):
                raise HTTPException(status_code=401, detail="Invalid credentials")

        elif auth_data.max_user_id:
            user = db.query(User).filter(User.max_user_id == auth_data.max_user_id).first()
            if not user:
                user = User(max_user_id=auth_data.max_user_id)
                db.add(user)
                db.commit()

        elif auth_data.initData:
            bot_token = settings.MAX_BOT_TOKEN
            if not bot_token:
                raise HTTPException(status_code=503, detail="MAX login is not configured")
            user_payload = validate_max_init_data(auth_data.initData, bot_token)
            max_id = str(user_payload.get("id"))
            
            user = db.query(User).filter(User.max_user_id == max_id).first()
            if not user:
                user = User(max_user_id=max_id)
                db.add(user)
                db.commit()
        else:
            raise HTTPException(status_code=400, detail="Either initData or username/max_user_id must be provided")

        # 2. Создание Core-сессии ядра
        session_id = generate_uuid()
        refresh_jti = generate_uuid()
        now = utc_now()
        refresh_exp = now + timedelta(days=7)

        core_session = CoreSession(
            id=session_id,
            user_id=user.id,
            current_refresh_jti=refresh_jti,
            expires_at=refresh_exp
        )
        db.add(core_session)
        db.commit()

        # 3. Выпуск токенов ядра (CoreTokenPair)
        access_token = AuthService._create_token(
            sub=user.id,
            token_use="core_access",
            aud="core-api",
            expiry=timedelta(minutes=10),
            custom_claims={"session_id": session_id}
        )

        refresh_token = AuthService._create_token(
            sub=user.id,
            token_use="core_refresh",
            aud="core-api",
            expiry=timedelta(days=7),
            custom_claims={"session_id": session_id, "jti": refresh_jti}
        )

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": 600,
            "refresh_expires_in": 604800,
            "session_id": session_id
        }


    @staticmethod
    def refresh_core_session(refresh_token_str: str, db: Session):
        """Атомарная ротация пары Core-токенов по спецификации."""
        try:
            payload = security._decode_token(refresh_token_str)
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid refresh token")

        data = payload.data if hasattr(payload, "data") and isinstance(payload.data, dict) else {}
        token_use = getattr(payload, "token_use", None) or data.get("token_use")
        session_id = getattr(payload, "session_id", None) or data.get("session_id")
        jti = getattr(payload, "jti", None) or data.get("jti")

        if token_use != "core_refresh" or not session_id or not jti:
            raise HTTPException(status_code=401, detail="Invalid token type")

        session_entry = db.query(CoreSession).filter(CoreSession.id == session_id).first()
        if not session_entry or session_entry.is_revoked:
            raise HTTPException(status_code=401, detail="Session revoked or not found")

        # Проверка повторного использования (Replay attack detection)
        if session_entry.current_refresh_jti != jti:
            # Атомарный отзыв всей семьи сессий
            session_entry.is_revoked = True
            db.commit()
            revoked_tokens_redis.set(f"core_session_revoked:{session_id}", "1", ex=604800)
            raise HTTPException(status_code=401, detail="Refresh token already used. Session terminated.")

        # Ротация JTI
        new_refresh_jti = generate_uuid()
        session_entry.current_refresh_jti = new_refresh_jti
        db.commit()

        new_access = AuthService._create_token(
            sub=session_entry.user_id,
            token_use="core_access",
            aud="core-api",
            expiry=timedelta(minutes=10),
            custom_claims={"session_id": session_id}
        )

        new_refresh = AuthService._create_token(
            sub=session_entry.user_id,
            token_use="core_refresh",
            aud="core-api",
            expiry=timedelta(days=7),
            custom_claims={"session_id": session_id, "jti": new_refresh_jti}
        )

        return {
            "access_token": new_access,
            "refresh_token": new_refresh,
            "token_type": "Bearer",
            "expires_in": 600,
            "refresh_expires_in": 604800,
            "session_id": session_id
        }


    @staticmethod
    def logout_core_session(refresh_token_str: str, db: Session):
        """Отзывает core-сессию и каскадно инвалидирует связанные service sessions."""
        try:
            payload = security._decode_token(refresh_token_str)
            data = payload.data if hasattr(payload, "data") and isinstance(payload.data, dict) else {}
            session_id = getattr(payload, "session_id", None) or data.get("session_id")
            if session_id:
                session_entry = db.query(CoreSession).filter(CoreSession.id == session_id).first()
                if session_entry:
                    session_entry.is_revoked = True
                    db.commit()
                # Помещаем session_id в Redis для мгновенной блокировки всех токенов
                revoked_tokens_redis.set(f"core_session_revoked:{session_id}", "1", ex=604800)
        except Exception:
            pass  # По спецификации 204 возвращается даже при неизвестном токене
        return


    @staticmethod
    def create_service_session(
        user: User,
        core_session: CoreSession,
        institution_id: str,
        service_id: str,
        profile: str,
        db: Session
    ):
        """Выдает пару JWT для обращения к конкретному сервису под выбранным профилем."""
        profile_norm = profile.lower()

        # 1. Проверяем, что пользователь состоит в этом ВУЗе и обладает данным профилем
        membership = db.query(Membership).filter(
            Membership.institution_id == institution_id,
            Membership.user_id == user.id
        ).first()

        if not membership:
            raise HTTPException(status_code=403, detail="Forbidden: User is not a member of this institution")

        if profile_norm not in [p.lower() for p in (membership.profiles or [])]:
            raise HTTPException(status_code=403, detail=f"Forbidden: Profile '{profile}' is not assigned to user")

        institution = db.query(Institution).filter(Institution.id == institution_id).first()
        if not institution or institution.status != "active":
            raise HTTPException(status_code=409, detail="RESOURCE_INACTIVE: Institution is not active")

        # 2. Проверяем сервис
        service = db.query(ServiceInstance).filter(
            ServiceInstance.id == service_id,
            ServiceInstance.institution_id == institution_id
        ).first()

        if not service:
            raise HTTPException(status_code=404, detail="Service not found in this institution")

        if not service.enabled:
            raise HTTPException(status_code=409, detail="RESOURCE_INACTIVE: Service is currently disabled")

        if profile_norm not in [p.lower() for p in service.supported_profiles]:
            raise HTTPException(status_code=404, detail=f"Service does not support profile '{profile}'")

        # 3. Вычисляем роли и права из БД
        assignments = db.query(RoleAssignment).filter(
            RoleAssignment.service_id == service_id,
            RoleAssignment.user_id == user.id,
            RoleAssignment.profile == profile_norm
        ).first()

        assigned_roles = assignments.roles if assignments else []
        permissions: List[str] = []

        if assigned_roles:
            roles_in_db = db.query(ServiceRole).filter(
                ServiceRole.service_id == service_id,
                ServiceRole.code.in_(assigned_roles)
            ).all()
            for r in roles_in_db:
                permissions.extend(r.permissions or [])
        permissions = list(set(permissions))

        # 4. Создаем Service-сессию
        service_session_id = generate_uuid()
        refresh_jti = generate_uuid()
        now = utc_now()
        exp_time = now + timedelta(hours=24)

        service_session = ServiceSession(
            id=service_session_id,
            parent_session_id=core_session.id,
            institution_id=institution_id,
            service_id=service_id,
            user_id=user.id,
            profile=profile_norm,
            current_refresh_jti=refresh_jti,
            expires_at=exp_time
        )
        db.add(service_session)
        db.commit()

        # 5. Генерируем токены сервиса (ServiceTokenPair)
        access_token = AuthService._create_token(
            sub=user.id,
            token_use="service_access",
            aud=f"service:{service_id}",
            expiry=timedelta(seconds=300),
            custom_claims={
                "session_id": service_session_id,
                "parent_session_id": core_session.id,
                "institution_id": institution_id,
                "service_id": service_id,
                "profile": profile_norm,
                "roles": assigned_roles,
                "permissions": permissions
            }
        )

        refresh_token = AuthService._create_token(
            sub=user.id,
            token_use="service_refresh",
            aud=f"service:{service_id}",
            expiry=timedelta(hours=24),
            custom_claims={
                "session_id": service_session_id,
                "parent_session_id": core_session.id,
                "institution_id": institution_id,
                "service_id": service_id,
                "profile": profile_norm,
                "jti": refresh_jti
            }
        )

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": 300,
            "refresh_expires_in": 86400,
            "session_id": service_session_id,
            "parent_session_id": core_session.id,
            "institution_id": institution_id,
            "service_id": service_id,
            "profile": profile_norm
        }


    @staticmethod
    def issue_machine_token(credentials, req_body, db: Session):
        """Обмен Basic Auth (client_id:client_secret) на Machine JWT."""
        from database.tables import ServiceCredential, InstitutionStatus

        if not credentials:
            raise HTTPException(
                status_code=401,
                detail="Basic credentials missing",
                headers={"WWW-Authenticate": 'Basic realm="core-service"'}
            )

        client_id = credentials.username
        client_secret = credentials.password

        cred = db.query(ServiceCredential).filter(ServiceCredential.client_id == client_id).first()
        if not cred or cred.revoked_at is not None:
            raise HTTPException(status_code=401, detail="Invalid client credentials")

        if not verify_password(client_secret, cred.hashed_secret):
            raise HTTPException(status_code=401, detail="Invalid client credentials")

        service = cred.service
        if not service:
            raise HTTPException(status_code=404, detail="Service instance not found")

        if service.institution.status in [InstitutionStatus.PENDING.value, InstitutionStatus.SUSPENDED.value]:
            raise HTTPException(status_code=409, detail="RESOURCE_INACTIVE: Institution is pending or suspended")

        # Scopes назначает платформа по типу экземпляра (CORE_API_SPEC.md §5).
        scopes = [
            "manifest:write",
            "roles:read",
            "roles:write",
            "assignments:read",
            "assignments:write",
            "profiles:read",
            "tokens:introspect",
        ]
        if service.service_type == "administration":
            scopes = [s for s in scopes if s not in ("roles:write", "assignments:write")] + ["institution:manage"]

        machine_jwt = AuthService._create_token(
            sub=client_id,
            token_use="machine_access",
            aud="core-internal",
            expiry=timedelta(seconds=300),
            custom_claims={
                "institution_id": service.institution_id,
                "service_id": service.id,
                "credential_id": cred.id,
                "jti": generate_uuid(),
                "scopes": scopes
            }
        )

        return {
            "access_token": machine_jwt,
            "token_type": "Bearer",
            "expires_in": 300,
            "institution_id": service.institution_id,
            "service_id": service.id,
            "credential_id": cred.id,
            "scopes": scopes
        }


    @staticmethod
    def introspect_service_token(token_str: str, machine_claims, db: Session):
        """Интроспекция service_access токена."""
        # Чужой, истекший или невалидный токен возвращает 200 {"active": False}
        try:
            payload = security._decode_token(token_str)
        except Exception:
            return {"active": False}

        data = payload.data if hasattr(payload, "data") and isinstance(payload.data, dict) else {}
        token_use = getattr(payload, "token_use", None) or data.get("token_use")
        service_id = getattr(payload, "service_id", None) or data.get("service_id")
        session_id = getattr(payload, "session_id", None) or data.get("session_id")
        parent_sid = getattr(payload, "parent_session_id", None) or data.get("parent_session_id")
        institution_id = getattr(payload, "institution_id", None) or data.get("institution_id")
        profile = getattr(payload, "profile", None) or data.get("profile")
        user_id = payload.sub
        exp = getattr(payload, "exp", None) or data.get("exp")

        # 1. Проверяем тип токена и совпадение с сервисом машины
        machine_service_id = getattr(machine_claims, "service_id", None) or machine_claims.get("service_id")
        if token_use != "service_access" or service_id != machine_service_id:
            return {"active": False}

        # 2. Проверяем отзыв в Redis (родительской Core сессии)
        if revoked_tokens_redis.exists(f"core_session_revoked:{parent_sid}"):
            return {"active": False}

        # 3. Проверяем сессию в БД
        service_session = db.query(ServiceSession).filter(ServiceSession.id == session_id).first()
        if not service_session or service_session.is_revoked or service_session.user_id != user_id \
                or service_session.service_id != service_id or service_session.profile != (profile or "").lower():
            return {"active": False}
        expires_at = service_session.expires_at
        now = utc_now() if expires_at.tzinfo else utc_now().replace(tzinfo=None)
        if expires_at <= now:
            return {"active": False}
        parent = db.query(CoreSession).filter(CoreSession.id == service_session.parent_session_id).first()
        if not parent or parent.is_revoked:
            return {"active": False}
        service_row = db.query(ServiceInstance).filter(ServiceInstance.id == service_id).first()
        if not service_row or not service_row.enabled or service_row.institution_id != institution_id \
                or service_row.institution.status != "active":
            return {"active": False}

        # 4. Проверяем актуальное членство пользователя
        membership = db.query(Membership).filter(
            Membership.institution_id == institution_id,
            Membership.user_id == user_id
        ).first()

        if not membership or profile.lower() not in [p.lower() for p in (membership.profiles or [])]:
            return {"active": False}

        # 5. Актуальные роли и permissions из БД
        assignments = db.query(RoleAssignment).filter(
            RoleAssignment.service_id == service_id,
            RoleAssignment.user_id == user_id,
            RoleAssignment.profile == profile.lower()
        ).first()

        assigned_roles = assignments.roles if assignments else []
        permissions: List[str] = []
        if assigned_roles:
            roles_in_db = db.query(ServiceRole).filter(
                ServiceRole.service_id == service_id,
                ServiceRole.code.in_(assigned_roles)
            ).all()
            for r in roles_in_db:
                permissions.extend(r.permissions or [])

        return {
            "active": True,
            "sub": user_id,
            "aud": f"service:{service_id}",
            "exp": exp,
            "session_id": session_id,
            "parent_session_id": parent_sid,
            "institution_id": institution_id,
            "service_id": service_id,
            "profile": profile.lower(),
            "roles": assigned_roles,
            "permissions": list(set(permissions))
        }


    @staticmethod
    def get_public_jwks():
        """Публичные ключи ядра по контракту JWKS."""
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": "core-access-key-1",
                    "n": "u1W_z8r45k9q8gLpX...",
                    "e": "AQAB"
                }
            ]
        }


    @staticmethod
    def refresh_service_session(service_id: str, institution_id: str, refresh_token_str: str, db: Session):
        """Ротация пары токенов сессии сервиса."""
        try:
            payload = security._decode_token(refresh_token_str)
        except Exception:
            raise HTTPException(status_code=401, detail="INVALID_REFRESH_TOKEN: Invalid token")

        data = payload.data if hasattr(payload, "data") and isinstance(payload.data, dict) else {}
        token_use = getattr(payload, "token_use", None) or data.get("token_use")
        session_id = getattr(payload, "session_id", None) or data.get("session_id")
        parent_sid = getattr(payload, "parent_session_id", None) or data.get("parent_session_id")
        token_srv = getattr(payload, "service_id", None) or data.get("service_id")
        token_inst = getattr(payload, "institution_id", None) or data.get("institution_id")
        profile = getattr(payload, "profile", None) or data.get("profile")
        jti = getattr(payload, "jti", None) or data.get("jti")
        user_id = payload.sub

        if token_use != "service_refresh" or token_srv != service_id or token_inst != institution_id:
            raise HTTPException(status_code=401, detail="INVALID_REFRESH_TOKEN: Path mismatch")

        # Проверка родительской Core-сессии в Redis
        if revoked_tokens_redis.exists(f"core_session_revoked:{parent_sid}"):
            raise HTTPException(status_code=401, detail="UNAUTHENTICATED: Parent core session is terminated")

        sess = db.query(ServiceSession).filter(ServiceSession.id == session_id).first()
        if not sess or sess.is_revoked:
            raise HTTPException(status_code=401, detail="UNAUTHENTICATED: Service session is revoked")

        if sess.current_refresh_jti != jti:
            # Replay attack: отзываем сервисную сессию
            sess.is_revoked = True
            db.commit()
            raise HTTPException(status_code=401, detail="INVALID_REFRESH_TOKEN: Token already used")

        # Ротация jti
        new_jti = generate_uuid()
        sess.current_refresh_jti = new_jti
        db.commit()

        # Актуальные роли
        assignments = db.query(RoleAssignment).filter(
            RoleAssignment.service_id == service_id,
            RoleAssignment.user_id == user_id,
            RoleAssignment.profile == profile
        ).first()
        assigned_roles = assignments.roles if assignments else []
        permissions = []
        if assigned_roles:
            roles_in_db = db.query(ServiceRole).filter(
                ServiceRole.service_id == service_id,
                ServiceRole.code.in_(assigned_roles)
            ).all()
            for r in roles_in_db:
                permissions.extend(r.permissions or [])

        access_token = AuthService._create_token(
            sub=user_id,
            token_use="service_access",
            aud=f"service:{service_id}",
            expiry=timedelta(seconds=300),
            custom_claims={
                "session_id": session_id,
                "parent_session_id": parent_sid,
                "institution_id": institution_id,
                "service_id": service_id,
                "profile": profile,
                "roles": assigned_roles,
                "permissions": list(set(permissions))
            }
        )

        new_refresh = AuthService._create_token(
            sub=user_id,
            token_use="service_refresh",
            aud=f"service:{service_id}",
            expiry=timedelta(hours=24),
            custom_claims={
                "session_id": session_id,
                "parent_session_id": parent_sid,
                "institution_id": institution_id,
                "service_id": service_id,
                "profile": profile,
                "jti": new_jti
            }
        )

        return {
            "access_token": access_token,
            "refresh_token": new_refresh,
            "token_type": "Bearer",
            "expires_in": 300,
            "refresh_expires_in": 86400,
            "session_id": session_id,
            "parent_session_id": parent_sid,
            "institution_id": institution_id,
            "service_id": service_id,
            "profile": profile
        }


    @staticmethod
    def revoke_service_session(session_id: str, core_session: CoreSession, user: User, db: Session):
        """Отзыв дочерней сервисной сессии пользователем."""
        sess = db.query(ServiceSession).filter(
            ServiceSession.id == session_id,
            ServiceSession.user_id == user.id,
            ServiceSession.parent_session_id == core_session.id
        ).first()

        if not sess:
            raise HTTPException(status_code=404, detail="RESOURCE_NOT_FOUND: Service session not found")

        sess.is_revoked = True
        db.commit()


