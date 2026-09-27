'use strict';
const CORE = 'https://195.133.197.144';
let channel = null, timer = null, pending = null;
const status = document.getElementById('status');
const content = document.getElementById('content');
const refresh = document.getElementById('refresh');
function send(type, fields = {}) {
  window.parent.postMessage({ type, protocol_version: 1, channel_id: channel, ...fields }, CORE);
}
function requestRefresh() {
  if (!channel || pending) return;
  pending = crypto.randomUUID(); refresh.disabled = true;
  send('service.refresh_requested', { request_id: pending });
}
function arm(expires) {
  clearTimeout(timer);
  const remaining = Date.parse(expires) - Date.now();
  if (Number.isFinite(remaining)) timer = setTimeout(requestRefresh, Math.max(1000, remaining - 45000));
}
window.addEventListener('message', event => {
  const data = event.data;
  if (event.source !== window.parent || event.origin !== CORE || !data || data.protocol_version !== 1) return;
  if (data.type === 'platform.init' && typeof data.channel_id === 'string') {
    clearTimeout(timer); channel = data.channel_id; pending = null; content.hidden = true;
    send('service.ready'); return;
  }
  if (!channel || data.channel_id !== channel) return;
  if (data.type === 'platform.context') {
    content.hidden = false; refresh.disabled = false;
    status.textContent = 'Сервис открыт внутри ядра-клиента. Связь установлена.';
    document.getElementById('context').textContent = `Профиль: ${data.profile} · Вуз: ${data.institution_id}`;
    // Demo has no protected API: access tokens are neither stored nor displayed.
    arm(data.expires_at);
  } else if (data.type === 'platform.access' && pending && data.request_id === pending) {
    pending = null; refresh.disabled = false;
    status.textContent = 'Сессия успешно обновлена.'; arm(data.expires_at);
  } else if (data.type === 'platform.session_ended') {
    clearTimeout(timer); channel = null; pending = null; content.hidden = true;
    status.textContent = 'Сессия завершена. Откройте сервис заново.';
  }
});
refresh.addEventListener('click', requestRefresh);
