# MAX Mini App

Простое мини-приложение для MAX на HTML, CSS и JavaScript.

Использует официальную библиотеку MAX Bridge и показывает имя пользователя при открытии через бота. В обычном браузере выводит подсказку открыть приложение в MAX.

## Структура проекта

```text
configs/
└── max-miniapp.conf   # Конфигурация Nginx
temlates/
└── index.html        # Страница приложения
```

## Размещение на сервере

Из корня проекта на сервере с установленным Nginx:

```bash
sudo mkdir -p /var/www/max-miniapp
sudo install -m 644 temlates/index.html /var/www/max-miniapp/index.html
sudo install -m 644 configs/max-miniapp.conf /etc/nginx/sites-available/max-miniapp
sudo ln -sfn /etc/nginx/sites-available/max-miniapp /etc/nginx/sites-enabled/max-miniapp
sudo nginx -t && sudo systemctl reload nginx
```

Адрес сайта задаётся в `configs/max-miniapp.conf` через `server_name`.

## Документация

- [Мини-приложения MAX](https://dev.max.ru/docs/webapps/introduction)
- [MAX Bridge](https://dev.max.ru/docs/webapps/bridge)