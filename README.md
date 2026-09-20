# MAX Mini App

Мини-приложение для MAX на HTML, CSS и JavaScript и бот на Node.js.

Страница использует MAX Bridge и показывает имя пользователя при запуске внутри MAX. В обычном браузере отображается подсказка открыть приложение в MAX.

Бот использует официальную библиотеку @maxhub/max-bot-api и отправляет кнопку открытия привязанного мини-приложения по команде /start.

## Структура проекта

```text
.
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
        ├── docker-nginx.conf
        └── max-miniapp.conf
```

Docker Compose запускает два сервиса:

- bot — бот, получающий события MAX через long polling.
- web — Nginx, обслуживающий страницу приложения.

Контейнер сайта использует web/configs/docker-nginx.conf.
Файл max-miniapp.conf сохранён для прежнего размещения через системный Nginx и контейнером не используется.

## Требования

- Docker Engine и Docker Compose.
- Токен бота MAX.
- Привязанный к боту HTTPS-адрес мини-приложения для запуска внутри MAX.

## Настройка

При первом запуске создайте файл настроек:

```bash
cp .env.example .env
chmod 600 .env
```

Укажите в .env настоящий токен:

```dotenv
BOT_TOKEN=YOUR_BOT_TOKEN
```

Файл .env исключён из Git. Не публикуйте токен в исходном коде.

Публичный корневой сертификат в bot/ используется Node.js для проверки TLS-соединения с API MAX через NODE_EXTRA_CA_CERTS.

## Запуск

Из корня репозитория:

```bash
sudo docker compose up -d --build
```

Перед запуском остановите другие экземпляры этого же бота, получающие события через long polling.

## Проверка

```bash
sudo docker compose ps
sudo docker compose logs -f --tail=100 bot
curl -I http://127.0.0.1:8080
```

Отправьте боту /start в MAX и нажмите кнопку открытия приложения.

## Доступ к сайту

Текущая конфигурация публикует сайт только на локальном интерфейсе VDS:

http://127.0.0.1:8080

Для публичного доступа нужно настроить обратный прокси или изменить публикацию портов. Для открытия внутри MAX необходим HTTPS и привязка адреса к боту.

Compose пока не настраивает HTTPS и не переключает существующий системный Nginx на контейнер.

## Управление

```bash
# Пересобрать и применить изменения
sudo docker compose up -d --build

# Логи всех сервисов
sudo docker compose logs -f --tail=100

# Перезапустить бота
sudo docker compose restart bot

# Остановить и удалить контейнеры проекта
sudo docker compose down
```

Файлы проекта и .env сохраняются после docker compose down.

## Документация

- [Мини-приложения MAX](https://dev.max.ru/docs/webapps/introduction)
- [MAX Bridge](https://dev.max.ru/docs/webapps/bridge)
- [Библиотека бота для JavaScript](https://dev.max.ru/docs/chatbots/bots-coding/js)
