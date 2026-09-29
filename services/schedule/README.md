# Расписание — облачный сервис

Работает в раннере (`services/runner`): контейнер `schedule` в общем `compose.yaml`, внутри у каждого вуза свой процесс этого сервиса. Подключение вузу — одна кнопка в админке или пульте («Сервисы платформы»). Ключ и адреса создаёт платформа, данные вуза — в `/data/<service_id>` тома раннера.

Отдельно, без раннера (разработка): `uvicorn app.main:app` с `CORE_URL`, `SHELL_ORIGIN`, `SERVICE_CLIENT_ID`, `SERVICE_CLIENT_SECRET`, `SERVICE_API_BASE_URL`, `SERVICE_CLIENT_BASE_URL`.

Тесты: `python -m unittest discover -s tests -t .` (сквозные — с `PYTHONPATH=../../backend`).
