from datetime import datetime, timezone
from uuid import UUID, uuid4
import re

import jwt
from passlib.hash import argon2
from settings.config import settings
from auth.keys import KeyRing

SKEW = 30
TTL = {"core_access": 600, "core_refresh": 604800, "service_access": 300,
       "service_refresh": 86400, "machine_access": 300}
AUDIENCES = {"core_access": "core-api", "core_refresh": "core-refresh",
             "service_refresh": "core-service-refresh", "machine_access": "core-internal"}
COMMON = {"iss", "sub", "aud", "iat", "nbf", "exp", "jti", "token_use"}
EXTRA = {"core_access": {"sid"}, "core_refresh": {"sid", "family_id"},
         "service_access": {"sid", "parent_sid", "institution_id", "service_id", "profile", "roles", "permissions"},
         "service_refresh": {"sid", "parent_sid", "family_id", "institution_id", "service_id", "profile"},
         "machine_access": {"credential_id", "institution_id", "service_id", "scopes"}}


def hash_password(password):
    return argon2.hash(password)


def verify_password(password, hashed):
    try:
        return argon2.verify(password, hashed)
    except (TypeError, ValueError):
        return False


def timestamp(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def is_uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


class TokenPayloadDict(dict):
    def __getattr__(self, name):
        return self.get(name)


class SecurityManager:
    @property
    def keys(self):
        return KeyRing(settings.JWT_KEYRING_PATH)

    def create_token(self, uid, token_use, expires_at, custom_claims=None):
        now = int(datetime.now(timezone.utc).timestamp())
        payload = dict(custom_claims or {})
        audience = f"service:{payload.get('service_id')}" if token_use == "service_access" else AUDIENCES[token_use]
        payload.update(iss=settings.JWT_ISSUER, sub=str(uid), aud=audience, iat=now, nbf=now,
                       exp=min(timestamp(expires_at), now + TTL[token_use]), token_use=token_use)
        payload.setdefault("jti", str(uuid4()))
        self._validate(payload, token_use)
        purpose = "refresh" if token_use.endswith("_refresh") else "access"
        return self.keys.sign(payload, purpose)

    @staticmethod
    def _validate(payload, token_use):
        if token_use not in EXTRA or not (COMMON | EXTRA[token_use]) <= payload.keys():
            raise jwt.InvalidTokenError("Missing claims")
        if payload["token_use"] != token_use:
            raise jwt.InvalidTokenError("Wrong token use")
        for name in ("sub", "jti", "sid", "parent_sid", "family_id", "institution_id", "service_id", "credential_id"):
            if name in payload and not is_uuid(payload[name]):
                raise jwt.InvalidTokenError("Invalid identifier")
        if any(type(payload[k]) is not int for k in ("iat", "nbf", "exp")):
            raise jwt.InvalidTokenError("Invalid timestamp")
        if not payload["iat"] <= payload["nbf"] < payload["exp"] or payload["exp"] - payload["iat"] > TTL[token_use]:
            raise jwt.InvalidTokenError("Invalid lifetime")
        if "profile" in payload and payload["profile"] not in ("admin", "teacher", "student"):
            raise jwt.InvalidTokenError("Invalid profile")
        for name, limit in (("roles", 32), ("permissions", 128), ("scopes", 8)):
            if name in payload:
                values = payload[name]
                if not isinstance(values, list) or len(values) > limit or any(not isinstance(v, str) for v in values):
                    raise jwt.InvalidTokenError("Invalid permissions")
                if len(set(values)) != len(values):
                    raise jwt.InvalidTokenError("Duplicate permissions")
                pattern = r"[a-z][a-z0-9_.:-]{0,63}" if name == "scopes" else r"[a-z][a-z0-9_.-]{0,63}"
                if any(not re.fullmatch(pattern, v) for v in values):
                    raise jwt.InvalidTokenError("Invalid permission code")
                if name == 'scopes' and not set(values) <= {'manifest:write', 'roles:read', 'roles:write',
                        'assignments:read', 'assignments:write', 'profiles:read', 'groups:read', 'tokens:introspect',
                        'institution:manage'}:
                    raise jwt.InvalidTokenError('Unknown scope')

    def decode(self, token, token_use, service_id=None):
        if not isinstance(token, str) or not 1 <= len(token) <= 16384 or token_use not in EXTRA:
            raise jwt.InvalidTokenError("Invalid token")
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or header.get("typ") != "JWT" or not is_uuid(header.get("kid")):
            raise jwt.InvalidTokenError("Invalid JWT header")
        purpose = "refresh" if token_use.endswith("_refresh") else "access"
        key = self.keys.verification_key(header["kid"], purpose)
        if token_use == "service_access":
            if not is_uuid(service_id):
                raise jwt.InvalidTokenError("Expected service audience is required")
            audience = f"service:{service_id}"
        else:
            audience = AUDIENCES[token_use]
        payload = jwt.decode(token, key, algorithms=["RS256"], issuer=settings.JWT_ISSUER,
                             audience=audience, leeway=SKEW,
                             options={"require": sorted(COMMON | EXTRA[token_use]), "strict_aud": True})
        self._validate(payload, token_use)
        return TokenPayloadDict(payload)


security = SecurityManager()
