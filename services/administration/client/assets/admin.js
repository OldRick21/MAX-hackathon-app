'use strict';
(() => {
  // ------------------------------------------------------------------
  // Конфигурация и утилиты
  // ------------------------------------------------------------------
  const BOOT = JSON.parse(document.querySelector('meta[name="admin-boot"]').content);
  const API = BOOT.api_base_url + '/administration';
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const PROFILES = { student: 'Студент', teacher: 'Преподаватель', admin: 'Администратор' };
  const PERMISSION_NAMES = {
    'institution.read': 'Просмотр настроек вуза', 'institution.update': 'Изменение настроек вуза',
    'members.read': 'Просмотр участников', 'members.manage': 'Управление участниками',
    'services.read': 'Просмотр сервисов', 'services.manage': 'Управление сервисами',
    'roles.manage': 'Назначение ролей', 'credentials.manage': 'Ключи локальных сервисов',
    'schedule.read_all': 'Чтение всего расписания', 'schedule.write': 'Изменение занятий',
    'groups.manage': 'Управление группами', 'profiles.manage': 'Должности и степени в анкетах',
    'coursework.manage': 'Управление курсовыми',
  };
  const ACTIONS = {
    'institution.update': 'Изменены настройки вуза', 'institution.provision': 'Вуз подключён платформой',
    'institution.status': 'Изменён статус вуза', 'service.manifest.publish': 'Сервис опубликовал меню', 'institution.local_hosts': 'Изменены одобренные хосты',
    'owner.initial_assign': 'Назначен первый владелец', 'member.add': 'Добавлен участник',
    'member.remove': 'Удалён участник', 'member.profiles.replace': 'Изменены профили участника',
    'service.install': 'Установлен сервис', 'service.update': 'Изменён сервис', 'service.uninstall': 'Удалён сервис',
    'service.manifest.replace': 'Изменено меню сервиса', 'role.create': 'Создана роль', 'role.update': 'Изменена роль',
    'role.delete': 'Удалена роль', 'assignments.replace': 'Изменены роли участника',
    'credential.issue': 'Выдан ключ сервиса', 'credential.revoke': 'Отозван ключ сервиса',
  };
  const $ = (id) => document.getElementById(id);

  function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
      else if (key === 'class') el.className = value;
      else if (key in el && typeof value !== 'string') el[key] = value;
      else el.setAttribute(key, value === true ? '' : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }
  const fmtDate = (v) => v ? new Date(v).toLocaleString('ru-RU') : '—';
  const short = (id) => id ? `${id.slice(0, 8)}…` : '—';
  const title = (titles) => (titles && (titles.ru || titles.en)) || '';

  // ------------------------------------------------------------------
  // Протокол shell ↔ iframe (Service SDK §7)
  // ------------------------------------------------------------------
  const session = { channel: null, context: null, token: null, expiresAt: 0, refresh: null, timer: null,
    ended: false, view: null, userId: '' };

  function post(type, fields = {}) {
    if (!session.channel) return;
    window.parent.postMessage({ type, protocol_version: 1, channel_id: session.channel, ...fields }, BOOT.shell_origin);
  }

  function armRefresh() {
    clearTimeout(session.timer);
    const wait = session.expiresAt - Date.now() - 45000;
    session.timer = setTimeout(() => { requestAccess().catch(() => {}); }, Math.max(1000, wait));
  }

  function requestAccess() {
    if (session.refresh) return session.refresh.promise;
    const requestId = crypto.randomUUID();
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    const timeout = setTimeout(() => { session.refresh = null; reject(new Error('refresh timeout')); }, 20000);
    session.refresh = { id: requestId, promise, resolve: (v) => { clearTimeout(timeout); resolve(v); },
      reject: (e) => { clearTimeout(timeout); reject(e); } };
    post('service.refresh_requested', { request_id: requestId });
    return promise;
  }

  function decodeSub(token) {
    try { return JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))).sub || ''; }
    catch { return ''; }
  }

  function endSession(reason) {
    session.ended = true; session.token = null; session.channel = null;
    clearTimeout(session.timer);
    session.refresh?.reject(new Error('ended')); session.refresh = null;
    $('tabs').hidden = true; $('view').replaceChildren();
    $('status').textContent = {
      expired: 'Сессия истекла. Откройте сервис заново.', revoked: 'Сессия отозвана. Откройте сервис заново.',
      context_changed: 'Сервис закрыт.', unavailable: 'Сервис недоступен. Попробуйте позднее.',
    }[reason] || 'Сессия завершена. Откройте сервис заново.';
  }

  window.addEventListener('message', (event) => {
    const data = event.data;
    if (event.source !== window.parent || event.origin !== BOOT.shell_origin || !data ||
        typeof data !== 'object' || data.protocol_version !== 1) return;
    if (data.type === 'platform.init' && typeof data.channel_id === 'string' && UUID.test(data.channel_id)) {
      clearTimeout(session.timer);
      session.refresh?.reject(new Error('new channel'));
      Object.assign(session, { channel: data.channel_id, context: null, token: null, refresh: null, ended: false });
      post('service.ready');
      return;
    }
    if (!session.channel || data.channel_id !== session.channel) return;
    if (data.type === 'platform.context') {
      if (data.api_base_url !== BOOT.api_base_url || !UUID.test(data.institution_id) || !UUID.test(data.service_id) ||
          data.profile !== 'admin' || typeof data.access_token !== 'string') {
        endSession('unavailable'); return;
      }
      session.context = { institution_id: data.institution_id, service_id: data.service_id, locale: data.locale === 'en' ? 'en' : 'ru' };
      session.token = data.access_token; session.expiresAt = Date.parse(data.expires_at) || Date.now() + 60000;
      session.userId = decodeSub(data.access_token);
      armRefresh();
      start().catch(showError);
    } else if (data.type === 'platform.access' && session.refresh && data.request_id === session.refresh.id) {
      session.token = data.access_token; session.expiresAt = Date.parse(data.expires_at) || Date.now() + 60000;
      const pending = session.refresh; session.refresh = null; armRefresh(); pending.resolve();
    } else if (data.type === 'platform.session_ended') {
      endSession(data.reason);
    }
  });

  // ------------------------------------------------------------------
  // API
  // ------------------------------------------------------------------
  class ApiError extends Error {
    constructor(message, status = 0, code = '', requestId = '') { super(message); Object.assign(this, { status, code, requestId }); }
  }
  const ERROR_TEXT = {
    PRECONDITION_FAILED: 'Данные изменил кто-то другой. Список обновлён — повторите действие.',
    PRECONDITION_REQUIRED: 'Обновите данные и повторите действие.',
    SERVICE_UNAVAILABLE: 'Ядро платформы временно недоступно. Повторите позже.',
    INSTANCE_NOT_READY: 'Сервис администрирования для вуза ещё не подключён платформой.',
    RATE_LIMITED: 'Слишком много запросов. Подождите немного.',
  };

  async function api(path, { method = 'GET', body, etag, idempotencyKey, retried = false } = {}) {
    if (session.ended || !session.token) throw new ApiError('Сессия завершена.', 401);
    if (session.expiresAt - Date.now() < 20000) await requestAccess();
    const headers = { Accept: 'application/json', Authorization: `Bearer ${session.token}` };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (etag) headers['If-Match'] = etag;
    if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;
    let response;
    try {
      const url = path.startsWith('!') ? BOOT.api_base_url + path.slice(1) : API + path;
      response = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body),
        credentials: 'omit', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer' });
    } catch { throw new ApiError('Нет связи с сервисом. Проверьте соединение.'); }
    if (response.status === 401 && !retried) {
      await requestAccess();
      return api(path, { method, body, etag, idempotencyKey, retried: true });
    }
    const data = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) {
      const err = data?.error || {};
      if (response.status === 401) endSession('revoked');
      throw new ApiError(ERROR_TEXT[err.code] || err.message || 'Запрос не выполнен.', response.status, err.code || '',
        err.request_id || response.headers.get('X-Request-ID') || '');
    }
    return { data, etag: response.headers.get('ETag') };
  }

  async function listAll(path) {
    const items = []; let cursor = null; let guard = 0;
    do {
      const q = new URLSearchParams({ limit: '100', ...(cursor ? { cursor } : {}) });
      const { data } = await api(`${path}?${q}`);
      items.push(...data.items); cursor = data.next_cursor; guard += 1;
    } while (cursor && guard < 50);
    return items;
  }

  // ------------------------------------------------------------------
  // Каркас интерфейса
  // ------------------------------------------------------------------
  const can = (p) => session.view?.permissions.includes(p);
  const isOwner = () => session.view?.roles.includes('owner');

  function showError(error) {
    if (!(error instanceof ApiError)) { console.error(error); error = new ApiError('Не удалось выполнить действие.'); }
    const notice = $('notice');
    notice.replaceChildren(h('span', {}, error.message),
      error.requestId ? h('small', {}, ` Код обращения: ${error.requestId}`) : null);
    notice.hidden = false;
  }
  function clearError() { $('notice').hidden = true; $('notice').replaceChildren(); }
  function toast(text) { $('status').textContent = text; }

  async function guarded(fn, after) {
    clearError();
    try { await fn(); if (after) await after(); }
    catch (error) {
      showError(error);
      if (error.code === 'PRECONDITION_FAILED' && after) await after().catch(() => {});
    }
  }

  function confirmDialog(titleText, body, okText = 'Подтвердить') {
    return new Promise((resolve) => {
      const dialog = $('dialog');
      $('dialog-title').textContent = titleText;
      $('dialog-body').replaceChildren(...(Array.isArray(body) ? body : [body]));
      $('dialog-ok').textContent = okText;
      $('dialog-cancel').hidden = false;
      dialog.onclose = () => resolve(dialog.returnValue === 'ok');
      dialog.showModal();
    });
  }
  function infoDialog(titleText, body) {
    const dialog = $('dialog');
    $('dialog-title').textContent = titleText;
    $('dialog-body').replaceChildren(...(Array.isArray(body) ? body : [body]));
    $('dialog-ok').textContent = 'Готово'; $('dialog-cancel').hidden = true;
    dialog.onclose = () => { $('dialog-body').replaceChildren(); };
    dialog.showModal();
  }

  const TABS = [
    { id: 'institution', label: 'Вуз', allowed: () => can('institution.read'), render: renderInstitution },
    { id: 'members', label: 'Участники', allowed: () => can('members.read'), render: renderMembers },
    { id: 'services', label: 'Сервисы', allowed: () => can('services.read'), render: renderServices },
    { id: 'roles', label: 'Роли', allowed: () => can('services.read') || can('roles.manage'), render: renderRoles },
    { id: 'audit', label: 'Журнал', allowed: () => can('institution.read'), render: renderAudit },
  ];
  let activeTab = null;

  async function start() {
    clearError(); toast('Проверяем доступ…');
    const { data } = await api('!/service?locale=' + session.context.locale);
    if (data.id !== session.context.service_id || data.institution_id !== session.context.institution_id || data.profile !== 'admin') {
      endSession('unavailable'); return;
    }
    session.view = data;
    $('roles').textContent = data.roles.length
      ? 'Ваши роли: ' + data.roles.map(r => ({ owner: 'владелец', technical_admin: 'технический администратор', membership_admin: 'администратор участников' }[r] || r)).join(', ')
      : 'У вас нет ролей в администрировании.';
    const tabs = TABS.filter(t => t.allowed());
    if (!tabs.length) {
      $('tabs').hidden = true;
      $('view').replaceChildren(h('div', { class: 'empty' },
        h('h2', {}, 'Нет прав на управление'),
        h('p', {}, 'Профиль администратора сам по себе не даёт полномочий. Попросите владельца вуза назначить вам роль в сервисе «Администрирование».'),
        h('p', { class: 'muted' }, 'Ваш ID: ', h('code', {}, session.userId))));
      toast('Доступ ограничен.');
      return;
    }
    $('tabs').replaceChildren(...tabs.map(t => h('button', { type: 'button', class: 'tab', 'data-tab': t.id,
      onclick: () => openTab(t.id) }, t.label)));
    $('tabs').hidden = false;
    await openTab(tabs.some(t => t.id === activeTab) ? activeTab : tabs[0].id);
  }

  async function openTab(id) {
    const tab = TABS.find(t => t.id === id);
    if (!tab || !tab.allowed()) return;
    activeTab = id; clearError();
    for (const b of $('tabs').children) b.setAttribute('aria-current', b.dataset.tab === id ? 'page' : 'false');
    $('view').replaceChildren(h('p', { class: 'muted' }, 'Загрузка…'));
    try { await tab.render($('view')); toast(''); }
    catch (error) { $('view').replaceChildren(); showError(error); }
  }
  const reload = () => openTab(activeTab);

  function checkboxGroup(name, options, selected = [], disabled = false) {
    return h('div', { class: 'checks' }, options.map(([value, label]) =>
      h('label', { class: 'check' }, h('input', { type: 'checkbox', name, value, checked: selected.includes(value), disabled }), label)));
  }
  const checked = (root, name) => [...root.querySelectorAll(`input[name="${name}"]:checked`)].map(i => i.value);
  const field = (label, input, hint) => h('label', { class: 'field' }, h('span', {}, label), input, hint ? h('small', { class: 'muted' }, hint) : null);

  // ------------------------------------------------------------------
  // Вуз
  // ------------------------------------------------------------------
  async function renderInstitution(view) {
    const { data, etag } = await api('');
    const editable = can('institution.update');
    const ru = h('input', { value: data.titles.ru || '', maxlength: 200, required: true, disabled: !editable });
    const en = h('input', { value: data.titles.en || '', maxlength: 200, disabled: !editable });
    const locale = h('select', { disabled: !editable }, h('option', { value: 'ru' }, 'Русский'), h('option', { value: 'en' }, 'English'));
    locale.value = data.default_locale;
    const statusNames = { active: 'Активен', pending: 'Ожидает одобрения', suspended: 'Приостановлен' };
    view.replaceChildren(h('section', { class: 'card' },
      h('h2', {}, 'Настройки вуза'),
      h('p', { class: 'muted' }, 'Статус: ', h('strong', {}, statusNames[data.status] || data.status),
        '. Статус и подключение вуза меняет поддержка платформы.'),
      field('Название (рус.)', ru), field('Название (англ.)', en, 'Необязательно'),
      field('Язык по умолчанию', locale),
      editable ? h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => guarded(async () => {
        const titles = { ru: ru.value.trim() }; if (en.value.trim()) titles.en = en.value.trim();
        await api('', { method: 'PATCH', body: { titles, default_locale: locale.value }, etag });
        toast('Настройки сохранены.');
      }, reload) }, 'Сохранить')) : h('p', { class: 'muted' }, 'Для изменения нужна роль владельца.')));
  }

  // ------------------------------------------------------------------
  // Участники
  // ------------------------------------------------------------------
  async function renderMembers(view) {
    const members = await listAll('/members');
    const manage = can('members.manage');
    const profileOptions = Object.entries(PROFILES);
    const parts = [];
    if (manage) {
      const idInput = h('input', { placeholder: 'UUID пользователя', pattern: UUID.source, autocomplete: 'off' });
      const profiles = checkboxGroup('new-profiles', profileOptions, ['student']);
      const form = h('section', { class: 'card' }, h('h2', {}, 'Добавить участника'),
        h('p', { class: 'muted' }, 'Пользователь должен хотя бы раз открыть приложение: его ID показан на главном экране.'),
        field('ID пользователя', idInput), profiles,
        h('p', { class: 'hint' }, 'Профиль «Администратор» не даёт прав сам по себе: права выдаются ролями.'),
        h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => guarded(async () => {
          const userId = idInput.value.trim().toLowerCase();
          if (!UUID.test(userId)) throw new ApiError('Введите UUID пользователя.');
          const selected = checked(form, 'new-profiles');
          if (!selected.length) throw new ApiError('Выберите хотя бы один профиль.');
          await api('/members', { method: 'POST', body: { user_id: userId, profiles: selected } });
          toast('Участник добавлен.');
        }, reload) }, 'Добавить')));
      parts.push(form);
    }
    const rows = members.map((m) => {
      const row = h('div', { class: 'row' });
      const me = m.user_id === session.userId;
      const profiles = checkboxGroup(`p-${m.user_id}`, profileOptions, m.profiles, !manage);
      row.append(h('div', { class: 'row-main' },
        h('code', { title: m.user_id }, m.user_id), me ? h('span', { class: 'badge' }, 'вы') : null,
        h('small', { class: 'muted' }, ` с ${fmtDate(m.created_at)}`)), profiles);
      if (manage) {
        row.append(h('div', { class: 'actions' },
          h('button', { class: 'quiet', onclick: () => guarded(async () => {
            const selected = checked(row, `p-${m.user_id}`);
            if (!selected.length) throw new ApiError('Нужен хотя бы один профиль. Чтобы исключить человека, удалите участника.');
            const { etag } = await api(`/members/${m.user_id}`);
            await api(`/members/${m.user_id}/profiles`, { method: 'PUT', body: { profiles: selected }, etag });
            toast('Профили обновлены. Роли снятых профилей отозваны.');
          }, reload) }, 'Сохранить профили'),
          h('button', { class: 'danger', onclick: () => guarded(async () => {
            if (!await confirmDialog('Удалить участника?', h('p', {}, `Пользователь ${short(m.user_id)} потеряет доступ ко всем сервисам вуза, его роли будут сняты.`), 'Удалить')) return;
            const { etag } = await api(`/members/${m.user_id}`);
            await api(`/members/${m.user_id}`, { method: 'DELETE', etag });
            toast('Участник удалён.');
          }, reload) }, 'Удалить')));
      }
      return row;
    });
    parts.push(h('section', { class: 'card' }, h('h2', {}, `Участники (${members.length})`),
      rows.length ? rows : h('p', { class: 'muted' }, 'Участников пока нет.')));
    view.replaceChildren(...parts);
  }

  // ------------------------------------------------------------------
  // Сервисы
  // ------------------------------------------------------------------
  async function renderServices(view) {
    const [services, typesResp] = await Promise.all([listAll('/services'), api('/service-types')]);
    const types = Object.fromEntries(typesResp.data.items.map(t => [t.code, t]));
    const manage = can('services.manage');
    const parts = [];
    const installed = new Set(services.map(s => s.service_type));

    if (manage) {
      const available = ['schedule', 'user-profile', 'coursework'].filter(c => !installed.has(c));
      if (available.length) {
        const select = h('select', {}, available.map(c => h('option', { value: c }, `${title(types[c].titles)} (${types[c].deployment === 'cloud' ? 'облако' : 'локально'})`)));
        const api_ = h('input', { placeholder: 'https://coursework.university.ru/api/v1' });
        const client = h('input', { placeholder: 'https://coursework.university.ru' });
        const localFields = h('div', {}, h('p', { class: 'hint' },
          'Локальный сервис работает на сервере вуза. Его адрес должен быть на хосте, который одобрила поддержка платформы.'),
          field('Адрес API', api_), field('Адрес клиента (origin)', client));
        const sync = () => { localFields.hidden = types[select.value].deployment !== 'local'; };
        select.addEventListener('change', sync); sync();
        const key = crypto.randomUUID();
        parts.push(h('section', { class: 'card' }, h('h2', {}, 'Подключить сервис'), field('Тип', select), localFields,
          h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => guarded(async () => {
            const type = types[select.value];
            const body = type.deployment === 'cloud' ? { service_type: type.code, deployment: 'cloud' }
              : { service_type: type.code, deployment: 'local', api_base_url: api_.value.trim(), client_base_url: client.value.trim() };
            await api('/services', { method: 'POST', body, idempotencyKey: key });
            toast(type.deployment === 'cloud' ? 'Сервис подключён и включён.'
              : 'Сервис зарегистрирован. Дальше — шаги в его карточке: ключ, запуск на сервере вуза, включение.');
          }, reload) }, 'Подключить'))));
      }
    }

    for (const s of services) {
      const type = types[s.service_type];
      const card = h('section', { class: 'card' });
      const badges = [h('span', { class: `badge ${s.enabled ? 'ok' : ''}` }, s.enabled ? 'включён' : 'выключен'),
        h('span', { class: 'badge' }, s.deployment === 'cloud' ? 'облако' : 'локальный'),
        s.protected ? h('span', { class: 'badge lock' }, 'защищён') : null];
      card.append(h('div', { class: 'row-main' }, h('h2', {}, title(s.manifest.titles) || title(type?.titles)), ...badges),
        h('dl', { class: 'meta' },
          h('dt', {}, 'Тип'), h('dd', {}, s.service_type),
          h('dt', {}, 'API'), h('dd', {}, h('code', {}, s.api_base_url)),
          h('dt', {}, 'Клиент'), h('dd', {}, h('code', {}, s.client_base_url)),
          h('dt', {}, 'Меню'), h('dd', {}, s.manifest.menus.length
            ? s.manifest.menus.map(m => `${title(m.titles)} (${m.entrypoint_path}; ${m.profiles.map(p => PROFILES[p]).join(', ')})`).join('; ')
            : 'не опубликовано')));
      if (s.protected) {
        card.append(h('p', { class: 'muted' }, 'Сервис администрирования предоставляется вузу по умолчанию: его нельзя отключить, перенастроить или удалить.'));
      } else if (manage) {
        const actions = h('div', { class: 'actions' });
        const waiting = s.deployment === 'local' && !s.enabled && !s.manifest.menus.length;
        actions.append(h('button', { class: s.enabled ? 'quiet' : 'primary', disabled: waiting,
          title: waiting ? 'Сначала сервис должен опубликовать меню' : '', onclick: () => guarded(async () => {
          const { etag } = await api(`/services/${s.id}`);
          await api(`/services/${s.id}`, { method: 'PATCH', body: { enabled: !s.enabled }, etag });
          toast(s.enabled ? 'Сервис выключен, его сессии завершены.' : 'Сервис включён.');
        }, reload) }, s.enabled ? 'Выключить' : 'Включить'));
        if (s.deployment === 'local') {
          actions.append(h('button', { class: 'quiet', onclick: () => editUrls(s) }, 'Адреса'),
            h('button', { class: 'quiet', onclick: () => editManifest(s) }, 'Меню вручную (JSON)'));
        }
        actions.append(h('button', { class: 'danger', onclick: () => guarded(async () => {
          if (!await confirmDialog('Удалить сервис?', h('p', {}, 'Экземпляр перестанет выдаваться, ключи и сессии будут отозваны, назначения ролей удалены. Данные сервиса удалением записи не уничтожаются.'), 'Удалить')) return;
          const { etag } = await api(`/services/${s.id}`);
          await api(`/services/${s.id}`, { method: 'DELETE', etag });
          toast('Сервис удалён.');
        }, reload) }, 'Удалить'));
        card.append(actions);
      }
      if (s.deployment === 'local') card.insertBefore(await localSetup(s), card.querySelector('.actions'));
      parts.push(card);
    }
    view.replaceChildren(...parts);
  }

  async function editUrls(s) {
    const apiInput = h('input', { value: s.api_base_url }); const clientInput = h('input', { value: s.client_base_url });
    if (!await confirmDialog('Адреса локального сервиса', [field('Адрес API', apiInput), field('Адрес клиента (origin)', clientInput),
      h('p', { class: 'hint' }, 'После смены адреса сервис выключается, сессии завершаются. Включите его снова вручную.')], 'Сохранить')) return;
    await guarded(async () => {
      const { etag } = await api(`/services/${s.id}`);
      await api(`/services/${s.id}`, { method: 'PATCH', etag,
        body: { api_base_url: apiInput.value.trim(), client_base_url: clientInput.value.trim() } });
      toast('Адреса обновлены, сервис выключен.');
    }, reload);
  }

  async function editManifest(s) {
    const area = h('textarea', { rows: 14, spellcheck: false });
    area.value = JSON.stringify(s.manifest, null, 2);
    if (!await confirmDialog('Меню сервиса', [area, h('p', { class: 'hint' },
      'Обычно manifest публикует backend вуза. Пути — абсолютные внутри адреса клиента; профили и permissions — из словаря типа.')], 'Сохранить')) return;
    await guarded(async () => {
      let manifest;
      try { manifest = JSON.parse(area.value); } catch { throw new ApiError('Manifest — некорректный JSON.'); }
      const { etag } = await api(`/services/${s.id}`);
      await api(`/services/${s.id}/manifest`, { method: 'PUT', body: manifest, etag });
      toast('Меню сохранено.');
    }, reload);
  }

  // Подключение локального сервиса по docs/services/coursework/SPEC.md §2: ключ → запуск на
  // сервере вуза (сервис сам создаёт роль и публикует меню) → включение администратором.
  async function localSetup(s) {
    const creds = can('credentials.manage') ? await listAll(`/services/${s.id}/credentials`) : [];
    const hasKey = creds.some(c => !c.revoked_at);
    const published = s.manifest.menus.length > 0;
    const step = (done, titleText, text, ...extra) => h('div', { class: 'row' },
      h('div', { class: 'row-main' }, h('span', { class: `badge ${done ? 'ok' : ''}` }, done ? 'готово' : 'ждёт'), h('strong', {}, titleText)),
      h('small', { class: 'muted' }, text), ...extra);
    const box = h('div', { class: 'sub' }, h('h3', {}, 'Подключение'),
      step(true, '1. Адреса', `Сервис зарегистрирован на ${s.client_base_url}.`),
      step(hasKey, '2. Ключ доступа', hasKey
        ? 'Ключ выдан. Если он потерян — выдайте новый и отзовите старый ниже.'
        : 'Выдайте ключ и передайте готовые настройки администратору сервера вуза.'),
      step(published, '3. Запуск сервиса', published
        ? `Сервис связался с платформой и опубликовал меню: ${s.manifest.menus.map(m => title(m.titles)).join(', ')}.`
        : 'Запустите сервис на сервере вуза с выданными настройками — он сам создаст свою роль и опубликует меню.',
        published ? null : h('div', { class: 'actions' }, h('button', { class: 'quiet', onclick: reload }, 'Проверить'))),
      step(s.enabled, '4. Включение', s.enabled ? 'Сервис доступен пользователям.' : 'Нажмите «Включить» — сервис появится у пользователей.'));
    if (can('credentials.manage')) box.append(credentialsBlock(s, creds));
    return box;
  }

  function envBlock(s, data) {
    const prefix = s.service_type.toUpperCase().replace(/[^A-Z0-9]+/g, '_');
    return [
      `CORE_URL=${BOOT.shell_origin}`,
      `SHELL_ORIGIN=${BOOT.shell_origin}`,
      `${prefix}_CLIENT_ID=${data.credential.client_id}`,
      `${prefix}_CLIENT_SECRET=${data.client_secret}`,
      `${prefix}_API_BASE_URL=${s.api_base_url}`,
      `${prefix}_CLIENT_BASE_URL=${s.client_base_url}`,
    ].join('\n');
  }

  function credentialsBlock(s, creds) {
    const box = h('div', { class: 'sub' }, h('h3', {}, 'Ключи доступа сервиса к ядру'));
    const active = creds.filter(c => !c.revoked_at);
    box.append(h('p', { class: 'muted' }, 'Секрет показывается один раз. Храните его только в серверных секретах сервиса. Допускается два активных ключа для ротации.'));
    for (const c of creds) {
      box.append(h('div', { class: 'row' }, h('div', { class: 'row-main' }, h('code', {}, c.client_id),
        h('small', { class: 'muted' }, ` выдан ${fmtDate(c.created_at)}`),
        c.revoked_at ? h('span', { class: 'badge' }, `отозван ${fmtDate(c.revoked_at)}`) : h('span', { class: 'badge ok' }, 'активен')),
      c.revoked_at ? null : h('button', { class: 'danger', onclick: () => guarded(async () => {
        if (!await confirmDialog('Отозвать ключ?', h('p', {}, 'Сервис с этим ключом сразу потеряет доступ к ядру.'), 'Отозвать')) return;
        await api(`/services/${s.id}/credentials/${c.id}`, { method: 'DELETE' });
        toast('Ключ отозван.');
      }, reload) }, 'Отозвать')));
    }
    if (active.length < 2) {
      box.append(h('button', { class: 'quiet', onclick: () => guarded(async () => {
        const { data } = await api(`/services/${s.id}/credentials`, { method: 'POST' });
        const env = envBlock(s, data);
        const area = h('textarea', { rows: 7, readonly: true, spellcheck: false });
        area.value = env;
        infoDialog('Настройки для сервера вуза', [
          h('p', {}, 'Передайте эти строки администратору сервера: их нужно добавить в файл .env сервиса и перезапустить его.'),
          area,
          h('p', { class: 'hint' }, 'Секрет показывается один раз и не хранится в интерфейсе. Не пересылайте его в открытых чатах.'),
          h('button', { type: 'button', class: 'quiet', onclick: () => navigator.clipboard?.writeText(env).then(() => toast('Скопировано.')) }, 'Скопировать')]);
      }, reload) }, 'Выдать ключ'));
    }
    return box;
  }

  // ------------------------------------------------------------------
  // Роли и назначения
  // ------------------------------------------------------------------
  let rolesServiceId = null;

  async function renderRoles(view) {
    const [services, typesResp] = await Promise.all([listAll('/services'), api('/service-types')]);
    const types = Object.fromEntries(typesResp.data.items.map(t => [t.code, t]));
    if (!services.some(s => s.id === rolesServiceId)) rolesServiceId = services[0]?.id;
    const select = h('select', { onchange: () => { rolesServiceId = select.value; reload(); } },
      services.map(s => h('option', { value: s.id }, title(s.manifest.titles) || s.service_type)));
    select.value = rolesServiceId;
    const service = services.find(s => s.id === rolesServiceId);
    const parts = [h('section', { class: 'card' }, field('Сервис', select),
      h('p', { class: 'muted' }, 'Профиль — статус человека в вузе. Роль — его полномочия внутри выбранного сервиса.'))];
    if (!service) { view.replaceChildren(...parts); return; }
    const type = types[service.service_type];
    const roles = await listAll(`/services/${service.id}/roles`);
    const isAdminService = service.service_type === 'administration';

    // Определения ролей
    const defs = h('section', { class: 'card' }, h('h2', {}, 'Роли сервиса'));
    for (const r of roles) {
      const row = h('div', { class: 'row' }, h('div', { class: 'row-main' }, h('strong', {}, title(r.titles)),
        h('code', {}, r.code), r.system ? h('span', { class: 'badge lock' }, 'системная') : null),
      h('small', { class: 'muted' }, `Профили: ${r.allowed_profiles.map(p => PROFILES[p]).join(', ')}. Права: ${r.permissions.map(p => PERMISSION_NAMES[p] || p).join(', ') || 'нет'}`));
      if (!r.system && !isAdminService && can('roles.manage')) {
        row.append(h('div', { class: 'actions' },
          h('button', { class: 'quiet', onclick: () => roleDialog(service, type, r) }, 'Изменить'),
          h('button', { class: 'danger', onclick: () => guarded(async () => {
            if (!await confirmDialog('Удалить роль?', h('p', {}, 'Роль можно удалить, только если она ни у кого не назначена.'), 'Удалить')) return;
            const { etag } = await api(`/services/${service.id}/roles/${r.code}`);
            await api(`/services/${service.id}/roles/${r.code}`, { method: 'DELETE', etag });
            toast('Роль удалена.');
          }, reload) }, 'Удалить')));
      }
      defs.append(row);
    }
    if (!roles.length) defs.append(h('p', { class: 'muted' }, 'Ролей нет.'));
    if (isAdminService) defs.append(h('p', { class: 'hint' }, 'Роли администрирования системные. Назначать их может только владелец вуза.'));
    else if (can('roles.manage')) defs.append(h('div', { class: 'actions' }, h('button', { class: 'quiet', onclick: () => roleDialog(service, type, null) }, 'Создать роль')));
    parts.push(defs);

    // Назначения
    if (can('roles.manage') && can('members.read')) {
      const members = await listAll('/members');
      const eligible = members.filter(m => m.profiles.some(p => service.supported_profiles.includes(p)));
      const box = h('section', { class: 'card' }, h('h2', {}, 'Назначение ролей'));
      if (isAdminService && !isOwner()) box.append(h('p', { class: 'hint' }, 'Только владелец может менять роли администрирования; вам доступен просмотр.'));
      const memberSelect = h('select', {}, h('option', { value: '' }, 'Выберите участника'),
        eligible.map(m => h('option', { value: m.user_id }, `${m.user_id}${m.user_id === session.userId ? ' (вы)' : ''}`)));
      const profileSelect = h('select', { disabled: true });
      const target = h('div', {});
      memberSelect.addEventListener('change', () => {
        const m = eligible.find(x => x.user_id === memberSelect.value);
        profileSelect.replaceChildren(...(m ? m.profiles.filter(p => service.supported_profiles.includes(p)).map(p => h('option', { value: p }, PROFILES[p])) : []));
        profileSelect.disabled = !m; target.replaceChildren();
        if (m) loadAssignment();
      });
      profileSelect.addEventListener('change', () => loadAssignment());
      async function loadAssignment() {
        await guarded(async () => {
          const path = `/services/${service.id}/users/${memberSelect.value}/profiles/${profileSelect.value}/roles`;
          const { data, etag } = await api(path);
          const options = roles.filter(r => r.allowed_profiles.includes(profileSelect.value)).map(r => [r.code, title(r.titles)]);
          const readOnly = isAdminService && !isOwner();
          const group = checkboxGroup('assign', options, data.roles, readOnly);
          target.replaceChildren(options.length ? group : h('p', { class: 'muted' }, 'Для этого профиля ролей нет.'),
            h('p', { class: 'muted' }, `Итоговые права: ${data.permissions.map(p => PERMISSION_NAMES[p] || p).join(', ') || 'нет'}`),
            readOnly || !options.length ? null : h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => guarded(async () => {
              await api(path, { method: 'PUT', body: { roles: checked(group, 'assign') }, etag });
              toast('Роли обновлены. Они действуют со следующего запроса пользователя.');
            }, loadAssignment) }, 'Сохранить роли')));
        });
      }
      box.append(field('Участник', memberSelect), field('Профиль', profileSelect), target);
      parts.push(box);
    }
    view.replaceChildren(...parts);
  }

  async function roleDialog(service, type, role) {
    const code = h('input', { value: role?.code || '', disabled: !!role, placeholder: 'например, schedule_viewer' });
    const ru = h('input', { value: role?.titles.ru || '' }); const en = h('input', { value: role?.titles.en || '' });
    const profiles = checkboxGroup('role-profiles', service.supported_profiles.map(p => [p, PROFILES[p]]), role?.allowed_profiles || []);
    const perms = checkboxGroup('role-perms', type.permission_codes.map(p => [p, PERMISSION_NAMES[p] || p]), role?.permissions || []);
    const body = [field('Код', code, 'Латиница в нижнем регистре, цифры, _ . -'), field('Название (рус.)', ru), field('Название (англ.)', en),
      h('p', {}, 'Профили, которым можно назначить роль:'), profiles, h('p', {}, 'Права:'), perms];
    if (!await confirmDialog(role ? 'Изменить роль' : 'Новая роль', body, 'Сохранить')) return;
    await guarded(async () => {
      const titles = { ru: ru.value.trim() }; if (en.value.trim()) titles.en = en.value.trim();
      const data = { titles, allowed_profiles: checked(profiles, 'role-profiles'), permissions: checked(perms, 'role-perms') };
      if (role) {
        const { etag } = await api(`/services/${service.id}/roles/${role.code}`);
        await api(`/services/${service.id}/roles/${role.code}`, { method: 'PATCH', body: data, etag });
      } else {
        await api(`/services/${service.id}/roles`, { method: 'POST', body: { code: code.value.trim(), ...data } });
      }
      toast('Роль сохранена.');
    }, reload);
  }

  // ------------------------------------------------------------------
  // Журнал
  // ------------------------------------------------------------------
  async function renderAudit(view) {
    const list = h('div', {});
    let cursor = null;
    const more = h('button', { class: 'quiet', onclick: () => guarded(load) }, 'Показать ещё');
    async function load() {
      const q = new URLSearchParams({ limit: '50', ...(cursor ? { cursor } : {}) });
      const { data } = await api(`/audit?${q}`);
      for (const e of data.items) {
        list.append(h('div', { class: `row ${e.outcome === 'denied' ? 'denied' : ''}` },
          h('div', { class: 'row-main' }, h('strong', {}, ACTIONS[e.action] || e.action),
            e.outcome === 'denied' ? h('span', { class: 'badge' }, `отклонено: ${e.error_code}`) : null),
          h('small', { class: 'muted' }, `${fmtDate(e.created_at)} · ${e.actor_kind === 'platform_support' ? 'поддержка платформы' : e.actor_kind === 'operator' ? 'оператор' : e.actor_kind === 'system' ? 'сервис' : 'администратор'} ${short(e.actor_user_id)}${e.target_id ? ' · объект ' + e.target_id : ''}`),
          Object.keys(e.details || {}).length ? h('details', {}, h('summary', {}, 'Подробности'), h('pre', {}, JSON.stringify(e.details, null, 2))) : null));
      }
      cursor = data.next_cursor; more.hidden = !cursor;
      if (!list.children.length) list.append(h('p', { class: 'muted' }, 'Записей пока нет.'));
    }
    view.replaceChildren(h('section', { class: 'card' }, h('h2', {}, 'Журнал административных изменений'),
      h('p', { class: 'muted' }, 'Записываются изменения и отклонённые попытки. Токены и секреты не сохраняются.'), list, more));
    await load();
  }
})();
