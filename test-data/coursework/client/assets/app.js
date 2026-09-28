// Интерфейс курсовых внутри приложения. Всё общение с платформой — через ServiceSDK (sdk-client.js).
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const STATUS = { submitted: ['на проверке', ''], changes_requested: ['нужна доработка', 'warn'], accepted: ['принята', 'ok'] };

  function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (k === 'class') el.className = v;
      else if (k in el && typeof v !== 'string') el[k] = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) el.append(c instanceof Node ? c : String(c));
    return el;
  }
  const field = (label, input, hint) => h('label', { class: 'field' }, h('span', {}, label), input, hint ? h('small', { class: 'muted' }, hint) : null);
  const say = (text, error = false) => { $('status').textContent = text; $('status').className = error ? 'error' : 'muted'; };

  function dialog(title, body, ok = 'OK') {
    return new Promise(resolve => {
      const d = $('dialog');
      $('dialog-title').textContent = title;
      $('dialog-body').replaceChildren(...[].concat(body));
      $('dialog-ok').textContent = ok;
      d.onclose = () => resolve(d.returnValue === 'ok');
      d.showModal();
    });
  }

  async function guarded(fn) {
    try { await fn(); } catch (e) { say(e.message || 'Что-то пошло не так', true); }
  }

  let me = null;

  async function start() {
    const ctx = await ServiceSDK.ready;
    ServiceSDK.onEnd(() => { $('root').replaceChildren(); say('Сессия завершена. Откройте сервис заново.', true); });
    me = (await ServiceSDK.api('/coursework/me')).data;
    // Имя нужно, чтобы студенты видели проверяющего, а преподаватель — автора работы.
    if (!me.display_name && ctx.profile !== 'admin') return askName();
    render();
  }

  function askName() {
    const input = h('input', { placeholder: 'Например, Иванова Анна Сергеевна', maxLength: 200 });
    say('');
    $('root').replaceChildren(h('section', { class: 'card' }, h('h2', {}, 'Как вас показывать?'),
      h('p', { class: 'muted' }, me.profile === 'teacher'
        ? 'Под этим именем студенты выберут вас проверяющим.' : 'Это имя увидит преподаватель в вашей работе.'),
      field('Имя и фамилия', input),
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => guarded(async () => {
        if (!input.value.trim()) throw new Error('Укажите имя.');
        me = (await ServiceSDK.api('/coursework/me', { method: 'PUT', body: { display_name: input.value.trim() } })).data;
        render();
      }) }, 'Сохранить'))));
  }

  async function render() {
    say('Загружаем…');
    const { data } = await ServiceSDK.api('/coursework/submissions?limit=100');
    const people = data.people || {};
    const who = id => people[id] || `без имени · ${id.slice(0, 8)}`;
    const parts = [];
    if (me.profile === 'student') parts.push(await uploadForm());
    const list = h('section', { class: 'card' }, h('h2', {}, me.profile === 'teacher' ? 'Работы на проверку' : me.manages ? 'Все работы вуза' : 'Мои работы'));
    if (!data.items.length) list.append(h('p', { class: 'muted' }, 'Работ пока нет.'));
    for (const w of data.items) list.append(workRow(w, who));
    parts.push(list);
    $('root').replaceChildren(...parts);
    say(me.display_name ? `Вы: ${me.display_name}` : '');
  }

  function workRow(w, who) {
    const [label, tone] = STATUS[w.status] || [w.status, ''];
    const actions = h('div', { class: 'actions' }, h('button', { onclick: () => guarded(() => download(w)) }, 'Скачать PDF'));
    if (me.profile === 'teacher' && w.status === 'submitted') actions.append(h('button', { class: 'primary', onclick: () => guarded(() => reviewWork(w)) }, 'Проверить'));
    if (me.profile === 'student' && w.status !== 'accepted') {
      actions.append(h('button', { onclick: () => guarded(() => replaceFile(w)) }, 'Новая версия'),
        h('button', { class: 'danger', onclick: () => guarded(() => removeWork(w)) }, 'Удалить'));
    }
    if (me.manages) actions.append(h('button', { class: 'danger', onclick: () => guarded(() => removeWork(w)) }, 'Удалить'));
    return h('div', { class: 'row' },
      h('div', { class: 'row-main' }, h('strong', {}, w.title), h('span', { class: `badge ${tone}` }, label), h('span', { class: 'badge' }, `версия ${w.version}`)),
      h('small', { class: 'muted' }, me.profile === 'student' ? `Проверяет: ${who(w.teacher_id)}` : `Автор: ${who(w.student_id)}` + (me.manages ? ` · проверяет: ${who(w.teacher_id)}` : '')),
      w.review ? h('small', {}, `Отзыв: ${w.review.comment || (w.review.decision === 'accepted' ? 'принята' : 'без комментария')}`) : null,
      actions);
  }

  async function uploadForm() {
    const { data } = await ServiceSDK.api('/coursework/teachers');
    const title = h('input', { maxLength: 200, placeholder: 'Тема работы' });
    const teacher = h('select', {}, h('option', { value: '' }, data.items.length ? 'Выберите преподавателя' : 'Преподаватели ещё не открывали сервис'),
      data.items.map(t => h('option', { value: t.user_id }, t.display_name)));
    const file = h('input', { type: 'file', accept: 'application/pdf,.pdf' });
    return h('section', { class: 'card' }, h('h2', {}, 'Отправить работу'),
      field('Тема', title), field('Проверяющий', teacher), field('Файл PDF', file, 'До 20 МБ'),
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => guarded(async () => {
        if (!title.value.trim() || !teacher.value || !file.files[0]) throw new Error('Заполните тему, проверяющего и выберите PDF.');
        const form = new FormData();
        form.set('title', title.value.trim());
        form.set('teacher_id', teacher.value);
        form.set('file', file.files[0], file.files[0].name);
        say('Загружаем файл…');
        await ServiceSDK.raw('/coursework/submissions', { method: 'POST', body: form, headers: { 'Idempotency-Key': crypto.randomUUID() } });
        await render();
        say('Работа отправлена на проверку.');
      }) }, 'Отправить')));
  }

  async function download(w) {
    const res = await ServiceSDK.raw(`/coursework/submissions/${w.id}/file`);
    const url = URL.createObjectURL(await res.blob());
    const a = h('a', { href: url, download: w.file.original_name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }

  async function reviewWork(w) {
    const decision = h('select', {}, h('option', { value: 'accepted' }, 'Принять'), h('option', { value: 'changes_requested' }, 'Вернуть на доработку'));
    const comment = h('textarea', { rows: 4, maxLength: 2000 });
    if (!await dialog(`Проверка: ${w.title}`, [field('Решение', decision), field('Комментарий', comment)], 'Сохранить')) return;
    const { etag } = await ServiceSDK.api(`/coursework/submissions/${w.id}`);
    await ServiceSDK.api(`/coursework/submissions/${w.id}/review`, { method: 'PUT', etag, body: { decision: decision.value, comment: comment.value.trim() } });
    await render();
  }

  async function replaceFile(w) {
    const file = h('input', { type: 'file', accept: 'application/pdf,.pdf' });
    if (!await dialog('Новая версия работы', field('Файл PDF', file, 'Прежний отзыв сбросится, работа снова уйдёт на проверку.'), 'Загрузить') || !file.files[0]) return;
    const { etag } = await ServiceSDK.api(`/coursework/submissions/${w.id}`);
    const form = new FormData();
    form.set('file', file.files[0], file.files[0].name);
    await ServiceSDK.raw(`/coursework/submissions/${w.id}/file`, { method: 'PUT', body: form, headers: { 'If-Match': etag } });
    await render();
  }

  async function removeWork(w) {
    if (!await dialog('Удалить работу?', h('p', {}, `«${w.title}» пропадёт из списка.`), 'Удалить')) return;
    const { etag } = await ServiceSDK.api(`/coursework/submissions/${w.id}`);
    await ServiceSDK.api(`/coursework/submissions/${w.id}`, { method: 'DELETE', etag });
    await render();
  }

  start().catch(e => say(e.message || 'Не удалось открыть сервис', true));
})();
