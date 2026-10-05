#!/usr/bin/env bash
set -Eeuo pipefail
# Compose reports normal container progress on stderr. Merge it once here so
# Windows PowerShell 5.1 displays clean status lines instead of ErrorRecords.
exec 2>&1

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

readonly TIMEOUT_SECONDS="${HOME_MEDIA_STARTUP_TIMEOUT:-240}"
readonly POLL_SECONDS=3
readonly -a SERVICES=(postgres redis api worker jellyfin prowlarr radarr sonarr qbittorrent)
readonly -a BIND_SERVICES=(jellyfin prowlarr radarr sonarr qbittorrent)
readonly -a COMPOSE=(docker compose --env-file .env -f docker-compose.yml --profile media)

# Docker Desktop injects its CLI into this WSL path before the normal shell
# environment necessarily learns about it.
if ! command -v docker >/dev/null 2>&1 && [[ -x /mnt/wsl/docker-desktop/cli-tools/usr/bin/docker ]]; then
    export PATH="/mnt/wsl/docker-desktop/cli-tools/usr/bin:$PATH"
fi

log() {
    printf '[home-media] %s\n' "$*"
}

fail() {
    printf '[home-media] ERROR: %s\n' "$*" >&2
    exit 1
}

[[ -f .env ]] || fail "Missing $SCRIPT_DIR/.env"
[[ -d /srv/home-media/config ]] || fail "Missing /srv/home-media/config"
[[ -d /srv/media/library/movies ]] || fail "Missing /srv/media/library/movies"
[[ -d /srv/media/library/tv ]] || fail "Missing /srv/media/library/tv"
[[ -d /srv/media/downloads ]] || fail "Missing /srv/media/downloads"

log "Validating Docker and Compose configuration"
docker info >/dev/null 2>&1 || fail "Docker Engine is not reachable from WSL"
"${COMPOSE[@]}" config --quiet

log "Starting the complete stack"
"${COMPOSE[@]}" up -d --remove-orphans

# Docker Desktop bind mounts can become detached when its WSL integration is
# restarted. Recreating only bind-mounted services re-establishes those mounts
# without unnecessarily replacing Postgres, Redis, the API, or the worker.
log "Refreshing host bind mounts"
"${COMPOSE[@]}" up -d --force-recreate --no-deps "${BIND_SERVICES[@]}"

deadline=$((SECONDS + TIMEOUT_SECONDS))
while (( SECONDS < deadline )); do
    all_ready=true
    waiting=()
    for service in "${SERVICES[@]}"; do
        container_id="$("${COMPOSE[@]}" ps -q "$service")"
        if [[ -z "$container_id" ]]; then
            all_ready=false
            waiting+=("$service:missing")
            continue
        fi
        state="$(docker inspect --format '{{.State.Status}}' "$container_id" 2>/dev/null || true)"
        health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id" 2>/dev/null || true)"
        if [[ "$state" != running || ( "$health" != none && "$health" != healthy ) ]]; then
            all_ready=false
            waiting+=("$service:$state/$health")
        fi
    done
    if [[ "$all_ready" == true ]]; then
        break
    fi
    log "Waiting: ${waiting[*]}"
    sleep "$POLL_SECONDS"
done

if [[ "$all_ready" != true ]]; then
    "${COMPOSE[@]}" ps >&2
    fail "Services did not become ready within ${TIMEOUT_SECONDS}s"
fi

log "Checking persistent configuration and media mounts"
"${COMPOSE[@]}" exec -T jellyfin sh -ec '
    test -s /config/data/jellyfin.db
    test -d /media/movies
    test -d /media/tv
'
"${COMPOSE[@]}" exec -T radarr sh -ec 'test -s /config/config.xml; test -d /data/library/movies; test -d /data/downloads'
"${COMPOSE[@]}" exec -T sonarr sh -ec 'test -s /config/config.xml; test -d /data/library/tv; test -d /data/downloads'
"${COMPOSE[@]}" exec -T qbittorrent sh -ec 'test -s /config/qBittorrent/qBittorrent.conf; test -d /data/downloads; test -d /data/library'

log "Checking internal service connectivity"
"${COMPOSE[@]}" exec -T api python - <<'PY'
import socket
import time
import urllib.request

ports = (
    ("postgres", 5432),
    ("redis", 6379),
    ("jellyfin", 8096),
    ("prowlarr", 9696),
    ("radarr", 7878),
    ("sonarr", 8989),
    ("qbittorrent", 8080),
)
urls = (
    "http://127.0.0.1:8000/healthz",
    "http://jellyfin:8096/health",
)
deadline = time.monotonic() + 120
last_error = None
while time.monotonic() < deadline:
    try:
        for host, port in ports:
            with socket.create_connection((host, port), timeout=5):
                pass
        for url in urls:
            with urllib.request.urlopen(url, timeout=8) as response:
                if response.status != 200:
                    raise RuntimeError(f"{url} returned HTTP {response.status}")
        break
    except Exception as exc:
        last_error = exc
        time.sleep(3)
else:
    raise RuntimeError(f"Services did not become functionally ready: {last_error}")
PY

log "Checking Telegram Bot API connectivity"
"${COMPOSE[@]}" exec -T worker python - <<'PY'
import httpx
import time

from app.config import settings

if not settings.telegram_bot_token:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
last_error = None
for _ in range(6):
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe",
            timeout=10,
        )
        response.raise_for_status()
        if not response.json().get("ok"):
            raise RuntimeError("Telegram rejected the configured bot token")
        break
    except (httpx.HTTPError, RuntimeError) as exc:
        last_error = exc
        time.sleep(3)
else:
    raise RuntimeError(f"Telegram Bot API did not become ready: {last_error}")
PY

log "All services, mounts, and external bot connectivity are ready"
"${COMPOSE[@]}" ps
