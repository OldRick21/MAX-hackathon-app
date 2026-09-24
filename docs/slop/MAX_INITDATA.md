# `initData` и `initDataUnsafe` в мини-приложениях MAX — детальный разбор

> Дополнение к [MAX_MINI_APPS.md](MAX_MINI_APPS.md). Дата сбора: 19.09.2026.
> Источники: `dev.max.ru/docs/webapps/bridge` и `dev.max.ru/docs/webapps/validation` (дословные примеры и код оттуда).
> Выводы, которых нет в доке напрямую, помечены как **[вывод]**.

---

## 1. Что это такое

Это **одни и те же данные в двух представлениях**, которые клиент MAX передаёт мини-приложению в момент запуска:

| | `window.WebApp.initData` | `window.WebApp.initDataUnsafe` |
|---|---|---|
| Тип | `string` | `InitData` (объект) |
| Что внутри | стартовые параметры в URL-кодировке, UTF-8 | те же параметры, уже распарсенные в JSON-объект |
| Для чего | **валидация на стороне сервера** | быстрое чтение в UI |
| Доверие | источник истины после проверки подписи | **«нельзя использовать для валидации данных»** (прямая цитата из доки) |

Смысл разделения: подпись `hash` считается по **точной байтовой строке**, которую прислал клиент. Любой парсинг в объект эту строку разрушает (порядок ключей, форматирование JSON, типы), поэтому для проверки годится только сырой `initData`. `initDataUnsafe` — производная величина для удобства, её содержимое пользователь теоретически может подменить, и сервер о подмене не узнает.

**Практическое правило:** `initDataUnsafe.user.id` можно показать на экране, но нельзя использовать как идентификатор для выдачи данных из БД. Для этого нужен `user.id`, извлечённый из `initData` **после** успешной проверки подписи на бэкенде.

---

## 2. Порядок передачи: полный путь данных

### Шаг 1. Клиент формирует URL

Пользователь нажимает кнопку запуска (в чате с ботом, inline-кнопку, диплинк или tabbar). Клиент MAX берёт URL, зарегистрированный в настройках бота, и дописывает к нему **фрагмент** — часть после `#`.

Дословный пример из документации:

```
https://example.com#WebAppData=chat%3D%257B%2522id%2522%253A12345%252C%2522type%2522%253A%2522DIALOG%2522%257D%26ip%3D192.168.0.1%26user%3D%257B%2522id%2522%253A67890%252C%2522first_name%2522%253A%2522Max%2522%252C%2522last_name%2522%253A%2522User%2522%252C%2522username%2522%253Anull%252C%2522language_code%2522%253A%2522ru%2522%252C%2522photo_url%2522%253Anull%257D%26query_id%3D4c0ab423-342b-4e45-aea4-2747dbc500cd%26auth_date%3D1771409719%26hash%3D<calculated_hash>&WebAppPlatform=web&WebAppVersion=26.2.8
```

### Шаг 2. Фрагмент содержит ровно три параметра

| Параметр | Пример | Что это |
|---|---|---|
| `WebAppData` | `chat%3D...%26hash%3D...` | **подписанный** блок стартовых параметров — это и есть `initData` |
| `WebAppPlatform` | `web` | платформа клиента; дублируется в `WebApp.platform` |
| `WebAppVersion` | `26.2.8` | версия клиента (`<год>.<build>.<patch>`); дублируется в `WebApp.version` |

⚠️ Подписан **только `WebAppData`**. `WebAppPlatform` и `WebAppVersion` лежат рядом, вне подписи, и подделываются тривиально — не принимайте на их основе security-решений. **[вывод]**

### Шаг 3. Фрагмент не уходит на сервер

Всё, что после `#`, браузер и WebView **не включают в HTTP-запрос**. Твой сервер при отдаче страницы этих данных не видит в принципе. Их читает JS на клиенте (`window.WebApp.initData`) и отправляет на бэкенд отдельным запросом — телом POST или заголовком.

### Шаг 4. Бэкенд проверяет подпись

