# Мини-приложения (Mini Apps / WebApps) в мессенджере MAX

> Отчёт-ресёрч для подготовки к хакатону. Дата сбора: 19.09.2026.
> Первоисточник — официальная документация `dev.max.ru/docs` (раздел «Мини-приложения»: *Общее описание*, *MAX Bridge*, *Валидация данных*), репозитории `github.com/max-messenger`, npm-организация `@maxhub`.
> Всё, что не подтверждено официальной докой, помечено явно как **[не подтверждено]**.

---

## 1. Что это такое в двух словах

Мини-приложение в MAX — это **обычный веб-сайт (HTML/JS/CSS), отданный по HTTPS**, который клиент MAX открывает во встроенном WebView и связывает с мессенджером через JS-мост `window.WebApp` (библиотека **MAX Bridge**).

Архитектурно это калька с Telegram Mini Apps, но с тремя важными отличиями:

| | Telegram | MAX |
|---|---|---|
| Привязка | бот, кнопка меню, attach-menu, ссылка `t.me/app` | **только бот**; автономно мини-приложение не существует |
| Кто может публиковать | любой аккаунт | только **юрлицо / ИП / самозанятый — резидент РФ**, после верификации и модерации бота (до 48 ч) |
| Дистрибуция | публичный поиск, каталоги | закрытая витрина + диплинки; уклон в госуслуги, медицину, образование, корпоративные сервисы **[по вторичным источникам]** |

Ключевая цитата из доки: *«Мини-приложения работают только внутри чат-ботов в MAX и не могут существовать автономно»*.

---

## 2. Схема работы

```
                    business.max.ru (кабинет партнёра)
                    ├─ создаём бота → модерация → токен бота
                    └─ в настройках бота вписываем URL мини-приложения
                                     │
   пользователь ───► чат с ботом / кнопка «Открыть» / диплинк
                                     │
                          клиент MAX (iOS/Android/desktop/web)
                                     │  открывает WebView по URL
                                     ▼
              ваш фронтенд на хостинге (HTTPS, VK Cloud / GH Pages / YC)
                     │  <script src="https://st.max.ru/js/max-web-app.js">
                     │  window.WebApp — мост к клиенту
                     ▼
              ваш бэкенд ──► валидация initData по HMAC с токеном бота
                        └──► Bot API MAX (отправка сообщений, шеринг медиа)
```

---

## 3. Что нужно, чтобы запустить мини-приложение

### 3.1 Доступ к платформе
1. Подключиться к «MAX для партнёров» (`business.max.ru`) — нужна верификация организации / ИП / самозанятого, резидент РФ.
2. Раздел **Чат-боты → Создать**. Карточка бота: логотип 500×500 px (до 5 МБ, jpg/png), имя 1–59 символов, ник (генерируется как `idИНН_bot` / `se(orgid)_bot`), описание до 200 символов.
3. **Модерация до 48 часов** (рабочие дни). После статуса «Опубликован» выдаётся **токен бота**.
   *Для быстрых экспериментов с Bot API токен исторически выдавал бот `@MasterBot` (аналог BotFather) — **[не подтверждено официальной докой для мини-приложений]**; боевое подключение мини-приложения идёт через кабинет.*

### 3.2 Подключение самого приложения
1. Залить статику на хостинг (дока прямо называет VK Cloud, GitHub Pages, Yandex Cloud) — **обязательно HTTPS**.
2. В кабинете: **Чат-боты → ваш бот → Настройки → URL мини-приложения**.
3. Выбрать тип кнопки запуска: **«Открыть» / «Старт» / «Играть» / без названия**.

**Требования к URL:** ≤ 1024 символов, только `https`, только латиница, цифры, точки и дефисы, без пробелов, валидный.

Изменение URL, смена кнопки и удаление приложения — там же, в настройках бота.

### 3.3 Требования к рабочему месту
Windows / macOS / Linux, мобильное устройство для регистрации профиля MAX, редактор кода и умение работать с командной строкой.

---

## 4. MAX Bridge — JS-мост (`window.WebApp`)

Подключение — одной строкой, **без npm-пакета** (отдельного пакета `@maxhub/max-web-app` в реестре npm нет, только CDN):

```html
<script src="https://st.max.ru/js/max-web-app.js"></script>
```

После загрузки доступен глобальный `window.WebApp`, отдельная инициализация не требуется.

### 4.1 Данные запуска

