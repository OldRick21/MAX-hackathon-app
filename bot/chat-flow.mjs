const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const shortLabel = (value) => String(value || '').slice(0, 80) || 'Без названия';

// Кнопки навигации. payload без префикса study-chat: — его добавляет bot.mjs.
export const MENU = { kind: 'callback', text: 'Главное меню', payload: 'menu' };
const back = (payload) => ({ kind: 'callback', text: '← Назад', payload });
// «Назад» и, если он ведёт не в меню, ещё и «Главное меню» — одним рядом.
const nav = (payload) => (payload === 'menu' ? [[back('menu')]] : [[back(payload), MENU]]);

// Короткие названия групп (ИВТ-212) — по две в ряд, длинные — по одной.
function pairs(buttons) {
  const rows = [];
  for (const button of buttons) {
    const last = rows[rows.length - 1];
    if (last && last.length === 1 && button.text.length <= 14 && last[0].text.length <= 14) last.push(button);
    else rows.push([button]);
  }
  return rows;
}

export const menuView = () => ({
  text: 'Добро пожаловать в «Вузы России».\n\nОткройте учебную платформу или перейдите в чат своей группы.',
  rows: [
    [{ kind: 'callback', text: 'Чат моей группы', payload: 'open' }],
    [{ kind: 'callback', text: 'Как это работает', payload: 'help' }],
  ],
});

export const helpView = () => ({
  text: 'Как это работает\n\n'
    + '1. Нажмите «Открыть платформу» и выберите свой вуз.\n'
    + '2. Отправьте заявку: студент выбирает группу, преподаватель — профиль.\n'
    + '3. После одобрения администратором вуза откроются расписание, «Люди» и другие сервисы.\n\n'
    + 'Чат группы появляется здесь, когда администратор вуза добавит на него ссылку.',
  rows: nav('menu'),
});

export const errorView = (selection = '') => ({
  text: 'Сервис временно недоступен. Попробуйте ещё раз немного позже.',
  rows: [[{ kind: 'callback', text: '↻ Повторить', payload: selection || 'open' }], [MENU]],
});

export function chatView(response, selection = '') {
  const institutions = (Array.isArray(response?.institutions) ? response.institutions : [])
    .map((institution) => ({ ...institution, groups: Array.isArray(institution.groups) ? institution.groups : [] }))
    .filter((institution) => institution.groups.length);
  const groups = institutions.flatMap((institution) => institution.groups.map((group) => ({ institution, group })));

  const stale = (text) => ({ text, rows: [[{ kind: 'callback', text: '↻ Обновить список', payload: 'open' }], [MENU]] });

  const institutionView = (institution) => ({
    text: `Учебная организация\n${shortLabel(institution.name)}\n\nВыберите группу.`,
    rows: [
      ...pairs(institution.groups.map((group) => ({
        kind: 'callback', text: shortLabel(group.name), payload: `group:${group.id}`,
      }))),
      ...nav(institutions.length > 1 ? 'open' : 'menu'),
    ],
  });

  const groupView = ({ institution, group }) => {
    // Назад — туда, откуда пришли: к группам вуза, к списку вузов или в меню, если выбирать было не из чего.
    const target = institution.groups.length > 1 ? `institution:${institution.id}`
      : institutions.length > 1 ? 'open' : 'menu';
    return {
      text: `Чат учебной группы\n${shortLabel(group.name)} · ${shortLabel(institution.name)}\n\nСсылка на чат: ${group.chat_url}`,
      rows: [[{ kind: 'link', text: 'Открыть чат', url: group.chat_url }], ...nav(target)],
    };
  };

  if (selection.startsWith('institution:')) {
    const id = selection.slice('institution:'.length);
    const institution = UUID.test(id) ? institutions.find((item) => item.id === id) : null;
    return institution ? institutionView(institution) : stale('Учебная организация недоступна или доступ к ней изменился.');
  }

  if (selection.startsWith('group:')) {
    const id = selection.slice('group:'.length);
    const found = UUID.test(id) ? groups.find((item) => item.group.id === id) : null;
    return found ? groupView(found) : stale('Чат недоступен или доступ к нему изменился.');
  }

  if (!groups.length) {
    return {
      text: 'Для ваших учебных групп пока нет доступных чатов.\n\nЕсли это ошибка, обратитесь к администратору своего вуза.',
      rows: nav('menu'),
    };
  }
  if (groups.length === 1) return groupView(groups[0]);
  if (institutions.length === 1) return institutionView(institutions[0]);
  return {
    text: 'Выберите учебную организацию.',
    rows: [
      ...institutions.map((institution) => [{
        kind: 'callback', text: shortLabel(institution.name), payload: `institution:${institution.id}`,
      }]),
      ...nav('menu'),
    ],
  };
}
