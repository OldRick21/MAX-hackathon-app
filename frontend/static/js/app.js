import { ApiError, CoreSession, isUUID, isProfile, listAll } from './core/api.js?v=7eaa0b600c59';
import { ServiceFrame } from './core/service-frame.js?v=7eaa0b600c59';

const $ = (id) => document.getElementById(id);
const labels = { student: 'Студент', teacher: 'Преподаватель', admin: 'Администратор' };
const statuses = { active: 'Активен', pending: 'Ожидает одобрения', suspended: 'Доступ приостановлен' };
const state = { user: null, institution: null, profile: '', services: [], locale: 'ru', localeChosen: false,
  version: 0, controller: null, busy: false };
const core = new CoreSession(() => expire());
const frame = new ServiceFrame(core, $('frame-host'), (status) => {
  $('frame-status').textContent = { loading: 'Подключаем сервис…', ready: 'Сервис открыт', closed: 'Сервис закрыт' }[status];
}, (error) => { $('service-panel').hidden = true; showError(error); });

function showError(error) {
  if (error?.name === 'AbortError') return;
  $('notice').textContent = error instanceof ApiError ? error.message : 'Не удалось открыть данные. Попробуйте ещё раз.';
  $('notice').hidden = false;
}
function clearError() { $('notice').hidden = true; $('notice').textContent = ''; }
function begin() {
  state.controller?.abort();
  state.controller = new AbortController();
  state.version += 1;
  frame.close();
  $('service-panel').hidden = true;
  $('selection').replaceChildren();
  clearError();
  return { version: state.version, signal: state.controller.signal };
}
function current(task) { return task.version === state.version && !!core.pair; }
function route(institution = null, replace = false) {
  const path = institution ? `/institution/${institution.id}` : '/institution';
  // При локальном просмотре статических файлов сервер не обслуживает SPA routes.
  if (location.pathname.startsWith('/templates/')) return;
  if (location.pathname !== path) history[replace ? 'replaceState' : 'pushState']({}, '', path);
}
function text(tag, value, className = '') {
  const el = document.createElement(tag); el.textContent = value; el.className = className; return el;
}
function card(title, description, badge, action) {
  const el = document.createElement('button');
  el.className = 'card';
  el.append(text('span', badge, 'badge'), text('h2', title), text('p', description), text('span', 'Открыть →', 'arrow'));
  el.addEventListener('click', action);
  return el;
}
function clearMenus() { $('services').replaceChildren(text('p', 'Выберите вуз и профиль', 'muted')); }
function setProfileOptions(items) {
  $('profile').replaceChildren(new Option('Выберите профиль', ''));
  for (const p of items) $('profile').add(new Option(labels[p], p));
  $('profile').disabled = items.length === 0;
  $('profile').value = state.profile;
}
function expire() {
  begin();
  state.user = null; state.institution = null; state.profile = ''; state.services = [];
  $('workspace').hidden = true; $('entry').hidden = false; $('logout').hidden = true;
  $('user-id').textContent = '';
  $('entry-status').textContent = 'Сессия завершена. Закройте мини-приложение и откройте его заново через MAX.';
  $('login').hidden = true;
}

async function listInstitutions(autoSelect = false, desiredId = '') {
  const task = begin();
  state.institution = null; state.profile = ''; state.services = [];
  clearMenus(); setProfileOptions([]);
  $('change-institution').textContent = 'Выбрать вуз ↗'; $('institution-status').textContent = '';
  $('page-title').textContent = 'Мои вузы'; $('breadcrumb').textContent = 'ЛИЧНОЕ ПРОСТРАНСТВО';
  $('page-status').textContent = 'Загружаем ваши вузы…';
  try {
    const items = await listAll(core, '/api/v1/institution', { locale: state.locale }, task.signal);
    if (!current(task)) return;
    if (desiredId && items.some(i => i.id === desiredId)) return chooseInstitution(desiredId, true);
    if (autoSelect && items.length === 1) return chooseInstitution(items[0].id, true);
    route(null, autoSelect);
    $('page-status').textContent = items.length ? 'Выберите вуз, в котором хотите работать.' : 'Вы пока не добавлены ни в один вуз. Передайте свой ID администратору.';
    for (const item of items) {
      $('selection').append(card(item.display_name, (item.profiles || []).map(p => labels[p]).filter(Boolean).join(' · '),
        statuses[item.status] || 'Недоступен', () => chooseInstitution(item.id)));
    }
  } catch (error) { if (current(task)) { $('page-status').textContent = 'Не удалось загрузить список вузов.'; showError(error); } }
}

