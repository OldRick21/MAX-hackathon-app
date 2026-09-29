"""Сквозная проверка на локальном стеке (ядро, пульт, три раннера): подключение вуза, меню и роли
сервисов, расписание, «Люди», администрирование, выключение сервиса, удаление вуза и остатки.
Запускается из scripts/e2e/run.sh; рабочий каталог — временный каталог стека."""
import httpx, time, os, sqlite3, datetime as dt
S=os.getcwd(); CORE='http://127.0.0.1:8000'; PORT={'schedule':18100,'user-profile':18101,'administration':18102}
ok_all=[]
def check(name, cond, info=''):
    ok_all.append(bool(cond)); print(('  ✓ ' if cond else '  ✗ ')+name+('' if cond else f'  → {info}'))
def wait(fn, t=40):
    end=time.time()+t
    while time.time()<end:
        try:
            if fn(): return True
        except Exception: pass
        time.sleep(0.5)
    return False
c=httpx.Client(base_url=CORE, timeout=20)
ready = wait(lambda: httpx.get(CORE+'/api/v1/health').status_code == 200
             and httpx.get('http://127.0.0.1:18500/api/v1/health').status_code == 200
             and all(httpx.get(f'http://127.0.0.1:{p}/health').json()['instances'] >= 1 for p in PORT.values()), 90)
if not ready:
    raise SystemExit('стек не поднялся за 90 секунд')
def login(n):
    t=c.post('/api/v1/auth/token', json={'username':n}).json(); h={'Authorization':'Bearer '+t['access_token']}
    return c.get('/api/v1/auth/me', headers=h).json()['id'], h, t
op=httpx.Client(base_url='http://127.0.0.1:18500', timeout=20); W={'X-Operator':'1'}
check('вход в пульт', op.post('/api/login', json={'password':'correct horse battery'}).status_code==200)

print('1. Подключение вуза')
owner, oh, _ = login('e2e_rector'); stud, sh, stok = login('e2e_student'); teach, th, _ = login('e2e_teacher')
app=c.post('/api/v1/institution-applications', headers=oh, json={'titles':{'ru':'Сквозной вуз'},'contact':'r@x.ru'}).json()['id']
r=op.post(f'/api/applications/{app}/approve', headers=W, json={}); iid=r.json()['institution_id']
check('заявка одобрена', r.status_code==200, r.text)
check('владелец назначен', op.post(f'/api/institutions/{iid}/owners', headers=W, json={'user_id':owner}).status_code==200)
svcs={s['service_type']:s['id'] for s in op.get(f'/api/institutions/{iid}').json()['services']}
check('у вуза три облачных сервиса', set(svcs)=={'administration','schedule','user-profile'}, svcs)
base=f'/api/institutions/{iid}/manage'
g=op.post(f'{base}/groups', headers=W, json={'name':'ПИ-11'}).json()['id']
jr=c.post('/api/v1/join-requests', headers=sh, json={'full_name':'Анна Студентова','items':[{'institution_id':iid,'profile':'student','group_id':g}]}).json()
check('заявка студента одобрена', op.post(f"{base}/join-requests/{jr['items'][0]['id']}/approve", headers=W).status_code==200)
jt=c.post('/api/v1/join-requests', headers=th, json={'full_name':'Пётр Преподов','items':[{'institution_id':iid,'profile':'teacher'}]}).json()
check('заявка преподавателя одобрена', op.post(f"{base}/join-requests/{jt['items'][0]['id']}/approve", headers=W).status_code==200)

print('2. Сервисы сами публикуют меню и роли')
def menus(h, prof):
    r=c.get(f'/api/v1/institution/{iid}/service', params={'profile':prof}, headers=h); return {s['service_type']:[m['id'] for m in s['menus']] for s in r.json()['items']}
