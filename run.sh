#!/usr/bin/env bash
# Launch the API and the web UI together. Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")"

[ -d .venv ] || { echo "no .venv — see README"; exit 1; }
[ -d web/node_modules ] || (cd web && npm install)

# A previous run wedged on this port will silently swallow every request.
if lsof -ti tcp:8000 >/dev/null 2>&1; then
  echo "port 8000 busy — killing previous API"
  lsof -ti tcp:8000 | xargs kill -9
  sleep 1
fi

./.venv/bin/python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 &
API=$!
trap 'kill $API 2>/dev/null || true' EXIT INT TERM

cd web && npm run dev
