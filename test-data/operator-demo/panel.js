// Карточка модуля «Тестовые данные» для раздела «Обслуживание» пульта оператора.
// Пульт импортирует этот файл и вызывает default-функцию со своими помощниками интерфейса.
export default function testDataCard({ id, h, field, api, act, userPicker, confirmDanger }) {
  const count = h('input', { type: 'number', min: 1, max: 10, value: 3 });
  const students = h('input', { type: 'number', min: 3, max: 40, value: 15 });
  let me = null;
  const picker = userPicker(u => { me = u; });
  const state = h('p', { class: 'muted m0' }, 'Проверяем…');
  const card = h('section', { class: 'card stack' }, h('h2', {}, 'Тестовые данные'),
    h('p', { class: 'muted m0' }, 'Создаёт вузы с администрированием, «Людьми» и расписанием: 4 группы, 6 преподавателей, студенты и администратор, занятия на две недели. Удаление стирает только их — настоящие вузы и люди не затрагиваются.'),
    field('Сколько вузов', count, 'от 1 до 10'), field('Студентов в группе', students, 'от 3 до 40'),
    field('Добавить себя (необязательно)', picker, 'Получит все профили и роль владельца в тестовых вузах, чтобы посмотреть их изнутри.'),
    state,
    h('div', { class: 'actions' },
      h('button', { class: 'primary', onclick: () => act(async () => {
        const body = { institutions: Number(count.value), students: Number(students.value), ...(me ? { user_id: me.id } : {}) };
        const msg = (await api(`/plugins/${id}/create`, { method: 'POST', body })).data.message;
        poll();
        return msg;
      }) }, 'Создать тестовые данные'),
      h('button', { class: 'danger', onclick: async () => {
        if (!await confirmDanger('Удалить тестовые данные?', 'Будут удалены все тестовые вузы (с расписанием и данными сервисов) и тестовые пользователи. Настоящие вузы и люди не затрагиваются.', 'Удалить')) return;
        act(async () => (await api(`/plugins/${id}/delete`, { method: 'POST', body: {} })).data.message);
      } }, 'Удалить тестовые данные')));
  let timer = null;
  async function poll() {
    clearTimeout(timer);
    if (!card.isConnected && timer !== null) return;
    try {
      const s = (await api(`/plugins/${id}/status`)).data;
      state.textContent = `${s.message}${s.progress ? ` (${s.progress})` : ''}. Сейчас тестовых вузов: ${s.institutions}, пользователей: ${s.users}.`;
      state.className = s.state === 'error' ? 'error m0' : 'muted m0';
      if (s.state === 'running') timer = setTimeout(poll, 2000);
    } catch { state.textContent = 'Не удалось получить состояние.'; }
  }
  timer = 0;
  setTimeout(poll, 0);
  return card;
}

