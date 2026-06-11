#!/usr/bin/env bash
# Maintain the read-only DuckDB snapshots the hub's collection browser needs.
#
# The hub mounts the index data READ-ONLY, so opening a live ``<store>.duckdb``
# read-only there fails ("Conflicting lock is held in PID 0") — the browser
# instead opens a checkpointed ``<store>.ro.duckdb`` snapshot beside the live
# file (see knowledge/duckdb_browser.py::_connect). The GME publishes those for
# some stores after each ingest, but not all (openalex, infoscience, orcid,
# renkulab, ror, swissubase, epfl_graph had none) — so those collections fell
# back to a plain list instead of a table.
#
# This script regenerates any MISSING or STALE snapshot (live newer than the
# .ro.duckdb), beside its live file, so every index renders as a table. It is
# idempotent: stores whose snapshot is already fresh (incl. GME-published ones)
# are skipped, so it never clobbers a fresher snapshot. Run it on a timer
# (see crontab) so snapshots self-heal after each re-ingest.
#
# The CHECKPOINT runs inside the hub container so the snapshot is written by the
# exact DuckDB build that reads it (no storage-format mismatch). The container
# can read /data (ro) and write /data/hub (rw); we stage there, then move the
# finished file into the read-only-to-the-container index dir from the host.
#
# Usage:  refresh_duckdb_ro_snapshots.sh [--force]
#   --force   rebuild every snapshot regardless of freshness.
set -euo pipefail
shopt -s nullglob

HUB=${HUB_CONTAINER:-open-pulse-hub}
FORCE=""
[ "${1:-}" = "--force" ] && FORCE=1

# Host path backing the hub's /data mount (so we can write the .ro.duckdb).
HOST_DATA=$(docker inspect "$HUB" --format \
  '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}')
[ -n "$HOST_DATA" ] || { echo "ERR: cannot resolve /data host path for $HUB" >&2; exit 1; }

SCRATCH_CTR="/data/hub/_rosnap"            # writable in the container
SCRATCH_HOST="$HOST_DATA/hub/_rosnap"      # same dir on the host
mkdir -p "$SCRATCH_HOST"

built=0 skipped=0 failed=0
for live in "$HOST_DATA"/index/*/duckdb/*.duckdb; do
  case "$live" in *.ro.duckdb) continue;; esac
  [ -e "$live" ] || continue
  dir=$(dirname "$live"); stem=$(basename "$live" .duckdb)
  store=$(basename "$(dirname "$dir")")          # …/index/<store>/duckdb
  ro="$dir/$stem.ro.duckdb"

  # Skip when a fresh snapshot already exists (live not newer than .ro).
  if [ -z "$FORCE" ] && [ -f "$ro" ] && [ ! "$live" -nt "$ro" ]; then
    skipped=$((skipped + 1)); continue
  fi

  rel="${live#"$HOST_DATA"/}"                     # index/<store>/duckdb/<stem>.duckdb
  ctr_live="/data/$rel"
  tag="${store}__${stem}"                         # unique scratch name
  echo "→ snapshot $rel"
  if docker exec "$HUB" sh -lc "mkdir -p '$SCRATCH_CTR' && cp '$ctr_live' '$SCRATCH_CTR/$tag.duckdb'" \
     && docker exec "$HUB" python -c \
        "import duckdb,sys;c=duckdb.connect(sys.argv[1]);c.execute('CHECKPOINT');c.close()" \
        "$SCRATCH_CTR/$tag.duckdb"; then
    # Atomic publish into the index dir (writable from the host).
    mv -f "$SCRATCH_HOST/$tag.duckdb" "$ro.tmp" && mv -f "$ro.tmp" "$ro"
    built=$((built + 1))
  else
    echo "  FAILED: $rel" >&2; failed=$((failed + 1))
    rm -f "$SCRATCH_HOST/$tag.duckdb" 2>/dev/null || true
  fi
done

rmdir "$SCRATCH_HOST" 2>/dev/null || true
echo "ro-snapshots: built=$built skipped(fresh)=$skipped failed=$failed"
[ "$failed" -eq 0 ]
