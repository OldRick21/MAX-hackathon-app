import hashlib
import json
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from database.create_tables import get_db
from services.service_registry import ServiceRegistry
from auth.dependencies import RequireMachineScope
from user_operations.user_operations import ServiceManifest, RoleInput, RolePatch, AssignmentInput

router = APIRouter()

@router.get("/api/v1/internal/service/{service_id}/manifest")
def get_own_manifest(
    service_id: str,
    machine_claims = Depends(RequireMachineScope("manifest:write")),
    db: Session = Depends(get_db)
):
    """Прочитать манифест своего экземпляра; ETag нужен для If-Match при замене (CORE_API_SPEC §7)."""
    manifest = ServiceRegistry.get_service_manifest(service_id, machine_claims, db)
    return JSONResponse(manifest, headers={"ETag": manifest_etag(manifest)})


def manifest_etag(manifest: dict) -> str:
    raw = json.dumps(manifest, sort_keys=True, ensure_ascii=False)
    return '"' + hashlib.sha256(raw.encode()).hexdigest()[:40] + '"'


@router.put("/api/v1/internal/service/{service_id}/manifest")
def replace_own_manifest(
    service_id: str,
    manifest: ServiceManifest,
    if_match: Optional[str] = Header(None, alias="If-Match"),
    machine_claims = Depends(RequireMachineScope("manifest:write")),
    db: Session = Depends(get_db)
):
    """Заменить имя и меню своего экземпляра: нет If-Match — 428, устаревший — 412."""
    if not if_match:
        raise HTTPException(status_code=428, detail="PRECONDITION_REQUIRED: If-Match is required")
    current = ServiceRegistry.get_service_manifest(service_id, machine_claims, db)
    if if_match != manifest_etag(current):
        raise HTTPException(status_code=412, detail="PRECONDITION_FAILED: Manifest changed")
    saved = ServiceRegistry.replace_service_manifest(service_id, manifest, machine_claims, db)
    return JSONResponse(saved, headers={"ETag": manifest_etag(saved)})


@router.get("/api/v1/internal/service/{service_id}/users/{user_id}/profiles")
def get_service_user_profiles(
    service_id: str,
    user_id: str,
    machine_claims = Depends(RequireMachineScope("profiles:read")),
    db: Session = Depends(get_db)
):
    """Прочитать профили участника в вузе своего экземпляра."""
    return ServiceRegistry.get_service_user_profiles(service_id, user_id, machine_claims, db)


@router.get("/api/v1/internal/service/{service_id}/groups")
def list_institution_groups(
    service_id: str,
    machine_claims = Depends(RequireMachineScope("groups:read")),
    db: Session = Depends(get_db)
):
    """Учебные группы вуза своего экземпляра."""
    return ServiceRegistry.list_service_groups(service_id, machine_claims, db)


@router.get("/api/v1/internal/service/{service_id}/groups/{group_id}/members")
def get_institution_group_members(
    service_id: str,
    group_id: str,
    machine_claims = Depends(RequireMachineScope("groups:read")),
    db: Session = Depends(get_db)
):
    """Состав учебной группы вуза своего экземпляра."""
    return ServiceRegistry.get_service_group_members(service_id, group_id, machine_claims, db)


@router.get("/api/v1/internal/service/{service_id}/roles")
def list_own_roles(
    service_id: str,
    machine_claims = Depends(RequireMachineScope("roles:read")),
    db: Session = Depends(get_db)
):
    """Получить определения ролей своего экземпляра."""
    return ServiceRegistry.list_service_roles(service_id, machine_claims, db)


@router.post("/api/v1/internal/service/{service_id}/roles", status_code=status.HTTP_201_CREATED)
def create_own_role(
    service_id: str,
    role_input: RoleInput,
    machine_claims = Depends(RequireMachineScope("roles:write")),
    db: Session = Depends(get_db)
):
    """Создать роль своего экземпляра."""
    return ServiceRegistry.create_service_role(service_id, role_input, machine_claims, db)


@router.get("/api/v1/internal/service/{service_id}/roles/{role_code}")
def get_own_role(
    service_id: str,
    role_code: str,
    machine_claims = Depends(RequireMachineScope("roles:read")),
    db: Session = Depends(get_db)
):
    """Получить определение конкретной роли."""
    return ServiceRegistry.get_service_role(service_id, role_code, machine_claims, db)


@router.patch("/api/v1/internal/service/{service_id}/roles/{role_code}")
def update_own_role(
    service_id: str,
    role_code: str,
    patch_input: RolePatch,
    machine_claims = Depends(RequireMachineScope("roles:write")),
    db: Session = Depends(get_db)
):
    """Изменить имя или права роли."""
    return ServiceRegistry.update_service_role(service_id, role_code, patch_input, machine_claims, db)


@router.delete("/api/v1/internal/service/{service_id}/roles/{role_code}", status_code=status.HTTP_204_NO_CONTENT)
def delete_own_role(
    service_id: str,
    role_code: str,
    machine_claims = Depends(RequireMachineScope("roles:write")),
    db: Session = Depends(get_db)
):
    """Удалить неназначенную роль."""
    ServiceRegistry.delete_service_role(service_id, role_code, machine_claims, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/v1/internal/service/{service_id}/users/{user_id}/profiles/{profile}/roles")
def get_own_assignments(
    service_id: str,
    user_id: str,
    profile: str,
    machine_claims = Depends(RequireMachineScope("assignments:read")),
    db: Session = Depends(get_db)
):
    """Прочитать назначенные роли и эффективные permissions."""
    return ServiceRegistry.get_service_assignments(service_id, user_id, profile, machine_claims, db)


@router.put("/api/v1/internal/service/{service_id}/users/{user_id}/profiles/{profile}/roles")
def replace_own_assignments(
    service_id: str,
    user_id: str,
    profile: str,
    assignment_input: AssignmentInput,
    machine_claims = Depends(RequireMachineScope("assignments:write")),
    db: Session = Depends(get_db)
):
    """Атомарно заменить назначения ролей в своём экземпляре."""
    return ServiceRegistry.replace_service_assignments(
        service_id,
        user_id,
        profile,
        assignment_input,
        machine_claims,
        db
    )

