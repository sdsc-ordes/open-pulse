#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GRIMOIRE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="${GRIMOIRE_REPO_ROOT:-$(git -C "${GRIMOIRE_DIR}" rev-parse --show-toplevel)}"
COMPOSE_FILE="${GRIMOIRE_COMPOSE_FILE:-${GRIMOIRE_DIR}/docker-compose.yml}"
ENV_FILE="${GRIMOIRE_ENV_FILE:-${GRIMOIRE_DIR}/.env}"
WATCHED_FILE="${GRIMOIRE_WATCHED_FILE:-${GRIMOIRE_DIR}/config/projects.json}"
STATE_DIR="${GRIMOIRE_STATE_DIR:-${GRIMOIRE_DIR}/tmp}"
STATE_FILE="${GRIMOIRE_STATE_FILE:-${STATE_DIR}/projects_file_checksum_mordred}"
ENABLE_GIT_PULL="${GRIMOIRE_ENABLE_GIT_PULL:-true}"
DOCKER_BIN="${GRIMOIRE_DOCKER_BIN:-$(command -v docker)}"
GIT_BIN="${GRIMOIRE_GIT_BIN:-$(command -v git)}"
HASH_BIN="${GRIMOIRE_HASH_BIN:-}"

mkdir -p "${STATE_DIR}"

hash_file() {
    if [[ -n "${HASH_BIN}" ]]; then
        case "$(basename "${HASH_BIN}")" in
            shasum)
                "${HASH_BIN}" -a 256 "$1" | awk '{print $1}'
                return
                ;;
            sha256sum)
                "${HASH_BIN}" "$1" | awk '{print $1}'
                return
                ;;
        esac
    fi

    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
        return
    fi

    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
        return
    fi

    echo "No SHA-256 command found. Install 'shasum' or 'sha256sum'." >&2
    exit 1
}

if [[ ! -f "${WATCHED_FILE}" ]]; then
    echo "The watched file does not exist: ${WATCHED_FILE}"
    exit 1
fi

CURRENT_SUM="$(hash_file "${WATCHED_FILE}")"

if [[ ! -f "${STATE_FILE}" ]]; then
    echo "${CURRENT_SUM}" > "${STATE_FILE}"
    exit 0
fi

if [[ "${ENABLE_GIT_PULL}" == "true" ]]; then
    if ! "${GIT_BIN}" -C "${REPO_ROOT}" pull --quiet; then
        echo "Git pull failed in ${REPO_ROOT}"
        exit 1
    fi

    CURRENT_SUM="$(hash_file "${WATCHED_FILE}")"
fi

PREVIOUS_SUM="$(<"${STATE_FILE}")"

if [[ "${CURRENT_SUM}" != "${PREVIOUS_SUM}" ]]; then
    echo "Projects file changed. Restarting docker compose for mordred..."

    "${DOCKER_BIN}" compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" restart mordred

    echo "${CURRENT_SUM}" > "${STATE_FILE}"
fi
