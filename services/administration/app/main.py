"""Облачный сервис «Администрирование».

Один процесс на все вузы. Браузер обращается только к публичному фасаду
/api/v1/administration/...; backend сам добавляет machine credential своего
binding и X-Actor-Token и вызывает private API ядра. Произвольного proxy нет:
каждая операция объявлена явно, параметры пути проверяются до обращения к ядру.
"""
import hashlib
from html import escape as html_escape
import json
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import jwt
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app import config as config_module
from app.core_client import BindingMissing, CoreClient, CoreUnavailable

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("administration")

CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
PROFILES = ("admin", "teacher", "student")
MAX_BODY = 64 * 1024
FACADE = "/api/v1/administration"

app = FastAPI(title="Administration service", docs_url=None, redoc_url=None, openapi_url=None)
_state = {}


def cfg() -> config_module.Config:
    if "config" not in _state:
        _state["config"] = config_module.load()
    return _state["config"]


def core() -> CoreClient:
    if "core" not in _state:
        c = cfg()
        _state["core"] = CoreClient(c.core_url, c.client_id, c.client_secret, c.public_api_base_url,
                                    c.public_client_base_url, c.core_timeout)
    return _state["core"]


# --------------------------------------------------------------------------
# Ошибки и заголовки
# --------------------------------------------------------------------------

class FacadeError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: Optional[dict] = None):
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


def error_response(request: Request, status: int, code: str, message: str, details=None, headers=None) -> JSONResponse:
    error = {"code": code, "message": message, "request_id": request.state.request_id}
    if details:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status, headers=headers or {})


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(FacadeError)
async def facade_error(request: Request, exc: FacadeError):
    return error_response(request, exc.status, exc.code, exc.message, headers=exc.headers)


@app.exception_handler(CoreUnavailable)
async def core_unavailable(request: Request, exc: CoreUnavailable):
    logger.warning("request %s: core unavailable: %s", request.state.request_id, exc)
    return error_response(request, 503, "SERVICE_UNAVAILABLE", "Ядро платформы временно недоступно")


@app.exception_handler(Exception)
async def unexpected(request: Request, exc: Exception):
    logger.exception("request %s failed", getattr(request.state, "request_id", "-"))
    return JSONResponse({"error": {"code": "INTERNAL_ERROR", "message": "Внутренняя ошибка",
                                   "request_id": getattr(request.state, "request_id", str(uuid.uuid4()))}},
                        status_code=500)


# --------------------------------------------------------------------------
# Аутентификация запроса (SDK §4.2)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Context:
    institution_id: str
    service_id: str
    user_id: str
    profile: str
    roles: List[str]
    permissions: List[str]
    token: str
    binding: object
    request_id: str


def authenticate(request: Request) -> Context:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise FacadeError(401, "UNAUTHENTICATED", "Нужен service access", {"WWW-Authenticate": "Bearer"})
    token = token.strip()
    try:
        # Unverified claims only select an existing binding. Core introspection
        # verifies RS256, issuer, audience and live state on every request.
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": True})
    except jwt.PyJWTError:
        raise FacadeError(401, "UNAUTHENTICATED", "Сессия недействительна или истекла")
    service_id, institution_id = claims.get("service_id"), claims.get("institution_id")
    if (claims.get("token_use") != "service_access" or not UUID_RE.match(str(service_id))
            or not UUID_RE.match(str(institution_id)) or claims.get("aud") != f"service:{service_id}"):
        raise FacadeError(401, "UNAUTHENTICATED", "Нужен service access сервиса администрирования")
    try:
        binding = core().binding(service_id)
    except BindingMissing:
        raise FacadeError(503, "INSTANCE_NOT_READY", "Сервис администрирования для вуза ещё не подключён")
    if binding.institution_id != institution_id:
        raise FacadeError(404, "RESOURCE_NOT_FOUND", "Ресурс не найден")
    info = core().introspect(binding, token)
    if not info.get("active"):
        raise FacadeError(401, "UNAUTHENTICATED", "Сессия завершена. Откройте сервис заново")
    if (info.get("sub") != claims.get("sub") or info.get("service_id") != service_id
            or info.get("institution_id") != institution_id or info.get("profile") != claims.get("profile")
            or info.get("session_id") != claims.get("sid")
            or info.get("parent_session_id") != claims.get("parent_sid")):
        raise FacadeError(401, "UNAUTHENTICATED", "Контекст сессии не совпадает")
    if info.get("profile") != "admin":
        raise FacadeError(403, "FORBIDDEN", "Сервис доступен только в профиле администратора")
    return Context(institution_id, service_id, info["sub"], "admin", list(info.get("roles") or []),
                   list(info.get("permissions") or []), token, binding, request.state.request_id)


