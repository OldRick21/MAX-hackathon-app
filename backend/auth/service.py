from datetime import timedelta, datetime, timezone
import hashlib
import hmac
import time
import jwt
from platform_core.errors import DomainError, validation
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from database.tables import (User, Institution, Membership, ServiceInstance, CoreSession,
                             ServiceSession, ServiceCredential, ProfileEnum, utc_now, generate_uuid)
from auth.models import RefreshUse
from auth.security import hash_password, verify_password, security, timestamp
from auth.state import alive, lock_core, revoke_core, core_state, service_state, machine_scopes
from auth.max_validation import validate_max_init_data
from settings.config import settings
from platform_core import registry


def invalid_refresh():
    return DomainError(401, 'INVALID_REFRESH_TOKEN', 'Refresh token недействителен, истёк или уже использован')


def decode_refresh(token, kind):
    try:
        return security.decode(token, kind)
    except jwt.PyJWTError:
        raise invalid_refresh()


def digest(token):
    return hashlib.sha256(token.encode('utf8')).hexdigest()


def max_user(db, max_id):
    user = db.query(User).filter_by(max_user_id=max_id).first()
    if user:
        return user
    try:
        with db.begin_nested():
            user = User(max_user_id=max_id)
            db.add(user)
            db.flush()
        return user
    except IntegrityError:
        # The unique MAX id arbitrates concurrent first logins.
        user = db.query(User).filter_by(max_user_id=max_id).first()
        if not user:
            raise
        return user


def known_refresh(db, token, claims, session):
    row = db.get(RefreshUse, claims['jti'], populate_existing=True)
    if (not row or row.family_id != session.family_id or row.session_id != session.id
            or row.token_use != claims['token_use'] or not hmac.compare_digest(row.token_hash, digest(token))):
        return None
    return row


def pair(db, session, parent=None):
    child = parent is not None
    kind = 'service' if child else 'core'
    deadline = timestamp(session.expires_at)
    if child:
        deadline = min(deadline, timestamp(parent.expires_at))
    expires_at = datetime.fromtimestamp(deadline, timezone.utc)
    common = {'sid': session.id}
    response = {'session_id': session.id, 'token_type': 'Bearer'}
    access = dict(common)
    if child:
        context = {'institution_id': session.institution_id, 'service_id': session.service_id, 'profile': session.profile}
        common.update(context, parent_sid=parent.id)
        access = dict(common)
        roles = registry.assigned_roles(db, session.service_id, session.user_id, session.profile)
        access.update(roles=roles, permissions=registry.permissions_for(db, session.service_id, roles, session.profile))
        response.update(context, parent_session_id=parent.id)
    session.current_refresh_jti = generate_uuid()
    refresh = dict(common, family_id=session.family_id, jti=session.current_refresh_jti)
    access_token = security.create_token(session.user_id, kind + '_access', expires_at, access)
    refresh_token = security.create_token(session.user_id, kind + '_refresh', expires_at, refresh)
    # Locally issued tokens: decode only to report their exact remaining lifetime.
    access_claims = jwt.decode(access_token, options={'verify_signature': False})
    refresh_claims = jwt.decode(refresh_token, options={'verify_signature': False})
    now = timestamp(utc_now())
    response.update(access_token=access_token, refresh_token=refresh_token,
                    expires_in=max(0, access_claims['exp'] - now),
                    refresh_expires_in=max(0, refresh_claims['exp'] - now))
    db.add(RefreshUse(jti=session.current_refresh_jti, family_id=session.family_id, session_id=session.id,
                      token_use=kind + '_refresh', token_hash=digest(refresh_token),
                      expires_at=expires_at + timedelta(seconds=30)))
    return response


