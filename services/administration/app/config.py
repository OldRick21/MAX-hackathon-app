"""Конфигурация процесса administration. Один процесс обслуживает все вузы."""
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
    # Private listener ядра во внутренней сети. Не публичный адрес.
    core_internal_url: str
    # Токен чтения bindings administration (секрет deployment).
    provisioning_token: str
    # Точный origin ядра-клиента: единственный допустимый родитель iframe.
    shell_origin: str
    # Публичный адрес API этого сервиса; должен совпасть с api_base_url из реестра.
    public_api_base_url: str
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
        core_internal_url=_required("CORE_INTERNAL_URL").rstrip("/"),
        provisioning_token=_required("ADMINISTRATION_PROVISIONING_TOKEN", 32),
        shell_origin=shell,
        public_api_base_url=_required("ADMINISTRATION_API_BASE_URL").rstrip("/"),
        frame_ancestors=(shell, *extra),
        core_timeout=float(os.environ.get("CORE_TIMEOUT_SECONDS", "3")),
    )
