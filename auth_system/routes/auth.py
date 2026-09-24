from typing import Optional
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session
from database.create_tables import get_db
from database.tables import Institution, Membership, ServiceInstance
from services.auth_service import AuthService
from user_operations.user_operations import AuthTokenRequest, RefreshRequest, CreateServiceSession
from dependencies.dependencies import get_current_core_session
from fastapi.responses import JSONResponse
from dependencies.dependencies import (
    get_current_core_session,
    basic_scheme,
    get_current_machine_token,
    RequireMachineScope
)
from user_operations.user_operations import (
    AuthTokenRequest,
    RefreshRequest,
    CreateServiceSession,
    MachineTokenRequest,
    IntrospectionRequest
)
from fastapi.responses import HTMLResponse

router_auth = APIRouter(tags=["Auth & Institutions"])

# --- Аутентификация ядра ---

@router_auth.post("/api/v1/auth/token")
def login_with_max(body: AuthTokenRequest, db: Session = Depends(get_db)):
    """Вход через MAX / dev-логин с созданием сессии ядра."""
    return AuthService.login_or_register(body, db)


@router_auth.post("/api/v1/auth/refresh")
def refresh_core_session(body: RefreshRequest, db: Session = Depends(get_db)):
    """Ротация токенов сессии ядра."""
    return AuthService.refresh_core_session(body.refresh_token, db)


