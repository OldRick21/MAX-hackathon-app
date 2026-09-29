"""Schedule cloud service: учебные группы и занятия, своя SQLite, online introspection ядра.

Контракт — docs/services/schedule/SPEC.md и OPENAPI.yaml; отличия — IMPLEMENTATION.md.
Права — по контракту (SPEC §1): студент видит свою группу, преподаватель — свои занятия, admin — только
с ролью «Редактор расписания» (schedule.read_all, schedule.write). Отличие: учебные группы хранит ядро.
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
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import List, Literal, Optional
from uuid import UUID

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from app import onboarding
from app.core_client import Binding, BindingMissing, CoreClient, CoreUnavailable

# Как у любого сервиса вуза: адреса и ключ — из .env, строки выдаёт карточка сервиса («Выдать ключ»).
CORE = os.environ.get('CORE_URL', '').rstrip('/')
SHELL_ORIGIN = os.environ.get('SHELL_ORIGIN', '').rstrip('/')
CLIENT_ID = os.environ.get('SERVICE_CLIENT_ID', '')
SECRET = os.environ.get('SERVICE_CLIENT_SECRET', '')
API_BASE = os.environ.get('SERVICE_API_BASE_URL', '').rstrip('/')
CLIENT_BASE = os.environ.get('SERVICE_CLIENT_BASE_URL', '').rstrip('/')
DB = os.environ.get('SCHEDULE_DB', '/data/schedule.db')
# Часовой пояс вуза: по нему виджет решает, что такое «сегодня».
TIMEZONE = ZoneInfo(os.environ.get('SCHEDULE_TIMEZONE', 'Europe/Moscow'))
core = CoreClient(CORE, CLIENT_ID, SECRET, API_BASE, CLIENT_BASE)

# Меню и роль — по контракту (SPEC §1); сервис публикует их сам. groups.manage в роль не входит:
# группы ведёт ядро (администрирование → «Группы»).
MANIFEST = {'titles': {'ru': 'Расписание', 'en': 'Schedule'}, 'menus': [
    {'id': 'schedule', 'titles': {'ru': 'Расписание', 'en': 'Schedule'}, 'entrypoint_path': '/schedule',
     'profiles': ['student', 'teacher'], 'required_permissions': [], 'order': 0},
    {'id': 'schedule_admin', 'titles': {'ru': 'Расписание', 'en': 'Schedule'}, 'entrypoint_path': '/schedule',
     'profiles': ['admin'], 'required_permissions': ['schedule.read_all'], 'order': 0}],
    # Виджеты главного экрана (docs/services/sdk/WIDGETS_SPEC.md §9).
    'widgets': [
        {'id': 'today', 'titles': {'ru': 'Сегодня', 'en': 'Today'}, 'kind': 'events', 'size': 'wide',
         'profiles': ['student', 'teacher'], 'required_permissions': [], 'data_path': '/schedule/widgets/today',
         'open_menu': 'schedule', 'order': 0},
        {'id': 'today_admin', 'titles': {'ru': 'Занятия сегодня', 'en': 'Classes today'}, 'kind': 'stat', 'size': 'small',
         'profiles': ['admin'], 'required_permissions': ['schedule.read_all'], 'data_path': '/schedule/widgets/today-admin',
         'open_menu': 'schedule_admin', 'order': 0}]}
ROLES = [{'code': 'schedule_editor', 'titles': {'ru': 'Редактор расписания', 'en': 'Schedule editor'},
          'allowed_profiles': ['admin'], 'permissions': ['schedule.read_all', 'schedule.write']},
         # Отличие от контракта: правку расписания администратор может включить и преподавателю.
         {'code': 'teacher_editor', 'titles': {'ru': 'Редактирование расписания', 'en': 'Schedule editing'},
          'allowed_profiles': ['teacher'], 'permissions': ['schedule.read_all', 'schedule.write']}]
RETIRED_ROLES = ('group_editor',)
STATE = {'onboarding': 'pending', 'error': None}

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
    FOREIGN KEY (institution_id, service_id, event_id) REFERENCES events (institution_id, service_id, id) ON DELETE CASCADE);
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
        # Группы переехали в ядро. Старые таблицы groups/group_students остаются только для
        # переноса (python -m app.export_groups); связь занятий с ними снимается.
        fks = db.execute("PRAGMA foreign_key_list('event_groups')").fetchall()
        if any(fk['table'] == 'groups' for fk in fks):
            db.execute('PRAGMA foreign_keys=OFF')
            db.executescript('''
                BEGIN;
                ALTER TABLE event_groups RENAME TO event_groups_old;
                DROP INDEX IF EXISTS event_groups_by_group;
                CREATE TABLE event_groups (
                    institution_id TEXT NOT NULL, service_id TEXT NOT NULL, event_id TEXT NOT NULL,
                    group_id TEXT NOT NULL, pos INTEGER NOT NULL,
                    PRIMARY KEY (institution_id, service_id, event_id, group_id),
                    FOREIGN KEY (institution_id, service_id, event_id)
                        REFERENCES events (institution_id, service_id, id) ON DELETE CASCADE);
                INSERT INTO event_groups SELECT institution_id, service_id, event_id, group_id, pos FROM event_groups_old;
                DROP TABLE event_groups_old;
                COMMIT;
            ''')
            db.execute('PRAGMA foreign_keys=ON')
        db.executescript(SCHEMA)


@asynccontextmanager
async def lifespan(app):
    missing = [name for name, value in (('CORE_URL', CORE), ('SHELL_ORIGIN', SHELL_ORIGIN), ('SERVICE_CLIENT_ID', CLIENT_ID),
                                        ('SERVICE_CLIENT_SECRET', SECRET), ('SERVICE_API_BASE_URL', API_BASE),
                                        ('SERVICE_CLIENT_BASE_URL', CLIENT_BASE)) if not value]
    if missing:
        raise RuntimeError('Не заданы в .env: ' + ', '.join(missing))
    try:
        prepare()
    except (OSError, sqlite3.Error) as error:
        # Обычная причина — том /data принадлежит root, а процесс работает под uid 10004.
        raise RuntimeError(f'Хранилище {DB} недоступно для записи: {error}') from error
    onboarding.start(core, MANIFEST, ROLES, STATE, RETIRED_ROLES)
    yield


app = FastAPI(lifespan=lifespan)

# Экраны сервиса рисует оболочка на своём origin и ходит сюда напрямую.
app.add_middleware(CORSMiddleware, allow_origins=[SHELL_ORIGIN] if SHELL_ORIGIN else [], allow_credentials=False,
                   allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'],
                   allow_headers=['Authorization', 'Content-Type', 'If-Match', 'Idempotency-Key'],
                   expose_headers=['ETag', 'Location', 'X-Request-ID'], max_age=600)


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


# Starlette HTTPException ловит и 404/405 маршрутизатора — все ошибки в формате контракта.
@app.exception_handler(StarletteHTTPException)
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
    group_ids: tuple = ()

    @property
    def tenant(self):
        return (self.binding.institution_id, self.binding.service_id)

    def admin_can(self, permission):
        """Право admin по контракту; преподаватель с ролью «Редактирование расписания» — тоже (отличие)."""
        return self.profile in ('admin', 'teacher') and permission in self.permissions

    @property
    def reads_all(self):
        return self.admin_can('schedule.read_all')

    @property
    def writes_any(self):
        """Занятия пишет admin с schedule.write (контракт) и преподаватель, которому правку включили."""
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
               tuple(info.get('roles') or []), tuple(info.get('group_ids') or []))


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
    return {'status': 'ok', 'onboarding': STATE['onboarding']}


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
            'deployment': 'local', 'display_name': name, 'locale': used, 'api_base_url': ctx.binding.api_base_url,
            'client_base_url': ctx.binding.client_base_url, 'profile': ctx.profile, 'roles': list(ctx.roles),
            'permissions': permissions, 'menus': menus}


# --------------------------------------------------------------------------
# Группы: сущность ядра. Сервис только читает их (machine API, scope groups:read);
# ведёт группы администратор вуза в администрировании.
# --------------------------------------------------------------------------

def visible_groups(ctx: Ctx) -> list:
    """Контракт: студент — своя текущая группа; преподаватель — названия групп вуза; admin — с schedule.read_all.

    Группы берутся из ядра (machine API groups:read), своя группа студента — из group_ids introspection.
    """
    if ctx.profile == 'teacher' or ctx.reads_all:
        groups = core.groups(ctx.binding)
    elif ctx.profile == 'student':
        groups = [g for g in core.groups(ctx.binding) if g['id'] in ctx.group_ids] if ctx.group_ids else []
    else:
        raise forbidden('Нужна роль «Редактор расписания»')
    return sorted(groups, key=lambda g: g['id'])


@app.get('/api/v1/schedule/groups')
def list_groups(limit: int = Query(50, ge=1, le=100), cursor: Optional[str] = Query(None, max_length=2048),
                ctx: Ctx = Depends(authenticate)):
    """Порядок id ASC, keyset-курсор."""
    scope = ['groups', *ctx.tenant, ctx.sub, ctx.profile, limit]
    after = read_cursor(cursor, scope)
    groups = [g for g in visible_groups(ctx) if not after or g['id'] > after[0]]
    page = groups[:limit]
    next_cursor = make_cursor(scope, [page[-1]['id']]) if len(groups) > limit else None
    return {'items': [{'id': g['id'], 'name': g['name']} for g in page], 'next_cursor': next_cursor}


@app.get('/api/v1/schedule/groups/{group_id}')
def get_group(group_id: UUID, ctx: Ctx = Depends(authenticate)):
    found = next((g for g in visible_groups(ctx) if g['id'] == str(group_id)), None)
    if not found:
        raise not_found('Группа не найдена')
    return {'id': found['id'], 'name': found['name']}


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
    """Контракт: студент — занятия своей группы, преподаватель — свои, admin — с schedule.read_all."""
    if ctx.reads_all:
        return True
    if ctx.profile == 'teacher':
        return ctx.sub in event['teacher_ids']
    if ctx.profile == 'student':
        return any(g in event['group_ids'] for g in ctx.group_ids)
    return False


def event_response(ctx, row, value, status=200, extra=None):
    return JSONResponse(value, status_code=status,
                        headers={'ETag': etag('event', ctx, row['id'], row['revision']), **(extra or {})})


def need_writer(ctx: Ctx):
    """Занятия пишет admin с schedule.write или преподаватель с включённой правкой."""
    if not ctx.writes_any:
        raise forbidden('Нужна роль «Редактор расписания»')


def checked_event(ctx: Ctx, body: EventInput) -> dict:
    """Проверки, не требующие локальной БД: время и профили преподавателей в ядре."""
    starts, ends = utc(body.starts_at, 'starts_at'), utc(body.ends_at, 'ends_at')
    if not starts < ends:
        raise Fail(422, 'INVALID_TIME_RANGE', 'Время окончания должно быть позже времени начала',
                   [{'path': 'ends_at', 'message': 'Должно быть позже starts_at'}])
    teacher_ids = [str(t) for t in body.teacher_ids]
    bad = [i for i, uid in enumerate(teacher_ids) if 'teacher' not in core.profiles(ctx.binding, uid)]
    if bad:
        raise Fail(422, 'INVALID_REFERENCE', 'Преподавателями могут быть только участники с профилем «Преподаватель»',
                   [{'path': f'teacher_ids/{i}', 'message': f'{teacher_ids[i]} — не преподаватель этого вуза'} for i in bad])
    return {'title': body.title, 'starts_at': starts, 'ends_at': ends, 'group_ids': [str(g) for g in body.group_ids],
            'teacher_ids': teacher_ids, 'location': body.location.strip(), 'description': body.description.strip(),
            'status': body.status}


def check_groups_exist(ctx: Ctx, group_ids: List[str]):
    """Группы занятия должны существовать в вузе (ядро). Проверка — до транзакции записи."""
    known = {g['id'] for g in core.groups(ctx.binding)}
    missing = [i for i, g in enumerate(group_ids) if g not in known]
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
    if ctx.profile == 'admin' and not ctx.reads_all:
        raise forbidden('Нужна роль «Редактор расписания»')
    scope = ['events', *ctx.tenant, ctx.sub, ctx.profile, start, end, str(group_id or ''), str(teacher_id or ''), limit]
    after = read_cursor(cursor, scope)
    sql = ['SELECT e.* FROM events e WHERE e.institution_id=? AND e.service_id=? AND e.starts_at < ? AND e.ends_at > ?']
    args = [*ctx.tenant, end, start]
    link = ('EXISTS (SELECT 1 FROM {t} x WHERE x.institution_id=e.institution_id AND x.service_id=e.service_id '
            'AND x.event_id=e.id AND x.{c}=?)')
    with database() as db:
        # Видимость по профилю; фильтры ниже только сужают её (контракт §2).
        if not ctx.reads_all:
            if ctx.profile == 'teacher':
                sql.append('AND ' + link.format(t='event_teachers', c='user_id'))
                args.append(ctx.sub)
            else:
                if not ctx.group_ids:
                    return {'items': [], 'next_cursor': None}
                marks = ','.join('?' * len(ctx.group_ids))
                sql.append('AND EXISTS (SELECT 1 FROM event_groups x WHERE x.institution_id=e.institution_id '
                           f'AND x.service_id=e.service_id AND x.event_id=e.id AND x.group_id IN ({marks}))')
                args.extend(ctx.group_ids)
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


# --------------------------------------------------------------------------
# Виджеты главного экрана (WIDGETS_SPEC.md §7): сводка из тех же данных и с теми же правами
# --------------------------------------------------------------------------

def day_bounds(day) -> tuple:
    start = datetime(day.year, day.month, day.day, tzinfo=TIMEZONE).astimezone(timezone.utc)
    fmt = '%Y-%m-%dT%H:%M:%SZ'
    return start.strftime(fmt), (start + timedelta(days=1)).strftime(fmt)


def own_events(db, ctx: Ctx, start: str, end: str) -> list:
    """Занятия пользователя в [start, end): студенту — его группы, преподавателю — где он ведёт."""
    sql = 'SELECT e.* FROM events e WHERE e.institution_id=? AND e.service_id=? AND e.starts_at < ? AND e.ends_at > ? AND '
    if ctx.profile == 'teacher':
        sql += ('EXISTS (SELECT 1 FROM event_teachers x WHERE x.institution_id=e.institution_id '
                'AND x.service_id=e.service_id AND x.event_id=e.id AND x.user_id=?)')
        args = [*ctx.tenant, end, start, ctx.sub]
    else:
        if not ctx.group_ids:
            return []
        marks = ','.join('?' * len(ctx.group_ids))
        sql += ('EXISTS (SELECT 1 FROM event_groups x WHERE x.institution_id=e.institution_id '
                f'AND x.service_id=e.service_id AND x.event_id=e.id AND x.group_id IN ({marks}))')
        args = [*ctx.tenant, end, start, *ctx.group_ids]
    return db.execute(sql + ' ORDER BY e.starts_at, e.id LIMIT 50', args).fetchall()


@app.get('/api/v1/schedule/widgets/today')
def widget_today(ctx: Ctx = Depends(authenticate)):
    """Занятия сегодня, иначе ближайшего учебного дня в пределах недели."""
    if ctx.profile not in ('student', 'teacher'):
        raise forbidden('Виджет для студентов и преподавателей')
    today = datetime.now(TIMEZONE).date()
    day, rows = None, []
    with database() as db:
        for shift in range(7):  # сегодняшние пары показываются до конца дня, даже прошедшие
            found = own_events(db, ctx, *day_bounds(today + timedelta(days=shift)))
            if found:
                day, rows = today + timedelta(days=shift), found
                break
    items = [{'title': r['title'], 'starts_at': r['starts_at'], 'ends_at': r['ends_at'],
              'place': r['location'] or 'онлайн', 'status': r['status']} for r in rows[:10]]
    return JSONResponse({'kind': 'events', 'day': day.isoformat() if day else None, 'items': items,
                         'empty_text': 'На ближайшую неделю занятий нет'}, headers={'Cache-Control': 'no-store'})


@app.get('/api/v1/schedule/widgets/today-admin')
def widget_today_admin(ctx: Ctx = Depends(authenticate)):
    """Сколько занятий сегодня в вузе и сколько из них отменено (admin с schedule.read_all)."""
    if ctx.profile != 'admin' or not ctx.reads_all:
        raise forbidden('Нужна роль «Редактор расписания»')
    start, end = day_bounds(datetime.now(TIMEZONE).date())
    with database() as db:
        row = db.execute("SELECT COUNT(*) AS total, SUM(status='cancelled') AS cancelled FROM events "
                         'WHERE institution_id=? AND service_id=? AND starts_at < ? AND ends_at > ?',
                         (*ctx.tenant, end, start)).fetchone()
    total, cancelled = row['total'] or 0, row['cancelled'] or 0
    caption = 'сегодня в вузе' + (f', отменено {cancelled}' if cancelled else '')
    return JSONResponse({'kind': 'stat', 'value': total, 'unit': plural(total, 'занятие', 'занятия', 'занятий'),
                         'caption': caption, 'tone': 'warning' if cancelled else 'normal'},
                        headers={'Cache-Control': 'no-store'})


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


@app.post('/api/v1/schedule/events', status_code=201)
def create_event(body: EventInput, idempotency: Optional[str] = Header(None, alias='Idempotency-Key'),
                 ctx: Ctx = Depends(authenticate)):
    need_writer(ctx)
    key, fp = idempotency_key(idempotency), fingerprint('event', body)
    value = checked_event(ctx, body)
    check_groups_exist(ctx, value['group_ids'])
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = replay(db, ctx, key, fp)
        if previous:
            return previous
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
    if not row or not sees_event(db, ctx, data):
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
    check_groups_exist(ctx, value['group_ids'])
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = editable_event(db, ctx, str(event_id), if_match)
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

# --------------------------------------------------------------------------
# HTML-клиент (SDK §6–7): статическая оболочка без токенов и данных, в iframe — только оболочка и MAX Web
# --------------------------------------------------------------------------

CLIENT_DIR = Path(__file__).resolve().parent.parent / 'client'


def client_page():
    page = (CLIENT_DIR / 'index.html').read_text(encoding='utf-8')
    boot = json.dumps({'shell_origin': SHELL_ORIGIN, 'api_base_url': API_BASE}).replace('"', '&quot;')
    return HTMLResponse(page.replace('__BOOT__', boot), headers={
        'Content-Security-Policy': f'frame-ancestors {SHELL_ORIGIN} https://web.max.ru',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


for _path in ['/schedule']:
    app.add_api_route(_path, client_page, methods=['GET'], include_in_schema=False)


@app.get('/assets/{name}', include_in_schema=False)
def client_asset(name: str):
    target = (CLIENT_DIR / 'assets' / name).resolve()
    if target.parent != (CLIENT_DIR / 'assets').resolve() or not target.is_file():
        raise HTTPException(status_code=404, detail='Файл не найден')
    return FileResponse(target, headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'})

