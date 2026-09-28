# Курсовые работы — тестовый сторонний сервис

Свой сервис вуза на шаблоне SDK (`docs/services/sdk/service-template`). Работает независимо от платформы: свой процесс, своя база и файлы, свой интерфейс внутри приложения. К платформе подключается через пульт оператора или админку вуза как «Свой сервис» с кодом `coursework`.

Что умеет: студент отправляет PDF и выбирает проверяющего, преподаватель принимает работу или возвращает на доработку, студент загружает новую версию; любой администратор вуза видит все работы, а менеджер курсовых (роль `coursework_manager`) ещё и удаляет их. Имена пользователи указывают прямо в сервисе — другие сервисы платформы он не вызывает.

## Запуск на сервере платформы (демо)

Сервис живёт только в этой папке: все настройки — в `coursework.env`, сертификат — в `./letsencrypt`, данные — в volume `coursework_coursework-data`. Платформенный `.env`, её compose и её сертификаты не используются. Единственное пересечение — каталог проверок Let's Encrypt (`ACME_WEBROOT`): порт 80 на этом сервере занят Nginx платформы.

Нужно DNS-имя: ядро не принимает IP-адреса для своих сервисов. Подойдёт бесплатное `coursework.195-133-197-144.sslip.io` — оно само указывает на IP сервера.

Все команды — из `test-data/coursework`, compose всегда с `--env-file coursework.env`.

1. **Настройки.**

   ```bash
   cp coursework.env.example coursework.env
   chmod 600 coursework.env
   nano coursework.env      # проверить COURSEWORK_HOST, COURSEWORK_PORT, адреса
   ```

2. **Сертификат** (сохраняется в `./letsencrypt`):

   ```bash
   sudo docker compose --env-file coursework.env run --rm certbot certonly --webroot -w /var/www/acme \
     -d coursework.195-133-197-144.sslip.io --cert-name coursework
   ```

3. **Одобрение хоста.** Пульт оператора (`https://195.133.197.144:8445`) → «Вузы» → вуз → «Обзор» → «Хосты своих сервисов» → `coursework.195-133-197-144.sslip.io` → «Сохранить».
4. **Регистрация.** Там же, вкладка «Сервисы» → «Подключить свой сервис вуза»: название «Курсовые работы», код `coursework`, все профили, адреса `https://coursework.195-133-197-144.sslip.io:9443/api/v1` и `https://coursework.195-133-197-144.sslip.io:9443`.
5. **Ключ.** В карточке сервиса «Выдать ключ» и вставить строки в `coursework.env` (`SHELL_ORIGIN` оставить из примера).
6. **Запуск.**

   ```bash
   sudo docker compose --env-file coursework.env up -d --build
   sudo docker compose --env-file coursework.env logs --tail=50 app    # onboarding complete
   ```

   Откройте порт 9443 в файрволе. Через минуту в карточке сервиса появится опубликованное меню.
7. **Включение.** В карточке — «Включить». Курсовые появятся в «Все сервисы».
8. Роль «Менеджер курсовых» — вкладка «Роли» → «Курсовые работы» → `coursework_manager` у нужного администратора.

Продление сертификата (раз в 1–2 месяца или по cron):

```bash
sudo docker compose --env-file coursework.env run --rm certbot renew
sudo docker compose --env-file coursework.env exec proxy nginx -s reload
```

На отдельном сервере вуза всё так же, только порт 80 свободен и сертификат выпускается без платформы:

```bash
sudo docker compose --env-file coursework.env run --rm -p 80:80 certbot certonly --standalone \
  -d <имя> --cert-name coursework
```

`CORE_URL` и `SHELL_ORIGIN` в этом случае указывают на платформу.

Остановка: `sudo docker compose --env-file coursework.env down` (данные остаются в volume `coursework_coursework-data`).

## Тесты

```bash
pip install -r requirements.txt httpx
python -m unittest discover -s tests -t . -p "test_coursework.py" -v
PYTHONPATH=../../backend python -m unittest tests.test_integration_core -v
```
