#!/usr/bin/env bash
#
# Create/refresh the read-only `openpulse_reader` OpenSearch role + user.
#
# The Hub connects to OpenSearch as this user (OPENSEARCH_READER_USERNAME /
# OPENSEARCH_READER_PASSWORD) to serve the OpenSearch query console for reader
# sessions and reader tokens; the same credential also allows direct read-only
# access at :9200. Without this the reader can browse index names but cannot
# search or read mappings, so the Hub's OpenSearch console fails.
#
# The role grants:
#   * cluster_composite_ops_ro + cluster:monitor/*  — health/monitor + _msearch
#   * read                                          — indices:data/read/* (search…)
#   * indices:monitor/*                             — index-level stats
#   * indices:admin/mappings/get                    — REQUIRED by the SQL plugin
#     (`_plugins/_sql`); not covered by the built-in `read` action group.
#
# Idempotent: the Security API PUTs replace, so re-running is safe. Runs
# automatically via prepare-opensearch.sh (any *.sh in this dir is executed).

set -euo pipefail

OPENSEARCH_URL="${OPENSEARCH_URL:?OPENSEARCH_URL is required}"
OPENSEARCH_USERNAME="${OPENSEARCH_USERNAME:?OPENSEARCH_USERNAME is required}"
OPENSEARCH_PASSWORD="${OPENSEARCH_PASSWORD:?OPENSEARCH_PASSWORD is required}"
READER_USERNAME="${OPENSEARCH_READER_USERNAME:-}"
READER_PASSWORD="${OPENSEARCH_READER_PASSWORD:-}"

if [[ -z "${READER_USERNAME}" || -z "${READER_PASSWORD}" ]]; then
  echo "OPENSEARCH_READER_USERNAME/PASSWORD unset — skipping reader provisioning"
  exit 0
fi

security_put() { # path body
  curl --fail --silent --show-error --insecure \
    -u "${OPENSEARCH_USERNAME}:${OPENSEARCH_PASSWORD}" \
    -H "Content-Type: application/json" \
    -o /dev/null \
    -X PUT "${OPENSEARCH_URL}/_plugins/_security/api/$1" \
    -d "$2"
}

echo "Creating/refreshing OpenSearch role '${READER_USERNAME}'"
security_put "roles/${READER_USERNAME}" '{
  "cluster_permissions": ["cluster_composite_ops_ro", "cluster:monitor/*"],
  "index_permissions": [
    {
      "index_patterns": ["*"],
      "allowed_actions": ["read", "indices:monitor/*", "indices:admin/mappings/get"]
    }
  ],
  "tenant_permissions": []
}'

echo "Creating/refreshing OpenSearch user '${READER_USERNAME}'"
# password is re-applied from OPENSEARCH_READER_PASSWORD on every run so the
# stored credential stays in sync with infra/.env.
security_put "internalusers/${READER_USERNAME}" "$(
  cat <<JSON
{
  "password": "${READER_PASSWORD}",
  "opendistro_security_roles": ["${READER_USERNAME}"],
  "backend_roles": []
}
JSON
)"

echo "OpenSearch reader '${READER_USERNAME}' ready"