async function chooseInstitution(id, replace = false) {
  if (!isUUID(id)) return;
  const task = begin();
  state.institution = null; state.profile = ''; state.services = [];
  setProfileOptions([]); clearMenus();
  $('page-status').textContent = 'Загружаем вуз и ваши профили…';
  try {
    const institution = await core.call(`/api/v1/institution/${id}?locale=${state.locale}`, { signal: task.signal });
    if (!current(task)) return;
    if (institution.id !== id) throw new ApiError('Сервер вернул другой вуз.');
    state.institution = institution;
    if (!state.localeChosen && ['ru', 'en'].includes(institution.default_locale)) {
      state.locale = institution.default_locale; $('locale').value = state.locale;
    }
    route(institution, replace);
    $('change-institution').textContent = `${institution.display_name} ↗`;
    $('institution-status').textContent = statuses[institution.status] || 'Недоступен';
    $('page-title').textContent = institution.display_name; $('breadcrumb').textContent = 'ВАШ ВУЗ';
    if (institution.status !== 'active') {
      $('page-status').textContent = institution.status === 'pending' ? 'Вуз ожидает одобрения платформы. Сервисы пока недоступны.' : 'Доступ к сервисам вуза приостановлен.';
      return;
    }
    const result = await core.call(`/api/v1/institution/${id}/profiles`, { signal: task.signal });
    if (!current(task)) return;
    if (result.institution_id !== id || result.user_id !== state.user.id || !Array.isArray(result.profiles) || !result.profiles.every(isProfile)) {
      throw new ApiError('Некорректный список профилей.');
    }
    const available = [...new Set(result.profiles)];
    setProfileOptions(available);
    if (available.length === 1) return chooseProfile(available[0]);
    $('page-status').textContent = available.length ? 'Под каким профилем вы хотите работать?' : 'Нет назначенных профилей. Обратитесь к администратору вуза.';
    for (const profile of available) $('selection').append(card(labels[profile], 'Доступные сервисы зависят от выбранного профиля.', 'Профиль', () => chooseProfile(profile)));
  } catch (error) { if (current(task)) { $('page-status').textContent = 'Не удалось загрузить вуз.'; showError(error); } }
}

function renderServices(items) {
  $('services').replaceChildren();
  $('selection').replaceChildren();
  for (const service of items) {
    const menus = [...service.menus].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
    if (!menus.length) continue;
    $('services').append(text('div', service.display_name, 'service-group'));
    for (const menu of menus) {
      const button = text('button', menu.display_name, 'menu-button');
      button.dataset.service = service.id; button.dataset.menu = menu.id;
      button.addEventListener('click', () => openService(service.id, menu.id));
      $('services').append(button);
      $('selection').append(card(menu.display_name, service.display_name, labels[state.profile], () => openService(service.id, menu.id)));
    }
  }
  $('page-status').textContent = $('selection').children.length ? 'Выберите сервис для работы в этом профиле.' : 'Для этого профиля пока нет доступных сервисов.';
}

async function chooseProfile(profile) {
  if (!state.institution || !isProfile(profile) || ![...$('profile').options].some(o => o.value === profile)) return;
  const task = begin();
  state.profile = profile; state.services = [];
  $('profile').value = profile;
  $('services').replaceChildren(text('p', 'Загружаем сервисы…', 'muted'));
  $('page-title').textContent = 'Сервисы'; $('breadcrumb').textContent = `${state.institution.display_name} / ${labels[profile]}`;
  $('page-status').textContent = 'Загружаем доступные сервисы…';
  const id = state.institution.id;
  try {
    const items = await listAll(core, `/api/v1/institution/${id}/service`, { profile, locale: state.locale }, task.signal);
    if (!current(task)) return;
    if (items.some(s => s.institution_id !== id || s.profile !== profile || !Array.isArray(s.menus))) throw new ApiError('Каталог содержит сервис другого контекста.');
    state.services = items;
    renderServices(items);
  } catch (error) { if (current(task)) { $('services').replaceChildren(); $('page-status').textContent = 'Не удалось загрузить сервисы.'; showError(error); } }
}

