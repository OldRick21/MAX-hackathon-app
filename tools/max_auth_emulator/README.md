# MAX auth emulator

Локальная CLI-утилита для контролируемого тестирования первичного входа через MAX. Она создаёт
настоящий подписанный `WebApp.initData`, который проходит штатную проверку ядра. Утилита не выдаёт
JWT и не меняет пользователей, профили или роли в базе.

Требуется Python 3.10 или новее. Сторонних зависимостей нет.

## Сырой initData

PowerShell:

```powershell
$env:MAX_BOT_TOKEN = '<bot token>'
python -m tools.max_auth_emulator init-data --user-id 123456789
```

Bash:

```bash
MAX_BOT_TOKEN='<bot token>' python -m tools.max_auth_emulator init-data --user-id 123456789
```

Полученное значение передаётся ядру без изменений:

```json
{"initData":"auth_date=...&query_id=...&user=...&hash=..."}
```

## Ссылка запуска frontend

```powershell
python -m tools.max_auth_emulator url `
  --user-id 123456789 `
  --base-url https://example.org/
```

Утилита напечатает ссылку вида `https://example.org/#WebAppData=...&WebAppPlatform=web`.
Frontend прочитает `WebAppData` из фрагмента и самостоятельно отправит `initData` в
`POST /api/v1/auth/token`.

Подписанные необязательные поля пользователя:

```powershell
python -m tools.max_auth_emulator url `
  --user-id 123456789 `
  --first-name Иван `
  --last-name Иванов `
  --username ivan `
  --base-url https://example.org/
```

Для отрицательных тестов можно явно задать `--auth-date <unix timestamp>` и `--query-id <value>`.
Токен также можно передать первой строкой stdin с флагом `--token-stdin`. Передавать токен аргументом
командной строки нельзя, чтобы он не попадал в историю shell и список процессов.

## Безопасность

- Не публикуйте токен бота, сырой `initData` и сгенерированные ссылки в логах или CI-артефактах.
- `initData` является временным bearer credential: ядро принимает его не дольше 300 секунд с момента
  `auth_date`.
- Любой владелец токена бота может сгенерировать вход для любого положительного MAX `user.id`.
- Утилита не поднимает HTTP-сервер и не должна включаться в публичный frontend.
- `ALLOW_DEV_LOGIN` для её работы не нужен и должен оставаться выключенным.
