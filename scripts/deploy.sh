#!/usr/bin/env bash
# Развёртывание и обновление платформы «Вузы России» на сервере.
#
#   sudo ./scripts/deploy.sh                 # всё: ядро, облачные сервисы, сервисы всем активным вузам
#   sudo ./scripts/deploy.sh --core-only     # только ядро и раннеры (без установки сервисов вузам)
#   sudo ./scripts/deploy.sh --verbose       # показывать вывод сборки и Docker (иначе он идёт в лог)
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
#
# Подробный вывод каждого запуска — в /var/log/max-miniapp-deploy.log.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CORE_ONLY=0
VERBOSE=0
for arg in "$@"; do
  case "$arg" in
    --core-only) CORE_ONLY=1 ;;
    -v|--verbose) VERBOSE=1 ;;
    -h|--help) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Неизвестный параметр: $arg (справка: --help)" >&2; exit 2 ;;
  esac
done

# --- Вывод -----------------------------------------------------------------
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  B=$'\e[1m' D=$'\e[2m' R=$'\e[31m' G=$'\e[32m' Y=$'\e[33m' C=$'\e[36m' N=$'\e[0m'
else
  B='' D='' R='' G='' Y='' C='' N=''
fi
LOG=/var/log/max-miniapp-deploy.log
STARTED=$SECONDS
STEP=''
STEP_AT=0

banner() {
  cat <<EOF
${C}${B}
   ███╗   ███╗ █████╗ ██╗  ██╗
   ████╗ ████║██╔══██╗╚██╗██╔╝
   ██╔████╔██║███████║ ╚███╔╝     Вузы России
   ██║╚██╔╝██║██╔══██║ ██╔██╗     платформа сервисов вузов в MAX
   ██║ ╚═╝ ██║██║  ██║██╔╝ ██╗
   ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝${N}
   ${D}версия ${VERSION} · $(date '+%d.%m.%Y %H:%M')${N}

EOF
}
# На терминале строка шага «▸» заменяется итогом «✓»; всё, что печатается внутри шага, — с новой строки.
LIVE=0; [[ -t 1 && $VERBOSE -eq 0 ]] && LIVE=1
PENDING=0
newline() { if [[ $PENDING -eq 1 ]]; then echo; PENDING=0; fi; }
step() {
  STEP="$*"; STEP_AT=$SECONDS
  if [[ $LIVE -eq 1 ]]; then printf ' %s▸%s %s…' "$C" "$N" "$STEP"; PENDING=1; else printf ' ▸ %s\n' "$STEP"; fi
}
done_step() {
  if [[ $PENDING -eq 1 ]]; then printf '\r\e[2K'; PENDING=0; fi
  printf ' %s✓%s %s %s(%ss)%s\n' "$G" "$N" "${1:-$STEP}" "$D" $((SECONDS - STEP_AT)) "$N"; STEP=''
}
info() { newline; printf '   %s%s%s\n' "$D" "$*" "$N"; }
warn() { newline; printf ' %s!%s %s\n' "$Y" "$N" "$*"; }
die() {
  newline
  printf '\n %s✗ %s%s\n' "$R$B" "$*" "$N" >&2
  exit 1
}
on_error() {
  local code=$?
  [[ $BASHPID == "$$" ]] || exit "$code"  # сбой в подоболочке сообщит основной процесс
  trap - ERR
  newline
  printf '\n %s✗ Сбой на шаге «%s» (код %s)%s\n' "$R$B" "${STEP:-подготовка}" "$code" "$N" >&2
  if [[ $VERBOSE -eq 0 && -s $LOG ]]; then
    printf '   %sПоследние строки лога:%s\n' "$D" "$N" >&2
    tail -n +"$LOG_FROM" "$LOG" | tail -n 25 | sed 's/^/   │ /' >&2
  fi
  printf '\n   Полный лог: %s\n   Повторный запуск безопасен: sudo %s\n' "$LOG" "$0" >&2
  exit "$code"
}
trap on_error ERR
# Шумные команды (сборка, Docker): в лог, а с --verbose — и на экран.
run() {
  echo "\$ $*" >>"$LOG"
  if [[ $VERBOSE -eq 1 ]]; then "$@" 2>&1 | tee -a "$LOG"; else "$@" >>"$LOG" 2>&1; fi
}

