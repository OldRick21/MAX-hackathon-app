"""Миграция базы прежней схемы: логическое удаление экземпляров (deleted_at, частичный уникальный индекс)."""
import os
import sqlite3
import tempfile
import unittest

_tmp = tempfile.TemporaryDirectory()
DB = f'{_tmp.name}/old.db'
os.environ.update(DATABASE_URL=f'sqlite:///{DB}', JWT_ISSUER='https://core.test', JWT_KEYRING_PATH=f'{_tmp.name}/keys.json',
                  CURSOR_SECRET_KEY='c' * 48, ALLOW_DEV_LOGIN='true', SEED_DEMO_DATA='false', SERVICE_CONFIG_DIR='',
                  CLOUD_BINDING_KEY='b' * 48)

INST = '11111111-1111-4111-8111-111111111111'
SVC = '22222222-2222-4222-8222-222222222222'

# Таблицы прежней схемы, которые участвуют в переносе: services с UNIQUE(institution_id, service_type)
# и зависимые роли (ON DELETE CASCADE — перенос не должен их удалить).
OLD_SCHEMA = f"""
CREATE TABLE institutions (id VARCHAR(36) PRIMARY KEY, titles JSON NOT NULL, default_locale VARCHAR(2) NOT NULL,
    status VARCHAR(20) NOT NULL, revision INTEGER NOT NULL DEFAULT 1, created_at DATETIME, updated_at DATETIME);
CREATE TABLE services (id VARCHAR(36) NOT NULL, institution_id VARCHAR(36) NOT NULL, service_type VARCHAR(50) NOT NULL,
    deployment VARCHAR(20) NOT NULL, enabled BOOLEAN NOT NULL, protected BOOLEAN NOT NULL,
    api_base_url VARCHAR(2048) NOT NULL, client_base_url VARCHAR(2048) NOT NULL, supported_profiles JSON NOT NULL,
    manifest JSON NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (id),
    CONSTRAINT uq_institution_service_type UNIQUE (institution_id, service_type),
    FOREIGN KEY(institution_id) REFERENCES institutions (id) ON DELETE CASCADE);
CREATE TABLE service_roles (service_id VARCHAR(36) NOT NULL, code VARCHAR(64) NOT NULL, titles JSON NOT NULL,
    allowed_profiles JSON NOT NULL, permissions JSON NOT NULL, system BOOLEAN NOT NULL, PRIMARY KEY (service_id, code),
    FOREIGN KEY(service_id) REFERENCES services (id) ON DELETE CASCADE);
INSERT INTO institutions VALUES ('{INST}', '{{"ru": "Вуз"}}', 'ru', 'active', 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00');
INSERT INTO services VALUES ('{SVC}', '{INST}', 'coursework', 'local', 1, 0, 'https://cw.example.ru/api/v1',
    'https://cw.example.ru', '["admin", "student"]', '{{}}', '2026-01-01 00:00:00');
INSERT INTO service_roles VALUES ('{SVC}', 'coursework_manager', '{{"ru": "Р"}}', '["admin"]', '["coursework.manage"]', 0);
"""


class ServiceTombstoneMigration(unittest.TestCase):
    def test_old_unique_constraint_is_replaced_without_losing_rows(self):
        with sqlite3.connect(DB) as conn:
            conn.executescript(OLD_SCHEMA)
        from database.create_tables import create_tables, session_local
        from database.tables import ServiceInstance, ServiceRole
        from database.base import utc_now
        create_tables()
        create_tables()  # повторный запуск ничего не меняет
        with sqlite3.connect(DB) as conn:
            ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name='services'").fetchone()[0]
            self.assertNotIn('uq_institution_service_type', ddl)
            self.assertIn('deleted_at', ddl)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            index = conn.execute("SELECT sql FROM sqlite_master WHERE name='uq_active_service_type'").fetchone()[0]
            self.assertIn('WHERE deleted_at IS NULL', index)
        with session_local() as db:
            self.assertIsNotNone(db.get(ServiceRole, (SVC, 'coursework_manager')))  # каскад не сработал
            old = db.get(ServiceInstance, SVC)
            old.deleted_at = utc_now()
            db.commit()
            # Слот типа освобождён удалением: новый экземпляр того же типа с новым UUID.
            db.add(ServiceInstance(id='33333333-3333-4333-8333-333333333333', institution_id=INST, service_type='coursework',
                                   deployment='local', enabled=False, protected=False, api_base_url='https://cw.example.ru/api/v1',
                                   client_base_url='https://cw.example.ru', supported_profiles=['admin'], manifest={}))
            db.commit()
            # Два действующих экземпляра одного типа — нарушение уникальности.
            db.add(ServiceInstance(id='44444444-4444-4444-8444-444444444444', institution_id=INST, service_type='coursework',
                                   deployment='local', enabled=False, protected=False, api_base_url='https://cw.example.ru/api/v1',
                                   client_base_url='https://cw.example.ru', supported_profiles=['admin'], manifest={}))
            with self.assertRaises(Exception):
                db.commit()


if __name__ == '__main__':
    unittest.main()
