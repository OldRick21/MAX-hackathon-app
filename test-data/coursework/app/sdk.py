"""Минимальный Service SDK для своего (локального) сервиса вуза.

Копируйте файл в свой сервис без изменений: доменная логика живёт в main.py.
Правила — docs/services/sdk/SPEC.md (§2 общие маршруты, §3 контекст, §4.2 проверки,
§5 HTTP) и docs/core/CORE_API_SPEC.md §5, §7 (ключ, machine API, manifest).

Что даёт модуль:
- CoreClient — обмен ключа на machine-токен, introspection, профили участника,
  публикация manifest (с If-Match) и создание ролей;
- onboard() — при старте сам создаёт роли и публикует меню, повторяя при сбоях;
- authenticate — зависимость FastAPI: проверенный контекст запроса (вуз, экземпляр,
  пользователь, профиль, актуальные права) или ошибка 401/404/503;
- install(app) — единые ошибки, X-Request-ID, no-store, CORS для оболочки,
  GET /api/v1/health, GET /api/v1/service и раздача клиента для iframe.
"""
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import UUID

import jwt
import requests
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

log = logging.getLogger("service.sdk")


# --------------------------------------------------------------------------
# Настройки: строки, которые выдаёт админка на шаге «Ключ доступа»
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    core_url: str
    shell_origin: str
    client_id: str
    client_secret: str
    api_base_url: str
    client_base_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        values = {name: os.environ.get(env, "").rstrip("/") for name, env in (
            ("core_url", "CORE_URL"), ("shell_origin", "SHELL_ORIGIN"), ("client_id", "SERVICE_CLIENT_ID"),
            ("client_secret", "SERVICE_CLIENT_SECRET"), ("api_base_url", "SERVICE_API_BASE_URL"),
            ("client_base_url", "SERVICE_CLIENT_BASE_URL"))}
        return cls(**values)

    def missing(self) -> list:
        return [k for k, v in self.__dict__.items() if not v]


# --------------------------------------------------------------------------
# Ошибки в формате ядра: {"error": {"code", "message", "request_id", "details"?}}
# --------------------------------------------------------------------------

class Fail(Exception):
    def __init__(self, status: int, code: str, message: str, details: Optional[list] = None, headers=None):
        self.status, self.code, self.message, self.details, self.headers = status, code, message, details, headers


class CoreUnavailable(Exception):
    """Ядро недоступно или ответило неожиданно → 503, без offline-режима."""


# --------------------------------------------------------------------------
# Клиент ядра
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Binding:
    institution_id: str
    service_id: str


class CoreClient:
    TOKEN_MARGIN = 30.0

    def __init__(self, settings: Settings, timeout: float = 3.0, session=None):
        self.s = settings
        self.timeout = timeout
        self.http = session or requests.Session()
        self._token: Optional[tuple] = None
        self._binding: Optional[Binding] = None
        self._lock = threading.Lock()

    def _call(self, method, path, headers=None, **kwargs):
        try:
            return self.http.request(method, self.s.core_url + path, headers=headers, timeout=self.timeout,
                                     allow_redirects=False, **kwargs)
        except requests.RequestException as error:
            raise CoreUnavailable(f"core transport error: {type(error).__name__}")

    @staticmethod
    def _json(response) -> dict:
        try:
            data = response.json()
        except ValueError:
            raise CoreUnavailable("core returned non-JSON")
        if not isinstance(data, dict):
            raise CoreUnavailable("core returned unexpected JSON")
        return data

    def machine_token(self, force=False) -> str:
        """Обмен ключа на machine-токен (5 минут); кэш до exp − 30 с, обмены объединяются."""
        with self._lock:
            if self._token and not force and self._token[0] - self.TOKEN_MARGIN > time.time():
                return self._token[1]
            response = self._call("POST", "/api/v1/internal/auth/token", json={"grant_type": "client_credentials"},
                                  auth=(self.s.client_id, self.s.client_secret))
            if response.status_code != 200:
                self._token = None
                raise CoreUnavailable(f"machine exchange failed: {response.status_code}")
            data = self._json(response)
            binding = Binding(str(data.get("institution_id")), str(data.get("service_id")))
            if self._binding and self._binding != binding:
                raise CoreUnavailable("credential belongs to another service instance")
            self._binding = binding
            self._token = (time.time() + float(data.get("expires_in", 300)), data["access_token"])
            return self._token[1]

    def binding(self) -> Binding:
        """Вуз и экземпляр сервиса берутся из ответа ядра на обмен ключа, а не из настроек."""
        if self._binding is None:
            self.machine_token()
        return self._binding

    def machine(self, method, path, headers=None, **kwargs):
        """Запрос с machine-токеном; на 401 — один новый обмен и один повтор."""
        response = None
        for attempt in (0, 1):
            token = self.machine_token(force=attempt == 1)
            response = self._call(method, path, headers={**(headers or {}), "Authorization": f"Bearer {token}"}, **kwargs)
            if response.status_code != 401:
                return response
        return response

    def introspect(self, service_token: str) -> dict:
        response = self.machine("POST", "/api/v1/internal/auth/introspect", json={"token": service_token})
        if response.status_code != 200:
            raise CoreUnavailable(f"introspection failed: {response.status_code}")
        return self._json(response)

    def profiles(self, user_id: str) -> list:
        """Профили участника в вузе этого сервиса; [] — не участник."""
        response = self.machine("GET", f"/api/v1/internal/service/{self.binding().service_id}/users/{user_id}/profiles")
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            raise CoreUnavailable("membership lookup failed")
        return list(self._json(response).get("profiles") or [])

    def manifest(self) -> tuple:
        response = self.machine("GET", f"/api/v1/internal/service/{self.binding().service_id}/manifest")
        if response.status_code != 200:
            raise CoreUnavailable(f"manifest read failed: {response.status_code}")
        return self._json(response), response.headers.get("ETag")

    def publish_manifest(self, manifest: dict, etag: Optional[str]) -> None:
        response = self.machine("PUT", f"/api/v1/internal/service/{self.binding().service_id}/manifest",
                                headers={"If-Match": etag or ""}, json=manifest)
        if response.status_code != 200:
            raise CoreUnavailable(f"manifest publish failed: {response.status_code} {response.text[:200]}")

    def ensure_role(self, role: dict) -> None:
        path = f"/api/v1/internal/service/{self.binding().service_id}/roles"
        if self.machine("GET", f"{path}/{role['code']}").status_code == 200:
            return
        response = self.machine("POST", path, json=role)
        if response.status_code not in (200, 201, 409):
            raise CoreUnavailable(f"role create failed: {response.status_code} {response.text[:200]}")


