'use strict';
// Пульт оператора платформы. Маршруты — в адресе после #: #/overview, #/institutions/<id>/members и т. д.
(() => {
  const PROFILES = { student: 'Студент', teacher: 'Преподаватель', admin: 'Администратор' };
  const APP_STATUS = { pending: ['На рассмотрении', 'warn'], approved: ['Одобрена', 'ok'], rejected: ['Отклонена', 'danger'], withdrawn: ['Отозвана', ''] };
  const INST_STATUS = { active: ['Активен', 'ok'], suspended: ['Приостановлен', 'danger'], pending: ['Ожидает', 'warn'] };
  const CLOUD = { 'user-profile': 'Люди (анкеты)', schedule: 'Расписание' };
  const PERMISSION_NAMES = {
    'institution.read': 'Просмотр настроек вуза', 'institution.update': 'Изменение настроек вуза',
    'members.read': 'Просмотр участников', 'members.manage': 'Управление участниками', 'groups.manage': 'Управление группами',
    'services.read': 'Просмотр сервисов', 'services.manage': 'Управление сервисами', 'roles.manage': 'Назначение ролей',
    'credentials.manage': 'Ключи локальных сервисов', 'schedule.read_all': 'Чтение всего расписания',
    'schedule.write': 'Изменение занятий', 'schedule.groups': 'Ведение учебных групп', 'profiles.manage': 'Должности и степени в анкетах', 'coursework.manage': 'Управление курсовыми',
  };
  const ACTIONS = {
    'application.submit': 'Подана заявка на подключение вуза', 'application.approve': 'Заявка вуза одобрена',
    'application.reject': 'Заявка вуза отклонена', 'application.withdraw': 'Заявка вуза отозвана',
    'institution.provision': 'Вуз подключён', 'institution.status': 'Изменён статус вуза', 'institution.update': 'Изменены настройки вуза',
    'institution.local_hosts': 'Изменены одобренные хосты', 'institution.initial_owner': 'Назначен владелец',
    'owner.initial_assign': 'Назначен владелец', 'staff.grant': 'Выданы права поддержки', 'staff.revoke': 'Отозваны права поддержки', 'user.delete': 'Удалён пользователь',
    'member.add': 'Добавлен участник', 'member.remove': 'Удалён участник', 'member.profiles.replace': 'Изменены профили',
    'member.group.set': 'Изменена группа студента', 'group.create': 'Создана группа', 'group.rename': 'Группа переименована',
    'group.delete': 'Группа удалена', 'group.members.replace': 'Изменён состав группы',
    'join_request.submit': 'Заявка на вступление', 'join_request.approve': 'Вступление одобрено', 'join_request.reject': 'Вступление отклонено',
    'service.install': 'Установлен сервис', 'service.update': 'Изменён сервис', 'service.uninstall': 'Удалён сервис',
    'service.manifest.replace': 'Изменено меню сервиса', 'service.manifest.publish': 'Сервис опубликовал меню',
    'role.create': 'Создана роль', 'role.update': 'Изменена роль', 'role.delete': 'Удалена роль',
    'assignments.replace': 'Изменены роли участника', 'credential.issue': 'Выдан ключ сервиса', 'credential.revoke': 'Отозван ключ сервиса',
  };
  const ACTOR = { operator: 'оператор', platform_support: 'поддержка', admin: 'администратор вуза', user: 'пользователь', system: 'сервис' };
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
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }
  const fmtDate = (v) => v ? new Date(v).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) : '—';
  const short = (id) => id ? `${id.slice(0, 8)}…` : '—';
  const title = (t) => (t && (t.ru || t.en)) || '';
  const badge = (text, tone = '') => h('span', { class: `badge ${tone}` }, text);
  const field = (label, input, hint) => h('label', { class: 'field' }, h('span', {}, label), input, hint ? h('small', { class: 'muted' }, hint) : null);
  const checks = (name, options, selected = []) => h('div', { class: 'checks' }, options.map(([v, l]) =>
    h('label', { class: 'check' }, h('input', { type: 'checkbox', name, value: v, checked: selected.includes(v) }), l)));
  const checked = (root, name) => [...root.querySelectorAll(`input[name="${name}"]:checked`)].map(i => i.value);
  const userLabel = (u) => u ? `${u.name || 'Без имени'} · ${short(u.id)}` : '—';

  // ------------------------------------------------------------------
  // API
  // ------------------------------------------------------------------
  class ApiError extends Error { constructor(message, status = 0, code = '') { super(message); this.status = status; this.code = code; } }
  const ERROR_TEXT = {
    PRECONDITION_FAILED: 'Данные кто-то изменил. Экран обновлён — повторите действие.',
    PRECONDITION_REQUIRED: 'Обновите данные и повторите действие.',
  };

  async function api(path, { method = 'GET', body, etag, idem } = {}) {
    const headers = { Accept: 'application/json' };
    if (method !== 'GET') headers['X-Operator'] = '1';
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (etag) headers['If-Match'] = etag;
    if (idem) headers['Idempotency-Key'] = crypto.randomUUID();
    let response;
    try {
      response = await fetch('/api' + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body),
        credentials: 'same-origin', cache: 'no-store' });
    } catch { throw new ApiError('Нет связи с сервером.'); }
    const data = response.status === 204 ? null : await response.json().catch(() => null);
    if (response.status === 401 && path !== '/login') { showLogin(); throw new ApiError('Войдите заново.', 401); }
    if (!response.ok) {
      const err = data?.error || {};
      const details = (err.details || []).map(d => d.message).filter(m => m && m.length > 12);
      throw new ApiError((ERROR_TEXT[err.code] || err.message || `Ошибка ${response.status}`) + (details.length ? ` (${details.join('; ')})` : ''),
        response.status, err.code || '');
    }
    return { data, etag: response.headers.get('ETag') };
  }
  const get = async (path) => (await api(path)).data;
  const M = (inst) => `/institutions/${inst}/manage`;
  async function listAll(path) {
    const items = []; let cursor = null; let guard = 0;
    do {
      const q = new URLSearchParams({ limit: '100', ...(cursor ? { cursor } : {}) });
      const data = await get(`${path}${path.includes('?') ? '&' : '?'}${q}`);
      items.push(...data.items); cursor = data.next_cursor; guard += 1;
    } while (cursor && guard < 50);
    return items;
  }

  // ------------------------------------------------------------------
  // Общие элементы интерфейса
  // ------------------------------------------------------------------
  let toastTimer = null;
  function toast(text) {
    const el = $('toast'); el.textContent = text; el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
  }
  function showError(error) {
    if (!(error instanceof ApiError)) { console.error(error); error = new ApiError('Не удалось выполнить действие.'); }
    if (error.status === 401) return;
    $('notice').textContent = error.message; $('notice').hidden = false;
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  const clearError = () => { $('notice').hidden = true; };

  /** Выполнить действие и перерисовать экран; ошибки — в плашку сверху. */
  async function act(fn, done = 'Готово') {
    clearError();
    try { const msg = await fn(); if (msg !== false) toast(typeof msg === 'string' ? msg : done); await render(); }
    catch (error) { showError(error); if (error.code === 'PRECONDITION_FAILED') await render().catch(() => {}); }
  }

  function dialog(titleText, body, okText = 'Сохранить', { danger = false, info = false } = {}) {
    return new Promise((resolve) => {
      const d = $('dialog');
      $('dialog-title').textContent = titleText;
      $('dialog-body').replaceChildren(...[body].flat());
      $('dialog-ok').textContent = okText;
      $('dialog-ok').className = danger ? 'danger' : 'primary';
      $('dialog-cancel').hidden = info;
      d.onclose = () => resolve(d.returnValue === 'ok');
      d.returnValue = '';
      d.showModal();
      d.querySelector('input, select, textarea')?.focus();
    });
  }
  const confirmDanger = (t, text, ok) => dialog(t, h('p', {}, text), ok, { danger: true });

  /** Поле выбора пользователя с поиском по имени, UUID, MAX ID и вузам. */
  function userPicker(onPick, placeholder = 'Имя, UUID или MAX ID') {
    let chosen = null;
    const input = h('input', { placeholder, autocomplete: 'off' });
    const list = h('div', { class: 'picker-list', hidden: true });
    let timer = null;
    input.addEventListener('input', () => {
      chosen = null; onPick(null);
      clearTimeout(timer);
      const q = input.value.trim();
      if (!q) { list.hidden = true; return; }
      timer = setTimeout(async () => {
        try {
          const { items } = await get(`/users?q=${encodeURIComponent(q)}`);
          list.replaceChildren(...(items.length ? items.slice(0, 20).map(u => h('button', { type: 'button', onclick: () => {
            chosen = u; input.value = userLabel(u); list.hidden = true; onPick(u);
          } }, h('strong', {}, u.name || 'Без имени'), ' ', h('small', { class: 'muted' }, `${short(u.id)} · MAX ${u.max_user_id}`),
          u.memberships.length ? h('div', { class: 'small muted' }, u.memberships.map(m => m.name).join(', ')) : null))
            : [h('div', { class: 'empty small' }, 'Никого не найдено. Пользователь должен хотя бы раз открыть приложение.')]));
          list.hidden = false;
        } catch (e) { showError(e); }
      }, 250);
    });
    input.addEventListener('blur', () => setTimeout(() => { list.hidden = true; }, 200));
    const wrap = h('div', { class: 'picker' }, input, list);
    wrap.value = () => chosen;
    return wrap;
  }

  // ------------------------------------------------------------------
  // Навигация
  // ------------------------------------------------------------------
  const SECTIONS = [
    { id: 'overview', label: 'Сводка' },
    { id: 'applications', label: 'Заявки вузов', count: 'pending_applications' },
    { id: 'institutions', label: 'Вузы', count: 'pending_join_requests' },
    { id: 'users', label: 'Пользователи' },
    { id: 'audit', label: 'Журнал' },
    { id: 'tools', label: 'Обслуживание' },
  ];
  let counts = {};
  const route = () => (location.hash.replace(/^#\/?/, '') || 'overview').split('/');

  function drawNav() {
    const [section] = route();
    $('nav').replaceChildren(...SECTIONS.map(s => h('a', { href: `#/${s.id}`, 'aria-current': s.id === section ? 'page' : 'false' },
      s.label, s.count && counts[s.count] ? h('span', { class: 'count' }, counts[s.count]) : null)));
  }

  async function refreshCounts() {
    try { counts = (await get('/overview')).counts; drawNav(); } catch { /* сводка необязательна для работы */ }
  }

  let rendering = 0;
  async function render() {
    const ticket = ++rendering;
    const [section, ...rest] = route();
    drawNav();
    const view = h('div', {});
    const renderer = { overview: renderOverview, applications: renderApplications, institutions: renderInstitutions,
      users: renderUsers, audit: renderAudit, tools: renderTools }[section] || renderOverview;
    try {
      await renderer(view, rest);
      if (ticket === rendering) $('view').replaceChildren(view);
    } catch (error) {
      if (ticket === rendering) { $('view').replaceChildren(); showError(error); }
    }
    refreshCounts();
  }
  const head = (titleText, subtitle, ...actions) => h('div', { class: 'page-head' },
    h('div', {}, h('h1', {}, titleText), subtitle ? h('p', { class: 'muted m0' }, subtitle) : null),
    actions.length ? h('div', { class: 'actions' }, actions) : null);

  // ------------------------------------------------------------------
  // Сводка
  // ------------------------------------------------------------------
  async function renderOverview(view) {
    const data = await get('/overview');
    counts = data.counts;
    const c = data.counts;
    const stat = (value, label, href, alert) => h('a', { class: `stat clickable plain ${alert && value ? 'alert' : ''}`, href },
      h('b', {}, value), h('span', {}, label));
    view.append(head('Сводка', 'Состояние платформы и то, что ждёт решения.'),
      h('div', { class: 'stats' },
        stat(c.pending_applications, 'заявок на подключение вуза', '#/applications', true),
        stat(c.pending_join_requests, 'заявок на вступление', '#/institutions', true),
        stat(`${c.active_institutions}/${c.institutions}`, 'активных вузов', '#/institutions'),
        stat(c.users, 'пользователей', '#/users'),
        stat(c.memberships, 'членств в вузах', '#/users'),
        stat(c.staff, 'сотрудников поддержки', '#/users')),
      h('section', { class: 'card' }, h('h2', {}, 'Сервисы платформы'),
        h('div', { class: 'health' }, data.health.map(s => badge(h('span', {}, h('span', { class: `dot ${s.ok ? 'ok' : ''}` }),
          `${s.name}: ${s.ok ? `работает (${s.ms} мс)` : 'не отвечает'}`), s.ok ? 'ok' : 'danger')))),
      h('section', { class: 'card' }, h('h2', {}, 'Требует внимания'),
        data.attention.length ? data.attention.map(a => h('div', { class: 'item' }, h('div', { class: 'item-head' },
          h('div', {}, h('div', { class: 'item-title' }, a.name), h('div', { class: 'muted small' },
            a.issue === 'no_owner' ? 'У вуза нет владельца — некому управлять им в приложении.' : `Новых заявок на вступление: ${a.count}.`)),
          h('a', { class: 'btn-link', href: `#/institutions/${a.institution_id}/${a.issue === 'no_owner' ? 'overview' : 'requests'}` },
            a.issue === 'no_owner' ? 'Назначить владельца' : 'Рассмотреть'))))
          : h('p', { class: 'muted' }, 'Всё в порядке.')));
  }

  // ------------------------------------------------------------------
  // Заявки на подключение вузов
  // ------------------------------------------------------------------
  async function renderApplications(view) {
    const { items } = await get('/applications');
    const pending = items.filter(a => a.status === 'pending');
    const done = items.filter(a => a.status !== 'pending');
    const card = (a) => h('div', { class: 'item' },
      h('div', { class: 'item-head' }, h('span', { class: 'item-title' }, title(a.titles)), badge(...APP_STATUS[a.status])),
      h('dl', { class: 'meta' },
        h('dt', {}, 'Заявитель'), h('dd', {}, a.applicant_name || 'Без имени', ' ', h('code', {}, a.applicant_user_id)),
        h('dt', {}, 'Контакт'), h('dd', {}, a.contact || '—'),
        a.comment ? [h('dt', {}, 'Комментарий'), h('dd', {}, a.comment)] : null,
        h('dt', {}, 'Подана'), h('dd', {}, fmtDate(a.created_at)),
        a.decision_reason ? [h('dt', {}, 'Причина отказа'), h('dd', {}, a.decision_reason)] : null,
        a.institution_id ? [h('dt', {}, 'Вуз'), h('dd', {}, h('a', { href: `#/institutions/${a.institution_id}` }, 'Открыть вуз'))] : null),
      a.status === 'pending' ? h('div', { class: 'actions' },
        h('button', { class: 'primary', onclick: () => approveApplication(a) }, 'Одобрить'),
        h('button', { class: 'danger', onclick: () => rejectApplication(a) }, 'Отклонить')) : null);
    view.append(head('Заявки на подключение вузов', 'Пользователи подают их с экрана регистрации в приложении. Одобрение создаёт вуз, а заявитель становится его владельцем.'),
      h('section', { class: 'card' }, h('h2', {}, `Ждут решения (${pending.length})`),
        pending.length ? pending.map(card) : h('p', { class: 'muted' }, 'Новых заявок нет.')),
      done.length ? h('section', { class: 'card' }, h('h2', {}, 'Рассмотренные'), done.slice(0, 50).map(card)) : '');
  }

  async function approveApplication(a) {
    const ru = h('input', { value: a.titles.ru || '', maxLength: 200 });
    const en = h('input', { value: a.titles.en || '', maxLength: 200 });
    const services = checks('cloud', Object.entries(CLOUD), Object.keys(CLOUD));
    if (!await dialog('Одобрить заявку', [h('p', { class: 'muted' }, 'Название можно поправить перед созданием вуза.'),
      field('Название (рус.)', ru), field('Название (англ.)', en, 'Необязательно'),
      h('p', { class: 'm0' }, 'Сразу подключить сервисы платформы:'), services], 'Одобрить и создать вуз')) return;
    await act(async () => {
      const titles = { ru: ru.value.trim() }; if (en.value.trim()) titles.en = en.value.trim();
      const { data } = await api(`/applications/${a.id}/approve`, { method: 'POST', body: { titles } });
      for (const s of checked(services, 'cloud')) await api(`/institutions/${data.institution_id}/cloud/${s}`, { method: 'POST' });
      return 'Вуз создан, заявитель назначен владельцем.';
    });
  }

  async function rejectApplication(a) {
    const reason = h('textarea', { rows: 3, maxLength: 1000, placeholder: 'Заявитель увидит эту причину' });
    if (!await dialog('Отклонить заявку', field('Причина', reason), 'Отклонить', { danger: true })) return;
    await act(() => api(`/applications/${a.id}/reject`, { method: 'POST', body: { reason: reason.value.trim() } }), 'Заявка отклонена.');
  }

  // ------------------------------------------------------------------
  // Вузы
  // ------------------------------------------------------------------
  let instFilter = '';
  async function renderInstitutions(view, rest) {
    if (rest[0]) return renderInstitution(view, rest[0], rest[1] || 'overview');
    const { items } = await get('/institutions');
    const search = h('input', { class: 'search', type: 'search', placeholder: 'Поиск по названию', value: instFilter });
    const grid = h('div', { class: 'grid' });
    const draw = () => {
      const q = search.value.trim().toLowerCase(); instFilter = search.value;
      const shown = items.filter(i => !q || (title(i.titles) + ' ' + (i.titles.en || '')).toLowerCase().includes(q));
      grid.replaceChildren(...shown.map(i => h('a', { class: 'card clickable plain inst-card', href: `#/institutions/${i.id}` },
        h('div', { class: 'item-head' }, h('span', { class: 'item-title' }, title(i.titles)), badge(...INST_STATUS[i.status] || [i.status])),
        h('div', { class: 'muted small' }, `Участников: ${i.members_count} · владельцев: ${i.owners_count}`),
        h('div', { class: 'actions' },
          i.pending_join_requests ? badge(`Заявок на вступление: ${i.pending_join_requests}`, 'warn') : null,
          !i.owners_count ? badge('Нет владельца', 'danger') : null,
          i.services.filter(s => s.service_type !== 'administration').map(s => badge(s.title, s.enabled ? 'accent' : ''))))));
      if (!shown.length) grid.append(h('p', { class: 'muted' }, items.length ? 'Ничего не найдено.' : 'Вузов пока нет.'));
    };
    search.addEventListener('input', draw);
    draw();
    view.append(head('Вузы', 'Всё управление вузом — внутри его карточки.', h('button', { class: 'primary', onclick: createInstitution }, '+ Подключить вуз')),
      h('div', { class: 'mb' }, search), grid);
  }

  async function createInstitution() {
    const ru = h('input', { maxLength: 200, placeholder: 'Например, МГТУ им. Н. Э. Баумана' });
    const en = h('input', { maxLength: 200 });
    let owner = null;
    const picker = userPicker(u => { owner = u; });
    const services = checks('cloud', Object.entries(CLOUD), Object.keys(CLOUD));
    if (!await dialog('Подключить вуз', [field('Название (рус.)', ru), field('Название (англ.)', en, 'Необязательно'),
      field('Владелец', picker, 'Необязательно. Можно назначить позже.'),
      h('p', { class: 'm0' }, 'Сервисы платформы:'), services], 'Подключить')) return;
    await act(async () => {
      if (!ru.value.trim()) throw new ApiError('Укажите название вуза.');
      const titles = { ru: ru.value.trim() }; if (en.value.trim()) titles.en = en.value.trim();
      const { data } = await api('/institutions', { method: 'POST', body: { titles, services: checked(services, 'cloud'),
        ...(owner ? { owner_user_id: owner.id } : {}) } });
      location.hash = `#/institutions/${data.id}`;
      return 'Вуз подключён.';
    });
  }

  const INST_TABS = [
    ['overview', 'Обзор'], ['requests', 'Заявки'], ['members', 'Участники'], ['groups', 'Группы'],
    ['services', 'Сервисы'], ['roles', 'Роли'], ['audit', 'Журнал'],
  ];

  async function renderInstitution(view, id, tab) {
    const inst = await api(`/institutions/${id}`);
    const i = inst.data;
    const tabs = h('nav', { class: 'tabs' }, INST_TABS.map(([t, label]) => h('button', { type: 'button', 'aria-current': t === tab ? 'page' : 'false',
      onclick: () => { location.hash = `#/institutions/${id}/${t}`; } }, label, t === 'requests' && i.pending_join_requests ? ` (${i.pending_join_requests})` : '')));
    const body = h('div', {});
    view.append(h('div', { class: 'crumbs' }, h('a', { href: '#/institutions' }, '← Все вузы')),
      head(title(i.titles), null, badge(...INST_STATUS[i.status] || [i.status])), tabs, body);
    const renderers = { overview: instOverview, requests: instRequests, members: instMembers, groups: instGroups,
      services: instServices, roles: instRoles, audit: instAudit };
    await (renderers[tab] || instOverview)(body, i, inst.etag);
  }

  async function instOverview(body, i, etag) {
    const base = M(i.id);
    const { data: settings, etag: settingsTag } = await api(base);
    const members = await listAll(`${base}/members`);
    const { items: users } = await get('/users');
    const byId = Object.fromEntries(users.map(u => [u.id, u]));
    const adminService = (await listAll(`${base}/services`)).find(s => s.service_type === 'administration');
    const owners = [];
    for (const m of members.filter(m => m.profiles.includes('admin'))) {
      const r = await get(`${base}/services/${adminService.id}/users/${m.user_id}/profiles/admin/roles`);
      if (r.roles.includes('owner')) owners.push(m.user_id);
    }

    const ru = h('input', { value: settings.titles.ru || '', maxLength: 200 });
    const en = h('input', { value: settings.titles.en || '', maxLength: 200 });
    const locale = h('select', {}, h('option', { value: 'ru' }, 'Русский'), h('option', { value: 'en' }, 'English'));
    locale.value = settings.default_locale;
    const hosts = h('textarea', { rows: 3, placeholder: 'coursework.university.ru' });
    hosts.value = i.local_hosts.join('\n');
    let newOwner = null;
    const picker = userPicker(u => { newOwner = u; });
    const cloud = Object.entries(CLOUD).map(([type, label]) => {
      const s = i.services.find(x => x.service_type === type);
      return h('div', { class: 'item' }, h('div', { class: 'item-head' },
        h('div', {}, h('span', { class: 'item-title' }, label), ' ', s ? badge(s.enabled ? 'включён' : 'выключен', s.enabled ? 'ok' : '') : badge('не подключён')),
        h('button', { class: s ? 'quiet' : 'primary', onclick: () => act(async () => (await api(`/institutions/${i.id}/cloud/${type}`, { method: 'POST' })).data.message) },
          s ? 'Обновить и включить' : 'Подключить')));
    });

    body.append(
      h('div', { class: 'grid' },
        h('section', { class: 'card stack' }, h('h2', {}, 'Название и язык'), field('Название (рус.)', ru), field('Название (англ.)', en), field('Язык по умолчанию', locale),
          h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => act(async () => {
            const titles = { ru: ru.value.trim() }; if (en.value.trim()) titles.en = en.value.trim();
            await api(base, { method: 'PATCH', body: { titles, default_locale: locale.value }, etag: settingsTag });
          }, 'Сохранено.') }, 'Сохранить'))),
        h('section', { class: 'card stack' }, h('h2', {}, 'Статус'),
          h('p', { class: 'muted m0' }, i.status === 'active'
            ? 'Вуз работает. Приостановка сразу закрывает все сессии сервисов вуза.'
            : 'Вуз приостановлен: участники не могут открыть его сервисы.'),
          h('dl', { class: 'meta' }, h('dt', {}, 'ID'), h('dd', {}, h('code', {}, i.id)), h('dt', {}, 'Создан'), h('dd', {}, fmtDate(i.created_at)),
            h('dt', {}, 'Участников'), h('dd', {}, i.members_count)),
          h('div', { class: 'actions' }, i.status === 'active'
            ? h('button', { class: 'danger', onclick: async () => { if (await confirmDanger('Приостановить вуз?', 'Все участники потеряют доступ к сервисам вуза до возобновления.', 'Приостановить'))
              act(() => api(`/institutions/${i.id}/status`, { method: 'PATCH', body: { status: 'suspended' }, etag }), 'Вуз приостановлен.'); } }, 'Приостановить')
            : h('button', { class: 'ok', onclick: () => act(() => api(`/institutions/${i.id}/status`, { method: 'PATCH', body: { status: 'active' }, etag }), 'Вуз возобновлён.') }, 'Возобновить'))),
        h('section', { class: 'card stack' }, h('h2', {}, 'Владельцы'),
          owners.length ? owners.map(uid => h('div', {}, h('strong', {}, byId[uid]?.name || 'Без имени'), ' ', h('code', {}, short(uid))))
            : h('p', { class: 'error' }, 'Владельца нет: вузом некому управлять в приложении.'),
          field('Добавить владельца', picker, 'Получит профиль «Администратор» и роль владельца.'),
          h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => act(async () => {
            if (!newOwner) throw new ApiError('Выберите пользователя из списка.');
            await api(`/institutions/${i.id}/owners`, { method: 'POST', body: { user_id: newOwner.id } });
          }, 'Владелец назначен.') }, 'Назначить'))),
        h('section', { class: 'card stack' }, h('h2', {}, 'Сервисы платформы'), cloud),
        h('section', { class: 'card stack' }, h('h2', {}, 'Хосты своих сервисов'),
          h('p', { class: 'muted small m0' }, 'DNS-имена, на которых вуз может подключать свои (локальные) сервисы. По одному в строке. Сервисы на удалённых хостах выключатся.'),
          hosts, h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => act(() => api(`/institutions/${i.id}/local-hosts`, { method: 'PUT', etag,
            body: { hostnames: hosts.value.split(/[\s,]+/).map(x => x.trim()).filter(Boolean) } }), 'Хосты сохранены.') }, 'Сохранить')))));
  }

  async function instRequests(body, i) {
    const base = M(i.id);
    const { items } = await get(`${base}/join-requests`);
    const pending = items.filter(r => r.status === 'pending');
    const card = (r) => h('div', { class: 'item' },
      h('div', { class: 'item-head' }, h('span', { class: 'item-title' }, r.full_name), badge(...APP_STATUS[r.status])),
      h('dl', { class: 'meta' }, h('dt', {}, 'Профиль'), h('dd', {}, PROFILES[r.profile]),
        r.group_name ? [h('dt', {}, 'Группа'), h('dd', {}, r.group_name)] : null,
        h('dt', {}, 'Пользователь'), h('dd', {}, h('code', {}, r.user_id)), h('dt', {}, 'Подана'), h('dd', {}, fmtDate(r.created_at)),
        r.decision_reason ? [h('dt', {}, 'Причина'), h('dd', {}, r.decision_reason)] : null),
      r.status === 'pending' ? h('div', { class: 'actions' },
        h('button', { class: 'primary', onclick: () => act(() => api(`${base}/join-requests/${r.id}/approve`, { method: 'POST' }), `${r.full_name} добавлен(а) в вуз.`) }, 'Одобрить'),
        h('button', { class: 'danger', onclick: async () => {
          const reason = h('input', { maxLength: 500, placeholder: 'Необязательно' });
          if (!await dialog('Отклонить заявку', field('Причина (увидит пользователь)', reason), 'Отклонить', { danger: true })) return;
          act(() => api(`${base}/join-requests/${r.id}/reject`, { method: 'POST', body: reason.value.trim() ? { reason: reason.value.trim() } : {} }), 'Заявка отклонена.');
        } }, 'Отклонить')) : null);
    body.append(h('section', { class: 'card' }, h('h2', {}, `Новые заявки на вступление (${pending.length})`),
      h('p', { class: 'muted small' }, 'Пользователь сам выбрал вуз, профиль и группу из списка и указал имя. Одобрение добавляет его в вуз и в группу.'),
      pending.length ? pending.map(card) : h('p', { class: 'muted' }, 'Новых заявок нет.')),
    items.length > pending.length ? h('section', { class: 'card' }, h('h2', {}, 'Рассмотренные'), items.filter(r => r.status !== 'pending').slice(0, 50).map(card)) : '');
  }

  async function instMembers(body, i) {
    const base = M(i.id);
    const [members, groups] = await Promise.all([listAll(`${base}/members`), listAll(`${base}/groups`)]);
    const groupName = Object.fromEntries(groups.map(g => [g.id, g.name]));
    const groupSelect = (current, onchange) => {
      const s = h('select', { onchange: () => onchange(s.value || null) }, h('option', { value: '' }, 'Без группы'), groups.map(g => h('option', { value: g.id }, g.name)));
      s.value = current || ''; return s;
    };
    let newUser = null; let newGroup = null;
    const picker = userPicker(u => { newUser = u; });
    const profiles = checks('new-profiles', Object.entries(PROFILES), ['student']);
    const search = h('input', { type: 'search', class: 'search', placeholder: 'Фильтр по имени или ID' });
    const list = h('div', {});
    const draw = () => {
      const q = search.value.trim().toLowerCase();
      list.replaceChildren(...members.filter(m => !q || `${m.full_name || ''} ${m.user_id}`.toLowerCase().includes(q)).map(m => {
        const row = h('div', { class: 'item' });
        const box = checks(`p-${m.user_id}`, Object.entries(PROFILES), m.profiles);
        row.append(h('div', { class: 'item-head' }, h('div', {}, h('span', { class: 'item-title' }, m.full_name || 'Без имени'), ' ', h('code', {}, m.user_id)),
          h('small', { class: 'muted' }, `с ${fmtDate(m.created_at)}`)), box,
        m.profiles.includes('student') ? field('Группа', groupSelect(m.group_id, v => act(() => api(`${base}/members/${m.user_id}/group`, { method: 'PUT', body: { group_id: v } }),
          v ? `Студент в группе «${groupName[v]}».` : 'Студент убран из группы.'))) : '',
        h('div', { class: 'actions' },
          h('button', { class: 'quiet', onclick: () => act(async () => {
            const selected = checked(row, `p-${m.user_id}`);
            if (!selected.length) throw new ApiError('Нужен хотя бы один профиль. Чтобы исключить человека, удалите его.');
            const { etag } = await api(`${base}/members/${m.user_id}`);
            await api(`${base}/members/${m.user_id}/profiles`, { method: 'PUT', body: { profiles: selected }, etag });
          }, 'Профили обновлены.') }, 'Сохранить профили'),
          h('button', { class: 'danger', onclick: async () => {
            if (!await confirmDanger('Удалить участника?', `${m.full_name || short(m.user_id)} потеряет доступ ко всем сервисам вуза.`, 'Удалить')) return;
            act(async () => { const { etag } = await api(`${base}/members/${m.user_id}`); await api(`${base}/members/${m.user_id}`, { method: 'DELETE', etag }); }, 'Участник удалён.');
          } }, 'Удалить')));
        return row;
      }));
      if (!list.children.length) list.append(h('p', { class: 'muted' }, members.length ? 'Никого не найдено.' : 'Участников пока нет.'));
    };
    search.addEventListener('input', draw);
    draw();
    body.append(
      h('section', { class: 'card stack' }, h('h2', {}, 'Добавить участника'),
        h('div', { class: 'row2' }, field('Пользователь', picker), groups.length ? field('Группа (для студента)', groupSelect(null, v => { newGroup = v; })) : null),
        profiles,
        h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => act(async () => {
          if (!newUser) throw new ApiError('Выберите пользователя из списка.');
          const selected = checked(profiles, 'new-profiles');
          if (!selected.length) throw new ApiError('Выберите хотя бы один профиль.');
          await api(`${base}/members`, { method: 'POST', body: { user_id: newUser.id, profiles: selected,
            ...(newGroup && selected.includes('student') ? { group_id: newGroup } : {}) } });
        }, 'Участник добавлен.') }, 'Добавить'))),
      h('section', { class: 'card' }, h('div', { class: 'item-head mb' }, h('h2', { class: 'm0' }, `Участники (${members.length})`), search), list));
  }

  async function instGroups(body, i) {
    const base = M(i.id);
    const [groups, members] = await Promise.all([listAll(`${base}/groups`), listAll(`${base}/members`)]);
    const students = members.filter(m => m.profiles.includes('student'));
    const name = h('input', { placeholder: 'Например, ИВТ-21', maxLength: 100 });
    body.append(h('section', { class: 'card stack' }, h('h2', {}, 'Новая группа'),
      h('div', { class: 'actions' }, h('div', { class: 'grow' }, name), h('button', { class: 'primary', onclick: () => act(async () => {
        if (!name.value.trim()) throw new ApiError('Укажите название группы.');
        await api(`${base}/groups`, { method: 'POST', body: { name: name.value.trim() } });
      }, 'Группа создана.') }, 'Создать'))));
    const ungrouped = students.filter(m => !m.group_id);
    if (ungrouped.length) body.append(h('section', { class: 'card' }, h('h2', {}, `Студенты без группы (${ungrouped.length})`),
      ungrouped.map(m => h('div', { class: 'item' }, h('div', { class: 'item-head' }, h('span', {}, m.full_name || short(m.user_id)),
        groups.length ? (() => { const s = h('select', { class: 'narrow', onchange: () => act(() => api(`${base}/members/${m.user_id}/group`, { method: 'PUT', body: { group_id: s.value } }), 'Группа назначена.') },
          h('option', { value: '' }, 'Назначить группу…'), groups.map(g => h('option', { value: g.id }, g.name))); return s; })() : null)))));
    body.append(h('section', { class: 'card' }, h('h2', {}, `Группы (${groups.length})`),
      groups.length ? groups.map(g => {
        const inGroup = students.filter(m => m.group_id === g.id);
        return h('div', { class: 'item' }, h('div', { class: 'item-head' }, h('span', { class: 'item-title' }, g.name), badge(`студентов: ${inGroup.length}`)),
          inGroup.length ? h('div', { class: 'small muted' }, inGroup.map(m => m.full_name || short(m.user_id)).join(', ')) : null,
          h('div', { class: 'actions' },
            h('button', { class: 'quiet', onclick: async () => {
              const { data, etag } = await api(`${base}/groups/${g.id}/members`);
              const box = checks('roster', students.filter(m => !m.group_id || m.group_id === g.id).map(m => [m.user_id, m.full_name || m.user_id]), data.user_ids);
              if (!await dialog(`Состав группы «${g.name}»`, [box, h('p', { class: 'muted small' }, 'Студенты из других групп сюда не попадают — переведите их на вкладке «Участники».')])) return;
              act(() => api(`${base}/groups/${g.id}/members`, { method: 'PUT', body: { user_ids: checked(box, 'roster') }, etag }), 'Состав обновлён.');
            } }, 'Состав'),
            h('button', { class: 'quiet', onclick: async () => {
              const input = h('input', { value: g.name, maxLength: 100 });
              if (!await dialog('Переименовать группу', field('Название', input))) return;
              act(async () => { const { etag } = await api(`${base}/groups/${g.id}`); await api(`${base}/groups/${g.id}`, { method: 'PATCH', body: { name: input.value.trim() }, etag }); }, 'Группа переименована.');
            } }, 'Переименовать'),
            h('button', { class: 'danger', onclick: async () => {
              if (!await confirmDanger('Удалить группу?', 'Удалить можно только пустую группу.', 'Удалить')) return;
              act(async () => { const { etag } = await api(`${base}/groups/${g.id}`); await api(`${base}/groups/${g.id}`, { method: 'DELETE', etag }); }, 'Группа удалена.');
            } }, 'Удалить')));
      }) : h('p', { class: 'muted' }, 'Групп пока нет. Без групп студенты не смогут подать заявку на вступление.')));
  }

  async function instServices(body, i) {
    const base = M(i.id);
    const services = await listAll(`${base}/services`);
    const name = h('input', { placeholder: 'Например, Курсовые работы' });
    const code = h('input', { placeholder: 'coursework', autocomplete: 'off' });
    const profiles = checks('custom-profiles', Object.entries(PROFILES), ['student', 'teacher', 'admin']);
    const apiUrl = h('input', { placeholder: 'https://coursework.university.ru/api/v1' });
    const client = h('input', { placeholder: 'https://coursework.university.ru' });
    for (const s of services) {
      const card = h('section', { class: 'card stack' },
        h('div', { class: 'item-head' }, h('h2', { class: 'm0' }, title(s.manifest.titles) || s.service_type),
          h('div', { class: 'actions' }, badge(s.enabled ? 'включён' : 'выключен', s.enabled ? 'ok' : ''), badge(s.deployment === 'cloud' ? 'облако' : 'свой сервер'),
            s.protected ? badge('защищён', 'accent') : null)),
        h('dl', { class: 'meta' }, h('dt', {}, 'Тип'), h('dd', {}, s.service_type), h('dt', {}, 'API'), h('dd', {}, h('code', {}, s.api_base_url)),
          h('dt', {}, 'Клиент'), h('dd', {}, h('code', {}, s.client_base_url)),
          h('dt', {}, 'Меню'), h('dd', {}, s.manifest.menus.length ? s.manifest.menus.map(m => title(m.titles)).join(', ') : 'не опубликовано')));
      if (!s.protected) {
        const actions = h('div', { class: 'actions' },
          h('button', { class: s.enabled ? 'quiet' : 'primary', onclick: () => act(async () => {
            const { etag } = await api(`${base}/services/${s.id}`);
            await api(`${base}/services/${s.id}`, { method: 'PATCH', body: { enabled: !s.enabled }, etag });
          }, s.enabled ? 'Сервис выключен.' : 'Сервис включён.') }, s.enabled ? 'Выключить' : 'Включить'),
          h('button', { class: 'danger', onclick: async () => {
            if (!await confirmDanger('Удалить сервис?', 'Ключи и сессии будут отозваны, назначения ролей удалены.', 'Удалить')) return;
            act(async () => { const { etag } = await api(`${base}/services/${s.id}`); await api(`${base}/services/${s.id}`, { method: 'DELETE', etag }); }, 'Сервис удалён.');
          } }, 'Удалить'));
        if (s.deployment === 'local') {
          actions.prepend(h('button', { class: 'quiet', onclick: () => act(async () => {
            const { data } = await api(`${base}/services/${s.id}/credentials`, { method: 'POST' });
            const env = [`CORE_URL=${location.protocol}//${location.hostname}`, `SERVICE_CLIENT_ID=${data.credential.client_id}`,
              `SERVICE_CLIENT_SECRET=${data.client_secret}`, `SERVICE_API_BASE_URL=${s.api_base_url}`, `SERVICE_CLIENT_BASE_URL=${s.client_base_url}`].join('\n');
            const area = h('textarea', { rows: 6, readOnly: true }); area.value = env;
            await dialog('Ключ выдан', [h('p', {}, 'Добавьте строки в .env сервиса на сервере вуза. Секрет больше не покажется.'), area], 'Готово', { info: true });
            return false;
          }) }, 'Выдать ключ'));
        }
        card.append(actions);
      } else card.append(h('p', { class: 'muted small m0' }, 'Администрирование есть у каждого вуза и не отключается.'));
      body.append(card);
    }
    body.append(h('section', { class: 'card stack' }, h('h2', {}, 'Подключить свой сервис вуза'),
      h('p', { class: 'muted small m0' }, 'Адреса должны быть на одобренном хосте (вкладка «Обзор»). После регистрации выдайте ключ — сервис сам опубликует меню и роли.'),
      h('div', { class: 'row2' }, field('Название', name), field('Код', code, 'латиница, цифры, -')), profiles,
      h('div', { class: 'row2' }, field('Адрес API', apiUrl), field('Адрес клиента (origin)', client)),
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => act(() => api(`${base}/services`, { method: 'POST', idem: true, body: {
        service_type: `custom.${code.value.trim().toLowerCase()}`, deployment: 'local', titles: { ru: name.value.trim() },
        supported_profiles: checked(profiles, 'custom-profiles'), api_base_url: apiUrl.value.trim(), client_base_url: client.value.trim() } }), 'Сервис зарегистрирован.') }, 'Зарегистрировать'))));
  }

  let rolesService = null;
  async function instRoles(body, i) {
    const base = M(i.id);
    const [services, members] = await Promise.all([listAll(`${base}/services`), listAll(`${base}/members`)]);
    if (!services.some(s => s.id === rolesService)) rolesService = services[0]?.id;
    const service = services.find(s => s.id === rolesService);
    const select = h('select', { onchange: () => { rolesService = select.value; render(); } }, services.map(s => h('option', { value: s.id }, title(s.manifest.titles) || s.service_type)));
    select.value = rolesService;
    body.append(h('section', { class: 'card stack' }, field('Сервис', select),
      h('p', { class: 'muted small m0' }, 'Профиль — кто человек в вузе. Роль — что он может в выбранном сервисе.')));
    if (!service) return;
    const roles = await listAll(`${base}/services/${service.id}/roles`);
    body.append(h('section', { class: 'card' }, h('h2', {}, 'Роли сервиса'), roles.length ? roles.map(r => h('div', { class: 'item' },
      h('div', { class: 'item-head' }, h('span', { class: 'item-title' }, title(r.titles)), h('div', { class: 'actions' }, h('code', {}, r.code), r.system ? badge('системная', 'accent') : null)),
      h('div', { class: 'small muted' }, `Профили: ${r.allowed_profiles.map(p => PROFILES[p]).join(', ')}. Права: ${r.permissions.map(p => PERMISSION_NAMES[p] || p).join(', ') || 'нет'}`)))
      : h('p', { class: 'muted' }, 'Ролей нет.')));

    const eligible = members.filter(m => m.profiles.some(p => service.supported_profiles.includes(p)));
    const rows = [];
    for (const m of eligible) {
      for (const p of m.profiles.filter(p => service.supported_profiles.includes(p) && roles.some(r => r.allowed_profiles.includes(p)))) {
        const path = `${base}/services/${service.id}/users/${m.user_id}/profiles/${p}/roles`;
        const { data, etag } = await api(path);
        const box = checks(`a-${m.user_id}-${p}`, roles.filter(r => r.allowed_profiles.includes(p)).map(r => [r.code, title(r.titles)]), data.roles);
        rows.push(h('div', { class: 'item' }, h('div', { class: 'item-head' }, h('span', {}, h('strong', {}, m.full_name || short(m.user_id)), ' · ', PROFILES[p]),
          h('button', { class: 'quiet', onclick: () => act(() => api(path, { method: 'PUT', body: { roles: checked(box, `a-${m.user_id}-${p}`) }, etag }), 'Роли обновлены.') }, 'Сохранить')), box));
      }
    }
    body.append(h('section', { class: 'card' }, h('h2', {}, 'Назначение ролей'), rows.length ? rows : h('p', { class: 'muted' }, 'Нет участников с подходящим профилем.')));
  }

  async function instAudit(body, i) { await auditList(body, i.id); }

  // ------------------------------------------------------------------
  // Пользователи
  // ------------------------------------------------------------------
  let userQuery = '';
  async function renderUsers(view) {
    const search = h('input', { type: 'search', class: 'search', placeholder: 'Имя, UUID, MAX ID или вуз', value: userQuery });
    const list = h('section', { class: 'card' });
    const institutions = (await get('/institutions')).items;
    async function load() {
      userQuery = search.value;
      const { items } = await get(`/users?q=${encodeURIComponent(search.value.trim())}`);
      list.replaceChildren(h('h2', {}, `Пользователи (${items.length}${items.length >= 500 ? '+' : ''})`),
        ...items.map(u => h('div', { class: 'item' },
          h('div', { class: 'item-head' }, h('div', {}, h('span', { class: 'item-title' }, u.name || 'Без имени'), ' ', u.staff ? badge('поддержка', 'accent') : null),
            h('small', { class: 'muted' }, `вход с ${fmtDate(u.created_at)}`)),
          h('dl', { class: 'meta' }, h('dt', {}, 'UUID'), h('dd', {}, h('code', {}, u.id)), h('dt', {}, 'MAX ID'), h('dd', {}, u.max_user_id),
            h('dt', {}, 'Вузы'), h('dd', {}, u.memberships.length ? u.memberships.map(m => h('div', {}, h('a', { href: `#/institutions/${m.institution_id}/members` }, m.name),
              ` — ${m.profiles.map(p => PROFILES[p]).join(', ')}`)) : 'не состоит')),
          h('div', { class: 'actions' },
            h('button', { class: 'quiet', onclick: () => addToInstitution(u, institutions) }, 'Добавить в вуз'),
            u.staff
              ? h('button', { class: 'danger', onclick: () => act(() => api(`/users/${u.id}/staff`, { method: 'DELETE' }), 'Права поддержки отозваны.') }, 'Отозвать поддержку')
              : h('button', { class: 'quiet', onclick: () => act(() => api(`/users/${u.id}/staff`, { method: 'POST' }), 'Права поддержки выданы.') }, 'Сделать сотрудником поддержки'),
            // Удалить можно только того, кто не состоит ни в одном вузе и не работает в поддержке.
            !u.memberships.length && !u.staff ? h('button', { class: 'danger', onclick: async () => {
              if (!await confirmDanger('Удалить пользователя?', 'Будут удалены его сессии, аватар и заявки. При следующем входе через MAX он появится заново с новым UUID.', 'Удалить')) return;
              act(() => api(`/users/${u.id}`, { method: 'DELETE' }), 'Пользователь удалён.');
            } }, 'Удалить') : null))));
      if (!items.length) list.append(h('p', { class: 'muted' }, 'Никого не найдено.'));
    }
    let timer = null;
    search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => load().catch(showError), 250); });
    await load();
    view.append(head('Пользователи', 'Все, кто хотя бы раз открывал приложение через MAX.'), h('div', { class: 'mb' }, search), list);
  }

  async function addToInstitution(u, institutions) {
    const inst = h('select', {}, institutions.map(i => h('option', { value: i.id }, title(i.titles))));
    const profiles = checks('add-profiles', Object.entries(PROFILES), ['student']);
    if (!await dialog(`Добавить ${u.name || 'пользователя'} в вуз`, [field('Вуз', inst), profiles,
      h('p', { class: 'muted small m0' }, 'Группу студенту можно назначить в карточке вуза → «Участники».')], 'Добавить')) return;
    await act(() => api(`${M(inst.value)}/members`, { method: 'POST', body: { user_id: u.id, profiles: checked(profiles, 'add-profiles') } }), 'Пользователь добавлен в вуз.');
  }

  // ------------------------------------------------------------------
  // Журнал
  // ------------------------------------------------------------------
  async function auditList(container, institutionId) {
    const list = h('div', {});
    let before = null;
    const more = h('button', { class: 'quiet', onclick: () => load().catch(showError) }, 'Показать ещё');
    async function load() {
      const q = new URLSearchParams({ ...(institutionId ? { institution_id: institutionId } : {}), ...(before ? { before } : {}) });
      const data = await get(`/audit?${q}`);
      for (const e of data.items) {
        list.append(h('div', { class: 'item' }, h('div', { class: 'item-head' },
          h('span', {}, h('strong', {}, ACTIONS[e.action] || e.action), e.outcome === 'denied' ? [' ', badge(`отказ: ${e.error_code}`, 'danger')] : null),
          h('small', { class: 'muted' }, fmtDate(e.created_at))),
          h('div', { class: 'small muted' }, [ACTOR[e.actor_kind] || e.actor_kind, e.actor_user_id ? short(e.actor_user_id) : null,
            !institutionId && e.institution_name ? `вуз: ${e.institution_name}` : null, e.target_id ? `объект: ${e.target_id}` : null].filter(Boolean).join(' · ')),
          Object.keys(e.details || {}).length ? h('details', {}, h('summary', { class: 'small' }, 'Подробности'), h('pre', { class: 'small' }, JSON.stringify(e.details, null, 2))) : null));
      }
      before = data.next_before; more.hidden = !before;
      if (!list.children.length) list.append(h('p', { class: 'muted' }, 'Записей пока нет.'));
    }
    await load();
    container.append(h('section', { class: 'card' }, list, more));
  }

  async function renderAudit(view) {
    view.append(head('Журнал', 'Все изменения на платформе: кто, что и когда.'));
    await auditList(view, null);
  }

  // ------------------------------------------------------------------
  // Обслуживание: бывшие команды manage.py
  // ------------------------------------------------------------------
  async function renderTools(view) {
    const tool = (name, text, run, button = 'Выполнить') => h('section', { class: 'card stack' }, h('h2', {}, name),
      h('p', { class: 'muted m0' }, text), h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: run }, button)));
    const area = h('textarea', { rows: 6, placeholder: '{"groups": [...]}' });
    const file = h('input', { type: 'file', accept: '.json,application/json', onchange: async () => { area.value = await file.files[0]?.text() || ''; } });
    const runTool = (name, body) => act(async () => (await api(`/tools/${name}`, { method: 'POST', body })).data.message);
    let demoUser = null;
    const demoPicker = userPicker(u => { demoUser = u; });
    view.append(head('Обслуживание', 'То, что раньше запускалось на сервере через docker compose exec backend python manage.py …'),
      h('div', { class: 'grid' },
        tool('Проверить платформу', 'У каждого вуза должен быть сервис администрирования с системными ролями; адреса и ключи облачных сервисов приводятся к настройкам сервера. Запускайте после смены адресов в .env.',
          () => runTool('ensure-invariants')),
        tool('Выгрузить настройки сервисов', 'Перезаписывает JSON-файлы в services/connected по данным ядра. Нужно, если файлы потерялись или папка была недоступна.',
          () => runTool('export-services')),
        h('section', { class: 'card stack' }, h('h2', {}, 'Импорт групп из расписания'),
          h('p', { class: 'muted m0' }, 'Перенос групп старого формата: JSON из python -m app.export_groups сервиса расписания. Повторный импорт безопасен.'),
          file, area, h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => {
            let body; try { body = JSON.parse(area.value); } catch { showError(new ApiError('Это не JSON.')); return; }
            runTool('import-groups', body);
          } }, 'Импортировать'))),
        h('section', { class: 'card stack' }, h('h2', {}, 'Тестовый вуз'),
          h('p', { class: 'muted m0' }, 'Создаёт «Тестовый университет» с расписанием и добавляет выбранного пользователя студентом. Повторный запуск ничего не дублирует.'),
          field('Пользователь', demoPicker), h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => {
            if (!demoUser) { showError(new ApiError('Выберите пользователя из списка.')); return; }
            runTool('seed-demo', { user_id: demoUser.id });
          } }, 'Создать')))),
      h('section', { class: 'card' }, h('h2', {}, 'Где остальные команды'), h('dl', { class: 'meta' },
        h('dt', {}, 'grant/revoke-platform-role'), h('dd', {}, 'Пользователи → «Сделать сотрудником поддержки»'),
        h('dt', {}, 'assign-owner'), h('dd', {}, 'Вузы → вуз → Обзор → «Владельцы»'),
        h('dt', {}, 'install-people / install-schedule'), h('dd', {}, 'Вузы → вуз → Обзор → «Сервисы платформы»'),
        h('dt', {}, 'list-institutions'), h('dd', {}, 'Раздел «Вузы»'),
        h('dt', {}, 'seed_demo.py'), h('dd', {}, 'Эта страница → «Тестовый вуз»'))));
  }

  // ------------------------------------------------------------------
  // Вход и запуск
  // ------------------------------------------------------------------
  function showLogin() { $('app').hidden = true; $('login').hidden = false; $('password').focus(); }
  function showApp() { $('login').hidden = true; $('app').hidden = false; render(); }

  $('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    $('login-error').textContent = '';
    try { await api('/login', { method: 'POST', body: { password: $('password').value } }); $('password').value = ''; showApp(); }
    catch (error) { $('login-error').textContent = error.message; }
  });
  $('logout').addEventListener('click', async () => { await api('/logout', { method: 'POST' }).catch(() => {}); showLogin(); });
  window.addEventListener('hashchange', () => { clearError(); render(); window.scrollTo(0, 0); });

  (async () => {
    const s = await get('/session').catch(() => ({ authenticated: false, configured: true }));
    if (!s.configured) $('login-error').textContent = 'Пароль оператора не задан: добавьте OPERATOR_PASSWORD (от 12 символов) в .env и перезапустите operator.';
    if (s.authenticated) showApp(); else showLogin();
  })();
})();
