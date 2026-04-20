#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
	COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
	COMPOSE_CMD=(docker-compose)
else
	echo "Docker Compose is not available. Install Docker Desktop or docker-compose." >&2
	exit 1
fi

cd "$DIR"

echo "=== Starting Keyselector containers ==="
echo "Using: ${COMPOSE_CMD[*]} up --build"

exec "${COMPOSE_CMD[@]}" up --build "$@"
