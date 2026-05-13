#!/usr/bin/env bash
#
# fix-data-perms.sh — make every host-side bind-mount directory writable by
# the container that needs it. Idempotent. Safe to run before *or* after
# `docker compose up`.
#
# Why this exists: every grimoirelab/open-pulse service runs as its own
# in-image UID (sortinghat=999, mordred/opensearch=1000, mariadb=999,
# extractor=non-root-user, …). Docker auto-creates missing bind-mount
# sources as root:root, which then blocks the service from writing. This
# script chowns each known source to the right UID and (optionally)
# restarts affected containers.
#
# Usage:
#   sudo bash scripts/fix-data-perms.sh              # apply fixes
#   sudo bash scripts/fix-data-perms.sh --dry-run    # show what would change
#   sudo bash scripts/fix-data-perms.sh --restart    # also restart affected containers
#   sudo bash scripts/fix-data-perms.sh --sparql-users   # only (re)generate the missing Caddy users file
#
# Exit code: 0 on success, 1 on any failure.

set -uo pipefail

DRY_RUN=0
RESTART=0
SPARQL_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --dry-run)        DRY_RUN=1 ;;
    --restart)        RESTART=1 ;;
    --sparql-users)   SPARQL_ONLY=1 ;;
    -h|--help)        sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "Run with sudo."; exit 1; }

# Discover repo root from this script's location: scripts/fix-data-perms.sh → repo
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null && pwd)
REPO=$(cd -- "${SCRIPT_DIR}/.." && pwd)
DATA="${REPO}/data"
INFRA="${REPO}/infra"

red()   { printf '\033[31m%s\033[0m' "$*"; }
green() { printf '\033[32m%s\033[0m' "$*"; }
yellow(){ printf '\033[33m%s\033[0m' "$*"; }
gray()  { printf '\033[90m%s\033[0m' "$*"; }

log()    { printf '\n=== %s ===\n' "$*"; }
do_or_say() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '  %s %s\n' "$(yellow '[dry-run]')" "$*"
  else
    eval "$*"
  fi
}

###############################################################################
# Helpers
###############################################################################

# UID/GID of a user inside an image. Spins up a throwaway container (--user 0
# so id(1) is allowed). Falls back to the supplied default if the lookup fails.
image_uid_gid() {
  local image="$1" user="$2" default="$3"
  local out
  out=$(docker run --rm --user 0:0 --entrypoint '' "$image" id "$user" 2>/dev/null || true)
  local uid gid
  uid=$(printf '%s' "$out" | sed -n 's/.*uid=\([0-9]*\).*/\1/p')
  gid=$(printf '%s' "$out" | sed -n 's/.*gid=\([0-9]*\).*/\1/p')
  if [[ -z "$uid" || -z "$gid" ]]; then
    printf '%s' "$default"
  else
    printf '%s:%s' "$uid" "$gid"
  fi
}

# UID/GID of a *running* container's runtime user (preferred — exact).
container_uid_gid() {
  local name="$1" default="$2"
  if docker ps --format '{{.Names}}' | grep -qx "$name"; then
    local out
    out=$(docker exec "$name" id 2>/dev/null || true)
    local uid gid
    uid=$(printf '%s' "$out" | sed -n 's/.*uid=\([0-9]*\).*/\1/p')
    gid=$(printf '%s' "$out" | sed -n 's/.*gid=\([0-9]*\).*/\1/p')
    [[ -n "$uid" && -n "$gid" ]] && { printf '%s:%s' "$uid" "$gid"; return; }
  fi
  printf '%s' "$default"
}

ensure_dir_owned() {
  # ensure_dir_owned <path> <uid:gid> <label>
  local path="$1" owner="$2" label="$3"
  local uid="${owner%%:*}" gid="${owner##*:}"

  if [[ ! -e "$path" ]]; then
    do_or_say "mkdir -p '$path'"
  fi

  if [[ "$DRY_RUN" -eq 1 ]]; then
    local cur
    cur=$(stat -c '%u:%g' "$path" 2>/dev/null || echo '?:?')
    printf '  %s %-50s  cur=%-9s  -> target=%s\n' "$(gray '[would chown]')" "$label" "$cur" "$owner"
    return
  fi

  chown -R "${uid}:${gid}" "$path"
  # Make sure dirs are at least u+rwx, g+rx so the container user can traverse/write.
  find "$path" -type d -exec chmod u+rwX,g+rX,o+rX {} + 2>/dev/null
  local newowner
  newowner=$(stat -c '%u:%g' "$path")
  printf '  %s %-50s  now %s\n' "$(green '[ok]')" "$label" "$newowner"
}

