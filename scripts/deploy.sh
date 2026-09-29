#!/usr/bin/env bash
# Авторазвёртывание всего приложения на сервере.
#
#   sudo ./scripts/deploy.sh                 # всё: ядро, облачные сервисы, сервисы всем активным вузам
#   sudo ./scripts/deploy.sh --core-only     # только ядро и раннеры (без установки сервисов вузам)
#
# Облачные сервисы — администрирование, расписание, «Люди» — работают в раннерах (по контейнеру на тип),
# внутри раннера у каждого вуза свой процесс. Ключи и адреса выдаёт ядро автоматически: отдельные DNS,
# сертификаты и .env на вуз не нужны. Курсовые и свои сервисы вуза — локальные (свой сервер вуза).
#
# Что делает:
#   1) готовит .env: генерирует недостающие секреты, спрашивает токен бота;
#   2) каталоги и проверка сертификата ядра;
#   3) сохраняет копию базы ядра (последние 5 — том core-data, /data/backups), поднимает ядро
#      (оно переводит экземпляры в облако), ставит расписание и «Людей» всем активным вузам;
#   4) один раз переносит данные прежних контейнеров вузов (schedule-<имя>, people-<имя>) и старых общих томов
#      в папки экземпляров раннеров и убирает контейнеры прежней схемы;
#   5) поднимает раннеры и оболочку. Повторный запуск безопасен.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "Ошибка: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

CORE_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --core-only) CORE_ONLY=1 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) die "неизвестный параметр: $arg" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "запустите через sudo"
command -v docker >/dev/null || die "не найден docker"
docker compose version >/dev/null 2>&1 || die "не найден docker compose (плагин Compose v2)"

