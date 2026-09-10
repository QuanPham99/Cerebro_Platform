#!/usr/bin/env bash
set -Eeuo pipefail

DATA_ROOT=${CEREBRO_DATA_ROOT:-/srv/cerebro}
deployment_env="${DATA_ROOT}/config/deployment.env"
evidence_dir=${1:-"${DATA_ROOT}/evidence/$(date -u +%Y%m%dT%H%M%SZ)"}

if [[ -r ${deployment_env} ]]; then
  set -a
  # shellcheck disable=SC1090
  . "${deployment_env}"
  set +a
fi

: "${CEREBRO_DOMAIN:?Set CEREBRO_DOMAIN in deployment.env}"
: "${CEREBRO_SMOKE_USER:?Set CEREBRO_SMOKE_USER in the operator environment}"
: "${CEREBRO_SMOKE_PASSWORD:?Set CEREBRO_SMOKE_PASSWORD in the operator environment}"

base_url="https://${CEREBRO_DOMAIN}"
mkdir -p "${evidence_dir}"

unauthenticated_status=$(curl --silent --output /dev/null --write-out '%{http_code}' "${base_url}/api/health/ready")
if [[ ${unauthenticated_status} != "401" ]]; then
  echo "Expected unauthenticated readiness to return 401; got ${unauthenticated_status}." >&2
  exit 1
fi

curl --fail --silent --show-error \
  --user "${CEREBRO_SMOKE_USER}:${CEREBRO_SMOKE_PASSWORD}" \
  "${base_url}/api/health/ready" >"${evidence_dir}/readiness.json"
curl --fail --silent --show-error \
  --user "${CEREBRO_SMOKE_USER}:${CEREBRO_SMOKE_PASSWORD}" \
  "${base_url}/api/runtime/status" >"${evidence_dir}/runtime-status.json"
curl --fail --silent --show-error \
  --user "${CEREBRO_SMOKE_USER}:${CEREBRO_SMOKE_PASSWORD}" \
  "${base_url}/" >"${evidence_dir}/index.html"
curl --fail --silent --show-error \
  --user "${CEREBRO_SMOKE_USER}:${CEREBRO_SMOKE_PASSWORD}" \
  --header "Accept: application/json, text/event-stream" \
  --header "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"cerebro-smoke","version":"1"}}}' \
  "${base_url}/mcp/" >"${evidence_dir}/mcp-initialize.txt"

python3 - "${evidence_dir}" <<'PY'
import json
import pathlib
import sys

evidence = pathlib.Path(sys.argv[1])
ready = json.loads((evidence / "readiness.json").read_text())
runtime = json.loads((evidence / "runtime-status.json").read_text())
assert ready["status"] == "ready"
assert runtime["web_ui"] == "built"
assert runtime["database_reachable"] is True
assert runtime["llm_configured"] is True
assert runtime["chat_ready"] is True
assert "<" in (evidence / "index.html").read_text()
PY

echo "Authenticated smoke checks passed."
echo "EVIDENCE_DIR=${evidence_dir}"