Сервер пересчитывает HMAC по токену бота и сравнивает с `hash`. Только после этого данные считаются подлинными и `user.id` становится пригоден для авторизации.

```
клиент MAX ──(fragment #WebAppData=...)──► WebView ──► window.WebApp.initData
                                                              │ JS читает
                                                              ▼
                                            POST /api/session { initData }
                                                              │
                                                              ▼
                                   бэкенд: HMAC-SHA256 → сравнение с hash
                                                              │
                                                              ▼
                                             доверенный user.id, chat.id
```

---

## 3. Слои кодирования — главный источник багов

`WebAppData` — это **query-строка, вложенная в query-строку**, поэтому кодирование двухуровневое. Разберём пример из доки по слоям.

### Слой 0 — сырой фрагмент

```
WebAppData=chat%3D%257B%2522id%2522%253A12345%252C%2522type%2522%253A%2522DIALOG%2522%257D%26ip%3D192.168.0.1%26...
```

Здесь `%3D` — это `=`, `%26` — это `&`, а `%257B` — это **дважды закодированная** `{` (`%7B` → `%257B`).

### Слой 1 — после парсинга фрагмента

`new URLSearchParams(new URL(link).hash.slice(1)).get('WebAppData')` снимает одно кодирование:

```
chat=%7B%22id%22%3A12345%2C%22type%22%3A%22DIALOG%22%7D&ip=192.168.0.1&user=%7B%22id%22%3A67890%2C%22first_name%22%3A%22Max%22%2C%22last_name%22%3A%22User%22%2C%22username%22%3Anull%2C%22language_code%22%3A%22ru%22%2C%22photo_url%22%3Anull%7D&query_id=4c0ab423-342b-4e45-aea4-2747dbc500cd&auth_date=1771409719&hash=<calculated_hash>
```

**Это и есть содержимое `window.WebApp.initData`** — разделители `=` и `&` уже настоящие, а значения всё ещё percent-encoded.

*Оговорка: дока описывает `initData` как «строку в URL-кодировке», а её же пример валидации берёт значение через `URLSearchParams.get()`, то есть после одного декодирования. Байтовое совпадение `initData` с сырым значением `WebAppData` явно не зафиксировано — пишите код защитно (см. §7, п. 2).* **[вывод]**

### Слой 2 — после `decodeURIComponent` каждого значения

```
chat={"id":12345,"type":"DIALOG"}
ip=192.168.0.1
user={"id":67890,"first_name":"Max","last_name":"User","username":null,"language_code":"ru","photo_url":null}
query_id=4c0ab423-342b-4e45-aea4-2747dbc500cd
auth_date=1771409719
hash=<calculated_hash>
```

Вот в таком виде значения и подписываются. Обратите внимание: `user` и `chat` **на проводе — это JSON-строки**, а не вложенные поля. В `initDataUnsafe` они уже распарсены в объекты.

---

## 4. Состав `WebAppData` (то, что подписывается)

| Ключ | Формат на проводе | Описание |
|---|---|---|
| `query_id` | UUID-строка | уникальный идентификатор сессии запуска |
| `auth_date` | Unix timestamp, секунды | момент выдачи данных; **дока рекомендует считать их валидными 1 час** |
| `user` | JSON-объект строкой | данные пользователя |
| `chat` | JSON-объект строкой | данные чата, из которого запущено приложение |
| `ip` | строка | IP пользователя; в интерфейсе помечен как опциональный |
| `start_param` | строка | payload из диплинка `?startapp=`; в примере доки отсутствует — очевидно, появляется только при запуске через диплинк **[вывод]** |
| `hash` | hex-строка | подпись всех остальных параметров; в подписи **не участвует** |

---

## 5. `initDataUnsafe`: интерфейс и поля

Дословно из доки:

```ts
interface InitData {
    query_id: string;
    ip?: string;
    auth_date: number;
    hash: string;
    user: {
        id: number;
        first_name: string;
        last_name: string;
        username: string;
        language_code: string;
        photo_url: string;
    };
    chat: {
        id: number;
        type: 'DIALOG' | 'CHAT' | 'CHANNEL';
    };
    start_param: string;
}
```

