#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIGILS_DIR="${SIGILS_DIR:-${SCRIPT_DIR}/../config/sigils}"
OPENSEARCH_DASHBOARDS_URL="${OPENSEARCH_DASHBOARDS_URL:?OPENSEARCH_DASHBOARDS_URL is required}"
OPENSEARCH_USERNAME="${OPENSEARCH_USERNAME:?OPENSEARCH_USERNAME is required}"
OPENSEARCH_PASSWORD="${OPENSEARCH_PASSWORD:?OPENSEARCH_PASSWORD is required}"

if [[ ! -d "${SIGILS_DIR}" ]]; then
  echo "Error: sigils directory not found: ${SIGILS_DIR}"
  exit 1
fi

shopt -s nullglob

for file in "${SIGILS_DIR}"/*.ndjson; do
  echo "Importing: $file"

  curl --fail --silent --show-error -u "${OPENSEARCH_USERNAME}:${OPENSEARCH_PASSWORD}" -X POST \
    "${OPENSEARCH_DASHBOARDS_URL}/api/saved_objects/_import?overwrite=true" \
    -H "osd-xsrf:true" \
    --form "file=@${file}"

  echo
done