def onboard(core: CoreClient, manifest: dict, roles: list, state: dict) -> None:
    """Роли и меню публикует сам сервис (CORE_API_SPEC §7). Повторы с паузой до 5 минут."""
    delay = 5
    while True:
        try:
            for role in roles:
                core.ensure_role(role)
            current, etag = core.manifest()
            if current != manifest:
                core.publish_manifest(manifest, etag)
            state.update(onboarding="ready", error=None)
            log.info("onboarding complete")
            return
        except CoreUnavailable as error:
            state.update(onboarding="waiting", error=str(error))
            log.warning("onboarding failed, retry in %ss: %s", delay, error)
        time.sleep(delay)
        delay = min(delay * 2, 300)


# --------------------------------------------------------------------------
# Контекст запроса
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Ctx:
    """Неизменяемый контекст одного запроса (SDK SPEC §3). Все данные храните с tenant."""
    institution_id: str
    service_id: str
    user_id: str
    profile: str
    permissions: frozenset
    roles: tuple

    @property
    def tenant(self) -> tuple:
        return (self.institution_id, self.service_id)

    def require(self, *, profiles=None, permission=None):
        """Профиль и право операции (403). Объектные проверки — в самом обработчике (404)."""
        if profiles and self.profile not in profiles:
            raise Fail(403, "FORBIDDEN", "Операция недоступна этому профилю")
        if permission and permission not in self.permissions:
            raise Fail(403, "FORBIDDEN", f"Нужно право {permission}")


def make_authenticate(core: CoreClient):
    """Зависимость FastAPI: service access → introspection в ядре → Ctx (SDK SPEC §4.2)."""

    def authenticate(request: Request) -> Ctx:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token or len(token) > 16384:
            raise Fail(401, "UNAUTHENTICATED", "Требуется сессия сервиса")
        try:
            # Без проверки подписи: поля нужны только для сверки, решение принимает introspection ядра.
            claims = jwt.decode(token, options={"verify_signature": False})
            service_id = str(UUID(claims["service_id"]))
            if claims.get("token_use") != "service_access" or claims.get("aud") != f"service:{service_id}":
                raise ValueError()
        except (jwt.PyJWTError, ValueError, KeyError, TypeError):
            raise Fail(401, "UNAUTHENTICATED", "Недействительная сессия")
        binding = core.binding()
        if service_id != binding.service_id:
            raise Fail(404, "RESOURCE_NOT_FOUND", "Сервис недоступен для этой сессии")
        info = core.introspect(token)
        if (not info.get("active") or info.get("service_id") != service_id
                or info.get("institution_id") != binding.institution_id
                or info.get("sub") != claims.get("sub") or info.get("profile") != claims.get("profile")
                or info.get("session_id") != claims.get("sid")
                or info.get("parent_session_id") != claims.get("parent_sid")
                or info.get("profile") not in ("student", "teacher", "admin")):
            raise Fail(401, "UNAUTHENTICATED", "Сессия завершена")
        return Ctx(binding.institution_id, binding.service_id, info["sub"], info["profile"],
                   frozenset(info.get("permissions") or []), tuple(info.get("roles") or []))

    return authenticate


