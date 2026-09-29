"""Клиент ядра для процесса administration (часть Service SDK).

Без FastAPI: тестируется против заглушки ядра (tests/test_core_client.py).
Правила — docs/services/sdk/SPEC.md §4.2 и docs/services/administration/SPEC.md §2.
"""
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger("administration.core")


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


@dataclass
class Upstream:
    status: int
    body: Optional[dict]
    headers: Dict[str, str]
    request_id: str = ""


class CoreClient:
    TOKEN_MARGIN = 30.0

    def __init__(self, base_url: str, client_id: str, client_secret: str, api_base_url: str = "",
                 client_base_url: str = "", timeout: float = 10.0, session=None):
        """Ключ процесса — из .env (выдан в карточке сервиса), как у любого сервиса вуза."""
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.api_base_url = api_base_url.rstrip("/")
        self.client_base_url = client_base_url.rstrip("/")
        self._own: Optional[Binding] = None
        self.timeout = timeout
        self.http = session or requests.Session()
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

    def binding(self, service_id: Optional[str] = None, fresh: bool = False) -> Binding:
        """Свой экземпляр: вуз и UUID сервиса ядро сообщает при обмене ключа. Чужой service_id — BindingMissing."""
        with self._guard:
            own = self._own
        if own is None or fresh:
            response = self._call("POST", "/api/v1/internal/auth/token", json={"grant_type": "client_credentials"},
                                  auth=(self.client_id, self.client_secret))
            if response.status_code in (400, 401, 403):
                raise BindingMissing("service key rejected")
            if response.status_code != 200:
                raise CoreUnavailable(f"machine exchange failed: {response.status_code}")
            data = self._json(response) or {}
            token = data.get("access_token")
            if not isinstance(token, str) or not data.get("service_id") or not data.get("institution_id"):
                raise CoreUnavailable("malformed machine token")
            own = Binding(data["institution_id"], data["service_id"], "", self.api_base_url, self.client_base_url,
                          self.client_id, self.client_id, self.client_secret, 0)
            with self._guard:
                self._own = own
                self._tokens[(own.credential_id, own.revision)] = (time.time() + float(data.get("expires_in", 300)), token)
        if service_id is not None and own.service_id != service_id:
            raise BindingMissing(service_id)
        return own

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
                    or "institution:manage" not in (data.get("scopes") or [])):
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

    # --- Private API вуза ---

    def private(self, binding: Binding, method: str, suffix: str, actor_token: str, *, body: Optional[bytes] = None,
                query: Optional[dict] = None, if_match: Optional[str] = None, idempotency_key: Optional[str] = None,
                facade_request_id: str = "") -> Upstream:
        headers = {"X-Actor-Token": actor_token, "Accept": "application/json"}
        if facade_request_id:
            headers["X-Facade-Request-ID"] = facade_request_id
        if if_match is not None:
            headers["If-Match"] = if_match
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        if body is not None:
            headers["Content-Type"] = "application/json"
        path = f"/api/v1/institution/{binding.institution_id}/internal{suffix}"
        response = self._machine_request(binding, method, path, headers=headers, params=query or None, data=body)
        upstream_id = response.headers.get("X-Request-ID", "")
        logger.info("facade %s -> core %s %s %s status=%s", facade_request_id, upstream_id, method, suffix,
                    response.status_code)
        if response.status_code >= 500:
            raise CoreUnavailable(f"core error {response.status_code}")
        data = self._json(response)
        if response.status_code >= 400 and not (data and isinstance(data.get("error"), dict)):
            raise CoreUnavailable("core error without ErrorResponse")
        keep = {k: v for k, v in response.headers.items() if k.lower() in ("etag", "location", "retry-after")}
        return Upstream(response.status_code, data, keep, upstream_id)


def dumps(value) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")
