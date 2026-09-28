// Данные запуска MAX из адреса (#WebAppData=...&WebAppPlatform=...). Модуль импортируется первым
// в main.tsx: фрагмент читается до того, как роутер или что-то ещё поменяет адрес.
// Нужен как запасной источник initData: на части Android-клиентов MAX Bridge подгружается
// позже или не заполняет WebApp.initData сразу.

function read(): string | null {
  try {
    const data = new URLSearchParams(window.location.hash.slice(1)).get('WebAppData');
    return data && data.trim() ? data : null;
  } catch {
    return null;
  }
}

export const launchInitData: string | null = read();