@router_auth.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout_core_session(body: RefreshRequest, db: Session = Depends(get_db)):
    """Отзыв сессии ядра и дочерних сервис-сессий."""
    AuthService.logout_core_session(body.refresh_token, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router_auth.get("/api/v1/auth/me")
def get_current_user(session_data=Depends(get_current_core_session)):
    """Получение данных текущего пользователя платформы."""
    user, _ = session_data
    return {
        "id": user.id,
        "max_user_id": user.max_user_id,
        "created_at": user.created_at.isoformat()
    }


# --- ВУЗы и профили ---

@router_auth.get("/api/v1/institution")
def list_my_institutions(session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    """Получить список своих ВУЗов и назначенные профили."""
    user, _ = session_data
    memberships = db.query(Membership).filter(Membership.user_id == user.id).all()

    items = []
    for m in memberships:
        inst = m.institution
        items.append({
            "id": inst.id,
            "display_name": inst.titles.get("ru", "ВУЗ"),
            "locale": "ru",
            "default_locale": inst.default_locale,
            "status": inst.status,
            "profiles": m.profiles or []
        })

    return {"items": items, "next_cursor": None}


@router_auth.get("/api/v1/institution/{institution_id}/profiles")
def list_my_profiles(institution_id: str, session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    """Получить свои профили в конкретном ВУЗе."""
    user, _ = session_data
    membership = db.query(Membership).filter(
        Membership.institution_id == institution_id,
        Membership.user_id == user.id
    ).first()

    profiles = membership.profiles if membership else []
    return {
        "user_id": user.id,
        "institution_id": institution_id,
        "profiles": profiles
    }


@router_auth.get("/api/v1/institution/{institution_id}/service")
def list_my_services(
    institution_id: str,
    profile: str = Query(..., description="Выбранный профиль"),
    session_data=Depends(get_current_core_session),
    db: Session = Depends(get_db)
):
    """Список сервисов и доступных меню для выбранного профиля."""
    user, _ = session_data
    profile_norm = profile.lower()

    # Проверка членства
    membership = db.query(Membership).filter(
        Membership.institution_id == institution_id,
        Membership.user_id == user.id
    ).first()

    if not membership or profile_norm not in [p.lower() for p in (membership.profiles or [])]:
        return {"items": [], "next_cursor": None}

    services = db.query(ServiceInstance).filter(
        ServiceInstance.institution_id == institution_id,
        ServiceInstance.enabled == True
    ).all()

    items = []
    for s in services:
        if profile_norm in [p.lower() for p in s.supported_profiles]:
            # Фильтрация меню манифеста по профилю
            menus = []
            manifest = s.manifest or {}
            for m in manifest.get("menus", []):
                if profile_norm in [p.lower() for p in m.get("profiles", [])]:
                    menus.append({
                        "id": m["id"],
                        "display_name": m.get("titles", {}).get("ru", m["id"]),
                        "locale": "ru",
                        "entrypoint_path": m.get("entrypoint_path", "/"),
                        "order": m.get("order", 0)
                    })

            items.append({
                "id": s.id,
                "institution_id": s.institution_id,
                "service_type": s.service_type,
                "deployment": s.deployment,
                "display_name": manifest.get("titles", {}).get("ru", s.service_type),
                "locale": "ru",
                "api_base_url": s.api_base_url,
                "client_base_url": s.client_base_url,
                "profile": profile_norm,
                "roles": [],
                "permissions": [],
                "menus": menus
            })

    return {"items": items, "next_cursor": None}


# --- Сервисные сессии ---

@router_auth.post("/api/v1/institution/{institution_id}/service/{service_id}/session", status_code=status.HTTP_201_CREATED)
def create_service_session(
    institution_id: str,
    service_id: str,
    body: CreateServiceSession,
    session_data=Depends(get_current_core_session),
    db: Session = Depends(get_db)
):
    """Выдать пару JWT токенов для одного экземпляра сервиса и профиля."""
    user, core_session = session_data
    return AuthService.create_service_session(
        user=user,
        core_session=core_session,
        institution_id=institution_id,
        service_id=service_id,
        profile=body.profile,
        db=db
    )

@router_auth.post("/api/v1/internal/auth/token")
def issue_machine_token(
    body: MachineTokenRequest,
    credentials = Depends(basic_scheme),
    db: Session = Depends(get_db)
):
    """Обмен Basic Auth client_id:client_secret на Machine JWT."""
    return AuthService.issue_machine_token(credentials, body, db)


@router_auth.get("/api/v1/internal/auth/jwks")
def get_access_jwks(response: Response):
    """Публичные ключи ядра для валидации access JWT."""
    response.headers["Cache-Control"] = "public, max-age=300"
    return AuthService.get_public_jwks()


@router_auth.post("/api/v1/internal/auth/introspect")
def introspect_service_access(
    body: IntrospectionRequest,
    machine_claims = Depends(RequireMachineScope("tokens:introspect")),
    db: Session = Depends(get_db)
):
    """Проверка валидности service JWT и возврат актуальных прав из БД."""
    return AuthService.introspect_service_token(body.token, machine_claims, db)

from user_operations.user_operations import (
    ServiceManifest,
    RoleInput,
    RolePatch,
    AssignmentInput
)

# =============================================================================
# SERVICE RBAC & MANIFESTS (/api/v1/internal/service/{service_id}/*)
# =============================================================================

@router_auth.get("/api/v1/internal/service/{service_id}/manifest")
def get_own_manifest(
    service_id: str,
    machine_claims = Depends(RequireMachineScope("manifest:write")),
    db: Session = Depends(get_db)
):
    """Прочитать манифест своего экземпляра."""
    return AuthService.get_service_manifest(service_id, machine_claims, db)


@router_auth.put("/api/v1/internal/service/{service_id}/manifest")
def replace_own_manifest(
    service_id: str,
    manifest: ServiceManifest,
    machine_claims = Depends(RequireMachineScope("manifest:write")),
    db: Session = Depends(get_db)
):
    """Заменить имя и меню своего экземпляра."""
    return AuthService.replace_service_manifest(service_id, manifest, machine_claims, db)


@router_auth.get("/api/v1/internal/service/{service_id}/users/{user_id}/profiles")
def get_service_user_profiles(
    service_id: str,
    user_id: str,
    machine_claims = Depends(RequireMachineScope("profiles:read")),
    db: Session = Depends(get_db)
):
    """Прочитать профили участника в вузе своего экземпляра."""
    return AuthService.get_service_user_profiles(service_id, user_id, machine_claims, db)


@router_auth.get("/api/v1/internal/service/{service_id}/roles")
def list_own_roles(
    service_id: str,
    machine_claims = Depends(RequireMachineScope("roles:read")),
    db: Session = Depends(get_db)
):
    """Получить определения ролей своего экземпляра."""
    return AuthService.list_service_roles(service_id, machine_claims, db)


@router_auth.post("/api/v1/internal/service/{service_id}/roles", status_code=status.HTTP_201_CREATED)
def create_own_role(
    service_id: str,
    role_input: RoleInput,
    machine_claims = Depends(RequireMachineScope("roles:write")),
    db: Session = Depends(get_db)
):
    """Создать роль своего экземпляра."""
    return AuthService.create_service_role(service_id, role_input, machine_claims, db)


@router_auth.get("/api/v1/internal/service/{service_id}/roles/{role_code}")
def get_own_role(
    service_id: str,
    role_code: str,
    machine_claims = Depends(RequireMachineScope("roles:read")),
    db: Session = Depends(get_db)
):
    """Получить определение конкретной роли."""
    return AuthService.get_service_role(service_id, role_code, machine_claims, db)


@router_auth.patch("/api/v1/internal/service/{service_id}/roles/{role_code}")
def update_own_role(
    service_id: str,
    role_code: str,
    patch_input: RolePatch,
    machine_claims = Depends(RequireMachineScope("roles:write")),
    db: Session = Depends(get_db)
):
    """Изменить имя или права роли."""
    return AuthService.update_service_role(service_id, role_code, patch_input, machine_claims, db)


@router_auth.delete("/api/v1/internal/service/{service_id}/roles/{role_code}", status_code=status.HTTP_204_NO_CONTENT)
def delete_own_role(
    service_id: str,
    role_code: str,
    machine_claims = Depends(RequireMachineScope("roles:write")),
    db: Session = Depends(get_db)
):
    """Удалить неназначенную роль."""
    AuthService.delete_service_role(service_id, role_code, machine_claims, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router_auth.get("/api/v1/internal/service/{service_id}/users/{user_id}/profiles/{profile}/roles")
def get_own_assignments(
    service_id: str,
    user_id: str,
    profile: str,
    machine_claims = Depends(RequireMachineScope("assignments:read")),
    db: Session = Depends(get_db)
):
    """Прочитать назначенные роли и эффективные permissions."""
    return AuthService.get_service_assignments(service_id, user_id, profile, machine_claims, db)


@router_auth.put("/api/v1/internal/service/{service_id}/users/{user_id}/profiles/{profile}/roles")
def replace_own_assignments(
    service_id: str,
    user_id: str,
    profile: str,
    assignment_input: AssignmentInput,
    machine_claims = Depends(RequireMachineScope("assignments:write")),
    db: Session = Depends(get_db)
):
    """Атомарно заменить назначения ролей в своём экземпляре."""
    return AuthService.replace_service_assignments(
        service_id,
        user_id,
        profile,
        assignment_input,
        machine_claims,
        db
    )
from dependencies.dependencies import RequirePrivateAdmin
from user_operations.user_operations import (
    MembershipCreate,
    ProfilesInput,
    InstitutionPatch,
    ServiceCreate
)

# =============================================================================
# PRIVATE ADMINISTRATION (/api/v1/institution/{institution_id}/internal/*)
# =============================================================================

@router_auth.get("/api/v1/institution/{institution_id}/internal")
def get_institution_settings(
    institution_id: str,
    _=Depends(RequirePrivateAdmin("institution.read")),
    db: Session = Depends(get_db)
):
    """Прочитать настройки ВУЗа."""
    return AuthService.get_institution_settings(institution_id, db)


@router_auth.patch("/api/v1/institution/{institution_id}/internal")
def update_institution_settings(
    institution_id: str,
    patch_data: InstitutionPatch,
    _=Depends(RequirePrivateAdmin("institution.update")),
    db: Session = Depends(get_db)
):
    """Изменить имя или язык ВУЗа."""
    return AuthService.update_institution_settings(institution_id, patch_data, db)


@router_auth.get("/api/v1/institution/{institution_id}/internal/service-types")
def list_service_types(
    institution_id: str,
    _=Depends(RequirePrivateAdmin("institution.read"))
):
    """Получить разрешенные типы сервисов MVP."""
    return AuthService.list_service_types()


@router_auth.get("/api/v1/institution/{institution_id}/internal/members")
def list_institution_members(
    institution_id: str,
    _=Depends(RequirePrivateAdmin("members.read")),
    db: Session = Depends(get_db)
):
    """Список участников ВУЗа."""
    return AuthService.list_institution_members(institution_id, db)


@router_auth.post("/api/v1/institution/{institution_id}/internal/members", status_code=status.HTTP_201_CREATED)
def add_institution_member(
    institution_id: str,
    member_data: MembershipCreate,
    _=Depends(RequirePrivateAdmin("members.manage")),
    db: Session = Depends(get_db)
):
    """Добавить зарегистрированного пользователя и назначить профили в ВУЗе."""
    return AuthService.add_institution_member(institution_id, member_data, db)


@router_auth.get("/api/v1/institution/{institution_id}/internal/members/{user_id}")
def get_institution_member(
    institution_id: str,
    user_id: str,
    _=Depends(RequirePrivateAdmin("members.read")),
    db: Session = Depends(get_db)
):
    """Прочитать данные участника ВУЗа."""
    return AuthService.get_institution_member(institution_id, user_id, db)


@router_auth.put("/api/v1/institution/{institution_id}/internal/members/{user_id}/profiles")
def replace_member_profiles(
    institution_id: str,
    user_id: str,
    profiles_data: ProfilesInput,
    _=Depends(RequirePrivateAdmin("members.manage")),
    db: Session = Depends(get_db)
):
    """Заменить набор профилей участника."""
    return AuthService.replace_member_profiles(institution_id, user_id, profiles_data, db)


@router_auth.delete("/api/v1/institution/{institution_id}/internal/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_institution_member(
    institution_id: str,
    user_id: str,
    _=Depends(RequirePrivateAdmin("members.manage")),
    db: Session = Depends(get_db)
):
    """Удалить участника из ВУЗа."""
    AuthService.remove_institution_member(institution_id, user_id, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router_auth.get("/api/v1/institution/{institution_id}/internal/services")
def list_institution_services(
    institution_id: str,
    _=Depends(RequirePrivateAdmin("services.read")),
    db: Session = Depends(get_db)
):
    """Прочитать все сервисы ВУЗа."""
    return AuthService.list_admin_services(institution_id, db)


@router_auth.post("/api/v1/institution/{institution_id}/internal/services", status_code=status.HTTP_201_CREATED)
def install_service_instance(
    institution_id: str,
    service_data: ServiceCreate,
    _=Depends(RequirePrivateAdmin("services.manage")),
    db: Session = Depends(get_db)
):
    """Зарегистрировать новый экземпляр сервиса (cloud / local)."""
    return AuthService.install_service_instance(institution_id, service_data, db)


@router_auth.post("/api/v1/institution/{institution_id}/internal/services/{service_id}/credentials", status_code=status.HTTP_201_CREATED)
def issue_local_service_credential(
    institution_id: str,
    service_id: str,
    _=Depends(RequirePrivateAdmin("credentials.manage")),
    db: Session = Depends(get_db)
):
    """Выдать новые client_id / client_secret для сервиса."""
    return AuthService.issue_local_credential(institution_id, service_id, db)

@router_auth.delete("/api/v1/institution/{institution_id}/internal/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def uninstall_service_instance(
    institution_id: str,
    service_id: str,
    _=Depends(RequirePrivateAdmin("services.manage")),
    db: Session = Depends(get_db)
):
    """Логически удалить экземпляр сервиса."""
    AuthService.uninstall_service_instance(institution_id, service_id, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router_auth.get("/institution", response_class=HTMLResponse, tags=["Shell"])
def get_institution_shell():
    """Отдает HTML-оболочку выбора ВУЗа (Shell)."""
    return HTMLResponse(content="""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <title>Вузы России — Выбор ВУЗа</title>
        <style>body { font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #f4f6f8; }</style>
    </head>
    <body>
        <div id="app">Загрузка платформы Вузы России...</div>
        <script>
            // Фронтенд Shell: считывает WebApp.initData и выполняет JSON POST /api/v1/auth/token
            console.log("Shell initialized");
        </script>
    </body>
    </html>
    """)

@router_auth.get("/institution/{institution_id}", response_class=HTMLResponse, tags=["Shell"])
def get_institution_workspace_shell(institution_id: str):
    """Отдает HTML-оболочку рабочего пространства ВУЗа."""
    return HTMLResponse(content=f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <title>Рабочее пространство ВУЗа</title>
        <style>body {{ font-family: sans-serif; margin: 0; background: #fff; }}</style>
    </head>
    <body>
        <div id="workspace">Рабочее пространство ВУЗа: {institution_id}</div>
        <script>
            // Загрузка динамических микрофронтендов сервисов
        </script>
    </body>
    </html>
    """)

@router_auth.get("/api/v1/health", tags=["Health"])
def get_health():
    """Проверить живость процесса ядра."""
    return {"status": "ok"}

# --- Детальные карточки ВУЗа и Сервиса ---
@router_auth.get("/api/v1/institution/{institution_id}")
def get_my_institution(
    institution_id: str,
    session_data=Depends(get_current_core_session),
    db: Session = Depends(get_db)
):
    """Получить информацию о доступном ВУЗе."""
    user, _ = session_data
    membership = db.query(Membership).filter(
        Membership.institution_id == institution_id,
        Membership.user_id == user.id
    ).first()

    if not membership:
        raise HTTPException(status_code=404, detail="RESOURCE_NOT_FOUND: Institution not found or not accessible")

    inst = membership.institution
    return {
        "id": inst.id,
        "display_name": inst.titles.get("ru", "ВУЗ"),
        "locale": "ru",
        "default_locale": inst.default_locale,
        "status": inst.status,
        "profiles": membership.profiles or []
    }


@router_auth.get("/api/v1/institution/{institution_id}/service/{service_id}")
def get_my_service(
    institution_id: str,
    service_id: str,
    profile: str = Query(..., description="Выбранный профиль"),
    session_data=Depends(get_current_core_session),
    db: Session = Depends(get_db)
):
    """Получить карточку конкретного сервиса."""
    user, _ = session_data
    profile_norm = profile.lower()

    membership = db.query(Membership).filter(
        Membership.institution_id == institution_id,
        Membership.user_id == user.id
    ).first()

    if not membership or profile_norm not in [p.lower() for p in (membership.profiles or [])]:
        raise HTTPException(status_code=403, detail="FORBIDDEN: Profile not assigned")

    service = db.query(ServiceInstance).filter(
        ServiceInstance.id == service_id,
        ServiceInstance.institution_id == institution_id
    ).first()

    if not service:
        raise HTTPException(status_code=404, detail="RESOURCE_NOT_FOUND: Service not found")
    if not service.enabled:
        raise HTTPException(status_code=409, detail="RESOURCE_INACTIVE: Service is disabled")
    if profile_norm not in [p.lower() for p in service.supported_profiles]:
        raise HTTPException(status_code=404, detail="RESOURCE_NOT_FOUND: Profile not supported by service")

    manifest = service.manifest or {}
    menus = []
    for m in manifest.get("menus", []):
        if profile_norm in [p.lower() for p in m.get("profiles", [])]:
            menus.append({
                "id": m["id"],
                "display_name": m.get("titles", {}).get("ru", m["id"]),
                "locale": "ru",
                "entrypoint_path": m.get("entrypoint_path", "/"),
                "order": m.get("order", 0)
            })

    return {
        "id": service.id,
        "institution_id": service.institution_id,
        "service_type": service.service_type,
        "deployment": service.deployment,
        "display_name": manifest.get("titles", {}).get("ru", service.service_type),
        "locale": "ru",
        "api_base_url": service.api_base_url,
        "client_base_url": service.client_base_url,
        "profile": profile_norm,
        "roles": [],
        "permissions": [],
        "menus": menus
    }


# --- Ротация и отзыв дочерней Service Session ---
@router_auth.post("/api/v1/institution/{institution_id}/service/{service_id}/session/refresh")
def refresh_service_session(
    institution_id: str,
    service_id: str,
    body: RefreshRequest,
    db: Session = Depends(get_db)
):
    """Ротировать пару JWT сервиса."""
    return AuthService.refresh_service_session(service_id, institution_id, body.refresh_token, db)


@router_auth.delete(
    "/api/v1/institution/{institution_id}/service/{service_id}/session/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
def revoke_service_session(
    institution_id: str,
    service_id: str,
    session_id: str,
    session_data=Depends(get_current_core_session),
    db: Session = Depends(get_db)
):
    """Отозвать дочернюю сессию сервиса."""
    user, core_session = session_data
    AuthService.revoke_service_session(session_id, core_session, user, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)