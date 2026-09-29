<div align="center">

# Вузы России

**Единая платформа сервисов вузов внутри мессенджера MAX**

[![MAX Mini App](https://img.shields.io/badge/MAX-mini%20app-0077FF?style=for-the-badge)](docs/core/MAX_DEPLOYMENT.md)
[![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.118-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00?style=for-the-badge&logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![Pydantic](https://img.shields.io/badge/Pydantic-2-E92063?style=for-the-badge&logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![SQLite](https://img.shields.io/badge/SQLite-3-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://www.sqlite.org/)

[![React](https://img.shields.io/badge/React-18.3-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.9-3178C6?style=for-the-badge&logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Vite](https://img.shields.io/badge/Vite-6.4-646CFF?style=for-the-badge&logo=vite&logoColor=white)](https://vite.dev/)
[![Node.js](https://img.shields.io/badge/Node.js-22%20%7C%2024-5FA04E?style=for-the-badge&logo=nodedotjs&logoColor=white)](https://nodejs.org/)

[![Docker Compose](https://img.shields.io/badge/Docker%20Compose-v2-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Nginx](https://img.shields.io/badge/Nginx-stable-009639?style=for-the-badge&logo=nginx&logoColor=white)](https://nginx.org/)
[![Redis](https://img.shields.io/badge/Redis-7-FF4438?style=for-the-badge&logo=redis&logoColor=white)](https://redis.io/)
[![JWT](https://img.shields.io/badge/JWT-RS256-000000?style=for-the-badge&logo=jsonwebtokens&logoColor=white)](docs/core/CORE_API_SPEC.md)
[![OpenAPI](https://img.shields.io/badge/OpenAPI-3.1-6BA539?style=for-the-badge&logo=openapiinitiative&logoColor=white)](openapi.yaml)
[![E2E](https://img.shields.io/badge/e2e-47%2F47-2EA44F?style=for-the-badge&logo=githubactions&logoColor=white)](scripts/e2e)

</div>

---

Пользователь открывает мини-приложение через бота MAX, выбирает вуз и профиль — **студент**, **преподаватель** или **администратор** — и работает с сервисами вуза: расписанием, анкетами участников, курсовыми и любыми своими сервисами вуза. Один человек может состоять в нескольких вузах и иметь в каждом несколько профилей.

Платформа состоит из **ядра** (вход через MAX, вузы, участники, профили, роли, сессии, учебные группы) и **сервисов**, которые владеют своими данными и подключаются к ядру по единому контракту.

## Содержание

- [Возможности](#возможности)
- [Архитектура](#архитектура)
- [Структура репозитория](#структура-репозитория)
- [Развёртывание](#развёртывание)
- [Пульт оператора](#пульт-оператора)
- [Пользователи, профили и роли](#пользователи-профили-и-роли)
- [Сервисы](#сервисы)
- [Главный экран и виджеты](#главный-экран-и-виджеты)
- [Разработка](#разработка)
- [Тесты](#тесты)
- [Эксплуатация](#эксплуатация)
- [Документация](#документация)

## Возможности

- 🔐 **Вход через MAX** — проверка подписанного `initData`, внутренний UUID пользователя, пары токенов ядра и сервисов (RS256), обновление и отзыв.
- 🏛 **Вузы и участники** — заявки на подключение вуза, заявки на вступление (имя, профиль, группа), владельцы, профили, учебные группы.
- 🧩 **Сервисы по единому контракту** — облачные (администрирование, расписание, «Люди») и локальные (курсовые, свои сервисы вуза); меню, роли и виджеты сервис публикует сам.
- ☁️ **Облачные раннеры** — один контейнер на тип сервиса, внутри отдельный процесс на каждый вуз; ленивый запуск и остановка при простое.
- 🗓 **Расписание** — занятия, права по профилям, роль «Редактор расписания», импорт из JSON, Excel, выгрузок 1С и XML.
- 👥 **«Люди»** — анкеты участников, фото, должность и учёная степень.
- 🏠 **Главная из виджетов** — «Мой профиль», «Сегодня», курсовые; включение по профилям у сервиса и администратора вуза.
- 🛠 **Пульт оператора** — заявки, вузы, пользователи, журнал, обслуживание, тестовые данные.
- 🛡 **Безопасность** — изоляция вузов, private API только во внутренней сети, CSP, ограничение частоты запросов, аудит, логическое удаление.
- 🌗 **Интерфейс** — адаптивный (телефон и десктоп), тёмная тема, демо-режим без сервера.

## Архитектура

```text
                    ┌────────────── MAX ──────────────┐
                    │  бот (Node.js) → мини-приложение │
                    └────────────────┬────────────────┘
                                     │ HTTPS
┌────────────────────────────────── Nginx (web) ───────────────────────────────────┐
│ :443  оболочка (React) · /api → ядро · /schedule/<id>/ · /people/<id>/            │
│ :8444 администрирование (отдельный origin для iframe)   :8445 пульт оператора     │
└───────┬───────────────────────────┬───────────────────────────┬──────────────────┘
        │                           │                           │
 ┌──────┴───────┐        ┌──────────┴───────────┐     ┌─────────┴─────────┐
 │ Ядро FastAPI │◄──────►│ Раннеры сервисов      │     │ Пульт оператора   │
 │ SQLite, JWT  │ machine│ administration        │     │ (та же БД ядра)   │
 │ private API  │  API,  │ schedule, user-profile│     └───────────────────┘
 └──────────────┘ intro- │ процесс на каждый вуз │
                  spection└──────────────────────┘
        ▲
        │ machine API (HTTPS)
 ┌──────┴──────────────────────┐
 │ Локальные сервисы вуза       │  курсовые, свои сервисы — на сервере вуза
 └─────────────────────────────┘
```

- **Ядро** — источник пользователей, членств, профилей, ролей и сессий. Контракт: [CORE_API_SPEC.md](docs/core/CORE_API_SPEC.md), [openapi.yaml](openapi.yaml).
- **Сервис** получает ключ, публикует меню, роли и виджеты через machine API и на каждый запрос проверяет пользователя у ядра (introspection). Общие правила — [SDK/SPEC.md](docs/services/sdk/SPEC.md).
- **Раннер** ([services/runner](services/runner/runner.py)) получает у ядра облачные экземпляры своего типа и держит для каждого вуза процесс сервиса со своей БД. Размещение — [CLOUD_RUNTIME_SPEC.md](docs/services/CLOUD_RUNTIME_SPEC.md).
- **Оболочка** ([frontend](frontend/src)) рисует экраны расписания, «Людей» и курсовых сама, остальные сервисы открывает в iframe по протоколу SDK.

## Структура репозитория

```text
.
├── README.md
├── openapi.yaml                 # OpenAPI ядра
├── compose.yaml                 # Всё приложение: ядро, пульт, бот, Redis, раннеры, Nginx
├── .env.example                 # Настройки сервера
├── scripts/
│   ├── deploy.sh                # Автоматическое развёртывание и обновление
│   └── e2e/                     # Сквозная проверка на локальном стеке
├── backend/                     # Ядро (FastAPI)
│   ├── main.py                  # Приложение ядра
│   ├── auth/                    # MAX, JWT, сессии, проверки доступа
│   ├── routes/                  # API оболочки, private и machine API, платформа
│   ├── services/                # Логика ядра: вузы, заявки, группы, тестовые данные
│   ├── platform_core/           # Каталог типов, реестр, виджеты, лимиты, ошибки
│   ├── database/                # Модели и миграции SQLite
│   ├── operator_panel/          # Пульт оператора
│   ├── manage.py                # Команды для автоматизации и аварийного доступа
│   └── tests/
├── services/                    # Облачные сервисы (контекст сборки образов)
│   ├── runner/                  # Раннер: процесс на вуз, маршрутизация /<service_id>/
│   ├── administration/          # Администрирование вуза
│   ├── schedule/                # Расписание
│   ├── user-profile/            # «Люди»
│   └── SERVICES.md
├── frontend/                    # Оболочка: React + TypeScript + Vite
│   ├── src/
│   └── Dockerfile               # Сборка Vite → Nginx
├── bot/                         # Бот MAX, открывающий мини-приложение
├── web/configs/docker-nginx.conf
├── test-data/coursework/        # Локальный сервис курсовых — пример стороннего сервиса
└── docs/                        # Контракты и спецификации
```

## Развёртывание

### Требования

| Что | Версия |
| --- | --- |
| Linux-сервер с публичным IP или DNS-именем | — |
| Docker Engine + Docker Compose | Compose v2 |
| Сертификат для HTTPS | Let's Encrypt (certbot в `compose.yaml`) |
| Бот MAX и его токен | — |

Всё остальное (Python 3.13, Node.js, Nginx, Redis) собирается в контейнерах.

### Установка и обновление

```bash
git clone <репозиторий> && cd MAX-hackathon-app
cp .env.example .env            # JWT_ISSUER / SHELL_ORIGIN — адрес сервера
sudo ./scripts/deploy.sh        # первый запуск и каждое обновление
```

[`scripts/deploy.sh`](scripts/deploy.sh):
1. генерирует недостающие секреты в `.env` и спрашивает токен бота;
2. проверяет сертификат и каталоги;
3. сохраняет копию базы ядра (последние 5 копий — в томе `core-data`, `/data/backups`);
4. поднимает ядро и ставит расписание и «Людей» всем активным вузам;
5. поднимает раннеры и оболочку.

Повторный запуск безопасен. Пароль пульта оператора: `sudo grep OPERATOR_PASSWORD .env`.

### Настройки `.env`

| Переменная | Назначение |
| --- | --- |
| `BOT_TOKEN` | Токен бота MAX |
| `JWT_ISSUER`, `SHELL_ORIGIN` | Публичный HTTPS-адрес приложения |
| `ADMINISTRATION_PUBLIC_ORIGIN` | Адрес администрирования (порт 8444) |
| `CURSOR_SECRET_KEY` | Подпись курсоров постраничного вывода |
| `CLOUD_BINDING_KEY` | Выведение ключей облачных экземпляров; смена перевыпускает все ключи |
| `*_PROVISIONING_TOKEN` | Токены раннеров (по одному на тип) |
| `OPERATOR_PASSWORD` | Пароль пульта оператора, от 12 символов |
| `RATE_LIMIT_*` | Лимиты запросов в минуту (необязательно; 0 — без лимита) |

Секреты `deploy.sh` создаёт сам (`openssl rand -hex 32`); храните их между обновлениями.

### Порты

| Порт | Что |
| --- | --- |
| 80, 443 | Оболочка, API ядра, расписание и «Люди» (`/schedule/…`, `/people/…`) |
| 8444 | Администрирование (отдельный origin для iframe) |
| 8445 | Пульт оператора (на сервере также `127.0.0.1:8500`) |

**Межсетевой экран.** Порты контейнеров Docker идут мимо цепочки `INPUT`: закрывайте лишнее в `DOCKER-USER`, оставляя 80, 443, 8444 и 8445:

```bash
iptables -A DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
for p in 80 443 8444 8445; do iptables -A DOCKER-USER -i eth0 -p tcp -m conntrack --ctorigdstport $p -j RETURN; done
iptables -A DOCKER-USER -i eth0 -j DROP && iptables -A DOCKER-USER -j RETURN
netfilter-persistent save
```

## Пульт оператора

`https://<сервер>:8445`, вход по `OPERATOR_PASSWORD`. Без открытого порта: `ssh -L 8500:127.0.0.1:8500 <сервер>` и `http://localhost:8500`.

| Раздел | Что там |
| --- | --- |
| Сводка | Счётчики, состояние контейнеров и сервисов, вузы без владельца, новые заявки |
| Заявки вузов | Одобрение (с правкой названия) и отказ с причиной |
| Вузы | Подключение без заявки; карточка вуза: статус, владельцы, хосты своих сервисов, заявки, участники, группы, сервисы, роли, журнал; **«Удалить вуз…»** — со всеми данными, включая данные сервисов в раннерах |
| Пользователи | Поиск, добавление в вуз, права поддержки, удаление пользователей без вузов |
| Журнал | Все изменения на платформе |
| Обслуживание | Проверка платформы, выгрузка настроек сервисов, импорт групп, **тестовые данные**: «Создать» (вузы, группы, люди, расписание на две недели) и «Удалить» (только созданное этой кнопкой) |

## Пользователи, профили и роли

- **Членство** связывает пользователя с вузом; **профиль** (`student`, `teacher`, `admin`) — его статус в вузе.
- **Роль** даёт права в конкретном сервисе и профиле. Профиль `admin` сам по себе полномочий не даёт: их дают роли.
- **Администрирование**: `owner` (все права), `technical_admin` (сервисы и ключи), `membership_admin` (участники и группы). Роли администрирования назначает только владелец.
- **Расписание**: «Редактор расписания» (`schedule_editor`) — видеть всё и править; назначается и администратору, и преподавателю.
- **«Люди»**: «Редактор анкет» (`profile_editor`) — должность и учёная степень.
- **Курсовые**: «Менеджер курсовых» (`coursework_manager`).

Владельцем становится заявитель одобренной заявки на подключение вуза; оператор может назначить владельца в карточке вуза.

## Сервисы

| Сервис | Размещение | Что делает | Документы |
| --- | --- | --- | --- |
| Администрирование | облачный, защищённый | Вуз, участники, заявки, группы, сервисы, роли, виджеты, журнал | [SPEC](docs/services/administration/SPEC.md) · [реализация](docs/services/administration/IMPLEMENTATION.md) |
| Расписание | облачный | Занятия, права по профилям, импорт из файлов | [SPEC](docs/services/schedule/SPEC.md) · [реализация](docs/services/schedule/IMPLEMENTATION.md) |
| «Люди» | облачный | Анкеты участников, фото | [SPEC](docs/services/user-profile/SPEC.md) · [реализация](docs/services/user-profile/IMPLEMENTATION.md) |
| Курсовые | локальный (сервер вуза) | Загрузка и проверка курсовых работ (PDF) | [SPEC](docs/services/coursework/SPEC.md) · [README](test-data/coursework/README.md) |
| Свой сервис вуза | локальный | Любой сервис на едином контракте | [шаблон](docs/services/sdk/service-template/README.md) |

Новый вуз сразу получает администрирование, расписание и «Людей». Расписание и «Людей» администратор вуза может выключить или удалить, а удалённый вернуть одной кнопкой.

### Импорт расписания

Кнопка «Импорт» на экране расписания. Формат один — таблица занятий (Дата, Начало, Конец, Дисциплина, Группы, Преподаватели, Аудитория, Комментарий, Статус), типы файлов разные:

`JSON` · `Excel .xlsx` · `выгрузка 1С .csv/.txt` (`;`, табуляция, UTF-8 или Windows-1251) · `XML`

Сначала проверка с ошибками по строкам, затем загрузка по принципу «всё или ничего»; повторная загрузка не создаёт дублей. Подробно — [SPEC §2.1](docs/services/schedule/SPEC.md), примеры — [import-examples](docs/services/schedule/import-examples).

### Подключение своего сервиса

1. Оператор одобряет DNS-имя сервера вуза (пульт → вуз → «Хосты своих сервисов»).
2. Администратор вуза: «Сервисы» → «Подключить сервис» → «Свой сервис» (название, код, профили, адреса).
3. «Выдать ключ» показывает строки `.env` для сервера вуза.
4. Сервис при запуске сам публикует меню, роли и виджеты; администратор нажимает «Включить».

Каркас — [service-template](docs/services/sdk/service-template/README.md), пример — [курсовые](test-data/coursework/README.md), порядок — [CORE_API_SPEC §7](docs/core/CORE_API_SPEC.md).

## Главный экран и виджеты

Главная состоит только из виджетов сервисов: оболочка рисует их сама по шаблонам `profile`, `events`, `list`, `stat`, `progress`, `notice`; данные даёт сервис. На десктопе — четыре колонки, на телефоне — одна.

| Виджет | Сервис | Кому |
| --- | --- | --- |
| Мой профиль | «Люди» | всем |
| Сегодня | Расписание | студентам и преподавателям |
| Сегодня в вузе, Занятия сегодня | Расписание | администраторам с ролью редактора |
| Моя курсовая · На проверке · Курсовые в вузе | Курсовые | студентам · преподавателям · менеджеру |

Включение по профилям — у сервиса и у администратора вуза («Администрирование» → «Сервисы» → «Виджеты на главной»). Контракт — [WIDGETS_SPEC.md](docs/services/sdk/WIDGETS_SPEC.md).

## Разработка

```bash
cd frontend
npm ci
npm run dev          # демо-режим с тестовыми данными, без сервера
npm run build        # production-сборка: вход только через MAX
```

Демо-режим включается автоматически в `npm run dev`; сценарии состояний задаются параметрами адреса (`?scenario=single|none|errors`, `?now=...`, см. [api/mock](frontend/src/api/mock/index.ts)).

Python-зависимости: [`backend/requirements.txt`](backend/requirements.txt) и `requirements.txt` каждого сервиса.

## Тесты

Каждый набор запускается отдельным процессом (у наборов свои временные БД и настройки):

```bash
# Ядро (из backend/)
for f in tests/test_*.py; do python -m pytest -q "$f"; done

# Сервисы (из каталога сервиса)
cd services/schedule       && python -m pytest -q
cd services/user-profile   && python -m pytest -q
cd services/administration && python -m pytest -q
cd services/runner         && python -m pytest -q
cd test-data/coursework    && python -m pytest -q
cd docs/services/sdk/service-template && python -m pytest -q

# Оболочка (из frontend/)
npm test && npx tsc -b

# Сквозная проверка: ядро, пульт и три раннера локально, без Docker
./scripts/e2e/run.sh                 # 47 проверок: вуз, роли, расписание, «Люди», виджеты, удаление
./scripts/e2e/run.sh test_data.py    # тестовые данные: создание и удаление
```

Интеграционные тесты сервисов (`tests/test_integration_core.py`) поднимают настоящее ядро в том же процессе; для них нужен `PYTHONPATH=../../backend` или установленные зависимости ядра.

## Эксплуатация

```bash
sudo docker compose ps
curl --fail https://<сервер>/api/v1/health
sudo docker compose logs --tail=80 backend schedule user-profile administration
```

- **Данные**: SQLite ядра — том `core-data`; данные облачных сервисов — `administration-data`, `schedule-data`, `profile-data` (папка на экземпляр вуза). Не используйте `docker compose down -v`: это удаляет тома.
- **Резервные копии** базы ядра `deploy.sh` делает перед каждым обновлением (`core-data:/data/backups`).
- **Сертификат**:
  ```bash
  sudo docker compose run --rm certbot renew
  sudo docker compose exec web nginx -t && sudo docker compose exec web nginx -s reload
  ```
- **Команды без браузера**: [`backend/manage.py`](backend/manage.py) (`list-institutions`, `install-cloud`, `assign-owner` и другие).

## Документация

| Документ | О чём |
| --- | --- |
| [SPEC_MANIFEST.md](docs/SPEC_MANIFEST.md) | Идея и сущности платформы |
| [docs/core/INDEX.md](docs/core/INDEX.md) | Навигация по документам ядра |
| [CORE_API_SPEC.md](docs/core/CORE_API_SPEC.md) · [openapi.yaml](openapi.yaml) · [эндпоинты](docs/core/CORE_API_ENDPOINTS.txt) | Контракт ядра |
| [CORE_CLIENT.md](docs/core/CORE_CLIENT.md) | Оболочка (ядро-клиент) |
| [MAX_DEPLOYMENT.md](docs/core/MAX_DEPLOYMENT.md) | Размещение в MAX |
| [CORE_API_TODO_REPORT.md](docs/core/CORE_API_TODO_REPORT.md) | Решения и открытые вопросы |
| [docs/services/INDEX.md](docs/services/INDEX.md) | Контракты сервисов |
| [SDK/SPEC.md](docs/services/sdk/SPEC.md) | Общие правила сервисов и протокол iframe |
| [WIDGETS_SPEC.md](docs/services/sdk/WIDGETS_SPEC.md) | Виджеты главного экрана |
| [CLOUD_RUNTIME_SPEC.md](docs/services/CLOUD_RUNTIME_SPEC.md) | Облачное размещение и раннеры |
| [services/SERVICES.md](services/SERVICES.md) | Облачные сервисы в этом репозитории |

Отличия реализации от контрактов описаны в `IMPLEMENTATION.md` каждого сервиса.
