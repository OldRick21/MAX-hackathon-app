from typing import Optional
from pydantic import BaseModel, Field

class AuthTokenRequest(BaseModel):
    initData: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    max_user_id: Optional[str] = None

class RefreshRequest(BaseModel):
    refresh_token: str

class CreateServiceSession(BaseModel):
    profile: str = Field(..., description="Выбранный профиль: admin, teacher или student")

class MachineTokenRequest(BaseModel):
    grant_type: str = Field("client_credentials", pattern="^client_credentials$")

class IntrospectionRequest(BaseModel):
    token: str

