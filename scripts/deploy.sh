#!/usr/bin/env bash
# Авторазвёртывание всего приложения на сервере.
#
#   sudo ./scripts/deploy.sh                 # ядро + сервисы всех активных вузов
#   sudo ./scripts/deploy.sh --core-only     # только ядро (оболочка, API, пульт оператора, бот)
#   sudo ./scripts/deploy.sh UUID [UUID...]  # ядро + сервисы указанных вузов
#
# Что делает:
#   1) проверяет Docker и готовит .env: копирует пример, генерирует секреты, спрашивает токен бота;
#   2) готовит каталоги (services/connected, ACME, Let's Encrypt) и проверяет сертификат ядра;
#   3) собирает и запускает ядро, удаляя контейнеры прежней общей схемы, и ждёт его готовности;
#   4) каждому вузу подключает администрирование, расписание и «Людей» (scripts/connect-services.sh):
#      свои контейнеры, ключи, сертификаты; данные прежней версии переносятся автоматически.
# Соответствие «вуз → имя экземпляра → сдвиг портов» хранится в services/instances.tsv:
# при повторном запуске вуз сохраняет те же адреса. Повторный запуск безопасен.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "Ошибка: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

CORE_ONLY=0
TARGETS=()
for arg in "$@"; do
  case "$arg" in
    --core-only) CORE_ONLY=1 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) [[ "$arg" =~ ^[0-9a-fA-F-]{36}$ ]] || die "некорректный UUID вуза: $arg"; TARGETS+=("$arg") ;;
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
step "Настройки ядра (.env)"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Создан .env из .env.example"
fi
chmod 600 .env
[[ -n "$(env_get CURSOR_SECRET_KEY)" ]] || { env_set CURSOR_SECRET_KEY "$(openssl rand -hex 32)"; echo "Сгенерирован CURSOR_SECRET_KEY"; }
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
if [[ -z "$(env_get ADMINISTRATION_PUBLIC_ORIGIN)" ]]; then
  host="${CORE_URL#https://}"; host="${host%%:*}"
  [[ "$host" =~ ^[0-9.]+$ ]] && host="${host//./-}.sslip.io"
  env_set ADMINISTRATION_PUBLIC_ORIGIN "https://admin.$host:9444"
fi

# --- 2. Каталоги и сертификат ядра ----------------------------------------
step "Каталоги и сертификат ядра"
mkdir -p services/connected /var/lib/max-miniapp/acme /var/lib/max-miniapp/letsencrypt
chown 10001:10001 services/connected
chmod 750 services/connected
[[ -e /var/lib/max-miniapp/letsencrypt/live/max-miniapp/fullchain.pem ]] || die "нет сертификата ядра в
/var/lib/max-miniapp/letsencrypt/live/max-miniapp (раздел «Сертификаты» README): выпустите его и запустите скрипт снова"

# --- 3. Ядро ----------------------------------------------------------------
step "Сборка и запуск ядра"
docker compose config --quiet
# --remove-orphans убирает контейнеры прежней общей схемы (administration, schedule, user-profile).
docker compose up -d --build --remove-orphans
for attempt in $(seq 1 60); do
  if docker compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)" >/dev/null 2>&1; then
    echo "Ядро готово: $CORE_URL"
    break
  fi
  [[ $attempt -eq 60 ]] && die "ядро не поднялось за 5 минут: sudo docker compose logs --tail=80 backend"
  sleep 5
done

if [[ $CORE_ONLY -eq 1 ]]; then
  echo; echo "Ядро развёрнуто. Пульт оператора: $CORE_URL:8445"
  exit 0
fi

# --- 4. Сервисы вузов ------------------------------------------------------
MAP=services/instances.tsv
touch "$MAP"
if [[ ${#TARGETS[@]} -eq 0 ]]; then
  mapfile -t TARGETS < <(docker compose exec -T backend python manage.py list-institutions | awk '$2 == "active" {print $1}')
fi
[[ ${#TARGETS[@]} -gt 0 ]] || { echo; echo "Активных вузов нет: подключите вуз в пульте ($CORE_URL:8445) и запустите скрипт снова."; exit 0; }

for inst in "${TARGETS[@]}"; do
  line="$(grep -E "^$inst[[:space:]]" "$MAP" || true)"
  if [[ -z "$line" ]]; then
    n="$(grep -c . "$MAP" || true)"
    if [[ "$n" -eq 0 ]]; then name=main; else name="v$((n + 1))"; fi
    printf '%s\t%s\t%s\n' "$inst" "$name" "$((n * 10))" >> "$MAP"
    line="$(grep -E "^$inst[[:space:]]" "$MAP")"
  fi
  name="$(echo "$line" | cut -f2)"
  offset="$(echo "$line" | cut -f3)"
  step "Вуз $inst: экземпляр $name, сдвиг портов $offset"
  PORT_OFFSET="$offset" "$ROOT/scripts/connect-services.sh" "$inst" "$name"
done

echo
echo "Развёртывание завершено."
echo "  Приложение:     $CORE_URL (через бота MAX)"
echo "  Пульт оператора: $CORE_URL:8445 (пароль — OPERATOR_PASSWORD в .env)"
echo "  Вузы и экземпляры: $MAP"