async function openService(id, menuId) {
  const institution = state.institution; const profile = state.profile;
  if (!institution || !profile || !isUUID(id)) return;
  const task = begin();
  $('page-status').textContent = 'Проверяем доступ и открываем сервис…';
  try {
    const service = await core.call(`/api/v1/institution/${institution.id}/service/${id}?profile=${profile}&locale=${state.locale}`, { signal: task.signal });
    if (!current(task)) return;
    if (service.id !== id || service.institution_id !== institution.id || service.profile !== profile || !Array.isArray(service.menus)) throw new ApiError('Контекст сервиса изменился. Обновите каталог.');
    const menu = service.menus.find(m => m.id === menuId);
    if (!menu) throw new ApiError('Это меню больше недоступно. Обновите каталог.');
    $('page-title').textContent = menu.display_name;
    $('service-panel').hidden = false;
    await frame.open(service, menu, state.locale);
    if (!current(task)) return;
    $('page-status').textContent = service.display_name;
    for (const button of $('services').querySelectorAll('button')) {
      if (button.dataset.service === id && button.dataset.menu === menuId) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    }
  } catch (error) { if (current(task)) { $('service-panel').hidden = true; $('page-status').textContent = 'Сервис не открыт. Выберите другой или повторите попытку.'; showError(error); } }
}

async function login() {
  if (state.busy) return;
  state.busy = true; $('login').hidden = true; clearError();
  try {
    const bridge = window.WebApp;
    if (typeof bridge?.initData !== 'string' || !bridge.initData.trim()) {
      $('entry').querySelector('h1').textContent = 'Откройте приложение в MAX';
      $('entry-description').textContent = 'Вход в сервисы вуза доступен через мини-приложение бота MAX.';
      $('locale').disabled = true;
      $('entry-status').textContent = bridge ? 'Откройте мини-приложение кнопкой бота в MAX. В браузере данные для входа отсутствуют.' : 'Не удалось получить данные MAX. Откройте мини-приложение через бота и проверьте соединение.';
      return;
    }
    $('entry-status').textContent = 'Проверяем данные входа…';
    await core.login(bridge.initData);
    state.user = await core.call('/api/v1/auth/me');
    if (!isUUID(state.user.id)) throw new ApiError('Сервер вернул некорректный аккаунт.');
    $('user-id').textContent = state.user.id;
    $('entry').hidden = true; $('workspace').hidden = false; $('logout').hidden = false;
    const requestedId = location.pathname.match(/^\/institution\/([0-9a-f-]+)$/i)?.[1] || '';
    await listInstitutions(true, requestedId);
  } catch (error) {
    core.clear();
    $('entry-status').textContent = 'Вход не выполнен. При истёкших данных заново откройте мини-приложение.';
    $('login').hidden = false; showError(error);
  } finally { state.busy = false; }
}

$('login').addEventListener('click', login);
$('home').addEventListener('click', (event) => { event.preventDefault(); if (core.pair) void listInstitutions(); });
$('change-institution').addEventListener('click', () => listInstitutions());
$('profile').addEventListener('change', () => {
  if ($('profile').value) void chooseProfile($('profile').value);
  else if (state.institution) void chooseInstitution(state.institution.id, true);
});
$('refresh').addEventListener('click', () => state.institution ? chooseInstitution(state.institution.id, true) : listInstitutions());
$('close-service').addEventListener('click', () => chooseProfile(state.profile));
$('locale').addEventListener('change', () => {
  state.locale = $('locale').value; state.localeChosen = true;
  if (core.pair) void (state.profile ? chooseProfile(state.profile) : state.institution ? chooseInstitution(state.institution.id, true) : listInstitutions());
});
$('logout').addEventListener('click', async () => {
  $('logout').disabled = true;
  begin();
  try { await core.logout(); expire(); $('entry-status').textContent = 'Вы вышли. Для нового входа откройте приложение через бота.'; }
  catch { expire(); showError(new ApiError('Данные входа удалены с устройства, но сервер не подтвердил выход.')); }
  finally { $('logout').disabled = false; }
});
window.addEventListener('popstate', () => {
  if (!core.pair) return;
  const id = location.pathname.match(/^\/institution\/([0-9a-f-]+)$/i)?.[1];
  if (id && isUUID(id)) void chooseInstitution(id, true); else void listInstitutions();
});
window.addEventListener('pagehide', () => { frame.close(); core.clear(); });
window.addEventListener('pageshow', (event) => { if (event.persisted) expire(); });
// defer у MAX Bridge завершается к DOMContentLoaded; module может исполниться раньше него.
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', login, { once: true });
else void login();
