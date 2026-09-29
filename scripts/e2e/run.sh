#!/usr/bin/env bash
# Сквозная проверка без Docker: поднимает ядро (порт 8000), пульт (18500) и раннеры
# расписания, «Людей», администрирования (18100–18102) во временном каталоге, прогоняет
# e2e.py и всё останавливает. Нужны Python-зависимости backend и сервисов.
#   ./scripts/e2e/run.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$(mktemp -d)"
pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; wait 2>/dev/null || true; rm -rf "$S"; }
trap cleanup EXIT
mkdir -p "$S"/data/{schedule,user-profile,administration} "$S"/run "$S/connected"
key() { printf "$1%.0s" {1..48}; }
export DATABASE_URL="sqlite:///$S/core.db" JWT_ISSUER=https://core.test JWT_KEYRING_PATH="$S/keys.json" \
  CURSOR_SECRET_KEY="$(key c)" MAX_BOT_TOKEN= ALLOW_DEV_LOGIN=true SEED_DEMO_DATA=true ALLOW_FAKE_REDIS=true REDIS_PORT=1 \
  SERVICE_CONFIG_DIR="$S/connected" CLOUD_BINDING_KEY="$(key b)" ADMINISTRATION_PROVISIONING_TOKEN="$(key p)" \
  SCHEDULE_PROVISIONING_TOKEN="$(key s)" USER_PROFILE_PROVISIONING_TOKEN="$(key u)" SHELL_ORIGIN=https://shell.test \
  ADMINISTRATION_PUBLIC_ORIGIN=https://shell.test:8444 OPERATOR_PASSWORD='correct horse battery' \
  OPERATOR_COOKIE_SECURE=false OPERATOR_HEALTH_TARGETS='{}'
(cd "$ROOT/backend" && exec python -m uvicorn main:app --port 8000 >"$S/core.log" 2>&1) & pids+=($!)
(cd "$ROOT/backend" && exec python -m uvicorn operator_panel.app:app --port 18500 >"$S/op.log" 2>&1) & pids+=($!)
runner() {  # тип, переменная БД, файл БД, токен, порт
  mkdir -p "$S/run/$1"
  (cd "$ROOT/services/$1" && exec env CORE_INTERNAL_URL=http://127.0.0.1:8000 PROVISIONING_TOKEN="$4" \
    SHELL_ORIGIN=https://shell.test DATA_DIR="$S/data/$1" RUN_DIR="$S/run/$1" DB_ENV="$2" DB_FILE="$3" POLL_SECONDS=1 \
    PYTHONPATH=.:../runner python -m uvicorn runner:app --port "$5" >"$S/runner-$1.log" 2>&1) & pids+=($!)
}
runner schedule SCHEDULE_DB schedule.db "$(key s)" 18100
runner user-profile PROFILE_DB profiles.db "$(key u)" 18101
runner administration "" "" "$(key p)" 18102
cd "$S"
python3 "$ROOT/scripts/e2e/e2e.py" || { echo "Логи: $S (сохранены)"; trap - EXIT; kill "${pids[@]}"; exit 1; }
