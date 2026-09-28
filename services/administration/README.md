# Администрирование — сервис вуза

Один контейнер — один вуз, как курсовые: свой ключ, своё DNS-имя, свой HTTPS. С ядром сервис общается только по HTTPS через `CORE_URL` и ключ.

## Автоматически

```bash
sudo ./scripts/connect-services.sh INSTITUTION_UUID [ИМЯ] administration
```

Скрипт регистрирует сервис в ядре, выдаёт ключ, пишет `administration-<ИМЯ>.env`, выпускает сертификат, запускает контейнер и включает сервис. Всё приложение целиком — `sudo ./scripts/deploy.sh`.

## Вручную

Ключ и адреса задаёт **оператор**: пульт → «Вузы» → вуз → «Сервисы» → «Администрирование» → адреса и «Выдать ключ». Роли и меню администрирования задаёт ядро: это права на его закрытое API.

```bash
cd services/administration
cp administration.env.example administration-main.env && chmod 600 administration-main.env
nano administration-main.env          # строки «Выдать ключ», SERVICE_HOST, SERVICE_PORT
sudo docker compose --env-file administration-main.env run --rm certbot certonly --webroot -w /var/www/acme \
  -d admin.195-133-197-144.sslip.io --cert-name admin-main
sudo docker compose --env-file administration-main.env up -d --build
curl https://admin.195-133-197-144.sslip.io:9444/api/v1/health
```

Для второго вуза на том же сервере — отдельный файл (`administration-<вуз>.env`) со своими `INSTANCE`, `SERVICE_HOST`, `SERVICE_PORT`, `CERT_NAME`.

Продление сертификата:

```bash
sudo docker compose --env-file administration-main.env run --rm certbot renew
sudo docker compose --env-file administration-main.env exec proxy nginx -s reload
```
