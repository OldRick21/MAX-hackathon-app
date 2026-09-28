#!/usr/bin/env bash
# Автоподключение сервисов вузу: администрирование, расписание, «Люди».
#
#   sudo ./scripts/connect-services.sh INSTITUTION_UUID [INSTANCE] [administration] [schedule] [people]
#
# INSTANCE — короткое имя вуза латиницей (по умолчанию main): из него строятся имена контейнеров,
# DNS-имена и сертификаты. Без списка сервисов подключаются все три.
#
# Для каждого сервиса скрипт:
#   1) регистрирует его в ядре на https://<имя>.<ip>.sslip.io:<порт> и выдаёт новый ключ
#      (администрированию — задаёт адреса), прежние ключи сервиса отзываются;
#   2) пишет файл настроек services/<сервис>/<код>-<INSTANCE>.env (chmod 600);
#   3) выпускает сертификат Let's Encrypt, если его ещё нет;
#   4) собирает и запускает контейнер сервиса;
#   5) ждёт, пока сервис опубликует меню, и включает его.
# Повторный запуск безопасен: ключ перевыпускается, контейнер пересоздаётся с новым ключом.
#
# Переменные окружения: PORT_OFFSET (сдвиг портов для второго и следующих вузов на том же сервере,
# например 10), CERTBOT_EMAIL (почта для Let's Encrypt, необязательно).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "Ошибка: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

