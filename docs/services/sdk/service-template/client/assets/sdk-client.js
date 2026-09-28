// Клиентская часть Service SDK: протокол оболочка ↔ iframe (docs/services/sdk/SPEC.md §7).
// Копируйте без изменений. Использование в app.js:
//   const ctx = await ServiceSDK.ready;            // { institution_id, service_id, profile, locale, user_id }
//   const { data, etag } = await ServiceSDK.api('/whoami');
//   ServiceSDK.onEnd(reason => ...);               // оболочка закрыла сессию
// Токен живёт только в памяти: не кладите его в URL, localStorage и журналы.
(() => {
  'use strict';
  const BOOT = JSON.parse(document.querySelector('meta[name="service-boot"]').content);
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const session = { channel: null, token: null, expiresAt: 0, refresh: null, timer: null, ended: false };
  const endHandlers = [];
  let resolveReady;
  const ready = new Promise(r => { resolveReady = r; });

  const post = (type, fields = {}) => {
    if (session.channel) window.parent.postMessage({ type, protocol_version: 1, channel_id: session.channel, ...fields }, BOOT.shell_origin);
  };

  const armRefresh = () => {
    clearTimeout(session.timer);
    session.timer = setTimeout(() => requestAccess().catch(() => {}), Math.max(1000, session.expiresAt - Date.now() - 45000));
  };

  // Новый access просим у оболочки; одновременные просьбы объединяются, refresh сервис не видит.
  function requestAccess() {
    if (session.refresh) return session.refresh.promise;
    const id = crypto.randomUUID();
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    const timeout = setTimeout(() => { session.refresh = null; reject(new Error('refresh timeout')); }, 20000);
    session.refresh = { id, promise, resolve: v => { clearTimeout(timeout); resolve(v); }, reject: e => { clearTimeout(timeout); reject(e); } };
    post('service.refresh_requested', { request_id: id });
    return promise;
  }

  const subject = token => {
    try { return JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))).sub || ''; } catch { return ''; }
  };

  function end(reason) {
    session.ended = true; session.token = null; session.channel = null;
    clearTimeout(session.timer);
    session.refresh?.reject(new Error('ended')); session.refresh = null;
    endHandlers.forEach(h => h(reason));
  }

  window.addEventListener('message', event => {
    const data = event.data;
    // Принимаем только сообщения родителя с разрешённого origin и текущего канала.
    if (event.source !== window.parent || event.origin !== BOOT.shell_origin || !data || typeof data !== 'object' ||
        data.protocol_version !== 1) return;
    if (data.type === 'platform.init' && UUID.test(data.channel_id || '')) {
      clearTimeout(session.timer);
      session.refresh?.reject(new Error('new channel'));
      Object.assign(session, { channel: data.channel_id, token: null, refresh: null, ended: false });
      post('service.ready');
      return;
    }
    if (!session.channel || data.channel_id !== session.channel) return;
    if (data.type === 'platform.context') {
      // Bearer отправляем только на свой настроенный API, а не на адрес из сообщения.
      if (data.api_base_url !== BOOT.api_base_url || !UUID.test(data.institution_id) || !UUID.test(data.service_id) ||
          typeof data.access_token !== 'string') { end('unavailable'); return; }
      session.token = data.access_token;
      session.expiresAt = Date.parse(data.expires_at) || Date.now() + 60000;
      armRefresh();
      resolveReady({ institution_id: data.institution_id, service_id: data.service_id, profile: data.profile,
        locale: data.locale === 'en' ? 'en' : 'ru', user_id: subject(data.access_token) });
    } else if (data.type === 'platform.access' && session.refresh && data.request_id === session.refresh.id) {
      session.token = data.access_token;
      session.expiresAt = Date.parse(data.expires_at) || Date.now() + 60000;
      const pending = session.refresh; session.refresh = null; armRefresh(); pending.resolve();
    } else if (data.type === 'platform.session_ended') {
      end(data.reason);
    }
  });

  class ApiError extends Error {
    constructor(message, status = 0, code = '') { super(message); Object.assign(this, { status, code }); }
  }

  // Запрос к своему API: путь без /api/v1 (например '/whoami'). На 401 — один новый access и повтор.
  async function api(path, { method = 'GET', body, etag, headers = {} } = {}) {
    await ready;
    for (let attempt = 0; attempt < 2; attempt++) {
      if (session.ended || !session.token) throw new ApiError('Сессия завершена. Откройте сервис заново.', 401);
      const res = await fetch(BOOT.api_base_url + path, {
        method, credentials: 'omit', cache: 'no-store', redirect: 'error',
        headers: { Accept: 'application/json', Authorization: `Bearer ${session.token}`,
          ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(etag ? { 'If-Match': etag } : {}), ...headers },
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
      if (res.status === 401 && attempt === 0) { await requestAccess(); continue; }
      const data = res.status === 204 ? null : await res.json().catch(() => null);
      if (!res.ok) throw new ApiError(data?.error?.message || 'Не удалось выполнить запрос', res.status, data?.error?.code || '');
      return { data, etag: res.headers.get('ETag') };
    }
    throw new ApiError('Сессия завершена. Откройте сервис заново.', 401);
  }

  window.ServiceSDK = Object.freeze({ ready, api, ApiError, onEnd: h => endHandlers.push(h) });
})();
