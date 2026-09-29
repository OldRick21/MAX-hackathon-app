const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const iid='11111111-1111-4111-8111-111111111111', sid='33333333-3333-4333-8333-333333333333';
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try {
 for(const width of [1440,390]) {
  const context=await browser.newContext({viewport:{width,height:900},colorScheme:width===390?'dark':'light'});
  const errors=[];
  await context.route('https://service.test/**', r=>r.fulfill({contentType:'text/html; charset=utf-8',body:`<script>addEventListener('message',e=>{if(e.origin!=='https://core.test')return;const d=e.data;if(d.type==='platform.init')parent.postMessage({...d,type:'service.ready'},e.origin);if(d.type==='platform.context')document.body.textContent='Сервис готов';});</script>`}));
  await context.addInitScript(()=>{window.WebApp={initData:'fixture',initDataUnsafe:{user:{first_name:'Андрей'}},ready(){}}});
  await context.route('https://core.test/**',async route=>{
   const p=new URL(route.request().url()).pathname;
   let data;
   if(p==='/api/v1/auth/token') data={token_type:'Bearer',session_id:iid,access_token:'test',refresh_token:'test-refresh',expires_in:600};
   else if(p==='/api/v1/auth/me') data={id:iid,max_user_id:'123'};
   else if(p==='/api/v1/platform/me') data={roles:['platform_support']};
   else if(p==='/api/v1/institution-applications' || p.startsWith('/api/v1/platform/applications')) data={items:[],next_cursor:null};
   else if(p==='/api/v1/join/institutions' || p==='/api/v1/join-requests') data={items:[],next_cursor:null};
   else if(p==='/api/v1/institution') data={items:[{id:iid,display_name:'Тестовый университет',status:'active',profiles:['student']}],next_cursor:null};
   else if(p===`/api/v1/institution/${iid}`) data={id:iid,display_name:'Тестовый университет',status:'active',profiles:['student']};
   else if(p.endsWith('/session') && route.request().method()==='POST') data={token_type:'Bearer',session_id:sid,parent_session_id:iid,institution_id:iid,service_id:sid,profile:'student',access_token:'service-access',refresh_token:'service-refresh',expires_in:600};
   else if(p.includes('/session/')) return route.fulfill({status:204});
   else if(p.endsWith('/profiles')) data={profiles:['student']};
   else if(p.endsWith('/widgets')) data={items:[],next_cursor:null};
   else if(p==='/api/v1/privacy/me') data={version:'demo-v1',accepted_at:'2026-09-29T10:00:00Z',text:'Согласие на обработку персональных данных для проверки интерфейса.'};
   else if(p.endsWith('/service')) data={items:[{id:sid,institution_id:iid,service_type:'schedule',client_base_url:'https://service.test',api_base_url:'https://service.test/api/v1',display_name:'Расписание — демо',profile:'student',menus:[{id:'home',display_name:'Расписание — демо',entrypoint_path:'/',order:0}]}],next_cursor:null};
   else if(p.startsWith('/api/')) throw Error('Unexpected API '+p);
   if(data) return route.fulfill({json:data});
   const file=p.startsWith('/assets/')?p.slice(1):'index.html';
   const ext=path.extname(file);
   await route.fulfill({body:await fs.readFile(path.join(__dirname,'../dist',file)),contentType:({'.js':'application/javascript','.css':'text/css','.html':'text/html','.woff2':'font/woff2','.png':'image/png'})[ext]||'application/octet-stream'});
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto('https://core.test/');
  try { await page.getByRole('heading',{name:'Привет, Андрей!'}).waitFor(); }
  catch(e){ console.error('Visible UI:',await page.locator('body').innerText());console.error('Page errors:',errors);throw e; }
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'horizontal overflow');
  await page.getByRole('button',{name:/Сменить вуз или оформление/}).click();
  await page.getByText('+ Вступить в другой вуз',{exact:true}).click();
  await page.getByRole('heading',{name:'Вступить в вуз'}).waitFor();
  await page.getByRole('link',{name:'Подать заявку на подключение вуза'}).click();
  await page.getByText('Новая заявка',{exact:true}).waitFor();
  await page.goto(`https://core.test/institution/${iid}/services`);
  await page.getByRole('link',{name:/Расписание — демо/}).last().click();
  await page.frameLocator('iframe').getByText('Сервис готов',{exact:true}).waitFor();
  await page.goto(`https://core.test/institution/${iid}`);
  await page.getByRole('heading',{name:'Привет, Андрей!'}).waitFor();
  const privacy=page.getByRole('button',{name:/Персональные данные/});
  await privacy.waitFor();
  assert.notEqual(await privacy.evaluate(el=>getComputedStyle(el).position),'fixed','privacy control must stay in page flow');
  if(width===390){
   const box=await privacy.boundingBox();
   const nav=page.getByRole('navigation',{name:'Разделы'}).last();
   const navBox=await nav.boundingBox();
   assert(box && navBox && box.y+box.height<=navBox.y,'privacy control overlaps mobile navigation');
  }
  await page.screenshot({path:`/tmp/vuzy-${width}.png`,fullPage:true});
  await privacy.click();
  await page.getByRole('dialog',{name:'Персональные данные'}).waitFor();
  await page.waitForTimeout(500);
  await page.screenshot({path:`/tmp/vuzy-privacy-${width}.png`,fullPage:true});
  await page.getByRole('button',{name:'Закрыть'}).click();
  assert.deepEqual(errors,[]);
  await context.close();
 }
 for(const width of [1440,390]) {
  const context=await browser.newContext({viewport:{width,height:900},colorScheme:width===390?'dark':'light'});
  const errors=[];
  await context.addInitScript(()=>{window.WebApp={initData:'fixture',initDataUnsafe:{user:{first_name:'Андрей'}},ready(){}}});
  await context.route('https://core.test/**',async route=>{
   const p=new URL(route.request().url()).pathname;
   if(p==='/api/v1/auth/token') return route.fulfill({status:403,json:{error:{code:'CONSENT_REQUIRED',message:'Consent required'}}});
   if(p==='/api/v1/privacy/document') return route.fulfill({json:{version:'demo-v1',demo:true,text:'Я подтверждаю согласие на обработку персональных данных для работы сервисов платформы.\n\nПеречень данных, цели обработки и срок хранения указаны в настоящем документе.',policy:'Политика описывает порядок обработки, хранения и удаления персональных данных пользователя.'}});
   const file=p.startsWith('/assets/')?p.slice(1):'index.html';
   const ext=path.extname(file);
   await route.fulfill({body:await fs.readFile(path.join(__dirname,'../dist',file)),contentType:({'.js':'application/javascript','.css':'text/css','.html':'text/html','.woff2':'font/woff2','.png':'image/png'})[ext]||'application/octet-stream'});
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto('https://core.test/');
  await page.getByRole('heading',{name:'Персональные данные'}).waitFor();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'consent horizontal overflow');
  await page.screenshot({path:`/tmp/vuzy-consent-${width}.png`,fullPage:true});
  assert.deepEqual(errors,[]);
  await context.close();
 }
 const c=await browser.newContext();await c.route('**/*',async r=>r.fulfill({body:await fs.readFile(path.join(__dirname,'../dist',new URL(r.request().url()).pathname.startsWith('/assets/')?new URL(r.request().url()).pathname.slice(1):'index.html')),contentType:r.request().url().endsWith('.js')?'application/javascript':r.request().url().endsWith('.css')?'text/css':'text/html'}));
 const p=await c.newPage();await p.goto('https://core.test/');await p.getByRole('heading',{name:/Откройте приложение в MAX/}).waitFor({timeout:15000});await c.close();
 console.log('PASS: desktop/mobile, light/dark, privacy layout, consent screen, applications, demo catalog, browser stub');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