| Поле | Тип | Описание |
|---|---|---|
| `query_id` | `string` | уникальный идентификатор сессии |
| `ip?` | `string` | IP-адрес пользователя (опционально) |
| `auth_date` | `number` | время выдачи данных; рекомендуемый интервал валидности — 1 час |
| `hash` | `string` | хеш параметров для проверки подлинности |
| `user.id` | `number` | идентификатор пользователя |
| `user.first_name` | `string` | имя |
| `user.last_name` | `string` | фамилия |
| `user.username` | `string` | ник |
| `user.language_code` | `string` | язык интерфейса MAX, по RFC 5646 |
| `user.photo_url` | `string` | ссылка на фото профиля |
| `chat.id` | `number` | идентификатор чата |
| `chat.type` | `'DIALOG' \| 'CHAT' \| 'CHANNEL'` | тип чата |
| `start_param` | `string` | значение из `https://max.ru/<your_awesome_bot>?startapp=someData` → здесь окажется `someData` |

⚠️ **Интерфейс врёт про nullability.** В собственном примере доки `username` и `photo_url` имеют значение `null`, хотя объявлены как `string` без `?`. Считайте их `string | null` и проверяйте перед использованием. То же вероятно и для `start_param` при запуске не через диплинк. **[вывод]**

---

## 6. Канонизация и подпись

### Порядок параметров

На проводе параметры идут в произвольном порядке — в примере доки это `chat, ip, user, query_id, auth_date, hash`, что **не** алфавитный порядок. Для подписи их обязательно пересортировать.

Алгоритм (дословные формулировки доки: *«Производим URL-декодирование значений параметров, если ваша платформа не сделала это автоматически»*, *«Сортируем параметры по названию ключа a → z»*):

1. Разбить `WebAppData` по `&` на пары `key=value`.
2. Убедиться, что `hash` встречается **ровно один раз**, и отложить его.
3. URL-декодировать значения.
4. Отсортировать пары по ключу a → z.
5. Исключить `hash`, склеить `key=value` через `\n` → `launch_params`.

Для примера выше `launch_params` выглядит так:

```
auth_date=1771409719
chat={"id":12345,"type":"DIALOG"}
ip=192.168.0.1
query_id=4c0ab423-342b-4e45-aea4-2747dbc500cd
user={"id":67890,"first_name":"Max","last_name":"User","username":null,"language_code":"ru","photo_url":null}
```

Дальше:

```
secret_key = HMAC_SHA256(key = "WebAppData", message = BOT_TOKEN)
signature  = hex(HMAC_SHA256(key = secret_key, message = launch_params))
signature === hash  →  данные подлинные
```

⚠️ Порядок аргументов в `secret_key` **обратный к Telegram**: ключом выступает литерал `"WebAppData"`, сообщением — токен бота.

### Официальный пример (TypeScript, Web Crypto API)

