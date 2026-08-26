#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

PYTHONPATH=src python3 -m cerebro serve --host 127.0.0.1 --port 8000 &
api_pid=$!

cleanup() {
  kill "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd apps/web
npm run dev -- --host 127.0.0.1 --port 5173
