from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from urllib.parse import urlsplit

class AuthSettings(BaseSettings):
    MAX_BOT_TOKEN: str = ""
    ALLOW_DEV_LOGIN: bool = False
    SEED_DEMO_DATA: bool = False
    ALLOW_FAKE_REDIS: bool = False
    CURSOR_SECRET_KEY: str
    JWT_ISSUER: str
    JWT_KEYRING_PATH: str = "./data/jwt-keys.json"

    @field_validator('JWT_ISSUER')
    @classmethod
    def valid_issuer(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('JWT_ISSUER must be an exact HTTPS issuer URL without query/fragment/userinfo')
        return value

    model_config = SettingsConfigDict(
        env_file=".env",            # need to read .env
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_REVOKED_TOKENS_DB: int = 1

