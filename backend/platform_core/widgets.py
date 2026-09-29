"""Виджеты главного экрана: включение по профилям и видимость (docs/services/sdk/WIDGETS_SPEC.md).

Определения виджетов живут в manifest экземпляра. Здесь — два уровня включения для каждого
(виджет, профиль): сервиса (machine API) и администратора вуза (private API). Виджет виден, если
включены оба, профиль есть у пользователя и у экземпляра, и у пользователя есть все required_permissions.
"""
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from database.tables import ServiceInstance, WidgetVisibility
from platform_core.concurrency import compute_etag
from platform_core.errors import not_found, validation

MAX_HOME_WIDGETS = 12
LAYERS = {"service": "by_service", "admin": "by_admin"}


def definitions(service: ServiceInstance) -> List[dict]:
    return list((service.manifest or {}).get("widgets") or [])


def definition(service: ServiceInstance, widget_id: str) -> dict:
    found = next((w for w in definitions(service) if w["id"] == widget_id), None)
    if not found:
        raise not_found("Виджет не найден")
    return found


def rows(db: Session, service_id: str) -> Dict[tuple, WidgetVisibility]:
    return {(r.widget_id, r.profile): r for r in db.query(WidgetVisibility).filter(WidgetVisibility.service_id == service_id)}


def sync(db: Session, service: ServiceInstance) -> None:
    """После публикации manifest (§4.3): новые пары (виджет, профиль) включены на обоих уровнях,
    исчезнувшие удаляются, существующие не меняются — выключенное остаётся выключенным."""
    existing = rows(db, service.id)
    wanted = {(w["id"], p) for w in definitions(service) for p in w["profiles"]}
    for key, row in existing.items():
        if key not in wanted:
            db.delete(row)
    for widget_id, profile in wanted - set(existing):
        db.add(WidgetVisibility(service_id=service.id, widget_id=widget_id, profile=profile, by_service=True, by_admin=True))
    db.flush()


def states(db: Session, service: ServiceInstance) -> List[dict]:
    """Виджеты экземпляра с обоими уровнями включения — для machine и private API."""
    current = rows(db, service.id)
    items = []
    for w in sorted(definitions(service), key=lambda w: (w["order"], w["id"])):
        def layer(attr):
            return {p: bool(getattr(current[(w["id"], p)], attr)) if (w["id"], p) in current else True for p in w["profiles"]}
        items.append({**w, "service_visibility": layer("by_service"), "admin_visibility": layer("by_admin")})
    return items


def etag(db: Session, service: ServiceInstance) -> str:
    return compute_etag({"widgets": service.id, "states": states(db, service)})


def set_visibility(db: Session, service: ServiceInstance, widget_id: str, payload, layer: str) -> dict:
    """PUT .../visibility: переданы ровно все профили виджета, значения true/false."""
    w = definition(service, widget_id)
    if not isinstance(payload, dict) or set(payload) != {"visibility"} or not isinstance(payload["visibility"], dict):
        raise validation("Тело: {\"visibility\": {профиль: true|false}}", "visibility")
    values = payload["visibility"]
    if set(values) != set(w["profiles"]):
        raise validation("Передайте включение для всех профилей виджета: " + ", ".join(w["profiles"]), "visibility")
    for profile, value in values.items():
        if not isinstance(value, bool):
            raise validation("Значение включения — true или false", f"visibility.{profile}")
    sync(db, service)
    current = rows(db, service.id)
    before = {p: bool(getattr(current[(widget_id, p)], LAYERS[layer])) for p in w["profiles"]}
    for profile, value in values.items():
        setattr(current[(widget_id, profile)], LAYERS[layer], value)
    db.flush()
    return {"before": before, "after": dict(values)}


def visible_for(db: Session, service: ServiceInstance, profile: str, permissions: List[str]) -> List[dict]:
    """Виджеты экземпляра, которые видит пользователь в профиле (§2, условия 3–5)."""
    current = rows(db, service.id)
    result = []
    for w in definitions(service):
        row: Optional[WidgetVisibility] = current.get((w["id"], profile))
        if profile not in w["profiles"] or (row is not None and not (row.by_service and row.by_admin)):
            continue
        if not set(w["required_permissions"]) <= set(permissions):
            continue
        result.append(w)
    return result
