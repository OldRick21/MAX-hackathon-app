from typing import Optional, List
from pydantic import BaseModel, Field

class LocalizedText(BaseModel):
    ru: str
    en: Optional[str] = None

class MenuDefinition(BaseModel):
    id: str
    titles: LocalizedText
    entrypoint_path: str
    profiles: List[str]
    required_permissions: List[str] = []
    order: int = 0

class ServiceManifest(BaseModel):
    titles: LocalizedText
    menus: List[MenuDefinition] = []

class RoleInput(BaseModel):
    code: str = Field(..., pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    titles: LocalizedText
    allowed_profiles: List[str]
    permissions: List[str] = []

class RolePatch(BaseModel):
    titles: Optional[LocalizedText] = None
    allowed_profiles: Optional[List[str]] = None
    permissions: Optional[List[str]] = None

class AssignmentInput(BaseModel):
    roles: List[str] = []

# --- Приватное администрирование ---

class MembershipCreate(BaseModel):
    user_id: str
    profiles: List[str] = Field(..., min_length=1)

class ProfilesInput(BaseModel):
    profiles: List[str] = Field(..., min_length=1)

class InstitutionPatch(BaseModel):
    titles: Optional[LocalizedText] = None
    default_locale: Optional[str] = None

class ServiceCreate(BaseModel):
    service_type: str
    deployment: str = Field(..., pattern="^(cloud|local)$")
    api_base_url: Optional[str] = None
    client_base_url: Optional[str] = None


class ServicePatch(BaseModel):
    enabled: Optional[bool] = None
    api_base_url: Optional[str] = None
    client_base_url: Optional[str] = None