# --------------------------------------------------------------------------
# Общие операции SDK
# --------------------------------------------------------------------------

@app.get("/api/v1/health")
def health():
    return {"status": "ok"}


def _text(titles: dict, locale: str):
    if locale in titles:
        return titles[locale], locale
    return titles.get("ru", ""), "ru"


@app.get("/api/v1/service")
def service_view(request: Request, locale: Optional[str] = None):
    if locale not in (None, "ru", "en"):
        raise FacadeError(422, "VALIDATION_ERROR", "locale: ru или en")
    ctx = authenticate(request)
    manifest = core().manifest(ctx.binding)
    locale = locale or "ru"
    menus = []
    for m in sorted(manifest.get("menus") or [], key=lambda m: (m.get("order", 0), m.get("id", ""))):
        if ctx.profile in m.get("profiles", []) and set(m.get("required_permissions", [])) <= set(ctx.permissions):
            name, used = _text(m.get("titles", {}), locale)
            menus.append({"id": m["id"], "display_name": name, "locale": used,
                          "entrypoint_path": m["entrypoint_path"], "order": m.get("order", 0)})
    name, used = _text(manifest.get("titles") or {"ru": "Администрирование"}, locale)
    return {"id": ctx.service_id, "institution_id": ctx.institution_id, "service_type": "administration",
            "deployment": "local", "display_name": name, "locale": used,
            "api_base_url": ctx.binding.api_base_url, "client_base_url": ctx.binding.client_base_url,
            "profile": ctx.profile, "roles": ctx.roles, "permissions": ctx.permissions, "menus": menus}


# --------------------------------------------------------------------------
# Фасад private API (явный список операций)
# --------------------------------------------------------------------------

# (метод, публичный суффикс, пермишен для ранней проверки или None, If-Match, Idempotency-Key, тело)
OPERATIONS = [
    ("GET", "", "institution.read", False, False, False),
    ("PATCH", "", "institution.update", True, False, True),
    ("GET", "/service-types", "institution.read", False, False, False),
    ("GET", "/audit", "institution.read", False, False, False),
    ("GET", "/members", "members.read", False, False, False),
    ("POST", "/members", "members.manage", False, False, True),
    ("GET", "/members/{user_id}", "members.read", False, False, False),
    ("DELETE", "/members/{user_id}", "members.manage", True, False, False),
    ("PUT", "/members/{user_id}/profiles", "members.manage", True, False, True),
    ("PUT", "/members/{user_id}/group", "groups.manage", False, False, True),
    ("GET", "/join-requests", "members.read", False, False, False),
    ("POST", "/join-requests/{request_id}/approve", "members.manage", False, False, False),
    ("POST", "/join-requests/{request_id}/reject", "members.manage", False, False, True),
    ("GET", "/groups", "members.read", False, False, False),
    ("POST", "/groups", "groups.manage", False, False, True),
    ("GET", "/groups/{group_id}", "members.read", False, False, False),
    ("PATCH", "/groups/{group_id}", "groups.manage", True, False, True),
    ("DELETE", "/groups/{group_id}", "groups.manage", True, False, False),
    ("GET", "/groups/{group_id}/members", "members.read", False, False, False),
    ("PUT", "/groups/{group_id}/members", "groups.manage", True, False, True),
    ("GET", "/services", "services.read", False, False, False),
    ("POST", "/services", "services.manage", False, True, True),
    ("GET", "/services/{service_id}", "services.read", False, False, False),
    ("PATCH", "/services/{service_id}", "services.manage", True, False, True),
    ("DELETE", "/services/{service_id}", "services.manage", True, False, False),
    ("PUT", "/services/{service_id}/manifest", "services.manage", True, False, True),
    ("GET", "/services/{service_id}/roles", "services.read", False, False, False),
    ("POST", "/services/{service_id}/roles", "roles.manage", False, False, True),
    ("GET", "/services/{service_id}/roles/{role_code}", "services.read", False, False, False),
    ("PATCH", "/services/{service_id}/roles/{role_code}", "roles.manage", True, False, True),
    ("DELETE", "/services/{service_id}/roles/{role_code}", "roles.manage", True, False, False),
    ("GET", "/services/{service_id}/users/{user_id}/profiles/{profile}/roles", "roles.manage", False, False, False),
    ("PUT", "/services/{service_id}/users/{user_id}/profiles/{profile}/roles", "roles.manage", True, False, True),
    ("GET", "/services/{service_id}/credentials", "credentials.manage", False, False, False),
    ("POST", "/services/{service_id}/credentials", "credentials.manage", False, False, False),
    ("DELETE", "/services/{service_id}/credentials/{credential_id}", "credentials.manage", False, False, False),
]

