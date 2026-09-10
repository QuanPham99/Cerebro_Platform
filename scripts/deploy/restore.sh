#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 1 )); then
  echo "Usage: $0 /mounted/off-server-backups/cerebro-TIMESTAMP.tar.gz.age" >&2
  exit 2
fi

archive=$1
DATA_ROOT=${CEREBRO_DATA_ROOT:-/srv/cerebro}
: "${CEREBRO_BACKUP_DESTINATION:?Set CEREBRO_BACKUP_DESTINATION}"
: "${CEREBRO_AGE_IDENTITY_FILE:?Set CEREBRO_AGE_IDENTITY_FILE to the offline restore identity}"

resolved_archive=$(realpath --canonicalize-existing -- "${archive}")
resolved_destination=$(realpath --canonicalize-existing -- "${CEREBRO_BACKUP_DESTINATION}")
if [[ $(dirname -- "${resolved_archive}") != "${resolved_destination}" || ! $(basename -- "${resolved_archive}") =~ ^cerebro-[0-9]{8}T[0-9]{6}Z\.tar\.gz\.age$ ]]; then
  echo "Archive must be a Cerebro backup directly inside the configured off-server destination." >&2
  exit 1
fi
if [[ -f ${resolved_archive}.sha256 ]]; then
  (cd "${resolved_destination}" && sha256sum --check "$(basename -- "${resolved_archive}").sha256")
fi

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
restore_root="${DATA_ROOT}/restore/${timestamp}"
work_dir=$(mktemp -d /tmp/cerebro-restore.XXXXXX)
trap 'rm -rf -- "${work_dir}"' EXIT
age --decrypt --identity "${CEREBRO_AGE_IDENTITY_FILE}" --output "${work_dir}/backup.tar.gz" "${resolved_archive}"

install -d -o root -g root -m 0700 "${restore_root}"
python3 - "${work_dir}/backup.tar.gz" "${restore_root}" <<'PY'
import pathlib
import shutil
import sys
import tarfile

archive = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2]).resolve()
allowed_roots = {"data", "state", "config", "deploy", "evidence"}
with tarfile.open(archive, mode="r:gz") as source:
    members = source.getmembers()
    for member in members:
        path = pathlib.PurePosixPath(member.name)
        valid_name = (
            member.name == "backup-manifest.txt"
            or (path.parts and path.parts[0] in allowed_roots)
        )
        if (
            not valid_name
            or path.is_absolute()
            or ".." in path.parts
            or not (member.isfile() or member.isdir())
        ):
            raise SystemExit(f"Unsafe archive member: {member.name}")
    for member in members:
        target = destination.joinpath(*pathlib.PurePosixPath(member.name).parts)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(member.mode & 0o777)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = source.extractfile(member)
        if payload is None:
            raise SystemExit(f"Could not read archive member: {member.name}")
        with payload, target.open("xb") as output:
            shutil.copyfileobj(payload, output)
        target.chmod(member.mode & 0o777)
PY
manifest="${restore_root}/backup-manifest.txt"
if [[ ! -r ${manifest} ]]; then
  echo "Restored archive is missing backup-manifest.txt." >&2
  exit 1
fi
expected_database_sha=$(awk -F= '$1 == "database_sha256" {print $2; exit}' "${manifest}")
expected_database_bytes=$(awk -F= '$1 == "database_bytes" {print $2; exit}' "${manifest}")
if [[ ${expected_database_sha} != "missing" ]]; then
  restored_database="${restore_root}/data/workshop.duckdb"
  [[ -f ${restored_database} ]]
  [[ $(sha256sum -- "${restored_database}" | awk '{print $1}') == "${expected_database_sha}" ]]
  [[ $(stat --format=%s -- "${restored_database}") == "${expected_database_bytes}" ]]
fi
echo "Restore drill completed in isolated directory: ${restore_root}"
echo "No live data was replaced. Compare and promote restored state only during an explicit maintenance action."
