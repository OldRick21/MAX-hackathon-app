#!/usr/bin/env bash
# Local deployment without MAX. Uses the checked-in emulator for primary authentication.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="$ROOT/.maxless"
ENV_FILE="$STATE/maxless.env"
COMPOSE_FILE="$ROOT/compose.maxless.yaml"
PROJECT=maxhack-maxless
NO_BUILD=0
USER_ID=''

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-build) NO_BUILD=1; shift ;;
    --user-id) USER_ID="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--no-build] [--user-id POSITIVE_INTEGER]"
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

cd "$ROOT"
command -v docker >/dev/null || { echo 'Docker is not installed.' >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo 'Docker Compose v2 is required.' >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo 'Docker is not running.' >&2; exit 1; }
command -v openssl >/dev/null || { echo 'OpenSSL is required to generate local secrets.' >&2; exit 1; }

mkdir -p "$STATE"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

env_get() { { grep -E "^$1=" "$ENV_FILE" || true; } | tail -1 | cut -d= -f2-; }
env_set() {
  local key="$1" value="$2" temporary
  temporary="$ENV_FILE.tmp"
  awk -v key="$key" -v value="$value" '
    BEGIN { done=0 }
    index($0, key "=") == 1 { if (!done) print key "=" value; done=1; next }
    { print }
    END { if (!done) print key "=" value }
  ' "$ENV_FILE" > "$temporary"
  mv "$temporary" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}
ensure_value() { [[ -n "$(env_get "$1")" ]] || env_set "$1" "$2"; }
ensure_secret() { [[ ${#2} -ge 32 ]] || env_set "$1" "$(openssl rand -hex 32)"; }

ensure_value MAXLESS_APP_PORT 18443
ensure_value MAXLESS_ADMIN_PORT 18444
ensure_value MAXLESS_OPERATOR_PORT 18500
for name in MAX_BOT_TOKEN BOT_CORE_TOKEN CURSOR_SECRET_KEY CLOUD_BINDING_KEY \
  ADMINISTRATION_PROVISIONING_TOKEN SCHEDULE_PROVISIONING_TOKEN USER_PROFILE_PROVISIONING_TOKEN OPERATOR_PASSWORD; do
  ensure_secret "$name" "$(env_get "$name")"
done

compose() { docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
compose config --quiet

echo 'MAX-less local deployment'
core=(up -d)
[[ $NO_BUILD -eq 1 ]] || core+=(--build)
core+=(bootstrap redis backend operator)
compose "${core[@]}"

echo 'Waiting for core...'
for attempt in $(seq 1 90); do
  if compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)" >/dev/null 2>&1; then
    break
  fi
  [[ $attempt -lt 90 ]] || { echo 'Core did not start in 3 minutes.' >&2; exit 1; }
  sleep 2
done

# Same behavior as deploy.sh: every active institution has schedule and user-profile.
while read -r institution status _; do
  [[ $status == active ]] || continue
  compose exec -T backend python manage.py install-cloud "$institution" schedule >/dev/null
  compose exec -T backend python manage.py install-cloud "$institution" user-profile >/dev/null
done < <(compose exec -T backend python manage.py list-institutions)

all=(up -d --remove-orphans)
[[ $NO_BUILD -eq 1 ]] || all+=(--build)
compose "${all[@]}"
compose restart operator

echo 'Waiting for the full stack...'
services=(backend operator administration schedule user-profile web redis)
service_state() {
  local container
  container="$(compose ps -q "$1")"
  [[ -n $container ]] || { echo missing; return; }
  docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container"
}
for attempt in $(seq 1 90); do
  pending=()
  for service in "${services[@]}"; do
    state="$(service_state "$service")"
    [[ $state == healthy || $state == running ]] || pending+=("$service=$state")
  done
  [[ ${#pending[@]} -eq 0 ]] && break
  if [[ $attempt -eq 90 ]]; then
    printf 'The full stack did not become ready in 3 minutes: %s\n' "${pending[*]}" >&2
    exit 1
  fi
  sleep 2
done
compose ps

APP_PORT="$(env_get MAXLESS_APP_PORT)"
ADMIN_PORT="$(env_get MAXLESS_ADMIN_PORT)"
OPERATOR_PORT="$(env_get MAXLESS_OPERATOR_PORT)"
APP_URL="https://localhost:$APP_PORT/"
echo
echo 'Ready.'
echo "Application:        $APP_URL"
echo "Administration:     https://localhost:$ADMIN_PORT/ (opened from the application)"
echo "Operator panel:     http://localhost:$OPERATOR_PORT/"
echo 'The browser will warn once about the local self-signed certificate.'
echo "Operator password:  grep '^OPERATOR_PASSWORD=' .maxless/maxless.env | cut -d= -f2-"

if [[ -n $USER_ID ]]; then
  echo
  echo "Login URL for MAX user.id=$USER_ID (valid for 5 minutes):"
  MAX_BOT_TOKEN="$(env_get MAX_BOT_TOKEN)" python -m tools.max_auth_emulator url --user-id "$USER_ID" --base-url "$APP_URL"
else
  echo
  echo "Generate a login URL: ./scripts/maxless-deploy.sh --no-build --user-id 123456789"
fi
echo
echo "Logs: docker compose --project-name $PROJECT --env-file .maxless/maxless.env -f compose.maxless.yaml logs -f --tail=100"
