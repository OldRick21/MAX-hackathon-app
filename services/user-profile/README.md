# Люди — сервис вуза

Один контейнер — один вуз, как курсовые: свой ключ, своё DNS-имя, свой HTTPS. С ядром сервис общается только по HTTPS через `CORE_URL` и ключ.

## Автоматически

```bash
sudo ./scripts/connect-services.sh INSTITUTION_UUID [ИМЯ] people
```

Скрипт регистрирует сервис в ядре, выдаёт ключ, пишет `people-<ИМЯ>.env`, выпускает сертификат, запускает контейнер и включает сервис. Всё приложение целиком — `sudo ./scripts/deploy.sh`.

## Вручную

Регистрация: админка вуза или пульт → «Сервисы» → «Подключить свой сервис», код `people`, адреса на одобренном хосте → «Выдать ключ». Меню и роль «Редактор анкет» (`people.manage`) сервис публикует сам.

```bash
cd services/user-profile
cp people.env.example people-main.env && chmod 600 people-main.env
nano people-main.env          # строки «Выдать ключ», SERVICE_HOST, SERVICE_PORT
sudo docker compose --env-file people-main.env run --rm certbot certonly --webroot -w /var/www/acme \
  -d people.195-133-197-144.sslip.io --cert-name people-main
sudo docker compose --env-file people-main.env up -d --build
curl https://people.195-133-197-144.sslip.io:9446/api/v1/health
```

Для второго вуза на том же сервере — отдельный файл (`people-<вуз>.env`) со своими `INSTANCE`, `SERVICE_HOST`, `SERVICE_PORT`, `CERT_NAME`.

Продление сертификата:

```bash
sudo docker compose --env-file people-main.env run --rm certbot renew
sudo docker compose --env-file people-main.env exec proxy nginx -s reload
```
