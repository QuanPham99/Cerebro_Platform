#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 3 )); then
  echo "Usage: $0 /srv/cerebro/incoming/workshop.duckdb EXPECTED_SHA256 EXPECTED_BYTES" >&2
  exit 2
fi

staged=$1
expected_sha=${2,,}
expected_size=$3
DATA_ROOT=${CEREBRO_DATA_ROOT:-/srv/cerebro}
target="${DATA_ROOT}/data/workshop.duckdb"
deployment_env="${DATA_ROOT}/config/deployment.env"

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi
if [[ ! ${expected_sha} =~ ^[0-9a-f]{64}$ || ! ${expected_size} =~ ^[0-9]+$ ]]; then
  echo "Expected checksum must be 64 lowercase hex characters and size must be bytes." >&2
  exit 2
fi
if [[ ! -f ${staged} || -L ${staged} ]]; then
  echo "Staged database must be a regular, non-symlink file." >&2
  exit 1
fi

resolved_staged=$(realpath --canonicalize-existing -- "${staged}")
resolved_incoming=$(realpath --canonicalize-existing -- "${DATA_ROOT}/incoming")
if [[ $(dirname -- "${resolved_staged}") != "${resolved_incoming}" ]]; then
  echo "Stage the database directly under ${DATA_ROOT}/incoming." >&2
  exit 1
fi
if [[ -e ${target} ]]; then
  echo "Refusing to overwrite existing ${target}; back it up and move it explicitly first." >&2
  exit 1
fi
if [[ $(stat --format=%d -- "${resolved_staged}") != $(stat --format=%d -- "${DATA_ROOT}/data") ]]; then
  echo "Incoming and data directories must share a filesystem for atomic publication." >&2
  exit 1
fi

actual_sha=$(sha256sum -- "${resolved_staged}" | awk '{print $1}')
actual_size=$(stat --format=%s -- "${resolved_staged}")
if [[ ${actual_sha} != "${expected_sha}" || ${actual_size} != "${expected_size}" ]]; then
  echo "Database checksum or byte size does not match the operator-supplied values." >&2
  exit 1
fi

if [[ ! -r ${deployment_env} ]]; then
  echo "Missing ${deployment_env}; install the pinned image configuration first." >&2
  exit 1
fi
image=$(awk -F= '$1 == "CEREBRO_IMAGE" {sub(/^[^=]*=/, ""); print; exit}' "${deployment_env}")
if [[ ! ${image} =~ @sha256:[0-9a-f]{64}$ ]]; then
  echo "CEREBRO_IMAGE must be pinned by digest before database preflight." >&2
  exit 1
fi

chmod 0444 "${resolved_staged}"
docker run --rm \
  --entrypoint python \
  --mount "type=bind,src=${resolved_staged},dst=/data/preflight.duckdb,readonly" \
  "${image}" \
  -c "import duckdb; connection = duckdb.connect('/data/preflight.duckdb', read_only=True); connection.execute('SELECT 1').fetchone(); print({'tables': connection.execute('SELECT count(*) FROM information_schema.tables WHERE table_type = \\'BASE TABLE\\'').fetchone()[0]}); connection.close()"

chown root:"${CEREBRO_APP_GID:-10001}" "${resolved_staged}"
mv -- "${resolved_staged}" "${target}"
echo "Published verified database: ${target}"
echo "SHA256=${actual_sha}"
echo "BYTES=${actual_size}"
