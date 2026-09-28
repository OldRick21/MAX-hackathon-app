"""Клиент ядра для локального сервиса курсовых (часть Service SDK, local-адаптер binding).

В отличие от облачных сервисов binding не читается из provisioning ядра: вуз кладёт в
серверные секреты client_id/client_secret, выданные в администрировании. institution_id
и service_id сервис узнаёт из ответа ядра на обмен credential.
Правила — docs/services/sdk/SPEC.md §4.2 и docs/services/coursework/SPEC.md §2.
"""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger("coursework.core")


class CoreUnavailable(Exception):
    """Сбой транспорта, отозванный credential или неожиданный ответ ядра → 503."""


@dataclass(frozen=True)
class Binding:
    institution_id: str
    service_id: str
    api_base_url: str
    client_base_url: str


class CoreClient:
    TOKEN_MARGIN = 30.0

    def __init__(self, base_url: str, client_id: str, client_secret: str, api_base_url: str, client_base_url: str,
                 timeout: float = 3.0, session=None):
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.api_base_url = api_base_url.rstrip("/")
        self.client_base_url = client_base_url.rstrip("/")
        self.timeout = timeout
        self.http = session or requests.Session()
        self._token: Optional[tuple] = None
        self._binding: Optional[Binding] = None
        self._lock = threading.Lock()

    # --- HTTP ---

    def _call(self, method: str, path: str, **kwargs) -> requests.Response:
        try:
            return self.http.request(method, self.base_url + path, timeout=self.timeout, allow_redirects=False, **kwargs)
        except requests.RequestException as error:
            logger.warning("core transport error %s %s: %s", method, path, type(error).__name__)
            raise CoreUnavailable("core transport error")

    @staticmethod
    def _json(response: requests.Response) -> dict:
        try:
            data = response.json()
        except ValueError:
            raise CoreUnavailable("core returned non-JSON")
        if not isinstance(data, dict):
            raise CoreUnavailable("core returned unexpected JSON")
        return data

    # --- Machine token и binding ---

    def machine_token(self, force: bool = False) -> str:
        """Обмен своего credential; одновременные обмены объединяются, кэш до exp − 30 с."""
        with self._lock:
            if self._token and not force and self._token[0] - self.TOKEN_MARGIN > time.time():
                return self._token[1]
            response = self._call("POST", "/api/v1/internal/auth/token", json={"grant_type": "client_credentials"},
                                  auth=(self.client_id, self.client_secret))
            if response.status_code != 200:
                self._token = None
                raise CoreUnavailable(f"machine exchange failed: {response.status_code}")
            data = self._json(response)
            token = data.get("access_token")
            if not isinstance(token, str) or not {"manifest:write", "roles:write", "profiles:read",
                                                  "tokens:introspect"} <= set(data.get("scopes") or []):
                raise CoreUnavailable("machine token of unexpected scope")
            binding = Binding(str(data.get("institution_id")), str(data.get("service_id")),
                              self.api_base_url, self.client_base_url)
            if self._binding and self._binding != binding:
                # Credential другого экземпляра в той же установке — ошибка конфигурации, не переключение tenant.
                raise CoreUnavailable("credential belongs to another service instance")
            self._binding = binding
            self._token = (time.time() + float(data.get("expires_in", 300)), token)
            return token

    def binding(self) -> Binding:
        if self._binding is None:
            self.machine_token()
        return self._binding

    def _machine_request(self, method: str, path: str, extra_headers=None, **kwargs) -> requests.Response:
        """Запрос с machine token; на 401 — один новый обмен и один повтор."""
        for attempt in (0, 1):
            token = self.machine_token(force=attempt == 1)
            response = self._call(method, path, headers={**(extra_headers or {}), "Authorization": f"Bearer {token}"}, **kwargs)
            if response.status_code != 401:
                return response
        return response

    # --- Операции SDK ---

    def introspect(self, service_token: str) -> dict:
        response = self._machine_request("POST", "/api/v1/internal/auth/introspect", json={"token": service_token})
        if response.status_code != 200:
            raise CoreUnavailable(f"introspection failed: {response.status_code}")
        data = self._json(response)
        if not isinstance(data.get("active"), bool):
            raise CoreUnavailable("malformed introspection")
        return data

    def manifest_versioned(self) -> tuple:
        response = self._machine_request("GET", f"/api/v1/internal/service/{self.binding().service_id}/manifest")
        if response.status_code != 200:
            raise CoreUnavailable(f"manifest read failed: {response.status_code}")
        return self._json(response), response.headers.get("ETag")

    def manifest(self) -> dict:
        return self.manifest_versioned()[0]

    def publish_manifest(self, manifest: dict, etag: Optional[str]) -> None:
        """Замена manifest с If-Match текущей версии (CORE_API_SPEC §7)."""
        response = self._machine_request("PUT", f"/api/v1/internal/service/{self.binding().service_id}/manifest",
                                         json=manifest, extra_headers={"If-Match": etag or ""})
        if response.status_code != 200:
            raise CoreUnavailable(f"manifest publish failed: {response.status_code} {response.text[:200]}")

    def ensure_role(self, role: dict) -> bool:
        """Создаёт определение роли, если его ещё нет. True — создана сейчас."""
        path = f"/api/v1/internal/service/{self.binding().service_id}/roles"
        existing = self._machine_request("GET", f"{path}/{role['code']}")
        if existing.status_code == 200:
            return False
        response = self._machine_request("POST", path, json=role)
        if response.status_code == 409:
            return False
        if response.status_code not in (200, 201):
            raise CoreUnavailable(f"role create failed: {response.status_code} {response.text[:200]}")
        return True

    def profiles(self, user_id: str) -> list:
        """Профили действующего участника вуза; [] — не участник, сбой — 503."""
        response = self._machine_request("GET", f"/api/v1/internal/service/{self.binding().service_id}/users/{user_id}/profiles")
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            raise CoreUnavailable("membership lookup failed")
        profiles = self._json(response).get("profiles")
        return [p for p in profiles if isinstance(p, str)] if isinstance(profiles, list) else []
