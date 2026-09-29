from typing import Optional, Literal
from pydantic import BaseModel, Field, ConfigDict

class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthTokenRequest(RequestModel):
    consent_version: Optional[str] = Field(None, max_length=80)
    consent_challenge: Optional[str] = Field(None, max_length=64)
    # Контракт: только initData (обязателен вне режима разработки — проверка в AuthService).
    initData: Optional[str] = Field(None, min_length=1, max_length=16384, strict=True)
    username: Optional[str] = None
    password: Optional[str] = None
    max_user_id: Optional[str] = None

class RefreshRequest(RequestModel):
    refresh_token: str = Field(..., min_length=1, max_length=16384, strict=True)

class CreateServiceSession(RequestModel):
    profile: Literal["admin", "teacher", "student"] = Field(..., description="Выбранный профиль: admin, teacher или student")

class MachineTokenRequest(RequestModel):
    grant_type: Literal["client_credentials"]

class IntrospectionRequest(RequestModel):
    token: str = Field(..., min_length=1, max_length=16384, strict=True)

