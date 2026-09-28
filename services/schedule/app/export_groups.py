"""Выгрузка учебных групп, которые раньше хранил сервис расписания, для переноса в ядро.

    sudo docker compose exec -T schedule python -m app.export_groups > groups.json
    sudo docker compose exec -T backend python manage.py import-groups < groups.json

UUID групп сохраняются, поэтому занятия продолжают ссылаться на те же группы.
"""
import json
import sqlite3
import sys

from app.main import DB


def export() -> dict:
    db = sqlite3.connect(DB)
    try:
        return _export(db)
    finally:
        db.close()


def _export(db) -> dict:
    db.row_factory = sqlite3.Row
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'groups' not in tables:
        return {'groups': []}
    groups = []
    for g in db.execute('SELECT institution_id, service_id, id, name FROM groups ORDER BY name'):
        students = [r['user_id'] for r in db.execute(
            'SELECT user_id FROM group_students WHERE institution_id=? AND service_id=? AND group_id=? ORDER BY user_id',
            (g['institution_id'], g['service_id'], g['id']))] if 'group_students' in tables else []
        groups.append({'id': g['id'], 'institution_id': g['institution_id'], 'name': g['name'], 'user_ids': students})
    return {'groups': groups}


if __name__ == '__main__':
    json.dump(export(), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')