# --- 1. .env ядра ---------------------------------------------------------
env_get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '"'; }
env_set() {
  if grep -qE "^$1=" .env; then sed -i "s#^$1=.*#$1=$2#" .env; else echo "$1=$2" >> .env; fi
}
secret() {  # создать секрет, если его нет или он короче 32 символов
  local value; value="$(env_get "$1")"
  if [[ ${#value} -lt 32 ]]; then env_set "$1" "$(openssl rand -hex 32)"; echo "Сгенерирован $1"; fi
}
step "Настройки ядра (.env)"
[[ -f .env ]] || { cp .env.example .env; echo "Создан .env из .env.example"; }
chmod 600 .env
for name in CURSOR_SECRET_KEY CLOUD_BINDING_KEY ADMINISTRATION_PROVISIONING_TOKEN SCHEDULE_PROVISIONING_TOKEN \
            USER_PROFILE_PROVISIONING_TOKEN; do secret "$name"; done
if [[ -z "$(env_get OPERATOR_PASSWORD)" ]]; then
  env_set OPERATOR_PASSWORD "$(openssl rand -base64 18 | tr -d '/+=')"
  echo "Сгенерирован OPERATOR_PASSWORD (пароль пульта оператора): sudo grep OPERATOR_PASSWORD .env"
fi
BOT="$(env_get BOT_TOKEN)"
if [[ -z "$BOT" || "$BOT" == YOUR_BOT_TOKEN ]]; then
  [[ -t 0 ]] || die "задайте BOT_TOKEN в .env"
  read -r -p "Токен бота MAX: " BOT
  [[ -n "$BOT" ]] || die "токен бота обязателен"
  env_set BOT_TOKEN "$BOT"
fi
CORE_URL="$(env_get JWT_ISSUER | sed 's#/*$##')"
[[ "$CORE_URL" == https://* ]] || die "JWT_ISSUER в .env должен быть https://<адрес сервера>"
[[ -n "$(env_get SHELL_ORIGIN)" ]] || env_set SHELL_ORIGIN "$CORE_URL"
# Администрирование облачное: порт 8444 того же сервера (адрес с sslip.io — от прежней схемы).
ADMIN_ORIGIN="$(env_get ADMINISTRATION_PUBLIC_ORIGIN)"
if [[ -z "$ADMIN_ORIGIN" || "$ADMIN_ORIGIN" == *sslip.io* ]]; then env_set ADMINISTRATION_PUBLIC_ORIGIN "$CORE_URL:8444"; fi

# --- 2. Каталоги и сертификат ядра ----------------------------------------
step "Каталоги и сертификат ядра"
mkdir -p services/connected /var/lib/max-miniapp/acme /var/lib/max-miniapp/letsencrypt
chown 10001:10001 services/connected
chmod 750 services/connected
[[ -e /var/lib/max-miniapp/letsencrypt/live/max-miniapp/fullchain.pem ]] || die "нет сертификата ядра в
/var/lib/max-miniapp/letsencrypt/live/max-miniapp (раздел «Сертификаты» README): выпустите его и запустите скрипт снова"

# --- 3. Ядро и облачные сервисы вузам --------------------------------------
step "Сборка и запуск ядра"
docker compose config --quiet
PROJECT="$(docker compose config 2>/dev/null | sed -n 's/^name: *//p' | head -1)"
# Резервная копия базы ядра перед обновлением (новая версия может перестроить таблицы при запуске).
if docker volume inspect "${PROJECT}_core-data" >/dev/null 2>&1; then
  docker run --rm -v "${PROJECT}_core-data:/data" alpine:3 sh -c '
    [ -e /data/core-jwt.db ] || exit 0
    dir="/data/backups/$(date +%Y%m%d-%H%M%S)"   # вместе с журналом WAL (-wal, -shm)
    mkdir -p "$dir" && cp -a /data/core-jwt.db* "$dir/"
    ls -1dt /data/backups/*/ | tail -n +6 | xargs -r rm -rf
    echo "  копия базы ядра: том core-data, $dir"'
fi
docker compose up -d --build backend operator redis bot
for attempt in $(seq 1 60); do
  if docker compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)" >/dev/null 2>&1; then
    echo "Ядро готово: $CORE_URL"; break
  fi
  [[ $attempt -eq 60 ]] && die "ядро не поднялось за 5 минут: sudo docker compose logs --tail=80 backend"
  sleep 5
done
manage() { docker compose exec -T backend python manage.py "$@"; }

declare -A SID  # "<вуз>:<тип>" → UUID экземпляра
if [[ $CORE_ONLY -eq 0 ]]; then
  step "Облачные сервисы вузам"
  mapfile -t INSTS < <(manage list-institutions | awk '$2 == "active" {print $1}')
  for inst in "${INSTS[@]}"; do
    for type in schedule user-profile; do
      SID["$inst:$type"]="$(manage install-cloud "$inst" "$type" | tail -1)"
      echo "  $inst: $type → ${SID["$inst:$type"]}"
    done
  done
fi

# --- 4. Данные прежней схемы -------------------------------------------------
step "Перенос данных прежних контейнеров вузов"
copy_data() {  # $1 том-источник, $2 файл в нём, $3 том раннера, $4 service_id, $5 uid
  docker run --rm -v "$1:/from:ro" -v "$3:/to" alpine:3 sh -c "
    [ -e /to/$4/$2 ] && exit 0
    [ -e /from/$2 ] || exit 0
    mkdir -p /to/$4 && cp -a /from/$2* /to/$4/ && chown -R $5:$5 /to/$4 && echo '  перенесено: $1/$2 → $4'"
}
MAP=services/instances.tsv  # вуз → имя контейнеров прежней схемы (создавал прежний deploy.sh)
for key in "${!SID[@]}"; do
  inst="${key%%:*}"; type="${key#*:}"; sid="${SID[$key]}"
  name="$( [[ -f $MAP ]] && awk -v i="$inst" '$1 == i {print $2}' "$MAP" || true)"
  if [[ "$type" == schedule ]]; then file=schedule.db; uid=10004; target="${PROJECT}_schedule-data"; old="schedule-${name:-main}_data"
  else file=profiles.db; uid=10003; target="${PROJECT}_profile-data"; old="people-${name:-main}_data"; fi
  docker volume create "$target" >/dev/null
  if [[ -n "$name" ]] && docker volume inspect "$old" >/dev/null 2>&1; then copy_data "$old" "$file" "$target" "$sid" "$uid"; fi
  # Ещё более старая общая схема: один файл на все вузы в корне тома раннера.
  copy_data "$target" "$file" "$target" "$sid" "$uid"
done
for project in $(docker ps -a --format '{{.Label "com.docker.compose.project"}}' | sort -u | grep -E '^(administration|schedule|people)-' || true); do
  echo "  останавливаю прежний контейнер вуза: $project"
  docker ps -aq --filter "label=com.docker.compose.project=$project" | xargs -r docker rm -f >/dev/null
done

# --- 5. Раннеры и оболочка ----------------------------------------------------
step "Раннеры облачных сервисов и оболочка"
# --remove-orphans убирает контейнеры, которых больше нет в compose.yaml.
docker compose up -d --build --remove-orphans

echo
echo "Развёртывание завершено."
echo "  Приложение:       $CORE_URL (через бота MAX)"
echo "  Администрирование: $(env_get ADMINISTRATION_PUBLIC_ORIGIN) (открывается из приложения)"
echo "  Пульт оператора:  $CORE_URL:8445 (пароль — OPERATOR_PASSWORD в .env)"
echo "  Порты 9444–9446 прежней схемы больше не нужны."
