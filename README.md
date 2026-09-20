# MAX Mini App

Мини-приложение для MAX на HTML, CSS и JavaScript и бот на Node.js.

Страница использует MAX Bridge и показывает имя пользователя при запуске внутри MAX. В обычном браузере отображается подсказка открыть приложение через MAX.

Бот использует официальную библиотеку @maxhub/max-bot-api и отправляет кнопку открытия привязанного мини-приложения по команде /start.

## Структура проекта

```text
.
├── README.md
├── compose.yaml
├── .env.example
├── .gitignore
├── bot/
│   ├── bot.mjs
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── package.json
│   ├── package-lock.json
│   └── russian_trusted_root_ca_pem.crt
└── web/
    ├── templates/
    │   └── index.html
    └── configs/
        └── docker-nginx.conf
```

## Как это работает

Docker Compose запускает два контейнера:

- bot — получает события MAX через long polling и отвечает пользователям.
- web — Nginx, который отдаёт страницу сайта на публичном порту 80.

```text
Браузер → порт 80 VDS → контейнер web → index.html
MAX API ↔ контейнер bot
```

Отдельный системный Nginx для этой схемы не требуется.

Адрес мини-приложения привязывается к боту на стороне MAX. Код бота получает свой username и создаёт кнопку открытия связанного мини-приложения.

## Требования

- Docker Engine и Docker Compose.
- Доступ к API MAX с сервера.
- Токен бота MAX.
- Свободный порт 80 и разрешённый входящий TCP-трафик на него.

## Настройка

При первом запуске из корня репозитория:

```bash
cp .env.example .env
chmod 600 .env
```

В файле .env укажите токен:

```dotenv
BOT_TOKEN=YOUR_BOT_TOKEN
```

Не перезаписывайте существующий .env при обновлении проекта.

Файл .env исключён из Git. Настоящий токен не должен попадать в исходный код или коммиты.

## Запуск

Перед запуском остановите другие экземпляры этого же бота и освободите порт 80.

```bash
sudo docker compose config --quiet
sudo docker compose up -d --build
```

Оба контейнера работают в фоне. Политика restart: unless-stopped обеспечивает автоматический запуск при старте Docker, если контейнеры не были остановлены вручную.

## Проверка

```bash
sudo docker compose ps
sudo docker compose logs --tail=50 bot
curl -I http://127.0.0.1
```

С другого устройства откройте:

```text
http://<IP-сервера>
```

Отправьте боту /start в MAX и проверьте появление кнопки приложения.

## Обновление

```bash
git pull --ff-only
sudo docker compose up -d --build
```

Страница и конфигурация Nginx подключены из репозитория через bind mounts.

После изменения HTML обновите страницу в браузере. Если изменения не появились, пересоздайте контейнер web.

После изменения конфигурации Nginx проверьте её и пересоздайте контейнер:

```bash
sudo docker compose exec web nginx -t
sudo docker compose up -d --force-recreate web
```

После изменения кода бота пересоберите его:

```bash
sudo docker compose up -d --build bot
```

## Управление

```bash
# Статус контейнеров
sudo docker compose ps

# Просмотр логов
sudo docker compose logs -f --tail=100

# Перезапуск бота
sudo docker compose restart bot

# Остановка контейнеров
sudo docker compose stop

# Остановка и удаление контейнеров проекта
sudo docker compose down
```

Файлы проекта и .env сохраняются после docker compose down.

## Документация

- [Мини-приложения MAX](https://dev.max.ru/docs/webapps/introduction)
- [MAX Bridge](https://dev.max.ru/docs/webapps/bridge)
- [Библиотека бота для JavaScript](https://dev.max.ru/docs/chatbots/bots-coding/js)
