document.addEventListener('DOMContentLoaded', () => {
  const app = window.WebApp;
  const greeting = document.getElementById('greeting');
  const status = document.getElementById('status');
  if (!app) {
    status.textContent = 'Не удалось загрузить MAX Bridge. Проверь интернет и открой приложение снова.';
    return;
  }
  if (!app.initData) {
    status.textContent = 'Открой мини-приложение через кнопку в чате с ботом MAX — здесь появится твоё имя.';
    return;
  }
  // Только отображение. Для авторизации нужна серверная проверка initData.
  const name = app.initDataUnsafe?.user?.first_name;
  if (typeof name === 'string' && name.trim()) {
    greeting.textContent = `Привет, ${name.trim()}!`;
    status.textContent = 'Рады видеть тебя в мини-приложении.';
  } else {
    status.textContent = 'Приложение открыто в MAX, но имя не передано. Попробуй открыть его заново через бота.';
  }
});
