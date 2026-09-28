"""Конфигурация процесса administration. Один контейнер — один вуз, как у любого сервиса вуза.

Адреса и ключ — из .env: строки выдаёт оператор в пульте («Выдать ключ» в карточке администрирования).
"""
import os
from dataclasses import dataclass
from urllib.parse import urlsplit


def _required(name: str, min_len: int = 1) -> str:
    value = os.environ.get(name, "").strip()
    if len(value) < min_len:
        raise RuntimeError(f"Set {name}")
    return value


@dataclass(frozen=True)
class Config:
    # Адрес ядра (CORE_URL из строк ключа).
    core_url: str
    # Ключ процесса: SERVICE_CLIENT_ID / SERVICE_CLIENT_SECRET.
    client_id: str
    client_secret: str
    # Точный origin ядра-клиента: единственный допустимый родитель iframe.
    shell_origin: str
    # Публичные адреса этого сервиса; должны совпасть с адресами в реестре ядра.
    public_api_base_url: str
    public_client_base_url: str
    # Дополнительные родители для CSP frame-ancestors (например, MAX Web).
    frame_ancestors: tuple
    core_timeout: float = 3.0

    @property
    def public_origin(self) -> str:
        parts = urlsplit(self.public_api_base_url)
        return f"{parts.scheme}://{parts.netloc}"


def load() -> Config:
    shell = _required("SHELL_ORIGIN").rstrip("/")
    extra = tuple(o.strip().rstrip("/") for o in os.environ.get("FRAME_ANCESTORS", "").split() if o.strip())
    return Config(
        core_url=_required("CORE_URL").rstrip("/"),
        client_id=_required("SERVICE_CLIENT_ID"),
        client_secret=_required("SERVICE_CLIENT_SECRET", 32),
        shell_origin=shell,
        public_api_base_url=_required("SERVICE_API_BASE_URL").rstrip("/"),
        public_client_base_url=_required("SERVICE_CLIENT_BASE_URL").rstrip("/"),
        frame_ancestors=(shell, *extra),
        core_timeout=float(os.environ.get("CORE_TIMEOUT_SECONDS", "3")),
    )
