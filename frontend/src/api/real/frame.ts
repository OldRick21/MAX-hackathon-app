// Клиент сервиса в iframe по протоколу SDK v1 (docs/services/sdk/SPEC.md, раздел 7).
// Перенесено из MAX-hackathon-app/frontend/static/js/core/service-frame.js.

import { ApiError, isUUID, request } from '../http';
import type { ServiceTokenPair, ServiceView } from '../types';
import type { FrameCallbacks, ServiceFrameHandle } from '../backend';
import type { CoreSession } from './core';
import { checkedPair, sessionPath } from './serviceSession';

type Pair = ReturnType<typeof checkedPair>;

export function clientAddress(service: ServiceView, entrypoint: string, coreOrigin = location.origin): URL {
  let base: URL, api: URL;
  try { base = new URL(service.client_base_url); api = new URL(service.api_base_url); }
  catch { throw new ApiError('Адрес сервиса указан с ошибкой. Сообщите администратору вуза.'); }
  if (base.protocol !== 'https:' || base.origin === coreOrigin || base.username || base.password ||
      base.pathname !== '/' || base.search || base.hash || api.protocol !== 'https:' ||
      api.username || api.password || api.search || api.hash) {
    throw new ApiError('Адрес сервиса указан с ошибкой. Сообщите администратору вуза.');
  }
  if (!entrypoint.startsWith('/') || entrypoint.startsWith('//') || /[\\%?#\s\u0000-\u001f]/.test(entrypoint) ||
      entrypoint.split('/').some(p => p === '.' || p === '..')) {
    throw new ApiError('Адрес сервиса указан с ошибкой. Сообщите администратору вуза.');
  }
  const url = new URL(entrypoint, base.origin);
  if (url.origin !== base.origin) throw new ApiError('Адрес сервиса указан с ошибкой. Сообщите администратору вуза.');
  return url;
}

export async function openServiceFrame(
  core: CoreSession, service: ServiceView, menuId: string, locale: string, host: HTMLElement, cb: FrameCallbacks,
): Promise<ServiceFrameHandle> {
  const menu = service.menus.find(m => m.id === menuId);
  if (!menu) throw new ApiError('Этот раздел больше недоступен для текущего профиля.', 403);
  const url = clientAddress(service, menu.entrypoint_path);
  const parent = core.pair?.session_id;
  let pair: Pair | null = checkedPair(
    await core.call<ServiceTokenPair>(sessionPath(service), { method: 'POST', body: { profile: service.profile } }),
    service, parent,
  );

  let closed = false;
  let channel: string | null = null;
  let ready = false;
  let refreshing: Promise<void> | null = null;
  const requestIds = new Set<string>();
  let handshakeTimer = 0;
  let expiryTimer = 0;

  const frame = document.createElement('iframe');
  frame.title = `${service.display_name} — ${menu.display_name}`;
  frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-downloads');
  frame.referrerPolicy = 'no-referrer';
  frame.src = url.href;
  frame.style.cssText = 'width:100%;height:100%;border:0;display:block';

  const send = (type: string, fields: Record<string, unknown> = {}) => {
    frame.contentWindow?.postMessage({ type, protocol_version: 1, channel_id: channel, ...fields }, url.origin);
  };

  const revoke = (p: Pair | null) => {
    if (!p || !core.pair || p.parent_session_id !== core.pair.session_id) return;
    void core.call(`${sessionPath(service)}/${p.session_id}`, { method: 'DELETE' }).catch(() => {});
  };

  const close = (reason = 'context_changed') => {
    if (closed) return;
    closed = true;
    send('platform.session_ended', { reason });
    clearTimeout(handshakeTimer); clearTimeout(expiryTimer);
    window.removeEventListener('message', receive);
    frame.remove();
    revoke(pair);
    pair = null;
    cb.onStatus('closed');
  };

  const fail = (error: unknown, reason = 'unavailable') => {
    if (closed) return;
    close(reason);
    cb.onError(error);
  };

  const armExpiry = () => {
    clearTimeout(expiryTimer);
    expiryTimer = window.setTimeout(
      () => fail(new ApiError('Сессия сервиса истекла. Откройте его заново.'), 'expired'),
      Math.max(0, pair!.expiresAt - Date.now()),
    );
  };

  const rotate = () => {
    if (refreshing) return refreshing;
    const previous = pair!;
    const token = previous.refresh_token;
    previous.refresh_token = null;
    refreshing = (async () => {
      try {
        const { data } = await request<ServiceTokenPair>(`${sessionPath(service)}/refresh`, {
          method: 'POST', body: { refresh_token: token },
        });
        const next = checkedPair(data, service, previous.parent_session_id, previous);
        if (closed) { revoke(next); return; }
        pair = next;
        armExpiry();
      } catch (e) {
        fail(e, e instanceof ApiError && e.status === 401 ? 'revoked' : 'unavailable');
        throw e;
      } finally { refreshing = null; }
    })();
    return refreshing;
  };

  const handshakeTimeout = () => fail(new ApiError('Сервис не ответил. Попробуйте открыть его позже.'));

  function receive(event: MessageEvent) {
    const data = event.data;
    if (closed || !channel || event.origin !== url.origin || event.source !== frame.contentWindow ||
        !data || typeof data !== 'object' || data.protocol_version !== 1 || data.channel_id !== channel) return;
    if (data.type === 'service.ready' && !ready) {
      ready = true;
      clearTimeout(handshakeTimer);
      const ch = channel;
      void (async () => {
        if (refreshing || pair!.expiresAt - Date.now() < 30000) await rotate();
        if (closed || channel !== ch) return;
        send('platform.context', {
          institution_id: service.institution_id, service_id: service.id, profile: service.profile,
          api_base_url: service.api_base_url, locale,
          access_token: pair!.access_token, expires_at: new Date(pair!.expiresAt).toISOString(),
        });
        cb.onStatus('ready');
      })().catch(() => {});
    } else if (data.type === 'service.refresh_requested' && ready && isUUID(data.request_id)) {
      if (requestIds.has(data.request_id)) return;
      if (requestIds.size >= 1000) { fail(new ApiError('Сервис работает с ошибками. Откройте его заново.')); return; }
      requestIds.add(data.request_id);
      const ch = channel;
      void rotate().then(() => {
        if (closed || channel !== ch) return;
        send('platform.access', {
          request_id: data.request_id, access_token: pair!.access_token,
          expires_at: new Date(pair!.expiresAt).toISOString(),
        });
      }).catch(() => {});
    }
  }

  window.addEventListener('message', receive);
  frame.addEventListener('load', () => {
    if (closed) return;
    channel = crypto.randomUUID();
    ready = false;
    requestIds.clear();
    clearTimeout(handshakeTimer);
    handshakeTimer = window.setTimeout(handshakeTimeout, 15000);
    cb.onStatus('loading');
    send('platform.init');
  });
  handshakeTimer = window.setTimeout(handshakeTimeout, 15000);
  host.replaceChildren(frame);
  armExpiry();
  cb.onStatus('loading');
  return { close };
}
