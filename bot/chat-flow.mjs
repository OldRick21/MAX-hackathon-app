const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const shortLabel = (value) => String(value || '').slice(0, 80) || 'Без названия';

export function chatView(response, selection = '') {
  const institutions = Array.isArray(response?.institutions) ? response.institutions : [];
  const groups = institutions.flatMap((institution) =>
    (Array.isArray(institution.groups) ? institution.groups : []).map((group) => ({ institution, group })));

  if (selection.startsWith('institution:')) {
    const id = selection.slice('institution:'.length);
    const institution = UUID.test(id) ? institutions.find((item) => item.id === id) : null;
    if (!institution) return { text: 'Этот вуз или доступ к нему больше недоступен.', buttons: [] };
    return {
      text: `Выберите учебную группу в «${shortLabel(institution.name)}»:`,
      buttons: institution.groups.map((group) => ({
        kind: 'callback', text: shortLabel(group.name), payload: `group:${group.id}`,
      })),
    };
  }

  if (selection.startsWith('group:')) {
    const id = selection.slice('group:'.length);
    const found = UUID.test(id) ? groups.find((item) => item.group.id === id) : null;
    if (!found) return { text: 'Этот чат или доступ к нему больше недоступен.', buttons: [] };
    return {
      text: `Чат учебной группы «${shortLabel(found.group.name)}» в «${shortLabel(found.institution.name)}»:`,
      buttons: [{ kind: 'link', text: 'Перейти в чат', url: found.group.chat_url }],
    };
  }

  if (!groups.length) {
    return { text: 'Для ваших учебных групп пока нет доступных чатов.', buttons: [] };
  }
  if (groups.length === 1) {
    const found = groups[0];
    return {
      text: `Чат учебной группы «${shortLabel(found.group.name)}» в «${shortLabel(found.institution.name)}»:`,
      buttons: [{ kind: 'link', text: 'Перейти в чат', url: found.group.chat_url }],
    };
  }
  if (institutions.length === 1) {
    return {
      text: `Выберите учебную группу в «${shortLabel(institutions[0].name)}»:`,
      buttons: institutions[0].groups.map((group) => ({
        kind: 'callback', text: shortLabel(group.name), payload: `group:${group.id}`,
      })),
    };
  }
  return {
    text: 'Выберите вуз:',
    buttons: institutions.map((institution) => ({
      kind: 'callback', text: shortLabel(institution.name), payload: `institution:${institution.id}`,
    })),
  };
}