restart_if_running() {
  local name="$1"
  [[ "$RESTART" -eq 1 ]] || return 0
  if docker ps -a --format '{{.Names}}' | grep -qx "$name"; then
    do_or_say "docker restart '$name' >/dev/null"
    printf '  %s restarted %s\n' "$(green '[ok]')" "$name"
  fi
}

###############################################################################
# Phase 1 — sparql-proxy missing users file
###############################################################################

fix_sparql_users() {
  log "sparql-proxy: ensure Caddy basic_auth users file"
  local sp_dir="$INFRA/services/sparql-proxy"
  local users_dir="$sp_dir/users"
  local caddy="$sp_dir/Caddyfile"
  local users_file="$users_dir/sparql_users.caddy"
  local env_file="$INFRA/env/.env"

  mkdir -p "$users_dir"
  if compgen -G "$users_dir/*" >/dev/null; then
    printf '  %s users file already present in %s\n' "$(green '[ok]')" "$users_dir"
  else
    if [[ ! -f "$env_file" ]]; then
      printf '  %s no %s; skipping users file generation\n' "$(yellow '[skip]')" "$env_file"
      return
    fi
    local sparql_auth user pass
    sparql_auth=$(grep -E '^SPARQL_AUTH=' "$env_file" | head -1 | sed 's/^SPARQL_AUTH=//')
    user=${sparql_auth%%/*}
    pass=${sparql_auth#*/}
    if [[ -z "$user" || -z "$pass" || "$user" == "$pass" ]]; then
      printf '  %s SPARQL_AUTH missing or malformed in %s\n' "$(red '[fail]')" "$env_file"
      return
    fi
    if [[ "$DRY_RUN" -eq 1 ]]; then
      printf '  %s would generate %s for user %s\n' "$(yellow '[dry-run]')" "$users_file" "$user"
      return
    fi
    local hash
    hash=$(docker run --rm caddy:2-alpine caddy hash-password --plaintext "$pass" 2>/dev/null)
    if [[ -z "$hash" ]]; then
      printf '  %s caddy hash-password failed\n' "$(red '[fail]')"
      return
    fi
    printf '%s %s\n' "$user" "$hash" > "$users_file"
    chmod 0644 "$users_file"
    printf '  %s wrote %s\n' "$(green '[ok]')" "$users_file"
  fi

  # Fix Caddyfile if it still imports the directory directly.
  if grep -qE '^[[:space:]]*import /etc/caddy/users[[:space:]]*$' "$caddy"; then
    if [[ "$DRY_RUN" -eq 1 ]]; then
      printf '  %s would change "import /etc/caddy/users" -> "import /etc/caddy/users/*"\n' "$(yellow '[dry-run]')"
    else
      cp -a "$caddy" "${caddy}.bak.$(date +%Y%m%d-%H%M%S)"
      sed -i -E 's|^([[:space:]]*)import /etc/caddy/users[[:space:]]*$|\1import /etc/caddy/users/*|' "$caddy"
      printf '  %s patched Caddyfile import to glob\n' "$(green '[ok]')"
    fi
  fi

  restart_if_running sparql-proxy-open-pulse
}

###############################################################################
# Phase 2 — bind-mount ownership fixes (the long list)
###############################################################################

