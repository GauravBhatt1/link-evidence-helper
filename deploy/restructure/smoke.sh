#!/usr/bin/env sh
set -eu

compose_file="${COMPOSE_FILE:-deploy/restructure/compose.yaml}"
preview_port="${LINK_EVIDENCE_PREVIEW_PORT:-18781}"
project_name="${COMPOSE_PROJECT_NAME:-link-evidence-restructure-smoke}"
health_url="http://127.0.0.1:${preview_port}/healthz"

case "$preview_port" in
  8765)
    echo "refusing to use protected production port 8765" >&2
    exit 2
    ;;
esac

: "${LINK_EVIDENCE_ADMIN_TOKEN:?LINK_EVIDENCE_ADMIN_TOKEN is required}"

cleanup() {
  docker compose -p "$project_name" -f "$compose_file" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

export LINK_EVIDENCE_PREVIEW_PORT="$preview_port"
docker compose -p "$project_name" -f "$compose_file" up --build --detach redis worker api

attempt=0
until response="$(curl --fail --silent --show-error --max-time 2 "$health_url" 2>/dev/null)"; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    docker compose -p "$project_name" -f "$compose_file" ps
    docker compose -p "$project_name" -f "$compose_file" logs --no-color api worker redis
    echo "API health check did not become ready" >&2
    exit 1
  fi
  sleep 1
done

HEALTH_RESPONSE="$response" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["HEALTH_RESPONSE"])
expected = {
    "ok": True,
    "service": "link-evidence-api",
    "mode": "development-fixture",
    "jobs": "redis-development",
}
if payload != expected:
    raise SystemExit(f"unexpected health payload: {payload!r}")
PY

for service in api worker redis; do
  container_id="$(docker compose -p "$project_name" -f "$compose_file" ps -q "$service")"
  test -n "$container_id"
  test "$(docker inspect --format '{{.State.Running}}' "$container_id")" = "true"
  test "$(docker inspect --format '{{.HostConfig.ReadonlyRootfs}}' "$container_id")" = "true"
  test "$(docker inspect --format '{{json .HostConfig.CapDrop}}' "$container_id")" = '["ALL"]'
done

api_container="$(docker compose -p "$project_name" -f "$compose_file" ps -q api)"
worker_container="$(docker compose -p "$project_name" -f "$compose_file" ps -q worker)"
test "$(docker inspect --format '{{.Config.User}}' "$api_container")" = "nonroot:nonroot"
test "$(docker inspect --format '{{.Config.User}}' "$worker_container")" = "nonroot:nonroot"

printf 'compose smoke rehearsal passed on loopback port %s\n' "$preview_port"
