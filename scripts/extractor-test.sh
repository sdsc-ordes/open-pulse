#!/usr/bin/env bash
#
# extractor-test.sh — send one /v2/extract request to git-metadata-extractor
# and print the full response. Useful for confirming the extractor is healthy
# without re-running a full quest pipeline.
#
# Usage:
#   sudo bash scripts/extractor-test.sh                              # defaults to sdsc-ordes/demo-biomedit-workflow
#   sudo bash scripts/extractor-test.sh <owner> <repo>               # custom repo
#   sudo bash scripts/extractor-test.sh sdsc-ordes open-pulse        # example
#
# Exit code: 0 on HTTP 200, 1 otherwise.

set -uo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo."; exit 1; }

OWNER=${1:-sdsc-ordes}
REPO=${2:-demo-biomedit-workflow}
CONTAINER=${EXTRACTOR_CONTAINER:-git-metadata-extractor}

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "Container '$CONTAINER' is not running."
  exit 1
fi

echo "POST http://localhost:1234/v2/extract  (inside $CONTAINER)"
echo "  owner=$OWNER  repo=$REPO"
echo

# Run the request inside the extractor container so we don't depend on
# the host having Python or network access to the compose-internal port.
docker exec -i -e OWNER="$OWNER" -e REPO="$REPO" "$CONTAINER" python -u <<'PY'
import json, os, time, urllib.error, urllib.request, sys

owner = os.environ["OWNER"]
repo  = os.environ["REPO"]
data  = json.dumps({"owner": owner, "repo": repo}).encode()
req   = urllib.request.Request(
    "http://localhost:1234/v2/extract",
    data=data, headers={"Content-Type": "application/json"}, method="POST",
)
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
        dt = time.time() - t0
        print(f"status : {r.status}  ({dt:.2f}s)")
        # Try pretty-printing JSON; fall back to raw text.
        try:
            print("body   :", json.dumps(json.loads(body), indent=2)[:2000])
        except Exception:
            print("body   :", body[:1500].decode(errors="replace"))
        sys.exit(0)
except urllib.error.HTTPError as e:
    body = e.read()
    dt = time.time() - t0
    print(f"status : {e.code}  ({dt:.2f}s)")
    try:
        print("body   :", json.dumps(json.loads(body), indent=2)[:2000])
    except Exception:
        print("body   :", body[:1500].decode(errors="replace"))
    sys.exit(1)
except Exception as e:
    print(f"err    : {e!r}")
    sys.exit(1)
PY
status=$?

echo
echo "== Recent extractor log tail (errors + traceback if any) =="
docker logs --tail 40 "$CONTAINER" 2>&1 \
  | grep -E 'ERROR|Traceback|Exception|PermissionError|500 Internal|^[A-Z][a-zA-Z]+Error|/v2/extract' \
  | tail -25

exit $status
