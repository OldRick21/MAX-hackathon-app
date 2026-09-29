def erase(subject, institution, service):
    from app.main import database
    with database() as db:
        db.execute('PRAGMA secure_delete=ON')
        db.execute('BEGIN IMMEDIATE')
        # Preserve shared lessons, remove the identifying association and free text
        # on affected lessons (which may contain the teacher's name).
        events = [r[0] for r in db.execute('SELECT event_id FROM event_teachers WHERE '
            'institution_id=? AND service_id=? AND user_id=?', (institution, service, subject))]
        for event in events:
            db.execute("UPDATE events SET title='Занятие', description='', revision=revision+1 WHERE "
                       'institution_id=? AND service_id=? AND id=?', (institution, service, event))
        db.execute('DELETE FROM event_teachers WHERE institution_id=? AND service_id=? AND user_id=?',
                   (institution, service, subject))
        # Cached responses can include the subject even when another actor created them.
        db.execute('DELETE FROM idempotency WHERE institution_id=? AND service_id=?', (institution, service))
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
