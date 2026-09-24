# Разворачивание бота MAX — пошаговая инструкция

> Шестой документ серии. Контекст: токен бота выдан организаторами хакатона, мини-приложение пока не подключаем.
> Дата: 20.09.2026. Источники: `dev.max.ru/docs-api`, SDK `@maxhub/max-bot-api`.
> Стек по умолчанию — **Node.js + TypeScript**: официальный SDK, и он же стыкуется с React + `@maxhub/max-ui` на этапе мини-приложения. Вариант на Python — в §11.
> Пометки: **[не подтверждено]** — не нашёл в документации, проверить эмпирически.

---

## 0. Что даёт токен и что с ним нельзя делать

Токен бота — это **одновременно**:

- ключ доступа ко всему Bot API от имени бота;
- ключ подписи `initData` для будущего мини-приложения.

Утечка токена означает и рассылку от имени бота, и возможность подделать личность любого пользователя в мини-приложении. Поэтому сразу, до первой строки кода:

- токен живёт **только** в переменных окружения, никогда в коде и не в репозитории;
- `.env` в `.gitignore` **до** первого коммита, а не после;
- в чат, тикет или скриншот токен не попадает;
- если засветился — просить организаторов перевыпустить, не «пока сойдёт».

---

## 1. Проверить токен (1 минута)

Прежде чем что-то устанавливать, убедитесь, что токен живой:

```bash
curl -s -X GET "https://platform-api2.max.ru/me" \
  -H "Authorization: ВАШ_ТОКЕН"
```

Ожидаемый ответ:

```json
{
  "user_id": 1,
  "name": "My Bot",
  "username": "my_bot",
  "is_bot": true,
  "last_activity_time": 1737500130100
}
```

| Код | Что значит | Что делать |
|---|---|---|
| `200` | токен рабочий | дальше по инструкции |
| `401` | ошибка аутентификации | проверить, что токен целиком, без пробелов и кавычек |
| `404` | ресурс не найден | проверить URL — хост именно `platform-api2.max.ru` |
| `429` | превышен лимит | подождать |
| `503` | сервис недоступен | повторить позже |

Запишите `username` из ответа — по нему строятся диплинки (`https://max.ru/<username>`), он же понадобится для мини-приложения.

⚠️ **Токен передаётся только заголовком.** Дока прямо фиксирует: *«Передача токена через query-параметры больше не поддерживается»*. Примеры из старых статей с `?access_token=` работать не будут.

---

## 2. Структура проекта

```
max_hack_pg/
├─ .env                 # токен, в .gitignore
├─ .env.example         # шаблон без секретов, в репозитории
├─ .gitignore
├─ package.json
├─ tsconfig.json
└─ src/
   ├─ index.ts          # точка входа
   ├─ bot.ts            # сборка бота и хендлеры
   └─ config.ts         # чтение окружения
```

---

## 3. Инициализация

```bash
npm init -y
npm i @maxhub/max-bot-api dotenv
npm i -D typescript tsx @types/node
npx tsc --init
```

`.gitignore` — **первым делом**:

```
node_modules/
dist/
.env
*.log
```

`.env`:

```
BOT_TOKEN=ВАШ_ТОКЕН
```

`.env.example` (коммитится, чтобы команда знала, какие переменные нужны):

```
BOT_TOKEN=
```

`package.json` — секция скриптов:

```json
{
  "type": "module",
  "scripts": {
    "dev": "tsx watch src/index.ts",
    "build": "tsc",
    "start": "node dist/index.js"
  }
}
```

`src/config.ts` — падать на старте, а не на первом запросе:

```ts
import 'dotenv/config';

function required(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`Не задана переменная окружения ${name}`);
  return v;
}

export const config = {
  botToken: required('BOT_TOKEN'),
  port: Number(process.env.PORT ?? 3000),
};
```

---

## 4. Минимальный бот

`src/bot.ts`:

