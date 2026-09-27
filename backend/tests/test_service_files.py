"""Run separately: python -m unittest tests.test_service_files -v."""
import os
import json
import tempfile
import unittest
from pathlib import Path

_tmp = tempfile.TemporaryDirectory()
os.environ.update(DATABASE_URL=f'sqlite:///{_tmp.name}/db.sqlite', JWT_ISSUER="https://core.test", JWT_KEYRING_PATH=f"{_tmp.name}/keys.json", CURSOR_SECRET_KEY='test-key-' * 8,
                  SERVICE_CONFIG_DIR=f'{_tmp.name}/connected')
from database.create_tables import session_local, engine
from database.tables import table_class, Institution, ServiceInstance
from platform_core.service_files import export_services

INST = '11111111-1111-4111-8111-111111111111'
SRV = '22222222-2222-4222-8222-222222222222'

class ServiceFilesTest(unittest.TestCase):
    def test_commit_update_rollback_delete_and_rebuild(self):
        table_class.metadata.create_all(engine)
        file = Path(_tmp.name) / 'connected' / (SRV + '.json')
        with session_local() as db:
            db.add(Institution(id=INST, titles={'ru': 'Test'}, status='active', default_locale='ru'))
            db.flush()
            service = ServiceInstance(id=SRV, institution_id=INST, service_type='schedule', deployment='cloud',
                api_base_url='https://service.example/api', client_base_url='https://service.example',
                supported_profiles=['student'], manifest={'titles': {'ru': 'Test'}, 'menus': []})
            db.add(service)
            db.flush()
            self.assertFalse(file.exists())
            db.commit()
            self.assertEqual(json.loads(file.read_text())['institution_id'], INST)
            self.assertNotIn('client_secret', file.read_text())
            service.enabled = False
            db.commit()
            self.assertFalse(json.loads(file.read_text())['enabled'])
            service.enabled = True
            db.flush()
            db.rollback()
            self.assertFalse(json.loads(file.read_text())['enabled'])
            file.unlink()
            export_services(session_local)
            self.assertTrue(file.exists())
            db.delete(service)
            db.commit()
            self.assertFalse(file.exists())
        engine.dispose()
        _tmp.cleanup()
