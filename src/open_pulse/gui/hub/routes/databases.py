"""DuckDB SQL console + saved queries (SQLite for the saved-queries table).

The DuckDB console is the analytics surface — it can read CSV/Parquet/JSON
straight from the shared `data/` mount and remote-attach SQLite. We persist
saved queries and recent history in a tiny SQLite app DB next to the
DuckDB scratch file. SPARQL, Cypher (Neo4j), and OpenSearch consoles share
the same surface — see ``../db_examples.py`` for the welcome-mat snippets.
"""

from __future__ import annotations

import contextvars
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import duckdb
import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ..auth import get_settings, require_auth
from ..db_examples import by_engine as db_examples_by_engine

router = APIRouter(prefix="/api/databases", tags=["databases"])

# The reader-token id of the caller, set at each query handler's entry so
# ``_log_history`` can attribute the query to a token without threading the
# id through all eleven call sites. Default None → admin / env-reader queries
# are recorded untagged. Each request runs in its own copied context (async
# task or threadpool worker), so a set here never leaks across requests.
_LOG_TOKEN_ID: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "op_log_token_id", default=None
)


def _stamp_token(request: Request) -> None:
    """Record the caller's reader-token id for this request's query logging."""
    _LOG_TOKEN_ID.set(getattr(request.state, "token_id", None))


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
    # Migration: attribute each console query to the reader token that ran it
    # (NULL for admin / env-reader). Surfaced in the Users → Activity panel.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(query_history)")}
    if "token_id" not in cols:
        conn.execute("ALTER TABLE query_history ADD COLUMN token_id INTEGER")
        conn.commit()
    return conn


