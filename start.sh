#!/usr/bin/env bash
# Start both backend and frontend for development
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"

load_env_file() {
	local env_file="$1"

	if [ -f "$env_file" ]; then
		set -a
		# shellcheck disable=SC1090
		. "$env_file"
		set +a
	fi
}

load_env_file "$DIR/.env"
load_env_file "$DIR/backend/.env"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_DEV_PORT="${FRONTEND_DEV_PORT:-5173}"

echo "=== Starting Keyselector ==="

# Backend
echo "→ Starting FastAPI backend on :$BACKEND_PORT ..."
cd "$DIR/backend"
uv run uvicorn main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT" &
BACK_PID=$!

# Frontend
echo "→ Starting Vite dev server on :$FRONTEND_DEV_PORT ..."
cd "$DIR/frontend"
npm run dev -- --host 0.0.0.0 --port "$FRONTEND_DEV_PORT" &
FRONT_PID=$!

echo ""
echo "Backend:  http://localhost:$BACKEND_PORT"
echo "Frontend: http://localhost:$FRONTEND_DEV_PORT"
echo ""
echo "Press Ctrl+C to stop both."

trap "kill $BACK_PID $FRONT_PID 2>/dev/null; exit" INT TERM
wait
