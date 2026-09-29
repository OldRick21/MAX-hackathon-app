import os
import tempfile
import unittest
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

_tmp=tempfile.TemporaryDirectory()
os.environ['PROFILE_DB']=_tmp.name+'/profiles.db'
os.environ.update(CORE_URL='https://core.test',SHELL_ORIGIN='https://shell.test',SERVICE_CLIENT_ID='client',
                  SERVICE_CLIENT_SECRET='t'*48,SERVICE_API_BASE_URL='https://p.example/api/v1',
                  SERVICE_CLIENT_BASE_URL='https://p.example')
from fastapi.testclient import TestClient
import jwt
from app import main as m

# Публикацию меню и ролей здесь не проверяем: заглушки ядра нет.
m.onboarding.start=lambda *a,**k:None

U='11111111-1111-4111-8111-111111111111'
V='22222222-2222-4222-8222-222222222222'
S='33333333-3333-4333-8333-333333333333'
I='44444444-4444-4444-8444-444444444444'
SID='55555555-5555-4555-8555-555555555555'
PSID='66666666-6666-4666-8666-666666666666'

BINDING=SimpleNamespace(service_id=S,institution_id=I,api_base_url='https://p.example/api/v1',client_base_url='https://p.example')
MANIFEST={'titles':{'ru':'Люди','en':'People'},'menus':[
    {'id':'users','titles':{'ru':'Пользователи','en':'Users'},'entrypoint_path':'/users','profiles':['admin','teacher','student'],'required_permissions':[],'order':10},
    {'id':'home','titles':{'ru':'Главная'},'entrypoint_path':'/home','profiles':['admin','teacher','student'],'required_permissions':[],'order':0},
    {'id':'staff','titles':{'ru':'Кадры'},'entrypoint_path':'/staff','profiles':['admin'],'required_permissions':['profiles.manage'],'order':20},
]}


def info(profile='student',permissions=()):
    return {'active':True,'service_id':S,'institution_id':I,'sub':U,'profile':profile,
            'session_id':SID,'parent_session_id':PSID,'roles':[],'permissions':list(permissions)}


def head(profile='student'):
    claims={'service_id':S,'sub':U,'aud':'service:'+S,'token_use':'service_access',
            'profile':profile,'sid':SID,'parent_sid':PSID}
    return {'Authorization':'Bearer '+jwt.encode(claims,'x'*32)}


