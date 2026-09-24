import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import HTTPException, status
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
from settings.redis_client import revoked_tokens_redis
from settings.security import hash_password, verify_password, security
import hashlib
import hmac
import json
import time
import urllib.parse
from fastapi import HTTPException
from settings.config import settings

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
                    demo_inst = db.query(Institution).first()
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
            bot_token = getattr(settings, "MAX_BOT_TOKEN", "test_bot_token_123")
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

        # 2. Проверяем сервис
        service = db.query(ServiceInstance).filter(
            ServiceInstance.id == service_id,
            ServiceInstance.institution_id == institution_id
        ).first()

        if not service:
            raise HTTPException(status_code=404, detail="Service not found in this institution")

        if not service.enabled:
            raise HTTPException(status_code=409, detail="Service is currently disabled")

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
            raise HTTPException(status_code=409, detail="Institution is pending or suspended")

        scopes = [
            "manifest:write",
            "roles:read",
            "roles:write",
            "assignments:read",
            "assignments:write",
            "profiles:read",
            "tokens:introspect",
            "institution:manage"
        ]

        machine_jwt = AuthService._create_token(
            sub=client_id,
            token_use="machine_access",
            aud="core-internal",
            expiry=timedelta(seconds=300),
            custom_claims={
                "institution_id": service.institution_id,
                "service_id": service.id,
                "scopes": scopes
            }
        )

        return {
            "access_token": machine_jwt,
            "token_type": "Bearer",
            "expires_in": 300,
            "institution_id": service.institution_id,
            "service_id": service.id,
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
        if not service_session or service_session.is_revoked:
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
    # =========================================================================
    # SERVICE RBAC & MANIFESTS
    # =========================================================================

    @staticmethod
    def _verify_service_ownership(service_id: str, machine_claims: dict):
        token_service_id = getattr(machine_claims, "service_id", None) or machine_claims.get("service_id")
        if token_service_id != service_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Cannot access other service instance"
            )

    @staticmethod
    def get_service_manifest(service_id: str, machine_claims: dict, db: Session):
        AuthService._verify_service_ownership(service_id, machine_claims)
        service = db.query(ServiceInstance).filter(ServiceInstance.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")
        return service.manifest or {"titles": {"ru": service.service_type}, "menus": []}

    @staticmethod
    def replace_service_manifest(service_id: str, manifest_data, machine_claims: dict, db: Session):
        AuthService._verify_service_ownership(service_id, machine_claims)
        service = db.query(ServiceInstance).filter(ServiceInstance.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")

        service.manifest = manifest_data.model_dump()
        db.commit()
        return service.manifest

    @staticmethod
    def get_service_user_profiles(service_id: str, user_id: str, machine_claims: dict, db: Session):
        AuthService._verify_service_ownership(service_id, machine_claims)
        service = db.query(ServiceInstance).filter(ServiceInstance.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")

        membership = db.query(Membership).filter(
            Membership.institution_id == service.institution_id,
            Membership.user_id == user_id
        ).first()

        if not membership:
            raise HTTPException(status_code=404, detail="User is not a member of this institution")

        return {
            "user_id": user_id,
            "institution_id": service.institution_id,
            "profiles": membership.profiles or []
        }

    @staticmethod
    def list_service_roles(service_id: str, machine_claims: dict, db: Session):
        AuthService._verify_service_ownership(service_id, machine_claims)
        roles = db.query(ServiceRole).filter(ServiceRole.service_id == service_id).all()
        items = [
            {
                "service_id": r.service_id,
                "code": r.code,
                "titles": r.titles,
                "allowed_profiles": r.allowed_profiles or [],
                "permissions": r.permissions or [],
                "system": r.system
            }
            for r in roles
        ]
        return {"items": items, "next_cursor": None}

    @staticmethod
    def create_service_role(service_id: str, role_data, machine_claims: dict, db: Session):
        AuthService._verify_service_ownership(service_id, machine_claims)
        service = db.query(ServiceInstance).filter(ServiceInstance.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")

        if service.protected:
            raise HTTPException(status_code=403, detail="Forbidden: Administration role definitions are protected")

        existing = db.query(ServiceRole).filter(
            ServiceRole.service_id == service_id,
            ServiceRole.code == role_data.code
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Role '{role_data.code}' already exists")

        new_role = ServiceRole(
            service_id=service_id,
            code=role_data.code,
            titles=role_data.titles.model_dump(),
            allowed_profiles=role_data.allowed_profiles,
            permissions=role_data.permissions,
            system=False
        )
        db.add(new_role)
        db.commit()

        return {
            "service_id": new_role.service_id,
            "code": new_role.code,
            "titles": new_role.titles,
            "allowed_profiles": new_role.allowed_profiles,
            "permissions": new_role.permissions,
            "system": new_role.system
        }

    @staticmethod
    def get_service_role(service_id: str, role_code: str, machine_claims: dict, db: Session):
        AuthService._verify_service_ownership(service_id, machine_claims)
        role = db.query(ServiceRole).filter(
            ServiceRole.service_id == service_id,
            ServiceRole.code == role_code
        ).first()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        return {
            "service_id": role.service_id,
            "code": role.code,
            "titles": role.titles,
            "allowed_profiles": role.allowed_profiles,
            "permissions": role.permissions,
            "system": role.system
        }

    @staticmethod
    def update_service_role(service_id: str, role_code: str, patch_data, machine_claims: dict, db: Session):
        AuthService._verify_service_ownership(service_id, machine_claims)
        role = db.query(ServiceRole).filter(
            ServiceRole.service_id == service_id,
            ServiceRole.code == role_code
        ).first()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        if role.system:
            raise HTTPException(status_code=403, detail="Forbidden: System roles cannot be modified")

        if patch_data.titles is not None:
            role.titles = patch_data.titles.model_dump()
        if patch_data.allowed_profiles is not None:
            role.allowed_profiles = patch_data.allowed_profiles
        if patch_data.permissions is not None:
            role.permissions = patch_data.permissions

        db.commit()
        return {
            "service_id": role.service_id,
            "code": role.code,
            "titles": role.titles,
            "allowed_profiles": role.allowed_profiles,
            "permissions": role.permissions,
            "system": role.system
        }

    @staticmethod
    def delete_service_role(service_id: str, role_code: str, machine_claims: dict, db: Session):
        AuthService._verify_service_ownership(service_id, machine_claims)
        role = db.query(ServiceRole).filter(
            ServiceRole.service_id == service_id,
            ServiceRole.code == role_code
        ).first()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        if role.system:
            raise HTTPException(status_code=403, detail="Forbidden: System roles cannot be deleted")

        # Проверка: есть ли пользователи с этой ролью (ROLE_IN_USE -> 409)
        all_assignments = db.query(RoleAssignment).filter(RoleAssignment.service_id == service_id).all()
        for a in all_assignments:
            if role_code in (a.roles or []):
                raise HTTPException(status_code=409, detail="Role is currently assigned to users (ROLE_IN_USE)")

        db.delete(role)
        db.commit()

    @staticmethod
    def get_service_assignments(service_id: str, user_id: str, profile: str, machine_claims: dict, db: Session):
        AuthService._verify_service_ownership(service_id, machine_claims)
        service = db.query(ServiceInstance).filter(ServiceInstance.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")

        assignment = db.query(RoleAssignment).filter(
            RoleAssignment.service_id == service_id,
            RoleAssignment.user_id == user_id,
            RoleAssignment.profile == profile.lower()
        ).first()

        assigned_roles = assignment.roles if assignment else []
        permissions: List[str] = []
        if assigned_roles:
            roles_in_db = db.query(ServiceRole).filter(
                ServiceRole.service_id == service_id,
                ServiceRole.code.in_(assigned_roles)
            ).all()
            for r in roles_in_db:
                permissions.extend(r.permissions or [])

        return {
            "institution_id": service.institution_id,
            "service_id": service_id,
            "user_id": user_id,
            "profile": profile.lower(),
            "roles": assigned_roles,
            "permissions": list(set(permissions))
        }

    @staticmethod
    def replace_service_assignments(
        service_id: str,
        user_id: str,
        profile: str,
        assignment_data,
        machine_claims: dict,
        db: Session
    ):
        AuthService._verify_service_ownership(service_id, machine_claims)
        profile_norm = profile.lower()
        service = db.query(ServiceInstance).filter(ServiceInstance.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")

        # 1. Проверяем, что пользователь состоит в этом ВУЗе и имеет данный профиль
        membership = db.query(Membership).filter(
            Membership.institution_id == service.institution_id,
            Membership.user_id == user_id
        ).first()
        if not membership or profile_norm not in [p.lower() for p in (membership.profiles or [])]:
            raise HTTPException(status_code=404, detail="Target user or profile does not exist in institution")

        # 2. Валидируем роли
        target_roles = assignment_data.roles or []
        if target_roles:
            existing_roles = db.query(ServiceRole).filter(
                ServiceRole.service_id == service_id,
                ServiceRole.code.in_(target_roles)
            ).all()
            existing_codes = {r.code: r for r in existing_roles}

            for role_code in target_roles:
                if role_code not in existing_codes:
                    raise HTTPException(status_code=400, detail=f"Role '{role_code}' does not exist in this service")
                # Проверяем, разрешен ли профиль пользователя для этой роли
                role_obj = existing_codes[role_code]
                if profile_norm not in [p.lower() for p in (role_obj.allowed_profiles or [])]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Role '{role_code}' is not allowed for profile '{profile_norm}'"
                    )

        # 3. Сохраняем назначения
        assignment = db.query(RoleAssignment).filter(
            RoleAssignment.service_id == service_id,
            RoleAssignment.user_id == user_id,
            RoleAssignment.profile == profile_norm
        ).first()

        if not assignment:
            assignment = RoleAssignment(
                service_id=service_id,
                user_id=user_id,
                profile=profile_norm,
                roles=target_roles
            )
            db.add(assignment)
        else:
            assignment.roles = target_roles

        db.commit()

        # 4. Вычисляем итоговые права
        permissions: List[str] = []
        if target_roles:
            roles_in_db = db.query(ServiceRole).filter(
                ServiceRole.service_id == service_id,
                ServiceRole.code.in_(target_roles)
            ).all()
            for r in roles_in_db:
                permissions.extend(r.permissions or [])

        return {
            "institution_id": service.institution_id,
            "service_id": service_id,
            "user_id": user_id,
            "profile": profile_norm,
            "roles": target_roles,
            "permissions": list(set(permissions))
        }
    # =========================================================================
    # PRIVATE ADMINISTRATION (/api/v1/institution/{institution_id}/internal/*)
    # =========================================================================

    @staticmethod
    def get_institution_settings(institution_id: str, db: Session):
        inst = db.query(Institution).filter(Institution.id == institution_id).first()
        if not inst:
            raise HTTPException(status_code=404, detail="Institution not found")
        return {
            "id": inst.id,
            "titles": inst.titles,
            "default_locale": inst.default_locale,
            "status": inst.status
        }

    @staticmethod
    def update_institution_settings(institution_id: str, patch_data, db: Session):
        inst = db.query(Institution).filter(Institution.id == institution_id).first()
        if not inst:
            raise HTTPException(status_code=404, detail="Institution not found")

        if patch_data.titles is not None:
            inst.titles = patch_data.titles.model_dump()
        if patch_data.default_locale is not None:
            inst.default_locale = patch_data.default_locale

        db.commit()
        return {
            "id": inst.id,
            "titles": inst.titles,
            "default_locale": inst.default_locale,
            "status": inst.status
        }

    @staticmethod
    def list_service_types():
        """4 фиксированных типа MVP по контракту ServiceTypeList."""
        return {
            "items": [
                {
                    "code": "administration",
                    "deployment": "cloud",
                    "titles": {"ru": "Администрирование", "en": "Administration"},
                    "supported_profiles": ["admin"],
                    "permission_codes": ["institution.read", "institution.update", "members.manage", "services.manage"],
                    "protected": True
                },
                {
                    "code": "schedule",
                    "deployment": "cloud",
                    "titles": {"ru": "Расписание", "en": "Schedule"},
                    "supported_profiles": ["student", "teacher", "admin"],
                    "permission_codes": ["schedule.read", "schedule.write"],
                    "protected": False
                },
                {
                    "code": "user-profile",
                    "deployment": "cloud",
                    "titles": {"ru": "Профиль пользователя", "en": "User Profile"},
                    "supported_profiles": ["student", "teacher", "admin"],
                    "permission_codes": ["profile.read", "profile.write"],
                    "protected": False
                },
                {
                    "code": "coursework",
                    "deployment": "local",
                    "titles": {"ru": "Курсовые работы", "en": "Coursework"},
                    "supported_profiles": ["student", "teacher"],
                    "permission_codes": ["coursework.upload", "coursework.grade"],
                    "protected": False
                }
            ]
        }

    @staticmethod
    def list_institution_members(institution_id: str, db: Session):
        memberships = db.query(Membership).filter(Membership.institution_id == institution_id).all()
        items = [
            {
                "institution_id": m.institution_id,
                "user_id": m.user_id,
                "profiles": m.profiles or [],
                "created_at": m.created_at.isoformat()
            }
            for m in memberships
        ]
        return {"items": items, "next_cursor": None}

    @staticmethod
    def add_institution_member(institution_id: str, member_data, db: Session):
        target_user = db.query(User).filter(User.id == member_data.user_id).first()
        if not target_user:
            raise HTTPException(status_code=404, detail="Target user not found")

        existing = db.query(Membership).filter(
            Membership.institution_id == institution_id,
            Membership.user_id == member_data.user_id
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail="User is already a member (MEMBERSHIP_ALREADY_EXISTS)")

        valid_profiles = ["admin", "teacher", "student"]
        normalized_profiles = [p.lower() for p in member_data.profiles]
        for p in normalized_profiles:
            if p not in valid_profiles:
                raise HTTPException(status_code=400, detail=f"Invalid profile '{p}'")

        new_membership = Membership(
            institution_id=institution_id,
            user_id=member_data.user_id,
            profiles=normalized_profiles
        )
        db.add(new_membership)
        db.commit()

        return {
            "institution_id": new_membership.institution_id,
            "user_id": new_membership.user_id,
            "profiles": new_membership.profiles,
            "created_at": new_membership.created_at.isoformat()
        }

    @staticmethod
    def get_institution_member(institution_id: str, user_id: str, db: Session):
        m = db.query(Membership).filter(
            Membership.institution_id == institution_id,
            Membership.user_id == user_id
        ).first()
        if not m:
            raise HTTPException(status_code=404, detail="Member not found")
        return {
            "institution_id": m.institution_id,
            "user_id": m.user_id,
            "profiles": m.profiles,
            "created_at": m.created_at.isoformat()
        }

    @staticmethod
    def replace_member_profiles(institution_id: str, user_id: str, profiles_data, db: Session):
        m = db.query(Membership).filter(
            Membership.institution_id == institution_id,
            Membership.user_id == user_id
        ).first()
        if not m:
            raise HTTPException(status_code=404, detail="Member not found")

        valid_profiles = ["admin", "teacher", "student"]
        normalized_profiles = [p.lower() for p in profiles_data.profiles]
        for p in normalized_profiles:
            if p not in valid_profiles:
                raise HTTPException(status_code=400, detail=f"Invalid profile '{p}'")

        m.profiles = normalized_profiles
        db.commit()
        return {
            "institution_id": m.institution_id,
            "user_id": m.user_id,
            "profiles": m.profiles,
            "created_at": m.created_at.isoformat()
        }

    @staticmethod
    def remove_institution_member(institution_id: str, user_id: str, db: Session):
        m = db.query(Membership).filter(
            Membership.institution_id == institution_id,
            Membership.user_id == user_id
        ).first()
        if not m:
            raise HTTPException(status_code=404, detail="Member not found")

        # Удаляем членство и все связанные назначения ролей в этом ВУЗе
        inst_services = db.query(ServiceInstance.id).filter(ServiceInstance.institution_id == institution_id).all()
        srv_ids = [s[0] for s in inst_services]
        if srv_ids:
            db.query(RoleAssignment).filter(
                RoleAssignment.service_id.in_(srv_ids),
                RoleAssignment.user_id == user_id
            ).delete(synchronize_session=False)

        db.delete(m)
        db.commit()

    @staticmethod
    def list_admin_services(institution_id: str, db: Session):
        services = db.query(ServiceInstance).filter(ServiceInstance.institution_id == institution_id).all()
        items = [
            {
                "id": s.id,
                "institution_id": s.institution_id,
                "service_type": s.service_type,
                "deployment": s.deployment,
                "enabled": s.enabled,
                "protected": s.protected,
                "api_base_url": s.api_base_url,
                "client_base_url": s.client_base_url,
                "supported_profiles": s.supported_profiles or [],
                "manifest": s.manifest or {},
                "created_at": s.created_at.isoformat()
            }
            for s in services
        ]
        return {"items": items, "next_cursor": None}

    @staticmethod
    def install_service_instance(institution_id: str, service_data, db: Session):
        # Проверка: один тип на вуз
        existing = db.query(ServiceInstance).filter(
            ServiceInstance.institution_id == institution_id,
            ServiceInstance.service_type == service_data.service_type
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail="Service of this type already exists in institution")

        is_local = service_data.deployment == "local"
        new_service = ServiceInstance(
            id=generate_uuid(),
            institution_id=institution_id,
            service_type=service_data.service_type,
            deployment=service_data.deployment,
            enabled=not is_local,  # local создается выключенным до настройки manifest
            protected=False,
            api_base_url=service_data.api_base_url or "https://service.platform.example/api/v1",
            client_base_url=service_data.client_base_url or "https://service.platform.example",
            supported_profiles=["student", "teacher"],
            manifest={"titles": {"ru": service_data.service_type}, "menus": []}
        )
        db.add(new_service)
        db.commit()
        db.refresh(new_service)

        return {
            "id": new_service.id,
            "institution_id": new_service.institution_id,
            "service_type": new_service.service_type,
            "deployment": new_service.deployment,
            "enabled": new_service.enabled,
            "protected": new_service.protected,
            "api_base_url": new_service.api_base_url,
            "client_base_url": new_service.client_base_url,
            "supported_profiles": new_service.supported_profiles,
            "manifest": new_service.manifest,
            "created_at": new_service.created_at.isoformat()
        }

    @staticmethod
    def issue_local_credential(institution_id: str, service_id: str, db: Session):
        from database.tables import ServiceCredential
        import secrets

        service = db.query(ServiceInstance).filter(
            ServiceInstance.id == service_id,
            ServiceInstance.institution_id == institution_id
        ).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")

        # Не более 2 активных credentials
        active_count = db.query(ServiceCredential).filter(
            ServiceCredential.service_id == service_id,
            ServiceCredential.revoked_at.is_(None)
        ).count()
        if active_count >= 2:
            raise HTTPException(status_code=409, detail="Max 2 active credentials allowed (CREDENTIAL_LIMIT)")

        raw_secret = secrets.token_urlsafe(32)
        new_cred = ServiceCredential(
            id=generate_uuid(),
            client_id=generate_uuid(),
            service_id=service_id,
            hashed_secret=hash_password(raw_secret)
        )
        db.add(new_cred)
        db.commit()
        db.refresh(new_cred)

        return {
            "credential": {
                "id": new_cred.id,
                "client_id": new_cred.client_id,
                "service_id": new_cred.service_id,
                "created_at": new_cred.created_at.isoformat(),
                "revoked_at": None
            },
            "client_secret": raw_secret
        }
    @staticmethod
    def uninstall_service_instance(institution_id: str, service_id: str, db: Session):
        service = db.query(ServiceInstance).filter(
            ServiceInstance.id == service_id,
            ServiceInstance.institution_id == institution_id
        ).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")
        if service.protected:
            raise HTTPException(status_code=403, detail="Forbidden: Protected administration service cannot be uninstalled")

        db.delete(service)
        db.commit()
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

### NEED TO CHECK HMAC

def validate_max_init_data(init_data_raw: str, bot_token: str) -> dict:
    """
    Валидирует WebApp.initData мессенджера Макс по HMAC-SHA256.
    Проверяет целостность данных и свежесть auth_date (до 300 секунд).
    """
    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data_raw, keep_blank_values=True))
    except Exception:
        raise HTTPException(status_code=400, detail="INVALID_INIT_DATA: Malformed query string")

    received_hash = parsed_data.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="INVALID_INIT_DATA: Hash missing")

    # 1. Проверка времени жизни auth_date (до 300 с, skew 30 с)
    auth_date = int(parsed_data.get("auth_date", 0))
    now = int(time.time())
    if auth_date == 0 or (now - auth_date) > 300 or (auth_date - now) > 30:
        raise HTTPException(status_code=401, detail="INVALID_INIT_DATA: auth_date expired or invalid")

    # 2. Формирование data_check_string (ключи по алфавиту через \n)
    sorted_items = sorted(parsed_data.items())
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted_items)

    # 3. Вычисление HMAC-SHA256
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(status_code=401, detail="INVALID_INIT_DATA: HMAC mismatch")

    # 4. Извлечение пользователя
    user_raw = parsed_data.get("user")
    if not user_raw:
        raise HTTPException(status_code=400, detail="INVALID_INIT_DATA: user payload missing")

    return json.loads(user_raw)