# Локальный MAX-less деплой

MAX-less режим поднимает полноценные ядро, frontend, Redis, пульт оператора и три облачных раннера
на одном компьютере. Контейнер MAX-бота не создаётся. Первичный вход выполняется через
[`tools/max_auth_emulator`](../../tools/max_auth_emulator/README.md) с отдельным случайным локальным
bot token.

Локальный стек изолирован от обычного deployment:

- Compose project — `maxhack-maxless`;
- отдельные volumes и сеть;
- отдельный файл секретов `.maxless/maxless.env`;
- все опубликованные порты слушают только `127.0.0.1`;
- production-файл `.env` не читается и не изменяется;
- контейнер `bot` не существует в MAX-less Compose-файле.

`.maxless/` находится в `.gitignore`. В Git попадают только скрипты, Compose-файл и конфигурация
локального Nginx; токены, пароли, JWT-ключи, сертификат и базы остаются локальными.

## Windows — основной вариант

Требуются запущенный Docker Desktop с Compose v2 и Python для генерации ссылки входа.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\maxless-deploy.ps1 -UserId 123456789
```

Скрипт напечатает готовую ссылку с `#WebAppData`. Она действует пять минут. Откройте её в браузере
и подтвердите исключение для локального самоподписанного сертификата.

Повторный запуск сохраняет базы и секреты:

```powershell
.\scripts\maxless-deploy.ps1 -NoBuild -UserId 987654321
```

## Linux

Требуются Docker Engine, Compose v2, OpenSSL и Python.

```bash
chmod +x scripts/maxless-deploy.sh
./scripts/maxless-deploy.sh --user-id 123456789
```

Повторный запуск без сборки образов:

```bash
./scripts/maxless-deploy.sh --no-build --user-id 987654321
```

## Адреса

По умолчанию:

- приложение — `https://localhost:18443/`;
- отдельный origin администрирования — `https://localhost:18444/`;
- пульт оператора — `http://localhost:18500/`.

Порты можно изменить в `.maxless/maxless.env` до создания тестовых вузов. После изменения портов
перезапустите скрипт без `--no-build`. Пароль пульта хранится там же в `OPERATOR_PASSWORD`.

## Тестовые данные

Модуль `test-data/operator-demo` загружается в пульт тем же способом, что и в обычном `deploy.sh`:
bootstrap-контейнер копирует все каталоги `test-data/*/`, содержащие `plugin.py`, в отдельный volume
плагинов. Откройте пульт оператора → «Обслуживание» → «Тестовые данные», чтобы создать вузы,
пользователей, группы и расписание. При повторном MAX-less деплое набор подключённых модулей
синхронизируется заново.

## Управление

Состояние и логи:

```powershell
docker compose --project-name maxhack-maxless --env-file .maxless/maxless.env -f compose.maxless.yaml ps
docker compose --project-name maxhack-maxless --env-file .maxless/maxless.env -f compose.maxless.yaml logs -f --tail=100
```

Остановить, сохранив данные:

```powershell
docker compose --project-name maxhack-maxless --env-file .maxless/maxless.env -f compose.maxless.yaml down
```

Удаление volumes уничтожит локальные базы, JWT-ключи, сертификат и данные сервисов; скрипты этого
автоматически не делают.
