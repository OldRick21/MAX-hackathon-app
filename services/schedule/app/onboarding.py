"""Публикация меню и ролей сервиса в ядро — так же, как у любого сервиса вуза (CORE_API_SPEC §7).

Запускается в фоне при старте и повторяется с паузой до 5 минут, пока ядро не примет всё:
- свои роли создаются или приводятся к описанию сервиса;
- права, которых у сервиса больше нет, снимаются с остальных ролей; роль без прав и роли
  прежних версий удаляются, а перед удалением или сужением профилей роль снимается с участников
  (ядро отвечает 409 ROLE_IN_USE, пока назначения есть);
- меню публикуется, если отличается от опубликованного.
"""
import logging
import threading
import time

from app.core_client import BindingMissing, CoreUnavailable

log = logging.getLogger(__name__.split(".")[0] + ".onboarding")


def sync(core, manifest: dict, roles: list, retired: tuple = ()) -> None:
    binding = core.binding()
    known = {p for r in roles for p in r["permissions"]}
    wanted = {r["code"]: r for r in roles}
    existing = {r["code"]: r for r in core.roles(binding)}
    for code, role in wanted.items():
        have = existing.get(code)
        if not have:
            core.create_role(binding, role)
            continue
        patch = {k: role[k] for k in ("titles", "allowed_profiles", "permissions") if have.get(k) != role[k]}
        removed = set(have.get("allowed_profiles") or []) - set(role["allowed_profiles"])
        if removed:
            core.release_role(binding, code, removed)
        if patch:
            core.update_role(binding, code, patch)
    for code, have in existing.items():
        if code in wanted or have.get("system"):
            continue
        kept = [p for p in have.get("permissions") or [] if p in known]
        if code in retired or not kept:
            core.release_role(binding, code)
            core.delete_role(binding, code)
        elif kept != list(have.get("permissions") or []):
            core.update_role(binding, code, {"permissions": kept})
    current, etag = core.manifest_with_etag(binding)
    if {k: current.get(k) for k in manifest} != manifest or set(current) - set(manifest) - {"titles", "menus"}:
        core.publish_manifest(binding, manifest, etag)


def start(core, manifest: dict, roles: list, state: dict, retired: tuple = ()) -> threading.Thread:
    def run():
        delay = 5
        while True:
            try:
                sync(core, manifest, roles, retired)
                state.update(onboarding="ready", error=None)
                log.info("onboarding complete")
                return
            except (CoreUnavailable, BindingMissing) as error:
                state.update(onboarding="waiting", error=str(error) or type(error).__name__)
                log.warning("onboarding failed, retry in %ss: %s", delay, error)
            time.sleep(delay)
            delay = min(delay * 2, 300)
    thread = threading.Thread(target=run, name="onboarding", daemon=True)
    thread.start()
    return thread
