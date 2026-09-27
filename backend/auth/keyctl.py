"""Operator CLI; run inside the core container with its persistent key volume."""
import argparse
import json
from auth.security import security


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init')
    commands.add_parser('status')
    commands.add_parser('prepare').add_argument('purpose', choices=['access', 'refresh'])
    commands.add_parser('activate').add_argument('kid')
    commands.add_parser('prune')
    commands.add_parser('cleanup-history')
    args = parser.parse_args()
    ring = security.keys
    if args.command == 'init':
        ring.initialize()
    elif args.command == 'status':
        print(json.dumps(ring.status(), indent=2))
    elif args.command == 'prepare':
        print(ring.prepare(args.purpose))
    elif args.command == 'activate':
        try:
            ring.activate(args.kid)
        except ValueError as exc:
            parser.error(str(exc))
    elif args.command == 'prune':
        print(json.dumps(ring.prune()))
    elif args.command == 'cleanup-history':
        from database.create_tables import session_local
        from auth.models import RefreshUse
        from database.base import utc_now
        with session_local() as db:
            count = db.query(RefreshUse).filter(RefreshUse.expires_at < utc_now()).delete(synchronize_session=False)
            db.commit()
        print(count)


if __name__ == '__main__':
    main()
