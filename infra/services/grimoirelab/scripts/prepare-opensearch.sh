#!/usr/bin/env bash

set -euo pipefail

OPENSEARCH_URL="${OPENSEARCH_URL:?OPENSEARCH_URL is required}"
OPENSEARCH_DASHBOARDS_URL="${OPENSEARCH_DASHBOARDS_URL:?OPENSEARCH_DASHBOARDS_URL is required}"
OPENSEARCH_USERNAME="${OPENSEARCH_USERNAME:?OPENSEARCH_USERNAME is required}"
OPENSEARCH_PASSWORD="${OPENSEARCH_PASSWORD:?OPENSEARCH_PASSWORD is required}"
SCRIPTS_DIR="${SCRIPTS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
SIGILS_DIR="${SIGILS_DIR:-$(cd "${SCRIPTS_DIR}/../config/sigils" && pwd)}"
WAIT_INTERVAL_SECONDS="${WAIT_INTERVAL_SECONDS:-5}"
MAX_WAIT_ATTEMPTS="${MAX_WAIT_ATTEMPTS:-120}"
SKIP_SCRIPTS="${SKIP_SCRIPTS:-bootstrap_host_setup.sh install_intake_cronjob.sh intake_new_projects.sh prepare-opensearch.sh}"

export OPENSEARCH_DASHBOARDS_URL OPENSEARCH_USERNAME OPENSEARCH_PASSWORD SIGILS_DIR

wait_for_service() {
  local service_name="$1"
  shift
  local attempt=1

  until "$@" >/dev/null 2>&1; do
    if (( attempt >= MAX_WAIT_ATTEMPTS )); then
      echo "Timed out waiting for ${service_name}" >&2
      exit 1
    fi

    echo "Waiting for ${service_name} (${attempt}/${MAX_WAIT_ATTEMPTS})..."
    sleep "${WAIT_INTERVAL_SECONDS}"
    attempt=$((attempt + 1))
  done
}

should_skip_script() {
  local script_name="$1"
  local skipped_script

  for skipped_script in ${SKIP_SCRIPTS}; do
    if [[ "${script_name}" == "${skipped_script}" ]]; then
      return 0
    fi
  done

  return 1
}

echo "Waiting for OpenSearch API at ${OPENSEARCH_URL}"
wait_for_service \
  "OpenSearch API" \
  curl --fail --silent --show-error --insecure \
  -u "${OPENSEARCH_USERNAME}:${OPENSEARCH_PASSWORD}" \
  "${OPENSEARCH_URL}/_cluster/health"

echo "Waiting for OpenSearch Dashboards API at ${OPENSEARCH_DASHBOARDS_URL}"
wait_for_service \
  "OpenSearch Dashboards API" \
  curl --fail --silent --show-error \
  -u "${OPENSEARCH_USERNAME}:${OPENSEARCH_PASSWORD}" \
  "${OPENSEARCH_DASHBOARDS_URL}/api/status"

shopt -s nullglob

for script_path in "${SCRIPTS_DIR}"/*.sh; do
  script_name="$(basename "${script_path}")"

  if should_skip_script "${script_name}"; then
    echo "Skipping ${script_name}"
    continue
  fi

  echo "Running ${script_name}"
  bash "${script_path}"
done
