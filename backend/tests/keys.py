"""Ключ процесса сервиса для тестов: то же, что «Выдать ключ» в карточке сервиса."""


def issue_key(service_id: str) -> dict:
    from database.create_tables import session_local
    from database.tables import ServiceInstance
    from platform_core import registry
    with session_local() as db:
        credential, secret = registry.issue_credential(db, db.get(ServiceInstance, service_id))
        db.commit()
        return {'client_id': credential.client_id, 'client_secret': secret}


def register_service(institution_id: str, code: str, host: str = 'svc.university.ru', enabled: bool = True,
                     manifest: dict = None) -> str:
    """Свой сервис вуза custom.<code> на одобренном хосте, по желанию — с опубликованным меню."""
    from database.create_tables import session_local
    from database.tables import InstitutionLocalHost
    from platform_core import registry
    with session_local() as db:
        if not db.get(InstitutionLocalHost, (institution_id, host)):
            db.add(InstitutionLocalHost(institution_id=institution_id, hostname=host, approved_by=None))
        service = registry.create_local_instance(db, institution_id, f'custom.{code}', f'https://{host}/api/v1',
                                                 f'https://{host}', {'ru': code}, ['admin', 'teacher', 'student'])
        if manifest:
            service.manifest = manifest
        service.enabled = enabled
        db.commit()
        return service.id
