"""Consent evidence and durable, service-scoped erasure tasks. No service schemas here."""
import hashlib
import json
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import Column, String, Text, DateTime, update
from sqlalchemy.orm import Session

from database.base import table_class, utc_now, generate_uuid
from database.create_tables import get_db
from database.tables import (User, Membership, ServiceInstance, CoreSession, ServiceSession,
                             AuditEvent, IdempotencyRecord, JoinRequest, InstitutionApplication)
from auth.dependencies import get_current_core_session, get_current_machine_token
from platform_core.errors import DomainError

VERSION = 'demo-2026-09-29-v1'
TEXT = '''Демонстрационное согласие на обработку персональных данных
Оператор: [указать ФИО/наименование и адрес]. Контакт для обращений: [указать].
Цели: регистрация в «Вузы России», участие в выбранных вузах, работа с профилем,
расписанием и курсовыми. Данные: идентификатор MAX и внутренний ID, имя, аватар,
вуз, группа, роли, сведения профиля, учебные работы и отзывы. Операции: сбор,
запись, хранение, уточнение, использование, предоставление участникам выбранного
вуза в пределах их прав и сервисам этого вуза, удаление. Публичная публикация
не предусмотрена. Необязательные поля профиля и аватар можно не заполнять.
Согласие действует до отзыва или завершения целей обработки. Отозвать его можно
в разделе «Персональные данные». Доступ прекращается, данные удаляются из ядра
и подключённых сервисов; состояние удаления доступно по выданной квитанции.
Срок выполнения удаления — не более 30 дней при отсутствии иного основания.
Технические подтверждения хранятся 30 дней после завершения удаления.
Это текст для проверки прототипа. До работы с реальными данными необходимо
указать оператора, получателей и контакт, согласовать основания и сроки хранения.'''
POLICY = '''Демонстрационная политика: данные используются только для функций
выбранных вузов, хранятся в инфраструктуре платформы и подключённых сервисов.
Доступ ограничивается вузом и ролью. Пользователь может исправить профиль,
отозвать согласие, запросить сведения и исправление данных через раздел
«Персональные данные». Запрос регистрируется с контрольным сроком 10 рабочих
дней. Резервные копии и журналы должны иметь утверждённые сроки очистки;
восстановление копий должно сопровождаться повторным применением удалений.
Оператор и контакт: [заполнить перед использованием реальных данных].'''