fix_perms() {
  log "Bind-mount ownership fixes"

  # OpenSearch — runs as opensearch (UID 1000) in opensearchproject/opensearch:3
  ensure_dir_owned "$DATA/grimoirelab/opensearch-data" \
    "$(image_uid_gid opensearchproject/opensearch:3 opensearch 1000:1000)" \
    'grimoirelab/opensearch-data'

  # SortingHat — runs as sortinghat (UID 999) in grimoirelab/sortinghat
  local sh_owner
  sh_owner=$(container_uid_gid open-pulse-stack-sortinghat-1 \
              "$(image_uid_gid grimoirelab/sortinghat sortinghat 999:999)")
  ensure_dir_owned "$DATA/grimoirelab/sortinghat/static" "$sh_owner" \
    'grimoirelab/sortinghat/static'

  # Mordred — runs as grimoire (UID 1000) in grimoirelab/grimoirelab
  local md_owner
  md_owner=$(container_uid_gid open-pulse-mordred \
              "$(image_uid_gid grimoirelab/grimoirelab:latest grimoire 1000:1000)")
  for sub in logs perceval cache tmp; do
    ensure_dir_owned "$DATA/grimoirelab/mordred/$sub" "$md_owner" \
      "grimoirelab/mordred/$sub"
  done

  # Projects-conf — shared between mordred + applier; both run as UID 1000
  ensure_dir_owned "$DATA/grimoirelab/projects-conf" "$md_owner" \
    'grimoirelab/projects-conf'

  # MariaDB — runs as mysql (UID 999)
  ensure_dir_owned "$DATA/grimoirelab/mariadb" "999:999" \
    'grimoirelab/mariadb'

  # Valkey — runs as valkey (UID 999)
  ensure_dir_owned "$DATA/grimoirelab/valkey" "999:999" \
    'grimoirelab/valkey'

  # Git-metadata-extractor — runs as non-root-user with unknown UID.
  # Prefer the live container; fall back to image lookup.
  local ex_owner
  ex_owner=$(container_uid_gid git-metadata-extractor "")
  if [[ -z "$ex_owner" ]]; then
    # Last-resort fallback — the image's USER directive.
    local ex_img
    ex_img=$(grep -E '^EXTRACTOR_IMAGE=' "$INFRA/env/.env" 2>/dev/null | sed 's/^EXTRACTOR_IMAGE=//')
    : "${ex_img:=ghcr.io/imaging-plaza/git-metadata-extractor:develop}"
    ex_owner=$(image_uid_gid "$ex_img" non-root-user 1000:1000)
  fi
  ensure_dir_owned "$DATA/extractor" "$ex_owner" 'extractor (/app/data)'

  # Qdrant — vector DB. Image runs as qdrant (UID 1000).
  ensure_dir_owned "$DATA/qdrant/storage" "1000:1000" 'qdrant/storage'

  # Neo4j — runs as neo4j (UID 7474)
  for sub in data logs config plugins; do
    ensure_dir_owned "$DATA/neo4j/$sub" "7474:7474" "neo4j/$sub"
  done

  # Sparql-proxy — caddy:2-alpine runs as root by default, but its data/
  # and config/ paths get root-owned files anyway; harmless to set explicitly.
  for sub in data config; do
    ensure_dir_owned "$DATA/sparql-proxy/$sub" "0:0" "sparql-proxy/$sub"
  done

  # Hub — same image as the CLI, runs as the package's runtime user (1000).
  ensure_dir_owned "$DATA/hub" "1000:1000" 'hub'

  # Portainer — runs as root inside its image; chown a no-op but creates dir.
  ensure_dir_owned "$DATA/portainer" "0:0" 'portainer'

  # GrimoireLab-db (Postgres profile) — postgres image runs as postgres (UID 999).
  ensure_dir_owned "$DATA/grimoirelab-db" "999:999" 'grimoirelab-db'

  # Restart affected services if requested.
  if [[ "$RESTART" -eq 1 ]]; then
    log "Restarting affected services"
    for c in \
        open-pulse-stack-opensearch-node1-1 \
        open-pulse-stack-sortinghat-1 \
        open-pulse-stack-sortinghat_worker-1 \
        open-pulse-mordred \
        open-pulse-stack-mariadb-1 \
        open-pulse-stack-valkey-1 \
        git-metadata-extractor \
        gme-qdrant \
        neo4j-open-pulse \
        sparql-proxy-open-pulse \
        open-pulse-hub \
        open-pulse-stack-nginx-1 \
    ; do
      restart_if_running "$c"
    done
  fi
}

###############################################################################
# Drive
###############################################################################

echo "Repo:  $REPO"
echo "Data:  $DATA"
[[ "$DRY_RUN" -eq 1 ]] && echo "Mode:  $(yellow 'DRY-RUN')"
[[ "$RESTART" -eq 1 ]] && echo "Mode:  $(green 'WITH RESTART')"

if [[ "$SPARQL_ONLY" -eq 1 ]]; then
  fix_sparql_users
else
  fix_sparql_users
  fix_perms
fi

echo
echo "Done."
