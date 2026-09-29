def erase(subject, institution, service):
    from app.main import database, FILES, TMP
    with database() as db:
        db.execute('PRAGMA secure_delete=ON')
        # Delete files before metadata so a failed unlink leaves a retryable reference.
        rows = db.execute('SELECT id, file_key FROM submissions WHERE institution_id=? AND service_id=? '
            'AND (student_id=? OR teacher_id=? OR reviewer_id=?)',
            (institution, service, subject, subject, subject)).fetchall()
        for row in rows:
            path = (FILES / row['file_key']).resolve()
            if not path.is_relative_to(FILES.resolve()):
                raise ValueError('File outside service storage')
            path.unlink(missing_ok=True)
            # All prior versions of this submission, including not-yet-due cleanup.
            directory = (FILES / institution / service / row['id']).resolve()
            if not directory.is_relative_to(FILES.resolve()):
                raise ValueError('Submission outside service storage')
            if directory.exists():
                for previous in directory.glob('*.pdf'):
                    previous.unlink(missing_ok=True)
        # Guard serializes all uploads with cleanup, so these are abandoned temporaries.
        for path in TMP.iterdir():
            if path.is_file():
                path.unlink()
        db.execute('DELETE FROM submissions WHERE institution_id=? AND service_id=? '
            'AND (student_id=? OR teacher_id=? OR reviewer_id=?)',
            (institution, service, subject, subject, subject))
        db.execute('DELETE FROM idempotency WHERE institution_id=? AND service_id=?', (institution, service))
        db.commit()
        # Remove replaced versions queued earlier, before confirming erasure.
        from app.main import cleanup_once
        cleanup_once()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
