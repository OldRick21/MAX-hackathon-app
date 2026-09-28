"""HTTP-слой private API вуза. Вся логика — в services/institution_admin.py.

Префикс закрыт на публичном Nginx (404 для любых методов); сюда обращается
только backend сервиса администрирования из внутренней сети.
"""
from typing import Any, Callable, Optional

from fastapi import APIRouter, Body, Depends, Header, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database.create_tables import get_db
from auth.authorization import ActorContext, private_actor
from platform_core.errors import DomainError
from services import institution_admin as svc
from services import join_requests

PREFIX = "/api/v1/institution/{institution_id}/internal"
router_private = APIRouter(prefix=PREFIX, tags=["Private institution administration"])


def respond(result: svc.Result) -> Response:
    headers = {"Cache-Control": "no-store", **result.headers}
    if result.etag:
        headers["ETag"] = result.etag
    if result.location:
        headers["Location"] = result.location
    if result.status == 204:
        return Response(status_code=204, headers=headers)
    return JSONResponse(result.body, status_code=result.status, headers=headers)


def mutate(db: Session, ctx: ActorContext, action: str, target: str, fn: Callable[[], svc.Result]) -> Response:
    """Выполняет изменение; отказ по правам/инварианту тоже попадает в журнал."""
    try:
        return respond(fn())
    except DomainError as error:
        db.rollback()
        if error.status in (403, 409):
            ctx.audit(db, action, "request", target, {"message": error.message}, outcome="denied",
                      error_code=error.code)
            db.commit()
        raise


# --- Вуз ---

