# Расписание — сервис вуза

Один контейнер — один вуз, как курсовые: свой ключ, своё DNS-имя, свой HTTPS. С ядром сервис общается только по HTTPS через `CORE_URL` и ключ.

## Автоматически

```bash
sudo ./scripts/connect-services.sh INSTITUTION_UUID [ИМЯ] schedule
```

Скрипт регистрирует сервис в ядре, выдаёт ключ, пишет `schedule-<ИМЯ>.env`, выпускает сертификат, запускает контейнер и включает сервис. Всё приложение целиком — `sudo ./scripts/deploy.sh`.

## Вручную

Регистрация: админка вуза или пульт → «Сервисы» → «Подключить свой сервис», код `schedule`, адреса на одобренном хосте → «Выдать ключ». Меню и роль «Редактирование расписания» сервис публикует сам; роли прежних версий удаляет.

```bash
cd services/schedule
cp schedule.env.example schedule-main.env && chmod 600 schedule-main.env
nano schedule-main.env          # строки «Выдать ключ», SERVICE_HOST, SERVICE_PORT
sudo docker compose --env-file schedule-main.env run --rm certbot certonly --webroot -w /var/www/acme \
  -d schedule.195-133-197-144.sslip.io --cert-name schedule-main
sudo docker compose --env-file schedule-main.env up -d --build
curl https://schedule.195-133-197-144.sslip.io:9445/api/v1/health
```

Для второго вуза на том же сервере — отдельный файл (`schedule-<вуз>.env`) со своими `INSTANCE`, `SERVICE_HOST`, `SERVICE_PORT`, `CERT_NAME`.

Продление сертификата:

```bash
sudo docker compose --env-file schedule-main.env run --rm certbot renew
sudo docker compose --env-file schedule-main.env exec proxy nginx -s reload
```
