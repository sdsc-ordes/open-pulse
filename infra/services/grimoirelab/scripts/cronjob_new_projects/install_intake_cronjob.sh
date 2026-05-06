#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GRIMOIRE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
WATCHER_SCRIPT="${SCRIPT_DIR}/intake_new_projects.sh"
LOG_DIR="${GRIMOIRE_DIR}/cron_logs"
LOG_FILE="${LOG_DIR}/intake_new_projects.log"
CRON_SCHEDULE="${GRIMOIRE_INTAKE_CRON_SCHEDULE:-*/5 * * * *}"
BASH_BIN="${GRIMOIRE_BASH_BIN:-$(command -v bash)}"
DOCKER_BIN="${GRIMOIRE_DOCKER_BIN:-$(command -v docker)}"
GIT_BIN="${GRIMOIRE_GIT_BIN:-$(command -v git)}"
HASH_BIN="${GRIMOIRE_HASH_BIN:-$(command -v shasum || command -v sha256sum)}"
MARKER_BEGIN="# BEGIN open-pulse grimoire intake"
MARKER_END="# END open-pulse grimoire intake"

mkdir -p "${LOG_DIR}"

CRON_COMMAND="${CRON_SCHEDULE} GRIMOIRE_DOCKER_BIN=\"${DOCKER_BIN}\" GRIMOIRE_GIT_BIN=\"${GIT_BIN}\" GRIMOIRE_HASH_BIN=\"${HASH_BIN}\" \"${BASH_BIN}\" \"${WATCHER_SCRIPT}\" >> \"${LOG_FILE}\" 2>&1"

TMP_CRONTAB="$(mktemp)"
trap 'rm -f "${TMP_CRONTAB}"' EXIT

if crontab -l >/dev/null 2>&1; then
    crontab -l | awk -v begin="${MARKER_BEGIN}" -v end="${MARKER_END}" '
        $0 == begin { skip=1; next }
        $0 == end { skip=0; next }
        skip != 1 { print }
    ' > "${TMP_CRONTAB}"
else
    : > "${TMP_CRONTAB}"
fi

{
    echo "${MARKER_BEGIN}"
    echo "${CRON_COMMAND}"
    echo "${MARKER_END}"
} >> "${TMP_CRONTAB}"

crontab "${TMP_CRONTAB}"

echo "Installed cronjob:"
echo "${CRON_COMMAND}"