@router_private.get("")
def get_institution(ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return respond(svc.get_institution(db, ctx))


@router_private.patch("")
def patch_institution(payload: Any = Body(None), if_match: Optional[str] = Header(None, alias="If-Match"),
                      ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return mutate(db, ctx, "institution.update", ctx.institution_id,
                  lambda: svc.patch_institution(db, ctx, payload, if_match))


@router_private.get("/service-types")
def service_types(ctx: ActorContext = Depends(private_actor)):
    return respond(svc.list_service_types(ctx))


@router_private.get("/audit")
def audit(limit: Optional[int] = Query(None), cursor: Optional[str] = Query(None, max_length=2048),
          ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return respond(svc.list_audit(db, ctx, limit, cursor))


# --- Участники ---

@router_private.get("/members")
def list_members(limit: Optional[int] = Query(None), cursor: Optional[str] = Query(None, max_length=2048),
                 ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return respond(svc.list_members(db, ctx, limit, cursor))


@router_private.post("/members")
def add_member(payload: Any = Body(None), ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    target = payload.get("user_id") if isinstance(payload, dict) else None
    return mutate(db, ctx, "member.add", str(target), lambda: svc.add_member(db, ctx, payload))


@router_private.get("/members/{user_id}")
def get_member(user_id: str, ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return respond(svc.get_member(db, ctx, user_id))


@router_private.delete("/members/{user_id}")
def remove_member(user_id: str, if_match: Optional[str] = Header(None, alias="If-Match"),
                  ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return mutate(db, ctx, "member.remove", user_id, lambda: svc.remove_member(db, ctx, user_id, if_match))


@router_private.put("/members/{user_id}/profiles")
def replace_profiles(user_id: str, payload: Any = Body(None), if_match: Optional[str] = Header(None, alias="If-Match"),
                     ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return mutate(db, ctx, "member.profiles.replace", user_id,
                  lambda: svc.replace_profiles(db, ctx, user_id, payload, if_match))


@router_private.put("/members/{user_id}/group")
def set_member_group(user_id: str, payload: Any = Body(None), ctx: ActorContext = Depends(private_actor),
                     db: Session = Depends(get_db)):
    return mutate(db, ctx, "member.group.set", user_id, lambda: svc.set_member_group(db, ctx, user_id, payload))


# --- Заявки на вступление ---

@router_private.get("/join-requests")
def list_join_requests(status: Optional[str] = Query(None), ctx: ActorContext = Depends(private_actor),
                       db: Session = Depends(get_db)):
    return respond(join_requests.list_for_institution(db, ctx, status))


@router_private.post("/join-requests/{request_id}/approve")
def approve_join_request(request_id: str, ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return mutate(db, ctx, "join_request.approve", request_id, lambda: join_requests.approve(db, ctx, request_id))


@router_private.post("/join-requests/{request_id}/reject")
def reject_join_request(request_id: str, payload: Any = Body(None), ctx: ActorContext = Depends(private_actor),
                        db: Session = Depends(get_db)):
    return mutate(db, ctx, "join_request.reject", request_id,
                  lambda: join_requests.reject(db, ctx, request_id, payload))


# --- Учебные группы ---

@router_private.get("/groups")
def list_groups(limit: Optional[int] = Query(None), cursor: Optional[str] = Query(None, max_length=2048),
                ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return respond(svc.list_groups(db, ctx, limit, cursor))


@router_private.post("/groups")
def create_group(payload: Any = Body(None), ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return mutate(db, ctx, "group.create", ctx.institution_id, lambda: svc.create_group(db, ctx, payload))


@router_private.get("/groups/{group_id}")
def get_group(group_id: str, ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return respond(svc.get_group(db, ctx, group_id))


@router_private.patch("/groups/{group_id}")
def rename_group(group_id: str, payload: Any = Body(None), if_match: Optional[str] = Header(None, alias="If-Match"),
                 ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return mutate(db, ctx, "group.rename", group_id, lambda: svc.rename_group(db, ctx, group_id, payload, if_match))


@router_private.delete("/groups/{group_id}")
def delete_group(group_id: str, if_match: Optional[str] = Header(None, alias="If-Match"),
                 ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return mutate(db, ctx, "group.delete", group_id, lambda: svc.delete_group(db, ctx, group_id, if_match))


@router_private.get("/groups/{group_id}/members")
def get_group_members(group_id: str, ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return respond(svc.get_group_members(db, ctx, group_id))


@router_private.put("/groups/{group_id}/members")
def replace_group_members(group_id: str, payload: Any = Body(None), if_match: Optional[str] = Header(None, alias="If-Match"),
                          ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return mutate(db, ctx, "group.members.replace", group_id,
                  lambda: svc.replace_group_members(db, ctx, group_id, payload, if_match))


# --- Экземпляры ---

@router_private.get("/services")
def list_services(limit: Optional[int] = Query(None), cursor: Optional[str] = Query(None, max_length=2048),
                  ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return respond(svc.list_services(db, ctx, limit, cursor))


@router_private.post("/services")
def install_service(payload: Any = Body(None), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
                    ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    target = payload.get("service_type") if isinstance(payload, dict) else None
    return mutate(db, ctx, "service.install", str(target),
                  lambda: svc.install_service(db, ctx, payload, idempotency_key))


@router_private.get("/services/{service_id}")
def get_service(service_id: str, ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return respond(svc.get_service(db, ctx, service_id))


@router_private.patch("/services/{service_id}")
def patch_service(service_id: str, payload: Any = Body(None), if_match: Optional[str] = Header(None, alias="If-Match"),
                  ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return mutate(db, ctx, "service.update", service_id,
                  lambda: svc.patch_service(db, ctx, service_id, payload, if_match))


@router_private.delete("/services/{service_id}")
def uninstall_service(service_id: str, if_match: Optional[str] = Header(None, alias="If-Match"),
                      ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return mutate(db, ctx, "service.uninstall", service_id,
                  lambda: svc.uninstall_service(db, ctx, service_id, if_match))


@router_private.put("/services/{service_id}/manifest")
def replace_manifest(service_id: str, payload: Any = Body(None), if_match: Optional[str] = Header(None, alias="If-Match"),
                     ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return mutate(db, ctx, "service.manifest.replace", service_id,
                  lambda: svc.replace_manifest(db, ctx, service_id, payload, if_match))


# --- Роли ---

@router_private.get("/services/{service_id}/roles")
def list_roles(service_id: str, limit: Optional[int] = Query(None), cursor: Optional[str] = Query(None, max_length=2048),
               ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return respond(svc.list_roles(db, ctx, service_id, limit, cursor))


@router_private.post("/services/{service_id}/roles")
def create_role(service_id: str, payload: Any = Body(None),
                ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return mutate(db, ctx, "role.create", service_id, lambda: svc.create_role(db, ctx, service_id, payload))


@router_private.get("/services/{service_id}/roles/{role_code}")
def get_role(service_id: str, role_code: str, ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return respond(svc.get_role(db, ctx, service_id, role_code))


@router_private.patch("/services/{service_id}/roles/{role_code}")
def patch_role(service_id: str, role_code: str, payload: Any = Body(None), if_match: Optional[str] = Header(None, alias="If-Match"),
               ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return mutate(db, ctx, "role.update", f"{service_id}:{role_code}",
                  lambda: svc.patch_role(db, ctx, service_id, role_code, payload, if_match))


@router_private.delete("/services/{service_id}/roles/{role_code}")
def delete_role(service_id: str, role_code: str, if_match: Optional[str] = Header(None, alias="If-Match"),
                ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return mutate(db, ctx, "role.delete", f"{service_id}:{role_code}",
                  lambda: svc.delete_role(db, ctx, service_id, role_code, if_match))


@router_private.get("/services/{service_id}/users/{user_id}/profiles/{profile}/roles")
def get_assignments(service_id: str, user_id: str, profile: str,
                    ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return respond(svc.get_assignments(db, ctx, service_id, user_id, profile))


@router_private.put("/services/{service_id}/users/{user_id}/profiles/{profile}/roles")
def replace_assignments(service_id: str, user_id: str, profile: str, payload: Any = Body(None),
                        if_match: Optional[str] = Header(None, alias="If-Match"),
                        ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return mutate(db, ctx, "assignments.replace", f"{service_id}:{user_id}:{profile}",
                  lambda: svc.replace_assignments(db, ctx, service_id, user_id, profile, payload, if_match))


# --- Credentials ---

@router_private.get("/services/{service_id}/credentials")
def list_credentials(service_id: str, limit: Optional[int] = Query(None), cursor: Optional[str] = Query(None, max_length=2048),
                     ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return respond(svc.list_credentials(db, ctx, service_id, limit, cursor))


@router_private.post("/services/{service_id}/credentials")
def issue_credential(service_id: str, ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return mutate(db, ctx, "credential.issue", service_id, lambda: svc.issue_credential(db, ctx, service_id))


@router_private.delete("/services/{service_id}/credentials/{credential_id}")
def revoke_credential(service_id: str, credential_id: str,
                      ctx: ActorContext = Depends(private_actor), db: Session = Depends(get_db)):
    return mutate(db, ctx, "credential.revoke", credential_id,
                  lambda: svc.revoke_credential(db, ctx, service_id, credential_id))
