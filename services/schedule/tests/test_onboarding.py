"""Публикация меню и ролей сервиса в ядро (app/onboarding.py) против поддельного клиента ядра.

    cd services/schedule && python -m unittest tests.test_onboarding -v
"""
import unittest
from types import SimpleNamespace

from app import onboarding

ROLE = {'code': 'schedule_editor', 'titles': {'ru': 'Редактирование расписания'}, 'allowed_profiles': ['teacher'],
        'permissions': ['schedule.write']}
MANIFEST = {'titles': {'ru': 'Расписание'}, 'menus': [{'id': 'schedule', 'titles': {'ru': 'Расписание'},
            'entrypoint_path': '/schedule', 'profiles': ['student'], 'required_permissions': [], 'order': 0}]}


class FakeCore:
    def __init__(self, roles, manifest):
        self._roles, self._manifest, self.calls = {r['code']: dict(r) for r in roles}, manifest, []

    def binding(self, service_id=None, fresh=False):
        return SimpleNamespace(service_id='svc')

    def roles(self, b):
        return [dict(r) for r in self._roles.values()]

    def create_role(self, b, role):
        self.calls.append(('create', role['code']))
        self._roles[role['code']] = dict(role)

    def update_role(self, b, code, patch):
        self.calls.append(('update', code, sorted(patch)))
        self._roles[code].update(patch)

    def release_role(self, b, code, profiles=None):
        self.calls.append(('release', code, sorted(profiles) if profiles else None))

    def delete_role(self, b, code):
        self.calls.append(('delete', code))
        self._roles.pop(code)

    def manifest_with_etag(self, b):
        return self._manifest, '"m1"'

    def publish_manifest(self, b, manifest, etag):
        self.calls.append(('manifest', etag))
        self._manifest = manifest


class Onboarding(unittest.TestCase):
    def test_first_start_creates_role_and_publishes_menu(self):
        core = FakeCore([], {'titles': {'ru': 'x'}, 'menus': []})
        onboarding.sync(core, MANIFEST, [ROLE])
        self.assertEqual(core.calls, [('create', 'schedule_editor'), ('manifest', '"m1"')])
        # Повторный запуск ничего не меняет.
        core.calls.clear()
        onboarding.sync(core, MANIFEST, [ROLE])
        self.assertEqual(core.calls, [])

    def test_old_roles_are_cleaned(self):
        old = [
            {**ROLE, 'titles': {'ru': 'Редактор расписания'}, 'allowed_profiles': ['admin', 'teacher'],
             'permissions': ['schedule.read_all', 'schedule.write']},
            {'code': 'group_editor', 'titles': {'ru': 'Редактор групп'}, 'allowed_profiles': ['admin'],
             'permissions': ['schedule.write']},
            {'code': 'groups_by_hand', 'titles': {'ru': 'Создание групп'}, 'allowed_profiles': ['teacher'],
             'permissions': ['schedule.groups']},
            {'code': 'custom_writer', 'titles': {'ru': 'Свой'}, 'allowed_profiles': ['teacher'],
             'permissions': ['schedule.write', 'schedule.read_all']},
        ]
        core = FakeCore(old, MANIFEST)
        onboarding.sync(core, MANIFEST, [ROLE], retired=('group_editor',))
        self.assertIn(('update', 'schedule_editor', ['allowed_profiles', 'permissions', 'titles']), core.calls)
        self.assertIn(('delete', 'group_editor'), core.calls)      # роль прежней версии
        # Перед удалением роль снимается с участников: иначе ядро ответит 409 ROLE_IN_USE.
        self.assertLess(core.calls.index(('release', 'group_editor', None)), core.calls.index(('delete', 'group_editor')))
        self.assertIn(('delete', 'groups_by_hand'), core.calls)    # не осталось ни одного права
        self.assertIn(('update', 'custom_writer', ['permissions']), core.calls)  # лишнее право снято
        self.assertEqual(core._roles['custom_writer']['permissions'], ['schedule.write'])
        self.assertNotIn('manifest', [c[0] for c in core.calls])   # меню уже совпадает


if __name__ == '__main__':
    unittest.main()
