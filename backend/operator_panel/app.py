"""Операторская веб-панель платформы.

Отдельный процесс из образа ядра: работает с той же БД и той же бизнес-логикой,
что ядро и manage.py. Запускается на сервере рядом с ядром; снаружи доступен через
Nginx по HTTPS (порт 8445) и, для SSH-туннеля, на 127.0.0.1:8500 хоста.

Вход — пароль оператора из OPERATOR_PASSWORD. Сессия — подписанная HMAC cookie
(HttpOnly, SameSite=Strict); изменяющие запросы требуют заголовок X-Operator,
который кросс-доменная страница не может отправить без CORS.

Оператор может всё, что владелец любого вуза в админке, плюс операции платформы:
заявки на подключение вузов, статусы, одобренные хосты, владельцы, права поддержки,
ключ и адреса администрирования вуза и служебные команды manage.py.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
import contextlib
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import Body, Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from auth.authorization import ActorContext
from database.create_tables import get_db, session_local
from database.tables import (
    AuditEvent,
    Institution,
    InstitutionApplication,
    JoinRequest,
    Membership,
    PlatformStaff,
    ServiceInstance,
    User,
)
from platform_core import catalog, registry
from platform_core.concurrency import is_uuid
from platform_core.errors import DomainError, not_found, validation
from services import institution_admin as svc
from services import join_requests, platform_ops, platform_support
from services.institution_admin import Result, _body

logger = logging.getLogger("operator")

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "operator_session"
SESSION_TTL = 12 * 3600
OPERATOR = "operator"  # actor_id оператора в проверках и курсорах; в журнал пишется actor_kind=operator
MIN_PASSWORD = 12

app = FastAPI(title="Operator panel", docs_url=None, redoc_url=None, openapi_url=None)


# --------------------------------------------------------------------------
# Настройки и сессия
# --------------------------------------------------------------------------

def _password() -> str:
    return os.environ.get("OPERATOR_PASSWORD", "")


def _secure_cookie() -> bool:
    return os.environ.get("OPERATOR_COOKIE_SECURE", "true").lower() != "false"


def _key() -> bytes:
    # Смена пароля оператора сразу завершает все сессии панели.
    from settings.config import settings
    return hashlib.sha256(f"operator:{_password()}:{settings.CURSOR_SECRET_KEY}".encode()).digest()


def _sign(expires: int, nonce: str) -> str:
    payload = f"{expires}.{nonce}"
    mac = hmac.new(_key(), payload.encode(), hashlib.sha256).digest()
    return payload + "." + base64.urlsafe_b64encode(mac).decode().rstrip("=")


def _valid(token: Optional[str]) -> bool:
    if not token or token.count(".") != 2:
        return False
    expires, nonce, _ = token.split(".")
    if not expires.isdigit() or int(expires) < time.time():
        return False
    return hmac.compare_digest(_sign(int(expires), nonce), token)


_failures: dict = {}


def _client_ip(request: Request) -> str:
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")


def _throttled(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < 300]
    _failures[ip] = recent
    return len(recent) >= 5


class Unauthenticated(Exception):
    pass


def operator(request: Request) -> str:
    if not _valid(request.cookies.get(COOKIE)):
        raise Unauthenticated()
    if request.method not in ("GET", "HEAD") and request.headers.get("x-operator") != "1":
        raise DomainError(403, "FORBIDDEN", "Нет заголовка X-Operator")
    return OPERATOR


# --------------------------------------------------------------------------
# Ошибки и заголовки
# --------------------------------------------------------------------------

@app.middleware("http")
async def headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.exception_handler(DomainError)
async def domain_error(request: Request, exc: DomainError):
    error = {"code": exc.code, "message": exc.message}
    if exc.details:
        error["details"] = exc.details
    return JSONResponse({"error": error}, status_code=exc.status)


@app.exception_handler(Unauthenticated)
async def unauthenticated(request: Request, exc: Unauthenticated):
    return JSONResponse({"error": {"code": "UNAUTHENTICATED", "message": "Войдите в панель заново"}}, status_code=401)


def respond(result: Result) -> Response:
    headers = {}
    if result.etag:
        headers["ETag"] = result.etag
    if result.status == 204:
        return Response(status_code=204, headers=headers)
    return JSONResponse(result.body, status_code=result.status, headers=headers)


def run(db: Session, fn: Callable[[], Result]) -> Response:
    try:
        return respond(fn())
    except DomainError:
        db.rollback()
        raise


# --------------------------------------------------------------------------
# Контексты оператора для существующей бизнес-логики
# --------------------------------------------------------------------------

class OperatorActor(ActorContext):
    """Оператор внутри вуза: все права владельца, в журнале — actor_kind=operator.

    Сверх владельца оператор выдаёт ключ администрированию вуза и меняет его адреса.
    """
    operator = True

    def audit(self, db, action, target_type, target_id, details=None, outcome="success", error_code=None):
        return registry.audit(db, scope="institution", action=action, actor_user_id=None, actor_kind="operator",
                              institution_id=self.institution_id, target_type=target_type, target_id=target_id,
                              details=details, outcome=outcome, error_code=error_code)


class OperatorStaff(platform_support.StaffContext):
    def audit(self, db, action, target_type, target_id, details=None, institution_id=None, outcome="success",
              error_code=None):
        return registry.audit(db, scope="platform", action=action, actor_user_id=None, actor_kind="operator",
                              institution_id=institution_id, target_type=target_type, target_id=target_id,
                              details=details, outcome=outcome, error_code=error_code)


STAFF = OperatorStaff(user_id=OPERATOR, role="operator", request_id=None)


def actor(db: Session, institution_id: str) -> OperatorActor:
    if not is_uuid(institution_id) or not db.get(Institution, institution_id):
        raise not_found("Вуз не найден")
    admin = registry.admin_service_of(db, institution_id)
    if not admin:
        admin = registry.create_admin_instance(db, institution_id)
        db.commit()
    return OperatorActor(institution_id=institution_id, admin_service_id=admin.id, actor_id=OPERATOR,
                         roles=[catalog.OWNER_ROLE], permissions=list(catalog.ADMIN_PERMISSIONS), credential_id=None,
                         request_id=None)


def _names(db: Session) -> dict:
    """Имя пользователя из последней его заявки на вступление."""
    return {r.user_id: r.full_name for r in db.query(JoinRequest).order_by(JoinRequest.created_at.asc())}


def _inst_name(inst: Institution) -> str:
    return (inst.titles or {}).get("ru") or "ВУЗ"


# --------------------------------------------------------------------------
# Вход
# --------------------------------------------------------------------------

@app.get("/api/session")
def session_info(request: Request):
    return {"authenticated": _valid(request.cookies.get(COOKIE)), "configured": len(_password()) >= MIN_PASSWORD}


@app.post("/api/login")
def login(request: Request, payload: Any = Body(None)):
    if len(_password()) < MIN_PASSWORD:
        raise DomainError(503, "NOT_CONFIGURED",
                          f"Задайте OPERATOR_PASSWORD (не короче {MIN_PASSWORD} символов) в .env и перезапустите operator")
    ip = _client_ip(request)
    if _throttled(ip):
        raise DomainError(429, "RATE_LIMITED", "Слишком много попыток. Подождите 5 минут")
    password = payload.get("password") if isinstance(payload, dict) else None
    if not isinstance(password, str) or not hmac.compare_digest(password.encode(), _password().encode()):
        _failures.setdefault(ip, []).append(time.time())
        logger.warning("operator login failed from %s", ip)
        raise DomainError(401, "UNAUTHENTICATED", "Неверный пароль")
    _failures.pop(ip, None)
    logger.info("operator login from %s", ip)
    response = JSONResponse({"authenticated": True})
    response.set_cookie(COOKIE, _sign(int(time.time()) + SESSION_TTL, secrets.token_urlsafe(12)), max_age=SESSION_TTL,
                        httponly=True, secure=_secure_cookie(), samesite="strict", path="/")
    return response


@app.post("/api/logout")
def logout():
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(COOKIE, path="/")
    return response


# --------------------------------------------------------------------------
# Сводка
# --------------------------------------------------------------------------

CORE_HEALTH = "http://backend:8000/api/v1/health"
# Раннеры облачных сервисов во внутренней сети compose (имя сервиса → адрес).
RUNNERS = {"administration": os.environ.get("RUNNER_ADMINISTRATION_URL", "http://administration:8000"),
           "schedule": os.environ.get("RUNNER_SCHEDULE_URL", "http://schedule:8000"),
           "user-profile": os.environ.get("RUNNER_USER_PROFILE_URL", "http://user-profile:8000")}


def _health_targets() -> dict:
    """Ядро и каждый включённый сервис каждого вуза.

    Облачный — у раннера его типа по внутренней сети: раннер знает экземпляр (процесс работает или
    спит до первого запроса) — сервис доступен; процесс проверкой не будится. Свой сервис вуза — по его адресу.

    OPERATOR_HEALTH_TARGETS (JSON «имя → URL») заменяет список целиком — для тестов и особых схем.
    """
    if os.environ.get("OPERATOR_HEALTH_TARGETS"):
        return json.loads(os.environ["OPERATOR_HEALTH_TARGETS"])
    targets = {"Ядро": CORE_HEALTH}
    with session_local() as db:
        insts = {i.id: _inst_name(i) for i in db.query(Institution)}
        services = db.query(ServiceInstance).filter(ServiceInstance.enabled.is_(True),
                                                    ServiceInstance.deleted_at.is_(None)).all()
        many = len({s.institution_id for s in services}) > 1
        for s in sorted(services, key=lambda x: (insts.get(x.institution_id, ""), x.service_type)):
            title = (s.manifest or {}).get("titles", {}).get("ru") or s.service_type
            name = f"{insts.get(s.institution_id, 'ВУЗ')} · {title}" if many else title
            if s.deployment == "cloud" and s.service_type in RUNNERS:
                targets[name] = f"{RUNNERS[s.service_type]}/health?service_id={s.id}"
            else:
                targets[name] = s.api_base_url.rstrip("/") + "/health"
    return targets


def _check(item) -> dict:
    name, url = item
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            ok = r.status == 200
    except Exception:
        ok = False
    return {"name": name, "ok": ok, "ms": int((time.monotonic() - started) * 1000)}


def _health() -> list:
    targets = list(_health_targets().items())
    if not targets:
        return []
    with ThreadPoolExecutor(max_workers=min(16, len(targets))) as pool:
        return list(pool.map(_check, targets))


@app.get("/api/overview")
async def overview(_: str = Depends(operator), db: Session = Depends(get_db)):
    counts = {
        "institutions": db.query(Institution).count(),
        "active_institutions": db.query(Institution).filter(Institution.status == "active").count(),
        "users": db.query(User).count(),
        "memberships": db.query(Membership).count(),
        "pending_applications": db.query(InstitutionApplication).filter(InstitutionApplication.status == "pending").count(),
        "pending_join_requests": db.query(JoinRequest).filter(JoinRequest.status == "pending").count(),
        "staff": db.query(PlatformStaff).count(),
    }
    per_inst = dict(db.query(JoinRequest.institution_id, func.count()).filter(JoinRequest.status == "pending")
                    .group_by(JoinRequest.institution_id).all())
    attention = []
    for inst in db.query(Institution).all():
        admin = registry.admin_service_of(db, inst.id)
        owners = len(registry.owner_ids(db, inst.id, admin.id)) if admin else 0
        if not owners:
            attention.append({"institution_id": inst.id, "name": _inst_name(inst), "issue": "no_owner"})
        if per_inst.get(inst.id):
            attention.append({"institution_id": inst.id, "name": _inst_name(inst), "issue": "join_requests",
                              "count": per_inst[inst.id]})
    return {"counts": counts, "attention": attention, "health": await run_in_threadpool(_health)}


# --------------------------------------------------------------------------
# Заявки на подключение вузов
# --------------------------------------------------------------------------

@app.get("/api/applications")
def applications(status: Optional[str] = Query(None), _: str = Depends(operator), db: Session = Depends(get_db)):
    query = db.query(InstitutionApplication)
    if status:
        query = query.filter(InstitutionApplication.status == status)
    names = _names(db)
    items = []
    for a in query.order_by(InstitutionApplication.created_at.desc()).limit(300):
        items.append({**platform_support.application_view(a), "applicant_name": names.get(a.applicant_user_id)})
    return {"items": items, "next_cursor": None}


@app.post("/api/applications/{application_id}/approve")
def approve_application(application_id: str, payload: Any = Body(None), _: str = Depends(operator),
                        db: Session = Depends(get_db)):
    return run(db, lambda: platform_support.approve_application(db, STAFF, application_id, payload))


@app.post("/api/applications/{application_id}/reject")
def reject_application(application_id: str, payload: Any = Body(None), _: str = Depends(operator),
                       db: Session = Depends(get_db)):
    return run(db, lambda: platform_support.reject_application(db, STAFF, application_id, payload))


# --------------------------------------------------------------------------
# Вузы: уровень платформы
# --------------------------------------------------------------------------

def _institution_card(db: Session, inst: Institution, pending: dict) -> dict:
    view = platform_support.institution_view(db, inst)
    view["pending_join_requests"] = pending.get(inst.id, 0)
    view["services"] = [{"id": s.id, "service_type": s.service_type, "enabled": bool(s.enabled),
                         "deployment": s.deployment,
                         "title": (s.manifest or {}).get("titles", {}).get("ru", s.service_type)}
                        for s in db.query(ServiceInstance).filter(ServiceInstance.institution_id == inst.id,
                                                                  ServiceInstance.deleted_at.is_(None))]
    return view


@app.get("/api/institutions")
def institutions(_: str = Depends(operator), db: Session = Depends(get_db)):
    pending = dict(db.query(JoinRequest.institution_id, func.count()).filter(JoinRequest.status == "pending")
                   .group_by(JoinRequest.institution_id).all())
    rows = sorted(db.query(Institution).all(), key=lambda i: _inst_name(i).casefold())
    return {"items": [_institution_card(db, i, pending) for i in rows], "next_cursor": None}


@app.post("/api/institutions")
def create_institution(payload: Any = Body(None), _: str = Depends(operator), db: Session = Depends(get_db)):
    """Подключить вуз без заявки: название, язык, по желанию — владелец."""
    def create():
        body = _body(payload, {"titles", "default_locale", "owner_user_id"}, {"titles"})
        titles = catalog.check_localized(body["titles"], "titles")
        locale = catalog.check_locale(body.get("default_locale", "ru"))
        owner = body.get("owner_user_id")
        if owner is not None and (not is_uuid(owner) or not db.get(User, owner)):
            raise validation("Владелец — пользователь, который хотя бы раз входил через MAX", "owner_user_id")
        inst = registry.provision_institution(db, titles, locale)
        registry.audit(db, scope="institution", action="institution.provision", actor_user_id=None,
                       actor_kind="operator", institution_id=inst.id, target_type="institution", target_id=inst.id,
                       details={"titles": titles})
        if owner:
            registry.assign_owner(db, inst.id, owner)
            registry.audit(db, scope="institution", action="owner.initial_assign", actor_user_id=None,
                           actor_kind="operator", institution_id=inst.id, target_type="member", target_id=owner)
        db.commit()
        return Result(_institution_card(db, inst, {}), status=201)
    return run(db, create)


@app.get("/api/institutions/{institution_id}")
def institution(institution_id: str, _: str = Depends(operator), db: Session = Depends(get_db)):
    inst = platform_support._institution(db, institution_id)
    pending = {inst.id: db.query(JoinRequest).filter(JoinRequest.institution_id == inst.id,
                                                     JoinRequest.status == "pending").count()}
    return JSONResponse(_institution_card(db, inst, pending),
                        headers={"ETag": platform_support._etag(db, inst)})


@app.delete("/api/institutions/{institution_id}")
def delete_institution(institution_id: str, payload: Any = Body(None), _: str = Depends(operator),
                       db: Session = Depends(get_db)):
    """Удалить вуз полностью: данные в ядре, процессы и данные облачных сервисов вуза в раннерах."""
    def remove():
        result = platform_support.delete_institution(db, STAFF, institution_id, payload)
        db.commit()
        return result
    return run(db, remove)


@app.patch("/api/institutions/{institution_id}/status")
def set_status(institution_id: str, payload: Any = Body(None), if_match: Optional[str] = Header(None, alias="If-Match"),
               _: str = Depends(operator), db: Session = Depends(get_db)):
    return run(db, lambda: platform_support.set_status(db, STAFF, institution_id, payload, if_match))


@app.put("/api/institutions/{institution_id}/local-hosts")
def local_hosts(institution_id: str, payload: Any = Body(None), if_match: Optional[str] = Header(None, alias="If-Match"),
                _: str = Depends(operator), db: Session = Depends(get_db)):
    return run(db, lambda: platform_support.replace_local_hosts(db, STAFF, institution_id, payload, if_match))


@app.post("/api/institutions/{institution_id}/owners")
def add_owner(institution_id: str, payload: Any = Body(None), _: str = Depends(operator), db: Session = Depends(get_db)):
    """Сделать пользователя владельцем вуза (профиль admin + роль owner), даже если владелец уже есть."""
    def assign():
        body = _body(payload, {"user_id"}, {"user_id"})
        user_id = body["user_id"]
        if not is_uuid(user_id) or not db.get(User, user_id):
            raise DomainError(404, "RESOURCE_NOT_FOUND", "Пользователь не найден: он должен войти через MAX")
        inst = registry.lock_institution(db, platform_support._institution(db, institution_id).id)
        registry.assign_owner(db, inst.id, user_id)
        registry.audit(db, scope="institution", action="owner.initial_assign", actor_user_id=None,
                       actor_kind="operator", institution_id=inst.id, target_type="member", target_id=user_id)
        db.commit()
        return Result({"institution_id": inst.id, "user_id": user_id})
    return run(db, assign)


# --------------------------------------------------------------------------
# Внутри вуза: всё, что умеет админка владельца (services/institution_admin.py)
# --------------------------------------------------------------------------

U = r"(?P<{}>[0-9a-f-]{{36}})"
ROUTES = [
    ("GET", r"", lambda db, c, m, p, e, q: svc.get_institution(db, c)),
    ("PATCH", r"", lambda db, c, m, p, e, q: svc.patch_institution(db, c, p, e)),
    ("GET", r"/service-types", lambda db, c, m, p, e, q: svc.list_service_types(c)),
    ("GET", r"/audit", lambda db, c, m, p, e, q: svc.list_audit(db, c, q.get("limit"), q.get("cursor"))),
    ("GET", r"/members", lambda db, c, m, p, e, q: svc.list_members(db, c, q.get("limit"), q.get("cursor"))),
    ("POST", r"/members", lambda db, c, m, p, e, q: svc.add_member(db, c, p)),
    ("GET", r"/members/" + U.format("u"), lambda db, c, m, p, e, q: svc.get_member(db, c, m["u"])),
    ("DELETE", r"/members/" + U.format("u"), lambda db, c, m, p, e, q: svc.remove_member(db, c, m["u"], e)),
    ("PUT", r"/members/" + U.format("u") + r"/profiles", lambda db, c, m, p, e, q: svc.replace_profiles(db, c, m["u"], p, e)),
    ("PUT", r"/members/" + U.format("u") + r"/group", lambda db, c, m, p, e, q: svc.set_member_group(db, c, m["u"], p)),
    ("GET", r"/groups", lambda db, c, m, p, e, q: svc.list_groups(db, c, q.get("limit"), q.get("cursor"))),
    ("POST", r"/groups", lambda db, c, m, p, e, q: svc.create_group(db, c, p)),
    ("GET", r"/groups/" + U.format("g"), lambda db, c, m, p, e, q: svc.get_group(db, c, m["g"])),
    ("PATCH", r"/groups/" + U.format("g"), lambda db, c, m, p, e, q: svc.rename_group(db, c, m["g"], p, e)),
    ("DELETE", r"/groups/" + U.format("g"), lambda db, c, m, p, e, q: svc.delete_group(db, c, m["g"], e)),
    ("GET", r"/groups/" + U.format("g") + r"/members", lambda db, c, m, p, e, q: svc.get_group_members(db, c, m["g"])),
    ("PUT", r"/groups/" + U.format("g") + r"/members", lambda db, c, m, p, e, q: svc.replace_group_members(db, c, m["g"], p, e)),
    ("GET", r"/join-requests", lambda db, c, m, p, e, q: join_requests.list_for_institution(db, c, q.get("status"))),
    ("POST", r"/join-requests/" + U.format("r") + r"/approve", lambda db, c, m, p, e, q: join_requests.approve(db, c, m["r"])),
    ("POST", r"/join-requests/" + U.format("r") + r"/reject", lambda db, c, m, p, e, q: join_requests.reject(db, c, m["r"], p)),
    ("GET", r"/services", lambda db, c, m, p, e, q: svc.list_services(db, c, q.get("limit"), q.get("cursor"))),
    ("POST", r"/services", lambda db, c, m, p, e, q: svc.install_service(db, c, p, q.get("idempotency_key"))),
    ("GET", r"/services/" + U.format("s"), lambda db, c, m, p, e, q: svc.get_service(db, c, m["s"])),
    ("PATCH", r"/services/" + U.format("s"), lambda db, c, m, p, e, q: svc.patch_service(db, c, m["s"], p, e)),
    ("DELETE", r"/services/" + U.format("s"), lambda db, c, m, p, e, q: svc.uninstall_service(db, c, m["s"], e)),
    ("PUT", r"/services/" + U.format("s") + r"/manifest", lambda db, c, m, p, e, q: svc.replace_manifest(db, c, m["s"], p, e)),
    ("GET", r"/services/" + U.format("s") + r"/roles", lambda db, c, m, p, e, q: svc.list_roles(db, c, m["s"], q.get("limit"), q.get("cursor"))),
    ("POST", r"/services/" + U.format("s") + r"/roles", lambda db, c, m, p, e, q: svc.create_role(db, c, m["s"], p)),
    ("GET", r"/services/" + U.format("s") + r"/roles/(?P<code>[a-z][a-z0-9_.-]{0,63})", lambda db, c, m, p, e, q: svc.get_role(db, c, m["s"], m["code"])),
    ("PATCH", r"/services/" + U.format("s") + r"/roles/(?P<code>[a-z][a-z0-9_.-]{0,63})", lambda db, c, m, p, e, q: svc.patch_role(db, c, m["s"], m["code"], p, e)),
    ("DELETE", r"/services/" + U.format("s") + r"/roles/(?P<code>[a-z][a-z0-9_.-]{0,63})", lambda db, c, m, p, e, q: svc.delete_role(db, c, m["s"], m["code"], e)),
    ("GET", r"/services/" + U.format("s") + r"/users/" + U.format("u") + r"/profiles/(?P<p>admin|teacher|student)/roles",
     lambda db, c, m, p, e, q: svc.get_assignments(db, c, m["s"], m["u"], m["p"])),
    ("PUT", r"/services/" + U.format("s") + r"/users/" + U.format("u") + r"/profiles/(?P<p>admin|teacher|student)/roles",
     lambda db, c, m, p, e, q: svc.replace_assignments(db, c, m["s"], m["u"], m["p"], p, e)),
    ("GET", r"/services/" + U.format("s") + r"/credentials", lambda db, c, m, p, e, q: svc.list_credentials(db, c, m["s"], q.get("limit"), q.get("cursor"))),
    ("POST", r"/services/" + U.format("s") + r"/credentials", lambda db, c, m, p, e, q: svc.issue_credential(db, c, m["s"])),
    ("DELETE", r"/services/" + U.format("s") + r"/credentials/" + U.format("cr"), lambda db, c, m, p, e, q: svc.revoke_credential(db, c, m["s"], m["cr"])),
]
COMPILED = [(method, re.compile(pattern + r"$"), fn) for method, pattern, fn in ROUTES]


@app.api_route("/api/institutions/{institution_id}/manage{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def manage(institution_id: str, rest: str, request: Request, _: str = Depends(operator)):
    for method, pattern, fn in COMPILED:
        match = pattern.match(rest)
        if match and method == request.method:
            break
    else:
        raise not_found("Операция не найдена")
    raw = await request.body()
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        raise validation("Тело запроса — некорректный JSON", "body")
    query = {k: v for k, v in request.query_params.items() if k in ("limit", "cursor", "status")}
    if "limit" in query:
        query["limit"] = int(query["limit"]) if query["limit"].isdigit() else query["limit"]
    query["idempotency_key"] = request.headers.get("idempotency-key")
    if_match = request.headers.get("if-match")

    def call():
        with session_local() as db:
            ctx = actor(db, institution_id)
            return run(db, lambda: fn(db, ctx, match.groupdict(), payload, if_match, query))
    return await run_in_threadpool(call)


# --------------------------------------------------------------------------
# Пользователи и поддержка платформы
# --------------------------------------------------------------------------

@app.get("/api/users")
def users(q: str = Query("", max_length=200), _: str = Depends(operator), db: Session = Depends(get_db)):
    names = _names(db)
    insts = {i.id: _inst_name(i) for i in db.query(Institution)}
    memberships = {}
    for m in db.query(Membership):
        memberships.setdefault(m.user_id, []).append({"institution_id": m.institution_id,
                                                      "name": insts.get(m.institution_id, "ВУЗ"),
                                                      "profiles": list(m.profiles or [])})
    staff = {s.user_id for s in db.query(PlatformStaff)}
    needle = q.strip().casefold()
    items = []
    for u in db.query(User).order_by(User.created_at.desc()):
        item = {"id": u.id, "max_user_id": u.max_user_id, "created_at": registry.iso(u.created_at),
                "name": names.get(u.id), "memberships": memberships.get(u.id, []), "staff": u.id in staff}
        haystack = " ".join([u.id, str(u.max_user_id or ""), item["name"] or "",
                             *[m["name"] for m in item["memberships"]]]).casefold()
        if needle and needle not in haystack:
            continue
        items.append(item)
        if len(items) >= 500:
            break
    return {"items": items, "next_cursor": None}


@app.post("/api/users/{user_id}/staff")
def grant_staff(user_id: str, _: str = Depends(operator), db: Session = Depends(get_db)):
    def grant():
        if not is_uuid(user_id):
            raise not_found("Пользователь не найден")
        granted = platform_ops.grant_staff(db, user_id, "operator-panel")
        db.commit()
        return Result({"staff": True, "changed": granted})
    return run(db, grant)


@app.delete("/api/users/{user_id}/staff")
def revoke_staff(user_id: str, _: str = Depends(operator), db: Session = Depends(get_db)):
    def revoke():
        changed = platform_ops.revoke_staff(db, user_id)
        db.commit()
        return Result({"staff": False, "changed": changed})
    return run(db, revoke)


@app.delete("/api/users/{user_id}")
def delete_user(user_id: str, _: str = Depends(operator), db: Session = Depends(get_db)):
    def remove():
        if not is_uuid(user_id):
            raise not_found("Пользователь не найден")
        platform_ops.delete_user(db, user_id)
        db.commit()
        return Result({"deleted": True})
    return run(db, remove)


# --------------------------------------------------------------------------
# Журнал и служебные команды
# --------------------------------------------------------------------------

@app.get("/api/audit")
def audit(institution_id: Optional[str] = Query(None), before: Optional[int] = Query(None),
          _: str = Depends(operator), db: Session = Depends(get_db)):
    query = db.query(AuditEvent)
    if institution_id:
        query = query.filter(AuditEvent.institution_id == institution_id)
    if before:
        query = query.filter(AuditEvent.id < before)
    rows = query.order_by(AuditEvent.id.desc()).limit(51).all()
    insts = {i.id: _inst_name(i) for i in db.query(Institution)}
    items = [{**registry.audit_view(r), "institution_name": insts.get(r.institution_id)} for r in rows[:50]]
    return {"items": items, "next_before": rows[49].id if len(rows) > 50 else None}


@app.post("/api/tools/{tool}")
def tool(tool: str, payload: Any = Body(None), _: str = Depends(operator), db: Session = Depends(get_db)):
    """Команды manage.py, которые раньше запускались через docker compose exec."""
    from platform_core.service_files import export_services
    if tool == "ensure-invariants":
        registry.ensure_platform_invariants(db)
        db.commit()
        message = "Инварианты платформы проверены: у каждого вуза есть администрирование, адреса и ключи сервисов актуальны."
    elif tool == "export-services":
        try:
            export_services(session_local)
        except OSError as error:
            raise DomainError(500, "EXPORT_FAILED", f"Не удалось записать файлы: {error}")
        message = "Настройки подключённых сервисов выгружены в services/connected."
    elif tool == "import-groups":
        import manage
        if not isinstance(payload, dict) or not isinstance(payload.get("groups"), list):
            raise validation("Нужен JSON вида {\"groups\": [...]} из app.export_groups сервиса расписания", "groups")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            manage.import_groups(io.StringIO(json.dumps(payload)))
        message = out.getvalue().strip()
    else:
        raise not_found("Команда не найдена")
    return {"message": message}


# --------------------------------------------------------------------------
# Интерфейс
# --------------------------------------------------------------------------

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


@app.get("/api/v1/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"), headers={"Content-Security-Policy": CSP})


@app.get("/static/{name}")
def static(name: str):
    path = STATIC / name
    if not re.fullmatch(r"[a-z]+\.(js|css)", name) or not path.is_file():
        return Response(status_code=404)
    media = "text/javascript" if name.endswith(".js") else "text/css"
    return FileResponse(path, media_type=f"{media}; charset=utf-8", headers={"Cache-Control": "no-cache"})
