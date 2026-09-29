def erase(subject, institution, service):
    from app.main import database
    with database() as db:
        db.execute('PRAGMA secure_delete=ON')
        db.execute('DELETE FROM cards WHERE institution_id=? AND service_id=? AND user_id=?',
                   (institution, service, subject))
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