[[ $EUID -eq 0 ]] || { echo "Запустите через sudo: sudo $0" >&2; exit 1; }
VERSION="$(git -c safe.directory="$ROOT" -C "$ROOT" describe --tags --always --dirty 2>/dev/null || echo 'без git')"
touch "$LOG" && chmod 640 "$LOG"
LOG_FROM=$(( $(wc -l <"$LOG") + 1 ))  # хвост лога при сбое — только этого запуска
{ echo; echo "=== $(date '+%F %T') deploy.sh $* · $VERSION"; } >>"$LOG"
banner

# --- 0. Проверка окружения --------------------------------------------------
step "Проверка окружения"
command -v docker >/dev/null || die "Не найден docker: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "Не найден docker compose (плагин Compose v2)"
command -v openssl >/dev/null || die "Не найден openssl: apt install openssl"
docker info >/dev/null 2>&1 || die "Docker не запущен: systemctl start docker"
done_step "Окружение: $(docker compose version --short 2>/dev/null | sed 's/^/Docker Compose /')"

# --- 1. .env ядра ---------------------------------------------------------
env_get() { { grep -E "^$1=" .env || true; } | tail -1 | cut -d= -f2- | tr -d '"'; }
env_set() {
  if grep -qE "^$1=" .env; then sed -i "s#^$1=.*#$1=$2#" .env; else echo "$1=$2" >> .env; fi
}
GENERATED=()
secret() {  # создать секрет, если его нет или он короче 32 символов
  local value; value="$(env_get "$1")"
  if [[ ${#value} -lt 32 ]]; then env_set "$1" "$(openssl rand -hex 32)"; GENERATED+=("$1"); fi
}
step "Настройки (.env)"
[[ -f .env ]] || { cp .env.example .env; info "создан .env из .env.example"; }
chmod 600 .env
for name in CURSOR_SECRET_KEY BOT_CORE_TOKEN CLOUD_BINDING_KEY ADMINISTRATION_PROVISIONING_TOKEN SCHEDULE_PROVISIONING_TOKEN \
            USER_PROFILE_PROVISIONING_TOKEN; do secret "$name"; done
NEW_OPERATOR_PASSWORD=0
if [[ -z "$(env_get OPERATOR_PASSWORD)" ]]; then
  env_set OPERATOR_PASSWORD "$(openssl rand -base64 18 | tr -d '/+=')"
  GENERATED+=(OPERATOR_PASSWORD); NEW_OPERATOR_PASSWORD=1
fi
BOT="$(env_get BOT_TOKEN)"
if [[ -z "$BOT" || "$BOT" == YOUR_BOT_TOKEN ]]; then
  [[ -t 0 ]] || die "Задайте BOT_TOKEN в .env"
  newline
  read -r -p "   Токен бота MAX: " BOT
  [[ -n "$BOT" ]] || die "Токен бота обязателен"
  env_set BOT_TOKEN "$BOT"
fi
CORE_URL="$(env_get JWT_ISSUER | sed 's#/*$##')"
[[ "$CORE_URL" == https://* ]] || die "JWT_ISSUER в .env должен быть https://<адрес сервера>"
[[ -n "$(env_get SHELL_ORIGIN)" ]] || env_set SHELL_ORIGIN "$CORE_URL"
# Администрирование облачное: порт 8444 того же сервера (адрес с sslip.io — от прежней схемы).
ADMIN_ORIGIN="$(env_get ADMINISTRATION_PUBLIC_ORIGIN)"
if [[ -z "$ADMIN_ORIGIN" || "$ADMIN_ORIGIN" == *sslip.io* ]]; then env_set ADMINISTRATION_PUBLIC_ORIGIN "$CORE_URL:8444"; fi
[[ ${#GENERATED[@]} -eq 0 ]] || info "сгенерированы: ${GENERATED[*]}"
done_step "Настройки: $CORE_URL"

# --- 2. Каталоги и сертификат ядра ----------------------------------------
step "Каталоги и сертификат"
mkdir -p services/connected /var/lib/max-miniapp/acme /var/lib/max-miniapp/letsencrypt
chown -R 10001:10001 services/connected
chmod 750 services/connected
CERT=/var/lib/max-miniapp/letsencrypt/live/max-miniapp/fullchain.pem
[[ -e $CERT ]] || die "Нет сертификата ядра: $CERT
   Выпустите его (README, раздел «Сертификаты») и запустите скрипт снова."
CERT_END="$(openssl x509 -enddate -noout -in "$CERT" 2>/dev/null | cut -d= -f2 || true)"
CERT_DAYS=''
if [[ -n $CERT_END ]]; then
  CERT_DAYS=$(( ($(date -d "$CERT_END" +%s) - $(date +%s)) / 86400 ))
  (( CERT_DAYS >= 0 )) || die "Сертификат ядра истёк $CERT_END: продлите его (README, раздел «Эксплуатация»)"
fi
done_step "Сертификат: ${CERT_DAYS:-?} дн. до окончания"
(( ${CERT_DAYS:-99} >= 14 )) || warn "Сертификат скоро истечёт: продлите его (README, раздел «Эксплуатация»)"

# --- 3. Ядро и облачные сервисы вузам --------------------------------------
step "Резервная копия базы ядра"
docker compose config --quiet
PROJECT="$(docker compose config 2>/dev/null | sed -n 's/^name: *//p' | head -1)"
BACKUP=''
# Копия перед обновлением: новая версия может перестроить таблицы при запуске.
if docker volume inspect "${PROJECT}_core-data" >/dev/null 2>&1; then
  BACKUP="$(docker run --rm -v "${PROJECT}_core-data:/data" alpine:3 sh -c '
    [ -e /data/core-jwt.db ] || exit 0
    dir="/data/backups/$(date +%Y%m%d-%H%M%S)"   # вместе с журналом WAL (-wal, -shm)
    mkdir -p "$dir" && cp -a /data/core-jwt.db* "$dir/"
    ls -1dt /data/backups/*/ | tail -n +6 | xargs -r rm -rf
    echo "$dir"' 2>>"$LOG")"
fi
done_step "Резервная копия: ${BACKUP:-не нужна (первый запуск)}"

step "Модули пульта оператора"
# Папки test-data/*/ с plugin.py (сейчас — тестовые данные). Нет папки — том очищается
# и пульт работает без модулей; остальное приложение от них не зависит.
run docker volume create "${PROJECT}_operator-plugins"
PLUGIN_SRC=()
PLUGINS=()
for dir in test-data/*/; do
  [[ -f "$dir/plugin.py" ]] || continue
  PLUGIN_SRC+=(-v "$ROOT/$dir:/src/$(basename "$dir"):ro"); PLUGINS+=("$(basename "$dir")")
done
run docker run --rm -v "${PROJECT}_operator-plugins:/plugins" "${PLUGIN_SRC[@]}" alpine:3 sh -c '
  rm -rf /plugins/* && for d in /src/*/; do [ -d "$d" ] && cp -r "$d" /plugins/; done; true'
done_step "Модули пульта: ${PLUGINS[*]:-нет}"

step "Сборка и запуск ядра"
run docker compose up -d --build backend operator redis bot
# Пульт читает модули при запуске: перезапуск подхватывает добавленные и убирает удалённые.
run docker compose restart operator
for attempt in $(seq 1 60); do
  if docker compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)" >/dev/null 2>&1; then
    break
  fi
  [[ $attempt -eq 60 ]] && die "Ядро не поднялось за 5 минут: sudo docker compose logs --tail=80 backend"
  sleep 5
done
done_step "Ядро запущено"
manage() { docker compose exec -T backend python manage.py "$@" 2>>"$LOG"; }

declare -A SID  # "<вуз>:<тип>" → UUID экземпляра
INST_COUNT=0
if [[ $CORE_ONLY -eq 0 ]]; then
  step "Облачные сервисы вузам"
  mapfile -t INSTS < <(manage list-institutions | awk '$2 == "active" {print $1}')
  for inst in "${INSTS[@]}"; do
    for type in schedule user-profile; do
      SID["$inst:$type"]="$(manage install-cloud "$inst" "$type" | tail -1)"
      echo "  $inst: $type → ${SID["$inst:$type"]}" >>"$LOG"
    done
  done
  INST_COUNT=${#INSTS[@]}
  done_step "Расписание и «Люди»: активных вузов — $INST_COUNT"
fi

# --- 4. Данные прежней схемы -------------------------------------------------
step "Данные прежней схемы"
MIGRATED=0
copy_data() {  # $1 том-источник, $2 файл в нём, $3 том раннера, $4 service_id, $5 uid
  local out
  out="$(docker run --rm -v "$1:/from:ro" -v "$3:/to" alpine:3 sh -c "
    [ -e /to/$4/$2 ] && exit 0
    [ -e /from/$2 ] || exit 0
    mkdir -p /to/$4 && cp -a /from/$2* /to/$4/ && chown -R $5:$5 /to/$4 && echo '$1/$2 → $4'")"
  [[ -z $out ]] || { info "перенесено: $out"; echo "перенесено: $out" >>"$LOG"; MIGRATED=$((MIGRATED + 1)); }
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
  info "убран прежний контейнер вуза: $project"
  docker ps -aq --filter "label=com.docker.compose.project=$project" | xargs -r docker rm -f >>"$LOG" 2>&1
  MIGRATED=$((MIGRATED + 1))
done
if [[ $MIGRATED -eq 0 ]]; then done_step "Данные прежней схемы: переносить нечего"; else done_step; fi

# --- 5. Раннеры и оболочка ----------------------------------------------------
step "Раннеры сервисов и оболочка"
# --remove-orphans убирает контейнеры, которых больше нет в compose.yaml.
run docker compose up -d --build --remove-orphans
done_step "Раннеры и оболочка запущены"

# --- Итог ----------------------------------------------------------------------
UNHEALTHY=()
TOTAL=0
while read -r service state health; do
  [[ -n $service ]] || continue
  TOTAL=$((TOTAL + 1))
  [[ $state == running && ( -z $health || $health == healthy || $health == starting ) ]] || UNHEALTHY+=("$service ($state${health:+, $health})")
done < <(docker compose ps -a --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null)

ELAPSED=$((SECONDS - STARTED))
echo
if [[ ${#UNHEALTHY[@]} -eq 0 ]]; then
  printf ' %s✓ Готово за %d мин %02d с%s · контейнеров: %d, все работают\n' "$G$B" $((ELAPSED / 60)) $((ELAPSED % 60)) "$N" "$TOTAL"
else
  printf ' %s! Готово за %d мин %02d с, но не все контейнеры работают:%s\n' "$Y$B" $((ELAPSED / 60)) $((ELAPSED % 60)) "$N"
  for item in "${UNHEALTHY[@]}"; do printf '   · %s\n' "$item"; done
  printf '   Логи: sudo docker compose logs --tail=80 <сервис>\n'
fi

cat <<EOF

 ${B}Адреса${N}
   Приложение         $CORE_URL  ${D}(открывается через бота MAX)${N}
   Администрирование  $(env_get ADMINISTRATION_PUBLIC_ORIGIN)  ${D}(из приложения)${N}
   Пульт оператора    $CORE_URL:8445  ${D}(или ssh -L 8500:127.0.0.1:8500 → http://localhost:8500)${N}

 ${B}Полезное${N}
   Пароль пульта      sudo grep OPERATOR_PASSWORD $ROOT/.env
   Состояние          sudo docker compose ps
   Логи               sudo docker compose logs -f --tail=100 <сервис>
   Лог развёртывания  $LOG
EOF
[[ $NEW_OPERATOR_PASSWORD -eq 0 ]] || printf '\n %s!%s Создан пароль пульта оператора — посмотрите его командой выше.\n' "$Y" "$N"
[[ $CORE_ONLY -eq 0 ]] || printf '\n %s!%s Режим --core-only: сервисы вузам не устанавливались.\n' "$Y" "$N"
echo
