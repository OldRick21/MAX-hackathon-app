// Клиент «Людей» в iframe (SDK §7): /home — своя анкета, /users — участники вуза.
(async () => {
  const status = document.getElementById('status');
  const root = document.getElementById('root');
  ServiceSDK.onEnd(() => { status.textContent = 'Сессия завершена. Откройте сервис заново.'; root.replaceChildren(); });
  await ServiceSDK.ready;
  const line = (text, muted) => Object.assign(document.createElement('p'), { textContent: text, className: muted ? 'muted' : '' });
  try {
    if (location.pathname === '/users') {
      const { data } = await ServiceSDK.api('/profile/users?limit=100');
      status.textContent = data.items.length ? 'Участники вуза' : 'Пока никого нет.';
      const list = document.createElement('ul');
      for (const c of data.items) {
        const li = document.createElement('li');
        li.append(line(c.display_name || 'Имя не указано'), line([c.position, c.academic_degree].filter(Boolean).join(' · '), true));
        list.append(li);
      }
      root.replaceChildren(list);
    } else {
      const { data } = await ServiceSDK.api('/profile/me');
      status.textContent = 'Моя анкета';
      root.replaceChildren(line(data.display_name || 'Имя не указано'),
        line([data.position, data.academic_degree].filter(Boolean).join(' · '), true), line(data.about || '', true));
    }
  } catch (e) {
    status.textContent = e.message;
  }
})();