```ts
import { Bot } from '@maxhub/max-bot-api';
import { config } from './config.js';

export const bot = new Bot(config.botToken);

// Пользователь нажал «Начать» / впервые открыл диалог
bot.on('bot_started', (ctx) => ctx.reply('Привет! Я бот проекта. Напишите /help'));

// Команды
bot.command('ping', (ctx) => ctx.reply('pong'));
bot.command('help', (ctx) => ctx.reply('Доступно: /ping, /help'));

// Любое входящее сообщение
bot.on('message_created', (ctx) => {
  const text = ctx.message?.body?.text ?? '';       // точное поле сверьте с типами SDK
  if (text.startsWith('/')) return;                  // команды уже обработаны выше
  ctx.reply(`Вы написали: ${text}`);
});

// Глобальный обработчик ошибок — иначе падение хендлера роняет процесс
bot.catch((err) => {
  console.error('Ошибка в обработчике:', err);
});
```

`src/index.ts`:

```ts
import { bot } from './bot.js';

bot.start();
console.log('Бот запущен');

// Корректное завершение: без него при перезапуске остаются висящие соединения
for (const sig of ['SIGINT', 'SIGTERM'] as const) {
  process.on(sig, () => {
    console.log(`Получен ${sig}, останавливаюсь`);
    process.exit(0);
  });
}
```

Запуск:

```bash
npm run dev
```

Откройте MAX, найдите бота по `username` из §1, нажмите «Начать» — должно прийти приветствие.

⚠️ `ctx.message?.body?.text` — поле указано по аналогии с типовыми Bot API; **точную структуру сверьте с типами SDK** (`node_modules/@maxhub/max-bot-api/dist/*.d.ts`) или с `dev.max.ru/docs-api`. **[не подтверждено]**

---

## 5. Как бот получает события: polling против webhook

Два механизма, и это главное архитектурное решение на старте.

| | Long polling (`GET /updates`) | Webhook (`POST /subscriptions`) |
|---|---|---|
| Как работает | бот сам опрашивает сервер | MAX сам шлёт события на ваш URL |
| Нужен публичный HTTPS | нет | **да** |
| Подходит для разработки | да | только через туннель |
| Для продакшена | **дока не рекомендует** | **рекомендует** |
| Ограничения | *«ограничено по скорости и сроку хранения событий»* | зависят от вашего сервера |

**Стратегия для хакатона:** разрабатывать на polling (ничего не настраивать, работает с ноутбука), переключиться на webhook при выкладке. Код хендлеров при этом не меняется — меняется только способ доставки.

Какой режим включает `bot.start()` в SDK, документация не уточняет. **[не подтверждено]** Проверьте по типам SDK или эмпирически: если бот получает сообщения без публичного URL — это polling.

---

## 6. Переключение на webhook

### 6.1 Поднять HTTP-эндпоинт

```ts
import express from 'express';
import { bot } from './bot.js';
import { config } from './config.js';

const app = express();
app.use(express.json());

// Проверка живости — пригодится для мониторинга хостинга
app.get('/health', (_req, res) => res.send('ok'));

app.post('/webhook', (req, res) => {
  res.sendStatus(200);           // сначала отвечаем, потом обрабатываем
  bot.handleUpdate(req.body).catch((e) => console.error(e));  // имя метода сверьте с SDK
});

app.listen(config.port, () => console.log(`Слушаю :${config.port}`));
```

Два правила, о которые спотыкаются чаще всего:

1. **Отвечать `200` немедленно**, до обработки. Если держать соединение на время работы хендлера, платформа будет считать доставку неуспешной и ретраить.
2. **Эндпоинт должен быть идемпотентным.** Ретраи означают повторную доставку одного события — не создавайте заказ дважды.

### 6.2 Зарегистрировать подписку

```bash
curl -X POST "https://platform-api2.max.ru/subscriptions" \
  -H "Authorization: ВАШ_ТОКЕН" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-domain.com/webhook", "update_types": ["message_created", "bot_started"]}'
```

