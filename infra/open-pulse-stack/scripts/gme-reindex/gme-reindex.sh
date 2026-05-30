#!/usr/bin/env bash
#
# gme-reindex.sh — clear + rebuild the GME RAG indices (DuckDB + Qdrant).
#
# The git-metadata-extractor ships one DuckDB store + one or more Qdrant
# collections per provider. This script wraps the per-provider `just`
# recipes (`python -m src.index.<provider> ...`) plus the manual clear
# (rm the .duckdb file + delete the Qdrant collections) that the GME has
# no built-in recipe for.
#
# Everything runs INSIDE the running containers via `docker exec`:
#   - ingest/embed/build run in   $EXTRACTOR_CONTAINER  (has the .venv + CLIs)
#   - Qdrant deletes hit          $QDRANT_URL           (from inside the network)
#
# ───────────────────────────────────────────────────────────────────────────
# ⚠️  READ BEFORE RUNNING
#   1. DESTRUCTIVE: `clear` deletes DuckDB files and Qdrant collections.
#      Always dry-run first (default) and pass --yes to actually execute.
#   2. LOCK CONTENTION: the running GME service holds a persistent
#      read-WRITE DuckDB handle on every store. The ingest CLIs also open
#      the store read-write → they WILL conflict. Stop the extractor's
#      request workers (or the whole service) during a reindex, or expect
#      "Conflicting lock is held" errors. See the README.
#   3. SCALE: a full reindex re-hits every upstream API (GitHub, OpenAlex,
#      Zenodo, ORCID, …) and re-embeds via the EPFL RCP endpoint. This is
#      hours-to-days and rate-limited. Prefer one provider at a time.
# ───────────────────────────────────────────────────────────────────────────
#
# Usage:
#   ./gme-reindex.sh status                      # counts per provider
#   ./gme-reindex.sh clear   <provider|all>      # dry-run delete (add --yes)
#   ./gme-reindex.sh reindex <provider|all>      # ingest → embed → qdrant
#   ./gme-reindex.sh full    <provider|all>      # clear + reindex
#
# Flags: --yes (execute, not dry-run) · --scope epfl|switzerland|all (default all)
#
set -uo pipefail

EXTRACTOR_CONTAINER="${EXTRACTOR_CONTAINER:-git-metadata-extractor}"
QDRANT_CONTAINER="${QDRANT_CONTAINER:-gme-qdrant}"
QDRANT_URL="${QDRANT_URL:-http://gme-qdrant:6333}"
INDEX_ROOT="${INDEX_ROOT:-/app/index}"

DO_IT=0          # 0 = dry-run, 1 = execute
SCOPE_ARG="all"  # all → run both epfl + switzerland for scoped providers

# ── Provider table ──────────────────────────────────────────────────────────
# For each provider:
#   <key>|<duckdb-relpath>|<qdrant-collections csv>|<scoped:yes/no>|<reindex-fn>
# duckdb-relpath is under $INDEX_ROOT. Qdrant collections are deleted on clear.
# reindex-fn names the bash function below that runs the rebuild steps.
#
# NOTE: the snsf / ror / oamonitor / communities / ethz providers are NOT
# rebuilt by a simple ingest CLI (snsf loads a data.snf.ch dump; ror/communities
# use `build`; oamonitor/ethz load via raw scripts). They are marked VERIFY and
# their reindex_* functions hold the best-known command — confirm against the
# GME justfile before a production run.
PROVIDERS=(
  "openalex|openalex/duckdb/openalex.duckdb|authors,concepts,institutions|yes|reindex_openalex"
  "github|github/duckdb/github.duckdb|github_repos|yes|reindex_github"
  "huggingface|huggingface/duckdb/huggingface.duckdb|hf_models,hf_datasets,hf_spaces,hf_orgs|yes|reindex_huggingface"
  "zenodo|zenodo/duckdb/zenodo.duckdb||yes|reindex_zenodo"
  "orcid-epfl|orcid-epfl/duckdb/orcid.duckdb|orcid_epfl_persons,orcid_epfl_employments,orcid_epfl_educations|epfl|reindex_orcid_epfl"
  "orcid-switzerland|orcid-switzerland/duckdb/orcid.duckdb|orcid_switzerland_persons,orcid_switzerland_employments|switzerland|reindex_orcid_ch"
  "renkulab|renkulab/duckdb/renkulab.duckdb|renkulab_projects,renkulab_groups,renkulab_users,renkulab_data_connectors|no|reindex_renkulab"
  "swissubase|swissubase/duckdb/swissubase.duckdb||no|reindex_swissubase"
  "epfl_graph|epfl_graph/duckdb/epfl_graph.duckdb|epfl_graph_disciplines|no|reindex_epfl_graph"
  "infoscience|infoscience/duckdb/infoscience.duckdb|infoscience_articles,infoscience_persons,infoscience_organizations,infoscience_chunks|no|reindex_infoscience"
  "ethz|ethz-research-collection/duckdb/ethz_research_collection.duckdb|ethz_research_collection_articles,ethz_research_collection_persons,ethz_research_collection_organizations,ethz_research_collection_chunks|no|reindex_ethz_VERIFY"
  "oamonitor|oamonitor/duckdb/oamonitor.duckdb|oamonitor_journals,oamonitor_publications,oamonitor_publishers,oamonitor_organisations|no|reindex_oamonitor_VERIFY"
  "ror|ror/duckdb/ror.duckdb||no|reindex_ror_VERIFY"
  "snsf|snsf/duckdb/snsf.duckdb||no|reindex_snsf_VERIFY"
  "communities|communities/duckdb/communities.duckdb||no|reindex_communities_VERIFY"
)

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { printf '\033[1;36m[reindex]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[err]\033[0m %s\n' "$*" >&2; }

