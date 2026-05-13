#!/usr/bin/env bash

set -euo pipefail

OPENSEARCH_DASHBOARDS_URL="${OPENSEARCH_DASHBOARDS_URL:?OPENSEARCH_DASHBOARDS_URL is required}"
OPENSEARCH_USERNAME="${OPENSEARCH_USERNAME:?OPENSEARCH_USERNAME is required}"
OPENSEARCH_PASSWORD="${OPENSEARCH_PASSWORD:?OPENSEARCH_PASSWORD is required}"
INDEXES=("git*" "github*" "gitlab*")

for index in "${INDEXES[@]}"; do
  echo "Creating index-pattern: $index"

  curl --fail --silent --show-error -u "${OPENSEARCH_USERNAME}:${OPENSEARCH_PASSWORD}" -X POST \
    "${OPENSEARCH_DASHBOARDS_URL}/api/saved_objects/index-pattern" \
    -H "osd-xsrf: true" \
    -H "Content-Type: application/json" \
    -d "{
      \"attributes\": {
        \"title\": \"$index\"
      }
    }"

  echo
done
