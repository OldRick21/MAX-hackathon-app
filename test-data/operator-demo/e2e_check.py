"""Модуль пульта «Тестовые данные»: создание (вузы, группы, люди, расписание через раннер) и полное удаление.

    ./scripts/e2e/run.sh test-data/operator-demo/e2e_check.py
"""
import os, sqlite3, time, httpx
S = os.getcwd()
ok_all = []
def check(name, cond, info=''):
    ok_all.append(bool(cond)); print(('  ✓ ' if cond else '  ✗ ') + name + ('' if cond else f'  → {info}'))
def wait(fn, t=240):
    end = time.time() + t
    while time.time() < end:
        try:
            if fn(): return True
        except Exception: pass
        time.sleep(1)
    return False
ready = wait(lambda: httpx.get('http://127.0.0.1:8000/api/v1/health').status_code == 200 and httpx.get('http://127.0.0.1:18500/api/v1/health').status_code == 200, 90)
op = httpx.Client(base_url='http://127.0.0.1:18500', timeout=60); W = {'X-Operator': '1'}
P = '/api/plugins/operator-demo'
op.post('/api/login', json={'password': 'correct horse battery'})
db = lambda: sqlite3.connect(f'{S}/core.db')
before = {t: db().execute(f'select count(*) from {t}').fetchone()[0] for t in ('institutions', 'users', 'memberships')}
r = op.post(f'{P}/create', headers=W, json={'institutions': 2, 'students': 5})
check('модуль загружен в пульт', any(x['id'] == 'operator-demo' for x in op.get('/api/plugins').json()['items']))
check('создание запущено', r.status_code == 200, r.text[:300])
check('загрузка расписания завершилась', wait(lambda: op.get(f'{P}/status').json()['state'] != 'running'), op.get(f'{P}/status').json())
st = op.get(f'{P}/status').json()
check('без ошибок', st['state'] == 'idle', st)
check('2 тестовых вуза и 2×(6+20+1)=54 пользователя', (st['institutions'], st['users']) == (2, 54), st)
c = db()
insts = [r[0] for r in c.execute("select object_id from test_data_records where kind='institution'")]
sched = [r[0] for r in c.execute(f"select id from services where service_type='schedule' and institution_id in ({','.join('?'*len(insts))})", insts)]
check('группы: по 4 в вузе', c.execute(f"select count(*) from study_groups where institution_id in ({','.join('?'*len(insts))})", insts).fetchone()[0] == 8)
counts = []
for sid in sched:
    p = f'{S}/data/schedule/{sid}/schedule.db'
    counts.append(sqlite3.connect(p).execute('select count(*) from events').fetchone()[0] if os.path.exists(p) else 0)
check('расписание загружено в оба вуза', all(n >= 40 for n in counts), counts)
check('админ тестового вуза — редактор расписания', c.execute("select count(*) from role_assignments where roles like '%schedule_editor%'").fetchone()[0] >= 2)
r = op.post(f'{P}/delete', headers=W, json={})
check('удаление', r.status_code == 200, r.text[:200])
st = op.get(f'{P}/status').json()
check('тестовых данных не осталось', (st['institutions'], st['users']) == (0, 0), st)
after = {t: db().execute(f'select count(*) from {t}').fetchone()[0] for t in ('institutions', 'users', 'memberships')}
check('настоящие данные не тронуты (вузы, пользователи, членства как до создания)', after == before, (before, after))
check('данные расписания в раннере стёрты', wait(lambda: not any(os.path.exists(f'{S}/data/schedule/{sid}') for sid in sched), 30))
print(f'\nИТОГО: {sum(ok_all)}/{len(ok_all)} проверок пройдено')