# Run a GME index CLI step inside the extractor container.
gme() {
  log "extractor: python -m src.index.$*"
  if [ "$DO_IT" = 1 ]; then
    docker exec "$EXTRACTOR_CONTAINER" .venv/bin/python -m src.index."$@" \
      || warn "step failed: src.index.$*"
  else
    echo "    (dry-run — pass --yes to execute)"
  fi
}

# Expand the requested scope into the list to run for a scoped provider.
scopes_to_run() {
  local pscope="$1"   # provider's allowed scope: yes|epfl|switzerland|no
  case "$pscope" in
    no) echo "" ;;
    epfl) echo "epfl" ;;
    switzerland) echo "switzerland" ;;
    yes)
      case "$SCOPE_ARG" in
        all) echo "epfl switzerland" ;;
        *)   echo "$SCOPE_ARG" ;;
      esac ;;
  esac
}

# ── Per-provider reindex functions (ingest → embed → qdrant) ──────────────────
# Each takes the scope list (may be empty for unscoped providers).
reindex_openalex()   { for s in $1; do gme openalex.cli ingest --scope "$s"; done; gme openalex.cli embed; gme openalex.cli rebuild-qdrant; }
reindex_github()     { for s in $1; do gme github ingest --scope "$s"; done; gme github embed; gme github rebuild-qdrant; }
reindex_huggingface(){ for s in $1; do gme huggingface discover-orgs --scope "$s"; gme huggingface ingest --scope "$s" --types models,datasets,spaces,orgs; done; gme huggingface embed; }
reindex_zenodo()     { for s in $1; do gme zenodo ingest --scope "$s"; done; gme zenodo embed; }
reindex_orcid_epfl() { gme orcid discover --scope epfl --source both; gme orcid ingest --scope epfl; gme orcid embed --scope epfl; }
reindex_orcid_ch()   { gme orcid discover --scope switzerland --source both; gme orcid ingest --scope switzerland; gme orcid embed --scope switzerland; }
reindex_renkulab()   { gme renkulab ingest; gme renkulab embed; }
reindex_swissubase() { gme swissubase ingest; gme swissubase embed; }
reindex_epfl_graph() { gme epfl_graph ingest; gme epfl_graph embed; }
reindex_infoscience(){ gme infoscience discover; gme infoscience fetch-text; gme infoscience extract-matches; gme infoscience extract-relations; gme infoscience fetch-related; gme infoscience ingest-duckdb; gme infoscience embed; }
# VERIFY against the GME justfile / per-provider docs before a real run:
reindex_ethz_VERIFY()        { warn "ethz uses a raw ingest (storage/ingest_raw.py) — VERIFY command"; gme ethz_research_collection ingest || true; }
reindex_oamonitor_VERIFY()   { warn "oamonitor load path unconfirmed (no ingest CLI) — VERIFY"; gme oamonitor ingest || true; }
reindex_ror_VERIFY()         { warn "ror uses build — VERIFY scope/args"; gme ror build || true; }
reindex_snsf_VERIFY()        { warn "snsf loads a data.snf.ch dump (no ingest CLI) — VERIFY load procedure"; }
reindex_communities_VERIFY() { warn "communities uses build (derives from zenodo) — VERIFY"; gme communities build || true; }