```typescript
// Вводные параметры
const BOT_TOKEN = 'YOUR_BOT_TOKEN';
const USER_LINK = 'https://example.com#WebAppData=...&WebAppPlatform=web&WebAppVersion=26.2.8';

// Извлекаем параметры платформы из фрагмента URL
const hashParams = new URLSearchParams(new URL(USER_LINK).hash.slice(1));
const appData: string = hashParams.get('WebAppData') || '';
const platform: string = hashParams.get('WebAppPlatform') || '';
const appVersion: string = hashParams.get('WebAppVersion') || '';

const validateAppData = async (appData: string, botToken: string): Promise<boolean> => {
    // Преобразуем appData из key1=value1&key2=value2 в [["key", "value"], ["key2", "value2"]]
    const params: string[][] = appData.split('&').map((x) => x.split('='));

    // Если hash встречается больше одного раза — прерываем проверку
    if (params.filter((x) => x[0] === 'hash').length !== 1) {
        return false;
    }

    // Сохраняем хеш, который пришёл вместе с параметрами
    const originalHash = params.find((x) => x[0] === 'hash');

    // Если хеш отсутствует — валидация невозможна
    if (!originalHash || typeof originalHash[1] !== 'string') {
        return false;
    }

    // Производим URL-декодирование значений параметров
    for (const param of params) {
        param[1] = decodeURIComponent(param[1]);
    }

    // Сортируем параметры по названию ключа a -> z
    params.sort((a, b) => a[0].localeCompare(b[0]));

    // Формируем строку для подписи с разделителем \n, исключаем hash
    const launchParams = params
        .filter((x) => x[0] !== 'hash')
        .map((x) => `${x[0]}=${x[1]}`)
        .join('\n');

    // Преобразуем строку для подписи и токен бота в массивы байтов
    const encoder = new TextEncoder();
    const botTokenBytes = encoder.encode(botToken);
    const launchParamsBytes = encoder.encode(launchParams);

    // Создаём secret_key: подписываем токен бота с помощью HMAC-SHA256,
    // используя строку "WebAppData" в качестве ключа
    const launchParamsKeyBytes = await crypto.subtle.sign(
        'HMAC',
        await crypto.subtle.importKey(
            'raw',
            encoder.encode('WebAppData'),
            { name: 'HMAC', hash: { name: 'SHA-256' } },
            false,
            ['sign'],
        ),
        botTokenBytes,
    );

    // Создаём подпись параметров с помощью HMAC-SHA256, используя secret_key
    const signature = await crypto.subtle.sign(
        'HMAC',
        await crypto.subtle.importKey(
            'raw',
            launchParamsKeyBytes,
            { name: 'HMAC', hash: { name: 'SHA-256' } },
            false,
            ['sign'],
        ),
        launchParamsBytes,
    );

    // Переводим подпись из массива байтов в hex-формат
    const hash = Array.from(new Uint8Array(signature))
        .map(b => ('00' + b.toString(16)).slice(-2))
        .join('');

    // Сравниваем с полученным хешем
    return hash === originalHash[1];
};

console.log(await validateAppData(appData, BOT_TOKEN));
```

### Версия для Node с исправленными краевыми случаями

```js
import crypto from 'node:crypto';

const MAX_AGE_SEC = 3600; // дока рекомендует считать данные валидными 1 час

export function parseAndValidate(initData, botToken) {
  const pairs = initData.split('&').map((p) => {
    const i = p.indexOf('=');                    // не split('=') — значение может содержать '='
    return i === -1 ? [p, ''] : [p.slice(0, i), p.slice(i + 1)];
  });

  if (pairs.filter(([k]) => k === 'hash').length !== 1) return null;
  const hash = pairs.find(([k]) => k === 'hash')[1];

  const decoded = pairs.map(([k, v]) => [k, decodeURIComponent(v)]);

  const launchParams = decoded
    .filter(([k]) => k !== 'hash')
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))  // байтовая сортировка, не локальная
    .map(([k, v]) => `${k}=${v}`)
    .join('\n');

  const secretKey = crypto.createHmac('sha256', 'WebAppData').update(botToken).digest();
  const signature = crypto.createHmac('sha256', secretKey).update(launchParams).digest('hex');

  // сравнение постоянного времени
  const a = Buffer.from(signature, 'hex');
  const b = Buffer.from(hash, 'hex');
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null;

  const data = Object.fromEntries(decoded);
  const authDate = Number(data.auth_date);
  if (!Number.isFinite(authDate)) return null;
  if (Math.abs(Date.now() / 1000 - authDate) > MAX_AGE_SEC) return null;  // защита от replay

  return {
    queryId: data.query_id,
    authDate,
    ip: data.ip,
    startParam: data.start_param,
    user: data.user ? JSON.parse(data.user) : null,
    chat: data.chat ? JSON.parse(data.chat) : null,
  };
}
```

Отличия от официального примера: корректный разбор значений с `=` внутри, байтовая сортировка вместо `localeCompare`, сравнение хешей постоянного времени, проверка `auth_date`.

---

## 7. Подводные камни

