#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if [[ -x "$project_root/.venv/bin/python" ]]; then
  python_bin="$project_root/.venv/bin/python"
else
  python_bin="python3"
fi

PYTHONPATH=src "$python_bin" -m cerebro serve --host 127.0.0.1 --port 8000 &
api_pid=$!

cleanup() {
  kill "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# A background-process failure does not trigger `set -e`; catch startup errors
# before launching a frontend that would only proxy to a dead API.
sleep 1
if ! kill -0 "$api_pid" 2>/dev/null; then
  wait "$api_pid"
fi

cd apps/web
npm run dev -- --host 127.0.0.1 --port 5173