# ── Lookups ───────────────────────────────────────────────────────────────────
provider_row() { local k="$1"; for row in "${PROVIDERS[@]}"; do [ "${row%%|*}" = "$k" ] && { echo "$row"; return 0; }; done; return 1; }
all_keys()     { for row in "${PROVIDERS[@]}"; do echo "${row%%|*}"; done; }

# ── Commands ──────────────────────────────────────────────────────────────────
cmd_clear() {
  local key="$1" row; row="$(provider_row "$key")" || { err "unknown provider: $key"; exit 2; }
  IFS='|' read -r k duckdb qcols scoped fn <<<"$row"
  local dbpath="$INDEX_ROOT/$duckdb"
  log "── clear '$k' ──"
  log "  duckdb: rm -f $dbpath (+ .wal)"
  if [ "$DO_IT" = 1 ]; then
    docker exec "$EXTRACTOR_CONTAINER" sh -c "rm -f '$dbpath' '$dbpath.wal' '$dbpath.wal.broken' '$dbpath.wal.broken2'" || warn "rm failed (locked? stop the GME service first)"
  fi
  if [ -n "$qcols" ]; then
    IFS=',' read -ra cols <<<"$qcols"
    for c in "${cols[@]}"; do
      log "  qdrant: DELETE /collections/$c"
      if [ "$DO_IT" = 1 ]; then
        docker exec "$EXTRACTOR_CONTAINER" python3 -c "
import urllib.request
req=urllib.request.Request('$QDRANT_URL/collections/$c', method='DELETE')
try: print('   ->', urllib.request.urlopen(req, timeout=15).status)
except Exception as e: print('   -> err', e)
" || true
      fi
    done
  fi
}

cmd_reindex() {
  local key="$1" row; row="$(provider_row "$key")" || { err "unknown provider: $key"; exit 2; }
  IFS='|' read -r k duckdb qcols scoped fn <<<"$row"
  local sc; sc="$(scopes_to_run "$scoped")"
  log "── reindex '$k' (scopes: ${sc:-none}) → $fn ──"
  "$fn" "$sc"
}

cmd_status() {
  log "Qdrant collections + point counts:"
  docker exec "$EXTRACTOR_CONTAINER" python3 -c "
import urllib.request, json
base='$QDRANT_URL'
cols=json.load(urllib.request.urlopen(base+'/collections', timeout=10))['result']['collections']
for c in sorted(x['name'] for x in cols):
    try:
        n=json.load(urllib.request.urlopen(base+'/collections/'+c, timeout=10))['result']['points_count']
    except Exception: n='?'
    print(f'  {c:40} {n}')
" 2>&1 || true
}

# ── Arg parsing ───────────────────────────────────────────────────────────────
ACTION="${1:-}"; shift || true
TARGET="${1:-}"; [ -n "${1:-}" ] && shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) DO_IT=1 ;;
    --scope) shift; SCOPE_ARG="$1" ;;
    --scope=*) SCOPE_ARG="${1#*=}" ;;
    *) err "unknown flag: $1"; exit 2 ;;
  esac; shift
done
[ "$DO_IT" = 0 ] && [ "$ACTION" != "status" ] && warn "DRY-RUN (no changes). Re-run with --yes to execute."

targets() { if [ "$TARGET" = "all" ]; then all_keys; else echo "$TARGET"; fi; }

case "$ACTION" in
  status)  cmd_status ;;
  clear)   [ -z "$TARGET" ] && { err "clear needs <provider|all>"; exit 2; }; for t in $(targets); do cmd_clear "$t"; done ;;
  reindex) [ -z "$TARGET" ] && { err "reindex needs <provider|all>"; exit 2; }; for t in $(targets); do cmd_reindex "$t"; done ;;
  full)    [ -z "$TARGET" ] && { err "full needs <provider|all>"; exit 2; }; for t in $(targets); do cmd_clear "$t"; cmd_reindex "$t"; done ;;
  *) err "usage: $0 {status|clear|reindex|full} <provider|all> [--yes] [--scope epfl|switzerland|all]"
     echo "providers: $(all_keys | tr '\n' ' ')"; exit 2 ;;
esac
