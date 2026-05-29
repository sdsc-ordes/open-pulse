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


async def _sparql_query_count(
    client: httpx.AsyncClient,
    base: str,
    where: str,
    timeout: float = 8.0,
) -> int | None:
    """Cheap COUNT helper for the SPARQL store.

    Counts ``?s`` matching ``<where>`` across the default graph **and**
    every named graph (UNION + DISTINCT). Uploads stream into named
    graphs (e.g. ``2026-05/hybrid``) and the ``publish_to_default``
    mirror that copies them into the default graph routinely times out
    on large stores — querying only the default graph understates the
    real count by however much the mirror lags. The UNION + DISTINCT
    combination is the cheapest correct shape: it lets Oxigraph reuse
    the type-index for both branches, and DISTINCT collapses subjects
    that appear in multiple graphs back to one. Returns ``None`` on any
    failure (network, non-200, malformed body); callers treat that as
    "skip this column for this sample" and the chart renders a gap.
    """
    settings = get_settings()
    url = base.rstrip("/")
    if not url.endswith("/query"):
        url += "/query"
    query = (
        f"SELECT (COUNT(DISTINCT ?s) AS ?c) WHERE {{ "
        f"{{ {where} }} UNION {{ GRAPH ?g {{ {where} }} }} "
        f"}}"
    )
    auth: tuple[str, str] | None = None
    if settings.sparql_user and settings.sparql_password:
        auth = (settings.sparql_user, settings.sparql_password)
    try:
        r = await client.get(
            url,
            params={"query": query},
            headers={"Accept": "application/sparql-results+json"},
            auth=auth,
            timeout=timeout,
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


async def _sparql_named_graphs(
    client: httpx.AsyncClient, timeout: float = 8.0
) -> list[dict[str, object]] | None:
    """Enumerate named graphs in the SPARQL store with per-graph triple counts.

    Returns a list of ``{"uri": str, "triples": int}`` sorted by triple
    count descending, or ``None`` on any failure (the Overview/Databases
    UIs gracefully skip the snapshots panel when this returns ``None``).

    The query is more expensive than the per-class counts above because
    it has to enumerate every named graph — fine for the Overview's
    periodic refresh, not for sub-second hot paths.
    """
    settings = get_settings()
    base = settings.sparql_url
    url = base.rstrip("/")
    if not url.endswith("/query"):
        url += "/query"
    query = (
        "SELECT ?g (COUNT(*) AS ?n) "
        "WHERE { GRAPH ?g { ?s ?p ?o } } "
        "GROUP BY ?g ORDER BY DESC(?n)"
    )
    auth: tuple[str, str] | None = None
    if settings.sparql_user and settings.sparql_password:
        auth = (settings.sparql_user, settings.sparql_password)
    try:
        r = await client.get(
            url,
            params={"query": query},
            headers={"Accept": "application/sparql-results+json"},
            auth=auth,
            timeout=timeout,
        )
        if r.status_code != 200:
            return None
        b = r.json()
        binds = (b.get("results") or {}).get("bindings") or []
        out: list[dict[str, object]] = []
        for row in binds:
            uri = (row.get("g") or {}).get("value")
            count_raw = (row.get("n") or {}).get("value")
            if not uri or count_raw is None:
                continue
            try:
                out.append({"uri": uri, "triples": int(count_raw)})
            except ValueError:
                continue
        return out
    except (httpx.HTTPError, ValueError, KeyError):
        return None


async def _sparql_counts(client: httpx.AsyncClient) -> dict[str, int | None]:
    """Per-class counts the Overview's SPARQL chart consumes.

    Repos: ``schema:SoftwareSourceCode`` (existing semantics — keep
    backward compatibility with the marquee tile).
    Users: ``schema:Person`` (the GME emits one per crawled GitHub user).
    Orgs:  ``org:Organization`` from W3C's org ontology (the GME emits
    these for both GitHub orgs and ROR-resolved institutions; the
    Overview facet card uses the same predicate).

    Each probe is independent so a stale ontology import (e.g. orgs
    not yet present) just nulls that one series.
    """
    settings = get_settings()
    base = settings.sparql_url
    repos, users, orgs = await asyncio.gather(
        _sparql_query_count(
            client,
            base,
            "?s a <http://schema.org/SoftwareSourceCode>",
        ),
        _sparql_query_count(
            client,
            base,
            "?s a <http://schema.org/Person>",
        ),
        _sparql_query_count(
            client,
            base,
            "?s a <http://www.w3.org/ns/org#Organization>",
        ),
    )
    return {"repos": repos, "users": users, "orgs": orgs}


async def _neo4j_counts() -> dict[str, int | None]:
    """Node-label counts for the Overview's Neo4j chart.

    Returns total ``nodes`` / ``rels`` (kept for backward compatibility
    with the marquee + dashboard tiles) plus per-label ``repos``,
    ``users``, ``orgs`` for the time-series chart. One round-trip:
    ``MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n`` is fast
    enough on graphs we run (~200k nodes) and avoids three separate
    auth handshakes.
    """
    settings = get_settings()
    blank = {"nodes": None, "rels": None, "repos": None, "users": None, "orgs": None}
    try:
        from neo4j import GraphDatabase
    except ImportError:
        return blank

    # The local stack uses neo4j/replace-me unless overridden in
    # HUB_NEO4J_PASSWORD; the hub doesn't get NEO4J_AUTH by design (auth
    # is a hub-side concern). Fall back gracefully on auth failure.
    import os

    auth_default = os.environ.get("HUB_NEO4J_PASSWORD", "replace-me")
    try:
        driver = GraphDatabase.driver(settings.neo4j_url, auth=("neo4j", auth_default))
        try:
            with driver.session() as s:
                by_label = {
                    r["label"]: int(r["n"])
                    for r in s.run(
                        "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n"
                    )
                    if r["label"] is not None
                }
                nodes = sum(by_label.values()) or None
                row = s.run("MATCH ()-[r]->() RETURN count(r) AS rels").single()
                rels = int(row["rels"]) if row else None
        finally:
            driver.close()
        return {
            "nodes": nodes,
            "rels": rels,
            "repos": by_label.get("Repo"),
            "users": by_label.get("User"),
            "orgs": by_label.get("Org"),
        }
    except Exception:
        return blank


# OpenSearch indices the GrimoireLab pipeline writes into. Cardinality
# aggs over ``origin`` / ``author_name.keyword`` give us repo + user
# counts; orgs come from the first path segment of ``origin`` URLs
# (GitHub: ``https://github.com/{org}/{repo}``).
_OS_GIT_INDEX = "git_*_enriched"
_OS_GITHUB_INDEX = "github_*_enriched"


def _os_auth() -> tuple[str, str]:
    import os

    return (
        os.environ.get("HUB_OPENSEARCH_USERNAME", "admin"),
        os.environ.get("HUB_OPENSEARCH_PASSWORD", "admin"),
    )


def _os_verify() -> bool:
    import os

    return os.environ.get("HUB_OPENSEARCH_VERIFY_TLS", "true").lower() not in (
        "false",
        "0",
        "no",
    )


async def _opensearch_counts(client: httpx.AsyncClient) -> dict[str, int | None]:
    """Repos / users / orgs counts from the GrimoireLab enriched indices.

    Issues a single multi-agg DSL search (``size=0`` so no documents are
    pulled back, only the aggs) per index. The ``terms`` ``include`` regex
    on ``origin`` is dropped — we trust the cardinality figure as-is. Hub
    bypasses certificate verification when ``HUB_OPENSEARCH_VERIFY_TLS=false``
    so the local self-signed Compose deployment works without extra setup.
    """
    settings = get_settings()
    base = (settings.opensearch_url or "").rstrip("/")
    if not base:
        return {"repos": None, "users": None, "orgs": None}

    # ``author_uuid`` is GrimoireLab's per-author surrogate key — stable
    # across name / email changes, so it's a more honest "distinct user"
    # measure than ``author_name``. The ``.keyword`` subfield is *not*
    # mapped on the git_* indices in this deployment, hence the bare
    # field name (text type, but cardinality still works).
    body = {
        "size": 0,
        "aggs": {
            "repos": {"cardinality": {"field": "origin"}},
            "users": {"cardinality": {"field": "author_uuid"}},
        },
    }
    try:
        # httpx 0.27 takes ``verify=`` as a bool/path/SSL context. The
        # bool form is enough for "trust whatever's in the chain" off.
        r = await client.post(
            f"{base}/{_OS_GIT_INDEX}/_search",
            json=body,
            auth=_os_auth(),
            timeout=6.0,
        )
        if r.status_code != 200:
            return {"repos": None, "users": None, "orgs": None}
        aggs = r.json().get("aggregations") or {}
        # Don't mask a legitimate 0 with ``or None`` — a brand-new
        # deployment with no commits indexed yet should show 0, not "—".
        repos_v = (aggs.get("repos") or {}).get("value")
        users_v = (aggs.get("users") or {}).get("value")
        repos = int(repos_v) if repos_v is not None else None
        users = int(users_v) if users_v is not None else None
    except (httpx.HTTPError, ValueError, KeyError):
        return {"repos": None, "users": None, "orgs": None}

    # Orgs ≈ distinct first path segment of ``origin``. We use the same
    # ``git_*_enriched`` index because ``origin`` is aggregatable there
    # (the ``github_*_enriched`` indices in this deployment don't have
    # ``origin`` as a keyword/fielddata-enabled mapping). ``size`` caps
    # the terms agg well above any plausible org count in a single
    # deployment.
    orgs_body = {
        "size": 0,
        "aggs": {
            "by_origin": {
                "terms": {"field": "origin", "size": 10000},
            }
        },
    }
    try:
        r = await client.post(
            f"{base}/{_OS_GIT_INDEX}/_search",
            json=orgs_body,
            auth=_os_auth(),
            timeout=6.0,
        )
        orgs: int | None = None
        if r.status_code == 200:
            buckets = ((r.json().get("aggregations") or {}).get("by_origin") or {}).get(
                "buckets"
            ) or []
            # https://github.com/{org}/{repo} -> {org}
            orgs_set: set[str] = set()
            for b in buckets:
                key = (b.get("key") or "").strip()
                # Strip the github.com prefix; for non-GitHub origins
                # use the netloc-less first segment as the bucket.
                if "github.com/" in key:
                    tail = key.split("github.com/", 1)[1]
                    org = tail.split("/", 1)[0]
                    if org:
                        orgs_set.add(org)
            orgs = len(orgs_set)
        return {"repos": repos, "users": users, "orgs": orgs}
    except (httpx.HTTPError, ValueError, KeyError):
        return {"repos": repos, "users": users, "orgs": None}


# Per-provider DuckDB files. Each provider keeps its catalog in its
# own .duckdb under ``OPEN_PULSE_DATA_DIR/extractor/index/<name>/duckdb/``;
# the hub mounts that read-only at ``/data/`` so we can open them from
# the API process.
_DUCKDB_PROBES: tuple[tuple[str, str, str], ...] = (
    (
        "github_repos",
        "/data/extractor/index/github/duckdb/github.duckdb",
        "SELECT COUNT(*) FROM repos",
    ),
    (
        "zenodo_records",
        "/data/extractor/index/zenodo/duckdb/zenodo.duckdb",
        "SELECT COUNT(*) FROM records",
    ),
    (
        # HF has datasets + models + spaces tables; the panel shows a
        # single "huggingface" line, so we sum the three. Excluding
        # ``orgs`` and ``chunks`` because the user-facing meaning is
        # "published artifacts on HF" not "people / embedding chunks".
        "huggingface_items",
        "/data/extractor/index/huggingface/duckdb/huggingface.duckdb",
        "SELECT "
        "(SELECT COUNT(*) FROM datasets) + "
        "(SELECT COUNT(*) FROM models) + "
        "(SELECT COUNT(*) FROM spaces)",
    ),
)


def _duckdb_counts() -> dict[str, int | None]:
    """Row counts from the three provider DuckDBs the Hub overview tracks.

    Sync because DuckDB's Python connector is sync; runs off the asyncio
    event loop via ``asyncio.to_thread`` in :func:`_gather` so a slow
    backing file doesn't block the marquee.
    """
    try:
        import duckdb  # type: ignore[import-untyped]
    except ImportError:
        return {k: None for k, _, _ in _DUCKDB_PROBES}

    out: dict[str, int | None] = {}
    for name, path, query in _DUCKDB_PROBES:
        try:
            c = duckdb.connect(path, read_only=True)
            try:
                row = c.execute(query).fetchone()
                out[name] = int(row[0]) if row else None
            finally:
                c.close()
        except Exception:  # noqa: BLE001 — every backing is optional
            out[name] = None
    return out


async def _gather() -> dict[str, Any]:
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

    async with httpx.AsyncClient(verify=_os_verify()) as client:
        sparql, named_graphs, neo, opensearch, duck = await asyncio.gather(
            _sparql_counts(client),
            _sparql_named_graphs(client),
            _neo4j_counts(),
            _opensearch_counts(client),
            asyncio.to_thread(_duckdb_counts),
        )

    sparql_payload: dict[str, Any] = dict(sparql)
    if named_graphs is not None:
        sparql_payload["named_graphs"] = named_graphs
    return {
        "services": {
            "total": total,
            "running": running,
            "healthy": healthy,
            "uptime_max_seconds": longest_uptime,
            "uptime_max_human": _humanize(longest_uptime) if longest_uptime else "—",
        },
        "sparql": sparql_payload,
        "neo4j": neo,
        "opensearch": opensearch,
        "duckdb": duck,
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


# Columns added to ``metrics_history`` after the initial schema landed.
# Kept as a list so adding more later is a one-line append — each
# missing column triggers an additive ``ALTER TABLE ... ADD COLUMN``
# (NULL on old rows; the chart layer renders gaps cleanly).
_HISTORY_ADDED_COLUMNS: tuple[str, ...] = (
    "neo4j_repos INTEGER",
    "neo4j_users INTEGER",
    "neo4j_orgs INTEGER",
    "sparql_users INTEGER",
    "sparql_orgs INTEGER",
    "opensearch_repos INTEGER",
    "opensearch_users INTEGER",
    "opensearch_orgs INTEGER",
    "duckdb_github_repos INTEGER",
    "duckdb_zenodo_records INTEGER",
    "duckdb_huggingface_items INTEGER",
)


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
    # Idempotent column add for deployments that pre-date the per-backend
    # chart work. ``PRAGMA table_info`` is the cheapest way to check.
    existing = {row[1] for row in conn.execute("PRAGMA table_info(metrics_history)")}
    for col_def in _HISTORY_ADDED_COLUMNS:
        col_name = col_def.split()[0]
        if col_name not in existing:
            conn.execute(f"ALTER TABLE metrics_history ADD COLUMN {col_def}")
    conn.commit()
    return conn


def _persist_sample(sample: dict[str, Any]) -> None:
    """Write one stats sample to the history table; prune old rows."""
    services = sample.get("services") or {}
    sparql = sample.get("sparql") or {}
    neo4j = sample.get("neo4j") or {}
    opensearch = sample.get("opensearch") or {}
    duckdb_ = sample.get("duckdb") or {}
    conn = _history_db()
    try:
        conn.execute(
            "INSERT INTO metrics_history("
            "  services_total, services_running, services_healthy,"
            "  uptime_max_seconds,"
            "  sparql_repos, sparql_users, sparql_orgs,"
            "  neo4j_nodes, neo4j_rels,"
            "  neo4j_repos, neo4j_users, neo4j_orgs,"
            "  opensearch_repos, opensearch_users, opensearch_orgs,"
            "  duckdb_github_repos, duckdb_zenodo_records, duckdb_huggingface_items"
            ") VALUES (?, ?, ?,  ?,  ?, ?, ?,  ?, ?,  ?, ?, ?,  ?, ?, ?,  ?, ?, ?)",
            (
                services.get("total"),
                services.get("running"),
                services.get("healthy"),
                services.get("uptime_max_seconds"),
                sparql.get("repos"),
                sparql.get("users"),
                sparql.get("orgs"),
                neo4j.get("nodes"),
                neo4j.get("rels"),
                neo4j.get("repos"),
                neo4j.get("users"),
                neo4j.get("orgs"),
                opensearch.get("repos"),
                opensearch.get("users"),
                opensearch.get("orgs"),
                duckdb_.get("github_repos"),
                duckdb_.get("zenodo_records"),
                duckdb_.get("huggingface_items"),
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

    # Kept verbose rather than ``SELECT *`` so the response shape stays
    # stable when new columns get added (clients depend on this exact
    # set of keys; new columns appear additively).
    _HISTORY_SELECT = (
        "SELECT ts, services_total, services_running, services_healthy,"
        "       uptime_max_seconds,"
        "       sparql_repos, sparql_users, sparql_orgs,"
        "       neo4j_nodes, neo4j_rels,"
        "       neo4j_repos, neo4j_users, neo4j_orgs,"
        "       opensearch_repos, opensearch_users, opensearch_orgs,"
        "       duckdb_github_repos, duckdb_zenodo_records, duckdb_huggingface_items"
        "  FROM metrics_history"
    )
    conn = _history_db()
    try:
        if use_custom:
            rows = conn.execute(
                _HISTORY_SELECT + " WHERE ts BETWEEN ? AND ? ORDER BY ts ASC",
                (start, end),
            ).fetchall()
        else:
            cutoff = _RANGE_MAP[range_]
            rows = conn.execute(
                _HISTORY_SELECT + " WHERE ts > datetime('now', ?) ORDER BY ts ASC",
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
        "sparql_users",
        "sparql_orgs",
        "neo4j_nodes",
        "neo4j_rels",
        "neo4j_repos",
        "neo4j_users",
        "neo4j_orgs",
        "opensearch_repos",
        "opensearch_users",
        "opensearch_orgs",
        "duckdb_github_repos",
        "duckdb_zenodo_records",
        "duckdb_huggingface_items",
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
