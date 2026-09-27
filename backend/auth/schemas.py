from typing import Optional, Literal
from pydantic import BaseModel, Field, ConfigDict

class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthTokenRequest(RequestModel):
    initData: Optional[str] = None
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

