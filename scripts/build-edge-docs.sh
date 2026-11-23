#!/usr/bin/env bash
#
# Build the Docusaurus docs-site for the self-hosted edge proxy and stage it
# where Caddy serves it at https://<host>/docs/ (see
# infra/services/edge-proxy/Caddyfile and the edge-proxy volume in
# infra/open-pulse-stack/docker-compose.yml).
#
# Same sources as the GitHub Pages deploy (.github/workflows/docs-pages-deploy.yml);
# only the base URL differs: Pages serves under /open-pulse/docs/, the edge
# serves under /docs/ (the docusaurus.config.js default).
#
# Usage: scripts/build-edge-docs.sh            # build + stage
#        OPEN_PULSE_DATA_DIR=/path scripts/build-edge-docs.sh
#
# Re-run after docs change; Caddy picks up the new files immediately (no
# restart needed — it serves straight from the mounted directory).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${OPEN_PULSE_DATA_DIR:-${REPO_ROOT}/data}"
DEST="${DATA_DIR}/edge-proxy/docs"

cd "${REPO_ROOT}/docs-site"
pnpm install --frozen-lockfile
DOCS_BASE_URL="/docs/" pnpm build

# Swap in the new build atomically-ish: stage next to the target, then rename,
# so Caddy never serves a half-copied tree.
mkdir -p "$(dirname "${DEST}")"
rm -rf "${DEST}.new"
cp -R build "${DEST}.new"
rm -rf "${DEST}.old"
[ -d "${DEST}" ] && mv "${DEST}" "${DEST}.old"
mv "${DEST}.new" "${DEST}"
rm -rf "${DEST}.old"

echo "Docs staged at ${DEST} — served at /docs/"