@router.post("/duckdb/query", dependencies=[Depends(require_auth)])
def duckdb_query(
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Run an arbitrary read-only DuckDB query against the scratch DB.

    The hub mounts `data/` at /data, so users can do e.g.::

        SELECT * FROM read_csv_auto('/data/some-export.csv') LIMIT 100

    or attach SQLite::

        ATTACH '/data/hub/app.db' AS app (TYPE SQLITE);
        SELECT * FROM app.saved_queries;
    """
    _stamp_token(request)
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
            "INSERT INTO query_history(engine, query, row_count, error, token_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (engine, query, row_count, error, _LOG_TOKEN_ID.get()),
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
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Run a SELECT against the SPARQL store and return raw bindings."""
    _stamp_token(request)
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
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Run a Cypher statement against Neo4j and return result rows.

    Admins get the full console (reads + writes). Readers (and the global
    HUB_READONLY switch) run inside a Neo4j READ transaction, so any write
    clause is rejected server-side — the graph stays immutable for them.
    """
    _stamp_token(request)
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

    # Only admins (and only when the hub isn't globally read-only) may run
    # write Cypher. Everyone else runs in a read transaction Neo4j refuses to
    # write through.
    is_admin = (
        getattr(request.state, "user_role", None) == "admin"
        and not settings.read_only
    )

    def _read_tx(tx: Any) -> tuple[list[str], list[list[Any]]]:
        res = tx.run(query)
        ks = list(res.keys())
        return ks, [[rec.get(k) for k in ks] for rec in res]

    try:
        driver = GraphDatabase.driver(settings.neo4j_url, auth=(user, pw))
        with driver.session() as s:
            if is_admin:
                result = s.run(query)
                keys = list(result.keys())
                rows = [[record.get(k) for k in keys] for record in result]
            else:
                keys, rows = s.execute_read(_read_tx)
        driver.close()
    except Exception as e:
        _log_history("cypher", query, row_count=None, error=str(e))
        # A reader's write attempt surfaces as a Neo4j access-mode error;
        # return a clear 403 instead of a cryptic 400.
        msg = str(e)
        if not is_admin and "write" in msg.lower():
            raise HTTPException(
                status_code=403,
                detail=(
                    "Read-only access: write Cypher (CREATE / MERGE / DELETE / "
                    "SET) is not permitted for this account."
                ),
            ) from e
        raise HTTPException(status_code=400, detail=msg) from e

    _log_history("cypher", query, row_count=len(rows), error=None)
    return {
        "columns": keys,
        "rows": [[_normalize(c) for c in row] for row in rows],
        "row_count": len(rows),
    }


def _normalize(value: Any) -> Any:
    """Make Cypher values JSON-friendly while preserving graph structure.

    Nodes get ``{"_kind":"node","_id":...,"_labels":[...], ...props}``;
    relationships get ``{"_kind":"rel","_id":...,"_type":..., "_start":...,
    "_end":..., ...props}``. The extra underscore-prefixed keys let the
    Databases console render a Cypher result as a graph in addition to the
    flat table — without them, ``rel`` came back as an empty dict.
    """
    # Relationship has _type and start/end_node ids — must be checked before
    # the generic Node branch because relationships also expose properties.
    if (
        hasattr(value, "type")
        and hasattr(value, "start_node")
        and hasattr(value, "end_node")
    ):
        return {
            "_kind": "rel",
            "_id": str(getattr(value, "element_id", getattr(value, "id", ""))),
            "_type": value.type,
            "_start": str(
                getattr(
                    value.start_node, "element_id", getattr(value.start_node, "id", "")
                )
            ),
            "_end": str(
                getattr(value.end_node, "element_id", getattr(value.end_node, "id", ""))
            ),
            **dict(value),
        }
    if hasattr(value, "labels") and hasattr(value, "items"):
        return {
            "_kind": "node",
            "_id": str(getattr(value, "element_id", getattr(value, "id", ""))),
            "_labels": list(value.labels),
            **dict(value),
        }
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


def _agg_key(bucket: dict[str, Any]) -> Any:
    """Bucket label: date_histogram exposes ``key_as_string``; terms uses ``key``."""
    v = bucket.get("key_as_string")
    return v if v is not None else bucket.get("key")


def _metric_value(node: dict[str, Any]) -> Any:
    """Scalar value of a metric sub-agg (cardinality/sum/avg/value_count/…)."""
    if "value_as_string" in node:
        return node["value_as_string"]
    return node.get("value")


def _shape_aggregations(
    aggs: dict[str, Any],
) -> tuple[list[str], list[list[Any]]] | None:
    """Flatten an OpenSearch ``aggregations`` block into ``(columns, rows)``.

    ``size: 0`` aggregation queries return no hits, so without this their
    buckets only ever reached the UI as an opaque JSON dump. Flattening
    them into a real table lets the row browser — and the chart layer —
    use them directly. Shapes handled (covering every curated example):

    * ``date_histogram`` / ``terms`` buckets   → ``[key, doc_count]``
    * bucket agg + metric sub-aggs (sum/avg/…)  → ``[key, doc_count, m1, m2…]``
    * bucket agg + a nested bucket sub-agg      → cross-tab ``[parent, child, doc_count]``
    * a bare top-level metric agg (no buckets)  → one row of metric values

    Returns ``None`` when nothing is bucketable/metric, so the caller keeps
    the raw-dump fallback rather than inventing an empty table.
    """
    if not isinstance(aggs, dict):
        return None

    # First named agg that has buckets wins; remember bare metric aggs as
    # a fallback for the no-buckets case.
    bucket_name: str | None = None
    bucket_node: dict[str, Any] | None = None
    metric_only: list[tuple[str, Any]] = []
    for name, node in aggs.items():
        if not isinstance(node, dict):
            continue
        if isinstance(node.get("buckets"), list):
            bucket_name, bucket_node = name, node
            break
        if "value" in node or "value_as_string" in node:
            metric_only.append((name, _metric_value(node)))

    if bucket_node is None:
        if metric_only:
            return [n for n, _ in metric_only], [[v for _, v in metric_only]]
        return None

    buckets = bucket_node.get("buckets") or []
    # Friendly key column: ``by_month`` → ``month``, ``by_author`` → ``author``.
    key_col = bucket_name[3:] if bucket_name.startswith("by_") else bucket_name

    sample = buckets[0] if buckets else {}
    nested_name = next(
        (
            k
            for k, v in sample.items()
            if isinstance(v, dict) and isinstance(v.get("buckets"), list)
        ),
        None,
    )

    # Cross-tab: parent bucket × child bucket → [parent, child, doc_count].
    if nested_name is not None:
        child_col = (
            nested_name[3:] if nested_name.startswith("by_") else nested_name
        )
        cols = [key_col, child_col, "doc_count"]
        rows: list[list[Any]] = []
        for b in buckets:
            parent = _agg_key(b)
            for cb in (b.get(nested_name) or {}).get("buckets") or []:
                rows.append([parent, _agg_key(cb), int(cb.get("doc_count") or 0)])
        return cols, rows

    # Metric sub-aggs → extra numeric columns (stable first-seen union).
    metric_names: list[str] = []
    for b in buckets:
        for k, v in b.items():
            if k in ("key", "key_as_string", "doc_count"):
                continue
            if isinstance(v, dict) and ("value" in v or "value_as_string" in v):
                if k not in metric_names:
                    metric_names.append(k)

    cols = [key_col, "doc_count", *metric_names]
    rows = []
    for b in buckets:
        row: list[Any] = [_agg_key(b), int(b.get("doc_count") or 0)]
        for m in metric_names:
            sub = b.get(m)
            row.append(_metric_value(sub) if isinstance(sub, dict) else None)
        rows.append(row)
    return cols, rows


def _shape_dsl_response(body: dict[str, Any]) -> dict[str, Any]:
    """Reshape a `_search` response: hits + scalar columns from _source.

    For each hit we emit one row; columns are the flattened union of all
    ``_source`` top-level keys plus ``_id`` / ``_index`` / ``_score`` so
    you always have the document identity. For aggregation-only responses
    (``size: 0``) the buckets are flattened into a real table via
    :func:`_shape_aggregations`; ``raw`` still carries the untouched
    aggregations as a fallback.
    """
    hits_block = body.get("hits") or {}
    hits = hits_block.get("hits") or []
    aggs = body.get("aggregations")

    # Aggregation-only response: turn the buckets into a table the row
    # browser + chart layer can consume, instead of an opaque JSON dump.
    if aggs and not hits:
        shaped = _shape_aggregations(aggs)
        if shaped is not None:
            agg_cols, agg_rows = shaped
            return {
                "columns": agg_cols,
                "rows": agg_rows,
                "row_count": len(agg_rows),
                "raw": {"aggregations": aggs},
            }

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
    request: Request,
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
    _stamp_token(request)
    settings = get_settings()
    mode = (payload.get("mode") or "sql").lower()
    # `query` is normally a string (SQL text, or JSON-as-text for DSL), but a
    # JSON API caller naturally sends a DSL body as an object — accept that too
    # rather than crashing on `.strip()`. Anything else stringifies cleanly.
    raw_query = payload.get("query")
    if isinstance(raw_query, (dict, list)):
        text = json.dumps(raw_query)
    elif raw_query is None:
        text = ""
    else:
        text = str(raw_query).strip()
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
