"""Сервис «Люди» вуза: своя SQLite, online introspection ядра. Один контейнер — один вуз."""
import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from uuid import UUID

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from app import onboarding
from app.core_client import CoreClient, CoreUnavailable, BindingMissing

# Как у любого сервиса вуза: адреса и ключ — из .env, строки выдаёт карточка сервиса («Выдать ключ»).
CORE = os.environ.get('CORE_URL', '').rstrip('/')
SHELL_ORIGIN = os.environ.get('SHELL_ORIGIN', '').rstrip('/')
CLIENT_ID = os.environ.get('SERVICE_CLIENT_ID', '')
SECRET = os.environ.get('SERVICE_CLIENT_SECRET', '')
API_BASE = os.environ.get('SERVICE_API_BASE_URL', '').rstrip('/')
CLIENT_BASE = os.environ.get('SERVICE_CLIENT_BASE_URL', '').rstrip('/')
DB = os.environ.get('PROFILE_DB', '/data/profiles.db')
core = CoreClient(CORE, CLIENT_ID, SECRET, API_BASE, CLIENT_BASE)

# Меню и роль сервиса публикует он сам (как курсовые). Экраны рисует оболочка по id меню.
MANAGE = 'profiles.manage'
MANIFEST = {'titles': {'ru': 'Люди', 'en': 'People'}, 'menus': [
    {'id': 'home', 'titles': {'ru': 'Главная', 'en': 'Home'}, 'entrypoint_path': '/home',
     'profiles': ['admin', 'teacher', 'student'], 'required_permissions': [], 'order': 0},
    {'id': 'users', 'titles': {'ru': 'Пользователи', 'en': 'Users'}, 'entrypoint_path': '/users',
     'profiles': ['admin', 'teacher', 'student'], 'required_permissions': [], 'order': 10}],
    # Карточка своего профиля на главном экране (docs/services/sdk/WIDGETS_SPEC.md §9).
    'widgets': [{'id': 'me', 'titles': {'ru': 'Мой профиль', 'en': 'My profile'}, 'kind': 'profile', 'size': 'wide',
                 'profiles': ['admin', 'teacher', 'student'], 'required_permissions': [], 'data_path': '/profile/widgets/me',
                 'open_menu': 'home', 'order': 0}]}
ROLES = [{'code': 'profile_editor', 'titles': {'ru': 'Редактор анкет', 'en': 'Profile editor'},
          'allowed_profiles': ['admin'], 'permissions': [MANAGE]}]
STATE = {'onboarding': 'pending', 'error': None}