PARAM_RULES = {
    "user_id": UUID_RE, "service_id": UUID_RE, "request_id": UUID_RE, "credential_id": UUID_RE, "group_id": UUID_RE, "role_code": CODE_RE,
    "profile": re.compile(r"^(admin|teacher|student)$"),
}


def _rewrite_location(value: str, institution_id: str) -> Optional[str]:
    prefix = f"/api/v1/institution/{institution_id}/internal"
    if value.startswith(prefix):
        return FACADE + value[len(prefix):]
    return None


def make_handler(method: str, suffix: str, permission: Optional[str], if_match: bool, idem: bool, has_body: bool):
    async def handler(request: Request):
        params = request.path_params
        for name, value in params.items():
            if not PARAM_RULES[name].match(value):
                raise FacadeError(404, "RESOURCE_NOT_FOUND", "Ресурс не найден")
        body = None
        if has_body:
            body = await request.body()
            if len(body) > MAX_BODY:
                raise FacadeError(413, "BAD_REQUEST", "Слишком большой запрос")
            body = body or b"null"
        return await run_in_threadpool(forward, request, method, suffix.format(**params), permission,
                                       request.headers.get("if-match") if if_match else None,
                                       request.headers.get("idempotency-key") if idem else None, body)
    handler.__name__ = f"{method.lower()}_{re.sub(r'[^a-z]+', '_', suffix) or 'root'}"
    return handler


def forward(request: Request, method: str, suffix: str, permission: Optional[str], if_match: Optional[str],
            idem: Optional[str], body: Optional[bytes]) -> Response:
    ctx = authenticate(request)
    if permission and permission not in ctx.permissions:
        # Ранний понятный отказ; ядро всё равно проверит права заново.
        raise FacadeError(403, "FORBIDDEN", f"Нужно разрешение {permission}")
    query = {k: v for k, v in request.query_params.items() if k in ("limit", "cursor", "status")}
    upstream = core().private(ctx.binding, method, suffix, ctx.token, body=body, query=query, if_match=if_match,
                              idempotency_key=idem, facade_request_id=ctx.request_id)
    headers = {}
    for key, value in upstream.headers.items():
        if key.lower() == "location":
            rewritten = _rewrite_location(value, ctx.institution_id)
            if rewritten:
                headers["Location"] = rewritten
        else:
            headers[key] = value
    if upstream.status == 204:
        return Response(status_code=204, headers=headers)
    data = upstream.body
    if upstream.status >= 400:
        error = dict(data["error"])
        error["request_id"] = ctx.request_id  # upstream ID остаётся в журнале сервера
        data = {"error": error}
    return JSONResponse(data, status_code=upstream.status, headers=headers)


from starlette.concurrency import run_in_threadpool  # noqa: E402

for _method, _suffix, _perm, _if_match, _idem, _body in OPERATIONS:
    app.add_api_route(FACADE + _suffix, make_handler(_method, _suffix, _perm, _if_match, _idem, _body),
                      methods=[_method], include_in_schema=False)


# --------------------------------------------------------------------------
# Клиент: HTML entrypoint и assets
# --------------------------------------------------------------------------

def _asset_versions() -> dict:
    if "assets" not in _state:
        assets = {}
        for path in sorted((CLIENT_DIR / "assets").iterdir()):
            if path.is_file() and path.suffix in (".js", ".css"):
                digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
                assets[path.name] = (f"{path.stem}.{digest}{path.suffix}", path)
        _state["assets"] = assets
    return _state["assets"]


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    c = cfg()
    html = (CLIENT_DIR / "admin.html").read_text(encoding="utf-8")
    for name, (hashed, _) in _asset_versions().items():
        html = html.replace(f"/assets/{name}", f"/assets/{hashed}")
    boot = json.dumps({"shell_origin": c.shell_origin, "api_origin": c.public_origin,
                       "api_base_url": c.public_api_base_url})
    html = html.replace("__BOOT__", html_escape(boot, quote=True))
    csp = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
           f"connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors {' '.join(c.frame_ancestors)}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "Content-Security-Policy": csp})


@app.get("/assets/{asset_name}")
def asset(asset_name: str):
    for hashed, path in _asset_versions().values():
        if hashed == asset_name:
            media = "text/javascript" if path.suffix == ".js" else "text/css"
            return Response(path.read_bytes(), media_type=f"{media}; charset=utf-8",
                            headers={"Cache-Control": "public, max-age=31536000, immutable"})
    return Response(status_code=404)
