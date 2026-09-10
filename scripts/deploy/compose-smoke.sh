#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 1 )); then
  echo "Usage: $0 local-image:tag" >&2
  exit 2
fi

image=$1
work_dir=$(mktemp -d /tmp/cerebro-compose-smoke.XXXXXX)
project="cerebro-smoke-$$"
compose_file="${work_dir}/deploy/compose.production.yaml"
http_port=${CEREBRO_SMOKE_HTTP_PORT:-18080}
https_port=${CEREBRO_SMOKE_HTTPS_PORT:-18443}
SMOKE_USER=smoke_user
SMOKE_PASSWORD=cerebro-compose-smoke
compose=(docker compose --project-name "${project}" --file "${compose_file}")
started=0
cleanup() {
  if (( started )); then
    CEREBRO_IMAGE="${image}" CEREBRO_DOMAIN=localhost CADDY_ACME_EMAIL=ops@example.com \
      "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
  docker run --rm --entrypoint /bin/sh --mount "type=bind,src=${work_dir},dst=/cleanup" \
    caddy:2.10.2-alpine -c 'rm -rf /cleanup/* /cleanup/.[!.]* /cleanup/..?*' >/dev/null 2>&1 || true
  rm -rf -- "${work_dir}"
}
trap cleanup EXIT

mkdir -p "${work_dir}"/{config,data,deploy,evidence,state/generated,state/reviewed,state/artifacts}
chmod 0777 "${work_dir}/data" "${work_dir}/state"/*
cp deploy/Caddyfile "${work_dir}/config/Caddyfile"
sed \
  -e "s#/srv/cerebro#${work_dir}#g" \
  -e "s#\"80:80\"#\"127.0.0.1:${http_port}:80\"#" \
  -e "s#\"443:443\"#\"127.0.0.1:${https_port}:443\"#" \
  deploy/compose.production.yaml >"${compose_file}"

docker run --rm --user 10001:10001 --entrypoint python \
  --mount "type=bind,src=${work_dir}/data,dst=/work" \
  "${image}" -c "import os, duckdb; p='/work/workshop.duckdb'; c=duckdb.connect(p); c.execute('create table smoke(id integer)'); c.close(); os.chmod(p, 0o444)"
password_hash=$(docker run --rm caddy:2.10.2-alpine caddy hash-password --algorithm bcrypt --plaintext "${SMOKE_PASSWORD}")
printf '(team_auth) {\n\tbasic_auth {\n\t\t%s %s\n\t}\n}\n' "${SMOKE_USER}" "${password_hash}" >"${work_dir}/config/caddy-users.caddy"
cat >"${work_dir}/config/cerebro.env" <<'EOF'
CEREBRO_DATABASE_PATH=/data/workshop.duckdb
CEREBRO_DATABASE_SCHEMA=main
CEREBRO_LLM_BASE_URL=http://127.0.0.1:9/v1
CEREBRO_LLM_API_KEY=compose-smoke-key
CEREBRO_LLM_MODEL=compose-smoke-model
CEREBRO_LLM_PROVIDER_ID=compose-smoke
CEREBRO_LLM_PROVIDER_NAME=Compose smoke
CEREBRO_LLM_MAX_RETRIES=0
CEREBRO_LLM_TIMEOUT_SECONDS=10
EOF

CEREBRO_IMAGE="${image}" CEREBRO_DOMAIN=localhost CADDY_ACME_EMAIL=ops@example.com \
  "${compose[@]}" config --quiet
CEREBRO_IMAGE="${image}" CEREBRO_DOMAIN=localhost CADDY_ACME_EMAIL=ops@example.com \
  "${compose[@]}" up --detach
started=1

for _ in $(seq 1 90); do
  status=$(curl --insecure --silent --output /dev/null --write-out '%{http_code}' "https://localhost:${https_port}/api/health/ready" || true)
  [[ ${status} == "401" ]] && break
  sleep 1
done
[[ ${status:-000} == "401" ]]

redirect_status=$(curl --silent --output /dev/null --write-out '%{http_code}' "http://localhost:${http_port}/api/health/ready")
[[ ${redirect_status} == "308" ]]
curl --insecure --fail --silent --show-error --user "${SMOKE_USER}:${SMOKE_PASSWORD}" \
  "https://localhost:${https_port}/api/health/ready" >"${work_dir}/evidence/readiness.json"
curl --insecure --fail --silent --show-error --user "${SMOKE_USER}:${SMOKE_PASSWORD}" \
  "https://localhost:${https_port}/" >"${work_dir}/evidence/index.html"
curl --insecure --fail --silent --show-error --user "${SMOKE_USER}:${SMOKE_PASSWORD}" \
  --header "Accept: application/json, text/event-stream" \
  --header "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"compose-smoke","version":"1"}}}' \
  "https://localhost:${https_port}/mcp/" >"${work_dir}/evidence/mcp.txt"

python3 - "${work_dir}/evidence/readiness.json" <<'PY'
import json
import pathlib
import sys
assert json.loads(pathlib.Path(sys.argv[1]).read_text())["status"] == "ready"
PY

touch "${work_dir}/state/reviewed/persistence-probe"
CEREBRO_IMAGE="${image}" CEREBRO_DOMAIN=localhost CADDY_ACME_EMAIL=ops@example.com \
  "${compose[@]}" restart cerebro caddy >/dev/null
for _ in $(seq 1 90); do
  status=$(curl --insecure --silent --output /dev/null --write-out '%{http_code}' \
    --user "${SMOKE_USER}:${SMOKE_PASSWORD}" "https://localhost:${https_port}/api/health/ready" || true)
  [[ ${status} == "200" ]] && break
  sleep 1
done
[[ ${status:-000} == "200" && -f ${work_dir}/state/reviewed/persistence-probe ]]
catalog=$(curl --insecure --fail --silent --show-error --user "${SMOKE_USER}:${SMOKE_PASSWORD}" \
  "https://localhost:${https_port}/api/bundles")
python3 -c 'import json,sys; assert json.load(sys.stdin)["default_id"] == "golden"' <<<"${catalog}"

run_payload=$(curl --insecure --fail --silent --show-error --user "${SMOKE_USER}:${SMOKE_PASSWORD}" \
  --header "Content-Type: application/json" --data '{"source_mode":"database_only"}' \
  "https://localhost:${https_port}/api/generation/runs")
run_id=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"${run_payload}")
set +e
curl --insecure --no-buffer --silent --show-error --max-time 8 \
  --user "${SMOKE_USER}:${SMOKE_PASSWORD}" \
  "https://localhost:${https_port}/api/generation/runs/${run_id}/events" >"${work_dir}/evidence/events.txt"
curl_status=$?
set -e
[[ ${curl_status} == "0" || ${curl_status} == "28" ]]
grep --quiet '^event:' "${work_dir}/evidence/events.txt"

for service in cerebro caddy; do
  container_id=$(CEREBRO_IMAGE="${image}" CEREBRO_DOMAIN=localhost CADDY_ACME_EMAIL=ops@example.com "${compose[@]}" ps --quiet "${service}")
  [[ $(docker inspect --format '{{index .HostConfig.LogConfig.Config "max-size"}}' "${container_id}") == "10m" ]]
  [[ $(docker inspect --format '{{index .HostConfig.LogConfig.Config "max-file"}}' "${container_id}") == "3" ]]
done

echo "Compose auth, TLS routing, MCP, SSE, persistence, Golden default, and logging smoke passed."
