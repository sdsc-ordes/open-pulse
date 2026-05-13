"""DuckDB SQL console + saved queries (SQLite for the saved-queries table).

The DuckDB console is the analytics surface — it can read CSV/Parquet/JSON
straight from the shared `data/` mount and remote-attach SQLite. We persist
saved queries and recent history in a tiny SQLite app DB next to the
DuckDB scratch file. SPARQL, Cypher (Neo4j), and OpenSearch consoles share
the same surface — see ``../db_examples.py`` for the welcome-mat snippets.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import duckdb
import httpx
from fastapi import APIRouter, Body, Depends, HTTPException

from ..auth import get_settings, require_auth
from ..db_examples import by_engine as db_examples_by_engine

router = APIRouter(prefix="/api/databases", tags=["databases"])


def _ensure_dirs() -> tuple[Path, Path]:
    """Return (sqlite_path, duckdb_path), making sure the parent dir exists."""
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings.data_dir / "app.db", settings.data_dir / "scratch.duckdb"


def _sqlite() -> sqlite3.Connection:
    sqlite_path, _ = _ensure_dirs()
    conn = sqlite3.connect(sqlite_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS saved_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            engine TEXT NOT NULL,                  -- duckdb | sparql | cypher
            query TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS query_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            engine TEXT NOT NULL,
            query TEXT NOT NULL,
            ran_at TEXT NOT NULL DEFAULT (datetime('now')),
            row_count INTEGER,
            error TEXT
        );
        """
    )
    return conn


