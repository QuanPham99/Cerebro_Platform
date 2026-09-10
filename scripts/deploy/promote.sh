#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 1 )); then
  echo "Usage: $0 registry/repository@sha256:DIGEST" >&2
  exit 2
fi

next_image=$1
DATA_ROOT=${CEREBRO_DATA_ROOT:-/srv/cerebro}
deployment_env="${DATA_ROOT}/config/deployment.env"
compose_file="${DATA_ROOT}/deploy/compose.production.yaml"
: "${CEREBRO_NO_ACTIVE_RUNS_CONFIRMED:?Set CEREBRO_NO_ACTIVE_RUNS_CONFIRMED=yes after checking generation status}"
if [[ ${CEREBRO_NO_ACTIVE_RUNS_CONFIRMED} != "yes" ]]; then
  echo "Promotion requires an explicit no-active-generation-run confirmation." >&2
  exit 1
fi
if [[ ! ${next_image} =~ ^[^[:space:]@]+/[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]]; then
  echo "Promotion accepts only a full registry/repository@sha256 digest." >&2
  exit 2
fi
if [[ ! -r ${deployment_env} || ! -r ${compose_file} ]]; then
  echo "Missing production deployment configuration under ${DATA_ROOT}." >&2
  exit 1
fi

previous_image=$(awk -F= '$1 == "CEREBRO_IMAGE" {sub(/^[^=]*=/, ""); print; exit}' "${deployment_env}")
if [[ ! ${previous_image} =~ @sha256:[0-9a-f]{64}$ ]]; then
  echo "Current CEREBRO_IMAGE is not digest-pinned; refusing promotion." >&2
  exit 1
fi

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
evidence_dir="${DATA_ROOT}/evidence/${timestamp}"
install -d -o root -g root -m 0700 "${evidence_dir}"
backup_output=$("$(dirname "$0")/backup.sh")
printf '%s\n' "${backup_output}" | tee "${evidence_dir}/backup.txt"

saved_env=$(mktemp "${DATA_ROOT}/config/deployment.env.previous.XXXXXX")
cp --preserve=mode,ownership,timestamps -- "${deployment_env}" "${saved_env}"
new_env=$(mktemp "${DATA_ROOT}/config/deployment.env.next.XXXXXX")
cleanup() { rm -f -- "${saved_env}" "${new_env}"; }
rollback_on_error() {
  exit_code=$?
  trap - ERR
  cp --preserve=mode,ownership -- "${saved_env}" "${new_env}"
  mv -- "${new_env}" "${deployment_env}"
  docker compose --env-file "${deployment_env}" --file "${compose_file}" pull cerebro || true
  docker compose --env-file "${deployment_env}" --file "${compose_file}" up --detach || true
  cleanup
  echo "Promotion failed; restored prior image pin ${previous_image}. Persistent data was not changed." >&2
  exit "${exit_code}"
}
trap rollback_on_error ERR
trap cleanup EXIT

awk -v image="${next_image}" -v rollback="${previous_image}" '
  BEGIN { image_written = 0; rollback_written = 0 }
  /^CEREBRO_IMAGE=/ { print "CEREBRO_IMAGE=" image; image_written = 1; next }
  /^CEREBRO_ROLLBACK_IMAGE=/ { print "CEREBRO_ROLLBACK_IMAGE=" rollback; rollback_written = 1; next }
  { print }
  END {
    if (!image_written) print "CEREBRO_IMAGE=" image
    if (!rollback_written) print "CEREBRO_ROLLBACK_IMAGE=" rollback
  }
' "${deployment_env}" >"${new_env}"
chmod --reference="${deployment_env}" "${new_env}"
chown --reference="${deployment_env}" "${new_env}"
mv -- "${new_env}" "${deployment_env}"

compose=(docker compose --env-file "${deployment_env}" --file "${compose_file}")
"${compose[@]}" config --quiet
"${compose[@]}" pull cerebro
"${compose[@]}" up --detach

container_id=$("${compose[@]}" ps --quiet cerebro)
for _ in $(seq 1 60); do
  health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "${container_id}")
  [[ ${health} == "healthy" ]] && break
  [[ ${health} == "unhealthy" ]] && { echo "Cerebro became unhealthy." >&2; false; }
  sleep 2
done
[[ ${health:-missing} == "healthy" ]] || { echo "Timed out waiting for Cerebro readiness." >&2; false; }

"$(dirname "$0")/smoke.sh" "${evidence_dir}" | tee "${evidence_dir}/smoke.txt"
revision=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "${next_image}")
cat >"${evidence_dir}/release.txt" <<EOF
promoted_at=${timestamp}
image=${next_image}
git_commit=${revision}
rollback_image=${previous_image}
$(printf '%s\n' "${backup_output}" | awk -F= '/^BACKUP_ARCHIVE=|^BACKUP_SHA256=/ {print}')
EOF

trap - ERR
cleanup
trap - EXIT
echo "Promotion passed: ${next_image}"
echo "EVIDENCE_DIR=${evidence_dir}"
