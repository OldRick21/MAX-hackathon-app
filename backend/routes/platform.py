"""Маршруты платформы.

- /api/v1/institution-applications — заявки пользователя на подключение вуза;
- /api/v1/platform/... — поддержка платформы (core access + запись в platform_staff);
- /api/v1/internal/provisioning/... — чтение bindings процессом administration.
  Префикс /api/v1/internal закрыт на публичном Nginx.
"""
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from database.create_tables import get_db
from database.tables import CloudBinding, ServiceCredential, ServiceInstance
from auth.dependencies import get_current_core_session
from platform_core.concurrency import constant_time_token_match, derive_binding_secret, is_uuid, split_bearer
from platform_core.errors import DomainError
from routes.private_admin import respond
from services import platform_support as svc
from services import join_requests
from settings.config import settings
from database.tables import PlatformStaff

router_platform = APIRouter(tags=["Platform support"])


def staff_context(request: Request, session_data=Depends(get_current_core_session),
                  db: Session = Depends(get_db)) -> svc.StaffContext:
    user, _ = session_data
    staff = db.get(PlatformStaff, user.id)
    if not staff:
        raise DomainError(403, "FORBIDDEN", "Раздел доступен только поддержке платформы")
    return svc.StaffContext(user_id=user.id, role=staff.role, request_id=getattr(request.state, "request_id", None))


def staff_mutation(db: Session, staff: svc.StaffContext, action: str, target: str, fn):
    try:
        return respond(fn())
    except DomainError as error:
        db.rollback()
        if error.status in (403, 409):
            staff.audit(db, action, "request", target, {"message": error.message}, outcome="denied",
                        error_code=error.code)
            db.commit()
        raise


# --- Любой вошедший пользователь ---

