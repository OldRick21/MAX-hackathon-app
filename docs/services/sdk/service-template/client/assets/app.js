// TODO: интерфейс сервиса. Пока — проверка подключения через /api/v1/whoami.
(async () => {
  const status = document.getElementById('status');
  ServiceSDK.onEnd(() => { status.textContent = 'Сессия завершена. Откройте сервис заново.'; });
  const ctx = await ServiceSDK.ready;
  try {
    const { data } = await ServiceSDK.api('/whoami');
    status.textContent = `Сервис подключён. Профиль: ${data.profile}.`;
    // document.getElementById('root') — сюда рисуйте свой интерфейс (ctx.profile, ctx.locale).
    void ctx;
  } catch (e) {
    status.textContent = e.message;
  }
})();
