from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session
from database.create_tables import get_db
from auth.service import AuthService
from platform_core import ratelimit, registry
from auth.schemas import AuthTokenRequest, RefreshRequest, CreateServiceSession
from auth.dependencies import basic_scheme, get_current_core_session, json_body, validated, RequireMachineScope
from auth.schemas import MachineTokenRequest, IntrospectionRequest

router = APIRouter()

@router.post("/api/v1/auth/token")
def login_with_max(request: Request, db: Session = Depends(get_db), payload=Depends(json_body)):
    """Вход через MAX / dev-логин с созданием сессии ядра. Лимит — на IP (до разбора тела)."""
    ratelimit.hit("login", request.headers.get("x-real-ip") or (request.client.host if request.client else None))
    body = validated(AuthTokenRequest, payload)
    return AuthService.login_or_register(body, db)


@router.post("/api/v1/auth/refresh")
def refresh_core_session(body: RefreshRequest, db: Session = Depends(get_db)):
    """Ротация токенов сессии ядра."""
    return AuthService.refresh_core_session(body.refresh_token, db)


@router.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout_core_session(body: RefreshRequest, db: Session = Depends(get_db)):
    """Отзыв сессии ядра и дочерних сервис-сессий."""
    AuthService.logout_core_session(body.refresh_token, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/v1/auth/me")
def get_current_user(session_data=Depends(get_current_core_session)):
    """Получение данных текущего пользователя платформы."""
    user, _ = session_data
    return {
        "id": user.id,
        "max_user_id": user.max_user_id,
        "created_at": registry.iso(user.created_at)
    }


@router.post("/api/v1/institution/{institution_id}/service/{service_id}/session", status_code=status.HTTP_201_CREATED)
def create_service_session(
    institution_id: str,
    service_id: str,
    response: Response,
    session_data=Depends(get_current_core_session),
    db: Session = Depends(get_db),
    payload=Depends(json_body),
):
    """Выдать пару JWT токенов для одного экземпляра сервиса и профиля (тело — после авторизации)."""
    user, core_session = session_data
    body = validated(CreateServiceSession, payload)
    result = AuthService.create_service_session(
        user=user,
        core_session=core_session,
        institution_id=institution_id,
        service_id=service_id,
        profile=body.profile,
        db=db
    )
    response.headers["Location"] = f"/api/v1/institution/{institution_id}/service/{service_id}/session/{result['session_id']}"
    return result


def machine_client(credentials=Depends(basic_scheme), db: Session = Depends(get_db)):
    return AuthService.verify_client(credentials, db)


@router.post("/api/v1/internal/auth/token")
def issue_machine_token(
    cred=Depends(machine_client),
    db: Session = Depends(get_db),
    payload=Depends(json_body),
):
    """Обмен Basic Auth client_id:client_secret на Machine JWT; тело проверяется после ключа."""
    validated(MachineTokenRequest, payload)
    return AuthService.issue_machine_token(cred, db)


@router.get("/api/v1/internal/auth/jwks")
def get_access_jwks(response: Response):
    """Публичные ключи ядра для валидации access JWT."""
    response.headers["Cache-Control"] = "public, max-age=300"
    return AuthService.get_public_jwks()


@router.post("/api/v1/internal/auth/introspect")
def introspect_service_access(
    machine_claims = Depends(RequireMachineScope("tokens:introspect")),
    db: Session = Depends(get_db),
    payload=Depends(json_body),
):
    """Проверка валидности service JWT и возврат актуальных прав из БД."""
    body = validated(IntrospectionRequest, payload)
    return AuthService.introspect_service_token(body.token, machine_claims, db)


@router.post("/api/v1/institution/{institution_id}/service/{service_id}/session/refresh")
def refresh_service_session(
    institution_id: str,
    service_id: str,
    body: RefreshRequest,
    db: Session = Depends(get_db)
):
    """Ротировать пару JWT сервиса."""
    return AuthService.refresh_service_session(service_id, institution_id, body.refresh_token, db)


@router.delete(
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
    AuthService.revoke_service_session(session_id, core_session, user, db, institution_id, service_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
