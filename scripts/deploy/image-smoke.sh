#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 1 )); then
  echo "Usage: $0 local-image:tag" >&2
  exit 2
fi

image=$1
work_dir=$(mktemp -d /tmp/cerebro-image-smoke.XXXXXX)
container_name="cerebro-image-smoke-${RANDOM}-$$"
container_started=0
cleanup() {
  if (( container_started )); then
    docker rm --force "${container_name}" >/dev/null 2>&1 || true
  fi
  rm -rf -- "${work_dir}"
}
trap cleanup EXIT

mkdir -p "${work_dir}/generated" "${work_dir}/reviewed" "${work_dir}/artifacts"
chmod 0777 "${work_dir}"/*

configured_user=$(docker image inspect --format '{{.Config.User}}' "${image}")
[[ ${configured_user} == "10001:10001" ]]
configured_environment=$(docker image inspect --format '{{json .Config.Env}}' "${image}")
[[ ${configured_environment} != *"API_KEY"* && ${configured_environment} != *"PASSWORD"* ]]
docker run --rm --entrypoint /bin/sh "${image}" -c '
  test ! -e /app/.git
  test ! -e /app/.env
  test ! -e /app/tests
  test ! -e /app/.codegraph
  test -z "$(find /app/knowledge/generated /app/knowledge/reviewed /app/artifacts -type f -print -quit)"
  test -f /data/workshop.duckdb
  test -z "$(find / -xdev -type f \( -name "*.duckdb" -o -name "*.duckdb.wal" \) -not -path "/data/workshop.duckdb" -print -quit)"
'
baked_db_owner=$(docker run --rm --entrypoint /bin/sh "${image}" -c "stat -c '%u:%g %a' /data/workshop.duckdb")
[[ ${baked_db_owner} == "10001:10001 444" ]]

docker run --detach --name "${container_name}" \
  --read-only \
  --tmpfs /tmp:size=128m,mode=1777 \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --publish 127.0.0.1::8000 \
  --env CEREBRO_LLM_API_KEY=image-smoke-key \
  --env CEREBRO_LLM_MODEL=image-smoke-model \
  --mount "type=bind,src=${work_dir}/generated,dst=/app/knowledge/generated" \
  --mount "type=bind,src=${work_dir}/reviewed,dst=/app/knowledge/reviewed" \
  --mount "type=bind,src=${work_dir}/artifacts,dst=/app/artifacts" \
  "${image}" >/dev/null
container_started=1

for _ in $(seq 1 60); do
  health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "${container_name}")
  [[ ${health} == "healthy" ]] && break
  [[ ${health} == "unhealthy" ]] && { docker logs "${container_name}" >&2; exit 1; }
  sleep 1
done
[[ ${health:-missing} == "healthy" ]]

port=$(docker port "${container_name}" 8000/tcp | sed -n 's/.*://p' | head -n 1)
curl --fail --silent --show-error "http://127.0.0.1:${port}/api/health/ready" >/dev/null
docker exec "${container_name}" sh -c '! touch /app/root-filesystem-must-be-read-only'
docker exec "${container_name}" sh -c 'touch /app/artifacts/write-probe /app/knowledge/generated/write-probe /app/knowledge/reviewed/write-probe /tmp/write-probe'
if docker exec "${container_name}" python -c "import duckdb; c=duckdb.connect('/data/workshop.duckdb'); c.execute('create table forbidden_write(id integer)')" >/dev/null 2>&1; then
  echo "Baked-in DuckDB unexpectedly accepted a write." >&2
  exit 1
fi

docker stop --time 10 "${container_name}" >/dev/null
exit_code=$(docker inspect --format '{{.State.ExitCode}}' "${container_name}")
[[ ${exit_code} == "0" || ${exit_code} == "143" ]]
docker rm "${container_name}" >/dev/null
container_started=0
echo "Image smoke passed for ${image}."