| Свойство | Тип | Описание |
|---|---|---|
| `initData` | `string` | URL-encoded строка стартовых параметров — **её и валидируем на бэкенде** |
| `initDataUnsafe` | `InitData` | распарсенный объект, **без проверки подписи — доверять нельзя** |
| `platform` | `string` | `ios` \| `android` \| `desktop` \| `web` |
| `version` | `string` | версия клиента, формат `<год>.<build>.<patch>` |
| `deviceName` | `string` | описание устройства |

```ts
interface InitData {
  query_id: string;      // идентификатор сессии
  ip?: string;
  auth_date: number;     // timestamp запуска
  hash: string;          // подпись
  user: {
    id: number;
    first_name: string;
    last_name: string;
    username: string;
    language_code: string;
    photo_url: string;
  };
  chat: { id: number; type: 'DIALOG' | 'CHAT' | 'CHANNEL' };
  start_param: string;   // payload из диплинка
}
```

Физически параметры приезжают во **фрагменте URL (после `#`)**, в параметре `WebAppData`.

### 4.2 Полный список API моста

**Контекст запуска**
- `getLaunchContext(): Promise<{ entryPoint: 'tabbar' | 'default' }>` — откуда открыли приложение.

**Экран**
- `getViewportSize(): Promise<{ height: string; width: string }>`
- `requestScreenMaxBrightness()` / `restoreScreenBrightness()` → `Promise<{ maxBrightness: boolean }>`
- `ScreenCapture.enableScreenCapture()` / `disableScreenCapture()` → `Promise<{ isScreenCaptureEnabled: boolean }>` — управление разрешением на скриншоты.

**Пользователь**
- `requestContact(): Promise<{ phone: string; authDate: string; hash: string }>` — запрос номера телефона с подписью (валидируется так же, как initData). Ошибки: `user_refused_provide_phone_number`, `request_error`.

**Навигация и UI**
- `BackButton.show()` / `hide()` / `isVisible` / `onClick(cb)` / `offClick(cb)`
- `enableClosingConfirmation()` / `disableClosingConfirmation()` — подтверждение при закрытии.

**Ссылки, шеринг, файлы**
- `openLink(url)` — во внешнем браузере
- `openMaxLink(url)` — диплинк `max.ru` внутри клиента
- `shareContent({ text?, link? })` — нативное меню «Поделиться» (только iOS/Android)
- `shareMaxContent({ text?, link? } | { mid, chatType: 'DIALOG' | 'CHAT' })` — шеринг внутри MAX, в т.ч. пересылка сообщения бота по `mid`
- `downloadFile(url, file_name)` — загрузка по HTTPS

**Хранилище**
- `DeviceStorage.setItem/getItem/removeItem/clear` — локальное хранилище
- `SecureStorage.setItem/getItem/removeItem/clear` — зашифрованное, **лимит 10 ключей на бота**

**Железо**
- `openCodeReader(fileSelect = true): Promise<string>` — сканер QR (камера или выбор файла)
- `BiometricManager` *(только мобильные)*: `init()`, `requestAccess(reason?)`, `authenticate(reason?)`, `updateBiometricToken(token?, reason?)`, `openSettings()`; поля `isInited`, `isBiometricAvailable`, `isAccessRequested`, `isAccessGranted`, `isBiometricTokenSaved`, `biometricType: Array<'finger'|'face'|'unknown'>`, `deviceId`
- `HapticFeedback` *(только мобильные)*: `impactOccurred('soft'|'light'|'medium'|'heavy'|'rigid', disableVibrationFallback?)`, `notificationOccurred('error'|'success'|'warning', ...)`, `selectionChanged(...)`
- `NfcManager` *(только Android)*: `init()`, `emulateNfcTag(nfctag?)`, `openSystemSettings()`, поле `isInited`

**Ошибки.** Почти все методы возвращают Promise; при отказе reject приходит в виде `{ error: { code: string } }`.

---

## 5. Валидация данных запуска (обязательный шаг на бэкенде)

`initDataUnsafe` читать удобно, но доверять ему нельзя. Алгоритм проверки (5 шагов, из раздела «Валидация данных»):

1. Достать `WebAppData` из фрагмента URL (`window.WebApp.initData`).
2. Разбить по `&` на пары `key=value`; **вынуть и отложить `hash`**; URL-декодировать значения; отсортировать пары по ключу; склеить через `\n` → `launch_params`.
3. `secret_key = HMAC_SHA256(key = "WebAppData", message = BOT_TOKEN)`
   ⚠️ **Порядок аргументов обратный к Telegram** (там ключ — токен, сообщение — `"WebAppData"`). Легко потерять час на отладке.