@router.post("/duckdb/query", dependencies=[Depends(require_auth)])
def duckdb_query(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Run an arbitrary read-only DuckDB query against the scratch DB.

    The hub mounts `data/` at /data, so users can do e.g.::

        SELECT * FROM read_csv_auto('/data/some-export.csv') LIMIT 100

    or attach SQLite::

        ATTACH '/data/hub/app.db' AS app (TYPE SQLITE);
        SELECT * FROM app.saved_queries;
    """
    query = (payload.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    limit = int(payload.get("limit") or 1000)
    if limit <= 0 or limit > 10_000:
        raise HTTPException(status_code=400, detail="limit must be in 1..10000")

    _, duckdb_path = _ensure_dirs()
    conn = duckdb.connect(str(duckdb_path), read_only=False)
    try:
        try:
            cur = conn.execute(query)
        except duckdb.Error as e:
            _log_history("duckdb", query, row_count=None, error=str(e))
            raise HTTPException(status_code=400, detail=str(e)) from e
        # If the statement doesn't return a result set (CREATE TABLE etc.),
        # description is None.
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(limit) if columns else []
    finally:
        conn.close()

    _log_history("duckdb", query, row_count=len(rows), error=None)
    return {
        "columns": columns,
        "rows": [list(r) for r in rows],
        "row_count": len(rows),
        "limit": limit,
        "truncated": len(rows) >= limit,
    }


def _log_history(
    engine: str, query: str, *, row_count: int | None, error: str | None
) -> None:
    conn = _sqlite()
    try:
        conn.execute(
            "INSERT INTO query_history(engine, query, row_count, error) VALUES (?, ?, ?, ?)",
            (engine, query, row_count, error),
        )
        conn.commit()
    finally:
        conn.close()


@router.get("/saved", dependencies=[Depends(require_auth)])
def list_saved() -> dict[str, Any]:
    conn = _sqlite()
    try:
        rows = conn.execute(
            "SELECT id, name, engine, query, created_at FROM saved_queries ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    return {
        "saved": [
            {
                "id": r[0],
                "name": r[1],
                "engine": r[2],
                "query": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]
    }


@router.post("/saved", dependencies=[Depends(require_auth)])
def save_query(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    name = (payload.get("name") or "").strip()
    engine = (payload.get("engine") or "").strip()
    query = (payload.get("query") or "").strip()
    if not name or not engine or not query:
        raise HTTPException(
            status_code=400, detail="name, engine, and query are required"
        )
    if engine not in {"duckdb", "sparql", "cypher"}:
        raise HTTPException(
            status_code=400, detail="engine must be duckdb|sparql|cypher"
        )
    conn = _sqlite()
    try:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO saved_queries(name, engine, query) VALUES (?, ?, ?)",
                (name, engine, query),
            )
            conn.commit()
        except sqlite3.Error as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    finally:
        conn.close()
    return {"ok": True, "name": name, "engine": engine}


@router.post("/sparql/query", dependencies=[Depends(require_auth)])
async def sparql_query(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Run a SELECT against the SPARQL store and return raw bindings."""
    settings = get_settings()
    endpoint = (payload.get("endpoint") or settings.sparql_url).rstrip("/")
    if not endpoint.endswith("/query"):
        endpoint += "/query"
    query = (payload.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    # Body wins; fall back to server-side SPARQL_AUTH (parsed in settings).
    user = (payload.get("auth_user") or "").strip() or settings.sparql_user
    pw = payload.get("auth_password")
    if pw is None or pw == "":
        pw = settings.sparql_password
    auth = (user, pw) if user and pw else None

    headers = {"Accept": "application/sparql-results+json"}
    async with httpx.AsyncClient(timeout=30.0) as c:
        resp = await c.get(
            endpoint, params={"query": query}, headers=headers, auth=auth
        )
    if resp.status_code != 200:
        _log_history(
            "sparql",
            query,
            row_count=None,
            error=f"HTTP {resp.status_code}: {resp.text[:200]}",
        )
        raise HTTPException(
            status_code=502,
            detail=f"SPARQL endpoint returned HTTP {resp.status_code}: {resp.text[:200]}",
        )
    body = resp.json()
    head = (body.get("head") or {}).get("vars") or []
    bindings = (body.get("results") or {}).get("bindings") or []
    rows: list[list[Any]] = []
    for b in bindings:
        rows.append([(b.get(v) or {}).get("value") for v in head])
    _log_history("sparql", query, row_count=len(rows), error=None)
    return {"columns": head, "rows": rows, "row_count": len(rows)}


@router.post("/cypher/query", dependencies=[Depends(require_auth)])
def cypher_query(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Run a Cypher statement against Neo4j and return result rows."""
    settings = get_settings()
    query = (payload.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    # Body wins; fall back to NEO4J_AUTH from settings (parsed at startup).
    user = (payload.get("auth_user") or "").strip() or settings.neo4j_user or "neo4j"
    pw = payload.get("auth_password")
    if not pw:
        pw = settings.neo4j_password
    if not pw:
        raise HTTPException(
            status_code=400,
            detail="Neo4j password not set — provide it in the auth inputs or set NEO4J_AUTH in .env.",
        )
    try:
        from neo4j import GraphDatabase
    except ImportError as e:  # pragma: no cover - hub image bundles it
        raise HTTPException(status_code=500, detail=f"neo4j driver missing: {e}") from e

    try:
        driver = GraphDatabase.driver(settings.neo4j_url, auth=(user, pw))
        with driver.session() as s:
            result = s.run(query)
            keys = list(result.keys())
            rows = [[record.get(k) for k in keys] for record in result]
        driver.close()
    except Exception as e:
        _log_history("cypher", query, row_count=None, error=str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e

    _log_history("cypher", query, row_count=len(rows), error=None)
    return {
        "columns": keys,
        "rows": [[_normalize(c) for c in row] for row in rows],
        "row_count": len(rows),
    }


def _normalize(value: Any) -> Any:
    """Make Cypher values JSON-friendly: nodes/relationships → dicts, etc."""
    if hasattr(value, "items"):
        return dict(value)
    if hasattr(value, "_properties"):
        return dict(value._properties)
    return value


@router.get("/history", dependencies=[Depends(require_auth)])
def history(limit: int = 50) -> dict[str, Any]:
    if limit <= 0 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be in 1..500")
    conn = _sqlite()
    try:
        rows = conn.execute(
            "SELECT id, engine, query, ran_at, row_count, error "
            "FROM query_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return {
        "history": [
            {
                "id": r[0],
                "engine": r[1],
                "query": r[2],
                "ran_at": r[3],
                "row_count": r[4],
                "error": r[5],
            }
            for r in rows
        ]
    }


# ── OpenSearch console (SQL plugin + raw Search DSL) ───────────────────────


def _opensearch_auth(payload: dict[str, Any]) -> tuple[str, str]:
    """Pick OpenSearch credentials.

    Per-request creds (sent from the Settings page's localStorage) win;
    otherwise we fall back to the values the hub container was started
    with (HUB_OPENSEARCH_USERNAME / HUB_OPENSEARCH_PASSWORD).
    """
    settings = get_settings()
    user = (payload.get("auth_user") or "").strip() or settings.opensearch_username
    pw = payload.get("auth_password")
    if pw is None or pw == "":
        pw = settings.opensearch_password
    if not pw:
        raise HTTPException(
            status_code=400,
            detail="OpenSearch password not set — add it under Settings.",
        )
    return user, pw


def _shape_sql_response(body: dict[str, Any]) -> dict[str, Any]:
    """Reshape an `_plugins/_sql` response into {columns, rows, row_count}."""
    schema = body.get("schema") or []
    columns = [c.get("name", "?") for c in schema]
    datarows = body.get("datarows") or []
    return {
        "columns": columns,
        "rows": [list(r) for r in datarows],
        "row_count": int(body.get("size", body.get("total", len(datarows)))),
        "raw": None,
    }


def _shape_dsl_response(body: dict[str, Any]) -> dict[str, Any]:
    """Reshape a `_search` response: hits + scalar columns from _source.

    For each hit we emit one row; columns are the flattened union of all
    ``_source`` top-level keys plus ``_id`` / ``_index`` / ``_score`` so
    you always have the document identity. Aggregations are returned in
    ``raw`` so the UI can still surface them.
    """
    hits_block = body.get("hits") or {}
    hits = hits_block.get("hits") or []
    aggs = body.get("aggregations")

    cols: list[str] = ["_index", "_id", "_score"]
    seen = set(cols)
    flattened: list[dict[str, Any]] = []
    for h in hits:
        src = h.get("_source") or {}
        flat = {
            "_index": h.get("_index"),
            "_id": h.get("_id"),
            "_score": h.get("_score"),
        }
        for k, v in src.items():
            if k not in seen:
                cols.append(k)
                seen.add(k)
            # Pass nested dicts/lists through as-is; the hub UI's
            # tree-view renderer expects real JSON values to expand into
            # multi-column headers / sub-tables. Stringifying here would
            # collapse them to opaque leaves on the client.
            flat[k] = v
        flattened.append(flat)
    rows = [[r.get(c) for c in cols] for r in flattened]
    total = (hits_block.get("total") or {}).get("value", len(hits))
    return {
        "columns": cols,
        "rows": rows,
        "row_count": int(total),
        "raw": {"aggregations": aggs} if aggs else None,
    }


@router.post("/opensearch/query", dependencies=[Depends(require_auth)])
def opensearch_query(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Run a query against OpenSearch.

    Body shape::

        {
          "mode": "sql" | "dsl",                       # default "sql"
          "query": "<text>",                            # required
          "endpoint": "https://opensearch-node1:9200",  # optional override
          "auth_user": "...", "auth_password": "..."    # optional override
        }

    For ``mode=sql``, ``query`` is sent verbatim to ``/_plugins/_sql``.
    For ``mode=dsl``, ``query`` is parsed as JSON and is expected to have
    ``index`` (target index) + the rest of the body (size, query, sort,
    aggs, …) as top-level keys.
    """
    settings = get_settings()
    mode = (payload.get("mode") or "sql").lower()
    text = (payload.get("query") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="query is required")

    base = (payload.get("endpoint") or settings.opensearch_url).rstrip("/")
    user, password = _opensearch_auth(payload)

    if mode == "sql":
        url = f"{base}/_plugins/_sql"
        try:
            resp = httpx.post(
                url,
                json={"query": text},
                auth=(user, password),
                verify=settings.opensearch_verify_tls,
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            _log_history("opensearch", text, row_count=None, error=str(exc))
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if resp.status_code != 200:
            _log_history(
                "opensearch",
                text,
                row_count=None,
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )
            raise HTTPException(
                status_code=400,
                detail=f"HTTP {resp.status_code}: {resp.text[:300]}",
            )
        shaped = _shape_sql_response(resp.json())
    elif mode == "dsl":
        # The example chips ship with `// …` doc lines so the user can read
        # what each query does. Strict JSON forbids comments, so strip the
        # leading `//` lines before parsing. We intentionally only drop
        # whole comment lines (after optional leading whitespace) — never
        # inline `//`, because that would mangle URL literals such as
        # "https://opensearch-node1:9200" sitting inside JSON string values.
        cleaned = re.sub(r"(?m)^\s*//.*$", "", text)
        try:
            doc = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400, detail=f"DSL must be valid JSON: {exc}"
            ) from exc
        index = doc.pop("index", None)
        if not index:
            raise HTTPException(
                status_code=400, detail="DSL body must include an 'index' key."
            )
        url = f"{base}/{index}/_search"
        try:
            resp = httpx.post(
                url,
                json=doc,
                auth=(user, password),
                verify=settings.opensearch_verify_tls,
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            _log_history("opensearch", text, row_count=None, error=str(exc))
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if resp.status_code != 200:
            _log_history(
                "opensearch",
                text,
                row_count=None,
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )
            raise HTTPException(
                status_code=400,
                detail=f"HTTP {resp.status_code}: {resp.text[:300]}",
            )
        shaped = _shape_dsl_response(resp.json())
    else:
        raise HTTPException(status_code=400, detail="mode must be 'sql' or 'dsl'")

    _log_history("opensearch", text, row_count=shaped["row_count"], error=None)
    return shaped


# ── Welcome-mat examples (chips under the editor) ─────────────────────────


@router.get("/examples", dependencies=[Depends(require_auth)])
def list_examples(engine: str | None = None) -> dict[str, Any]:
    """Return the curated example library.

    Without ``engine``: returns every list keyed by engine. With
    ``engine``: returns only that one (404 on an unknown engine).
    """
    catalog = db_examples_by_engine()
    if engine is None:
        return {"engines": catalog}
    if engine not in catalog:
        raise HTTPException(status_code=404, detail=f"unknown engine: {engine}")
    return {"engine": engine, "examples": catalog[engine]}
