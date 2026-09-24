# Как открыть мини-приложение из вашего бота — инструкция

> Продолжение [BOT_SETUP.md](BOT_SETUP.md). Предполагается, что бот уже отвечает на сообщения.
> Дата: 20.09.2026. Теория — в [MAX_MINI_APPS.md](MAX_MINI_APPS.md), [MAX_ENTRY_POINTS.md](MAX_ENTRY_POINTS.md), [MAX_INITDATA.md](MAX_INITDATA.md).
> Пометки: **[не подтверждено]** — не нашёл в документации, проверяется эмпирически.

---

## 0. Исходная точка

URL мини-приложения регистрируется только руками в кабинете `business.max.ru` (Чат-боты → бот → Настройки) — программного способа нет: в Bot API есть лишь читающий `GET /me`. **Считаем, что это уже сделано.**

Тогда всё, что вам остаётся, — выложить приложение **ровно по зарегистрированному адресу** и связать его с ботом. Две вещи, которые нужно знать точно:

- **какой именно URL зарегистрирован** (включая протокол, поддомен и путь — символ в символ);
- **какая надпись выбрана на кнопке запуска** («Открыть» / «Старт» / «Играть» / без названия) — чтобы искать её в чате, а не гадать.

---

## 1. Выложить приложение по зарегистрированному адресу

Адрес уже зафиксирован, поэтому задача обратная обычной: не выбрать хостинг, а **попасть в заданный URL**. Проверьте по требованиям платформы, что он именно такой, каким вы его запомнили: только `https`, до 1024 символов, без пробелов.

Важное следствие: **туннели (ngrok и т.п.) не подходят** — их адрес меняется при перезапуске и перестанет совпадать с зарегистрированным. Нужен стабильный хостинг: GitHub Pages для статики (адрес `https://<org>.github.io/<repo>/` не меняется), VK Cloud или Yandex Cloud, если рядом нужен бэкенд.

Бэкенд при этом **нигде не регистрируется** — он общается с фронтендом по API и может жить на любом адресе.

Если зарегистрированный URL указывает на путь (`/app/`, `/miniapp/`), убедитесь, что хостинг отдаёт по нему `index.html`, а не 404: на GitHub Pages это означает положить файл в соответствующую папку репозитория.

---

## 2. Сделать диагностическую страницу — первым делом

Прежде чем писать продуктовый фронтенд, выложите **одну страницу, которая показывает всё, что прислал клиент**. Это сэкономит часы: в десктоп-клиенте MAX нет DevTools, и отлаживать вслепую мучительно. Заодно эта страница закрывает несколько вопросов, на которые в документации нет ответа.

`index.html`:

```html
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>MAX diag</title>
  <script src="https://st.max.ru/js/max-web-app.js"></script>
  <style>
    body { font: 14px/1.5 system-ui, sans-serif; margin: 0; padding: 16px; }
    pre  { background: #f4f4f5; padding: 12px; border-radius: 8px; overflow-x: auto;
           white-space: pre-wrap; word-break: break-all; }
    h2   { font-size: 15px; margin: 16px 0 6px; }
    button { padding: 10px 14px; border-radius: 8px; border: 1px solid #ccc; background: #fff; }
  </style>
</head>
<body>
  <h1>Диагностика</h1>
  <div id="out">Загрузка…</div>
  <button id="copy">Скопировать всё</button>

  <script>
    const out = document.getElementById('out');

    function section(title, value) {
      const h = document.createElement('h2'); h.textContent = title;
      const p = document.createElement('pre');
      p.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
      out.append(h, p);
    }

    (async () => {
      out.textContent = '';
      const wa = window.WebApp;

      if (!wa) {
        section('window.WebApp', 'ОТСУТСТВУЕТ — страница открыта вне клиента MAX ' +
                                 'или не загрузился мост с st.max.ru');
        return;
      }

      section('platform / version / device', {
        platform: wa.platform, version: wa.version, deviceName: wa.deviceName
      });

      // Сырой фрагмент — видно реальные слои кодирования
      section('location.hash (сырой)', location.hash || '(пусто)');
      section('initData (строка)', wa.initData || '(пусто)');
      section('initDataUnsafe (объект)', wa.initDataUnsafe);

      // Есть ли chat при запуске из таббара? Приходит ли start_param?
      section('ключевые поля', {
        start_param: wa.initDataUnsafe?.start_param ?? '(нет)',
        chat:        wa.initDataUnsafe?.chat ?? '(нет)',
        auth_date:   wa.initDataUnsafe?.auth_date ?? '(нет)',
        user_id:     wa.initDataUnsafe?.user?.id ?? '(нет)'
      });

      try {
        section('getLaunchContext()', await wa.getLaunchContext());
      } catch (e) {
        section('getLaunchContext() — ошибка', e);
      }

      try {
        section('getViewportSize()', await wa.getViewportSize());
      } catch (e) {
        section('getViewportSize() — ошибка', e);
      }
    })();

    document.getElementById('copy').onclick = () => {
      navigator.clipboard?.writeText(out.innerText);
    };
  </script>
</body>
</html>
```

