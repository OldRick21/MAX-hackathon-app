"""Ограничение частоты запросов (CORE_API_SPEC.md §3): фиксированное окно, 429 с Retry-After.

Корзины — в памяти процесса ядра (ядро работает одним worker; при нескольких процессах лимит
делится на их число). Ключ берётся только из проверенной личности (пользователь, credential,
сессия) либо из IP и client_id для входа и обмена ключа — подделанный токен не расходует чужой лимит.
Ответ о превышении одинаков для существующих и несуществующих аккаунтов.
"""
import math
import threading
import time
from typing import Dict, Optional, Tuple

from platform_core.errors import DomainError
from settings.config import settings

_buckets: Dict[str, Tuple[int, int]] = {}  # ключ → (начало окна, число запросов)
_lock = threading.Lock()
_last_sweep = 0.0


def _limits() -> Dict[str, int]:
    return {"login": settings.RATE_LIMIT_LOGIN, "refresh": settings.RATE_LIMIT_REFRESH,
            "machine_exchange": settings.RATE_LIMIT_MACHINE_EXCHANGE, "user": settings.RATE_LIMIT_USER,
            "credential": settings.RATE_LIMIT_CREDENTIAL}


def hit(bucket: str, key: Optional[str], now: Optional[float] = None) -> None:
    """Учитывает запрос; при превышении лимита корзины — 429 RATE_LIMITED с Retry-After."""
    limit = _limits()[bucket]
    if limit <= 0 or not key:
        return
    window = max(1, settings.RATE_LIMIT_WINDOW_SECONDS)
    now = time.time() if now is None else now
    start = int(now // window * window)
    name = f"{bucket}:{key}"
    with _lock:
        _sweep(now, window)
        began, count = _buckets.get(name, (start, 0))
        if began != start:
            began, count = start, 0
        count += 1
        _buckets[name] = (began, count)
    if count > limit:
        retry = max(1, math.ceil(began + window - now))
        raise DomainError(429, "RATE_LIMITED", "Слишком много запросов. Повторите позже",
                          headers={"Retry-After": str(retry)})


def _sweep(now: float, window: int) -> None:
    """Корзины живут до конца окна (§9): прошедшие окна периодически удаляются."""
    global _last_sweep
    if now - _last_sweep < window:
        return
    _last_sweep = now
    current = int(now // window * window)
    for name in [k for k, (began, _) in _buckets.items() if began < current]:
        del _buckets[name]


def reset() -> None:
    with _lock:
        _buckets.clear()
