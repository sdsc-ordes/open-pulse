#!/bin/sh
# Nightly maintenance for the long-running sirmordred loop.
#
# sirmordred (update = true) never exits on its own: its RSS creeps up over a
# full collection/enrichment cycle until the cgroup OOM-kills it, and its
# plain logging.FileHandler appends to all.log forever. Once a day, at
# MORDRED_RESTART_AT (HH:MM, container time = UTC), this script:
#   1. stops mordred cleanly (frees the leaked memory),
#   2. rotates all.log -> all.log.1.gz … keeping MORDRED_LOG_KEEP archives,
#   3. starts mordred again (it resumes incrementally).
# Rotation happens while mordred is stopped, so no lines are lost and no
# copytruncate tricks are needed.
set -eu

CONTAINER="${MORDRED_CONTAINER_NAME:-open-pulse-mordred}"
AT="${MORDRED_RESTART_AT:-03:00}"
KEEP="${MORDRED_LOG_KEEP:-3}"
LOG_DIR="${MORDRED_LOG_DIR:-/logs}"
LOG="$LOG_DIR/all.log"

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') mordred-maintenance: $*"; }

rotate_logs() {
  [ -f "$LOG" ] || { log "no $LOG, nothing to rotate"; return 0; }
  i="$KEEP"
  rm -f "$LOG.$i.gz"
  while [ "$i" -gt 1 ]; do
    prev=$((i - 1))
    [ -f "$LOG.$prev.gz" ] && mv "$LOG.$prev.gz" "$LOG.$i.gz"
    i="$prev"
  done
  mv "$LOG" "$LOG.1"
  gzip -f "$LOG.1"
  log "rotated all.log (keeping $KEEP archives)"
}

run_once() {
  if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    log "container $CONTAINER not found, skipping"
    return 0
  fi
  log "stopping $CONTAINER"
  docker stop -t 120 "$CONTAINER" >/dev/null || log "stop failed (continuing)"
  rotate_logs || log "log rotation failed (continuing)"
  log "starting $CONTAINER"
  docker start "$CONTAINER" >/dev/null
}

case "${1:-loop}" in
  once) run_once; exit 0 ;;
esac

log "scheduled daily at $AT UTC for $CONTAINER"
while true; do
  if [ "$(date -u +%H:%M)" = "$AT" ]; then
    run_once || log "maintenance run failed"
    sleep 61   # step past the matching minute
  fi
  sleep 30
done
