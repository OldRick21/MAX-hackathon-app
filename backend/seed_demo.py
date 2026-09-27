"""Explicit test fixture: python seed_demo.py USER_UUID."""
import argparse
from uuid import UUID
from database.create_tables import session_local
from database.tables import User, Institution, Membership, ServiceInstance

INSTITUTION = '83a927fc-6825-4944-9ed1-c27076100101'
SERVICE = '83a927fc-6825-4944-9ed1-c27076100102'

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
        if not db.get(ServiceInstance, SERVICE):
            db.add(ServiceInstance(id=SERVICE, institution_id=INSTITUTION, service_type='schedule', deployment='cloud', enabled=True, protected=False,
                client_base_url='https://195.133.197.144:8443', api_base_url='https://195.133.197.144:8443/api/v1', supported_profiles=['student'],
                manifest={'titles': {'ru': 'Расписание — демо', 'en': 'Demo schedule'}, 'menus': [{'id': 'home', 'titles': {'ru': 'Расписание', 'en': 'Schedule'}, 'entrypoint_path': '/', 'profiles': ['student'], 'required_permissions': [], 'order': 0}]}))
        db.commit()
    print('Demo university and service ready. Student membership assigned; no admin permissions granted.')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('user_id')
    seed(parser.parse_args().user_id)
