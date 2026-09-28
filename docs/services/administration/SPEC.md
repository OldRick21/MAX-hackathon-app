# Сервис администрирования

Версия `0.1.0`, 26 сентября 2026. Облачный фасад управления вузом; своей копии реестра и членств нет. Контракт разделён без изменения операций; код не реализуется.

[OpenAPI](OPENAPI.yaml) · [Эндпоинты](ENDPOINTS.txt) · [Общее и SDK](../sdk/SPEC.md) · [Навигация](../INDEX.md).

Этот комплект содержит 20 собственных операций. Полный API дополняется тремя общими операциями [SDK](../sdk/OPENAPI.yaml). Все входящие защищённые запросы используют ServiceBearer и online introspection по общим правилам.

## 1. Размещение и меню

Один процесс administration обслуживает все вузы; только этот тип имеет сетевой доступ к private listener ядра. У каждого вуза свой binding и credential. См. [облачную схему](../CLOUD_RUNTIME_SPEC.md).

- api_base_url: `https://administration.platform.example/api/v1`.
- client_base_url: `https://administration.platform.example`.
- Поддерживаемый профиль: только `admin`.
- Меню `administration`: «Администрирование», `/admin`, order=0, profiles=[admin], required_permissions=[].

Меню не даёт всех прав: кнопки и операции проверяют актуальные permissions. Учебные данные расписания, анкет и курсовых этот сервис не редактирует: соответствующие права назначаются в целевых экземплярах.

## 2. Публичный фасад и обращения в ядро

Префикс сервиса: `/api/v1/administration`. Он необходим, поскольку браузер не имеет доступа к `/api/v1/institution/{institution_id}/internal` ядра.

Соответствие механическое: публичный суффикс после `/administration` добавляется к private префиксу ядра, а `institution_id` подставляется из actor JWT. Например:

| Пользователь → administration | Administration → private ядро |
| --- | --- |
| `GET /api/v1/administration/members` | `GET /api/v1/institution/{actor_institution}/internal/members` |
| `PUT /api/v1/administration/members/{user_id}/profiles` | `PUT /api/v1/institution/{actor_institution}/internal/members/{user_id}/profiles` |
| `POST /api/v1/administration/services` | `POST /api/v1/institution/{actor_institution}/internal/services` |

Все операции перечислены в OpenAPI; `x-core-operation` указывает точный метод/путь/operationId ядра. Публичная операция принимает один ServiceBearer. Backend сам задаёт `Authorization: Bearer <machine administration>` и `X-Actor-Token: <исходный service access>`. Присланные браузером X-Actor-Token, tenant-заголовки, upstream URL и machine credentials не используются; arbitrary proxy endpoint отсутствует. `service_id` в суффиксе — целевой сервис управления **в этом вузе**, не возможность выбрать чужой administration binding.

Тела, фильтры, пагинация и strong ETag совпадают с ядром. Фасад пересылает If-Match и Idempotency-Key; Location заменяет на свой публичный путь, private URL не выдаёт. Ошибки валидации, прав actor и конфликтов ядра сохраняют смысл. Сбой транспорта, неправильный machine credential или неожиданный ответ ядра дают 503. X-Request-ID ответа и error.request_id принадлежат публичному запросу; upstream request ID остаётся в серверном журнале для связи. Выдача секретов не повторяется автоматически при потере ответа.

Проверки последнего owner, неизменяемых системных ролей, запрета удаления administration и scope целевого сервиса выполняются ядром заново. Administration не может сделать их слабее. Назначения ролей собственного типа меняет только owner; `roles.manage` сам по себе этого не разрешает. Остальные permissions и роли administration без изменений берутся из §2.3 [CORE_API_SPEC.md](../../core/CORE_API_SPEC.md).

## 3. Права и системные роли

Правила ниже перенесены из [спецификации ядра](../../core/CORE_API_SPEC.md); SDK проверяет профиль/permission, а private API ядра повторяет проверки и обеспечивает транзакционные ограничения.

| Permission администратора | Операции private API |
| --- | --- |
| `institution.read` | Карточка настроек вуза, каталог типов |
| `institution.update` | Изменение имени и языка вуза |
| `members.read` | Список/карточка участников |
| `members.manage` | Добавление/удаление участника, изменение профилей |
| `services.read` | Список/карточка экземпляров, их роли |
| `services.manage` | Установка, настройка, удаление разрешённых экземпляров |
| `roles.manage` | Чтение и замена назначений ролей в любом экземпляре данного вуза |
| `credentials.manage` | Список, выдача и отзыв credentials локального экземпляра |

У admin service системные неизменяемые роли: `owner` (все permissions таблицы), `technical_admin` (`institution.read`, `services.read`, `services.manage`, `credentials.manage`), `membership_admin` (`institution.read`, `members.read`, `members.manage`). У всех допустим только профиль `admin`. Публичный machine API этого экземпляра может читать, но **не изменять** определения и назначения ролей (`403 PROTECTED_RESOURCE`). Назначения меняются через private API только actor с текущей ролью `owner`; одного `roles.manage` недостаточно. Это препятствует эскалации до owner через собственное назначение. У остальных типов ролевую модель обслуживает их backend.

Нельзя удалить/отключить экземпляр администрирования, переписать его endpoints либо убрать последнего owner удалением членства, профиля или роли. Проверка последнего owner и изменение выполняются в одной транзакции под блокировкой записи вуза; иначе два параллельных запроса могут оба пройти проверку. Ошибка — `409 LAST_OWNER` или `409 PROTECTED_RESOURCE` соответственно.

Профиль в суффиксе `/users/{user_id}/profiles/{profile}/roles` относится к целевому участнику и не переключает профиль actor.

## 4. Создание экземпляров и credentials

Начальный administration и первого owner создаёт оператор платформы. Процесс не запускается для каждого вуза заново. Cloud-установка и доставка binding описаны в [облачной схеме](../CLOUD_RUNTIME_SPEC.md), подключение local — в [спецификации coursework](../coursework/SPEC.md).

Фасад сохраняет idempotency/preconditions ядра. Добавлению membership и выдаче credential не приписывается replay-гарантия. При потере ответа добавление проверяют GET; для credential смотрят список, выпускают новый и отзывают неизвестный. Отзыв credential не требует If-Match. Тела выдачи секретов не журналируются и не сохраняются в браузере.

## 5. Хранение и приёмка

Членства, роли, настройки и экземпляры хранятся только ядром. Фасад не заводит второй источник истины; для HTTP-аудита связывает свой request_id с upstream request_id без записи токенов/секретов.

1. Браузер вызывает только публичный фасад; private-префикс ядра извне по-прежнему возвращает 404.
2. Tenant, machine credential и actor нельзя подменить входящими заголовками; произвольного proxy нет.
3. Все 19 операций фасада сохраняют тела, статусы и permissions соответствующих операций ядра; Location меняется на публичный путь.
4. Не-owner не назначает роли administration; параллельные удаления не оставляют вуз без последнего owner.
5. Cloud credentials не выдаются вузу; потерянный ответ выдачи local credential не повторяется автоматически.