4. `signature = hex(HMAC_SHA256(key = secret_key, message = launch_params))`
5. Сравнить `signature` с `hash`. Совпало — данные подлинные.

Официальный пример в доке — на TypeScript поверх Web Crypto API. Эквивалент на Node:

```js
import crypto from 'node:crypto';

export function validateInitData(initData, botToken) {
  const pairs = initData.split('&').map(p => {
    const i = p.indexOf('=');
    return [p.slice(0, i), decodeURIComponent(p.slice(i + 1))];
  });

  const hash = pairs.find(([k]) => k === 'hash')?.[1];
  const launchParams = pairs
    .filter(([k]) => k !== 'hash')
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}=${v}`)
    .join('\n');

  const secretKey = crypto.createHmac('sha256', 'WebAppData').update(botToken).digest();
  const signature = crypto.createHmac('sha256', secretKey).update(launchParams).digest('hex');

  return signature === hash;   // + отдельно проверяйте свежесть auth_date
}
```

Дополнительно стоит проверять `auth_date` на свежесть (защита от replay) — дока этого явно не требует, но практика стандартная.

---

## 6. Диплинки и точки входа

**Запуск с параметром:**
```
https://max.ru/<botName>?startapp=<payload>
```
`payload` — до 512 символов, разрешены латиница, цифры, `_`, `-`. В приложении приезжает как `window.WebApp.initDataUnsafe.start_param`. Важно: переход по такой ссылке **открывает мини-приложение, не запуская самого бота** — удобно для лендингов, QR-кодов и внешних приложений, и для UTM-подобной атрибуции источников.

**Шеринг текста в MAX:**
```
https://max.ru/:share?text=<URL-encoded текст>
```
Параметр `text` обязателен.

**Шеринг медиа из мини-приложения:** бот отправляет контент → мини-приложение получает `mid` сообщения → вызывает `shareMaxContent({ mid, chatType: 'DIALOG' | 'CHAT' })`.

**Кнопка в клавиатуре бота (Bot API):** в `inline_keyboard` есть тип кнопки, открывающий мини-приложение — в TS-SDK это `button.openApp(text, webApp, contactId)`. Полная JSON-схема полей (`web_app`, `contact_id`) в публичной выжимке доки не раскрыта — **смотреть в `dev.max.ru/docs-api` перед реализацией**.

**Точки входа суммарно:** кнопка запуска в чате с ботом (появляется автоматически после подключения URL), inline-кнопка в сообщении бота, диплинк `?startapp=`, tabbar (различается через `getLaunchContext()`).

---

## 7. UI: библиотека MAX UI

React-компоненты в фирменном дизайне MAX — `github.com/max-messenger/max-ui`.

```bash
npm i @maxhub/max-ui     # актуальная версия на момент сбора: 0.5.0
```

```tsx
import '@maxhub/max-ui/dist/styles.css';
import { MaxUI, Panel, Button } from '@maxhub/max-ui';

