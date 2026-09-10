#!/usr/bin/env bash
set -Eeuo pipefail

DATA_ROOT=${CEREBRO_DATA_ROOT:-/srv/cerebro}
APP_UID=10001
APP_GID=10001

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

architecture=$(uname -m)
if [[ ${architecture} != "x86_64" && ${architecture} != "amd64" ]]; then
  echo "Cerebro production images require an x86-64 vServer; found ${architecture}." >&2
  exit 1
fi

if [[ ! -r /etc/os-release ]]; then
  echo "Cannot identify the operating system." >&2
  exit 1
fi
. /etc/os-release
if [[ ${ID:-} != "ubuntu" ]]; then
  echo "The first deployment requires Ubuntu LTS; found ${PRETTY_NAME:-unknown}." >&2
  exit 1
fi

if (( $(nproc) < 2 )); then
  echo "At least 2 vCPUs are required." >&2
  exit 1
fi
memory_kib=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
if (( memory_kib < 3800000 )); then
  echo "At least 4 GB RAM is required." >&2
  exit 1
fi

for command_name in docker mountpoint findmnt; do
  command -v "${command_name}" >/dev/null || {
    echo "Missing required command: ${command_name}" >&2
    exit 1
  }
done
docker compose version >/dev/null

if ! mountpoint --quiet "${DATA_ROOT}"; then
  echo "${DATA_ROOT} must already be a separate volume mounted by filesystem UUID." >&2
  exit 1
fi
mount_source=$(findmnt --noheadings --output SOURCE --target "${DATA_ROOT}" | xargs)
mount_uuid=$(findmnt --noheadings --output UUID --target "${DATA_ROOT}" | xargs)
if [[ -z ${mount_uuid} ]] || ! awk -v uuid="UUID=${mount_uuid}" -v target="${DATA_ROOT}" '
  $1 == uuid && $2 == target { found = 1 }
  END { exit(found ? 0 : 1) }
' /etc/fstab; then
  echo "${DATA_ROOT} must have a matching UUID entry in /etc/fstab." >&2
  exit 1
fi
minimum_free_kib=${CEREBRO_MIN_FREE_KIB:-10485760}
available_kib=$(df --output=avail "${DATA_ROOT}" | tail -n 1 | xargs)
if (( available_kib < minimum_free_kib )); then
  echo "${DATA_ROOT} needs at least ${minimum_free_kib} KiB free for the configured growth margin." >&2
  exit 1
fi

install -d -o root -g root -m 0755 "${DATA_ROOT}"
install -d -o root -g root -m 0700 "${DATA_ROOT}/config" "${DATA_ROOT}/incoming"
install -d -o root -g root -m 0755 "${DATA_ROOT}/data" "${DATA_ROOT}/deploy" "${DATA_ROOT}/evidence"
install -d -o "${APP_UID}" -g "${APP_GID}" -m 0750 \
  "${DATA_ROOT}/state/generated" \
  "${DATA_ROOT}/state/reviewed" \
  "${DATA_ROOT}/state/artifacts"

echo "Provisioning checks passed for ${PRETTY_NAME} on ${architecture}."
echo "Persistent data root: ${DATA_ROOT} (${mount_source})"
echo "Next: install config files, restrict them to 0600, configure DNS/firewall, and log in with the pull-only vCR robot."