class AuthService:
    @staticmethod
    def login_or_register(auth_data, db: Session):
        import random
        user = None

        if not settings.ALLOW_DEV_LOGIN:
            # Контракт: единственное поле initData (1..16384); dev-поля вне режима разработки — 422.
            dev = sorted(k for k in ("username", "password", "max_user_id") if getattr(auth_data, k) is not None)
            if dev:
                raise validation(f"Неизвестные поля: {', '.join(dev)}", dev[0])
            if auth_data.initData is None:
                raise validation("Нужен initData", "initData")

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
                db.flush()
            elif auth_data.password and not verify_password(auth_data.password, user.hashed_password):
                raise DomainError(401, "UNAUTHENTICATED", "Неверный логин или пароль")

        elif auth_data.max_user_id:
            user = max_user(db, auth_data.max_user_id)

        elif auth_data.initData:
            bot_token = settings.MAX_BOT_TOKEN
            if not bot_token:
                raise DomainError(503, "SERVICE_UNAVAILABLE", "Вход через MAX не настроен")
            user_payload = validate_max_init_data(auth_data.initData, bot_token)
            max_id = str(user_payload.get("id"))
            
            user = max_user(db, max_id)

        else:
            raise validation("Нужен initData", "initData")

        now = utc_now().replace(microsecond=0)
        session = CoreSession(id=generate_uuid(), user_id=user.id, family_id=generate_uuid(),
                              current_refresh_jti=generate_uuid(), expires_at=now + timedelta(days=7))
        db.add(session)
        db.flush()
        result = pair(db, session)
        db.commit()
        return result


    @staticmethod
    def refresh_core_session(refresh_token_str, db):
        claims = decode_refresh(refresh_token_str, 'core_refresh')
        lock_core(db, claims['sid'])
        state = core_state(db, claims)
        if not state:
            raise invalid_refresh()
        _, session = state
        row = known_refresh(db, refresh_token_str, claims, session)
        if not row:
            raise invalid_refresh()
        if row.used_at is not None or session.current_refresh_jti != claims['jti']:
            revoke_core(db, session)
            db.commit()
            raise invalid_refresh()
        row.used_at = utc_now()
        result = pair(db, session)
        db.commit()
        return result

    @staticmethod
    def logout_core_session(refresh_token_str, db):
        try:
            claims = security.decode(refresh_token_str, 'core_refresh')
        except jwt.PyJWTError:
            return
        lock_core(db, claims['sid'])
        state = core_state(db, claims)
        if state and known_refresh(db, refresh_token_str, claims, state[1]):
            revoke_core(db, state[1])
        db.commit()

    @staticmethod
    def create_service_session(user, core_session, institution_id, service_id, profile, db):
        parent = lock_core(db, core_session.id)
        if not alive(parent) or parent.user_id != user.id:
            raise DomainError(401, 'UNAUTHENTICATED', 'Сессия завершена')
        institution = registry.lock_institution(db, institution_id)
        member = db.get(Membership, (institution_id, user.id), populate_existing=True)
        if not institution or not member:
            raise DomainError(404, 'RESOURCE_NOT_FOUND', 'Вуз не найден')
        if profile not in (member.profiles or []):
            raise DomainError(403, 'FORBIDDEN', 'У вас нет этого профиля в вузе')
        db.refresh(institution)
        if institution.status != 'active':
            raise DomainError(409, 'RESOURCE_INACTIVE', 'Вуз не активен')
        service = db.get(ServiceInstance, service_id, populate_existing=True)
        if not service or service.institution_id != institution_id or profile not in service.supported_profiles:
            raise DomainError(404, 'RESOURCE_NOT_FOUND', 'Сервис не найден для этого профиля')
        if not service.enabled:
            raise DomainError(409, 'RESOURCE_INACTIVE', 'Сервис отключён')
        deadline = min(timestamp(utc_now()) + 86400, timestamp(parent.expires_at))
        session = ServiceSession(id=generate_uuid(), parent_session_id=parent.id, user_id=user.id,
                                 institution_id=institution_id, service_id=service_id, profile=profile,
                                 family_id=generate_uuid(), current_refresh_jti=generate_uuid(),
                                 expires_at=datetime.fromtimestamp(deadline, timezone.utc))
        db.add(session)
        db.flush()
        result = pair(db, session, parent)
        db.commit()
        return result

    @staticmethod
    def refresh_service_session(service_id, institution_id, refresh_token_str, db):
        claims = decode_refresh(refresh_token_str, 'service_refresh')
        if claims['service_id'] != service_id or claims['institution_id'] != institution_id:
            raise invalid_refresh()
        lock_core(db, claims['parent_sid'])
        registry.lock_institution(db, institution_id)
        state = service_state(db, claims)
        if not state:
            raise invalid_refresh()
        session, parent = state
        row = known_refresh(db, refresh_token_str, claims, session)
        if not row:
            raise invalid_refresh()
        if row.used_at is not None or session.current_refresh_jti != claims['jti']:
            session.is_revoked = True
            db.commit()
            raise invalid_refresh()
        row.used_at = utc_now()
        result = pair(db, session, parent)
        db.commit()
        return result

    @staticmethod
    def revoke_service_session(session_id, core_session, user, db, institution_id, service_id):
        parent = lock_core(db, core_session.id)
        if not alive(parent) or parent.user_id != user.id:
            raise DomainError(401, 'UNAUTHENTICATED', 'Сессия завершена')
        session = db.get(ServiceSession, session_id, populate_existing=True)
        if (not session or session.user_id != user.id or session.parent_session_id != parent.id
                or session.institution_id != institution_id or session.service_id != service_id):
            raise DomainError(404, 'RESOURCE_NOT_FOUND', 'Сессия сервиса не найдена')
        session.is_revoked = True
        db.commit()

    @staticmethod
    def issue_machine_token(credentials, req_body, db):
        failure = DomainError(401, 'INVALID_CLIENT', 'Неверный ключ сервиса', headers={'WWW-Authenticate': 'Basic realm="core-service"'})
        if not credentials:
            raise failure
        cred = db.query(ServiceCredential).filter_by(client_id=credentials.username).first()
        if not cred or cred.revoked_at is not None or not verify_password(credentials.password, cred.hashed_secret):
            raise failure
        service = db.get(ServiceInstance, cred.service_id)
        if not service:
            raise failure
        institution = registry.lock_institution(db, service.institution_id)
        db.refresh(cred)
        db.refresh(service)
        db.refresh(institution)
        if cred.revoked_at is not None:
            raise failure
        if institution.status != 'active':
            raise DomainError(409, 'RESOURCE_INACTIVE', 'Вуз не активен')
        scopes = machine_scopes(service)
        token = security.create_token(cred.client_id, 'machine_access', utc_now() + timedelta(seconds=300),
                                      {'institution_id': service.institution_id, 'service_id': service.id,
                                       'credential_id': cred.id, 'scopes': scopes})
        result = {'access_token': token, 'token_type': 'Bearer', 'expires_in': 300,
                  'institution_id': service.institution_id, 'service_id': service.id, 'scopes': scopes}
        db.commit()
        return result

    @staticmethod
    def get_public_jwks():
        return security.keys.jwks()

    @staticmethod
    def introspect_service_token(token_str, machine_claims, db):
        try:
            claims = security.decode(token_str, 'service_access', machine_claims['service_id'])
        except jwt.PyJWTError:
            return {'active': False}
        if claims['institution_id'] != machine_claims['institution_id'] or not service_state(db, claims):
            return {'active': False}
        roles = registry.assigned_roles(db, claims['service_id'], claims['sub'], claims['profile'])
        return {'active': True, 'sub': claims['sub'], 'aud': f"service:{claims['service_id']}", 'session_id': claims['sid'],
                'parent_session_id': claims['parent_sid'], 'institution_id': claims['institution_id'],
                'service_id': claims['service_id'], 'profile': claims['profile'], 'exp': claims['exp'],
                'roles': roles, 'permissions': registry.permissions_for(db, claims['service_id'], roles, claims['profile']),
                # Учебные группы пользователя в вузе: сервису не нужен отдельный запрос за своей группой.
                'group_ids': registry.user_group_ids(db, claims['institution_id'], claims['sub'])}
