"""Локальный сервис курсовых: загрузка PDF, проверка преподавателем, своя SQLite и файловый том.

Контракт — docs/services/coursework/SPEC.md и OPENAPI.yaml; отличия — IMPLEMENTATION.md.
Сервис разворачивает вуз. При старте он сам создаёт роль coursework_manager и публикует
меню в ядре по ключу, выданному в администрировании; затем администратор включает его.
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
from uuid import UUID

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.core_client import Binding, CoreClient, CoreUnavailable

log = logging.getLogger('coursework')

CORE = os.environ.get('CORE_URL', 'http://backend:8000')
CLIENT_ID = os.environ.get('COURSEWORK_CLIENT_ID', '')
CLIENT_SECRET = os.environ.get('COURSEWORK_CLIENT_SECRET', '')
API_BASE_URL = os.environ.get('COURSEWORK_API_BASE_URL', '')
CLIENT_BASE_URL = os.environ.get('COURSEWORK_CLIENT_BASE_URL', '')
# Origin оболочки: экраны курсовых рисует она и обращается к API этого сервиса напрямую (CORS).
SHELL_ORIGIN = os.environ.get('SHELL_ORIGIN', '')
DATA = Path(os.environ.get('COURSEWORK_DATA', '/data'))
DB = DATA / 'coursework.db'
FILES = DATA / 'files'
TMP = DATA / 'tmp'
core = CoreClient(CORE, CLIENT_ID, CLIENT_SECRET, API_BASE_URL, CLIENT_BASE_URL)

MAX_FILE = 20 * 1024 * 1024
MAX_BODY = 21 * 1024 * 1024
UPLOADS_PER_MINUTE = 10
UPLOADS_PARALLEL = 2
IDEMPOTENCY_TTL = 24 * 3600
CURSOR_TTL = 900
CLEANUP_MIN_AGE = 24 * 3600
UPLOAD_MAX_AGE = 600

MANIFEST = {
    'titles': {'ru': 'Курсовые работы', 'en': 'Coursework'},
    'menus': [
        {'id': 'coursework', 'titles': {'ru': 'Курсовые', 'en': 'Coursework'}, 'entrypoint_path': '/coursework',
         'profiles': ['student', 'teacher'], 'required_permissions': [], 'order': 0},
        {'id': 'coursework_admin', 'titles': {'ru': 'Курсовые', 'en': 'Coursework'}, 'entrypoint_path': '/coursework',
         'profiles': ['admin'], 'required_permissions': ['coursework.manage'], 'order': 0},
    ],
}
MANAGER_ROLE = {'code': 'coursework_manager', 'titles': {'ru': 'Менеджер курсовых', 'en': 'Coursework manager'},
                'allowed_profiles': ['admin'], 'permissions': ['coursework.manage']}


# --------------------------------------------------------------------------
# Ошибки
# --------------------------------------------------------------------------

class Fail(Exception):
    def __init__(self, status: int, code: str, message: str, details: Optional[list] = None, headers=None):
        self.status, self.code, self.message, self.details, self.headers = status, code, message, details, headers


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
CREATE TABLE IF NOT EXISTS cleanup (
    file_key TEXT PRIMARY KEY, not_before REAL NOT NULL);
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


# --------------------------------------------------------------------------
# Подключение к ядру: роль и меню публикует сам сервис (SPEC §2)
# --------------------------------------------------------------------------

onboarding = {'state': 'pending', 'error': None}


def onboard() -> None:
    """Создаёт роль и публикует manifest; при неудаче повторяет — ключ могут вписать позже ядра."""
    delay = 5
    while True:
        try:
            if core.ensure_role(MANAGER_ROLE):
                log.info('role coursework_manager created')
            current, version = core.manifest_versioned()
            if current != MANIFEST:
                core.publish_manifest(MANIFEST, version)
                log.info('manifest published')
            onboarding.update(state='ready', error=None)
            return
        except CoreUnavailable as error:
            onboarding.update(state='waiting', error=str(error))
            log.warning('onboarding with core failed, retry in %ss: %s', delay, error)
        time.sleep(delay)
        delay = min(delay * 2, 300)


def cleanup_once() -> int:
    """Удаляет брошенные загрузки и старые версии файлов; текущий файл работы не трогает."""
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
        # Сироты после сбоя между записью файла и commit: без ссылки и старше суток.
        for path in FILES.rglob('*.pdf'):
            key = path.relative_to(FILES).as_posix()
            if key not in live and limit - path.stat().st_mtime > CLEANUP_MIN_AGE:
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
    if not (CLIENT_ID and CLIENT_SECRET and API_BASE_URL and CLIENT_BASE_URL and SHELL_ORIGIN):
        raise RuntimeError('Set COURSEWORK_CLIENT_ID, COURSEWORK_CLIENT_SECRET, COURSEWORK_API_BASE_URL, '
                           'COURSEWORK_CLIENT_BASE_URL and SHELL_ORIGIN')
    try:
        prepare()
    except (OSError, sqlite3.Error) as error:
        raise RuntimeError(f'Хранилище {DATA} недоступно для записи: {error}') from error
    threading.Thread(target=onboard, name='onboarding', daemon=True).start()
    task = asyncio.create_task(cleanup_loop())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=[SHELL_ORIGIN] if SHELL_ORIGIN else [], allow_credentials=False,
    allow_methods=['GET', 'POST', 'PUT', 'DELETE'],
    allow_headers=['Authorization', 'Content-Type', 'If-Match', 'Idempotency-Key'],
    expose_headers=['ETag', 'Location', 'X-Request-ID', 'Retry-After', 'Content-Disposition'], max_age=600)


@app.middleware('http')
async def headers(request, call_next):
    request.state.request_id = str(uuid.uuid4())
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Request-ID'] = request.state.request_id
    return response


def error_body(request, code, message, details=None):
    error = {'code': code, 'message': message, 'request_id': request.state.request_id}
    if details:
        error['details'] = details[:100]
    return {'error': error}


@app.exception_handler(Fail)
async def failed(request, exc: Fail):
    return JSONResponse(error_body(request, exc.code, exc.message, exc.details), status_code=exc.status,
                        headers=exc.headers)


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    codes = {401: 'UNAUTHENTICATED', 403: 'FORBIDDEN', 404: 'RESOURCE_NOT_FOUND'}
    return JSONResponse(error_body(request, codes.get(exc.status_code, 'BAD_REQUEST'), str(exc.detail)),
                        status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def invalid(request, exc):
    details = [{'path': '.'.join(str(x) for x in e.get('loc', ())), 'message': e.get('msg', 'Validation error')}
               for e in exc.errors()]
    return JSONResponse(error_body(request, 'VALIDATION_ERROR', 'Проверьте заполненные поля', details), status_code=422)


@app.exception_handler(CoreUnavailable)
async def unavailable(request, exc):
    return JSONResponse(error_body(request, 'SERVICE_UNAVAILABLE', 'Ядро временно недоступно'), status_code=503)


# --------------------------------------------------------------------------
# Аутентификация
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Ctx:
    binding: Binding
    sub: str
    profile: str
    permissions: frozenset
    roles: tuple

    @property
    def tenant(self):
        return (self.binding.institution_id, self.binding.service_id)

    @property
    def manages(self):
        return self.profile == 'admin' and 'coursework.manage' in self.permissions


def verify(token: str) -> Ctx:
    try:
        claims = jwt.decode(token, options={'verify_signature': False})
        service_id = str(UUID(claims['service_id']))
        if claims.get('token_use') != 'service_access' or claims.get('aud') != f'service:{service_id}':
            raise ValueError()
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise Fail(401, 'UNAUTHENTICATED', 'Недействительная сессия')
    binding = core.binding()
    # Local обслуживает ровно один экземпляр: токен другого сервиса — чужой binding, 404.
    if service_id != binding.service_id:
        raise Fail(404, 'RESOURCE_NOT_FOUND', 'Сервис недоступен для этой сессии')
    info = core.introspect(token)
    if (not info.get('active') or info.get('service_id') != service_id
            or info.get('institution_id') != binding.institution_id
            or info.get('sub') != claims.get('sub')
            or info.get('profile') != claims.get('profile')
            or info.get('session_id') != claims.get('sid')
            or info.get('parent_session_id') != claims.get('parent_sid')
            or info.get('profile') not in ('student', 'teacher', 'admin')):
        raise Fail(401, 'UNAUTHENTICATED', 'Сессия завершена')
    return Ctx(binding, info['sub'], info['profile'], frozenset(info.get('permissions') or []),
               tuple(info.get('roles') or []))


def bearer(request: Request) -> str:
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 16384:
        raise Fail(401, 'UNAUTHENTICATED', 'Требуется сессия сервиса')
    return token


def authenticate(request: Request) -> Ctx:
    return verify(bearer(request))


# --------------------------------------------------------------------------
# Помощники
# --------------------------------------------------------------------------

def etag(ctx: Ctx, submission_id: str, revision: int) -> str:
    raw = json.dumps(['submission', *ctx.tenant, submission_id, revision])
    return '"' + hashlib.sha256(raw.encode()).hexdigest()[:40] + '"'


def check_if_match(if_match: Optional[str], current: str):
    if not if_match:
        raise Fail(428, 'PRECONDITION_REQUIRED', 'Сначала загрузите актуальную версию работы')
    if if_match != current:
        raise Fail(412, 'PRECONDITION_FAILED', 'Работа успела измениться. Обновите страницу')


def sign(payload: str) -> str:
    return hmac.new(CLIENT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


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


def respond(ctx: Ctx, row, status=200, extra=None) -> JSONResponse:
    return JSONResponse(row_json(row), status_code=status,
                        headers={'ETag': etag(ctx, row['id'], row['revision']), **(extra or {})})


def load(db, ctx: Ctx, submission_id: str):
    return db.execute('SELECT * FROM submissions WHERE institution_id=? AND service_id=? AND id=? AND deleted=0',
                      (*ctx.tenant, submission_id)).fetchone()


def visible(ctx: Ctx, row) -> bool:
    if ctx.manages:
        return True
    if ctx.profile == 'student':
        return row['student_id'] == ctx.sub
    if ctx.profile == 'teacher':
        return row['teacher_id'] == ctx.sub
    return False


def visible_row(db, ctx: Ctx, submission_id: str):
    row = load(db, ctx, submission_id)
    if not row or not visible(ctx, row):
        raise not_found()
    return row


def clean_name(name: str) -> str:
    """Имя файла только для показа: без путей и управляющих символов."""
    name = (name or '').replace('\\', '/').rsplit('/', 1)[-1]
    name = ''.join(ch for ch in unicodedata.normalize('NFC', name) if unicodedata.category(ch)[0] != 'C').strip()
    return (name or 'coursework.pdf')[:255]


# --------------------------------------------------------------------------
# Загрузка PDF: потоковая запись во временный файл с проверками (SPEC §3)
# --------------------------------------------------------------------------

class Limiter:
    """10 загрузок в минуту и 2 одновременные на пользователя экземпляра."""

    def __init__(self):
        self.lock = threading.Lock()
        self.recent = {}
        self.active = {}

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
    """Сигнатура и хвост структуры PDF. Не антивирус и не рендеринг — только отсев не-PDF."""
    with open(path, 'rb') as f:
        head = f.read(1024)
        f.seek(max(0, size - 2048))
        tail = f.read()
    return head.startswith(b'%PDF-') and b'%%EOF' in tail and b'startxref' in tail


async def receive_upload(request: Request, allowed: set) -> Upload:
    """Тело целиком ≤ 21 MiB считается по фактическим байтам, файл ≤ 20 MiB, без лишних частей."""
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
        names = [k for k, _ in form.multi_items()]
        if set(names) != allowed or len(names) != len(allowed):
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
        fields = {k: v for k, v in form.items() if k != 'file'}
        return Upload(target, clean_name(upload.filename), size, digest.hexdigest(), fields)
    finally:
        await form.close()
        spool.close()


def store(ctx: Ctx, upload: Upload, submission_id: str, version: int) -> str:
    """Переносит проверенный файл по новому неизменяемому ключу до commit: после сбоя он — сирота для очистки."""
    key = f'{ctx.tenant[0]}/{ctx.tenant[1]}/{submission_id}/{version}-{secrets.token_hex(8)}.pdf'
    target = FILES / key
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(upload.path, target)
    return key


# --------------------------------------------------------------------------
# Маршруты
# --------------------------------------------------------------------------

@app.get('/api/v1/health')
def health():
    return {'status': 'ok', 'onboarding': onboarding['state']}


def text(titles, locale):
    return (titles[locale], locale) if locale in titles else (titles.get('ru', ''), 'ru')


@app.get('/api/v1/service')
def service_view(locale: Optional[str] = Query(None, max_length=8), ctx: Ctx = Depends(authenticate)):
    if locale not in (None, 'ru', 'en'):
        raise Fail(422, 'VALIDATION_ERROR', 'locale: ru или en')
    manifest = core.manifest()
    locale = locale or 'ru'
    menus = []
    for item in sorted(manifest.get('menus') or [], key=lambda m: (m.get('order', 0), m.get('id', ''))):
        if ctx.profile in (item.get('profiles') or []) and set(item.get('required_permissions') or []) <= ctx.permissions:
            name, used = text(item.get('titles') or {}, locale)
            menus.append({'id': item['id'], 'display_name': name, 'locale': used,
                          'entrypoint_path': item['entrypoint_path'], 'order': item.get('order', 0)})
    name, used = text(manifest.get('titles') or MANIFEST['titles'], locale)
    return {'id': ctx.binding.service_id, 'institution_id': ctx.binding.institution_id, 'service_type': 'coursework',
            'deployment': 'local', 'display_name': name, 'locale': used, 'api_base_url': ctx.binding.api_base_url,
            'client_base_url': ctx.binding.client_base_url, 'profile': ctx.profile, 'roles': list(ctx.roles),
            'permissions': sorted(ctx.permissions), 'menus': menus}


@app.get('/api/v1/coursework/submissions')
def list_submissions(status: Optional[Literal['submitted', 'accepted', 'changes_requested']] = Query(None),
                     limit: int = Query(50, ge=1, le=100), cursor: Optional[str] = Query(None, max_length=2048),
                     ctx: Ctx = Depends(authenticate)):
    scope = json.dumps([*ctx.tenant, ctx.sub, ctx.profile, ctx.manages, status or '', limit])
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
    if not ctx.manages:
        if ctx.profile == 'student':
            sql.append('AND student_id=?')
        elif ctx.profile == 'teacher':
            sql.append('AND teacher_id=?')
        else:
            raise Fail(403, 'FORBIDDEN', 'Нужна роль «Менеджер курсовых»')
        args.append(ctx.sub)
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
    next_cursor = None
    if len(rows) > limit:
        payload = json.dumps([page[-1]['created_at'], page[-1]['id'], int(time.time()) + CURSOR_TTL])
        next_cursor = payload + '.' + sign(scope + payload)
    return {'items': [row_json(r) for r in page], 'next_cursor': next_cursor}


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision: Literal['accepted', 'changes_requested']
    comment: str = Field(max_length=2000)


@app.post('/api/v1/coursework/submissions', status_code=201)
async def create_submission(request: Request, idempotency: Optional[str] = Header(None, alias='Idempotency-Key')):
    ctx = await run_in_threadpool(authenticate, request)
    if ctx.profile != 'student':
        raise Fail(403, 'FORBIDDEN', 'Загружать работы могут студенты')
    try:
        key = str(UUID(idempotency or ''))
    except ValueError:
        raise Fail(400, 'BAD_REQUEST', 'Нужен заголовок Idempotency-Key (UUID)')
    limit_key = (*ctx.tenant, ctx.sub)
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
        if teacher_id == ctx.sub:
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


def commit_create(ctx: Ctx, key: str, fingerprint: str, title: str, teacher_id: str, upload: Upload):
    submission_id = str(uuid.uuid4())
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM idempotency WHERE created_at < ?', (time.time() - IDEMPOTENCY_TTL,))
        seen = db.execute('SELECT * FROM idempotency WHERE institution_id=? AND service_id=? AND actor=? AND key=?',
                          (*ctx.tenant, ctx.sub, key)).fetchone()
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
                   (*ctx.tenant, submission_id, ctx.sub, teacher_id, title, file_key, upload.name, upload.size,
                    upload.sha256, stamp, stamp))
        db.execute('INSERT INTO idempotency VALUES (?,?,?,?,?,?,?)',
                   (*ctx.tenant, ctx.sub, key, fingerprint, submission_id, time.time()))
        row = load(db, ctx, submission_id)
        db.commit()
    return respond(ctx, row, 201, {'Location': f'/api/v1/coursework/submissions/{submission_id}'})


@app.get('/api/v1/coursework/submissions/{submission_id}')
def get_submission(submission_id: UUID, ctx: Ctx = Depends(authenticate)):
    with database() as db:
        return respond(ctx, visible_row(db, ctx, str(submission_id)))


@app.put('/api/v1/coursework/submissions/{submission_id}/file')
async def replace_file(submission_id: UUID, request: Request, if_match: Optional[str] = Header(None)):
    ctx = await run_in_threadpool(authenticate, request)
    if not if_match:
        raise Fail(428, 'PRECONDITION_REQUIRED', 'Сначала загрузите актуальную версию работы')
    with database() as db:
        row = visible_row(db, ctx, str(submission_id))
    if ctx.profile != 'student' or row['student_id'] != ctx.sub:
        raise Fail(403, 'FORBIDDEN', 'Заменить файл может только автор работы')
    limit_key = (*ctx.tenant, ctx.sub)
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


def commit_replace(ctx: Ctx, submission_id: str, if_match: str, upload: Upload):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = visible_row(db, ctx, submission_id)
        check_if_match(if_match, etag(ctx, row['id'], row['revision']))
        if row['status'] not in ('submitted', 'changes_requested'):
            raise Fail(409, 'INVALID_STATE', 'Принятую работу изменить нельзя')
        version = row['version'] + 1
        file_key = store(ctx, upload, row['id'], version)
        # Старый файл не удаляется до commit: в той же транзакции он ставится на отложенную очистку.
        db.execute('INSERT OR REPLACE INTO cleanup VALUES (?,?)', (row['file_key'], time.time() + CLEANUP_MIN_AGE))
        db.execute("UPDATE submissions SET status='submitted', version=?, revision=revision+1, file_key=?, file_name=?, "
                   'file_size=?, file_sha256=?, review_decision=NULL, review_comment=NULL, reviewer_id=NULL, '
                   'reviewed_at=NULL, updated_at=? WHERE institution_id=? AND service_id=? AND id=?',
                   (version, file_key, upload.name, upload.size, upload.sha256, now_iso(), *ctx.tenant, row['id']))
        row = load(db, ctx, row['id'])
        db.commit()
    return respond(ctx, row)


@app.put('/api/v1/coursework/submissions/{submission_id}/review')
def review(submission_id: UUID, body: ReviewInput, if_match: Optional[str] = Header(None),
           ctx: Ctx = Depends(authenticate)):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = visible_row(db, ctx, str(submission_id))
        # Проверяет только назначенный преподаватель и никогда — автор; manage не даёт права проверки.
        if ctx.profile != 'teacher' or row['teacher_id'] != ctx.sub or row['student_id'] == ctx.sub:
            raise Fail(403, 'FORBIDDEN', 'Проверить работу может только назначенный преподаватель')
        check_if_match(if_match, etag(ctx, row['id'], row['revision']))
        if row['status'] != 'submitted':
            raise Fail(409, 'INVALID_STATE', 'Решение по этой версии уже вынесено')
        stamp = now_iso()
        db.execute('UPDATE submissions SET status=?, revision=revision+1, review_decision=?, review_comment=?, '
                   'reviewer_id=?, reviewed_at=?, updated_at=? WHERE institution_id=? AND service_id=? AND id=?',
                   (body.decision, body.decision, body.comment.strip(), ctx.sub, stamp, stamp, *ctx.tenant, row['id']))
        row = load(db, ctx, row['id'])
        db.commit()
    return respond(ctx, row)


@app.delete('/api/v1/coursework/submissions/{submission_id}', status_code=204)
def delete_submission(submission_id: UUID, if_match: Optional[str] = Header(None), ctx: Ctx = Depends(authenticate)):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = visible_row(db, ctx, str(submission_id))
        author = ctx.profile == 'student' and row['student_id'] == ctx.sub
        if not (author or ctx.manages):
            raise Fail(403, 'FORBIDDEN', 'Удалить работу может автор или менеджер курсовых')
        check_if_match(if_match, etag(ctx, row['id'], row['revision']))
        if not ctx.manages and row['status'] == 'accepted':
            raise Fail(409, 'INVALID_STATE', 'Принятую работу удалить нельзя')
        db.execute('UPDATE submissions SET deleted=1, revision=revision+1, updated_at=? '
                   'WHERE institution_id=? AND service_id=? AND id=?', (now_iso(), *ctx.tenant, row['id']))
        db.execute('INSERT OR REPLACE INTO cleanup VALUES (?,?)', (row['file_key'], time.time()))
        db.commit()
    return Response(status_code=204)


SAFE_ASCII = re.compile(r'[^A-Za-z0-9._-]+')


@app.get('/api/v1/coursework/submissions/{submission_id}/file')
def download(submission_id: UUID, ctx: Ctx = Depends(authenticate)):
    with database() as db:
        row = visible_row(db, ctx, str(submission_id))
    path = FILES / row['file_key']
    if not path.is_file():
        raise Fail(503, 'FILE_STORAGE_UNAVAILABLE', 'Файл временно недоступен')
    from urllib.parse import quote
    ascii_name = SAFE_ASCII.sub('_', row['file_name']).strip('_') or 'coursework.pdf'
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(row['file_name'])}"
    return FileResponse(path, media_type='application/pdf',
                        headers={'Content-Disposition': disposition, 'X-Content-Type-Options': 'nosniff',
                                 'ETag': etag(ctx, row['id'], row['revision'])})
