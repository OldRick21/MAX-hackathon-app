"""Курсовые работы — тестовый сторонний сервис вуза на минимальном SDK (app/sdk.py).

Подключается в админке как «Свой сервис» с кодом coursework и работает независимо от
платформы: свой процесс, своя БД и файлы, свой интерфейс в iframe. С ядром общается только
через machine API по ключу из админки. Другие сервисы (например, «Люди») не вызывает:
имена преподавателей и студентов пользователи указывают прямо здесь.
Правила загрузки и проверки — docs/services/coursework/SPEC.md §3; отличия — IMPLEMENTATION.md.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import unicodedata
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app import sdk

log = logging.getLogger('coursework')

SERVICE_CODE = 'coursework'
MANAGE = f'{SERVICE_CODE}.manage'
MANIFEST = {
    'titles': {'ru': 'Курсовые работы', 'en': 'Coursework'},
    'menus': [
        {'id': 'coursework', 'titles': {'ru': 'Курсовые работы', 'en': 'Coursework'}, 'entrypoint_path': '/coursework',
         'profiles': ['student', 'teacher'], 'required_permissions': [], 'order': 0},
        {'id': 'coursework_admin', 'titles': {'ru': 'Курсовые работы', 'en': 'Coursework'}, 'entrypoint_path': '/coursework',
         'profiles': ['admin'], 'required_permissions': [MANAGE], 'order': 0},
    ],
}
ROLES = [{'code': 'coursework_manager', 'titles': {'ru': 'Менеджер курсовых', 'en': 'Coursework manager'},
          'allowed_profiles': ['admin'], 'permissions': [MANAGE]}]

settings = sdk.Settings.from_env()
core = sdk.CoreClient(settings)
state = {'onboarding': 'pending'}
DATA = Path(os.environ.get('SERVICE_DATA', '/data'))
DB = DATA / 'coursework.db'
FILES = DATA / 'files'
TMP = DATA / 'tmp'
CLIENT = Path(__file__).resolve().parent.parent / 'client'

MAX_FILE = 20 * 1024 * 1024
MAX_BODY = 21 * 1024 * 1024
UPLOADS_PER_MINUTE = 10
UPLOADS_PARALLEL = 2
IDEMPOTENCY_TTL = 24 * 3600
CURSOR_TTL = 900
CLEANUP_MIN_AGE = 24 * 3600
UPLOAD_MAX_AGE = 600

Fail = sdk.Fail


def not_found():
    return Fail(404, 'RESOURCE_NOT_FOUND', 'Работа не найдена')


# --------------------------------------------------------------------------
# Хранилище
# --------------------------------------------------------------------------

@contextmanager
def database():
    db = sqlite3.connect(DB, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA busy_timeout=10000')
    try:
        yield db
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


SCHEMA = '''
CREATE TABLE IF NOT EXISTS submissions (
    institution_id TEXT NOT NULL, service_id TEXT NOT NULL, id TEXT NOT NULL,
    student_id TEXT NOT NULL, teacher_id TEXT NOT NULL, title TEXT NOT NULL,
    status TEXT NOT NULL, version INTEGER NOT NULL, revision INTEGER NOT NULL,
    file_key TEXT NOT NULL, file_name TEXT NOT NULL, file_size INTEGER NOT NULL, file_sha256 TEXT NOT NULL,
    review_decision TEXT, review_comment TEXT, reviewer_id TEXT, reviewed_at TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (institution_id, service_id, id));
CREATE INDEX IF NOT EXISTS submissions_by_student ON submissions (institution_id, service_id, student_id);
CREATE INDEX IF NOT EXISTS submissions_by_teacher ON submissions (institution_id, service_id, teacher_id);
CREATE TABLE IF NOT EXISTS people (
    institution_id TEXT NOT NULL, service_id TEXT NOT NULL, user_id TEXT NOT NULL,
    display_name TEXT NOT NULL, is_teacher INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (institution_id, service_id, user_id));
CREATE TABLE IF NOT EXISTS cleanup (file_key TEXT PRIMARY KEY, not_before REAL NOT NULL);
CREATE TABLE IF NOT EXISTS idempotency (
    institution_id TEXT NOT NULL, service_id TEXT NOT NULL, actor TEXT NOT NULL, key TEXT NOT NULL,
    fingerprint TEXT NOT NULL, submission_id TEXT NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY (institution_id, service_id, actor, key));
'''


def prepare():
    for path in (DATA, FILES, TMP):
        path.mkdir(parents=True, exist_ok=True)
    with database() as db:
        db.executescript(SCHEMA)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def cleanup_once() -> int:
    """Брошенные загрузки, старые версии и сироты; текущий файл работы не трогается."""
    removed = 0
    limit = time.time()
    for path in TMP.glob('*'):
        if path.is_file() and limit - path.stat().st_mtime > UPLOAD_MAX_AGE:
            path.unlink(missing_ok=True)
            removed += 1
    with database() as db:
        live = {r['file_key'] for r in db.execute('SELECT file_key FROM submissions WHERE deleted=0')}
        for row in db.execute('SELECT file_key FROM cleanup WHERE not_before < ?', (limit,)).fetchall():
            if row['file_key'] not in live:
                (FILES / row['file_key']).unlink(missing_ok=True)
                removed += 1
            db.execute('DELETE FROM cleanup WHERE file_key=?', (row['file_key'],))
        for path in FILES.rglob('*.pdf'):
            if path.relative_to(FILES).as_posix() not in live and limit - path.stat().st_mtime > CLEANUP_MIN_AGE:
                path.unlink(missing_ok=True)
                removed += 1
    return removed


async def cleanup_loop():
    while True:
        try:
            await run_in_threadpool(cleanup_once)
        except (OSError, sqlite3.Error):
            log.exception('cleanup failed')
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app):
    missing = settings.missing()
    if missing:
        raise RuntimeError(f"Не заданы настройки из админки: {', '.join(missing)}")
    try:
        prepare()
    except (OSError, sqlite3.Error) as error:
        raise RuntimeError(f'Хранилище {DATA} недоступно для записи: {error}') from error
    threading.Thread(target=sdk.onboard, args=(core, MANIFEST, ROLES, state), daemon=True).start()
    task = asyncio.create_task(cleanup_loop())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)
sdk.install(app, core, settings, MANIFEST, f'custom.{SERVICE_CODE}', state, CLIENT, ['/coursework'])
authenticate = app.state.authenticate


def manages(ctx: sdk.Ctx) -> bool:
    return ctx.profile == 'admin' and MANAGE in ctx.permissions


# --------------------------------------------------------------------------
# Имена: сервис независим и не вызывает «Людей» — пользователи представляются здесь
# --------------------------------------------------------------------------

class NameInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    display_name: str = Field(min_length=1, max_length=200)

    @field_validator('display_name')
    @classmethod
    def strip(cls, v):
        if not v.strip():
            raise ValueError('Укажите имя')
        return v.strip()


def names(db, ctx: sdk.Ctx, ids) -> dict:
    ids = sorted(set(ids))
    if not ids:
        return {}
    rows = db.execute(f'SELECT user_id, display_name FROM people WHERE institution_id=? AND service_id=? '
                      f'AND user_id IN ({",".join("?" * len(ids))})', (*ctx.tenant, *ids))
    return {r['user_id']: r['display_name'] for r in rows}


@app.get('/api/v1/coursework/me')
def get_me(ctx: sdk.Ctx = Depends(authenticate)):
    with database() as db:
        return {'user_id': ctx.user_id, 'profile': ctx.profile, 'manages': manages(ctx),
                'display_name': names(db, ctx, [ctx.user_id]).get(ctx.user_id)}


@app.put('/api/v1/coursework/me')
def set_me(body: NameInput, ctx: sdk.Ctx = Depends(authenticate)):
    """Имя для списков сервиса. Преподаватель с именем появляется в выборе проверяющего."""
    with database() as db:
        db.execute('INSERT INTO people VALUES (?,?,?,?,?) ON CONFLICT(institution_id, service_id, user_id) DO UPDATE '
                   'SET display_name=excluded.display_name, is_teacher=MAX(is_teacher, excluded.is_teacher)',
                   (*ctx.tenant, ctx.user_id, body.display_name, int(ctx.profile == 'teacher')))
    return get_me(ctx)


@app.get('/api/v1/coursework/teachers')
def teachers(ctx: sdk.Ctx = Depends(authenticate)):
    with database() as db:
        rows = db.execute('SELECT user_id, display_name FROM people WHERE institution_id=? AND service_id=? '
                          'AND is_teacher=1 AND user_id != ? ORDER BY display_name', (*ctx.tenant, ctx.user_id)).fetchall()
    return {'items': [{'user_id': r['user_id'], 'display_name': r['display_name']} for r in rows]}


# --------------------------------------------------------------------------
# Работы
# --------------------------------------------------------------------------

def etag(ctx, submission_id, revision):
    return sdk.etag_of(ctx, 'submission', submission_id, revision)


def row_json(row) -> dict:
    review = None
    if row['review_decision']:
        review = {'decision': row['review_decision'], 'comment': row['review_comment'] or '',
                  'reviewer_id': row['reviewer_id'], 'reviewed_at': row['reviewed_at']}
    return {'id': row['id'], 'student_id': row['student_id'], 'teacher_id': row['teacher_id'], 'title': row['title'],
            'status': row['status'], 'version': row['version'],
            'file': {'original_name': row['file_name'], 'size_bytes': row['file_size'],
                     'media_type': 'application/pdf', 'sha256': row['file_sha256']},
            'review': review, 'created_at': row['created_at'], 'updated_at': row['updated_at']}


def respond(ctx, row, status=200, extra=None) -> JSONResponse:
    return JSONResponse(row_json(row), status_code=status, headers={'ETag': etag(ctx, row['id'], row['revision']), **(extra or {})})


def load(db, ctx, submission_id):
    return db.execute('SELECT * FROM submissions WHERE institution_id=? AND service_id=? AND id=? AND deleted=0',
                      (*ctx.tenant, submission_id)).fetchone()


def visible_row(db, ctx, submission_id):
    row = load(db, ctx, submission_id)
    if not row or not (manages(ctx) or (ctx.profile == 'student' and row['student_id'] == ctx.user_id)
                       or (ctx.profile == 'teacher' and row['teacher_id'] == ctx.user_id)):
        raise not_found()
    return row


def clean_name(name: str) -> str:
    name = (name or '').replace('\\', '/').rsplit('/', 1)[-1]
    name = ''.join(ch for ch in unicodedata.normalize('NFC', name) if unicodedata.category(ch)[0] != 'C').strip()
    return (name or 'coursework.pdf')[:255]


class Limiter:
    """10 загрузок в минуту и 2 одновременные на пользователя экземпляра."""

    def __init__(self):
        self.lock = threading.Lock()
        self.recent, self.active = {}, {}

    def enter(self, key):
        with self.lock:
            now = time.time()
            recent = [t for t in self.recent.get(key, []) if now - t < 60]
            if len(recent) >= UPLOADS_PER_MINUTE or self.active.get(key, 0) >= UPLOADS_PARALLEL:
                retry = str(max(1, int(60 - (now - recent[0])))) if len(recent) >= UPLOADS_PER_MINUTE else '5'
                raise Fail(429, 'RATE_LIMITED', 'Слишком много загрузок подряд. Подождите минуту', headers={'Retry-After': retry})
            self.recent[key] = recent + [now]
            self.active[key] = self.active.get(key, 0) + 1

    def leave(self, key):
        with self.lock:
            self.active[key] = max(0, self.active.get(key, 1) - 1)


limiter = Limiter()


@dataclass
class Upload:
    path: Path
    name: str
    size: int
    sha256: str
    fields: dict


def looks_like_pdf(path: Path, size: int) -> bool:
    """Сигнатура и хвост структуры PDF: отсев не-PDF, не антивирус."""
    with open(path, 'rb') as f:
        head = f.read(1024)
        f.seek(max(0, size - 2048))
        tail = f.read()
    return head.startswith(b'%PDF-') and b'%%EOF' in tail and b'startxref' in tail


async def receive_upload(request: Request, allowed: set) -> Upload:
    declared = request.headers.get('content-length')
    if declared and declared.isdigit() and int(declared) > MAX_BODY:
        raise Fail(413, 'PAYLOAD_TOO_LARGE', 'Файл слишком большой. Максимум — 20 МБ')
    if not request.headers.get('content-type', '').startswith('multipart/form-data'):
        raise Fail(415, 'UNSUPPORTED_MEDIA_TYPE', 'Нужна отправка формы multipart/form-data')
    spool = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, dir=TMP)
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY:
            spool.close()
            raise Fail(413, 'PAYLOAD_TOO_LARGE', 'Файл слишком большой. Максимум — 20 МБ')
        spool.write(chunk)
    spool.seek(0)

    async def replay():
        data = spool.read(64 * 1024)
        return {'type': 'http.request', 'body': data, 'more_body': bool(data)}

    try:
        form = await Request(request.scope, replay).form(max_files=1, max_fields=len(allowed))
    except Exception:
        spool.close()
        raise Fail(400, 'BAD_REQUEST', 'Некорректная форма загрузки')
    try:
        keys = [k for k, _ in form.multi_items()]
        if set(keys) != allowed or len(keys) != len(allowed):
            raise Fail(422, 'VALIDATION_ERROR', 'Форма должна содержать ровно поля: ' + ', '.join(sorted(allowed)))
        upload = form['file']
        if isinstance(upload, str):
            raise Fail(422, 'VALIDATION_ERROR', 'Поле file должно быть файлом')
        if (upload.content_type or '').split(';')[0].strip().lower() != 'application/pdf':
            raise Fail(415, 'UNSUPPORTED_MEDIA_TYPE', 'Можно загрузить только PDF-файл')
        target = TMP / f'{uuid.uuid4()}.part'
        digest, size = hashlib.sha256(), 0
        with open(target, 'wb') as out:
            while chunk := await upload.read(256 * 1024):
                size += len(chunk)
                if size > MAX_FILE:
                    out.close()
                    target.unlink(missing_ok=True)
                    raise Fail(413, 'PAYLOAD_TOO_LARGE', 'Файл слишком большой. Максимум — 20 МБ')
                digest.update(chunk)
                out.write(chunk)
        if size == 0 or not looks_like_pdf(target, size):
            target.unlink(missing_ok=True)
            raise Fail(415, 'UNSUPPORTED_MEDIA_TYPE', 'Файл не похож на PDF')
        return Upload(target, clean_name(upload.filename), size, digest.hexdigest(),
                      {k: v for k, v in form.items() if k != 'file'})
    finally:
        await form.close()
        spool.close()


def store(ctx, upload: Upload, submission_id: str, version: int) -> str:
    """Новый неизменяемый ключ файла до commit: после сбоя файл — сирота для очистки."""
    key = f'{ctx.tenant[0]}/{ctx.tenant[1]}/{submission_id}/{version}-{secrets.token_hex(8)}.pdf'
    (FILES / key).parent.mkdir(parents=True, exist_ok=True)
    os.replace(upload.path, FILES / key)
    return key


def sign(payload: str) -> str:
    return hmac.new(settings.client_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


@app.get('/api/v1/coursework/submissions')
def list_submissions(status: Optional[Literal['submitted', 'accepted', 'changes_requested']] = Query(None),
                     limit: int = Query(50, ge=1, le=100), cursor: Optional[str] = Query(None, max_length=2048),
                     ctx: sdk.Ctx = Depends(authenticate)):
    scope = json.dumps([*ctx.tenant, ctx.user_id, ctx.profile, manages(ctx), status or '', limit])
    after = None
    if cursor:
        payload, sep, signature = cursor.rpartition('.')
        try:
            if not sep or not hmac.compare_digest(signature, sign(scope + payload)):
                raise ValueError()
            created, last_id, expiry = json.loads(payload)
            if expiry < time.time():
                raise ValueError()
            after = (created, last_id)
        except (ValueError, TypeError):
            raise Fail(400, 'INVALID_CURSOR', 'Недействительный курсор')
    sql = ['SELECT * FROM submissions WHERE institution_id=? AND service_id=? AND deleted=0']
    args = list(ctx.tenant)
    if not manages(ctx):
        if ctx.profile == 'student':
            sql.append('AND student_id=?')
        elif ctx.profile == 'teacher':
            sql.append('AND teacher_id=?')
        else:
            raise Fail(403, 'FORBIDDEN', 'Нужна роль «Менеджер курсовых»')
        args.append(ctx.user_id)
    if status:
        sql.append('AND status=?')
        args.append(status)
    if after:
        sql.append('AND (created_at < ? OR (created_at = ? AND id < ?))')
        args += [after[0], after[0], after[1]]
    sql.append('ORDER BY created_at DESC, id DESC LIMIT ?')
    args.append(limit + 1)
    with database() as db:
        rows = db.execute(' '.join(sql), args).fetchall()
        page = rows[:limit]
        people = names(db, ctx, [r['student_id'] for r in page] + [r['teacher_id'] for r in page])
    next_cursor = None
    if len(rows) > limit:
        payload = json.dumps([page[-1]['created_at'], page[-1]['id'], int(time.time()) + CURSOR_TTL])
        next_cursor = payload + '.' + sign(scope + payload)
    return {'items': [row_json(r) for r in page], 'next_cursor': next_cursor, 'people': people}


@app.post('/api/v1/coursework/submissions', status_code=201)
async def create_submission(request: Request, idempotency: Optional[str] = Header(None, alias='Idempotency-Key')):
    ctx = await run_in_threadpool(authenticate, request)
    if ctx.profile != 'student':
        raise Fail(403, 'FORBIDDEN', 'Загружать работы могут студенты')
    try:
        key = str(UUID(idempotency or ''))
    except ValueError:
        raise Fail(400, 'BAD_REQUEST', 'Нужен заголовок Idempotency-Key (UUID)')
    limit_key = (*ctx.tenant, ctx.user_id)
    limiter.enter(limit_key)
    try:
        upload = await receive_upload(request, {'title', 'teacher_id', 'file'})
    finally:
        limiter.leave(limit_key)
    try:
        title = str(upload.fields.get('title', '')).strip()
        if not title or len(title) > 200:
            raise Fail(422, 'VALIDATION_ERROR', 'Укажите тему работы (до 200 символов)', [{'path': 'title', 'message': '1–200 символов'}])
        try:
            teacher_id = str(UUID(str(upload.fields.get('teacher_id', ''))))
        except ValueError:
            raise Fail(422, 'VALIDATION_ERROR', 'Выберите преподавателя', [{'path': 'teacher_id', 'message': 'Нужен UUID'}])
        if teacher_id == ctx.user_id:
            raise Fail(422, 'INVALID_REFERENCE', 'Нельзя назначить проверяющим себя')
        if 'teacher' not in await run_in_threadpool(core.profiles, teacher_id):
            raise Fail(422, 'INVALID_REFERENCE', 'Проверяющим может быть только преподаватель вуза',
                       [{'path': 'teacher_id', 'message': 'Нет профиля «Преподаватель»'}])
        fingerprint = hashlib.sha256(json.dumps([title, teacher_id, upload.name, upload.sha256]).encode()).hexdigest()
        # Повторная авторизация перед commit: доступ мог пропасть за время загрузки.
        ctx = await run_in_threadpool(authenticate, request)
        return await run_in_threadpool(commit_create, ctx, key, fingerprint, title, teacher_id, upload)
    finally:
        upload.path.unlink(missing_ok=True)


def commit_create(ctx, key, fingerprint, title, teacher_id, upload: Upload):
    submission_id = str(uuid.uuid4())
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM idempotency WHERE created_at < ?', (time.time() - IDEMPOTENCY_TTL,))
        seen = db.execute('SELECT * FROM idempotency WHERE institution_id=? AND service_id=? AND actor=? AND key=?',
                          (*ctx.tenant, ctx.user_id, key)).fetchone()
        if seen:
            if seen['fingerprint'] != fingerprint:
                raise Fail(409, 'IDEMPOTENCY_CONFLICT', 'Этот Idempotency-Key уже использован для другой загрузки')
            row = load(db, ctx, seen['submission_id'])
            if not row:
                raise not_found()
            return respond(ctx, row, 201, {'Location': f'/api/v1/coursework/submissions/{row["id"]}'})
        file_key = store(ctx, upload, submission_id, 1)
        stamp = now_iso()
        db.execute('INSERT INTO submissions (institution_id, service_id, id, student_id, teacher_id, title, status, version, '
                   'revision, file_key, file_name, file_size, file_sha256, created_at, updated_at) '
                   "VALUES (?,?,?,?,?,?,'submitted',1,1,?,?,?,?,?,?)",
                   (*ctx.tenant, submission_id, ctx.user_id, teacher_id, title, file_key, upload.name, upload.size,
                    upload.sha256, stamp, stamp))
        db.execute('INSERT INTO idempotency VALUES (?,?,?,?,?,?,?)',
                   (*ctx.tenant, ctx.user_id, key, fingerprint, submission_id, time.time()))
        row = load(db, ctx, submission_id)
        db.commit()
    return respond(ctx, row, 201, {'Location': f'/api/v1/coursework/submissions/{submission_id}'})


@app.get('/api/v1/coursework/submissions/{submission_id}')
def get_submission(submission_id: UUID, ctx: sdk.Ctx = Depends(authenticate)):
    with database() as db:
        return respond(ctx, visible_row(db, ctx, str(submission_id)))


@app.put('/api/v1/coursework/submissions/{submission_id}/file')
async def replace_file(submission_id: UUID, request: Request, if_match: Optional[str] = Header(None)):
    ctx = await run_in_threadpool(authenticate, request)
    if not if_match:
        raise Fail(428, 'PRECONDITION_REQUIRED', 'Сначала загрузите актуальную версию работы')
    with database() as db:
        row = visible_row(db, ctx, str(submission_id))
    if ctx.profile != 'student' or row['student_id'] != ctx.user_id:
        raise Fail(403, 'FORBIDDEN', 'Заменить файл может только автор работы')
    limit_key = (*ctx.tenant, ctx.user_id)
    limiter.enter(limit_key)
    try:
        upload = await receive_upload(request, {'file'})
    finally:
        limiter.leave(limit_key)
    try:
        ctx = await run_in_threadpool(authenticate, request)
        return await run_in_threadpool(commit_replace, ctx, str(submission_id), if_match, upload)
    finally:
        upload.path.unlink(missing_ok=True)


def commit_replace(ctx, submission_id, if_match, upload: Upload):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = visible_row(db, ctx, submission_id)
        sdk.check_if_match(if_match, etag(ctx, row['id'], row['revision']))
        if row['status'] not in ('submitted', 'changes_requested'):
            raise Fail(409, 'INVALID_STATE', 'Принятую работу изменить нельзя')
        version = row['version'] + 1
        file_key = store(ctx, upload, row['id'], version)
        db.execute('INSERT OR REPLACE INTO cleanup VALUES (?,?)', (row['file_key'], time.time() + CLEANUP_MIN_AGE))
        db.execute("UPDATE submissions SET status='submitted', version=?, revision=revision+1, file_key=?, file_name=?, "
                   'file_size=?, file_sha256=?, review_decision=NULL, review_comment=NULL, reviewer_id=NULL, '
                   'reviewed_at=NULL, updated_at=? WHERE institution_id=? AND service_id=? AND id=?',
                   (version, file_key, upload.name, upload.size, upload.sha256, now_iso(), *ctx.tenant, row['id']))
        row = load(db, ctx, row['id'])
        db.commit()
    return respond(ctx, row)


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision: Literal['accepted', 'changes_requested']
    comment: str = Field(max_length=2000)


@app.put('/api/v1/coursework/submissions/{submission_id}/review')
def review(submission_id: UUID, body: ReviewInput, if_match: Optional[str] = Header(None),
           ctx: sdk.Ctx = Depends(authenticate)):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = visible_row(db, ctx, str(submission_id))
        if ctx.profile != 'teacher' or row['teacher_id'] != ctx.user_id or row['student_id'] == ctx.user_id:
            raise Fail(403, 'FORBIDDEN', 'Проверить работу может только назначенный преподаватель')
        sdk.check_if_match(if_match, etag(ctx, row['id'], row['revision']))
        if row['status'] != 'submitted':
            raise Fail(409, 'INVALID_STATE', 'Решение по этой версии уже вынесено')
        stamp = now_iso()
        db.execute('UPDATE submissions SET status=?, revision=revision+1, review_decision=?, review_comment=?, '
                   'reviewer_id=?, reviewed_at=?, updated_at=? WHERE institution_id=? AND service_id=? AND id=?',
                   (body.decision, body.decision, body.comment.strip(), ctx.user_id, stamp, stamp, *ctx.tenant, row['id']))
        row = load(db, ctx, row['id'])
        db.commit()
    return respond(ctx, row)


@app.delete('/api/v1/coursework/submissions/{submission_id}', status_code=204)
def delete_submission(submission_id: UUID, if_match: Optional[str] = Header(None), ctx: sdk.Ctx = Depends(authenticate)):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = visible_row(db, ctx, str(submission_id))
        author = ctx.profile == 'student' and row['student_id'] == ctx.user_id
        if not (author or manages(ctx)):
            raise Fail(403, 'FORBIDDEN', 'Удалить работу может автор или менеджер курсовых')
        sdk.check_if_match(if_match, etag(ctx, row['id'], row['revision']))
        if not manages(ctx) and row['status'] == 'accepted':
            raise Fail(409, 'INVALID_STATE', 'Принятую работу удалить нельзя')
        db.execute('UPDATE submissions SET deleted=1, revision=revision+1, updated_at=? '
                   'WHERE institution_id=? AND service_id=? AND id=?', (now_iso(), *ctx.tenant, row['id']))
        db.execute('INSERT OR REPLACE INTO cleanup VALUES (?,?)', (row['file_key'], time.time()))
        db.commit()
    return Response(status_code=204)


SAFE_ASCII = re.compile(r'[^A-Za-z0-9._-]+')


@app.get('/api/v1/coursework/submissions/{submission_id}/file')
def download(submission_id: UUID, ctx: sdk.Ctx = Depends(authenticate)):
    with database() as db:
        row = visible_row(db, ctx, str(submission_id))
    path = FILES / row['file_key']
    if not path.is_file():
        raise Fail(503, 'FILE_STORAGE_UNAVAILABLE', 'Файл временно недоступен')
    ascii_name = SAFE_ASCII.sub('_', row['file_name']).strip('_') or 'coursework.pdf'
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(row['file_name'])}"
    return FileResponse(path, media_type='application/pdf',
                        headers={'Content-Disposition': disposition, 'X-Content-Type-Options': 'nosniff',
                                 'ETag': etag(ctx, row['id'], row['revision'])})