Открыв её из разных точек входа, вы за пять минут закроете вопросы, помеченные в предыдущих отчётах как непроверенные:

- присутствует ли `chat` при запуске из таббара;
- приходит ли `start_param` в подписанной части при входе по диплинку;
- в каком именно виде отдаётся `initData` (совпадает ли с сырым `WebAppData`);
- в каких единицах `auth_date`.

Кнопка «Скопировать всё» — потому что снять текст с экрана телефона иначе неудобно.

---

## 3. Первая проверка: кнопка запуска в чате

Раз URL зарегистрирован, в чате с ботом уже есть кнопка запуска — это **точка входа №1, и она работает без единой строки кода в боте**.

Откройте чат с ботом, нажмите кнопку, убедитесь, что диагностическая страница открылась и показала ваш `user.id`.

Это первая проверка, которую надо сделать, и её результат делит дальнейшую работу надвое:

- **открылось** — базовая связка (кабинет → хостинг → мост) исправна, дальше только ваш код;
- **не открылось** — проблема в связке, и писать код бота бессмысленно, пока она не решена (см. §8).

---

## 4. Точка входа №2: inline-кнопка из кода бота

Теперь то, ради чего инструкция: открыть приложение **из сценария бота**.

Тип кнопки в Bot API:

```json
{ "type": "open_app", "text": "Открыть приложение" }
```

В SDK:

```ts
import { Keyboard } from '@maxhub/max-bot-api';

bot.command('app', async (ctx) => {
  const keyboard = Keyboard.inlineKeyboard([
    [Keyboard.button.openApp('Открыть приложение', webApp, contactId)],
  ]);
  await ctx.reply('Нажмите кнопку ниже:', { attachments: [keyboard] });
});
```

⚠️ **Здесь главная неясность.** Сигнатура SDK — `button.openApp(text, webApp, contactId)`, но что именно передавать в `webApp` и `contactId`, публичная документация не раскрывает: в её JSON-схеме у `open_app` показаны только `type` и `text`. **[не подтверждено]**

Как разобраться за пять минут, не гадая:

```bash
# смотрим типы прямо в установленном пакете
grep -rn "openApp" node_modules/@maxhub/max-bot-api/dist/*.d.ts
grep -rn "open_app" node_modules/@maxhub/max-bot-api/dist/*.d.ts
```

Скорее всего `contactId` — это `user_id` бота (тот самый, что вернул `GET /me` в §1 инструкции по боту), а `webApp` — идентификатор или ник приложения/бота. Но **проверьте по типам**, а не по этой догадке.

### Запасной вариант, если с `open_app` не заладилось

Отправьте обычную кнопку-ссылку с диплинком на собственного бота:

```ts
const url = `https://max.ru/${BOT_USERNAME}?startapp=fromchat`;
const keyboard = Keyboard.inlineKeyboard([
  [Keyboard.button.link('Открыть приложение', url)],
]);
```

Это рабочий обходной путь **с оговоркой**: дока описывает кнопку `link` как открывающую URL «в новой вкладке», то есть возможен выход в браузер на страницу-заглушку `max.ru` вместо прямого открытия приложения. **[не подтверждено]** Проверьте на реальном клиенте; если переход происходит внутри MAX — вариант полноценный, и у него есть бонус: через `startapp` вы передаёте payload, чего `open_app` может и не уметь.

---

## 5. Точка входа №3: диплинк

```
https://max.ru/<username_бота>?startapp=<payload>
```

`<username_бота>` — из ответа `GET /me`. Payload: до 512 символов, только `A-Za-z0-9_-`.

Бот может присылать такие ссылки текстом, класть в кнопку `link`, а вы — размещать их на лендинге и в QR-кодах. Напоминание из [MAX_ENTRY_POINTS.md](MAX_ENTRY_POINTS.md): переход по диплинку **открывает приложение, не запуская бота**, и это единственный способ передать в приложение параметр снаружи.

В приложении payload читается так:

```js
const payload = window.WebApp.initDataUnsafe.start_param;
```

---

## 6. Подключить бэкенд-валидацию

Мини-приложение и бот используют **один и тот же токен**: им подписывается `initData`. Значит валидацию удобно поднять прямо в проекте бота — токен уже в окружении.

```ts
// src/webapp.ts
import express from 'express';
import cors from 'cors';
import crypto from 'node:crypto';
import { config } from './config.js';

const app = express();
app.use(express.json());
app.use(cors({ origin: 'https://<ваш-домен-фронтенда>' })); // фронт и API на разных origin

