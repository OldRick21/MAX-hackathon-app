# MAX Mini App

Мини-приложение MAX для платформы сервисов вузов. Планируется единая точка доступа к расписанию и профилю пользователя с возможностью состоять в нескольких вузах.

Сейчас реализованы страница приветствия с MAX Bridge и бот, который отправляет кнопку открытия приложения. Серверная аутентификация, регистрация вузов и привязка пользователей к вузам ещё не реализованы. Добавлена заглушка backend на Python и Flask с маршрутом проверки работы. Текущий бот работает на Node.js.

## Структура проекта

```text
.
├── README.md
├── compose.yaml
├── .env.example
├── .gitignore
├── backend/
│   ├── app.py
│   ├── Dockerfile
│   ├── .dockerignore
│   └── requirements.txt
├── bot/
│   ├── bot.mjs
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── package.json
│   ├── package-lock.json
│   └── russian_trusted_root_ca_pem.crt
├── frontend/
│   ├── templates/
│   │   └── index.html
│   └── static/
│       ├── css/
│       │   └── styles.css
│       └── js/
│           └── app.js
└── web/
    └── configs/
        └── docker-nginx.conf
```

## Работа с интерфейсом

Весь интерфейс находится в `frontend/`:

- `templates/index.html` — разметка страницы.
- `static/css/styles.css` — оформление, адаптивность и цветовые темы.
- `static/js/app.js` — поведение страницы и получение данных из MAX Bridge для отображения.

Дизайн можно менять независимо от кода бота. Новые шаблоны добавляем по мере появления страниц. Сейчас HTML отдаётся как обычный статический файл: шаблонизатор и Flask к выдаче страниц ещё не подключены.

Для локального просмотра нужен Python 3. Из корня репозитория выполните:

```bash
python3 -m http.server 8000 --bind 127.0.0.1 --directory frontend
```

Откройте [локальную страницу](http://127.0.0.1:8000/templates/index.html). Для остановки нажмите `Ctrl+C` в терминале.

Для такого просмотра не нужны Docker, токен бота и настройки сервера. После изменения файлов обновите страницу в браузере. Если браузер использует старые стили или скрипты, обновите страницу с очисткой кеша.

В обычном браузере страница предлагает открыть приложение через MAX. Если MAX Bridge не загрузился, выводится сообщение об ошибке загрузки. При запуске внутри MAX отображается имя пользователя, если оно передано. Эти данные используются только для приветствия; серверная проверка `initData` для аутентификации пока отсутствует.

## Python backend

В `backend/` находится минимальная заглушка на Flask. Единственный маршрут — `GET /api/health`. База данных и бизнес-логика пока не подключены.

Backend запускается в Docker через Gunicorn. Для запуска только backend из корня репозитория:

```bash
sudo docker compose up -d --build backend
sudo docker compose exec backend python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health').read().decode())"
```

Порт backend доступен внутри сети Compose. В полном серверном запуске Nginx направляет `/api/` в backend, поэтому проверка снаружи выполняется по адресу `https://195.133.197.144/api/health`.

При необходимости можно запустить Flask без Docker для локальной разработки:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
python -m flask --app backend.app run --host 127.0.0.1 --port 5000
```

В другом терминале проверьте ответ:

```bash
curl http://127.0.0.1:5000/api/health
```

Ожидаемый JSON:

```json
{"status": "ok", "service": "backend", "mode": "placeholder"}
```

Команда `flask run` запускает только локальный сервер разработки. В Docker используется Gunicorn.

## Контейнеры

Обычный запуск Docker Compose поднимает три сервиса:

- `bot` — бот на Node.js с библиотекой `@maxhub/max-bot-api`; получает события через long polling и отправляет кнопку приложения.
- `backend` — заглушка API на Python, Flask и Gunicorn. Nginx запускается после успешной проверки здоровья backend.
- `web` — Nginx; отдаёт `frontend/templates/` из корня сайта, а `frontend/static/` — по адресу `/static/`.

Сервис `certbot` включён в профиль `tools` и запускается отдельно для обслуживания сертификата.

Адрес приложения привязывается к боту на стороне MAX. В коде бота используется ссылка на самого бота для кнопки открытия привязанного приложения.

## Запуск на настроенном сервере

Текущий Compose использует серверную конфигурацию для `195.133.197.144`. Nginx слушает порты 80 и 443; запросы страницы на порт 80 перенаправляются на защищённый адрес.

Для запуска нужны:

- Docker Engine и Docker Compose.
- Токен бота и доступ к API MAX.
- Свободные порты 80 и 443 с разрешённым входящим трафиком.
- Существующие файлы `fullchain.pem` и `privkey.pem` в `/var/lib/max-miniapp/letsencrypt/live/max-miniapp/` и вся структура каталога Let’s Encrypt, на которую они ссылаются.
- Каталог `/var/lib/max-miniapp/acme/` для проверки владения адресом при продлении сертификата.

Эти серверные данные находятся вне репозитория. На новой машине одного клонирования недостаточно: текущий Nginx не запустится без сертификата. Для работы только с дизайном используйте локальный просмотр выше.

При первом запуске создайте `.env` из корня репозитория:

```bash
cp .env.example .env
chmod 600 .env
```

Укажите в нём токен:

```dotenv
BOT_TOKEN=YOUR_BOT_TOKEN
```

Существующий `.env` при обновлении не перезаписывайте. Он исключён из Git.

Остановите другие экземпляры этого же бота, затем выполните:

```bash
sudo docker compose config --quiet
sudo docker compose up -d --build
```

Контейнеры работают в фоне. Политика `restart: unless-stopped` обеспечивает их запуск после перезапуска Docker, если они не были остановлены вручную.

## Проверка сервера

```bash
sudo docker compose ps
sudo docker compose logs --tail=50 bot web
curl -I https://195.133.197.144
curl https://195.133.197.144/api/health
```

С другого устройства откройте [приложение](https://195.133.197.144). Отправьте боту `/start` в MAX и проверьте кнопку открытия приложения.

## Обновление на сервере

После отправки локальных изменений в Git выполните на сервере из папки репозитория:

```bash
git pull --ff-only
sudo docker compose up -d --build
```

После перехода со старой папки `web/templates/` на `frontend/` пересоздайте веб-контейнер, чтобы применить подключения папок:

```bash
sudo docker compose up -d --force-recreate web
```

HTML, CSS и JavaScript подключены в контейнер из репозитория. Для следующих изменений содержимого этих файлов пересборка образа не нужна — достаточно обновить страницу в браузере.

После изменения конфигурации Nginx пересоздайте контейнер и проверьте результат:

```bash
sudo docker compose up -d --force-recreate web
sudo docker compose exec web nginx -t
sudo docker compose logs --tail=50 web
```

После изменения кода бота пересоберите его:

```bash
sudo docker compose up -d --build bot
```

После изменения Python-кода или зависимостей пересоберите backend:

```bash
sudo docker compose up -d --build backend
```

## Управление

```bash
# Статус
sudo docker compose ps

# Логи
sudo docker compose logs -f --tail=100

# Перезапуск бота
sudo docker compose restart bot

# Остановка
sudo docker compose stop

# Остановка и удаление контейнеров проекта
sudo docker compose down
```

Файлы проекта, `.env` и серверные данные в `/var/lib/max-miniapp/` сохраняются после `docker compose down`.

## Документация

- [Мини-приложения MAX](https://dev.max.ru/docs/webapps/introduction)
- [MAX Bridge](https://dev.max.ru/docs/webapps/bridge)
- [Библиотека бота для JavaScript](https://dev.max.ru/docs/chatbots/bots-coding/js)
