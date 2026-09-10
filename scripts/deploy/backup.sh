#!/usr/bin/env bash
set -Eeuo pipefail

DATA_ROOT=${CEREBRO_DATA_ROOT:-/srv/cerebro}
KEEP_BACKUPS=3
: "${CEREBRO_BACKUP_DESTINATION:?Set CEREBRO_BACKUP_DESTINATION to a separately mounted off-server path}"
: "${CEREBRO_AGE_RECIPIENT_FILE:?Set CEREBRO_AGE_RECIPIENT_FILE to an age recipients file}"

for command_name in age mountpoint sha256sum tar; do
  command -v "${command_name}" >/dev/null || {
    echo "Missing required command: ${command_name}" >&2
    exit 1
  }
done
if ! mountpoint --quiet "${CEREBRO_BACKUP_DESTINATION}"; then
  echo "Backup destination must be a separately mounted off-server filesystem." >&2
  exit 1
fi
if [[ $(stat --format=%d -- "${DATA_ROOT}") == $(stat --format=%d -- "${CEREBRO_BACKUP_DESTINATION}") ]]; then
  echo "Backup destination must not use the Cerebro data filesystem." >&2
  exit 1
fi
if [[ ! -r ${CEREBRO_AGE_RECIPIENT_FILE} ]]; then
  echo "Age recipients file is not readable." >&2
  exit 1
fi

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
archive="${CEREBRO_BACKUP_DESTINATION}/cerebro-${timestamp}.tar.gz.age"
temporary_archive="${archive}.partial"
work_dir=$(mktemp -d /tmp/cerebro-backup.XXXXXX)
trap 'rm -rf -- "${work_dir}" "${temporary_archive}"' EXIT

database_sha=missing
database_bytes=0
if [[ -f ${DATA_ROOT}/data/workshop.duckdb ]]; then
  database_sha=$(sha256sum -- "${DATA_ROOT}/data/workshop.duckdb" | awk '{print $1}')
  database_bytes=$(stat --format=%s -- "${DATA_ROOT}/data/workshop.duckdb")
fi
cat >"${work_dir}/backup-manifest.txt" <<EOF
created_at=${timestamp}
database_sha256=${database_sha}
database_bytes=${database_bytes}
EOF

include=(state config deploy)
[[ -f ${DATA_ROOT}/data/workshop.duckdb ]] && include+=(data/workshop.duckdb)
tar --create --gzip --file=- \
  --directory="${DATA_ROOT}" "${include[@]}" \
  --directory="${work_dir}" backup-manifest.txt \
  | age --recipients-file "${CEREBRO_AGE_RECIPIENT_FILE}" --output "${temporary_archive}"
chmod 0600 "${temporary_archive}"
mv -- "${temporary_archive}" "${archive}"
sha256sum -- "${archive}" >"${archive}.sha256"
chmod 0600 "${archive}.sha256"

mapfile -d '' -t expired < <(
  find "${CEREBRO_BACKUP_DESTINATION}" -maxdepth 1 -type f -name 'cerebro-*.tar.gz.age' \
    -printf '%T@ %p\0' | sort --zero-terminated --numeric-sort --reverse \
    | tail --zero-terminated --lines=+$((KEEP_BACKUPS + 1)) \
    | cut --zero-terminated --delimiter=' ' --fields=2-
)
for old_archive in "${expired[@]}"; do
  rm -- "${old_archive}"
  rm -f -- "${old_archive}.sha256"
done

echo "Encrypted backup completed."
echo "BACKUP_ARCHIVE=${archive}"
echo "BACKUP_SHA256=$(awk '{print $1}' "${archive}.sha256")"
