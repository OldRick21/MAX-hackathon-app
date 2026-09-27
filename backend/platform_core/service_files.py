"""Rebuildable, secret-free projection of registered services onto disk."""
from filelock import FileLock
import json
import logging
import os
from pathlib import Path
import tempfile
from uuid import UUID

from sqlalchemy import event
from database.tables import ServiceInstance
from settings.config import settings

log = logging.getLogger(__name__)


def export_services(session_factory):
    if not settings.SERVICE_CONFIG_DIR:
        return
    root = Path(settings.SERVICE_CONFIG_DIR)
    root.mkdir(parents=True, exist_ok=True)
    # Readers see whole files; concurrent commits rebuild from the latest DB state.
    with FileLock(str(root / '.export.lock')):
        with session_factory() as db:
            services = db.query(ServiceInstance).all()
            expected = set()
            for service in services:
                name = str(UUID(service.id)) + '.json'
                expected.add(name)
                data = {key: getattr(service, key) for key in (
                    'id', 'institution_id', 'service_type', 'deployment', 'enabled',
                    'protected', 'api_base_url', 'client_base_url', 'supported_profiles', 'manifest')}
                data['schema_version'] = 1
                temp = None
                try:
                    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root, prefix='.service-', delete=False) as out:
                        temp = Path(out.name)
                        json.dump(data, out, ensure_ascii=False, indent=2)
                        out.write('\n')
                        out.flush()
                        os.fsync(out.fileno())
                    temp.replace(root / name)
                finally:
                    if temp and temp.exists():
                        temp.unlink()
            for path in root.glob('*.json'):
                try:
                    UUID(path.stem)
                except ValueError:
                    continue
                if path.name not in expected:
                    path.unlink()


def install_export_hook(session_factory):
    @event.listens_for(session_factory, 'before_flush')
    def changed(session, *_):
        if any(isinstance(obj, ServiceInstance) for obj in session.new | session.dirty | session.deleted):
            session.info['export_services'] = True

    @event.listens_for(session_factory, 'after_rollback')
    def rolled_back(session):
        session.info.pop('export_services', None)

    @event.listens_for(session_factory, 'after_commit')
    def committed(_session):
        if not _session.info.pop('export_services', False):
            return
        try:
            export_services(session_factory)
        except Exception:
            # A committed API operation must not masquerade as a failed transaction.
            log.exception('Service settings export failed; rebuild with manage.py export-services')