const App = () => (
  <MaxUI>
    <Panel centeredX centeredY>
      <Button>Hello world!</Button>
    </Panel>
  </MaxUI>
);
```

- Стек: TypeScript, React 18+, полиморфные компоненты.
- Компоненты: `Panel`, `Grid`, `Container`, `Flex`, `Avatar`, `Typography`, `Button` и др. Адаптируются под iOS/Android и размеры экрана.
- Кастомизация: переопределение CSS-переменных (дизайн-токены) либо проп `innerClassNames` для составных компонентов.
- Витрина компонентов — `dev.max.ru/ui`. Отдельно дока упоминает **гайдлайн в формате Figma** с примерами мини-приложений.

---

## 8. Экосистема SDK (для бэкенда бота)

Организация `github.com/max-messenger`, 7 репозиториев:

| Репозиторий | Язык | Назначение |
|---|---|---|
| `max-bot-api-client-ts` | TypeScript | клиент Bot API (npm: `@maxhub/max-bot-api`, v0.3.1) |
| `max-bot-api-client-go` | Go | клиент Bot API |
| `maxbot` | Go | фреймворк для ботов |
| `max-botapi-python` | Python | библиотека для чат-ботов |
| `demo-bot-go` | Go | демо-бот: сообщения, клавиатуры, вложения, админка чата |
| `max-bot-example-todolist` | Go | пример todo-бота |
| `max-ui` | TypeScript | React-компоненты для мини-приложений |

Есть и сторонние обёртки: `node-red-contrib-maxbot`, `@manihateu/nestjs-maximus`, `messenger-bot-adapters`.

---

## 9. Подводные камни (важно для хакатона)

1. **Барьер входа — юрлицо и модерация до 48 часов.** Это главный риск таймингов: без опубликованного бота нет ни токена, ни слота под URL мини-приложения. Планируйте получение доступа заранее, а не в день сдачи.
2. **Нет DevTools в десктоп-клиенте.** По разбору на Хабре: во встроенном WebView десктопа нет ни Inspect, ни F12, ни описанного способа включить DevTools. Веб-версия MAX открывается в обычном браузере — там DevTools (Network/Console/Storage) работают, и отлаживать надо там.
3. **Веб-версия ≠ десктоп-клиент**: различаются User-Agent, доступные браузерные фичи, политики безопасности и нативные интеграции. Поведение, проверенное в вебе, надо перепроверять в клиенте.
4. **Мост непрозрачен**: последовательность событий клиента и lifecycle-коллбэков не документирована; рабочий обходной путь — обильное логирование + внешняя телеметрия (Sentry).
5. **Платформенные ограничения API**: биометрия и haptic — только мобильные, NFC — только Android, `shareContent` — только iOS/Android. Всегда проверяйте `WebApp.platform` и делайте фолбэк.
6. **`SecureStorage` — 10 ключей на бота.** Не использовать как кэш.
7. **Обратный порядок HMAC при валидации** относительно Telegram (см. §5, шаг 3).
8. **Никогда не доверять `initDataUnsafe`** на бэкенде — только `initData` после проверки подписи.
9. **Строгие ограничения URL** (1024 символа, только латиница/цифры/точки/дефисы) — не получится завести приложение на домене с не-ASCII или на http-стенде без https.

---

## 10. Минимальный скелет

```html
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <script src="https://st.max.ru/js/max-web-app.js"></script>
</head>
<body>
  <div id="root">Загрузка…</div>
  <script>
    const wa = window.WebApp;

    // 1. Кто нас открыл и откуда
    const { user, chat, start_param } = wa.initDataUnsafe;
    console.log(wa.platform, wa.version, start_param);

    // 2. Отдаём initData на бэкенд — только после проверки подписи доверяем user.id
    fetch('/api/session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ initData: wa.initData })
    });

    // 3. Кнопка «Назад» клиента
    wa.BackButton.show();
    wa.BackButton.onClick(() => history.back());

    // 4. Контекст запуска
    wa.getLaunchContext().then(({ entryPoint }) => {
      if (entryPoint === 'tabbar') { /* другой стартовый экран */ }
    });
  </script>
</body>
</html>
```

---

## 11. Источники

Официальные:
- [Подключение мини-приложения — dev.max.ru/docs/webapps/introduction](https://dev.max.ru/docs/webapps/introduction)
- [MAX Bridge — dev.max.ru/docs/webapps/bridge](https://dev.max.ru/docs/webapps/bridge)
- [Валидация данных — dev.max.ru/docs/webapps/validation](https://dev.max.ru/docs/webapps/validation)
- [Создание чат-бота — dev.max.ru/docs/chatbots/bots-create](https://dev.max.ru/docs/chatbots/bots-create)
- [MAX для разработчиков](https://dev.max.ru/) · [Bot API](https://dev.max.ru/docs-api) · [MAX UI](https://dev.max.ru/ui)
- [github.com/max-messenger](https://github.com/max-messenger) · [max-ui](https://github.com/max-messenger/max-ui) · [max-bot-api-client-ts](https://github.com/max-messenger/max-bot-api-client-ts)

Вторичные (для контекста и подводных камней):
- [Как отлаживать мини-приложения в MAX и почему без DevTools это боль — Хабр](https://habr.com/ru/articles/1039030/)
- [Боты и мини-приложения в MAX в 2026 — блог студии Бобрик](https://bobrick.su/blog/bot-mini-app-v-max-2026/)
- [Обзор мессенджера MAX 2026 — click.ru](https://blog.click.ru/growthhacking/messendzher-max/)
- [Max (мессенджер) — TAdviser](https://www.tadviser.ru/index.php/Продукт:Max_(мессенджер))

**Оговорка:** домены `dev.max.ru` и `habr.com` недоступны напрямую из этой среды, содержимое получено через текстовый прокси-ридер (r.jina.ai), поэтому часть длинных страниц пришла в пересказанном виде. Числовые лимиты (1024 символа URL, 512 символов payload, 10 ключей SecureStorage, 48 ч модерации) стоит перепроверить по живой доке перед реализацией.
