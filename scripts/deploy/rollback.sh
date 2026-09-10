#!/usr/bin/env bash
set -Eeuo pipefail

DATA_ROOT=${CEREBRO_DATA_ROOT:-/srv/cerebro}
deployment_env="${DATA_ROOT}/config/deployment.env"
rollback_image=${1:-}
if [[ -z ${rollback_image} && -r ${deployment_env} ]]; then
  rollback_image=$(awk -F= '$1 == "CEREBRO_ROLLBACK_IMAGE" {sub(/^[^=]*=/, ""); print; exit}' "${deployment_env}")
fi
if [[ ! ${rollback_image} =~ @sha256:[0-9a-f]{64}$ ]]; then
  echo "Usage: $0 registry/repository@sha256:PREVIOUS_DIGEST" >&2
  exit 2
fi

echo "Rolling back application code only. Persistent data will not be restored automatically."
"$(dirname "$0")/promote.sh" "${rollback_image}"