class Consent(table_class):
    __tablename__ = 'privacy_consents'
    user_id = Column(String(36), primary_key=True)  # evidence deliberately survives user deletion
    version = Column(String(80), nullable=False)
    document = Column(Text, nullable=False)
    document_hash = Column(String(64), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    withdrawn_at = Column(DateTime(timezone=True))

class Challenge(table_class):
    __tablename__ = 'privacy_challenges'
    id = Column(String(64), primary_key=True)
    version = Column(String(80), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

class Erasure(table_class):
    __tablename__ = 'privacy_erasures'
    id = Column(String(36), primary_key=True, default=generate_uuid)
    subject_id = Column(String(36), nullable=False, index=True)
    receipt_hash = Column(String(64), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at = Column(DateTime(timezone=True))

class ErasureTask(table_class):
    __tablename__ = 'privacy_tasks'
    id = Column(String(36), primary_key=True, default=generate_uuid)
    erasure_id = Column(String(36), nullable=False, index=True)
    service_id = Column(String(36), nullable=False, index=True)
    institution_id = Column(String(36), nullable=False)
    subject_id = Column(String(36), nullable=False)
    completed_at = Column(DateTime(timezone=True))
    verification = Column(Text, nullable=True)

class Disclosure(table_class):
    __tablename__ = 'privacy_disclosures'
    subject_id = Column(String(36), primary_key=True)
    service_id = Column(String(36), primary_key=True)
    institution_id = Column(String(36), nullable=False)

class PrivacyRequest(table_class):
    __tablename__ = 'privacy_requests'
    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    due_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(20), default='pending', nullable=False)

def active(db, user_id):
    row = db.get(Consent, user_id)
    return bool(row and row.withdrawn_at is None)

def remember(db, user_id, service):
    if not db.get(Disclosure, (user_id, service.id)):
        db.add(Disclosure(subject_id=user_id, service_id=service.id, institution_id=service.institution_id))

def housekeeping():
    """Bound retention of completed receipts/evidence; pending tasks are never lost."""
    from database.create_tables import session_local
    with session_local() as db:
        cutoff = utc_now() - timedelta(days=30)
        for job in db.query(Erasure).filter(Erasure.completed_at < cutoff).all():
            db.query(ErasureTask).filter_by(erasure_id=job.id).delete(synchronize_session=False)
            db.query(Disclosure).filter_by(subject_id=job.subject_id).delete(synchronize_session=False)
            db.query(Consent).filter_by(user_id=job.subject_id).delete(synchronize_session=False)
            db.delete(job)
        db.query(Challenge).filter(Challenge.expires_at < utc_now()).delete(synchronize_session=False)
        db.query(PrivacyRequest).filter(PrivacyRequest.status == 'resolved', PrivacyRequest.created_at < cutoff).delete(synchronize_session=False)
        db.commit()
    # Reclaim deleted bytes in the local SQLite WAL; backups need separate operations.
    from database.create_tables import engine
    if engine.dialect.name == 'sqlite':
        with engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA wal_checkpoint(TRUNCATE)')

async def maintenance():
    import asyncio
    import logging
    while True:
        try:
            await asyncio.to_thread(housekeeping)
        except Exception as error:
            logging.getLogger('privacy').warning('Privacy maintenance deferred: %s', type(error).__name__)
        await asyncio.sleep(3600)

def accept(db, user, data):
    if active(db, user.id):
        return
    if data.consent_version != VERSION or not data.consent_challenge:
        raise DomainError(403, 'CONSENT_REQUIRED', 'Необходимо отдельное согласие на обработку данных')
    consumed = db.query(Challenge).filter(Challenge.id == data.consent_challenge,
        Challenge.version == VERSION, Challenge.expires_at > utc_now()).delete(synchronize_session=False)
    if consumed != 1:
        raise DomainError(409, 'CONSENT_EXPIRED', 'Откройте текст согласия ещё раз')
    db.add(Consent(user_id=user.id, version=VERSION, document=TEXT,
                   document_hash=hashlib.sha256(TEXT.encode()).hexdigest()))

router = APIRouter()

@router.get('/api/v1/privacy/document')
def document():
    return {'version': VERSION, 'text': TEXT, 'policy': POLICY, 'demo': True,
            'hash': hashlib.sha256(TEXT.encode()).hexdigest()}

@router.post('/api/v1/privacy/challenge')
def challenge(request: Request, db: Session = Depends(get_db)):
    from platform_core import ratelimit
    ratelimit.hit('login', request.headers.get('x-real-ip') or (request.client.host if request.client else None))
    # Public, short-lived, no identity or initData persisted.
    db.query(Challenge).filter(Challenge.expires_at < utc_now()).delete(synchronize_session=False)
    value = secrets.token_urlsafe(32)
    db.add(Challenge(id=value, version=VERSION, expires_at=utc_now() + timedelta(minutes=10)))
    db.commit()
    return {'challenge': value, 'version': VERSION}

@router.get('/api/v1/privacy/me')
def mine(state=Depends(get_current_core_session), db: Session = Depends(get_db)):
    row = db.get(Consent, state[0].id)
    return {'version': row.version, 'accepted_at': row.accepted_at, 'text': row.document}

@router.post('/api/v1/privacy/withdraw')
def withdraw(state=Depends(get_current_core_session), db: Session = Depends(get_db),
             x_erasure_receipt: str | None = Header(None, min_length=64, max_length=64, pattern='^[0-9a-f]{64}$')):
    result = erase_user(db, state[0], x_erasure_receipt or secrets.token_urlsafe(32))
    db.commit()
    return result

def erase_user(db, user, receipt=None):
    """Shared with the operator deletion path; caller owns the transaction."""
    # Serialize against core mutations before collecting recipients and cascading deletion.
    db.execute(update(User).where(User.id == user.id).values(id=User.id))
    receipt = receipt or secrets.token_urlsafe(32)
    job = Erasure(subject_id=user.id, receipt_hash=hashlib.sha256(receipt.encode()).hexdigest())
    db.add(job)
    db.flush()
    recipients = {r.service_id: r.institution_id for r in db.query(Disclosure).filter_by(subject_id=user.id)}
    institutions = [m.institution_id for m in db.query(Membership).filter_by(user_id=user.id)]
    # Include disabled/uninstalled instances: they can still hold data.
    for service in db.query(ServiceInstance).filter(ServiceInstance.institution_id.in_(institutions)):
        recipients[service.id] = service.institution_id
    for sid, iid in recipients.items():
        db.add(ErasureTask(erasure_id=job.id, subject_id=user.id, service_id=sid, institution_id=iid))
    consent = db.get(Consent, user.id)
    if consent:
        consent.withdrawn_at = utc_now()
    for row in db.query(CoreSession).filter_by(user_id=user.id):
        from auth.state import revoke_core
        revoke_core(db, row)
    from auth.models import RefreshUse
    session_ids = [r.id for r in db.query(CoreSession).filter_by(user_id=user.id)]
    session_ids += [r.id for r in db.query(ServiceSession).filter_by(user_id=user.id)]
    db.query(RefreshUse).filter(RefreshUse.session_id.in_(session_ids)).delete(synchronize_session=False)
    # Free text can contain the subject's name: discard affected cached results.
    db.query(IdempotencyRecord).filter(IdempotencyRecord.institution_id.in_(institutions)).delete(synchronize_session=False)
    db.query(IdempotencyRecord).filter_by(actor_user_id=user.id).delete(synchronize_session=False)
    related = {user.id}
    related.update(r.id for r in db.query(JoinRequest).filter_by(user_id=user.id))
    related.update(r.id for r in db.query(InstitutionApplication).filter_by(applicant_user_id=user.id))
    for row in db.query(AuditEvent).all():
        if row.actor_user_id == user.id or any(ref in (row.target_id or '') or ref in json.dumps(row.details) for ref in related):
            row.actor_user_id = None
            row.target_id = None
            row.details = {}
    for model in (JoinRequest, InstitutionApplication):
        db.query(model).filter(model.reviewed_by == user.id).update({'reviewed_by': None}, synchronize_session=False)
    db.query(PrivacyRequest).filter_by(user_id=user.id).delete(synchronize_session=False)
    db.delete(user)
    if not recipients:
        job.completed_at = utc_now()
    return {'receipt': receipt, 'status': 'completed' if job.completed_at else 'pending'}

@router.get('/api/v1/privacy/erasure-status')
def erasure_status(authorization: str = Header(''), db: Session = Depends(get_db)):
    token = authorization.removeprefix('Bearer ')
    job = db.query(Erasure).filter_by(receipt_hash=hashlib.sha256(token.encode()).hexdigest()).first()
    if not job:
        raise DomainError(404, 'RESOURCE_NOT_FOUND', 'Квитанция не найдена')
    pending = db.query(ErasureTask).filter_by(erasure_id=job.id, completed_at=None).count()
    return {'status': 'pending' if pending else 'completed', 'pending_services': pending,
            'created_at': job.created_at, 'completed_at': job.completed_at,
            'deadline': job.created_at + timedelta(days=30)}

@router.get('/api/v1/internal/privacy/tasks')
def tasks(claims=Depends(get_current_machine_token), db: Session = Depends(get_db)):
    rows = db.query(ErasureTask).filter_by(service_id=claims['service_id'], completed_at=None).all()
    return {'items': [{'task_id': r.id, 'subject_id': r.subject_id, 'institution_id': r.institution_id,
                       'action': 'restrict_and_erase'} for r in rows]}

@router.post('/api/v1/internal/privacy/tasks/{task_id}/complete')
def complete(task_id: str, claims=Depends(get_current_machine_token), db: Session = Depends(get_db)):
    row = db.get(ErasureTask, task_id)
    if not row or row.service_id != claims['service_id']:
        raise DomainError(404, 'RESOURCE_NOT_FOUND', 'Задание не найдено')
    finish_task(db, row)
    db.commit()
    return {'status': 'completed'}

def finish_task(db, row):
    row.completed_at = row.completed_at or utc_now()
    db.flush()
    if not db.query(ErasureTask).filter_by(erasure_id=row.erasure_id, completed_at=None).count():
        job = db.get(Erasure, row.erasure_id)
        job.completed_at = job.completed_at or utc_now()

from pydantic import BaseModel, Field
class RequestBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)

@router.post('/api/v1/privacy/requests', status_code=201)
def request_information(body: RequestBody, state=Depends(get_current_core_session), db: Session = Depends(get_db)):
    due = utc_now()
    for _ in range(10):
        due += timedelta(days=1)
        while due.weekday() >= 5:
            due += timedelta(days=1)
    row = PrivacyRequest(user_id=state[0].id, message=body.message, due_at=due)
    db.add(row)
    db.commit()
    return {'id': row.id, 'status': row.status, 'due_at': row.due_at}