⚠️ **Полный список значений `update_types` уточните в `dev.max.ru/docs-api`** — в доступной выжимке фигурирует только `comment_created` в примере, а имена `message_created` / `bot_started` взяты из событий SDK. **[не подтверждено]** Если не уверены — проверьте, не подписывает ли вызов без `update_types` на всё сразу.

### 6.3 Локальная разработка с webhook

Нужен публичный HTTPS-адрес. Туннель (`ngrok http 3000` или аналог) даёт такой URL, его и указываете в `/subscriptions`. Помните: при перезапуске туннеля адрес меняется — подписку надо регистрировать заново.

---

## 7. Отправка сообщений

```ts
await bot.api.sendMessageToUser(12345, 'Привет!');
await bot.api.sendMessageToChat(54321, 'Всем привет!');
```

Через REST напрямую:

```bash
curl -X POST "https://platform-api2.max.ru/messages" \
  -H "Authorization: ВАШ_ТОКЕН" \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello", "chat_id": "..."}'
```

### Клавиатуры

```ts
import { Keyboard } from '@maxhub/max-bot-api';

const keyboard = Keyboard.inlineKeyboard([
  [Keyboard.button.callback('Действие', 'payload')],
  [Keyboard.button.link('Открыть сайт', 'https://max.ru')],
]);

await ctx.reply('Выберите:', { attachments: [keyboard] });
```

Лимит: до **210 кнопок** в **30 рядов**. Типы кнопок: `callback`, `link`, `request_contact`, `request_geo_location`, `message`, `clipboard` и `open_app` — последняя понадобится, когда подключите мини-приложение (см. [MAX_ENTRY_POINTS.md](MAX_ENTRY_POINTS.md)).

Нажатие `callback`-кнопки приходит событием `message_callback` с вашим `payload`.

---

## 8. Выкладка

### Что важно для хакатона

Бот на polling **не требует хостинга вообще** — он работает с ноутбука, пока запущен процесс. Для демо этого достаточно, и это осознанно правильный выбор: не тратьте время жюри-дедлайна на инфраструктуру. Хостинг нужен, когда: (а) переходите на webhook, (б) бот должен жить после закрытия ноутбука, (в) подключаете мини-приложение, которому в любом случае нужен HTTPS-хостинг.

### Вариант А. Процесс на сервере (проще)

```bash
npm run build
node dist/index.js
```

Под присмотром systemd, чтобы переживал падения и перезагрузки:

```ini
# /etc/systemd/system/maxbot.service
[Unit]
Description=MAX bot
After=network.target

[Service]
WorkingDirectory=/opt/maxbot
EnvironmentFile=/opt/maxbot/.env
ExecStart=/usr/bin/node dist/index.js
Restart=always
RestartSec=5
User=maxbot

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now maxbot
sudo journalctl -u maxbot -f     # логи
```

### Вариант Б. Docker

```dockerfile
FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY dist ./dist
CMD ["node", "dist/index.js"]
```

```bash
docker build -t maxbot .
docker run -d --restart=always --env-file .env -p 3000:3000 maxbot
```

### Где хостить

Дока для мини-приложений называет **VK Cloud** и **Yandex Cloud** — для бота подойдут они же. Плюс: если дальше будут персональные данные, требование локализации по 152-ФЗ закрывается сразу (см. [MAX_ACCOUNTS_AND_PII.md](MAX_ACCOUNTS_AND_PII.md)). Зарубежные VPS для боевого сценария с ПД не подходят.

---

## 9. Эксплуатация

**Лимиты и ретраи.** На `429` нужен повтор с экспоненциальной паузой, на `503` — тоже. Наивный цикл без пауз превратит временную деградацию в блокировку.

**Логи.** Логируйте `user_id`, тип события и результат — но **не тело сообщений** без необходимости: переписка пользователей это ПД. Когда дойдёте до мини-приложения, туда же добавится правило вырезать `initData` (см. [MAX_INITDATA.md](MAX_INITDATA.md)).

**Состояние диалога.** SDK по умолчанию хранит сессии в памяти — при перезапуске они теряются. Для сценариев из нескольких шагов (анкета, оформление) подключите SQLite или Redis: в README SDK это заявлено как поддерживаемое.