@contextmanager
def database():
    db = sqlite3.connect(DB, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    # WAL откатывает прерванную запись при следующем открытии файла, поэтому перезапуск
    # контейнера посреди PATCH не оставляет БД нерабочей, а busy_timeout ждёт чужую
    # запись вместо ошибки "database is locked". synchronous — штатный FULL.
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA busy_timeout=10000')
    try:
        yield db
    except BaseException:
        # Явный rollback освобождает write-lock сразу, не дожидаясь close().
        db.rollback()
        raise
    finally:
        db.close()

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
        # Обычная причина — том /data принадлежит root, а процесс работает под uid 10003.
        raise RuntimeError(f'Хранилище {DB} недоступно для записи: {error}') from error
    onboarding.start(core, MANIFEST, ROLES, STATE)
    yield


def prepare():
    """Создаёт схему; вызывается при каждом старте и безопасна для существующего тома."""
    Path(DB).parent.mkdir(parents=True, exist_ok=True)
    with database() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS cards (
            institution_id TEXT NOT NULL, service_id TEXT NOT NULL, user_id TEXT NOT NULL,
            display_name TEXT, about TEXT NOT NULL DEFAULT '', position TEXT, academic_degree TEXT,
            revision INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(institution_id, service_id, user_id))''')
        # Начало членства, при котором записана анкета. Том до этой версии — колонка добавляется.
        if 'member_since' not in {r['name'] for r in db.execute('PRAGMA table_info(cards)')}:
            db.execute('ALTER TABLE cards ADD COLUMN member_since TEXT')
        db.commit()

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

# Starlette HTTPException ловит и 404/405 маршрутизатора — все ошибки в формате контракта.
@app.exception_handler(StarletteHTTPException)
async def error(request, exc):
    codes = {401:'UNAUTHENTICATED',403:'FORBIDDEN',404:'RESOURCE_NOT_FOUND',400:'BAD_REQUEST',412:'PRECONDITION_FAILED',428:'PRECONDITION_REQUIRED'}
    return JSONResponse({'error': {'code': codes.get(exc.status_code,'VALIDATION_ERROR'), 'message':str(exc.detail), 'request_id':request.state.request_id}}, status_code=exc.status_code)

@app.exception_handler(RequestValidationError)
async def invalid(request, exc):
    details = [{'path':'.'.join(str(x) for x in e.get('loc',())),'message':e.get('msg','Validation error')} for e in exc.errors()]
    return JSONResponse({'error': {'code':'VALIDATION_ERROR','message':'Validation error','request_id':request.state.request_id,'details':details[:100]}},status_code=422)

@app.exception_handler(CoreUnavailable)
@app.exception_handler(BindingMissing)
async def unavailable(request, exc):
    return JSONResponse({'error': {'code':'SERVICE_UNAVAILABLE','message':'Ядро временно недоступно','request_id':request.state.request_id}},status_code=503)


def authenticate(request: Request):
    scheme, _, token = request.headers.get('authorization','').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token)>16384:
        raise HTTPException(401,'Требуется сессия сервиса')
    try:
        claims = jwt.decode(token, options={'verify_signature':False})
        service_id = str(UUID(claims['service_id']))
        if claims.get('token_use') != 'service_access' or claims.get('aud') != f'service:{service_id}':
            raise ValueError()
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise HTTPException(401,'Недействительная сессия')
    # Untrusted claims select only a binding; identity and live permissions come from core.
    try:
        binding = core.binding(service_id)
    except BindingMissing:
        # Ядро ответило определённо: экземпляра «Людей» с таким id нет. Это токен чужого
        # или снятого сервиса — по SDK SPEC §4.2 п.2 такой binding даёт 404, а 503
        # остаётся за случаем, когда ядро недоступно и проверить нечем.
        raise HTTPException(404,'Сервис недоступен для этой сессии')
    info = core.introspect(binding,token)
    if (not info.get('active') or info.get('service_id') != service_id
            or info.get('institution_id') != binding.institution_id
            or info.get('sub') != claims.get('sub')
            or info.get('profile') != claims.get('profile')
            or info.get('session_id') != claims.get('sid')
            or info.get('parent_session_id') != claims.get('parent_sid')
            or info.get('profile') not in ('student','teacher','admin')):
        raise HTTPException(401,'Сессия завершена')
    return binding,info


class Membership:
    """Что ядро знает об участнике: имя из регистрации и начало текущего членства."""
    def __init__(self, data):
        data = data if isinstance(data, dict) else {}
        name = data.get('display_name')
        self.name = name if isinstance(name, str) and name.strip() else None
        since = data.get('member_since')
        self.since = since if isinstance(since, str) else None


def member(ctx, user_id):
    """Проверяет членство; 404, если человек не участник вуза."""
    found = core.member(ctx[0],user_id)
    if not found:
        raise HTTPException(404,'Участник не найден')
    return Membership(found)


def forget_stale(db, ctx, user_id, since):
    """Анкета от прошлого членства удаляется: удалённого и снова добавленного участника
    встречает новая пустая анкета. Анкете без отметки (старый том) отметка ставится."""
    if not since:
        return
    row = db.execute('SELECT member_since FROM cards WHERE institution_id=? AND service_id=? AND user_id=?',
                     (ctx[0].institution_id,ctx[0].service_id,user_id)).fetchone()
    if row is None or row['member_since'] == since:
        return
    if row['member_since'] is None:
        db.execute('UPDATE cards SET member_since=? WHERE institution_id=? AND service_id=? AND user_id=?',
                   (since,ctx[0].institution_id,ctx[0].service_id,user_id))
    else:
        db.execute('DELETE FROM cards WHERE institution_id=? AND service_id=? AND user_id=?',
                   (ctx[0].institution_id,ctx[0].service_id,user_id))


def card(db, ctx, user_id, registered_name=None):
    """Анкета участника. Пока человек не задал имя сам, показывается имя из регистрации."""
    row = db.execute('SELECT * FROM cards WHERE institution_id=? AND service_id=? AND user_id=?',
                     (ctx[0].institution_id,ctx[0].service_id,user_id)).fetchone()
    data = dict(row) if row else dict(institution_id=ctx[0].institution_id,service_id=ctx[0].service_id,user_id=user_id,
                                   display_name=None,about='',position=None,academic_degree=None,revision=0)
    tag = '"'+hashlib.sha256(json.dumps([data['institution_id'],data['service_id'],user_id,data['revision']]).encode()).hexdigest()+'"'
    value = {k:data[k] for k in ('user_id','display_name','about','position','academic_degree')}
    if not value['display_name']:
        value['display_name'] = registered_name
    return value,tag,data['revision']


def get_card(ctx,user_id):
    who = member(ctx,user_id)
    with database() as db:
        forget_stale(db,ctx,user_id,who.since)
        value,etag,_ = card(db,ctx,user_id,who.name)
    return JSONResponse(value,headers={'ETag':etag})

class SelfPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    display_name: str = Field(default='',min_length=1,max_length=200)
    about: str = Field(default='',max_length=2000)
    @field_validator('display_name')
    @classmethod
    def name(cls,v):
        if not v.strip(): raise ValueError('Укажите имя')
        return v.strip()

class AcademicPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    position: str | None = Field(default=None,max_length=200)
    academic_degree: str | None = Field(default=None,max_length=200)


def patch_card(ctx,user_id,values,if_match):
    who = member(ctx,user_id)
    if not if_match: raise HTTPException(428,'Сначала загрузите актуальный профиль')
    with database() as db:
        # Serializes creation of virtual rows and updates, including concurrent first PATCH.
        db.execute('BEGIN IMMEDIATE')
        forget_stale(db,ctx,user_id,who.since)
        old,etag,revision = card(db,ctx,user_id)
        if if_match != etag: raise HTTPException(412,'Профиль изменился. Обновите страницу')
        old.update(values)
        db.execute('''INSERT INTO cards (institution_id,service_id,user_id,display_name,about,position,academic_degree,revision,member_since)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(institution_id,service_id,user_id) DO UPDATE SET
            display_name=excluded.display_name,about=excluded.about,position=excluded.position,
            academic_degree=excluded.academic_degree,revision=excluded.revision,member_since=excluded.member_since''',
            (ctx[0].institution_id,ctx[0].service_id,user_id,old['display_name'],old['about'],old['position'],old['academic_degree'],revision+1,who.since))
        value,new_tag,_=card(db,ctx,user_id,who.name)
        db.commit()
    return JSONResponse(value,headers={'ETag':new_tag})

@app.get('/api/v1/health')
def health(): return {'status':'ok','onboarding':STATE['onboarding']}

def text(titles, locale):
    # Обязателен перевод ru; остальные локали падают на него (SDK SPEC §2).
    return (titles[locale], locale) if locale in titles else (titles.get('ru',''), 'ru')

@app.get('/api/v1/service')
def service_view(locale:str|None=Query(None,max_length=8),ctx=Depends(authenticate)):
    if locale not in (None,'ru','en'): raise HTTPException(422,'locale: ru или en')
    binding,info=ctx
    manifest=core.manifest(binding)
    locale=locale or 'ru'
    permissions=list(info.get('permissions') or [])
    menus=[]
    for item in sorted(manifest.get('menus') or [],key=lambda m:(m.get('order',0),m.get('id',''))):
        if info['profile'] in (item.get('profiles') or []) and set(item.get('required_permissions') or [])<=set(permissions):
            name,used=text(item.get('titles') or {},locale)
            menus.append({'id':item['id'],'display_name':name,'locale':used,
                          'entrypoint_path':item['entrypoint_path'],'order':item.get('order',0)})
    name,used=text(manifest.get('titles') or {'ru':'Люди'},locale)
    return {'id':binding.service_id,'institution_id':binding.institution_id,'service_type':'user-profile',
            'deployment':'local','display_name':name,'locale':used,'api_base_url':binding.api_base_url,
            'client_base_url':binding.client_base_url,'profile':info['profile'],
            'roles':list(info.get('roles') or []),'permissions':permissions,'menus':menus}

@app.get('/api/v1/profile/me')
def me(ctx=Depends(authenticate)): return get_card(ctx,ctx[1]['sub'])

PROFILE_LABEL = {'student': 'Студент', 'teacher': 'Преподаватель', 'admin': 'Администратор'}
# Заполненность анкеты считается только по полям, которые человек меняет сам (PATCH /profile/me):
# должность и учёную степень вносит редактор анкет, их отсутствие не должно мешать дойти до 100 %.
FILL_FIELDS = (('display_name', 'имя'), ('about', 'о себе'))


@app.get('/api/v1/profile/widgets/me')
def widget_me(ctx=Depends(authenticate)):
    """Виджет «Мой профиль»: имя, профиль и группа, должность, заполненность анкеты (WIDGETS_SPEC §7)."""
    binding, info = ctx
    user_id, profile = info['sub'], info['profile']
    who = member(ctx, user_id)
    with database() as db:
        forget_stale(db, ctx, user_id, who.since)
        row = db.execute('SELECT * FROM cards WHERE institution_id=? AND service_id=? AND user_id=?',
                         (binding.institution_id, binding.service_id, user_id)).fetchone()
        value, _, _ = card(db, ctx, user_id, who.name)
    first = PROFILE_LABEL[profile]
    group_ids = info.get('group_ids') or []
    if profile == 'student' and group_ids:
        names = [g['name'] for g in core.groups(binding) if g['id'] in group_ids]
        if names:
            first += ' · Группа ' + ', '.join(names)
    lines = [first] + [x for x in (value.get('position'), value.get('academic_degree')) if x][:2]
    own = dict(row) if row else {}
    own['display_name'] = own.get('display_name') or who.name  # имя из заявки уже видно в карточке
    fields = FILL_FIELDS
    missing = [label for key, label in fields if not (own.get(key) or '').strip()]
    body = {'kind': 'profile', 'user_id': user_id, 'title': value.get('display_name') or 'Без имени', 'lines': lines,
            'progress': round(100 * (len(fields) - len(missing)) / len(fields))}
    if missing:
        body['hint'] = 'Добавьте ' + ', '.join(missing)
    return JSONResponse(body)

@app.patch('/api/v1/profile/me')
def update_me(patch:SelfPatch, if_match:str|None=Header(None),ctx=Depends(authenticate)):
    return patch_card(ctx,ctx[1]['sub'],patch.model_dump(exclude_unset=True),if_match)

@app.get('/api/v1/profile/users/{user_id}')
def user(user_id:UUID,ctx=Depends(authenticate)): return get_card(ctx,str(user_id))

@app.patch('/api/v1/profile/users/{user_id}')
def update_user(user_id:UUID,patch:AcademicPatch,if_match:str|None=Header(None),ctx=Depends(authenticate)):
    if ctx[1]['profile']!='admin' or MANAGE not in ctx[1].get('permissions',[]):
        raise HTTPException(403,'Нет права редактирования академических сведений')
    return patch_card(ctx,str(user_id),patch.model_dump(exclude_unset=True),if_match)

@app.get('/api/v1/profile/users')
def users(q:str=Query('',max_length=200),cursor:str|None=Query(None,max_length=256),limit:int=Query(50,ge=1,le=100),ctx=Depends(authenticate)):
    scope=f'{ctx[0].institution_id}:{ctx[0].service_id}:{q}:{limit}'
    def sign(after): return hmac.new(SECRET.encode(),f'{scope}:{after}'.encode(),hashlib.sha256).hexdigest()
    after=''
    if cursor:
        payload,sep,signature=cursor.rpartition('.')
        after,_,expiry=payload.partition(':')
        if not sep or not expiry.isdigit() or int(expiry)<time.time() or not hmac.compare_digest(signature,sign(payload)):
            raise HTTPException(400,'Недействительный курсор')
    # Список — действующие участники вуза из ядра, у которых есть имя: своё из анкеты
    # или указанное при регистрации. Заполнять анкету, чтобы попасть в «Людей», не нужно.
    registered={u:Membership(v) for u,v in core.members(ctx[0]).items()}
    with database() as db:
        stored={}
        for r in db.execute('SELECT user_id,member_since FROM cards WHERE institution_id=? AND service_id=?',
                            (ctx[0].institution_id,ctx[0].service_id)).fetchall():
            if r['user_id'] not in registered:
                # Человека удалили из вуза — его анкета удаляется вместе с членством.
                db.execute('DELETE FROM cards WHERE institution_id=? AND service_id=? AND user_id=?',
                           (ctx[0].institution_id,ctx[0].service_id,r['user_id']))
                continue
            forget_stale(db,ctx,r['user_id'],registered[r['user_id']].since)
            stored[r['user_id']]=True
        needle=q.casefold()
        matched=[]
        for user_id in sorted(u for u in registered if u>after):
            who=registered[user_id]
            value=card(db,ctx,user_id,who.name)[0] if user_id in stored else \
                {'user_id':user_id,'display_name':who.name,'about':'','position':None,'academic_degree':None}
            if value['display_name'] and needle in (value['display_name']+' '+(value['position'] or '')).casefold():
                matched.append(value)
                if len(matched)>limit:
                    break
        items=matched[:limit]
        next_cursor=None
        if len(matched)>limit:
            last=items[-1]['user_id']+':'+str(int(time.time())+900);next_cursor=last+'.'+sign(last)
    return {'items':items,'next_cursor':next_cursor}

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


for _path in ['/home', '/users']:
    app.add_api_route(_path, client_page, methods=['GET'], include_in_schema=False)


@app.get('/assets/{name}', include_in_schema=False)
def client_asset(name: str):
    target = (CLIENT_DIR / 'assets' / name).resolve()
    if target.parent != (CLIENT_DIR / 'assets').resolve() or not target.is_file():
        raise HTTPException(status_code=404, detail='Файл не найден')
    return FileResponse(target, headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'})


from app.privacy_protocol import PrivacyGuard
from app.privacy_cleanup import erase as erase_personal_data
app.add_middleware(PrivacyGuard, erase=erase_personal_data, client=lambda: core)