check('расписание и «Люди» опубликовали меню', wait(lambda: {'schedule','user-profile'} <= set(menus(sh,'student'))), menus(sh,'student'))
print('    студент видит:', menus(sh,'student')); print('    преподаватель видит:', menus(th,'teacher')); print('    администратор видит:', menus(oh,'admin'))
check('админ без ролей не видит расписание', 'schedule' not in menus(oh,'admin'))
roles=[x['code'] for x in op.get(f"{base}/services/{svcs['schedule']}/roles").json()['items']]
check('роли расписания опубликованы', {'schedule_editor','teacher_editor'} <= set(roles), roles)

print('3. Назначение роли и работа с расписанием через раннер')
path=f"{base}/services/{svcs['schedule']}/users/{owner}/profiles/admin/roles"
tag=op.get(path).headers['ETag']
check('владелец назначил себе schedule_editor', op.put(path, headers={**W,'If-Match':tag}, json={'roles':['schedule_editor']}).status_code==200)
check('админ видит schedule_admin', 'schedule_admin' in menus(oh,'admin').get('schedule',[]), menus(oh,'admin'))
def session(h, t, prof):
    r=c.post(f'/api/v1/institution/{iid}/service/{svcs[t]}/session', headers=h, json={'profile':prof}); return r
sa=session(oh,'schedule','admin'); check('сессия админа в расписании', sa.status_code==201, sa.text)
sched=httpx.Client(base_url=f"http://127.0.0.1:{PORT['schedule']}/{svcs['schedule']}", timeout=20)
start=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=1)).replace(microsecond=0)
ev={'title':'Базы данных','starts_at':start.isoformat().replace('+00:00','Z'),'ends_at':(start+dt.timedelta(hours=1,minutes=30)).isoformat().replace('+00:00','Z'),
    'group_ids':[g],'teacher_ids':[teach],'location':'А-101','description':'Лекция','status':'scheduled'}
r=sched.post('/api/v1/schedule/events', headers={'Authorization':'Bearer '+sa.json()['access_token'],'Idempotency-Key':str(__import__('uuid').uuid4())}, json=ev)
check('админ создал занятие', r.status_code==201, r.text[:300])
ss=session(sh,'schedule','student'); sth={'Authorization':'Bearer '+ss.json()['access_token']}
r=sched.get('/api/v1/schedule/events', headers=sth, params={'from':start.date().isoformat()+'T00:00:00Z','to':(start+dt.timedelta(days=1)).date().isoformat()+'T00:00:00Z'})
check('студент видит занятие своей группы', r.status_code==200 and any(e['title']=='Базы данных' for e in r.json().get('items',[])), r.text[:300])
check('студент не может создать занятие', sched.post('/api/v1/schedule/events', headers={**sth,'Idempotency-Key':str(__import__('uuid').uuid4())}, json=ev).status_code==403)
tsess=session(th,'schedule','teacher')
r=sched.get('/api/v1/schedule/events', headers={'Authorization':'Bearer '+tsess.json()['access_token']}, params={'from':start.date().isoformat()+'T00:00:00Z','to':(start+dt.timedelta(days=1)).date().isoformat()+'T00:00:00Z'})
check('преподаватель видит своё занятие', r.status_code==200 and len(r.json().get('items',[]))>=1, r.text[:200])

imp_day=(start+dt.timedelta(days=1)).astimezone(dt.timezone(dt.timedelta(hours=3))).strftime('%d.%m.%Y')
csv_file=('Дата;Начало;Конец;Дисциплина;Группы;Преподаватели;Аудитория\n'
          f'{imp_day};09:00;10:30;Импорт: алгебра;ПИ-11;Пётр Преподов;Б-2\n').encode('cp1251')