class Profiles(unittest.TestCase):
    def test_access_storage_and_conflicts(self):
        state=info()
        h=head()
        with patch.object(m.core,'binding',return_value=BINDING), patch.object(m.core,'introspect',side_effect=lambda *a: state), \
             patch.object(m.core,'member',return_value=True) as member, \
             patch.object(m.core,'members',side_effect=lambda b: {U: None} if m.core.member(b,U) else {}), TestClient(m.app) as c:
            self.assertEqual(c.get('/api/v1/profile/me').status_code,401)
            empty=c.get('/api/v1/profile/me',headers=h)
            self.assertIsNone(empty.json()['display_name'])
            self.assertEqual(c.get('/api/v1/profile/users',headers=h).json()['items'],[])
            etag=empty.headers['etag']
            self.assertEqual(c.patch('/api/v1/profile/me',headers=h,json={'display_name':'Андрей'}).status_code,428)
            def save(name):return c.patch('/api/v1/profile/me',headers={**h,'If-Match':etag},json={'display_name':name})
            with ThreadPoolExecutor(2) as pool:
                result=list(pool.map(save,['Андрей','Иван']))
            self.assertEqual(sorted(x.status_code for x in result),[200,412])
            current=c.get('/api/v1/profile/me',headers=h)
            self.assertEqual(c.get('/api/v1/profile/users',headers=h).json()['items'][0]['user_id'],U)
            # Чужое поле в своей анкете: 422 в конверте ErrorResponse, а не в формате FastAPI.
            rejected=c.patch('/api/v1/profile/me',headers={**h,'If-Match':current.headers['etag']},json={'position':'Ректор'})
            self.assertEqual(rejected.status_code,422)
            self.assertEqual(rejected.json()['error']['code'],'VALIDATION_ERROR')
            self.assertTrue(rejected.json()['error']['details'])
            academic={'position':'Доцент'}
            self.assertEqual(c.patch('/api/v1/profile/users/'+U,headers={**h,'If-Match':current.headers['etag']},json=academic).status_code,403)
            state.update(info('admin'))
            admin=head('admin')
            self.assertEqual(c.patch('/api/v1/profile/users/'+U,headers={**admin,'If-Match':current.headers['etag']},json=academic).status_code,403)
            state.update(info('admin',['profiles.manage']))
            self.assertEqual(c.patch('/api/v1/profile/users/'+U,headers={**admin,'If-Match':current.headers['etag']},json=academic).status_code,200)
            self.assertEqual(len(c.get('/api/v1/profile/users?q=доцент',headers=admin).json()['items']),1)
            member.return_value=False
            self.assertEqual(c.get('/api/v1/profile/users/'+U,headers=admin).status_code,404)
            self.assertEqual(c.get('/api/v1/profile/users',headers=admin).json()['items'],[])
            member.side_effect=m.CoreUnavailable('offline')
            self.assertEqual(c.get('/api/v1/profile/me',headers=admin).status_code,503)
            member.side_effect=None;member.return_value=True
            state['active']=False
            self.assertEqual(c.get('/api/v1/profile/me',headers=admin).status_code,401)
            state.update(info('admin',['profiles.manage']),institution_id=V)
            self.assertEqual(c.get('/api/v1/profile/me',headers=admin).status_code,401)
            # Профиль и сессия из токена обязаны совпадать с introspection.
            state.update(info('admin',['profiles.manage']))
            self.assertEqual(c.get('/api/v1/profile/me',headers=head('teacher')).status_code,401)
            state.update(info('admin',['profiles.manage']),session_id=PSID)
            self.assertEqual(c.get('/api/v1/profile/me',headers=admin).status_code,401)
            state.update(info('admin',['profiles.manage']))
            # Другой вуз того же типа не видит сохранённые карточки.
            with patch.object(m.core,'binding',return_value=SimpleNamespace(service_id=S,institution_id=V,api_base_url='x',client_base_url='y')):
                state.update(info('admin',['profiles.manage']),institution_id=V)
                self.assertEqual(c.get('/api/v1/profile/users',headers=admin).json()['items'],[])

    def test_widget_me(self):
        """Виджет «Мой профиль»: имя из регистрации, группа из ядра, заполненность анкеты."""
        state={**info(),'group_ids':['g1']}
        h=head()
        registered={'profiles':['student'],'display_name':'Мария Ильина'}
        with tempfile.TemporaryDirectory() as folder, patch.object(m,'DB',folder+'/w.db'), \
             patch.object(m.core,'binding',return_value=BINDING), patch.object(m.core,'introspect',side_effect=lambda *a: state), \
             patch.object(m.core,'member',return_value=registered), \
             patch.object(m.core,'groups',return_value=[{'id':'g1','name':'ПИ-11'},{'id':'g2','name':'ПИ-12'}]), TestClient(m.app) as c:
            w=c.get('/api/v1/profile/widgets/me',headers=h).json()
            # Имя из заявки уже есть — не хватает только «О себе».
            self.assertEqual((w['kind'],w['user_id'],w['title'],w['lines'],w['progress']),
                             ('profile',U,'Мария Ильина',['Студент · Группа ПИ-11'],50))
            self.assertEqual(w['hint'],'Добавьте о себе')
            me=c.get('/api/v1/profile/me',headers=h)
            c.patch('/api/v1/profile/me',headers={**h,'If-Match':me.headers['etag']},json={'display_name':'Маша','about':'Люблю матан'})
            w=c.get('/api/v1/profile/widgets/me',headers=h).json()
            self.assertEqual((w['title'],w['progress'],'hint' in w),('Маша',100,False))
            state.update(info('teacher'))
            w=c.get('/api/v1/profile/widgets/me',headers=head('teacher')).json()
            # Должность и степень вносит редактор анкет — сам преподаватель доходит до 100 %.
            self.assertEqual((w['lines'],w['progress'],'hint' in w),(['Преподаватель'],100,False))
        self.assertEqual([x['id'] for x in m.MANIFEST['widgets']],['me'])

    def test_registered_name_without_card(self):
        """Имя из регистрации: участник сразу в «Людях», своя правка имени важнее."""
        state=info()
        h=head()
        registered={'profiles':['student'],'display_name':'Мария Ильина'}
        with tempfile.TemporaryDirectory() as folder, patch.object(m,'DB',folder+'/names.db'), \
             patch.object(m.core,'binding',return_value=BINDING), patch.object(m.core,'introspect',side_effect=lambda *a: state), \
             patch.object(m.core,'member',return_value=registered), \
             patch.object(m.core,'members',return_value={U:{'display_name':'Мария Ильина'},V:{'display_name':None}}), TestClient(m.app) as c:
            me=c.get('/api/v1/profile/me',headers=h)
            self.assertEqual(me.json()['display_name'],'Мария Ильина')
            # V без имени в список не попадает; U — попадает без заполненной анкеты.
            self.assertEqual([x['display_name'] for x in c.get('/api/v1/profile/users',headers=h).json()['items']],['Мария Ильина'])
            self.assertEqual(len(c.get('/api/v1/profile/users?q=ильина',headers=h).json()['items']),1)
            # Правка должности администратором не записывает имя из регистрации в анкету.
            state.update(info('admin',['profiles.manage']))
            saved=c.patch('/api/v1/profile/users/'+U,headers={**head('admin'),'If-Match':me.headers['etag']},json={'position':'Староста'})
            self.assertEqual(saved.json()['display_name'],'Мария Ильина')
            with m.database() as db:
                self.assertIsNone(db.execute('SELECT display_name FROM cards').fetchone()[0])
            state.update(info())
            me=c.get('/api/v1/profile/me',headers=h)
            c.patch('/api/v1/profile/me',headers={**h,'If-Match':me.headers['etag']},json={'display_name':'Маша'})
            self.assertEqual(c.get('/api/v1/profile/users',headers=h).json()['items'][0]['display_name'],'Маша')

    def test_card_removed_with_membership(self):
        """Удалённого и снова добавленного участника встречает новая анкета."""
        state=info()
        h=head()
        who={'profiles':['student'],'display_name':None,'member_since':'2026-09-01T10:00:00'}
        people={U:who}
        with tempfile.TemporaryDirectory() as folder, patch.object(m,'DB',folder+'/lifecycle.db'), \
             patch.object(m.core,'binding',return_value=BINDING), patch.object(m.core,'introspect',side_effect=lambda *a: state), \
             patch.object(m.core,'member',side_effect=lambda b,u: people.get(u) or False), \
             patch.object(m.core,'members',side_effect=lambda b: dict(people)), TestClient(m.app) as c:
            etag=c.get('/api/v1/profile/me',headers=h).headers['etag']
            c.patch('/api/v1/profile/me',headers={**h,'If-Match':etag},json={'display_name':'Старое имя','about':'Старое'})
            self.assertEqual(c.get('/api/v1/profile/me',headers=h).json()['about'],'Старое')
            # Снова добавлен: членство новое — анкета прежнего членства не показывается и удаляется.
            who['member_since']='2026-09-02T10:00:00'
            fresh=c.get('/api/v1/profile/me',headers=h).json()
            self.assertEqual((fresh['display_name'],fresh['about']),(None,''))
            with m.database() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM cards').fetchone()[0],0)
            # Удалён из вуза: список «Людей» стирает его анкету.
            etag=c.get('/api/v1/profile/me',headers=h).headers['etag']
            c.patch('/api/v1/profile/me',headers={**h,'If-Match':etag},json={'display_name':'Новое'})
            people.clear()
            self.assertEqual(c.get('/api/v1/profile/users',headers=h).json()['items'],[])
            with m.database() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM cards').fetchone()[0],0)

    def test_survives_restart(self):
        """Старт на существующем томе: схема создаётся повторно, анкеты остаются."""
        state=info()
        h=head()
        core_up=lambda: (patch.object(m.core,'binding',return_value=BINDING),
                         patch.object(m.core,'introspect',side_effect=lambda *a: state),
                         patch.object(m.core,'member',return_value=True))
        with tempfile.TemporaryDirectory() as folder, patch.object(m,'DB',folder+'/restart.db'):
            first,second,third=core_up()
            with first,second,third,TestClient(m.app) as c:
                etag=c.get('/api/v1/profile/me',headers=h).headers['etag']
                self.assertEqual(c.patch('/api/v1/profile/me',headers={**h,'If-Match':etag},
                                         json={'display_name':'Мария','about':'Кафедра 42'}).status_code,200)
                with m.database() as db:
                    self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0],'wal')
            # Выход из TestClient гасит lifespan; новый вход — это перезапуск контейнера.
            first,second,third=core_up()
            with first,second,third,TestClient(m.app) as c:
                saved=c.get('/api/v1/profile/me',headers=h).json()
                self.assertEqual((saved['display_name'],saved['about']),('Мария','Кафедра 42'))

    def test_service_view(self):
        state=info('teacher')
        h=head('teacher')
        with patch.object(m.core,'binding',return_value=BINDING), patch.object(m.core,'introspect',side_effect=lambda *a: state), \
             patch.object(m.core,'manifest',return_value=MANIFEST), TestClient(m.app) as c:
            self.assertEqual(c.get('/api/v1/service').status_code,401)
            view=c.get('/api/v1/service',headers=h).json()
            self.assertEqual((view['id'],view['institution_id'],view['service_type'],view['deployment']),(S,I,'user-profile','local'))
            self.assertEqual((view['display_name'],view['locale'],view['profile']),('Люди','ru','teacher'))
            self.assertEqual(view['api_base_url'],BINDING.api_base_url)
            # Сортировка (order,id); меню с required_permissions скрыто без права.
            self.assertEqual([x['id'] for x in view['menus']],['home','users'])
            en=c.get('/api/v1/service?locale=en',headers=h).json()
            self.assertEqual(en['display_name'],'People')
            self.assertEqual([(x['id'],x['display_name'],x['locale']) for x in en['menus']],
                             [('home','Главная','ru'),('users','Users','en')])
            state.update(info('admin',['profiles.manage']))
            admin=c.get('/api/v1/service',headers=head('admin')).json()
            self.assertEqual([x['id'] for x in admin['menus']],['home','users','staff'])
            self.assertEqual(admin['permissions'],['profiles.manage'])
            bad=c.get('/api/v1/service?locale=de',headers=head('admin'))
            self.assertEqual((bad.status_code,bad.json()['error']['code']),(422,'VALIDATION_ERROR'))

    def test_client_pages(self):
        with TestClient(m.app) as c:
            for path in ('/home', '/users'):
                page = c.get(path)
                self.assertEqual(page.status_code, 200)
                self.assertIn('frame-ancestors https://shell.test', page.headers['content-security-policy'])
                self.assertNotIn('__BOOT__', page.text)
            missing = c.get('/assets/nothing.js')
            self.assertEqual((missing.status_code, missing.json()['error']['code']), (404, 'RESOURCE_NOT_FOUND'))


if __name__=='__main__':unittest.main()