[[ $# -ge 1 ]] || die "укажите UUID вуза: sudo $0 INSTITUTION_UUID [INSTANCE] [administration schedule people]"
INSTITUTION="$1"; shift
[[ "$INSTITUTION" =~ ^[0-9a-fA-F-]{36}$ ]] || die "некорректный UUID вуза: $INSTITUTION"
INSTANCE="main"
if [[ $# -ge 1 && ! "$1" =~ ^(administration|schedule|people)$ ]]; then INSTANCE="$1"; shift; fi
[[ "$INSTANCE" =~ ^[a-z][a-z0-9-]{0,20}$ ]] || die "INSTANCE — латиница в нижнем регистре, цифры и -, до 21 символа"
SERVICES=("$@")
[[ ${#SERVICES[@]} -gt 0 ]] || SERVICES=(administration schedule people)

[[ $EUID -eq 0 ]] || die "запустите через sudo: контейнеры и сертификаты требуют прав root"
command -v docker >/dev/null || die "не найден docker"
[[ -f .env ]] || die "нет .env ядра: сначала scripts/deploy.sh"

# Адрес ядра — JWT_ISSUER из .env (https://<ip>).
CORE_URL="$(grep -E '^JWT_ISSUER=' .env | cut -d= -f2- | tr -d '"' | sed 's#/*$##')"
[[ -n "$CORE_URL" ]] || die "в .env не задан JWT_ISSUER"
PUBLIC_HOST="${CORE_URL#https://}"
PUBLIC_HOST="${PUBLIC_HOST%%:*}"
# sslip.io: имя вида schedule.1-2-3-4.sslip.io указывает на IP 1.2.3.4 — отдельный DNS не нужен.
if [[ "$PUBLIC_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then DOMAIN="${PUBLIC_HOST//./-}.sslip.io"; else DOMAIN="$PUBLIC_HOST"; fi
PORT_OFFSET="${PORT_OFFSET:-0}"

core_manage() { docker compose exec -T backend python manage.py "$@"; }
# Имя проекта ядра в Docker (префикс его томов).
CORE_PROJECT="$(docker compose config 2>/dev/null | sed -n 's/^name: *//p' | head -1)"
docker compose ps --status running backend | grep -q backend || die "ядро не запущено: sudo docker compose up -d (или scripts/deploy.sh)"

for code in "${SERVICES[@]}"; do
  case "$code" in
    administration) folder=services/administration; prefix=admin;    port=$((9444 + PORT_OFFSET)) ;;
    schedule)       folder=services/schedule;       prefix=schedule; port=$((9445 + PORT_OFFSET)) ;;
    people)         folder=services/user-profile;   prefix=people;   port=$((9446 + PORT_OFFSET)) ;;
    *) die "неизвестный сервис: $code (administration, schedule, people)" ;;
  esac
  if [[ "$INSTANCE" == main ]]; then host="$prefix.$DOMAIN"; else host="$prefix-$INSTANCE.$DOMAIN"; fi
  cert="$prefix-$INSTANCE"
  env_file="$folder/$code-$INSTANCE.env"

  step "$code: регистрация в ядре и ключ (https://$host:$port)"
  lines="$(core_manage connect-service "$INSTITUTION" "$code" "$host" "$port")" || die "ядро не подключило $code"
  umask 077
  {
    echo "# Создано scripts/connect-services.sh $(date -u +%Y-%m-%dT%H:%M:%SZ) для вуза $INSTITUTION."
    echo "INSTANCE=$INSTANCE"
    echo "SERVICE_HOST=$host"
    echo "SERVICE_PORT=$port"
    echo "CERT_NAME=$cert"
    echo "ACME_WEBROOT=/var/lib/max-miniapp/acme"
    echo "$lines" | grep -E '^[A-Z_]+='
    [[ "$code" == administration ]] && echo "FRAME_ANCESTORS=https://web.max.ru"
  } > "$env_file"
  chmod 600 "$env_file"
  compose=(docker compose --project-directory "$folder" -f "$folder/compose.yaml" --env-file "$env_file")

  if [[ ! -e "$folder/letsencrypt/live/$cert/fullchain.pem" ]]; then
    step "$code: сертификат для $host"
    email=(--register-unsafely-without-email)
    [[ -n "${CERTBOT_EMAIL:-}" ]] && email=(--email "$CERTBOT_EMAIL")
    "${compose[@]}" run --rm certbot certonly --webroot -w /var/www/acme -d "$host" --cert-name "$cert" \
      --non-interactive --agree-tos "${email[@]}" || die "не удалось выпустить сертификат для $host"
  fi

  # Данные прежней общей версии (один контейнер на все вузы) переносятся в том этого вуза один раз:
  # записи в базе разделены по вузу и UUID сервиса, а UUID при переходе сохранился.
  legacy=""
  case "$code" in schedule) legacy="${CORE_PROJECT}_schedule-data" ;; people) legacy="${CORE_PROJECT}_profile-data" ;; esac
  target="$code-${INSTANCE}_data"
  if [[ -n "$legacy" ]] && docker volume inspect "$legacy" >/dev/null 2>&1 && ! docker volume inspect "$target" >/dev/null 2>&1; then
    step "$code: перенос данных из $legacy в $target"
    # Метки как у Compose: иначе при запуске он предупреждает, что том создан не им.
    docker volume create --label "com.docker.compose.project=$code-$INSTANCE" \
      --label com.docker.compose.volume=data "$target" >/dev/null
    docker run --rm -v "$legacy:/from:ro" -v "$target:/to" alpine:3 sh -c 'cp -a /from/. /to/'
  fi

  step "$code: запуск контейнера"
  "${compose[@]}" up -d --build

  step "$code: ждём публикации меню и включаем"
  for attempt in $(seq 1 60); do
    status=0
    core_manage enable-service "$INSTITUTION" "$code" >/dev/null 2>&1 || status=$?
    if [[ $status -eq 0 ]]; then echo "$code включён: https://$host:$port"; break; fi
    [[ $status -eq 3 ]] || { core_manage enable-service "$INSTITUTION" "$code" || true; die "$code не включился"; }
    [[ $attempt -eq 60 ]] && die "$code не опубликовал меню за 5 минут: ${compose[*]} logs --tail=80 app"
    sleep 5
  done
done

echo
echo "Готово. Откройте порты сервисов в файрволе, если он включён."
