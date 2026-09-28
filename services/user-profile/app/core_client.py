"""Клиент ядра для процесса user-profile (часть Service SDK).

Без FastAPI: тестируется против заглушки ядра (tests/test_profiles.py).
Правила — docs/services/sdk/SPEC.md §4.2 и docs/services/user-profile/SPEC.md §2.
"""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger("user-profile.core")


class CoreUnavailable(Exception):
    """Сбой транспорта, machine-конфигурации или неожиданный ответ ядра → 503."""


class BindingMissing(Exception):
    """Binding для экземпляра не зарегистрирован → 503 INSTANCE_NOT_READY."""


@dataclass(frozen=True)
class Binding:
    institution_id: str
    service_id: str
    service_type: str
    api_base_url: str
    client_base_url: str
    client_id: str
    credential_id: str
    client_secret: str
    revision: int


class CoreClient:
    BINDING_TTL = 30.0
    TOKEN_MARGIN = 30.0

    def __init__(self, base_url: str, provisioning_token: str, timeout: float = 3.0, session=None):
        self.base_url = base_url.rstrip("/")
        self.provisioning_token = provisioning_token
        self.timeout = timeout
        self.http = session or requests.Session()
        self._bindings: Dict[str, Tuple[float, Binding]] = {}
        self._tokens: Dict[Tuple[str, int], Tuple[float, str]] = {}
        self._locks: Dict[Tuple[str, int], threading.Lock] = {}
        self._guard = threading.Lock()

    # --- HTTP ---

    def _call(self, method: str, path: str, **kwargs) -> requests.Response:
        try:
            return self.http.request(method, self.base_url + path, timeout=self.timeout, allow_redirects=False, **kwargs)
        except requests.RequestException as error:
            logger.warning("core transport error %s %s: %s", method, path, type(error).__name__)
            raise CoreUnavailable("core transport error")

    @staticmethod
    def _json(response: requests.Response) -> Optional[dict]:
        if response.status_code == 204 or not response.content:
            return None
        try:
            data = response.json()
        except ValueError:
            raise CoreUnavailable("core returned non-JSON")
        if not isinstance(data, dict):
            raise CoreUnavailable("core returned unexpected JSON")
        return data

    # --- Bindings ---

    def binding(self, service_id: str, fresh: bool = False) -> Binding:
        now = time.monotonic()
        cached = self._bindings.get(service_id)
        if cached and not fresh and cached[0] > now:
            return cached[1]
        response = self._call("GET", f"/api/v1/internal/provisioning/bindings/{service_id}",
                              headers={"Authorization": f"Bearer {self.provisioning_token}"})
        if response.status_code == 404:
            self._bindings.pop(service_id, None)
            raise BindingMissing(service_id)
        if response.status_code != 200:
            raise CoreUnavailable(f"binding lookup failed: {response.status_code}")
        data = self._json(response) or {}
        try:
            binding = Binding(data["institution_id"], data["service_id"], data["service_type"], data["api_base_url"],
                              data["client_base_url"], data["client_id"], data["credential_id"],
                              data["client_secret"], int(data["binding_revision"]))
        except (KeyError, TypeError, ValueError):
            raise CoreUnavailable("malformed binding")
        if binding.service_id != service_id or binding.service_type != "user-profile":
            raise CoreUnavailable("binding of another service")
        self._bindings[service_id] = (now + self.BINDING_TTL, binding)
        return binding

    # --- Machine token (кэш по credential/revision, обмены одного credential объединяются) ---

    def machine_token(self, binding: Binding, force: bool = False) -> str:
        key = (binding.credential_id, binding.revision)
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            cached = self._tokens.get(key)
            if cached and not force and cached[0] - self.TOKEN_MARGIN > time.time():
                return cached[1]
            response = self._call("POST", "/api/v1/internal/auth/token", json={"grant_type": "client_credentials"},
                                  auth=(binding.client_id, binding.client_secret))
            if response.status_code != 200:
                self._tokens.pop(key, None)
                raise CoreUnavailable(f"machine exchange failed: {response.status_code}")
            data = self._json(response) or {}
            token = data.get("access_token")
            if (not isinstance(token, str) or data.get("service_id") != binding.service_id
                    or data.get("institution_id") != binding.institution_id
                    or "profiles:read" not in (data.get("scopes") or [])):
                raise CoreUnavailable("machine token of unexpected scope")
            self._tokens[key] = (time.time() + float(data.get("expires_in", 300)), token)
            return token

    def _machine_request(self, binding: Binding, method: str, path: str, headers=None, **kwargs) -> requests.Response:
        """Запрос с machine token; на 401 — один новый обмен и один повтор."""
        for attempt in (0, 1):
            token = self.machine_token(binding, force=attempt == 1)
            response = self._call(method, path, headers={**(headers or {}), "Authorization": f"Bearer {token}"}, **kwargs)
            if response.status_code != 401:
                return response
        return response

    # --- Introspection (без кэша, fail closed) ---

    def introspect(self, binding: Binding, service_token: str) -> dict:
        response = self._machine_request(binding, "POST", "/api/v1/internal/auth/introspect",
                                         json={"token": service_token})
        if response.status_code != 200:
            raise CoreUnavailable(f"introspection failed: {response.status_code}")
        data = self._json(response) or {}
        if not isinstance(data.get("active"), bool):
            raise CoreUnavailable("malformed introspection")
        return data

    def manifest(self, binding: Binding) -> dict:
        response = self._machine_request(binding, "GET", f"/api/v1/internal/service/{binding.service_id}/manifest")
        if response.status_code != 200:
            raise CoreUnavailable(f"manifest read failed: {response.status_code}")
        return self._json(response) or {}

    def member(self, binding, user_id):
        """Действующее членство в вузе экземпляра: 404 ядра — не участник (False), сбой — 503.

        Участнику возвращается ответ ядра (dict, истинный): в нём display_name —
        имя из регистрации, которое показывается, пока анкета не заполнена.
        """
        response = self._machine_request(binding, "GET", f"/api/v1/internal/service/{binding.service_id}/users/{user_id}/profiles")
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            raise CoreUnavailable("membership lookup failed")
        data = self._json(response) or {}
        return data if data.get("profiles") else False

    def members(self, binding) -> Dict[str, dict]:
        """Все участники вуза: user_id → {display_name, member_since}."""
        response = self._machine_request(binding, "GET", f"/api/v1/internal/service/{binding.service_id}/members")
        if response.status_code != 200:
            raise CoreUnavailable("members lookup failed")
        items = (self._json(response) or {}).get("items")
        if not isinstance(items, list):
            raise CoreUnavailable("malformed members list")
        return {m["user_id"]: {"display_name": m.get("display_name"), "member_since": m.get("member_since")}
                for m in items if isinstance(m, dict) and m.get("profiles")}
