"""Pre-computed top-N statistics, cached in DuckDB.

Hits the data plane (Neo4j + Qdrant + OpenSearch) periodically and
stores the resulting leaderboards so the home page can show
"top organizations / contributors / languages / countries" without
re-running the heavy queries on every render.

DuckDB is used because the hub already ships with it (the Databases
console writes to ``data/hub/scratch.duckdb``); a tiny additional
file ``data/hub/hub_stats.duckdb`` holds the cache. Each entry has
a TTL — :func:`cached_compute` recomputes when stale.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

_STATS_TTL = 3600.0  # 1 hour
_DUCKDB_FILE = "hub_stats.duckdb"


def _conn(data_dir: Path):
    """Open (or create) the stats DuckDB file. The connection is
    short-lived — the caller closes it; we never hold one open.
    """
    import duckdb  # local import; only the [hub] extra installs it

    data_dir.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(data_dir / _DUCKDB_FILE))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hub_stats (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at DOUBLE NOT NULL
        )
        """
    )
    return conn


def get_cached(data_dir: Path, key: str) -> Any | None:
    """Return the cached JSON value or None when stale / missing."""
    try:
        conn = _conn(data_dir)
    except Exception as exc:  # noqa: BLE001
        log.info("duckdb open failed: %s", exc)
        return None
    try:
        row = conn.execute(
            "SELECT value, updated_at FROM hub_stats WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return None
        value_json, updated_at = row
        if time.time() - float(updated_at) > _STATS_TTL:
            return None
        return json.loads(value_json)
    except Exception as exc:  # noqa: BLE001
        log.info("duckdb read %s failed: %s", key, exc)
        return None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def put_cached(data_dir: Path, key: str, value: Any) -> None:
    try:
        conn = _conn(data_dir)
    except Exception as exc:  # noqa: BLE001
        log.info("duckdb open failed: %s", exc)
        return
    try:
        conn.execute(
            """
            INSERT INTO hub_stats(key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, json.dumps(value), time.time()),
        )
    except Exception as exc:  # noqa: BLE001
        log.info("duckdb write %s failed: %s", key, exc)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def cached_compute(data_dir: Path, key: str, compute_fn: Callable[[], Any]) -> Any:
    """Memoised computation. First hit triggers ``compute_fn``; the
    result is JSON-cached in DuckDB with a 1-hour TTL."""
    v = get_cached(data_dir, key)
    if v is not None:
        return v
    try:
        v = compute_fn()
    except Exception as exc:  # noqa: BLE001
        log.warning("stats compute %s failed: %s", key, exc)
        return None
    if v is None:
        return None
    put_cached(data_dir, key, v)
    return v


# ── Compute functions ────────────────────────────────────────────────────


def compute_top_github_orgs(limit: int = 10) -> list[dict[str, Any]]:
    """Orgs ranked by how many Repos they own (Neo4j OWNS edges)."""
    from .stores import neo4j_run

    cypher = (
        "MATCH (o:Org)-[:OWNS]->(r:Repo) "
        "RETURN o.login AS login, "
        "       coalesce(o.name, '') AS name, "
        "       count(r) AS n_repos "
        "ORDER BY n_repos DESC, login "
        "LIMIT $limit"
    )
    rows = neo4j_run(cypher, {"limit": limit})
    return [
        {
            "login": r["login"],
            "name": (r.get("name") or "").strip(),
            "count": int(r["n_repos"]),
            "hub_url": f"/hub/github.com/{r['login']}",
        }
        for r in rows
        if r.get("login")
    ]


def compute_top_contributors(limit: int = 10) -> list[dict[str, Any]]:
    """Users ranked by repo-count they CONTRIBUTE_TO."""
    from .stores import neo4j_run

    cypher = (
        "MATCH (u:User)-[:CONTRIBUTES_TO]->(r:Repo) "
        "RETURN u.login AS login, "
        "       coalesce(u.name, '') AS name, "
        "       count(DISTINCT r) AS n_repos "
        "ORDER BY n_repos DESC, login "
        "LIMIT $limit"
    )
    rows = neo4j_run(cypher, {"limit": limit})
    return [
        {
            "login": r["login"],
            "name": (r.get("name") or "").strip(),
            "count": int(r["n_repos"]),
            "hub_url": f"/hub/github.com/{r['login']}",
        }
        for r in rows
        if r.get("login")
    ]


def compute_top_repos_by_contributors(limit: int = 10) -> list[dict[str, Any]]:
    """Repos with the most distinct contributors (community signal)."""
    from .stores import neo4j_run

    cypher = (
        "MATCH (r:Repo)<-[:CONTRIBUTES_TO]-(u:User) "
        "WITH r, count(DISTINCT u) AS n_contributors "
        "WHERE n_contributors > 1 "
        "RETURN r.full_name AS slug, n_contributors "
        "ORDER BY n_contributors DESC, slug "
        "LIMIT $limit"
    )
    rows = neo4j_run(cypher, {"limit": limit})
    return [
        {
            "slug": r["slug"],
            "count": int(r["n_contributors"]),
            "hub_url": f"/hub/github.com/{r['slug']}",
        }
        for r in rows
        if r.get("slug")
    ]


def _qdrant_facet_scroll(collection: str, field: str, *, sample: int = 5000) -> Counter:
    """Sample up to ``sample`` points from ``collection`` and tally
    the values appearing in ``field``. Cheap aggregator that doesn't
    need full collection scans."""
    from . import qdrant
    import httpx

    pair = qdrant._client()
    if pair is None:
        return Counter()
    _, headers = pair
    settings = qdrant.get_settings()
    url = f"{settings.qdrant_url.rstrip('/')}/collections/{collection}/points/scroll"

    counts: Counter = Counter()
    next_offset: Any = None
    fetched = 0
    page_size = 500
    while fetched < sample:
        body = {
            "limit": min(page_size, sample - fetched),
            "with_payload": [field],
            "with_vector": False,
        }
        if next_offset is not None:
            body["offset"] = next_offset
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.post(url, json=body, headers=headers)
        except httpx.HTTPError:
            break
        if r.status_code != 200:
            break
        try:
            payload = r.json()
        except ValueError:
            break
        result = payload.get("result") or {}
        points = result.get("points") or []
        if not points:
            break
        for p in points:
            v = (p.get("payload") or {}).get(field)
            if (
                isinstance(v, str)
                and v.strip()
                and v.strip()
                not in (
                    "NOASSERTION",
                    "None",
                    "n/a",
                )
            ):
                counts[v.strip()] += 1
            elif isinstance(v, list):
                for x in v:
                    if isinstance(x, str) and x.strip():
                        counts[x.strip()] += 1
        fetched += len(points)
        next_offset = result.get("next_page_offset")
        if not next_offset:
            break
    return counts


def compute_top_languages(limit: int = 10) -> list[dict[str, Any]]:
    """Top primary_language values across ``github_repos`` (sample)."""
    counts = _qdrant_facet_scroll("github_repos", "primary_language", sample=5000)
    return [{"label": label, "count": n} for label, n in counts.most_common(limit)]


def compute_top_licenses(limit: int = 10) -> list[dict[str, Any]]:
    counts = _qdrant_facet_scroll("github_repos", "license_spdx", sample=5000)
    return [{"label": label, "count": n} for label, n in counts.most_common(limit)]


def compute_top_countries(limit: int = 10) -> list[dict[str, Any]]:
    """Top country_code on ROR (worldwide bucket — the global mirror)."""
    counts = _qdrant_facet_scroll("ror_worldwide", "country_code", sample=5000)
    return [{"label": label, "count": n} for label, n in counts.most_common(limit)]


def compute_top_publication_years(limit: int = 10) -> list[dict[str, Any]]:
    counts = _qdrant_facet_scroll("infoscience_articles", "year", sample=5000)
    return [{"label": str(label), "count": n} for label, n in counts.most_common(limit)]


# ── Public dispatch table ────────────────────────────────────────────────


_TOP_REGISTRY: dict[str, tuple[str, str, Callable[[int], list[dict[str, Any]]]]] = {
    # key: (label, description, fn)
    "github_orgs": (
        "Top GitHub organizations",
        "Most repos owned in the crawl graph.",
        compute_top_github_orgs,
    ),
    "contributors": (
        "Top GitHub contributors",
        "Researchers active across the most repos.",
        compute_top_contributors,
    ),
    "repos_by_contributors": (
        "Repos with most contributors",
        "GitHub repos ranked by distinct community.",
        compute_top_repos_by_contributors,
    ),
    "languages": (
        "Top programming languages",
        "Sampled from github_repos.",
        compute_top_languages,
    ),
    "licenses": (
        "Top licenses",
        "Sampled from github_repos.license_spdx.",
        compute_top_licenses,
    ),
    "countries": (
        "Top ROR countries",
        "Sampled from ror_worldwide.country_code.",
        compute_top_countries,
    ),
    "publication_years": (
        "Top publication years",
        "Sampled from infoscience_articles.year.",
        compute_top_publication_years,
    ),
}


def fetch_top(data_dir: Path, topic: str, *, limit: int = 10) -> dict[str, Any] | None:
    entry = _TOP_REGISTRY.get(topic)
    if entry is None:
        return None
    label, description, fn = entry
    key = f"top:{topic}:{limit}"
    items = cached_compute(data_dir, key, lambda: fn(limit))
    if items is None:
        return None
    return {
        "topic": topic,
        "label": label,
        "description": description,
        # Key is ``rows`` (not ``items``) because Jinja's getattr
        # would shadow it with ``dict.items`` otherwise.
        "rows": items,
    }


def topics() -> list[str]:
    return list(_TOP_REGISTRY.keys())
