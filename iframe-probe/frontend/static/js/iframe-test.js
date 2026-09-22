'use strict';
const records = new Map();
let services = [];
let sequence = 0;
const $ = (id) => document.getElementById(id);
const counters = () => {
  const rows = [...records.values()];
  $('totals').textContent = `Клиентов: ${rows.length}; iframe: ${rows.filter(r => r.frame).length}; связь подтверждена: ${rows.filter(r => r.state === 'ready').length}`;
};
function setStatus(record, state, message) {
  record.state = state;
  record.status.textContent = message;
  counters();
}
function unload(record) {
  clearTimeout(record.timer);
  record.frame?.remove();
  record.frame = null;
  record.channel = null;
  record.ms = null;
  record.actions = 0;
  record.open.textContent = 'Открыть';
  setStatus(record, 'idle', 'Не загружен');
}
function open(record) {
  if (record.frame) { unload(record); return; }
  record.channel = crypto.randomUUID();
  const url = new URL(record.service.url, location.origin);
  if (url.origin !== location.origin || !url.pathname.startsWith('/api/demo/clients/')) {
    setStatus(record, 'error', 'Недопустимый адрес клиента'); return;
  }
  url.hash = new URLSearchParams({channel: record.channel}).toString();
  const frame = document.createElement('iframe');
  // Без allow-same-origin: клиент не получает доступ к DOM и хранилищу ядра.
  frame.setAttribute('sandbox', 'allow-scripts');
  frame.referrerPolicy = 'no-referrer';
  frame.title = record.service.title;
  frame.src = url.href;
  record.frame = frame;
  record.started = performance.now();
  setStatus(record, 'loading', 'Загрузка и проверка связи…');
  record.open.textContent = 'Выгрузить';
  record.timer = setTimeout(() => {
    setStatus(record, 'timeout', 'Нет ответа за 15 секунд. Проверь сеть, консоль и ограничения iframe.');
  }, 15000);
  record.card.append(frame);
}
function add() {
  const id = ++sequence;
  const service = services[(id - 1) % services.length];
  const card = document.createElement('article');
  const title = document.createElement('h2');
  title.textContent = `${service.title} · экземпляр ${id}`;
  const status = document.createElement('p');
  const controls = document.createElement('div');
  controls.className = 'controls';
  const launch = document.createElement('button');
  const remove = document.createElement('button');
  remove.textContent = 'Удалить';
  controls.append(launch, remove);
  card.append(title, status, controls);
  const record = {id, service, card, status, open: launch, frame: null};
  records.set(id, record);
  launch.addEventListener('click', () => open(record));
  remove.addEventListener('click', () => {
    unload(record); records.delete(id); card.remove(); counters();
  });
  $('clients').append(card);
  unload(record);
}
window.addEventListener('message', (event) => {
  // sandbox без allow-same-origin даёт origin "null"; дополнительно проверяем
  // конкретное окно iframe и случайный идентификатор его текущего запуска.
  if (event.origin !== 'null' || !event.data || typeof event.data !== 'object') return;
  const record = [...records.values()].find(r => r.frame?.contentWindow === event.source);
  if (!record || event.data.channel !== record.channel) return;
  if (event.data.type === 'service:ready') {
    // Для opaque origin нужен '*'. Передаём только тестовый ping, без токенов.
    record.frame.contentWindow.postMessage({type: 'core:ping', channel: record.channel}, '*');
  } else if (event.data.type === 'service:pong') {
    clearTimeout(record.timer);
    record.ms = Math.round(performance.now() - record.started);
    setStatus(record, 'ready', `Связь в обе стороны работает · ${record.ms} мс`);
  } else if (event.data.type === 'service:action' && record.state === 'ready') {
    record.actions += 1;
    record.status.textContent = `Связь работает · действий из клиента: ${record.actions}`;
  }
});
$('add').addEventListener('click', add);
$('add-five').addEventListener('click', () => { for (let i = 0; i < 5; i++) add(); });
$('clear').addEventListener('click', () => {
  for (const record of records.values()) { unload(record); record.card.remove(); }
  records.clear(); counters();
});
$('report').addEventListener('click', () => {
  $('result').hidden = false;
  $('result').value = JSON.stringify({
    testedAt: new Date().toISOString(), userAgent: navigator.userAgent,
    note: 'Среду MAX и её версию укажите вручную. Это не замер памяти.',
    clients: [...records.values()].map(r => ({id: r.id, service: r.service.id, state: r.state, handshakeMs: r.ms, actions: r.actions})),
  }, null, 2);
});
async function start() {
  try {
    const response = await fetch('/api/demo/services');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!Array.isArray(data.services) || !data.services.length ||
        !data.services.every(s => typeof s.id === 'string' && typeof s.title === 'string' && typeof s.url === 'string')) {
      throw new Error('Некорректный каталог');
    }
    services = data.services;
    $('catalog-status').textContent = `Каталог получен: ${services.length} типа клиентов. HTML загружается только после «Открыть».`;
    $('add').disabled = $('add-five').disabled = false;
    add();
  } catch (error) {
    $('catalog-status').textContent = `Не удалось получить каталог: ${error.message}`;
  }
}
counters();
start();
