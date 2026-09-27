import { ApiError, isUUID } from './core/api.js?v=9ff99b1c478b';

// Заявки на подключение вуза и консоль поддержки платформы.
// Работает в оболочке ядра: поддержка не привязана к конкретному вузу,
// поэтому это не iframe сервиса администрирования.

const APP_STATUS = { pending: 'На рассмотрении', approved: 'Одобрена', rejected: 'Отклонена', withdrawn: 'Отозвана' };
const INST_STATUS = { active: 'Активен', pending: 'Ожидает', suspended: 'Приостановлен' };

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'class') node.className = value;
    else if (typeof value === 'boolean' || typeof value === 'number') node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
const date = (v) => (v ? new Date(v).toLocaleString('ru-RU') : '—');
const field = (label, input) => el('label', { class: 'form-field' }, el('span', {}, label), input);
const titleOf = (t) => (t && (t.ru || t.en)) || '—';

export function createPlatform({ core, host, setHeader, setStatus, showError, clearError, onChanged }) {
  let isStaff = false;
  let generation = 0;

  async function detect() {
    try {
      const me = await core.call('/api/v1/platform/me');
      isStaff = Array.isArray(me.roles) && me.roles.includes('platform_support');
    } catch { isStaff = false; }
    return isStaff;
  }

  async function run(fn) {
    clearError();
    try { await fn(); } catch (error) { if (error?.name !== 'AbortError') showError(error); }
  }

  // --------------------------------------------------------------
  // Пользователь: мои заявки
  // --------------------------------------------------------------
  async function showApplications() {
    const my = ++generation;
    setHeader('ПОДКЛЮЧЕНИЕ ВУЗА', 'Заявка на подключение');
    setStatus('Загружаем ваши заявки…');
    host.replaceChildren();
    const result = await core.call('/api/v1/institution-applications');
    if (my !== generation) return;
    const ru = el('input', { maxlength: '200', placeholder: 'Полное название вуза' });
    const en = el('input', { maxlength: '200', placeholder: 'Необязательно' });
    const contact = el('input', { maxlength: '200', placeholder: 'Email или телефон для связи' });
    const comment = el('textarea', { rows: 3, maxlength: '1000', placeholder: 'Должность, подтверждающие сведения' });
    const form = el('section', { class: 'panel' },
      el('h2', {}, 'Новая заявка'),
      el('p', { class: 'muted' }, 'После одобрения поддержкой платформы вуз будет создан, а вы станете его владельцем: получите профиль администратора и полный доступ к сервису «Администрирование».'),
      field('Название (рус.)', ru), field('Название (англ.)', en), field('Контакт', contact), field('Комментарий', comment),
      el('button', { class: 'primary', onclick: () => run(async () => {
        const titles = { ru: ru.value.trim() };
        if (en.value.trim()) titles.en = en.value.trim();
        await core.call('/api/v1/institution-applications', { method: 'POST',
          body: { titles, contact: contact.value.trim(), comment: comment.value.trim(), default_locale: 'ru' } });
        await showApplications();
        setStatus('Заявка отправлена. Решение появится здесь.');
      }) }, 'Отправить заявку'));
    const list = el('section', { class: 'panel' }, el('h2', {}, 'Мои заявки'));
    for (const app of result.items || []) {
      list.append(el('div', { class: 'list-row' },
        el('strong', {}, titleOf(app.titles)), el('span', { class: 'badge' }, APP_STATUS[app.status] || app.status),
        el('small', { class: 'muted' }, `Отправлена ${date(app.created_at)}`),
        app.decision_reason ? el('p', {}, `Причина: ${app.decision_reason}`) : null,
        app.status === 'approved' ? el('p', { class: 'muted' }, 'Вуз подключён: выберите его в списке «Мои вузы».') : null,
        app.status === 'pending' ? el('button', { class: 'quiet', onclick: () => run(async () => {
          await core.call(`/api/v1/institution-applications/${app.id}/withdraw`, { method: 'POST' });
          await showApplications();
        }) }, 'Отозвать') : null));
    }
    if (!(result.items || []).length) list.append(el('p', { class: 'muted' }, 'Заявок пока нет.'));
    host.replaceChildren(form, list);
    setStatus('');
  }

  // --------------------------------------------------------------
  // Поддержка платформы
  // --------------------------------------------------------------
  let supportTab = 'applications';

  async function showSupport(tab = supportTab) {
    if (!isStaff) return;
    supportTab = tab;
    const my = ++generation;
    setHeader('ПОДДЕРЖКА ПЛАТФОРМЫ', 'Управление платформой');
    const tabs = el('nav', { class: 'pill-tabs' }, [['applications', 'Заявки'], ['institutions', 'Вузы'], ['audit', 'Журнал']]
      .map(([id, label]) => el('button', { class: id === tab ? 'pill active' : 'pill', onclick: () => run(() => showSupport(id)) }, label)));
    const body = el('div', {});
    host.replaceChildren(tabs, body);
    setStatus('Загрузка…');
    if (tab === 'applications') await renderApplications(body, my);
    else if (tab === 'institutions') await renderInstitutions(body, my);
    else await renderAudit(body, my);
    if (my === generation) setStatus('');
  }

  async function renderApplications(body, my) {
    const data = await core.call('/api/v1/platform/applications?status=pending&limit=100');
    if (my !== generation) return;
    body.append(el('p', { class: 'muted' }, 'Заявки на рассмотрении. При одобрении создаётся вуз с защищённым сервисом администрирования, заявитель становится владельцем.'));
    for (const app of data.items) {
      const ru = el('input', { value: app.titles.ru || '' });
      const reason = el('input', { placeholder: 'Причина отказа (увидит заявитель)' });
      body.append(el('section', { class: 'panel' },
        el('h2', {}, titleOf(app.titles)),
        el('p', {}, `Контакт: ${app.contact || '—'}`),
        app.comment ? el('p', {}, `Комментарий: ${app.comment}`) : null,
        el('p', { class: 'muted' }, `Заявитель: ${app.applicant_user_id} · ${date(app.created_at)}`),
        field('Название при создании (можно исправить)', ru),
        el('div', { class: 'row-actions' },
          el('button', { class: 'primary', onclick: () => run(async () => {
            const titles = { ...app.titles, ru: ru.value.trim() };
            await core.call(`/api/v1/platform/applications/${app.id}/approve`, { method: 'POST', body: { titles } });
            setStatus('Заявка одобрена, вуз создан.'); onChanged?.();
            await showSupport('applications');
          }) }, 'Одобрить'),
          reason,
          el('button', { class: 'quiet', onclick: () => run(async () => {
            await core.call(`/api/v1/platform/applications/${app.id}/reject`, { method: 'POST', body: { reason: reason.value.trim() } });
            await showSupport('applications');
          }) }, 'Отклонить'))));
    }
    if (!data.items.length) body.append(el('p', { class: 'muted' }, 'Новых заявок нет.'));
  }

  async function renderInstitutions(body, my) {
    const data = await core.call('/api/v1/platform/institutions?limit=100');
    if (my !== generation) return;
    for (const inst of data.items) {
      const hosts = el('textarea', { rows: 2, placeholder: 'coursework.university.ru — по одному в строке' });
      hosts.value = (inst.local_hosts || []).join('\n');
      const ownerId = el('input', { placeholder: 'UUID пользователя' });
      const path = `/api/v1/platform/institutions/${inst.id}`;
      const withEtag = async (fn) => { const { etag } = await core.call(path, { meta: true }); return fn(etag); };
      body.append(el('section', { class: 'panel' },
        el('h2', {}, titleOf(inst.titles)), el('span', { class: 'badge' }, INST_STATUS[inst.status] || inst.status),
        el('p', { class: 'muted' }, `${inst.id} · участников: ${inst.members_count} · владельцев: ${inst.owners_count}`),
        el('div', { class: 'row-actions' },
          el('button', { class: 'quiet', onclick: () => run(async () => {
            const next = inst.status === 'active' ? 'suspended' : 'active';
            if (next === 'suspended' && !window.confirm('Приостановить вуз? Все сессии пользователей будут завершены.')) return;
            await withEtag((etag) => core.call(path, { method: 'PATCH', body: { status: next }, headers: { 'If-Match': etag } }));
            await showSupport('institutions');
          }) }, inst.status === 'active' ? 'Приостановить' : 'Активировать')),
        field('Одобренные хосты для локальных сервисов', hosts),
        el('button', { class: 'quiet', onclick: () => run(async () => {
          const hostnames = hosts.value.split(/\s+/).map(v => v.trim()).filter(Boolean);
          await withEtag((etag) => core.call(`${path}/local-hosts`, { method: 'PUT', body: { hostnames }, headers: { 'If-Match': etag } }));
          setStatus('Список хостов сохранён.');
          await showSupport('institutions');
        }) }, 'Сохранить хосты'),
        inst.owners_count === 0 ? el('div', {}, el('p', { class: 'muted' }, 'У вуза нет владельца. Назначьте первого владельца (он должен уже войти в приложение):'),
          ownerId, el('button', { class: 'primary', onclick: () => run(async () => {
            const userId = ownerId.value.trim().toLowerCase();
            if (!isUUID(userId)) throw new ApiError('Введите UUID пользователя.');
            await core.call(`${path}/initial-owner`, { method: 'POST', body: { user_id: userId } });
            await showSupport('institutions');
          }) }, 'Назначить владельца')) : null));
    }
    if (!data.items.length) body.append(el('p', { class: 'muted' }, 'Вузов пока нет.'));
  }

  async function renderAudit(body, my) {
    const data = await core.call('/api/v1/platform/audit?limit=100');
    if (my !== generation) return;
    const panel = el('section', { class: 'panel' }, el('h2', {}, 'Журнал платформы'));
    for (const e of data.items) {
      panel.append(el('div', { class: 'list-row' }, el('strong', {}, e.action),
        e.outcome === 'denied' ? el('span', { class: 'badge' }, `отклонено: ${e.error_code}`) : null,
        el('small', { class: 'muted' }, `${date(e.created_at)} · ${e.actor_kind} ${e.actor_user_id || ''} · ${e.target_id || ''}`)));
    }
    if (!data.items.length) panel.append(el('p', { class: 'muted' }, 'Записей нет.'));
    body.append(panel);
  }

  return { detect, showApplications, showSupport, get isStaff() { return isStaff; }, cancel() { generation += 1; } };
}
