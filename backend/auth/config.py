from pydantic_settings import BaseSettings, SettingsConfigDict
from datetime import timedelta

class AuthSettings(BaseSettings):
    MAX_BOT_TOKEN: str = ""
    ALLOW_DEV_LOGIN: bool = False
    SEED_DEMO_DATA: bool = False
    ALLOW_FAKE_REDIS: bool = False
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRES: timedelta = timedelta(minutes=2)     # 2 minutes for access token
    JWT_REFRESH_TOKEN_EXPIRES: timedelta = timedelta(days=28)      # 28 days for refresh token (keep in HTTP-only cookie)

    # cookie settings
    JWT_ACCESS_COOKIE_NAME: str = "access_token"
    JWT_REFRESH_COOKIE_NAME: str = "refresh_token"
    JWT_TOKEN_LOCATION: list = ["headers", "cookies"]

    JWT_COOKIE_HTTPONLY: bool = True        # protect for XSS (block access from JS to cookies)
    # JWT_COOKIE_SECURE: bool = True        # https is needed (cookie only with https)
    JWT_COOKIE_SAMESITE: str = "lax"        # protect for CSRF (block post requests from other site)
    JWT_COOKIE_CSRF_PROTECT: bool = True    # other protect for CSRF

    model_config = SettingsConfigDict(
        env_file=".env",            # need to read .env
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_REVOKED_TOKENS_DB: int = 1

