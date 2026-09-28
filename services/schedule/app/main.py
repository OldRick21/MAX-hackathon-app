"""Schedule cloud service: учебные группы и занятия, своя SQLite, online introspection ядра.

Контракт — docs/services/schedule/SPEC.md и OPENAPI.yaml; отличия — IMPLEMENTATION.md.
Главное отличие: занятия задают преподаватели (свои), а не только admin со schedule.write.
"""
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Literal, Optional
from uuid import UUID

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core_client import Binding, BindingMissing, CoreClient, CoreUnavailable

CORE = os.environ.get('CORE_INTERNAL_URL', 'http://backend:8000')
SECRET = os.environ.get('SCHEDULE_PROVISIONING_TOKEN', '')
DB = os.environ.get('SCHEDULE_DB', '/data/schedule.db')
core = CoreClient(CORE, SECRET)

MAX_RANGE = timedelta(days=31)
IDEMPOTENCY_TTL = 24 * 3600
CURSOR_TTL = 900


# --------------------------------------------------------------------------
# Ошибки
# --------------------------------------------------------------------------

class Fail(Exception):
    def __init__(self, status: int, code: str, message: str, details: Optional[list] = None):
        self.status, self.code, self.message, self.details = status, code, message, details


def not_found(what='Ресурс не найден'):
    return Fail(404, 'RESOURCE_NOT_FOUND', what)


def forbidden(what='Недостаточно прав'):
    return Fail(403, 'FORBIDDEN', what)


# --------------------------------------------------------------------------
# Хранилище
# --------------------------------------------------------------------------