ah_s={'Authorization':'Bearer '+sa.json()['access_token'],'Content-Type':'application/octet-stream'}
r=sched.post('/api/v1/schedule/import', params={'name':'1c.csv','dry_run':'true'}, headers=ah_s, content=csv_file)
check('импорт 1С-CSV: проверка', r.status_code==200 and r.json()['to_create']==1 and not r.json()['errors'], r.text[:300])
r=sched.post('/api/v1/schedule/import', params={'name':'1c.csv','dry_run':'false'}, headers=ah_s, content=csv_file)
check('импорт 1С-CSV: загрузка', r.status_code==200 and r.json()['created']==1, r.text[:300])
r=sched.get('/api/v1/schedule/events', headers={'Authorization':'Bearer '+tsess.json()['access_token']},
            params={'from':start.date().isoformat()+'T00:00:00Z','to':(start+dt.timedelta(days=3)).date().isoformat()+'T00:00:00Z'})
check('преподаватель видит импортированное занятие', any(e['title']=='Импорт: алгебра' for e in r.json().get('items',[])), r.text[:300])

print('4. «Люди» через раннер')
ps=session(sh,'user-profile','student'); people=httpx.Client(base_url=f"http://127.0.0.1:{PORT['user-profile']}/{svcs['user-profile']}", timeout=20)
r=people.get('/api/v1/profile/me', headers={'Authorization':'Bearer '+ps.json()['access_token']})
check('анкета студента с именем из заявки', r.status_code==200 and 'Анна' in r.text, r.text[:300])

print('5. Администрирование через раннер (отдельный origin)')
adm=httpx.Client(base_url=f"http://127.0.0.1:{PORT['administration']}/{svcs['administration']}", timeout=20)
check('страница /admin отдаётся', adm.get('/admin').status_code==200)
asess=session(oh,'administration','admin'); ah={'Authorization':'Bearer '+asess.json()['access_token']}
r=adm.get('/api/v1/administration/members', headers=ah)
check('админка читает участников через private API ядра', r.status_code==200 and len(r.json().get('items',[]))==3, r.text[:300])
r=adm.get('/api/v1/administration/services', headers=ah)
check('админка видит три сервиса', r.status_code==200 and len(r.json()['items'])==3, r.text[:200])
check('студент не попадает в администрирование', session(sh,'administration','admin').status_code in (403,404))
check('private API ядра снаружи без ключа закрыт', c.get(f'/api/v1/institution/{iid}/internal').status_code in (401,404))

print('5a. Виджеты главного экрана')
def home(h, prof):
    r=c.get(f'/api/v1/institution/{iid}/widgets', params={'profile':prof}, headers=h)
    return {w['widget_id']: w for w in r.json().get('items', [])} if r.status_code==200 else r.status_code
check('студент: профиль и «Сегодня»', wait(lambda: set(home(sh,'student'))=={'me','today'}), home(sh,'student'))
check('администратор с ролью: профиль, занятия дня по вузу и сводка', set(home(oh,'admin'))=={'me','today_admin','today_stats'}, home(oh,'admin'))
w=home(sh,'student')['today']
r=httpx.get(w['data_url'].replace('https://shell.test/schedule', f"http://127.0.0.1:{PORT['schedule']}"), headers=sth)
check('данные «Сегодня» через раннер', r.status_code==200 and r.json()['kind']=='events', r.text[:200])
me=home(sh,'student')['me']
psh={'Authorization':'Bearer '+ps.json()['access_token']}
r=httpx.get(me['data_url'].replace('https://shell.test/people', f"http://127.0.0.1:{PORT['user-profile']}"), headers=psh)
check('карточка профиля: имя и группа', r.status_code==200 and r.json()['title']=='Анна Студентова' and 'ПИ-11' in ' '.join(r.json()['lines']), r.text[:200])
wl=adm.get(f"/api/v1/administration/services/{svcs['schedule']}/widgets", headers=ah)
check('админка видит виджеты расписания', wl.status_code==200 and {x['id'] for x in wl.json()['items']}=={'today','today_admin','today_stats'}, wl.text[:200])
r=adm.put(f"/api/v1/administration/services/{svcs['schedule']}/widgets/today/visibility",
          headers={**ah,'If-Match':wl.headers.get('ETag')}, json={'visibility':{'student':False,'teacher':True}})
