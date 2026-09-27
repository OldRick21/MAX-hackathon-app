import { ApiError, isUUID, request, tokenPair } from './api.js?v=9ff99b1c478b';

export function clientAddress(service, menu, coreOrigin = location.origin) {
  let base, api;
  try { base = new URL(service.client_base_url); api = new URL(service.api_base_url); }
  catch { throw new ApiError('У сервиса некорректный адрес.'); }
  if (base.protocol !== 'https:' || base.origin === coreOrigin || base.username || base.password ||
      base.pathname !== '/' || base.search || base.hash || api.protocol !== 'https:' ||
      api.username || api.password || api.search || api.hash) {
    throw new ApiError('Клиент сервиса должен иметь отдельный защищённый адрес.');
  }
  const path = menu.entrypoint_path;
  if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//') ||
      /[\\%?#\s\u0000-\u001f]/.test(path) || path.split('/').some(p => p === '.' || p === '..')) {
    throw new ApiError('Недопустимый путь страницы сервиса.');
  }
  const url = new URL(path, base.origin);
  if (url.origin !== base.origin) throw new ApiError('Недопустимый адрес страницы.');
  return url;
}

export class ServiceFrame {
  current = null;
  epoch = 0;
  constructor(core, host, onStatus, onError) {
    this.core = core; this.host = host; this.onStatus = onStatus; this.onError = onError;
    window.addEventListener('message', (event) => this.receive(event));
  }

  path(service) { return `/api/v1/institution/${service.institution_id}/service/${service.id}/session`; }

  checkedPair(data, service, parent, previous) {
    const pair = tokenPair(data);
    if (pair.institution_id !== service.institution_id || pair.service_id !== service.id ||
        pair.profile !== service.profile || pair.parent_session_id !== parent ||
        (previous && pair.session_id !== previous.session_id)) {
      throw new ApiError('Сервер вернул сессию другого профиля или сервиса.');
    }
    return pair;
  }

  send(state, type, fields = {}) {
    state.frame.contentWindow?.postMessage({ type, protocol_version: 1, channel_id: state.channel, ...fields }, state.origin);
  }

  revoke(service, pair) {
    if (!pair || !this.core.pair || pair.parent_session_id !== this.core.pair.session_id) return;
    // При переключении UI не ждёт сеть; ошибка отзыва не оставляет старый iframe.
    void this.core.call(`${this.path(service)}/${pair.session_id}`, { method: 'DELETE' }).catch(() => {});
  }

  close(reason = 'context_changed') {
    this.epoch += 1;
    const state = this.current;
    this.current = null;
    if (!state) return;
    this.send(state, 'platform.session_ended', { reason });
    clearTimeout(state.handshakeTimer); clearTimeout(state.expiryTimer);
    state.frame.remove();
    this.revoke(state.service, state.pair);
    state.pair = null;
    this.onStatus('closed');
  }

  fail(state, error, reason = 'unavailable') {
    if (this.current !== state) return;
    this.close(reason);
    this.onError(error);
  }

  async open(service, menu, locale) {
    this.close();
    const epoch = this.epoch;
    const url = clientAddress(service, menu);
    const parent = this.core.pair?.session_id;
    const pair = this.checkedPair(await this.core.call(this.path(service), {
      method: 'POST', body: { profile: service.profile },
    }), service, parent);
    if (epoch !== this.epoch) { this.revoke(service, pair); return; }
    const frame = document.createElement('iframe');
    frame.title = `${service.display_name} — ${menu.display_name}`;
    frame.sandbox = 'allow-scripts allow-same-origin allow-forms allow-downloads';
    frame.referrerPolicy = 'no-referrer';
    frame.src = url.href;
    const state = { service, pair, frame, locale, origin: url.origin, channel: null,
      ready: false, refresh: null, requestIds: new Set(), expiryTimer: null, handshakeTimer: null };
    this.current = state;
    const handshakeTimeout = () => this.fail(state, new ApiError('Сервис не ответил. Попробуйте открыть его позднее.'));
    state.handshakeTimer = setTimeout(handshakeTimeout, 15000);
    frame.addEventListener('load', () => {
      if (this.current !== state) return;
      state.channel = crypto.randomUUID();
      state.ready = false;
      state.requestIds.clear();
      clearTimeout(state.handshakeTimer);
      state.handshakeTimer = setTimeout(handshakeTimeout, 15000);
      this.onStatus('loading');
      this.send(state, 'platform.init');
    });
    this.host.replaceChildren(frame);
    this.armExpiry(state);
    this.onStatus('loading');
  }

  armExpiry(state) {
    clearTimeout(state.expiryTimer);
    state.expiryTimer = setTimeout(() => {
      this.fail(state, new ApiError('Сессия сервиса истекла. Откройте сервис заново.'), 'expired');
    }, Math.max(0, state.pair.expiresAt - Date.now()));
  }

  async rotate(state) {
    if (state.refresh) return state.refresh;
    const previous = state.pair;
    const refreshToken = previous.refresh_token;
    previous.refresh_token = null;
    state.refresh = (async () => {
      try {
        const data = await request(`${this.path(state.service)}/refresh`, {
          method: 'POST', body: { refresh_token: refreshToken },
        });
        const pair = this.checkedPair(data, state.service, previous.parent_session_id, previous);
        if (this.current !== state) { this.revoke(state.service, pair); return; }
        state.pair = pair;
        this.armExpiry(state);
      } catch (error) {
        this.fail(state, error, error.status === 401 ? 'revoked' : 'unavailable');
        throw error;
      } finally { state.refresh = null; }
    })();
    return state.refresh;
  }

  receive(event) {
    const state = this.current;
    const data = event.data;
    if (!state || !state.channel || event.origin !== state.origin || event.source !== state.frame.contentWindow ||
        !data || typeof data !== 'object' || data.protocol_version !== 1 || data.channel_id !== state.channel) return;
    if (data.type === 'service.ready' && !state.ready) {
      state.ready = true;
      clearTimeout(state.handshakeTimer);
      const channel = state.channel;
      void (async () => {
        if (state.refresh || state.pair.expiresAt - Date.now() < 30000) await this.rotate(state);
        if (this.current !== state || state.channel !== channel) return;
        this.send(state, 'platform.context', {
          institution_id: state.service.institution_id, service_id: state.service.id, profile: state.service.profile,
          api_base_url: state.service.api_base_url, locale: state.locale,
          access_token: state.pair.access_token, expires_at: new Date(state.pair.expiresAt).toISOString(),
        });
        this.onStatus('ready');
      })().catch(() => {});
    } else if (data.type === 'service.refresh_requested' && state.ready && isUUID(data.request_id)) {
      if (state.requestIds.has(data.request_id)) return;
      if (state.requestIds.size >= 1000) { this.fail(state, new ApiError('Клиент сервиса отправляет слишком много запросов.')); return; }
      state.requestIds.add(data.request_id);
      const channel = state.channel;
      void this.rotate(state).then(() => {
        if (this.current !== state || state.channel !== channel) return;
        this.send(state, 'platform.access', { request_id: data.request_id, access_token: state.pair.access_token,
          expires_at: new Date(state.pair.expiresAt).toISOString() });
      }).catch(() => {});
    }
  }
}
