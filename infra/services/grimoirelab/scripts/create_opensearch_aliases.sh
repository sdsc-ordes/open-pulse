#!/usr/bin/env bash

set -euo pipefail

OPENSEARCH_URL="${OPENSEARCH_URL:?OPENSEARCH_URL is required}"
OPENSEARCH_USERNAME="${OPENSEARCH_USERNAME:?OPENSEARCH_USERNAME is required}"
OPENSEARCH_PASSWORD="${OPENSEARCH_PASSWORD:?OPENSEARCH_PASSWORD is required}"
OPENSEARCH_ALIAS_INDEX="${OPENSEARCH_ALIAS_INDEX:-github_enriched}"
OPENSEARCH_ALIAS_NAME="${OPENSEARCH_ALIAS_NAME:-github_issues}"

if ! curl --silent --show-error --insecure \
  -u "${OPENSEARCH_USERNAME}:${OPENSEARCH_PASSWORD}" \
  -o /dev/null \
  -w "%{http_code}" \
  "${OPENSEARCH_URL}/${OPENSEARCH_ALIAS_INDEX}" | grep -q "^200$"; then
  echo "Skipping alias creation because index ${OPENSEARCH_ALIAS_INDEX} does not exist yet"
  exit 0
fi

echo "Creating alias ${OPENSEARCH_ALIAS_NAME} for index ${OPENSEARCH_ALIAS_INDEX}"

curl --fail --silent --show-error --insecure \
  -u "${OPENSEARCH_USERNAME}:${OPENSEARCH_PASSWORD}" \
  -X POST "${OPENSEARCH_URL}/_aliases" \
  -H "Content-Type: application/json" \
  -d "{
    \"actions\": [
      {
        \"add\": {
          \"index\": \"${OPENSEARCH_ALIAS_INDEX}\",
          \"alias\": \"${OPENSEARCH_ALIAS_NAME}\"
        }
      }
    ]
  }"

echo