@router_platform.get("/api/v1/platform/me")
def platform_me(session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    user, _ = session_data
    return respond(svc.platform_me(db, user))


@router_platform.get("/api/v1/institution-applications")
def my_applications(session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    user, _ = session_data
    return respond(svc.my_applications(db, user))


@router_platform.post("/api/v1/institution-applications")
def submit_application(request: Request, payload: Any = Body(None),
                       session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    user, _ = session_data
    return respond(svc.submit_application(db, user, payload, getattr(request.state, "request_id", None)))


@router_platform.post("/api/v1/institution-applications/{application_id}/withdraw")
def withdraw_application(application_id: str, request: Request,
                         session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    user, _ = session_data
    return respond(svc.withdraw_application(db, user, application_id, getattr(request.state, "request_id", None)))


@router_platform.get("/api/v1/join/institutions")
def join_options(session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    user, _ = session_data
    return respond(join_requests.join_options(db, user))


@router_platform.get("/api/v1/join-requests")
def my_join_requests(session_data=Depends(get_current_core_session), db: Session = Depends(get_db)):
    user, _ = session_data
    return respond(join_requests.my_requests(db, user))


@router_platform.post("/api/v1/join-requests")
def submit_join_requests(payload: Any = Body(None), session_data=Depends(get_current_core_session),
                         db: Session = Depends(get_db)):
    user, _ = session_data
    return respond(join_requests.submit(db, user, payload))


@router_platform.post("/api/v1/join-requests/{request_id}/withdraw")
def withdraw_join_request(request_id: str, session_data=Depends(get_current_core_session),
                          db: Session = Depends(get_db)):
    user, _ = session_data
    return respond(join_requests.withdraw(db, user, request_id))


# --- Поддержка платформы ---

@router_platform.get("/api/v1/platform/applications")
def list_applications(status: Optional[str] = Query(None), limit: Optional[int] = Query(None),
                      cursor: Optional[str] = Query(None, max_length=2048),
                      staff: svc.StaffContext = Depends(staff_context), db: Session = Depends(get_db)):
    return respond(svc.list_applications(db, staff, status, limit, cursor))


@router_platform.get("/api/v1/platform/applications/{application_id}")
def get_application(application_id: str, staff: svc.StaffContext = Depends(staff_context),
                    db: Session = Depends(get_db)):
    return respond(svc.get_application(db, staff, application_id))


@router_platform.post("/api/v1/platform/applications/{application_id}/approve")
def approve_application(application_id: str, payload: Any = Body(None),
                        staff: svc.StaffContext = Depends(staff_context), db: Session = Depends(get_db)):
    return staff_mutation(db, staff, "application.approve", application_id,
                          lambda: svc.approve_application(db, staff, application_id, payload))


@router_platform.post("/api/v1/platform/applications/{application_id}/reject")
def reject_application(application_id: str, payload: Any = Body(None),
                       staff: svc.StaffContext = Depends(staff_context), db: Session = Depends(get_db)):
    return staff_mutation(db, staff, "application.reject", application_id,
                          lambda: svc.reject_application(db, staff, application_id, payload))


@router_platform.get("/api/v1/platform/institutions")
def list_institutions(limit: Optional[int] = Query(None), cursor: Optional[str] = Query(None, max_length=2048),
                      staff: svc.StaffContext = Depends(staff_context), db: Session = Depends(get_db)):
    return respond(svc.list_institutions(db, staff, limit, cursor))


@router_platform.get("/api/v1/platform/institutions/{institution_id}")
def get_institution(institution_id: str, staff: svc.StaffContext = Depends(staff_context),
                    db: Session = Depends(get_db)):
    return respond(svc.get_institution(db, staff, institution_id))


@router_platform.patch("/api/v1/platform/institutions/{institution_id}")
def set_status(institution_id: str, payload: Any = Body(None), if_match: Optional[str] = Header(None, alias="If-Match"),
               staff: svc.StaffContext = Depends(staff_context), db: Session = Depends(get_db)):
    return staff_mutation(db, staff, "institution.status", institution_id,
                          lambda: svc.set_status(db, staff, institution_id, payload, if_match))


@router_platform.put("/api/v1/platform/institutions/{institution_id}/local-hosts")
def replace_local_hosts(institution_id: str, payload: Any = Body(None),
                        if_match: Optional[str] = Header(None, alias="If-Match"),
                        staff: svc.StaffContext = Depends(staff_context), db: Session = Depends(get_db)):
    return staff_mutation(db, staff, "institution.local_hosts", institution_id,
                          lambda: svc.replace_local_hosts(db, staff, institution_id, payload, if_match))


@router_platform.post("/api/v1/platform/institutions/{institution_id}/initial-owner")
def assign_initial_owner(institution_id: str, payload: Any = Body(None),
                         staff: svc.StaffContext = Depends(staff_context), db: Session = Depends(get_db)):
    return staff_mutation(db, staff, "institution.initial_owner", institution_id,
                          lambda: svc.assign_initial_owner(db, staff, institution_id, payload))


@router_platform.get("/api/v1/platform/audit")
def platform_audit(limit: Optional[int] = Query(None), cursor: Optional[str] = Query(None, max_length=2048),
                   staff: svc.StaffContext = Depends(staff_context), db: Session = Depends(get_db)):
    return respond(svc.list_platform_audit(db, staff, limit, cursor))


# --- Provisioning для процесса administration (private listener) ---

@router_platform.get("/api/v1/internal/provisioning/bindings/{service_id}")
def get_binding(service_id: str, authorization: Optional[str] = Header(None), db: Session = Depends(get_db)):
    """Binding облачного экземпляра administration по его UUID.

    Доступ — только токен процесса administration из секретов deployment.
    Другие типы этим токеном не читаются; tenant выбирается по UUID из уже
    подписанного service access, а не из пользовательского ввода.
    """
    scheme, token = split_bearer(authorization)
    service_type = None
    if scheme == "bearer":
        if constant_time_token_match(token, settings.ADMINISTRATION_PROVISIONING_TOKEN):
            service_type = "administration"
        elif constant_time_token_match(token, settings.USER_PROFILE_PROVISIONING_TOKEN):
            service_type = "user-profile"
        elif constant_time_token_match(token, settings.SCHEDULE_PROVISIONING_TOKEN):
            service_type = "schedule"
    if service_type is None:
        raise DomainError(404, "RESOURCE_NOT_FOUND", "Ресурс не найден")
    if not is_uuid(service_id):
        raise DomainError(404, "RESOURCE_NOT_FOUND", "Binding не найден")
    binding = db.get(CloudBinding, service_id)
    service = db.get(ServiceInstance, service_id)
    if not binding or not binding.active or not service or binding.service_type != service_type \
            or service.service_type != service_type:
        raise DomainError(404, "RESOURCE_NOT_FOUND", "Binding не найден")
    credential = db.get(ServiceCredential, binding.credential_id)
    if not credential or credential.revoked_at is not None or not settings.CLOUD_BINDING_KEY:
        raise DomainError(503, "SERVICE_UNAVAILABLE", "Binding ещё не готов")
    return respond(svc.Result({
        "institution_id": binding.institution_id,
        "service_id": binding.service_id,
        "service_type": binding.service_type,
        "api_base_url": service.api_base_url,
        "client_base_url": service.client_base_url,
        "client_id": binding.client_id,
        "credential_id": binding.credential_id,
        "client_secret": derive_binding_secret(settings.CLOUD_BINDING_KEY, binding.credential_id),
        "binding_revision": binding.revision,
    }))
