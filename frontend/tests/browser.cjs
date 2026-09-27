/* Browser acceptance against HTTP fixtures shaped like the core contract.
   No real MAX credentials, bot or production API are used. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const uid = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const iid = '11111111-1111-4111-8111-111111111111';
const iid2 = '22222222-2222-4222-8222-222222222222';
const sid = '33333333-3333-4333-8333-333333333333';
const csid = '44444444-4444-4444-8444-444444444444';
const ssid = '55555555-5555-4555-8555-555555555555';
const corePair = { token_type: 'Bearer', session_id: csid, access_token: 'core-access', refresh_token: 'core-refresh', expires_in: 600, refresh_expires_in: 6000 };
const childHTML = `<!doctype html><html><body><h1>Клиент расписания</h1><p id="state">Ожидание</p><button id="renew">Обновить дважды</button><script>
let channel; window.received=[];
const send=(type,extra={})=>parent.postMessage({type,protocol_version:1,channel_id:channel,...extra},'https://core.test');
addEventListener('message',e=>{
 if(e.origin!=='https://core.test'||e.source!==parent||e.data.protocol_version!==1)return;
 if(e.data.type==='platform.init'){channel=e.data.channel_id; send('service.ready');}
 if(e.data.channel_id!==channel)return;
 window.received.push(e.data);
 if(e.data.type==='platform.context')document.getElementById('state').textContent='Контекст получен: '+e.data.profile;
 if(e.data.type==='platform.access')document.getElementById('state').textContent='Обновлено';
});
document.getElementById('renew').onclick=()=>{send('service.refresh_requested',{request_id:crypto.randomUUID()});send('service.refresh_requested',{request_id:crypto.randomUUID()});};
</script></body></html>`;

async function setup(browser, options = {}) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const stats = { creates: 0, revokes: 0, refreshes: 0, logout: 0, calls: [], frameRequests: [] };
  const errors = [];
  await context.addInitScript(({ noMax, emptyInitData }) => {
    if (!noMax) window.WebApp = { initData: emptyInitData ? '' : 'SIGNED_MAX_FIXTURE', initDataUnsafe: { user: { id: 123 } } };
  }, options);
  await context.route('https://st.max.ru/**', route => route.fulfill({ contentType: 'application/javascript', body: '' }));
  await context.route('https://service.test/**', route => {
    stats.frameRequests.push(route.request().url());
    return route.fulfill({ contentType: 'text/html', body: childHTML });
  });
  await context.route('https://core.test/**', async route => {
    const req = route.request(); const url = new URL(req.url()); const p = url.pathname;
    if (p.startsWith('/api/')) {
      stats.calls.push({ path: p, method: req.method() });
      const body = req.postDataJSON();
      const reply = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: status === 204 ? '' : JSON.stringify(data) });
      const institution = { id: iid, display_name: 'Тестовый университет', locale: 'ru', default_locale: 'ru', status: options.pending ? 'pending' : 'active', profiles: options.singleProfile ? ['student'] : ['student', 'teacher'] };
      const service = { id: sid, institution_id: iid, service_type: 'schedule', deployment: 'cloud', display_name: 'Расписание', locale: 'ru', api_base_url: 'https://service.test/api/v1', client_base_url: options.sameOrigin ? 'https://core.test' : 'https://service.test', profile: url.searchParams.get('profile') || 'student', roles: [], permissions: [], menus: [{ id: 'schedule', display_name: 'Моё расписание', locale: 'ru', entrypoint_path: '/schedule', order: 0 }] };
      if (p === '/api/v1/auth/token') {
        assert.deepEqual(body, { initData: 'SIGNED_MAX_FIXTURE' }); return reply(corePair);
      }
      if (p === '/api/v1/auth/logout') { stats.logout++; return reply(null, 204); }
      if (p === '/api/v1/auth/me') return reply({ id: uid, max_user_id: '123', created_at: '2026-09-27T00:00:00Z' });
      if (p === '/api/v1/institution') {
        if (options.empty) return reply({ items: [], next_cursor: null });
        if (options.multiple && !url.searchParams.has('cursor')) return reply({ items: [institution], next_cursor: 'page2' });
        return reply({ items: options.multiple ? [{ ...institution, id: iid2, display_name: 'Второй вуз' }] : [institution], next_cursor: null });
      }
      if (p === `/api/v1/institution/${iid}`) return reply(institution);
      if (p.endsWith('/profiles')) return reply({ user_id: uid, institution_id: iid, profiles: institution.profiles });
      if (p.endsWith('/service')) return reply({ items: options.noServices ? [] : [service], next_cursor: null });
      if (p.endsWith(`/service/${sid}`)) return reply(service);
      if (p.endsWith('/session') && req.method() === 'POST') {
        stats.creates++;
        if (options.delayed) await new Promise(r => setTimeout(r, 250));
        stats.profile = body.profile;
        return reply({ ...corePair, access_token: 'service-access', refresh_token: 'service-refresh', session_id: ssid, parent_session_id: csid, institution_id: iid, service_id: sid, profile: body.profile, expires_in: 300 }, 201);
      }
      if (p.endsWith('/session/refresh')) {
        stats.refreshes++;
        await new Promise(r => setTimeout(r, 80));
        if (options.refreshFailure) return route.abort('failed');
        return reply({ ...corePair, access_token: 'service-access-new', refresh_token: 'service-refresh-new', session_id: ssid, parent_session_id: csid, institution_id: iid, service_id: sid, profile: stats.profile, expires_in: 300 });
      }
      if (req.method() === 'DELETE') { stats.revokes++; return reply(null, 204); }
      return reply({ error: { code: 'RESOURCE_NOT_FOUND', message: 'Missing mock' } }, 404);
    }
    const file = p.startsWith('/static/') ? path.join(root, p) : path.join(root, 'templates/index.html');
    const body = await fs.readFile(file);
    const type = p.endsWith('.js') ? 'application/javascript' : p.endsWith('.css') ? 'text/css' : 'text/html';
    await route.fulfill({ contentType: type, body });
  });
  const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', e => errors.push(String(e)));
  await page.goto('https://core.test/institution');
  return { context, page, stats, errors };
}

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}), args: ['--no-sandbox'] });
  try {
    // Real cross-origin iframe handshake, user profile selection, refresh coalescing and disposal.
    let { context, page, stats, errors } = await setup(browser, { multiple: true });
    await page.getByRole('button', { name: /Тестовый университет/ }).click();
    await page.getByRole('button', { name: /Студент.*Доступные сервисы/ }).waitFor();
    assert.equal(stats.creates, 0);
    await page.selectOption('#profile', 'student');
    if (process.env.SCREENSHOT_DIR) {
      await fs.mkdir(process.env.SCREENSHOT_DIR, {recursive:true});
      await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR,'core-mobile.png'),fullPage:true});
      await page.setViewportSize({width:1360,height:900});
      await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR,'core-desktop.png'),fullPage:true});
      await page.setViewportSize({width:390,height:844});
    }
    await page.locator('#services button').click();
    await page.getByText('Сервис открыт', { exact: true }).waitFor();
    const child = page.frames().find(f => f.url().startsWith('https://service.test'));
    const messages = await child.evaluate(() => window.received);
    const receivedContext = messages.find(m => m.type === 'platform.context');
    assert.equal(receivedContext.profile, 'student');
    assert.equal(receivedContext.access_token, 'service-access');
    assert.ok(!JSON.stringify(messages).includes('refresh_token'));
    assert.ok(!JSON.stringify(messages).includes('core-access'));
    assert.deepEqual(stats.frameRequests, ['https://service.test/schedule']);
    // Correct channel but wrong source/origin: no token or refresh is released.
    await page.evaluate(channel => window.postMessage({ type: 'service.refresh_requested', protocol_version: 1, channel_id: channel, request_id: crypto.randomUUID() }, '*'), receivedContext.channel_id);
    await child.locator('#renew').click();
    await page.waitForFunction(() => document.querySelector('iframe') !== null);
    await child.waitForFunction(() => window.received.filter(m => m.type === 'platform.access').length === 2);
    assert.equal(stats.refreshes, 1);
    const oldChannel = receivedContext.channel_id;
    await child.evaluate(() => location.reload());
    await child.waitForFunction(() => window.received.some(m => m.type === 'platform.context'));
    assert.notEqual(await child.evaluate(() => window.received.find(m => m.type === 'platform.context').channel_id), oldChannel);
    await page.selectOption('#profile', 'teacher');
    await page.locator('#services button').waitFor();
    assert.equal(await page.locator('iframe').count(), 0);
    await page.locator('#services button').click();
    await page.getByText('Сервис открыт', { exact: true }).waitFor();
    assert.equal(stats.profile, 'teacher');
    assert.ok(stats.revokes >= 1);
    assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
    await page.getByRole('button', { name: 'Выйти', exact: true }).click();
    await page.getByText('Вы вышли.', { exact: false }).waitFor();
    assert.equal(stats.logout, 1); assert.equal(await page.locator('iframe').count(), 0);
    assert.deepEqual(errors, []);
    await context.close();
    console.log('PASS: paginated institutions, explicit profiles, cross-origin handshake, refresh, reload, switch, logout, memory-only tokens');

    for (const scenario of [{ noMax: true }, { emptyInitData: true }, { empty: true }, { pending: true }, { noServices: true, singleProfile: true }, { sameOrigin: true, singleProfile: true }]) {
      ({ context, page, stats, errors } = await setup(browser, scenario));
      if (scenario.noMax) { await page.getByText('Не удалось получить данные MAX', { exact: false }).waitFor(); assert.equal(stats.calls.length, 0); }
      if (scenario.emptyInitData) { await page.getByText('В браузере данные', { exact: false }).waitFor(); assert.equal(stats.calls.length, 0); }
      if (scenario.empty) await page.getByText('Вы пока не добавлены', { exact: false }).waitFor();
      if (scenario.pending) await page.getByText('Вуз ожидает одобрения', { exact: false }).waitFor();
      if (scenario.noServices) await page.getByText('Для этого профиля пока нет', { exact: false }).waitFor();
      if (scenario.sameOrigin) { await page.locator('#services button').click(); await page.getByRole('alert').filter({ hasText: 'отдельный защищённый адрес' }).waitFor(); assert.equal(stats.creates, 0); }
      assert.equal(await page.locator('iframe').count(), 0); assert.deepEqual(errors, []);
      await context.close();
    }
    console.log('PASS: no MAX, empty membership, pending university, empty service list, same-origin rejection');

    ({ context, page, stats, errors } = await setup(browser, { refreshFailure: true, singleProfile: true }));
    await page.locator('#services button').click(); await page.getByText('Сервис открыт', { exact: true }).waitFor();
    await page.frameLocator('iframe').locator('#renew').click();
    await page.getByRole('alert').waitFor();
    assert.equal(stats.refreshes, 1); assert.equal(await page.locator('iframe').count(), 0);
    assert.deepEqual(errors, []); await context.close();
    console.log('PASS: lost service refresh response closes iframe and is not retried');

    ({ context, page, stats, errors } = await setup(browser, { delayed: true }));
    await page.selectOption('#profile', 'student'); await page.locator('#services button').click();
    await page.waitForTimeout(70);
    await page.selectOption('#profile', 'teacher');
    await page.waitForTimeout(400);
    assert.equal(await page.locator('iframe').count(), 0);
    assert.ok(stats.revokes >= 1); assert.deepEqual(errors, []); await context.close();
    console.log('PASS: late service session after profile switch is revoked, not displayed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
