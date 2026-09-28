# Документация сервисов

Документы разделены по исполнителю: общий Service SDK, четыре сервиса и размещение cloud на платформе. Это та же версия предложения `0.1.0`; пути, методы, тела и правила авторизации при разделении не изменены. Код приложения не добавляется.

## Комплекты

| Часть | OpenAPI | Список эндпоинтов | Спецификация | Операций | Реализация |
| --- | --- | --- | --- | --- | --- |
| Общее / SDK | [OPENAPI.yaml](sdk/OPENAPI.yaml) | [ENDPOINTS.txt](sdk/ENDPOINTS.txt) | [SPEC.md](sdk/SPEC.md) | 3 | — |
| Администрирование | [OPENAPI.yaml](administration/OPENAPI.yaml) | [ENDPOINTS.txt](administration/ENDPOINTS.txt) | [SPEC.md](administration/SPEC.md) | 20 | [IMPLEMENTATION.md](administration/IMPLEMENTATION.md) |
| Расписание | [OPENAPI.yaml](schedule/OPENAPI.yaml) | [ENDPOINTS.txt](schedule/ENDPOINTS.txt) | [SPEC.md](schedule/SPEC.md) | 13 | [IMPLEMENTATION.md](schedule/IMPLEMENTATION.md) |
| Анкеты пользователей | [OPENAPI.yaml](user-profile/OPENAPI.yaml) | [ENDPOINTS.txt](user-profile/ENDPOINTS.txt) | [SPEC.md](user-profile/SPEC.md) | 7 | [IMPLEMENTATION.md](user-profile/IMPLEMENTATION.md) |
| Локальные курсовые | [OPENAPI.yaml](coursework/OPENAPI.yaml) | [ENDPOINTS.txt](coursework/ENDPOINTS.txt) | [SPEC.md](coursework/SPEC.md) | 8 | [IMPLEMENTATION.md](coursework/IMPLEMENTATION.md) |

Полный API каждого сервиса = **три общие операции SDK + собственный контракт сервиса**. Числа включают HTML/asset endpoints. В сумме это исходная 51 операция без потерь и дубликатов между комплектами. В administration 19 операций фасада ядра и один HTML entrypoint.

Каждый YAML валиден самостоятельно и содержит все необходимые схемы с локальными $ref. Общие операции физически описаны только в SDK, а используемые доменными операциями общие схемы включены в их YAML для удобства инструментов. При изменении общих форматов эти копии нужно обновлять согласованно. ENDPOINTS.txt точно соответствует paths своего YAML, без добавления общих путей к каждому сервису.

Шаблон своего сервиса вуза на минимальном SDK — [sdk/service-template](sdk/service-template/README.md): каркас серверной части и клиента iframe, который подключается через «Свой сервис» в администрировании.

## С чего читать

1. [SDK/SPEC.md](sdk/SPEC.md) — общие проверки, контекст экземпляра, клиент ядра, HTTP и iframe. В первой таблице явно разделены обязанности библиотеки и сервиса.
2. Спецификация нужного сервиса — его права, меню, данные, транзакции и критерии приёмки.
3. [CLOUD_RUNTIME_SPEC.md](CLOUD_RUNTIME_SPEC.md) — один процесс на тип, доставка bindings и создание/отключение экземпляров. Это работа платформы; универсальный SDK только потребляет доверенные настройки.

Столбец «Реализация» ведёт на разбор написанного кода: что сделано, где реализация отходит от контракта и как это проверяется. Контракты описывают целевое поведение и меняются отдельно от кода.

Ядро остаётся источником пользователей, членств, профилей, ролей и сессий. Сервисы владеют своими учебными данными; administration — фасад без второго реестра. Сервисы не вызывают друг друга в MVP: schedule/coursework могут работать без установленного user-profile.

## Исходные материалы

- [Манифест](../SPEC_MANIFEST.md).
- [Контракт ядра](../core/CORE_API_OPENAPI.yaml), [его эндпоинты](../core/CORE_API_ENDPOINTS.txt), [спецификация](../core/CORE_API_SPEC.md), [разбор TODO](../core/CORE_API_TODO_REPORT.md).
- [Прежний сводный контракт сервисов](archive/SERVICES_API_OPENAPI.yaml), [его список](archive/SERVICES_API_ENDPOINTS.txt), [прежняя спецификация](archive/SERVICES_API_SPEC.md) сохранены как исходный снимок этой работы. Для дальнейшего чтения и правок используйте разделённые комплекты выше.

При переносе сохранено поведение HTTP-контрактов. Архив нужен для сверки полноты разделения, а не как отдельная параллельная версия проекта.
