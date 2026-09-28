// Клиент расписания в iframe (SDK §7): занятия текущей недели в пределах прав профиля.
(async () => {
  const status = document.getElementById('status');
  const root = document.getElementById('root');
  ServiceSDK.onEnd(() => { status.textContent = 'Сессия завершена. Откройте сервис заново.'; root.replaceChildren(); });
  await ServiceSDK.ready;
  const monday = new Date(); monday.setHours(0, 0, 0, 0); monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
  const to = new Date(monday); to.setDate(to.getDate() + 7);
  try {
    const items = [];
    let cursor = null;
    do {
      const q = new URLSearchParams({ from: monday.toISOString(), to: to.toISOString(), limit: '100', ...(cursor ? { cursor } : {}) });
      const { data } = await ServiceSDK.api(`/schedule/events?${q}`);
      items.push(...data.items); cursor = data.next_cursor;
    } while (cursor);
    status.textContent = items.length ? 'Занятия этой недели' : 'На этой неделе занятий нет.';
    const fmt = new Intl.DateTimeFormat('ru-RU', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' });
    const list = document.createElement('ul');
    for (const e of items) {
      const li = document.createElement('li');
      li.textContent = `${fmt.format(new Date(e.starts_at))} — ${e.title}${e.location ? ', ' + e.location : ''}${e.status === 'cancelled' ? ' (отменено)' : ''}`;
      list.append(li);
    }
    root.replaceChildren(list);
  } catch (e) {
    status.textContent = e.message;
  }
})();