**Идемпотентность.** При webhook одно событие может прийти дважды. Складывайте обработанные id событий в кэш с TTL и пропускайте повторы.

---

## 10. Чек-лист

**До кода**
- [ ] `GET /me` отвечает `200`, `username` записан.
- [ ] `.env` в `.gitignore`, `.env.example` закоммичен.
- [ ] Токен нигде не захардкожен.

**Бот**
- [ ] Обрабатываются `bot_started`, команды, `message_created`.
- [ ] Есть `bot.catch()` — ошибка хендлера не роняет процесс.
- [ ] Конфиг падает на старте при отсутствии `BOT_TOKEN`.
- [ ] Обработаны `SIGINT`/`SIGTERM`.

**Выкладка**
- [ ] Выбран режим доставки; для продакшена — webhook.
- [ ] Webhook отвечает `200` сразу, обработка асинхронная.
- [ ] Повторная доставка события не ломает логику.
- [ ] Автоперезапуск (systemd / `--restart=always`).
- [ ] Есть `/health` и доступ к логам.
- [ ] Ретраи на `429` и `503` с паузой.

---

## 11. Вариант на Python

Если команде ближе Python — официальная библиотека `max-botapi-python` (`github.com/max-messenger/max-botapi-python`). Логика та же: токен из окружения, хендлеры, polling для разработки и webhook для продакшена. Эндпоинты и заголовок авторизации общие, так что §1, §5, §6 и §8 применимы без изменений; меняется только код из §3–§4.

Есть и сторонние обёртки (`@manihateu/nestjs-maximus` для NestJS, `node-red-contrib-maxbot`), но на хакатоне безопаснее официальный SDK — меньше шансов упереться в неподдерживаемую фичу.

---

## 12. Если что-то не работает

| Симптом | Вероятная причина |
|---|---|
| `401` на любом запросе | токен с пробелом/переносом; передан в query вместо заголовка |
| `404` на все эндпоинты | неверный хост — нужен `platform-api2.max.ru` |
| Бот не реагирует на сообщения | не тот режим доставки: на polling процесс не запущен, на webhook не зарегистрирована подписка |
| Webhook зарегистрирован, событий нет | URL не HTTPS, не публичный, туннель перезапущен и адрес сменился |
| События приходят дважды | эндпоинт отвечает `200` слишком поздно → ретраи |
| Работает локально, падает на сервере | не проброшен `.env`; нет `dist` после `npm run build` |
| `429` | нет пауз между запросами |

---

## 13. Источники

- [Bot API — dev.max.ru/docs-api](https://dev.max.ru/docs-api) — базовый URL, заголовок `Authorization`, `/me`, `/updates`, `/subscriptions`, `/messages`, коды ответов
- [max-bot-api-client-ts](https://github.com/max-messenger/max-bot-api-client-ts) — `@maxhub/max-bot-api`, быстрый старт, `bot.catch()`, сессии
- [max-botapi-python](https://github.com/max-messenger/max-botapi-python) — вариант на Python
- [demo-bot-go](https://github.com/max-messenger/demo-bot-go) — демо-бот: сообщения, клавиатуры, вложения, админка чата
- [Создание чат-бота — dev.max.ru/docs/chatbots/bots-create](https://dev.max.ru/docs/chatbots/bots-create) — если понадобится свой бот вне хакатонного токена

**Оговорка:** `dev.max.ru` недоступен напрямую из этой среды, страницы получены через текстовый прокси-ридер (r.jina.ai). Дословно подтверждены: базовый URL, способ передачи токена, ответ `/me`, примеры curl для `/subscriptions` и `/messages`, коды ответов, рекомендация webhook вместо polling, быстрый старт SDK. Не подтверждены и помечены в тексте: точная структура объекта сообщения в хендлере, имя метода обработки апдейта в SDK, полный список `update_types`, режим доставки по умолчанию у `bot.start()`.