@contextmanager
def database():
    db = sqlite3.connect(DB, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    # Как в user-profile: WAL переживает перезапуск посреди записи, busy_timeout ждёт чужую запись.
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA busy_timeout=10000')
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


SCHEMA = '''
CREATE TABLE IF NOT EXISTS groups (
    institution_id TEXT NOT NULL, service_id TEXT NOT NULL, id TEXT NOT NULL,
    name TEXT NOT NULL, name_key TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1, students_revision INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (institution_id, service_id, id),
    UNIQUE (institution_id, service_id, name_key));
CREATE TABLE IF NOT EXISTS group_students (
    institution_id TEXT NOT NULL, service_id TEXT NOT NULL, group_id TEXT NOT NULL, user_id TEXT NOT NULL,
    PRIMARY KEY (institution_id, service_id, user_id),
    FOREIGN KEY (institution_id, service_id, group_id) REFERENCES groups (institution_id, service_id, id));
CREATE INDEX IF NOT EXISTS group_students_by_group ON group_students (institution_id, service_id, group_id);
CREATE TABLE IF NOT EXISTS events (
    institution_id TEXT NOT NULL, service_id TEXT NOT NULL, id TEXT NOT NULL,
    title TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
    location TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (institution_id, service_id, id));
CREATE INDEX IF NOT EXISTS events_by_time ON events (institution_id, service_id, starts_at, id);
CREATE TABLE IF NOT EXISTS event_groups (
    institution_id TEXT NOT NULL, service_id TEXT NOT NULL, event_id TEXT NOT NULL, group_id TEXT NOT NULL,
    pos INTEGER NOT NULL,
    PRIMARY KEY (institution_id, service_id, event_id, group_id),
    FOREIGN KEY (institution_id, service_id, event_id) REFERENCES events (institution_id, service_id, id) ON DELETE CASCADE,
    FOREIGN KEY (institution_id, service_id, group_id) REFERENCES groups (institution_id, service_id, id));
CREATE INDEX IF NOT EXISTS event_groups_by_group ON event_groups (institution_id, service_id, group_id);
CREATE TABLE IF NOT EXISTS event_teachers (
    institution_id TEXT NOT NULL, service_id TEXT NOT NULL, event_id TEXT NOT NULL, user_id TEXT NOT NULL,
    pos INTEGER NOT NULL,
    PRIMARY KEY (institution_id, service_id, event_id, user_id),
    FOREIGN KEY (institution_id, service_id, event_id) REFERENCES events (institution_id, service_id, id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS event_teachers_by_user ON event_teachers (institution_id, service_id, user_id);
CREATE TABLE IF NOT EXISTS idempotency (
    institution_id TEXT NOT NULL, service_id TEXT NOT NULL, actor TEXT NOT NULL, key TEXT NOT NULL,
    fingerprint TEXT NOT NULL, status INTEGER NOT NULL, body TEXT NOT NULL, headers TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (institution_id, service_id, actor, key));
'''


def prepare():
    """Создаёт схему; вызывается при каждом старте и безопасна для существующего тома."""
    Path(DB).parent.mkdir(parents=True, exist_ok=True)
    with database() as db:
        db.executescript(SCHEMA)


@asynccontextmanager
async def lifespan(app):
    if len(SECRET) < 32:
        raise RuntimeError('Set SCHEDULE_PROVISIONING_TOKEN (32+ characters)')
    try:
        prepare()
    except (OSError, sqlite3.Error) as error:
        # Обычная причина — том /data принадлежит root, а процесс работает под uid 10004.
        raise RuntimeError(f'Хранилище {DB} недоступно для записи: {error}') from error
    yield


app = FastAPI(lifespan=lifespan)


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
    return JSONResponse(error_body(request, exc.code, exc.message, exc.details), status_code=exc.status)


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    codes = {401: 'UNAUTHENTICATED', 403: 'FORBIDDEN', 404: 'RESOURCE_NOT_FOUND', 405: 'BAD_REQUEST'}
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
# Аутентификация и права
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

    def admin_can(self, permission):
        return self.profile == 'admin' and permission in self.permissions

    @property
    def reads_all(self):
        return self.admin_can('schedule.read_all')

    @property
    def manages_groups(self):
        return self.admin_can('groups.manage')

    @property
    def writes_any(self):
        return self.admin_can('schedule.write')


def authenticate(request: Request) -> Ctx:
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 16384:
        raise Fail(401, 'UNAUTHENTICATED', 'Требуется сессия сервиса')
    try:
        claims = jwt.decode(token, options={'verify_signature': False})
        service_id = str(UUID(claims['service_id']))
        if claims.get('token_use') != 'service_access' or claims.get('aud') != f'service:{service_id}':
            raise ValueError()
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise Fail(401, 'UNAUTHENTICATED', 'Недействительная сессия')
    # Непроверенные claims выбирают только binding; личность и права — из introspection ядра.
    try:
        binding = core.binding(service_id)
    except BindingMissing:
        raise not_found('Сервис недоступен для этой сессии')
    info = core.introspect(binding, token)
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


# --------------------------------------------------------------------------
# Общие помощники: время, ETag, курсоры, idempotency
# --------------------------------------------------------------------------

def utc(value: str, field: str) -> str:
    """RFC 3339 с часовым поясом → 'YYYY-MM-DDTHH:MM:SSZ' (строки такого вида сравниваются как время)."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise Fail(422, 'VALIDATION_ERROR', 'Некорректная дата', [{'path': field, 'message': 'RFC 3339, например 2026-09-28T06:00:00Z'}])
    if parsed.tzinfo is None:
        raise Fail(422, 'VALIDATION_ERROR', 'Укажите часовой пояс', [{'path': field, 'message': 'Нужен суффикс Z или смещение'}])
    return parsed.astimezone(timezone.utc).replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_utc(value: str) -> datetime:
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def etag(kind: str, ctx: Ctx, object_id: str, revision: int) -> str:
    raw = json.dumps([kind, *ctx.tenant, object_id, revision])
    return '"' + hashlib.sha256(raw.encode()).hexdigest()[:40] + '"'


def check_if_match(if_match: Optional[str], current: str):
    if not if_match:
        raise Fail(428, 'PRECONDITION_REQUIRED', 'Сначала загрузите актуальную версию')
    if if_match != current:
        raise Fail(412, 'PRECONDITION_FAILED', 'Данные успели измениться. Обновите страницу')


def sign(payload: str) -> str:
    return hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_cursor(scope: list, position: list) -> str:
    payload = base64.urlsafe_b64encode(json.dumps([scope, position, int(time.time()) + CURSOR_TTL]).encode()).decode()
    return payload + '.' + sign(payload)


def read_cursor(cursor: Optional[str], scope: list) -> Optional[list]:
    """Курсор связан с actor, tenant и фильтрами: чужой или просроченный — 400 INVALID_CURSOR."""
    if not cursor:
        return None
    payload, sep, signature = cursor.rpartition('.')
    try:
        if not sep or not hmac.compare_digest(signature, sign(payload)):
            raise ValueError()
        saved_scope, position, expiry = json.loads(base64.urlsafe_b64decode(payload.encode()))
        if saved_scope != scope or expiry < time.time():
            raise ValueError()
        return position
    except (ValueError, TypeError):
        raise Fail(400, 'INVALID_CURSOR', 'Недействительный курсор')


def idempotency_key(value: Optional[str]) -> str:
    try:
        return str(UUID(value or ''))
    except ValueError:
        raise Fail(400, 'BAD_REQUEST', 'Нужен заголовок Idempotency-Key (UUID)')


def replay(db, ctx: Ctx, key: str, fingerprint: str) -> Optional[Response]:
    """Внутри транзакции записи: прежний ответ для того же ключа и тела, 409 — для другого тела."""
    db.execute('DELETE FROM idempotency WHERE created_at < ?', (time.time() - IDEMPOTENCY_TTL,))
    row = db.execute('SELECT * FROM idempotency WHERE institution_id=? AND service_id=? AND actor=? AND key=?',
                     (*ctx.tenant, ctx.sub, key)).fetchone()
    if not row:
        return None
    if row['fingerprint'] != fingerprint:
        raise Fail(409, 'IDEMPOTENCY_CONFLICT', 'Этот Idempotency-Key уже использован для другого запроса')
    return JSONResponse(json.loads(row['body']), status_code=row['status'], headers=json.loads(row['headers']))


def remember(db, ctx: Ctx, key: str, fingerprint: str, status: int, body: dict, headers: dict) -> JSONResponse:
    db.execute('INSERT INTO idempotency VALUES (?,?,?,?,?,?,?,?,?)',
               (*ctx.tenant, ctx.sub, key, fingerprint, status, json.dumps(body), json.dumps(headers), time.time()))
    return JSONResponse(body, status_code=status, headers=headers)


def fingerprint(kind: str, body: BaseModel) -> str:
    return hashlib.sha256(json.dumps([kind, body.model_dump(mode='json')], sort_keys=True).encode()).hexdigest()


# --------------------------------------------------------------------------
# Схемы
# --------------------------------------------------------------------------

def unique(values):
    if len(set(values)) != len(values):
        raise ValueError('Значения не должны повторяться')
    return values


class GroupInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)

    @field_validator('name')
    @classmethod
    def strip(cls, v):
        if not v.strip():
            raise ValueError('Укажите название группы')
        return v.strip()


class StudentsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_ids: List[UUID] = Field(max_length=500)

    @field_validator('user_ids')
    @classmethod
    def distinct(cls, v):
        return unique(v)


class EventInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=200)
    starts_at: str = Field(max_length=64)
    ends_at: str = Field(max_length=64)
    group_ids: List[UUID] = Field(min_length=1, max_length=50)
    teacher_ids: List[UUID] = Field(min_length=1, max_length=10)
    location: str = Field(max_length=200)
    description: str = Field(max_length=2000)
    status: Literal['scheduled', 'cancelled']

    @field_validator('group_ids', 'teacher_ids')
    @classmethod
    def distinct(cls, v):
        return unique(v)

    @field_validator('title')
    @classmethod
    def strip(cls, v):
        if not v.strip():
            raise ValueError('Укажите название занятия')
        return v.strip()


# --------------------------------------------------------------------------
# Служебные маршруты
# --------------------------------------------------------------------------

@app.get('/api/v1/health')
def health():
    return {'status': 'ok'}


def text(titles, locale):
    return (titles[locale], locale) if locale in titles else (titles.get('ru', ''), 'ru')


@app.get('/api/v1/service')
def service_view(locale: Optional[str] = Query(None, max_length=8), ctx: Ctx = Depends(authenticate)):
    if locale not in (None, 'ru', 'en'):
        raise Fail(422, 'VALIDATION_ERROR', 'locale: ru или en')
    manifest = core.manifest(ctx.binding)
    locale = locale or 'ru'
    permissions = sorted(ctx.permissions)
    menus = []
    for item in sorted(manifest.get('menus') or [], key=lambda m: (m.get('order', 0), m.get('id', ''))):
        if ctx.profile in (item.get('profiles') or []) and set(item.get('required_permissions') or []) <= ctx.permissions:
            name, used = text(item.get('titles') or {}, locale)
            menus.append({'id': item['id'], 'display_name': name, 'locale': used,
                          'entrypoint_path': item['entrypoint_path'], 'order': item.get('order', 0)})
    name, used = text(manifest.get('titles') or {'ru': 'Расписание'}, locale)
    return {'id': ctx.binding.service_id, 'institution_id': ctx.binding.institution_id, 'service_type': 'schedule',
            'deployment': 'cloud', 'display_name': name, 'locale': used, 'api_base_url': ctx.binding.api_base_url,
            'client_base_url': ctx.binding.client_base_url, 'profile': ctx.profile, 'roles': list(ctx.roles),
            'permissions': permissions, 'menus': menus}


# --------------------------------------------------------------------------
# Группы
# --------------------------------------------------------------------------

def own_group(db, ctx: Ctx) -> Optional[str]:
    row = db.execute('SELECT group_id FROM group_students WHERE institution_id=? AND service_id=? AND user_id=?',
                     (*ctx.tenant, ctx.sub)).fetchone()
    return row['group_id'] if row else None


def reads_groups(ctx: Ctx) -> bool:
    """Все группы видят преподаватель (выбор групп в своих занятиях) и admin с правами расписания."""
    return ctx.profile == 'teacher' or ctx.reads_all or ctx.manages_groups or ctx.writes_any


def group_row(db, ctx: Ctx, group_id: str):
    return db.execute('SELECT * FROM groups WHERE institution_id=? AND service_id=? AND id=?',
                      (*ctx.tenant, group_id)).fetchone()


def visible_group(db, ctx: Ctx, group_id: str):
    row = group_row(db, ctx, group_id)
    if not row:
        raise not_found('Группа не найдена')
    if reads_groups(ctx):
        return row
    if ctx.profile == 'student' and own_group(db, ctx) == group_id:
        return row
    if ctx.profile == 'admin':
        raise forbidden('Нужна роль «Редактор расписания»')
    raise not_found('Группа не найдена')


def group_json(row):
    return {'id': row['id'], 'name': row['name']}


def group_response(ctx, row, status=200, extra=None):
    return JSONResponse(group_json(row), status_code=status,
                        headers={'ETag': etag('group', ctx, row['id'], row['revision']), **(extra or {})})


def need_groups_manage(ctx: Ctx):
    if not ctx.manages_groups:
        raise forbidden('Нужна роль «Редактор расписания»')


def name_conflict():
    return Fail(409, 'GROUP_ALREADY_EXISTS', 'Группа с таким названием уже есть')


@app.get('/api/v1/schedule/groups')
def list_groups(limit: int = Query(50, ge=1, le=100), cursor: Optional[str] = Query(None, max_length=2048),
                ctx: Ctx = Depends(authenticate)):
    scope = ['groups', *ctx.tenant, ctx.sub, ctx.profile, limit]
    after = read_cursor(cursor, scope)
    with database() as db:
        if reads_groups(ctx):
            rows = db.execute('SELECT * FROM groups WHERE institution_id=? AND service_id=? AND id>? ORDER BY id LIMIT ?',
                              (*ctx.tenant, after[0] if after else '', limit + 1)).fetchall()
        elif ctx.profile == 'student':
            mine = own_group(db, ctx)
            row = group_row(db, ctx, mine) if mine and not after else None
            rows = [row] if row else []
        else:
            raise forbidden('Нужна роль «Редактор расписания»')
    next_cursor = make_cursor(scope, [rows[limit - 1]['id']]) if len(rows) > limit else None
    return {'items': [group_json(r) for r in rows[:limit]], 'next_cursor': next_cursor}


@app.post('/api/v1/schedule/groups', status_code=201)
def create_group(body: GroupInput, idempotency: Optional[str] = Header(None, alias='Idempotency-Key'),
                 ctx: Ctx = Depends(authenticate)):
    need_groups_manage(ctx)
    key, fp = idempotency_key(idempotency), fingerprint('group', body)
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = replay(db, ctx, key, fp)
        if previous:
            return previous
        group_id = str(uuid.uuid4())
        try:
            db.execute('INSERT INTO groups (institution_id, service_id, id, name, name_key) VALUES (?,?,?,?,?)',
                       (*ctx.tenant, group_id, body.name, body.name.casefold()))
        except sqlite3.IntegrityError:
            raise name_conflict()
        row = group_row(db, ctx, group_id)
        response = remember(db, ctx, key, fp, 201, group_json(row),
                            {'ETag': etag('group', ctx, group_id, row['revision']),
                             'Location': f'/api/v1/schedule/groups/{group_id}'})
        db.commit()
    return response


@app.get('/api/v1/schedule/groups/{group_id}')
def get_group(group_id: UUID, ctx: Ctx = Depends(authenticate)):
    with database() as db:
        return group_response(ctx, visible_group(db, ctx, str(group_id)))


@app.patch('/api/v1/schedule/groups/{group_id}')
def rename_group(group_id: UUID, body: GroupInput, if_match: Optional[str] = Header(None),
                 ctx: Ctx = Depends(authenticate)):
    need_groups_manage(ctx)
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = group_row(db, ctx, str(group_id))
        if not row:
            raise not_found('Группа не найдена')
        check_if_match(if_match, etag('group', ctx, row['id'], row['revision']))
        try:
            db.execute('UPDATE groups SET name=?, name_key=?, revision=revision+1 WHERE institution_id=? AND service_id=? AND id=?',
                       (body.name, body.name.casefold(), *ctx.tenant, row['id']))
        except sqlite3.IntegrityError:
            raise name_conflict()
        row = group_row(db, ctx, row['id'])
        db.commit()
    return group_response(ctx, row)


@app.delete('/api/v1/schedule/groups/{group_id}', status_code=204)
def delete_group(group_id: UUID, if_match: Optional[str] = Header(None), ctx: Ctx = Depends(authenticate)):
    need_groups_manage(ctx)
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = group_row(db, ctx, str(group_id))
        if not row:
            raise not_found('Группа не найдена')
        check_if_match(if_match, etag('group', ctx, row['id'], row['revision']))
        students = db.execute('SELECT 1 FROM group_students WHERE institution_id=? AND service_id=? AND group_id=? LIMIT 1',
                              (*ctx.tenant, row['id'])).fetchone()
        used = db.execute('SELECT 1 FROM event_groups WHERE institution_id=? AND service_id=? AND group_id=? LIMIT 1',
                          (*ctx.tenant, row['id'])).fetchone()
        if students or used:
            raise Fail(409, 'GROUP_IN_USE', 'В группе есть студенты или занятия. Сначала уберите их')
        db.execute('DELETE FROM groups WHERE institution_id=? AND service_id=? AND id=?', (*ctx.tenant, row['id']))
        db.commit()
    return Response(status_code=204)


def students_of(db, ctx: Ctx, group_id: str) -> List[str]:
    return [r['user_id'] for r in db.execute(
        'SELECT user_id FROM group_students WHERE institution_id=? AND service_id=? AND group_id=? ORDER BY user_id',
        (*ctx.tenant, group_id))]


def students_response(db, ctx, row):
    return JSONResponse({'group_id': row['id'], 'user_ids': students_of(db, ctx, row['id'])},
                        headers={'ETag': etag('students', ctx, row['id'], row['students_revision'])})


@app.get('/api/v1/schedule/groups/{group_id}/students')
def get_students(group_id: UUID, ctx: Ctx = Depends(authenticate)):
    need_groups_manage(ctx)
    with database() as db:
        row = group_row(db, ctx, str(group_id))
        if not row:
            raise not_found('Группа не найдена')
        return students_response(db, ctx, row)


@app.put('/api/v1/schedule/groups/{group_id}/students')
def replace_students(group_id: UUID, body: StudentsInput, if_match: Optional[str] = Header(None),
                     ctx: Ctx = Depends(authenticate)):
    need_groups_manage(ctx)
    if not if_match:
        raise Fail(428, 'PRECONDITION_REQUIRED', 'Сначала загрузите актуальный состав')
    user_ids = [str(u) for u in body.user_ids]
    # Проверка профилей — до локальной транзакции, чтобы не держать запись на время запросов к ядру.
    bad = [i for i, uid in enumerate(user_ids) if 'student' not in core.profiles(ctx.binding, uid)]
    if bad:
        raise Fail(422, 'INVALID_REFERENCE', 'Добавлять в группу можно только участников с профилем «Студент»',
                   [{'path': f'user_ids/{i}', 'message': f'{user_ids[i]} — не студент этого вуза'} for i in bad])
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = group_row(db, ctx, str(group_id))
        if not row:
            raise not_found('Группа не найдена')
        check_if_match(if_match, etag('students', ctx, row['id'], row['students_revision']))
        taken = [] if not user_ids else db.execute(
            f'SELECT user_id FROM group_students WHERE institution_id=? AND service_id=? AND group_id!=? '
            f'AND user_id IN ({",".join("?" * len(user_ids))})', (*ctx.tenant, row['id'], *user_ids)).fetchall()
        if taken:
            raise Fail(409, 'STUDENT_ALREADY_GROUPED', 'Студент уже состоит в другой группе. Сначала уберите его оттуда',
                       [{'path': 'user_ids', 'message': r['user_id']} for r in taken])
        db.execute('DELETE FROM group_students WHERE institution_id=? AND service_id=? AND group_id=?', (*ctx.tenant, row['id']))
        db.executemany('INSERT INTO group_students VALUES (?,?,?,?)', [(*ctx.tenant, row['id'], uid) for uid in user_ids])
        db.execute('UPDATE groups SET students_revision=students_revision+1 WHERE institution_id=? AND service_id=? AND id=?',
                   (*ctx.tenant, row['id']))
        response = students_response(db, ctx, group_row(db, ctx, row['id']))
        db.commit()
    return response


# --------------------------------------------------------------------------
# Занятия
# --------------------------------------------------------------------------

def event_row(db, ctx: Ctx, event_id: str):
    return db.execute('SELECT * FROM events WHERE institution_id=? AND service_id=? AND id=?',
                      (*ctx.tenant, event_id)).fetchone()


def event_links(db, ctx: Ctx, event_ids: List[str]):
    groups = {i: [] for i in event_ids}
    teachers = {i: [] for i in event_ids}
    if event_ids:
        marks = ','.join('?' * len(event_ids))
        for r in db.execute(f'SELECT event_id, group_id FROM event_groups WHERE institution_id=? AND service_id=? '
                            f'AND event_id IN ({marks}) ORDER BY pos', (*ctx.tenant, *event_ids)):
            groups[r['event_id']].append(r['group_id'])
        for r in db.execute(f'SELECT event_id, user_id FROM event_teachers WHERE institution_id=? AND service_id=? '
                            f'AND event_id IN ({marks}) ORDER BY pos', (*ctx.tenant, *event_ids)):
            teachers[r['event_id']].append(r['user_id'])
    return groups, teachers


def event_json(row, group_ids, teacher_ids):
    return {'id': row['id'], 'title': row['title'], 'starts_at': row['starts_at'], 'ends_at': row['ends_at'],
            'group_ids': group_ids, 'teacher_ids': teacher_ids, 'location': row['location'],
            'description': row['description'], 'status': row['status']}


def load_event(db, ctx: Ctx, event_id: str):
    row = event_row(db, ctx, event_id)
    if not row:
        return None, None
    groups, teachers = event_links(db, ctx, [event_id])
    return row, event_json(row, groups[event_id], teachers[event_id])


def sees_event(db, ctx: Ctx, event: dict) -> bool:
    if ctx.reads_all or ctx.writes_any:
        return True
    if ctx.profile == 'teacher':
        return ctx.sub in event['teacher_ids']
    if ctx.profile == 'student':
        mine = own_group(db, ctx)
        return bool(mine) and mine in event['group_ids']
    return False


def event_response(ctx, row, value, status=200, extra=None):
    return JSONResponse(value, status_code=status,
                        headers={'ETag': etag('event', ctx, row['id'], row['revision']), **(extra or {})})


def need_writer(ctx: Ctx):
    """Занятия пишут преподаватели (только свои) и admin с schedule.write (любые)."""
    if not (ctx.writes_any or ctx.profile == 'teacher'):
        raise forbidden('Расписание меняют преподаватели и редакторы расписания')


def checked_event(ctx: Ctx, body: EventInput) -> dict:
    """Проверки, не требующие локальной БД: время, своё участие преподавателя, профили в ядре."""
    starts, ends = utc(body.starts_at, 'starts_at'), utc(body.ends_at, 'ends_at')
    if not starts < ends:
        raise Fail(422, 'INVALID_TIME_RANGE', 'Время окончания должно быть позже времени начала',
                   [{'path': 'ends_at', 'message': 'Должно быть позже starts_at'}])
    teacher_ids = [str(t) for t in body.teacher_ids]
    if not ctx.writes_any and ctx.sub not in teacher_ids:
        raise Fail(422, 'VALIDATION_ERROR', 'Преподаватель может задавать только занятия, которые ведёт сам',
                   [{'path': 'teacher_ids', 'message': 'Добавьте себя в преподаватели занятия'}])
    bad = [i for i, uid in enumerate(teacher_ids) if 'teacher' not in core.profiles(ctx.binding, uid)]
    if bad:
        raise Fail(422, 'INVALID_REFERENCE', 'Преподавателями могут быть только участники с профилем «Преподаватель»',
                   [{'path': f'teacher_ids/{i}', 'message': f'{teacher_ids[i]} — не преподаватель этого вуза'} for i in bad])
    return {'title': body.title, 'starts_at': starts, 'ends_at': ends, 'group_ids': [str(g) for g in body.group_ids],
            'teacher_ids': teacher_ids, 'location': body.location.strip(), 'description': body.description.strip(),
            'status': body.status}


def check_groups_exist(db, ctx: Ctx, group_ids: List[str]):
    marks = ','.join('?' * len(group_ids))
    found = {r['id'] for r in db.execute(f'SELECT id FROM groups WHERE institution_id=? AND service_id=? AND id IN ({marks})',
                                         (*ctx.tenant, *group_ids))}
    missing = [i for i, g in enumerate(group_ids) if g not in found]
    if missing:
        raise Fail(422, 'INVALID_REFERENCE', 'Группа не найдена',
                   [{'path': f'group_ids/{i}', 'message': f'{group_ids[i]} — нет такой группы'} for i in missing])


def write_links(db, ctx: Ctx, event_id: str, value: dict):
    db.execute('DELETE FROM event_groups WHERE institution_id=? AND service_id=? AND event_id=?', (*ctx.tenant, event_id))
    db.execute('DELETE FROM event_teachers WHERE institution_id=? AND service_id=? AND event_id=?', (*ctx.tenant, event_id))
    db.executemany('INSERT INTO event_groups VALUES (?,?,?,?,?)',
                   [(*ctx.tenant, event_id, g, i) for i, g in enumerate(value['group_ids'])])
    db.executemany('INSERT INTO event_teachers VALUES (?,?,?,?,?)',
                   [(*ctx.tenant, event_id, t, i) for i, t in enumerate(value['teacher_ids'])])


@app.get('/api/v1/schedule/events')
def list_events(from_: str = Query(..., alias='from', max_length=64), to: str = Query(..., max_length=64),
                group_id: Optional[UUID] = Query(None), teacher_id: Optional[UUID] = Query(None),
                limit: int = Query(50, ge=1, le=100), cursor: Optional[str] = Query(None, max_length=2048),
                ctx: Ctx = Depends(authenticate)):
    start, end = utc(from_, 'from'), utc(to, 'to')
    if not start < end or parse_utc(end) - parse_utc(start) > MAX_RANGE:
        raise Fail(422, 'INVALID_TIME_RANGE', 'Период должен быть непустым и не длиннее 31 дня')
    if ctx.profile == 'admin' and not (ctx.reads_all or ctx.writes_any):
        raise forbidden('Нужна роль «Редактор расписания»')
    scope = ['events', *ctx.tenant, ctx.sub, ctx.profile, start, end, str(group_id or ''), str(teacher_id or ''), limit]
    after = read_cursor(cursor, scope)
    sql = ['SELECT e.* FROM events e WHERE e.institution_id=? AND e.service_id=? AND e.starts_at < ? AND e.ends_at > ?']
    args = [*ctx.tenant, end, start]
    link = ('EXISTS (SELECT 1 FROM {t} x WHERE x.institution_id=e.institution_id AND x.service_id=e.service_id '
            'AND x.event_id=e.id AND x.{c}=?)')
    with database() as db:
        # Видимость по профилю; фильтры ниже только сужают её.
        if not (ctx.reads_all or ctx.writes_any):
            if ctx.profile == 'teacher':
                sql.append('AND ' + link.format(t='event_teachers', c='user_id'))
                args.append(ctx.sub)
            else:
                mine = own_group(db, ctx)
                if not mine:
                    return {'items': [], 'next_cursor': None}
                sql.append('AND ' + link.format(t='event_groups', c='group_id'))
                args.append(mine)
        if group_id:
            sql.append('AND ' + link.format(t='event_groups', c='group_id'))
            args.append(str(group_id))
        if teacher_id:
            sql.append('AND ' + link.format(t='event_teachers', c='user_id'))
            args.append(str(teacher_id))
        if after:
            sql.append('AND (e.starts_at > ? OR (e.starts_at = ? AND e.id > ?))')
            args += [after[0], after[0], after[1]]
        sql.append('ORDER BY e.starts_at, e.id LIMIT ?')
        args.append(limit + 1)
        rows = db.execute(' '.join(sql), args).fetchall()
        page = rows[:limit]
        groups, teachers = event_links(db, ctx, [r['id'] for r in page])
    items = [event_json(r, groups[r['id']], teachers[r['id']]) for r in page]
    next_cursor = make_cursor(scope, [page[-1]['starts_at'], page[-1]['id']]) if len(rows) > limit else None
    return {'items': items, 'next_cursor': next_cursor}


@app.post('/api/v1/schedule/events', status_code=201)
def create_event(body: EventInput, idempotency: Optional[str] = Header(None, alias='Idempotency-Key'),
                 ctx: Ctx = Depends(authenticate)):
    need_writer(ctx)
    key, fp = idempotency_key(idempotency), fingerprint('event', body)
    value = checked_event(ctx, body)
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = replay(db, ctx, key, fp)
        if previous:
            return previous
        check_groups_exist(db, ctx, value['group_ids'])
        event_id = str(uuid.uuid4())
        db.execute('INSERT INTO events (institution_id, service_id, id, title, starts_at, ends_at, location, description, status) '
                   'VALUES (?,?,?,?,?,?,?,?,?)', (*ctx.tenant, event_id, value['title'], value['starts_at'], value['ends_at'],
                                                   value['location'], value['description'], value['status']))
        write_links(db, ctx, event_id, value)
        row, data = load_event(db, ctx, event_id)
        response = remember(db, ctx, key, fp, 201, data,
                            {'ETag': etag('event', ctx, event_id, row['revision']),
                             'Location': f'/api/v1/schedule/events/{event_id}'})
        db.commit()
    return response


@app.get('/api/v1/schedule/events/{event_id}')
def get_event(event_id: UUID, ctx: Ctx = Depends(authenticate)):
    with database() as db:
        row, data = load_event(db, ctx, str(event_id))
        if not row or not sees_event(db, ctx, data):
            raise not_found('Занятие не найдено')
    return event_response(ctx, row, data)


def editable_event(db, ctx: Ctx, event_id: str, if_match: Optional[str]):
    row, data = load_event(db, ctx, event_id)
    # Чужое для преподавателя занятие — 404, как и при чтении: его существование не раскрывается.
    if not row or not (ctx.writes_any or ctx.sub in data['teacher_ids']):
        raise not_found('Занятие не найдено')
    check_if_match(if_match, etag('event', ctx, row['id'], row['revision']))
    return row


@app.put('/api/v1/schedule/events/{event_id}')
def replace_event(event_id: UUID, body: EventInput, if_match: Optional[str] = Header(None),
                  ctx: Ctx = Depends(authenticate)):
    need_writer(ctx)
    if not if_match:
        raise Fail(428, 'PRECONDITION_REQUIRED', 'Сначала загрузите актуальную версию')
    value = checked_event(ctx, body)
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = editable_event(db, ctx, str(event_id), if_match)
        check_groups_exist(db, ctx, value['group_ids'])
        db.execute('UPDATE events SET title=?, starts_at=?, ends_at=?, location=?, description=?, status=?, revision=revision+1 '
                   'WHERE institution_id=? AND service_id=? AND id=?',
                   (value['title'], value['starts_at'], value['ends_at'], value['location'], value['description'],
                    value['status'], *ctx.tenant, row['id']))
        write_links(db, ctx, row['id'], value)
        row, data = load_event(db, ctx, row['id'])
        db.commit()
    return event_response(ctx, row, data)


@app.delete('/api/v1/schedule/events/{event_id}', status_code=204)
def delete_event(event_id: UUID, if_match: Optional[str] = Header(None), ctx: Ctx = Depends(authenticate)):
    need_writer(ctx)
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = editable_event(db, ctx, str(event_id), if_match)
        db.execute('DELETE FROM events WHERE institution_id=? AND service_id=? AND id=?', (*ctx.tenant, row['id']))
        db.commit()
    return Response(status_code=204)
