"""People cloud service: isolated SQLite storage, online core introspection."""
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
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.core_client import CoreClient, CoreUnavailable, BindingMissing

CORE = os.environ.get('CORE_INTERNAL_URL', 'http://backend:8000')
SECRET = os.environ.get('USER_PROFILE_PROVISIONING_TOKEN', '')
DB = os.environ.get('PROFILE_DB', '/data/profiles.db')
core = CoreClient(CORE, SECRET)

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
    if len(SECRET) < 32:
        raise RuntimeError('Set USER_PROFILE_PROVISIONING_TOKEN (32+ characters)')
    try:
        prepare()
    except (OSError, sqlite3.Error) as error:
        # Обычная причина — том /data принадлежит root, а процесс работает под uid 10003.
        raise RuntimeError(f'Хранилище {DB} недоступно для записи: {error}') from error
    yield


def prepare():
    """Создаёт схему; вызывается при каждом старте и безопасна для существующего тома."""
    Path(DB).parent.mkdir(parents=True, exist_ok=True)
    with database() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS cards (
            institution_id TEXT NOT NULL, service_id TEXT NOT NULL, user_id TEXT NOT NULL,
            display_name TEXT, about TEXT NOT NULL DEFAULT '', position TEXT, academic_degree TEXT,
            revision INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(institution_id, service_id, user_id))''')
        db.commit()

app = FastAPI(lifespan=lifespan)

@app.middleware('http')
async def headers(request, call_next):
    request.state.request_id = str(uuid.uuid4())
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Request-ID'] = request.state.request_id
    return response

@app.exception_handler(HTTPException)
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


def member(ctx, user_id):
    if not core.member(ctx[0],user_id):
        raise HTTPException(404,'Участник не найден')


def card(db, ctx, user_id):
    row = db.execute('SELECT * FROM cards WHERE institution_id=? AND service_id=? AND user_id=?',
                     (ctx[0].institution_id,ctx[0].service_id,user_id)).fetchone()
    data = dict(row) if row else dict(institution_id=ctx[0].institution_id,service_id=ctx[0].service_id,user_id=user_id,
                                   display_name=None,about='',position=None,academic_degree=None,revision=0)
    tag = '"'+hashlib.sha256(json.dumps([data['institution_id'],data['service_id'],user_id,data['revision']]).encode()).hexdigest()+'"'
    return {k:data[k] for k in ('user_id','display_name','about','position','academic_degree')},tag,data['revision']


def get_card(ctx,user_id):
    member(ctx,user_id)
    with database() as db:
        value,etag,_ = card(db,ctx,user_id)
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
    member(ctx,user_id)
    if not if_match: raise HTTPException(428,'Сначала загрузите актуальный профиль')
    with database() as db:
        # Serializes creation of virtual rows and updates, including concurrent first PATCH.
        db.execute('BEGIN IMMEDIATE')
        old,etag,revision = card(db,ctx,user_id)
        if if_match != etag: raise HTTPException(412,'Профиль изменился. Обновите страницу')
        old.update(values)
        db.execute('''INSERT INTO cards VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(institution_id,service_id,user_id) DO UPDATE SET
            display_name=excluded.display_name,about=excluded.about,position=excluded.position,
            academic_degree=excluded.academic_degree,revision=excluded.revision''',
            (ctx[0].institution_id,ctx[0].service_id,user_id,old['display_name'],old['about'],old['position'],old['academic_degree'],revision+1))
        value,new_tag,_=card(db,ctx,user_id)
        db.commit()
    return JSONResponse(value,headers={'ETag':new_tag})

@app.get('/api/v1/health')
def health(): return {'status':'ok'}

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
            'deployment':'cloud','display_name':name,'locale':used,'api_base_url':binding.api_base_url,
            'client_base_url':binding.client_base_url,'profile':info['profile'],
            'roles':list(info.get('roles') or []),'permissions':permissions,'menus':menus}

@app.get('/api/v1/profile/me')
def me(ctx=Depends(authenticate)): return get_card(ctx,ctx[1]['sub'])

@app.patch('/api/v1/profile/me')
def update_me(patch:SelfPatch, if_match:str|None=Header(None),ctx=Depends(authenticate)):
    return patch_card(ctx,ctx[1]['sub'],patch.model_dump(exclude_unset=True),if_match)

@app.get('/api/v1/profile/users/{user_id}')
def user(user_id:UUID,ctx=Depends(authenticate)): return get_card(ctx,str(user_id))

@app.patch('/api/v1/profile/users/{user_id}')
def update_user(user_id:UUID,patch:AcademicPatch,if_match:str|None=Header(None),ctx=Depends(authenticate)):
    if ctx[1]['profile']!='admin' or 'profiles.manage' not in ctx[1].get('permissions',[]):
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
    with database() as db:
        rows=db.execute('''SELECT user_id,display_name,position FROM cards WHERE institution_id=? AND service_id=?
            AND display_name IS NOT NULL AND display_name != '' AND user_id>? ORDER BY user_id LIMIT ?''',
            (ctx[0].institution_id,ctx[0].service_id,after,limit+1)).fetchall()
        items=[]
        for row in rows[:limit]:
            if q.casefold() in (row['display_name']+' '+(row['position'] or '')).casefold() and core.member(ctx[0],row['user_id']):
                items.append(card(db,ctx,row['user_id'])[0])
        next_cursor=None
        if len(rows)>limit:
            last=rows[limit-1]['user_id']+':'+str(int(time.time())+900);next_cursor=last+'.'+sign(last)
    return {'items':items,'next_cursor':next_cursor}
