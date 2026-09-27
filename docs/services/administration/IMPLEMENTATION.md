# Сервис «Администрирование»: реализация

Документ описывает код в `services/administration/` и связанные изменения ядра (`backend/`). Контракт — [SPEC.md](SPEC.md) и [OPENAPI.yaml](OPENAPI.yaml). Здесь перечислено, что реализовано, какие операции добавлены сверх контракта и где реализация сознательно проще целевой архитектуры.

## Компоненты

| Где | Что делает |
| --- | --- |
| `services/administration/app/main.py` | Процесс сервиса (один на все вузы): SDK-проверка запроса, 25 явных операций фасада, `GET /api/v1/service`, `/admin`, assets с content hash |
| `services/administration/app/core_client.py` | Клиент ядра: binding по UUID, machine JWT с кэшем по credential/revision, online introspection без кэша, вызовы private API, отказ → 503 |
| `services/administration/client/` | Интерфейс в iframe: протокол SDK (init/ready/context/refresh/session_ended), разделы «Вуз», «Участники», «Сервисы», «Роли», «Журнал» |
| `backend/routes/private_admin.py`, `services/institution_admin.py` | Private API вуза: все операции контракта + расширения, ETag/If-Match, Idempotency-Key, LAST_OWNER, журнал |
| `backend/auth/authorization.py` | Три проверки private API: сеть, machine credential administration этого вуза, actor с живыми сессиями и правами из БД |
| `backend/routes/platform.py`, `services/platform_support.py` | Заявки вузов, поддержка платформы, provisioning bindings |
| `backend/platform_core/` | Каталог типов, валидация URL/manifest/ролей, ETag, курсоры, общие операции реестра |
| `backend/manage.py` | Одноразовые команды оператора: первый сотрудник поддержки, восстановление владельца |
| `frontend/static/js/platform.js` | В оболочке ядра: «Подключить свой вуз» и «Поддержка платформы» |

## Разделение полномочий

- **Поддержка платформы** (`platform_staff`) рассматривает заявки, меняет статус вуза active/suspended, ведёт список одобренных хостов для local и может назначить владельца **только вузу без владельцев**. Участников, роли и сервисы вуза она не видит и не меняет. Одобрить собственную заявку нельзя.
- **Администратор вуза** действует только через сервис «Администрирование» и только по ролям `owner`, `technical_admin`, `membership_admin`. Профиль `admin` без роли прав не даёт. Прежний код выдавал все права любому admin-профилю — это исправлено.
- Роли administration назначает только `owner`; `roles.manage` без `owner` этого не позволяет. Участника с ролью `owner` может изменить или удалить только владелец. Последнего владельца снять нельзя (`409 LAST_OWNER`) — проверка идёт под блокировкой записи вуза.

## Первый администратор

1. Оператор один раз выполняет `python manage.py grant-platform-role <UUID>` для первого сотрудника поддержки.
2. Пользователь в приложении нажимает «Подключить свой вуз» и отправляет заявку.
3. Поддержка одобряет её в разделе «Поддержка платформы». В одной транзакции создаются active-вуз, защищённый экземпляр administration, его системные роли, credential и binding; заявитель получает membership с профилем `admin` и роль `owner`.
4. Для вузов, созданных до этой версии (у них нет владельца, так как роль «по умолчанию» удалена), поддержка назначает владельца в карточке вуза, либо оператор выполняет `python manage.py assign-owner <вуз> <пользователь>`.

## Операции сверх контракта

Все они проходят те же проверки и пишутся в журнал. В OpenAPI пока не внесены.

| Метод и путь фасада | Private API ядра | Право |
| --- | --- | --- |
| GET `/api/v1/administration/audit` | GET `.../internal/audit` | institution.read |
| PUT `/api/v1/administration/services/{id}/manifest` | PUT `.../internal/services/{id}/manifest` | services.manage (только local) |
| POST `/api/v1/administration/services/{id}/roles` | POST `.../internal/services/{id}/roles` | roles.manage (не administration) |
| GET/PATCH/DELETE `/api/v1/administration/services/{id}/roles/{code}` | то же в `.../internal` | services.read / roles.manage |

Платформенные (core, публичный ingress, core access):

```
GET  /api/v1/platform/me
GET  /api/v1/institution-applications
POST /api/v1/institution-applications
POST /api/v1/institution-applications/{id}/withdraw
GET  /api/v1/platform/applications?status=
GET  /api/v1/platform/applications/{id}
POST /api/v1/platform/applications/{id}/approve
POST /api/v1/platform/applications/{id}/reject
GET  /api/v1/platform/institutions
GET  /api/v1/platform/institutions/{id}
PATCH /api/v1/platform/institutions/{id}               (If-Match; status active|suspended)
PUT  /api/v1/platform/institutions/{id}/local-hosts    (If-Match)
POST /api/v1/platform/institutions/{id}/initial-owner
GET  /api/v1/platform/audit
GET  /api/v1/internal/provisioning/bindings/{service_id}   (private listener, токен deployment)
```

Новые коды ошибок: `APPLICATION_LIMIT`, `APPLICATION_NOT_PENDING`, `OWNER_EXISTS`, `INSTANCE_NOT_READY` (из SDK).

## Отличия от целевой архитектуры

- **Проверка service JWT.** Ядро подписывает RS256 и публикует access-ключи в JWKS. Администрация проверяет чувствительные операции через online introspection на каждом запросе (fail closed). Непроверенные `service_id/institution_id` используются только для выбора **уже зарегистрированного** binding; ядро проверяет подпись, issuer, audience, тип токена и живые сессии, затем возвращает актуальные права. Фасад сопоставляет также `sid` и `parent_sid` с ответом ядра.
- **Доставка bindings.** Вместо общей таблицы в PostgreSQL (CLOUD_RUNTIME_SPEC §2) — закрытый эндпоинт provisioning. Секрет cloud credential в БД не хранится: он выводится из `CLOUD_BINDING_KEY`; смена ключа и перезапуск ядра ротируют все cloud credentials.
- **Удаление экземпляра** физическое (с каскадом ролей, назначений, сессий, credentials, binding), а не tombstone. UUID не переиспользуется, журнал сохраняется. Tombstone потребует миграции уникального индекса.
- **ETag** вычисляется по содержимому представления (sha256), отдельного счётчика revision нет.
- **Rate limits** из спецификации не реализованы.

## Проверка

```bash
cd backend && python -m unittest tests.test_platform_core -v          # без зависимостей
cd backend && python -m unittest tests.test_administration_flow -v    # нужен requirements.txt
cd administration && python -m unittest tests.test_core_client -v      # requests, PyJWT
```

`test_max_integration` и `test_administration_flow` задают окружение при импорте, запускайте их отдельными командами.
