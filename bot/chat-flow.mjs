const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const shortLabel = (value) => String(value || '').slice(0, 80) || 'Без названия';

export function chatView(response, selection = '') {
  const institutions = Array.isArray(response?.institutions) ? response.institutions : [];
  const groups = institutions.flatMap((institution) =>
    (Array.isArray(institution.groups) ? institution.groups : []).map((group) => ({ institution, group })));

  if (selection.startsWith('institution:')) {
    const id = selection.slice('institution:'.length);
    const institution = UUID.test(id) ? institutions.find((item) => item.id === id) : null;
    if (!institution) return { text: 'Учебная организация недоступна или доступ к ней изменился.', buttons: [] };
    return {
      text: `Учебная организация\n${shortLabel(institution.name)}\n\nВыберите группу.`,
      buttons: institution.groups.map((group) => ({
        kind: 'callback', text: shortLabel(group.name), payload: `group:${group.id}`,
      })),
    };
  }

  if (selection.startsWith('group:')) {
    const id = selection.slice('group:'.length);
    const found = UUID.test(id) ? groups.find((item) => item.group.id === id) : null;
    if (!found) return { text: 'Чат недоступен или доступ к нему изменился.', buttons: [] };
    return {
      text: `Чат учебной группы\n${shortLabel(found.group.name)} · ${shortLabel(found.institution.name)}\n\nИспользуйте кнопку ниже, чтобы перейти в чат.`,
      buttons: [{ kind: 'link', text: 'Открыть чат', url: found.group.chat_url }],
    };
  }

  if (!groups.length) {
    return { text: 'Для ваших учебных групп пока нет доступных чатов. Если это ошибка, обратитесь к администратору организации.', buttons: [] };
  }
  if (groups.length === 1) {
    const found = groups[0];
    return {
      text: `Чат учебной группы\n${shortLabel(found.group.name)} · ${shortLabel(found.institution.name)}\n\nИспользуйте кнопку ниже, чтобы перейти в чат.`,
      buttons: [{ kind: 'link', text: 'Открыть чат', url: found.group.chat_url }],
    };
  }
  if (institutions.length === 1) {
    return {
      text: `Учебная организация\n${shortLabel(institutions[0].name)}\n\nВыберите группу.`,
      buttons: institutions[0].groups.map((group) => ({
        kind: 'callback', text: shortLabel(group.name), payload: `group:${group.id}`,
      })),
    };
  }
  return {
    text: 'Выберите учебную организацию.',
    buttons: institutions.map((institution) => ({
      kind: 'callback', text: shortLabel(institution.name), payload: `institution:${institution.id}`,
    })),
  };
}