function validate(initData: string) {
  const pairs = initData.split('&').map((p) => {
    const i = p.indexOf('=');
    return i === -1 ? [p, ''] : [p.slice(0, i), p.slice(i + 1)];
  });
  if (pairs.filter(([k]) => k === 'hash').length !== 1) return null;
  const hash = pairs.find(([k]) => k === 'hash')![1];

  const decoded = pairs.map(([k, v]) => [k, decodeURIComponent(v)] as [string, string]);
  const launchParams = decoded
    .filter(([k]) => k !== 'hash')
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([k, v]) => `${k}=${v}`)
    .join('\n');

  const secretKey = crypto.createHmac('sha256', 'WebAppData').update(config.botToken).digest();
  const signature = crypto.createHmac('sha256', secretKey).update(launchParams).digest('hex');

  const a = Buffer.from(signature, 'hex');
  const b = Buffer.from(hash, 'hex');
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null;

  const data = Object.fromEntries(decoded);
  const authDate = Number(data.auth_date);
  if (!Number.isFinite(authDate) || Math.abs(Date.now() / 1000 - authDate) > 3600) return null;

  return { user: JSON.parse(data.user), startParam: data.start_param };
}

app.post('/api/session', (req, res) => {
  const data = validate(req.body.initData ?? '');
  if (!data) return res.status(401).json({ error: 'invalid_init_data' });
  // здесь выдавайте свою сессию; см. MAX_ACCOUNTS_AND_PII.md
  res.json({ userId: data.user.id, name: data.user.first_name });
});

app.listen(config.port);
```

Подробности алгоритма и краевые случаи — в [MAX_INITDATA.md](MAX_INITDATA.md). Ключевое напоминание: **ключ HMAC выводится как `HMAC("WebAppData", BOT_TOKEN)`** — порядок обратный к Telegram.

---

## 7. Порядок действий (что за чем)

1. Узнать точный зарегистрированный URL и надпись кнопки запуска.
2. Выложить диагностическую страницу ровно по этому адресу.
3. Проверить запуск из чата с ботом → на экране должен быть ваш `user.id`.
4. Снять с диагностики ответы на открытые вопросы (`chat` в таббаре, `start_param`, формат `initData`).
5. Посмотреть в типах SDK сигнатуру `openApp`, добавить команду `/app` с кнопкой.
6. Проверить диплинк со своим payload.
7. Поднять `/api/session` с валидацией.
8. Заменить диагностическую страницу продуктовым фронтендом (React + `@maxhub/max-ui`).

Диагностическую страницу не удаляйте — перенесите на `/diag`. Она пригодится, когда что-то перестанет работать на чужом устройстве.

---

## 8. Если не открывается

| Симптом | Причина |
|---|---|
| Кнопки запуска нет в чате с ботом | URL не сохранён в кабинете либо сохранён у другого бота |
| Кнопка есть, открывается пустой экран | ошибка JS до первого рендера; проверьте в веб-версии MAX с DevTools |
| `window.WebApp` не определён | не загрузился скрипт с `st.max.ru` либо страница открыта вне MAX |
| Приложение открылось, `initData` пуст | страница открыта не через MAX (прямой заход в браузере) |
| Подпись не сходится | двойное декодирование значений; сортировка не байтовая; порядок аргументов HMAC как в Telegram |
| Работает в вебе, не работает в десктоп-клиенте | различия User-Agent и политик безопасности; DevTools там нет — логируйте на сервер |
| Запросы к API блокируются | не настроен CORS: фронтенд и бэкенд на разных origin |
| `startapp` не доезжает | проверьте алфавит payload: только `A-Za-z0-9_-` |

---

## 9. Источники

- [Подключение мини-приложения — dev.max.ru/docs/webapps/introduction](https://dev.max.ru/docs/webapps/introduction) — регистрация URL, требования, кнопка запуска, диплинки
- [MAX Bridge — dev.max.ru/docs/webapps/bridge](https://dev.max.ru/docs/webapps/bridge) — `window.WebApp`, `getLaunchContext`
- [Валидация данных — dev.max.ru/docs/webapps/validation](https://dev.max.ru/docs/webapps/validation) — алгоритм проверки подписи
- [Bot API — dev.max.ru/docs-api](https://dev.max.ru/docs-api) — кнопка `open_app`, `GET /me`
- [max-bot-api-client-ts](https://github.com/max-messenger/max-bot-api-client-ts) — `Keyboard.button.openApp()`
- [max-ui](https://github.com/max-messenger/max-ui) — компоненты для продуктового фронтенда

**Оговорка:** `dev.max.ru` недоступен напрямую из этой среды, страницы получены через текстовый прокси-ридер (r.jina.ai). Подтверждено: отсутствие программного способа привязать URL (в API только `GET /me`), требования к URL, варианты надписи кнопки, тип кнопки `open_app`, формат диплинка и ограничения payload. Не подтверждены и помечены в тексте: семантика аргументов `webApp` и `contactId` у `openApp`, поведение кнопки `link` с диплинком на собственного бота.
