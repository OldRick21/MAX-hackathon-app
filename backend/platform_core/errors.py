"""Доменная ошибка с кодом из ErrorResponse.code (CORE_API_OPENAPI.yaml).

Модуль не зависит от FastAPI: его используют и чистые проверки (каталог, ETag,
курсоры), и обработчики. В main.py DomainError переводится в ErrorResponse.
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DomainError(Exception):
    status: int
    code: str
    message: str
    details: Optional[List[dict]] = field(default=None)
    headers: Optional[dict] = field(default=None)

    def __str__(self) -> str:  # pragma: no cover - для журналов
        return f"{self.status} {self.code}: {self.message}"


def not_found(message: str = "Ресурс не найден") -> DomainError:
    return DomainError(404, "RESOURCE_NOT_FOUND", message)


def forbidden(message: str = "Недостаточно прав для операции") -> DomainError:
    return DomainError(403, "FORBIDDEN", message)


def protected(message: str, status: int = 409) -> DomainError:
    """409 — инвариант (удаление/отключение administration), 403 — запись через machine API."""
    return DomainError(status, "PROTECTED_RESOURCE", message)


def validation(message: str, path: str = "") -> DomainError:
    details = [{"path": path, "message": message}] if path else None
    return DomainError(422, "VALIDATION_ERROR", message, details)
