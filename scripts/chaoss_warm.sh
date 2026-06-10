#!/usr/bin/env bash
# Warm the hub's CHAOSS per-project metric cache so UI clicks are instant.
#
# Lists every GrimoireLab project and recomputes its full metric set with
# ?refresh=true (at the hub's default/all-time window), populating the
# in-process _PROJECT_CACHE. Intended to run weekly from cron — the cache TTL
# (CHAOSS_PROJECT_CACHE_TTL_S, default 8 days) outlasts the week so users always
# hit a warm cache. Heavy compute happens here, off-peak, not on the click.
#
# Env overrides:
#   CHAOSS_WARM_BASE   base URL of the hub        (default http://localhost:7507)
#   CHAOSS_WARM_AUTH   HTTP Basic <user>:<pass>   (default dev:read-me-only — reader)
#   CHAOSS_WARM_TIMEOUT  per-project curl timeout in seconds (default 600)
set -uo pipefail

BASE="${CHAOSS_WARM_BASE:-http://localhost:7507}"
AUTH="${CHAOSS_WARM_AUTH:-dev:read-me-only}"
TIMEOUT="${CHAOSS_WARM_TIMEOUT:-600}"
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

echo "$(stamp) [chaoss-warm] start — $BASE"

projects=$(curl -fsSL --max-time 60 -u "$AUTH" \
  "$BASE/api/v1/metrics/chaoss/projects" \
  | python3 -c 'import sys,json; [print(p["project"]) for p in json.load(sys.stdin).get("projects",[])]') || {
    echo "$(stamp) [chaoss-warm] ERROR: could not list projects (hub down or auth?)"; exit 1; }

ok=0; fail=0
for p in $projects; do
  code=$(curl -fsSL -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" -u "$AUTH" \
    "$BASE/api/v1/metrics/chaoss/projects/$p/metrics?refresh=true" 2>/dev/null) || code="ERR"
  if [ "$code" = "200" ]; then ok=$((ok+1)); else fail=$((fail+1)); fi
  echo "$(stamp) [chaoss-warm] $p -> $code"
done

echo "$(stamp) [chaoss-warm] done — warmed=$ok failed=$fail of $(echo "$projects" | wc -w)"