1. **`localeCompare` ≠ байтовая сортировка.** В примере доки сортировка идёт через `localeCompare`, который зависит от локали и правил коллации. На текущем наборе ключей (`auth_date`, `chat`, `ip`, `query_id`, `start_param`, `user` — только латиница и `_`) результат совпадает с байтовым, но если MAX добавит ключ с другим регистром или символом, клиент и сервер могут отсортировать по-разному. Используйте байтовое сравнение. **[вывод]**
2. **Двойное декодирование — самая частая ошибка.** Не скармливайте `initData` в `new URLSearchParams(initData)` с последующим `decodeURIComponent`: `URLSearchParams` уже декодирует значения, второй проход испортит строку, и подпись не сойдётся. Либо ручной `split` + `decodeURIComponent` (как в доке), либо `URLSearchParams` **без** ручного декодирования. Дока прямо оговаривает: декодировать, *«если ваша платформа не сделала это автоматически»*.
3. **`split('=')` ломается на значениях с `=`.** Официальный пример берёт `x[1]`, то есть кусок до первого `=`. Пока внутренние значения приходят percent-encoded (`%3D`), это безопасно, но base64-payload в `start_param` или `photo_url` с query-параметрами способны это сломать. Режьте по первому вхождению.
4. **Подписывается декодированный JSON — не пересобирайте его.** `JSON.parse` + `JSON.stringify` меняет порядок ключей и пробелы, подпись после этого не сойдётся. Парсите объекты только после успешной валидации.
5. **`hash` может прийти несколько раз.** Дока не зря требует проверку на ровно одно вхождение: два `hash` в строке — классический вектор обхода наивной валидации.
6. **`auth_date` не проверяется сам по себе.** Без проверки свежести валидный `initData`, утёкший из логов, работает вечно. Дока рекомендует окно в 1 час.
7. **`username` и `photo_url` бывают `null`** вопреки типу в интерфейсе (видно в примере самой доки).
8. **`WebAppPlatform` / `WebAppVersion` вне подписи** — только для UI-логики, не для проверок доступа.
9. **`initDataUnsafe` на бэкенде — запрещённый приём.** Если фронт отправляет на сервер распарсенный объект вместо сырой строки, валидация невозможна в принципе: подписи для объекта не существует.
10. **`chat.type === 'CHANNEL'` и групповой `CHAT`** означают, что `user` — не обязательно владелец контента, и один и тот же пользователь приходит с разными `chat.id`. Решите заранее, ключ сессии у вас — `user.id`, `chat.id` или пара. **[вывод]**

---

## 8. Чек-лист бэкенда

- [ ] Принимать **строку** `initData`, а не разобранный объект.
- [ ] Ровно одно вхождение `hash`.
- [ ] Разбор по первому `=`, декодирование значений ровно один раз.
- [ ] Байтовая сортировка ключей a → z, склейка через `\n`, `hash` исключён.
- [ ] `secret_key = HMAC("WebAppData", BOT_TOKEN)` — именно в таком порядке.
- [ ] Сравнение подписей постоянного времени.
- [ ] `auth_date` в пределах часа.
- [ ] Токен бота — только в переменных окружения, никогда не на фронте.
- [ ] Сессию выдавать по `user.id` из проверенных данных; `initDataUnsafe` — только для отрисовки.

---

## 9. Источники

- [MAX Bridge — dev.max.ru/docs/webapps/bridge](https://dev.max.ru/docs/webapps/bridge) — `initData`, `initDataUnsafe`, интерфейс `InitData`, описания полей
- [Валидация данных — dev.max.ru/docs/webapps/validation](https://dev.max.ru/docs/webapps/validation) — пример фрагмента URL, алгоритм, TypeScript-код
- [Подключение мини-приложения — dev.max.ru/docs/webapps/introduction](https://dev.max.ru/docs/webapps/introduction) — диплинки и `start_param`

**Оговорка:** `dev.max.ru` недоступен напрямую из этой среды, страницы получены через текстовый прокси-ридер (r.jina.ai). Пример фрагмента URL, интерфейс `InitData`, таблица полей и TypeScript-код пришли дословно; формулировки в §3 про точное содержимое `initData` — вывод из сопоставления двух страниц, их стоит проверить, залогировав реальный `window.WebApp.initData` на живом клиенте.
