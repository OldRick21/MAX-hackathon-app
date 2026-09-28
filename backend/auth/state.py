"""Live authorization state. Database failures must propagate, never mean inactive."""
from sqlalchemy import update
from database.tables import CoreSession, ServiceSession, User, ServiceCredential, ServiceInstance, Institution, Membership
from auth.security import timestamp
from database.base import utc_now


def alive(session):
    return session is not None and not session.is_revoked and timestamp(session.expires_at) > timestamp(utc_now())


def lock_core(db, session_id):
    # UPDATE also serializes writers in SQLite, where SELECT FOR UPDATE is ignored.
    db.execute(update(CoreSession).where(CoreSession.id == session_id).values(is_revoked=CoreSession.is_revoked)
               .execution_options(synchronize_session=False))
    return db.get(CoreSession, session_id, populate_existing=True)


def revoke_core(db, session):
    session.is_revoked = True
    db.execute(update(ServiceSession).where(ServiceSession.parent_session_id == session.id)
               .values(is_revoked=True).execution_options(synchronize_session=False))


def core_state(db, claims):
    session = db.get(CoreSession, claims['sid'], populate_existing=True)
    if not alive(session) or session.user_id != claims['sub'] or claims['exp'] > timestamp(session.expires_at):
        return None
    if claims.get('family_id', session.family_id) != session.family_id:
        return None
    user = db.get(User, session.user_id)
    return (user, session) if user else None


def machine_scopes(service):
    scopes = ['manifest:write', 'roles:read', 'roles:write', 'assignments:read',
              'assignments:write', 'profiles:read', 'groups:read', 'tokens:introspect']
    if service.service_type == 'administration':
        scopes = [s for s in scopes if s not in ('roles:write', 'assignments:write')] + ['institution:manage']
    return scopes


def machine_state(db, claims, require_active=True):
    credential = db.get(ServiceCredential, claims['credential_id'], populate_existing=True)
    if (not credential or credential.revoked_at is not None or credential.client_id != claims['sub']
            or credential.service_id != claims['service_id']):
        return None
    service = db.get(ServiceInstance, credential.service_id, populate_existing=True)
    if not service or service.institution_id != claims['institution_id']:
        return None
    institution = db.get(Institution, service.institution_id, populate_existing=True)
    if (not institution or (require_active and institution.status != 'active')
            or not set(claims['scopes']) <= set(machine_scopes(service))):
        return None
    # Disabled instances can still onboard via machine API.
    return credential, service


def service_state(db, claims):
    session = db.get(ServiceSession, claims['sid'], populate_existing=True)
    if not alive(session):
        return None
    expected = {'sub': session.user_id, 'parent_sid': session.parent_session_id,
                'institution_id': session.institution_id, 'service_id': session.service_id, 'profile': session.profile}
    if any(claims[k] != value for k, value in expected.items()):
        return None
    if claims.get('family_id', session.family_id) != session.family_id or claims['exp'] > timestamp(session.expires_at):
        return None
    parent = db.get(CoreSession, session.parent_session_id, populate_existing=True)
    if not alive(parent) or parent.user_id != session.user_id or claims['exp'] > timestamp(parent.expires_at):
        return None
    institution = db.get(Institution, session.institution_id, populate_existing=True)
    service = db.get(ServiceInstance, session.service_id, populate_existing=True)
    member = db.get(Membership, (session.institution_id, session.user_id), populate_existing=True)
    if (not institution or institution.status != 'active' or not service or not service.enabled
            or service.institution_id != session.institution_id or session.profile not in service.supported_profiles
            or not member or session.profile not in member.profiles):
        return None
    return session, parent