def etag_of(ctx: Ctx, kind: str, object_id: str, revision: int) -> str:
    """Strong ETag: tenant + агрегат + revision (SDK SPEC §5)."""
    raw = json.dumps([kind, *ctx.tenant, object_id, revision])
    return '"' + hashlib.sha256(raw.encode()).hexdigest()[:40] + '"'


def check_if_match(if_match: Optional[str], current: str) -> None:
    if not if_match:
        raise Fail(428, "PRECONDITION_REQUIRED", "Сначала загрузите актуальную версию")
    if if_match != current:
        raise Fail(412, "PRECONDITION_FAILED", "Данные успели измениться. Обновите страницу")


# --------------------------------------------------------------------------
# Общие маршруты и HTTP-правила
# --------------------------------------------------------------------------

def install(app: FastAPI, core: CoreClient, settings: Settings, manifest: dict, service_type: str,
            state: dict, client_dir: Path, entrypoints: list) -> None:
    app.add_middleware(
        CORSMiddleware, allow_origins=[settings.shell_origin, settings.client_base_url], allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "If-Match", "Idempotency-Key"],
        expose_headers=["ETag", "Location", "X-Request-ID", "Retry-After", "Content-Disposition"], max_age=600)

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def body(request, code, message, details=None):
        error = {"code": code, "message": message, "request_id": request.state.request_id}
        if details:
            error["details"] = details[:100]
        return {"error": error}

    @app.exception_handler(Fail)
    async def failed(request, exc: Fail):
        return JSONResponse(body(request, exc.code, exc.message, exc.details), status_code=exc.status, headers=exc.headers)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        codes = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "RESOURCE_NOT_FOUND"}
        return JSONResponse(body(request, codes.get(exc.status_code, "BAD_REQUEST"), str(exc.detail)),
                            status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        details = [{"path": ".".join(str(x) for x in e.get("loc", ())), "message": e.get("msg", "")} for e in exc.errors()]
        return JSONResponse(body(request, "VALIDATION_ERROR", "Проверьте заполненные поля", details), status_code=422)

    @app.exception_handler(CoreUnavailable)
    async def unavailable(request, exc):
        return JSONResponse(body(request, "SERVICE_UNAVAILABLE", "Ядро временно недоступно"), status_code=503)

    authenticate = make_authenticate(core)

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "onboarding": state.get("onboarding", "pending")}

    @app.get("/api/v1/service")
    def service_view(locale: Optional[str] = Query(None, max_length=8), ctx: Ctx = Depends(authenticate)):
        """ServiceView: меню, отфильтрованные по профилю и всем required_permissions (SDK SPEC §2)."""
        if locale not in (None, "ru", "en"):
            raise Fail(422, "VALIDATION_ERROR", "locale: ru или en")
        current, _ = core.manifest()
        locale = locale or "ru"
        text = lambda titles: (titles[locale], locale) if locale in titles else (titles.get("ru", ""), "ru")
        menus = []
        for m in sorted(current.get("menus") or [], key=lambda m: (m.get("order", 0), m.get("id", ""))):
            if ctx.profile in m.get("profiles", []) and set(m.get("required_permissions") or []) <= ctx.permissions:
                name, used = text(m.get("titles") or {})
                menus.append({"id": m["id"], "display_name": name, "locale": used,
                              "entrypoint_path": m["entrypoint_path"], "order": m.get("order", 0)})
        name, used = text(current.get("titles") or manifest["titles"])
        return {"id": ctx.service_id, "institution_id": ctx.institution_id, "service_type": service_type,
                "deployment": "local", "display_name": name, "locale": used, "api_base_url": settings.api_base_url,
                "client_base_url": settings.client_base_url, "profile": ctx.profile, "roles": list(ctx.roles),
                "permissions": sorted(ctx.permissions), "menus": menus}

    # HTML клиента: без токенов и данных; в iframe пускаем только оболочку и MAX Web (SDK SPEC §6–7).
    page = (client_dir / "index.html").read_text(encoding="utf-8")
    boot = json.dumps({"shell_origin": settings.shell_origin, "api_base_url": settings.api_base_url})
    page = page.replace("__BOOT__", boot.replace('"', "&quot;"))
    frame_policy = f"frame-ancestors {settings.shell_origin} https://web.max.ru"

    def serve_page():
        return HTMLResponse(page, headers={"Content-Security-Policy": frame_policy, "Cache-Control": "no-store",
                                           "X-Content-Type-Options": "nosniff"})

    for path in entrypoints:
        app.add_api_route(path, serve_page, methods=["GET"], include_in_schema=False)

    @app.get("/assets/{name}", include_in_schema=False)
    def asset(name: str):
        target = (client_dir / "assets" / name).resolve()
        if target.parent != (client_dir / "assets").resolve() or not target.is_file():
            raise Fail(404, "RESOURCE_NOT_FOUND", "Файл не найден")
        return FileResponse(target, headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})

    app.state.authenticate = authenticate
