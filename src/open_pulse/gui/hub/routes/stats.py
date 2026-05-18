"""Aggregated stats for the marquee + dashboard tiles + overview charts.

Cheap, cached for a few seconds — the marquee polls every ~10s. A
background sampler writes one row to ``data/hub/app.db ::
metrics_history`` per minute, which the Overview page reads to draw
its time-series charts.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query

from ..auth import get_settings, require_auth
from ..docker_client import list_services

router = APIRouter(prefix="/api/stats", tags=["stats"])
log = logging.getLogger(__name__)

_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
_TTL = 6.0  # seconds

_HISTORY_INTERVAL = 60.0  # seconds between samples
_HISTORY_RETENTION_DAYS = 30  # prune older rows on each insert


def _uptime_seconds(started_at_iso: str | None) -> int | None:
    if not started_at_iso:
        return None
    try:
        # Docker timestamps look like "2026-05-04T01:00:06.254303707Z"
        s = started_at_iso.replace("Z", "+00:00")
        # Trim trailing-fractional second precision past 6 digits (python isoformat limit)
        if "." in s:
            head, _, tail = s.partition(".")
            if "+" in tail or "-" in tail[1:]:  # tz follows
                # split fractional and tz
                idx = max(tail.find("+"), tail.find("-", 1))
                frac, tz = tail[:idx], tail[idx:]
                tail = frac[:6] + tz
            else:
                tail = tail[:6]
            s = f"{head}.{tail}"
        dt = datetime.fromisoformat(s)
        return max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
    except (ValueError, TypeError):
        return None


def _humanize(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86_400:
        h, m = divmod(seconds, 3600)
        return f"{h}h{m // 60:02d}m"
    d, rem = divmod(seconds, 86_400)
    h = rem // 3600
    return f"{d}d{h:02d}h"


async def _sparql_count(client: httpx.AsyncClient, base: str) -> int | None:
    """Repo count via COUNT(?repo) — cheap on Oxigraph."""
    url = base.rstrip("/")
    if not url.endswith("/query"):
        url += "/query"
    query = (
        "PREFIX schema: <http://schema.org/> "
        "SELECT (COUNT(?r) AS ?c) WHERE { ?r a schema:SoftwareSourceCode }"
    )
    try:
        r = await client.get(
            url,
            params={"query": query},
            headers={"Accept": "application/sparql-results+json"},
            timeout=4.0,
        )
        if r.status_code != 200:
            return None
        b = r.json()
        binds = (b.get("results") or {}).get("bindings") or []
        if not binds:
            return 0
        return int(binds[0]["c"]["value"])
    except (httpx.HTTPError, ValueError, KeyError):
        return None


async def _neo4j_counts() -> dict[str, int | None]:
    """Two-row probe: total nodes + total relationships via the Neo4j driver."""
    settings = get_settings()
    try:
        from neo4j import GraphDatabase
    except ImportError:
        return {"nodes": None, "rels": None}

    # Reasonable default — the local stack uses neo4j/replace-me unless
    # overridden in NEO4J_AUTH; we don't have that secret in the hub by
    # design (auth is a hub-side concern), so we attempt with the conventional
    # dev password and fall back gracefully on auth failure.
    import os

    auth_default = os.environ.get("HUB_NEO4J_PASSWORD", "replace-me")
    try:
        driver = GraphDatabase.driver(settings.neo4j_url, auth=("neo4j", auth_default))
        try:
            with driver.session() as s:
                row = s.run("MATCH (n) RETURN count(n) AS nodes").single()
                nodes = int(row["nodes"]) if row else None
                row = s.run("MATCH ()-[r]->() RETURN count(r) AS rels").single()
                rels = int(row["rels"]) if row else None
        finally:
            driver.close()
        return {"nodes": nodes, "rels": rels}
    except Exception:
        return {"nodes": None, "rels": None}


async def _gather() -> dict[str, Any]:
    settings = get_settings()
    services = list_services()
    running = sum(1 for s in services if s["status"] == "running")
    healthy = sum(
        1
        for s in services
        if s["health"] == "healthy"
        or (s["health"] is None and s["status"] == "running")
    )
    total = len(services)
    longest_uptime = max(
        (_uptime_seconds(s.get("started_at")) or 0 for s in services),
        default=0,
    )

    async with httpx.AsyncClient() as client:
        sparql_repos, neo = await asyncio.gather(
            _sparql_count(client, settings.sparql_url),
            _neo4j_counts(),
        )

    return {
        "services": {
            "total": total,
            "running": running,
            "healthy": healthy,
            "uptime_max_seconds": longest_uptime,
            "uptime_max_human": _humanize(longest_uptime) if longest_uptime else "—",
        },
        "sparql": {"repos": sparql_repos},
        "neo4j": neo,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


@router.get("/", dependencies=[Depends(require_auth)])
async def get_stats() -> dict[str, Any]:
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["at"] < _TTL:
        return _CACHE["data"]
    data = await _gather()
    _CACHE["data"] = data
    _CACHE["at"] = now
    return data


# ── Time-series history ────────────────────────────────────────────────────


def _history_db() -> sqlite3.Connection:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.data_dir / "app.db")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS metrics_history (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            services_total      INTEGER,
            services_running    INTEGER,
            services_healthy    INTEGER,
            uptime_max_seconds  INTEGER,
            sparql_repos        INTEGER,
            neo4j_nodes         INTEGER,
            neo4j_rels          INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_metrics_history_ts ON metrics_history(ts);
        """
    )
    return conn


