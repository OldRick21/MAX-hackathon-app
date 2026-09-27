from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
import jwt
from passlib.hash import argon2
from settings.config import settings


def hash_password(password: str) -> str:
    return argon2.hash(password)

def verify_password(inp_pass: str, hash_pass: str) -> bool:
    return argon2.verify(inp_pass, hash_pass)

class TokenPayloadDict(dict):
    def __getattr__(self, item: str):
        return self.get(item)

class SecurityManager:
    def __init__(self):
        self.secret_key = settings.JWT_SECRET_KEY
        self.algorithm = settings.JWT_ALGORITHM

    def create_token(
        self,
        uid: str,
        token_use: str,
        aud: str,
        expiry: timedelta,
        custom_claims: Optional[Dict[str, Any]] = None
    ) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(uid),
            "token_use": token_use,
            "aud": aud,
            "iat": int(now.timestamp()),
            "exp": int((now + expiry).timestamp()),
        }
        if custom_claims:
            payload.update(custom_claims)
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def _decode_token(self, token: str) -> TokenPayloadDict:
        decoded = jwt.decode(
            token,
            self.secret_key,
            algorithms=[self.algorithm],
            options={"verify_aud": False, "verify_exp": True}
        )
        return TokenPayloadDict(decoded)

security = SecurityManager()