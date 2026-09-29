"""Клиент ядра для процесса schedule (часть Service SDK).

Копия клиента user-profile с проверкой своего типа и чтением профилей участника.
Правила — docs/services/sdk/SPEC.md §4.2 и docs/services/schedule/SPEC.md §2.
"""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger("schedule.core")


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

    def groups(self, binding: Binding) -> list:
        """Учебные группы вуза экземпляра из ядра (scope groups:read); сбой — 503."""
        response = self._machine_request(binding, "GET", f"/api/v1/internal/service/{binding.service_id}/groups")
        if response.status_code != 200:
            raise CoreUnavailable(f"groups read failed: {response.status_code}")
        items = (self._json(response) or {}).get("items")
        return [{"id": g["id"], "name": g["name"]} for g in items if isinstance(g, dict)] if isinstance(items, list) else []

    def members(self, binding: Binding) -> list:
        """Участники вуза: user_id, профили и имя из заявки на вступление (для импорта по ФИО); сбой — 503."""
        response = self._machine_request(binding, "GET", f"/api/v1/internal/service/{binding.service_id}/members")
        if response.status_code != 200:
            raise CoreUnavailable(f"members read failed: {response.status_code}")
        items = (self._json(response) or {}).get("items")
        return [m for m in items if isinstance(m, dict) and m.get("user_id")] if isinstance(items, list) else []

    def profiles(self, binding: Binding, user_id: str) -> list:
        """Профили действующего участника вуза экземпляра; [] — не участник, сбой — 503."""
        response = self._machine_request(binding, "GET", f"/api/v1/internal/service/{binding.service_id}/users/{user_id}/profiles")
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            raise CoreUnavailable("membership lookup failed")
        data = self._json(response) or {}
        profiles = data.get("profiles")
        return [p for p in profiles if isinstance(p, str)] if isinstance(profiles, list) else []

    # --- Меню и роли: сервис публикует их сам (CORE_API_SPEC §7), как любой сервис вуза ---

    def roles(self, binding: Binding) -> list:
        """Все роли экземпляра: список постраничный (limit/cursor)."""
        result, cursor = [], None
        while True:
            response = self._machine_request(binding, "GET", f"/api/v1/internal/service/{binding.service_id}/roles",
                                             params={"limit": 100, **({"cursor": cursor} if cursor else {})})
            if response.status_code != 200:
                raise CoreUnavailable(f"roles read failed: {response.status_code}")
            data = self._json(response) or {}
            items = data.get("items")
            result += [r for r in items if isinstance(r, dict)] if isinstance(items, list) else []
            cursor = data.get("next_cursor")
            if not cursor:
                return result

    def create_role(self, binding: Binding, role: dict) -> None:
        response = self._machine_request(binding, "POST", f"/api/v1/internal/service/{binding.service_id}/roles", json=role)
        if response.status_code not in (200, 201, 409):
            raise CoreUnavailable(f"role create failed: {response.status_code} {response.text[:200]}")

    def _role_etag(self, binding: Binding, code: str) -> Optional[str]:
        response = self._machine_request(binding, "GET", f"/api/v1/internal/service/{binding.service_id}/roles/{code}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise CoreUnavailable(f"role read failed: {response.status_code}")
        return response.headers.get("ETag")

    def update_role(self, binding: Binding, code: str, patch: dict) -> None:
        response = self._machine_request(binding, "PATCH", f"/api/v1/internal/service/{binding.service_id}/roles/{code}",
                                         json=patch, headers={"If-Match": self._role_etag(binding, code) or ""})
        if response.status_code != 200:
            raise CoreUnavailable(f"role update failed: {response.status_code} {response.text[:200]}")

    def delete_role(self, binding: Binding, code: str) -> None:
        """Удаляет свою роль; назначения нужно снять заранее (release_role), иначе ядро ответит 409."""
        etag = self._role_etag(binding, code)
        if etag is None:
            return
        response = self._machine_request(binding, "DELETE", f"/api/v1/internal/service/{binding.service_id}/roles/{code}",
                                         headers={"If-Match": etag})
        if response.status_code not in (204, 404):
            raise CoreUnavailable(f"role delete failed: {response.status_code} {response.text[:200]}")

    def release_role(self, binding: Binding, code: str, profiles=None, replace_with: Optional[str] = None) -> None:
        """Снимает роль с участников (во всех профилях или только в указанных) — перед удалением
        роли или сужением её профилей: ядро не делает этого само (409 ROLE_IN_USE).
        replace_with — вместо снятия заменить роль другой (перенос назначений при переименовании роли)."""
        base = f"/api/v1/internal/service/{binding.service_id}"
        response = self._machine_request(binding, "GET", f"{base}/members")
        if response.status_code != 200:
            raise CoreUnavailable(f"members read failed: {response.status_code}")
        for member in (self._json(response) or {}).get("items") or []:
            for profile in member.get("profiles") or []:
                if profiles is not None and profile not in profiles:
                    continue
                path = f"{base}/users/{member['user_id']}/profiles/{profile}/roles"
                current = self._machine_request(binding, "GET", path)
                if current.status_code == 404:
                    continue
                if current.status_code != 200:
                    raise CoreUnavailable(f"assignments read failed: {current.status_code}")
                roles = (self._json(current) or {}).get("roles") or []
                if code not in roles:
                    continue
                updated = [r for r in roles if r != code]
                if replace_with and replace_with not in updated:
                    updated.append(replace_with)
                saved = self._machine_request(binding, "PUT", path, json={"roles": updated},
                                              headers={"If-Match": current.headers.get("ETag") or ""})
                if saved.status_code != 200:
                    raise CoreUnavailable(f"assignments update failed: {saved.status_code} {saved.text[:200]}")

    def manifest_with_etag(self, binding: Binding):
        response = self._machine_request(binding, "GET", f"/api/v1/internal/service/{binding.service_id}/manifest")
        if response.status_code != 200:
            raise CoreUnavailable(f"manifest read failed: {response.status_code}")
        return self._json(response) or {}, response.headers.get("ETag")

    def publish_manifest(self, binding: Binding, manifest: dict, etag: Optional[str]) -> None:
        response = self._machine_request(binding, "PUT", f"/api/v1/internal/service/{binding.service_id}/manifest",
                                         json=manifest, headers={"If-Match": etag} if etag else None)
        if response.status_code != 200:
            raise CoreUnavailable(f"manifest publish failed: {response.status_code} {response.text[:200]}")