def _persist_sample(sample: dict[str, Any]) -> None:
    """Write one stats sample to the history table; prune old rows."""
    services = sample.get("services") or {}
    sparql = sample.get("sparql") or {}
    neo4j = sample.get("neo4j") or {}
    conn = _history_db()
    try:
        conn.execute(
            "INSERT INTO metrics_history(\n"
            "  services_total, services_running, services_healthy,\n"
            "  uptime_max_seconds,\n"
            "  sparql_repos, neo4j_nodes, neo4j_rels\n"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                services.get("total"),
                services.get("running"),
                services.get("healthy"),
                services.get("uptime_max_seconds"),
                sparql.get("repos"),
                neo4j.get("nodes"),
                neo4j.get("rels"),
            ),
        )
        conn.execute(
            "DELETE FROM metrics_history WHERE ts < datetime('now', ?)",
            (f"-{_HISTORY_RETENTION_DAYS} days",),
        )
        conn.commit()
    finally:
        conn.close()


async def metrics_history_loop() -> None:
    """Background task: sample stats every minute and persist them.

    Started from the FastAPI lifespan hook in ``main.py``. Runs forever
    until the app shuts down (``CancelledError``). Errors are logged but
    never fatal — a transient SPARQL/Neo4j outage shouldn't kill the loop.
    """
    log.info("metrics_history_loop: starting (interval=%ss)", _HISTORY_INTERVAL)
    # First sample after a short delay so other services have a chance to
    # come up; otherwise the very first row often shows zero counts.
    try:
        await asyncio.sleep(15)
    except asyncio.CancelledError:
        return
    while True:
        try:
            sample = await _gather()
            _persist_sample(sample)
        except asyncio.CancelledError:
            log.info("metrics_history_loop: cancelled")
            raise
        except Exception:  # noqa: BLE001 — never let the loop die
            log.exception("metrics_history_loop: sample failed")
        try:
            await asyncio.sleep(_HISTORY_INTERVAL)
        except asyncio.CancelledError:
            return


_RANGE_MAP = {
    "1h": "-1 hours",
    "6h": "-6 hours",
    "24h": "-1 days",
    "7d": "-7 days",
    "30d": "-30 days",
}


@router.get("/history", dependencies=[Depends(require_auth)])
def history(
    range_: str = Query(
        "6h",
        alias="range",
        description="Window size: 1h, 6h, 24h, 7d, 30d, or 'custom' "
        "(in which case ``start`` + ``end`` are required).",
    ),
    bucket_seconds: int = Query(
        0, description="Down-sample to one row per N seconds (0 = no bucketing)."
    ),
    start: str | None = Query(
        None,
        description="ISO-8601 lower bound (UTC). Required when range='custom'.",
    ),
    end: str | None = Query(
        None,
        description="ISO-8601 upper bound (UTC). Required when range='custom'.",
    ),
) -> dict[str, Any]:
    """Return the metric series, in chronological order.

    Each row carries the same shape as a snapshot of ``/api/stats/`` but
    flattened into the columns the chart UI consumes directly. When
    ``range`` is ``custom`` the rows are bounded by ``start`` / ``end``
    instead of the preset windows.
    """
    use_custom = range_ == "custom" and start and end
    if not use_custom and range_ not in _RANGE_MAP:
        range_ = "6h"

    conn = _history_db()
    try:
        if use_custom:
            rows = conn.execute(
                "SELECT ts, services_total, services_running, services_healthy,\n"
                "       uptime_max_seconds, sparql_repos, neo4j_nodes, neo4j_rels\n"
                "  FROM metrics_history\n"
                " WHERE ts BETWEEN ? AND ?\n"
                " ORDER BY ts ASC",
                (start, end),
            ).fetchall()
        else:
            cutoff = _RANGE_MAP[range_]
            rows = conn.execute(
                "SELECT ts, services_total, services_running, services_healthy,\n"
                "       uptime_max_seconds, sparql_repos, neo4j_nodes, neo4j_rels\n"
                "  FROM metrics_history\n"
                " WHERE ts > datetime('now', ?)\n"
                " ORDER BY ts ASC",
                (cutoff,),
            ).fetchall()
    finally:
        conn.close()

    keys = (
        "ts",
        "services_total",
        "services_running",
        "services_healthy",
        "uptime_max_seconds",
        "sparql_repos",
        "neo4j_nodes",
        "neo4j_rels",
    )
    samples = [dict(zip(keys, r)) for r in rows]

    # Optional bucketing: collapse to one point per bucket_seconds window
    # using the last sample in each bucket. Keeps charts crisp when range
    # is wide (e.g. 30d at 1-minute granularity = 43k points).
    if bucket_seconds and len(samples) > 1:
        buckets: dict[int, dict[str, Any]] = {}
        for s in samples:
            try:
                ts = datetime.fromisoformat(s["ts"].replace("Z", "+00:00"))
            except ValueError:
                continue
            slot = int(ts.timestamp() // bucket_seconds)
            buckets[slot] = s
        samples = [buckets[k] for k in sorted(buckets.keys())]

    return {"range": range_, "count": len(samples), "samples": samples}