check('администратор скрыл «Сегодня» у студентов', r.status_code==200, r.text[:200])
check('у студента «Сегодня» пропал, у преподавателя остался', 'today' not in home(sh,'student') and 'today' in home(th,'teacher'))
wl=adm.get(f"/api/v1/administration/services/{svcs['schedule']}/widgets", headers=ah)
adm.put(f"/api/v1/administration/services/{svcs['schedule']}/widgets/today/visibility",
        headers={**ah,'If-Match':wl.headers.get('ETag')}, json={'visibility':{'student':True,'teacher':True}})
check('вернули студентам', 'today' in home(sh,'student'))

print('6. Выключение сервиса')
sp=f"/api/v1/administration/services/{svcs['schedule']}"
tag=adm.get(sp, headers=ah).headers.get('ETag')
r=adm.patch(sp, headers={**ah,'If-Match':tag}, json={'enabled':False})
check('админ выключил расписание', r.status_code==200 and r.json()['enabled'] is False, r.text[:200])
check('студент больше не видит расписание', 'schedule' not in menus(sh,'student'))
check('старый токен студента в расписании не работает', sched.get('/api/v1/schedule/events', headers=sth).status_code in (401,403,503))
tag=adm.get(sp, headers=ah).headers.get('ETag')
r=adm.patch(sp, headers={**ah,'If-Match':tag}, json={'enabled':True})
check('админ включил обратно (меню уже опубликовано)', r.status_code==200 and r.json()['enabled'], r.text[:200])
check('студент снова видит расписание', 'schedule' in menus(sh,'student'))

print('7. Удаление вуза и остатки')
demo_before = {p: httpx.get(f'http://127.0.0.1:{p}/health').json()['instances']-1 for p in PORT.values()}
r=op.request('DELETE', f'/api/institutions/{iid}', headers=W, json={'confirm_title':'Сквозной вуз'})
check('вуз удалён', r.status_code==204, r.text)
check('раннеры остановили процессы вуза', wait(lambda: all(httpx.get(f'http://127.0.0.1:{p}/health').json()['instances']==demo_before[p] for p in PORT.values())))
time.sleep(2)
check('данные сервисов вуза стёрты', not any(os.path.exists(f'{S}/data/{t}/{sid}') for t,sid in svcs.items()), {t: os.listdir(f'{S}/data/{t}') for t in PORT})
check('файлы настроек сервисов удалены', not any(sid in ' '.join(os.listdir(f'{S}/connected')) for sid in svcs.values()))
check('прокси раннера отвечает 404', sched.get('/api/v1/schedule/events', headers=sth).status_code==404)
check('вуз недоступен владельцу', c.get(f'/api/v1/institution/{iid}', headers=oh).status_code==404)
db=sqlite3.connect(f'{S}/core.db'); ids=[iid, app, g, *svcs.values()]; found=[]
for (t,) in db.execute("select name from sqlite_master where type='table'"):
    if t in ('audit_events','purged_instances'): continue
    for col in [r[1] for r in db.execute(f'pragma table_info("{t}")')]:
        for v in ids:
            if db.execute(f'select count(*) from "{t}" where cast("{col}" as text) like ?', (f'%{v}%',)).fetchone()[0]: found.append(f'{t}.{col}')
check('в базе ядра нет строк вуза (кроме журнала и списка очистки)', not found, found)
check('refresh-история сессий сервисов вуза удалена', db.execute("select count(*) from refresh_uses r where token_use='service_refresh' and not exists(select 1 from service_sessions s where s.id=r.session_id)").fetchone()[0]==0)
check('демо-вуз не затронут', all(httpx.get(f'http://127.0.0.1:{p}/health').json()['instances']==demo_before[p] and p for p in PORT.values()) and db.execute("select count(*) from institutions").fetchone()[0]>=1)
print(f'\nИТОГО: {sum(ok_all)}/{len(ok_all)} проверок пройдено')
