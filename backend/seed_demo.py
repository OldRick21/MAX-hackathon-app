"""Explicit test fixture: python seed_demo.py USER_UUID."""
import argparse
from uuid import UUID
from database.create_tables import session_local
from database.tables import User, Institution, Membership
from platform_core import registry

INSTITUTION = '83a927fc-6825-4944-9ed1-c27076100101'

def seed(user_id):
    user_id = str(UUID(user_id))
    with session_local() as db:
        if not db.get(User, user_id):
            raise SystemExit('User not found. Sign in through MAX first; no changes made.')
        if not db.get(Institution, INSTITUTION):
            db.add(Institution(id=INSTITUTION, titles={'ru': 'Тестовый университет', 'en': 'Demo University'}, default_locale='ru', status='active'))
            db.flush()
        member = db.get(Membership, (INSTITUTION, user_id))
        if not member:
            db.add(Membership(institution_id=INSTITUTION, user_id=user_id, profiles=['student']))
        elif 'student' not in member.profiles:
            member.profiles = [*member.profiles, 'student']
        if not registry.admin_service_of(db, INSTITUTION):
            registry.create_admin_instance(db, INSTITUTION)
        db.commit()
    # Расписание, «Люди» и курсовые подключаются вузу как свои сервисы (custom.<код>) со своими контейнерами.
    print('Demo university ready. Student membership assigned; no admin permissions granted.')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('user_id')
    seed(parser.parse_args().user_id)
