"""Tools the AI assistant can invoke from /api/ai/chat.

Each tool maps to a single read-only query against one of the
project's data stores. The chat endpoint streams the LLM's reply,
detects ``tool_calls`` deltas, dispatches them through this module,
and feeds the JSON results back into the next turn — agentic loop,
bounded by ``MAX_TOOL_TURNS`` to keep runaway models from racking up
queries indefinitely.

Read-only guardrails:

* SPARQL — any of the SPARQL 1.1 Update keywords (``INSERT``,
  ``DELETE``, ``CLEAR``, ``DROP``, ``COPY``, ``MOVE``, ``ADD``,
  ``LOAD``, ``CREATE``) anywhere in the query is rejected before
  forwarding.
* Cypher — any of the write clauses (``CREATE``, ``MERGE``, ``SET``,
  ``REMOVE``, ``DELETE``, ``DETACH``, ``DROP``, ``FOREACH``, ``CALL
  apoc.periodic.*``, ``LOAD CSV``) anywhere is rejected.

The check is conservative on purpose: a legitimate read-only query
that happens to mention "drop" in a comment would be rejected, but
the cost is "user has to remove the word" — much better than letting
an agentic loop accidentally wipe the store.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx

from ..auth import get_settings

log = logging.getLogger(__name__)

# Hard caps the agent operates under regardless of what the model
# requests. Per-tool timeout is short so a slow / hung query doesn't
# stall the chat indefinitely; the row cap keeps the JSON body small
# enough that round-tripping it into the model's context window
# doesn't blow the token budget on a single turn.
TOOL_ROW_CAP = 1000
TOOL_TIMEOUT_SECONDS = 20.0
MAX_TOOL_TURNS = 5


# OpenAI-style tool definitions. The descriptions are deliberately
# explicit about the read-only contract so a model with good prompt
# hygiene won't waste a turn trying ``DELETE`` and bouncing off the
# guard.
TOOLS_SPEC: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "run_sparql",
            "description": (
                "Execute a SPARQL SELECT / ASK / DESCRIBE / CONSTRUCT query against "
                "the open-pulse SPARQL store (Oxigraph). Read-only — any update "
                "form (INSERT / DELETE / CLEAR / DROP / COPY / MOVE / ADD / LOAD / "
                "CREATE) is rejected before forwarding. Returns at most "
                f"{TOOL_ROW_CAP} rows. Use ``FROM <…/2026-MM/{{runtime}}>`` to "
                "scope the query to a specific snapshot graph; without a FROM "
                "clause the query targets the default graph (a mirror of the "
                "latest hybrid extraction)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The full SPARQL query text.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_cypher",
            "description": (
                "Execute a read-only Cypher query against the Neo4j community "
                "graph. Reject any write clause (CREATE / MERGE / SET / REMOVE / "
                "DELETE / DETACH / DROP / FOREACH / LOAD CSV). Returns at most "
                f"{TOOL_ROW_CAP} rows. Common label set: ``Repo`` / ``User`` / "
                "``Org``; common relationships: ``CONTRIBUTES_TO``, ``OWNS``, "
                "``MEMBER_OF``, ``FORK_OF``, ``DEPENDS_ON``, plus PR-8 opt-ins "
                "(``STARRED`` / ``WATCHES`` / ``OPENED_ISSUE`` / ``OPENED_PR`` / "
                "``COMMENTED_ON`` / ``REVIEWED_PR`` / ``FOLLOWS``)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The full Cypher query text.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_opensearch",
            "description": (
                "Query the GrimoireLab OpenSearch indices (git/github activity, "
                "commits, contributors). Read-only by construction. ``mode='sql'`` "
                "sends the text to the OpenSearch SQL plugin (treat indices as "
                "tables, e.g. ``SELECT origin, COUNT(*) FROM git_demo_raw GROUP BY "
                "origin``). ``mode='dsl'`` parses ``query`` as a JSON _search body "
                "with a top-level ``index`` key (e.g. date_histogram / terms aggs); "
                "aggregation buckets are flattened into rows. Common enriched "
                f"indices: ``git_*_enriched``, ``github_*_enriched``. Max {TOOL_ROW_CAP} rows."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SQL text (mode=sql) or a JSON _search body with an 'index' key (mode=dsl).",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["sql", "dsl"],
                        "description": "Query dialect. Default 'sql'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_duckdb",
            "description": (
                "Run a read-only DuckDB SQL query across the index source-of-truth "
                "stores. Every store is ATTACHed read-only under its name, so query "
                'tables as ``"<store>"."<table>"`` — e.g. ``SELECT owner, COUNT(*) '
                'FROM "github_repos"."repos" GROUP BY owner ORDER BY 2 DESC LIMIT '
                "10``. Discover what's available with ``SELECT database_name, "
                "schema_name, table_name FROM duckdb_tables() ORDER BY 1,3`` (stores include "
                "github_repos, zenodo, openalex, snsf, infoscience, ror, "
                "huggingface_*, orcid-*, swissubase, …). SELECT / WITH only — "
                "INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/ATTACH/COPY/PRAGMA/INSTALL/"
                f"LOAD are rejected. Max {TOOL_ROW_CAP} rows."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "Read-only DuckDB SQL (SELECT / WITH).",
                    },
                },
                "required": ["sql"],
            },
        },
    },
]


# Read-only keyword guards. Word-boundary regex so e.g. ``description``
# doesn't trigger on ``CREATE``. Comment text inside ``--`` / ``#`` /
# ``//`` strings can still trip it — that's the conservative side of
# the trade-off; harmless rewording fixes it.
_SPARQL_WRITE_RE = re.compile(
    r"\b(INSERT|DELETE|CLEAR|DROP|COPY|MOVE|ADD|LOAD|CREATE)\b",
    re.IGNORECASE,
)
# Cypher: same idea. ``CALL apoc.periodic.*`` is the agent-friendly
# escape hatch that could side-step the keyword filter — block the
# whole ``apoc.periodic`` family rather than try to enumerate verbs.
_CYPHER_WRITE_RE = re.compile(
    r"\b(CREATE|MERGE|SET|REMOVE|DELETE|DETACH|DROP|FOREACH|"
    r"LOAD\s+CSV|CALL\s+apoc\.periodic)\b",
    re.IGNORECASE,
)


class ToolError(RuntimeError):
    """Raised by ``run_tool`` for any condition the LLM should retry."""


def _truncate_rows(rows: list[Any]) -> tuple[list[Any], bool]:
    """Return ``(rows[:cap], truncated)`` — the model sees the flag."""
    if len(rows) > TOOL_ROW_CAP:
        return rows[:TOOL_ROW_CAP], True
    return rows, False


def _run_sparql(query: str) -> dict[str, Any]:
    if _SPARQL_WRITE_RE.search(query or ""):
        raise ToolError(
            "Rejected: SPARQL write keywords (INSERT / DELETE / CLEAR / DROP / "
            "COPY / MOVE / ADD / LOAD / CREATE) are not allowed via the tool "
            "interface. Use a SELECT / ASK / DESCRIBE / CONSTRUCT form."
        )
    settings = get_settings()
    base = settings.sparql_url.rstrip("/")
    if not base.endswith("/query"):
        base += "/query"
    auth = None
    if settings.sparql_user and settings.sparql_password:
        auth = httpx.BasicAuth(settings.sparql_user, settings.sparql_password)
    t0 = time.monotonic()
    resp = httpx.get(
        base,
        params={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        auth=auth,
        timeout=TOOL_TIMEOUT_SECONDS,
    )
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code != 200:
        raise ToolError(f"SPARQL HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise ToolError(f"SPARQL endpoint returned non-JSON: {exc}") from exc
    # SELECT path: ``{"head": {"vars": […]}, "results": {"bindings": […]}}``.
    # We flatten bindings to records so the LLM sees row-shaped JSON.
    head = (body.get("head") or {}).get("vars") or []
    bindings = (body.get("results") or {}).get("bindings") or []
    if bindings or head:
        records = [
            {v: ((row.get(v) or {}).get("value")) for v in head} for row in bindings
        ]
        records, truncated = _truncate_rows(records)
        return {
            "engine": "sparql",
            "columns": head,
            "rows": records,
            "row_count": len(records),
            "truncated": truncated,
            "elapsed_ms": elapsed_ms,
        }
    # ASK returns ``{"boolean": true/false}``; we forward that as-is.
    if "boolean" in body:
        return {
            "engine": "sparql",
            "ask": bool(body["boolean"]),
            "elapsed_ms": elapsed_ms,
        }
    # DESCRIBE / CONSTRUCT — text/turtle would be a separate negotiation;
    # we currently only request JSON results so anything else hits this
    # branch. The body is small enough; the model can parse it.
    return {"engine": "sparql", "raw": str(body)[:8000], "elapsed_ms": elapsed_ms}


def _run_cypher(query: str) -> dict[str, Any]:
    if _CYPHER_WRITE_RE.search(query or ""):
        raise ToolError(
            "Rejected: Cypher write clauses (CREATE / MERGE / SET / REMOVE / "
            "DELETE / DETACH / DROP / FOREACH / LOAD CSV / CALL apoc.periodic.*) "
            "are not allowed via the tool interface. Use MATCH / RETURN / OPTIONAL "
            "MATCH / WITH / UNWIND combinations only."
        )
    settings = get_settings()
    if not settings.neo4j_password:
        raise ToolError("Neo4j is not configured on this hub deployment.")
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise ToolError(f"Neo4j driver missing: {exc}") from exc

    t0 = time.monotonic()
    driver = GraphDatabase.driver(
        settings.neo4j_url,
        auth=(settings.neo4j_user or "neo4j", settings.neo4j_password),
    )
    try:
        with driver.session() as session:
            result = session.run(query)
            keys = list(result.keys())
            raw_rows = list(result)
    finally:
        driver.close()
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    records = []
    for r in raw_rows:
        rec: dict[str, Any] = {}
        for k in keys:
            v = r.get(k)
            # Normalise neo4j Node/Relationship/Path into JSON-friendly
            # dicts so the model sees something it can reason about.
            if hasattr(v, "labels") and hasattr(v, "items"):
                rec[k] = {"_labels": list(v.labels), **dict(v)}
            elif (
                hasattr(v, "type")
                and hasattr(v, "start_node")
                and hasattr(v, "end_node")
            ):
                rec[k] = {"_type": v.type, **dict(v)}
            elif hasattr(v, "items"):
                rec[k] = dict(v)
            else:
                rec[k] = v
        records.append(rec)
    records, truncated = _truncate_rows(records)
    return {
        "engine": "cypher",
        "columns": keys,
        "rows": records,
        "row_count": len(records),
        "truncated": truncated,
        "elapsed_ms": elapsed_ms,
    }


# DuckDB: forbid anything that isn't a pure read. We do the ATTACHes
# ourselves, so the agent never needs ATTACH/INSTALL/LOAD/COPY/PRAGMA —
# blocking them also closes the obvious filesystem / extension escape
# hatches.
_DUCKDB_WRITE_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|ATTACH|DETACH|COPY|REPLACE|"
    r"TRUNCATE|INSTALL|LOAD|PRAGMA|EXPORT|IMPORT|CALL)\b",
    re.IGNORECASE,
)


def _os_shapers() -> tuple[Any, Any]:
    """Import the OpenSearch response shapers lazily (avoids an import-time
    cycle and keeps ``run_duckdb``-only deployments from importing the SQL
    console module unless OpenSearch is actually queried)."""
    from .databases import _shape_dsl_response, _shape_sql_response

    return _shape_sql_response, _shape_dsl_response


def _run_opensearch(query: str, mode: str = "sql") -> dict[str, Any]:
    settings = get_settings()
    base = (settings.opensearch_url or "").rstrip("/")
    if not base:
        raise ToolError("OpenSearch is not configured on this hub deployment.")
    auth = None
    if settings.opensearch_password:
        auth = (settings.opensearch_username, settings.opensearch_password)
    mode = (mode or "sql").lower()
    shape_sql, shape_dsl = _os_shapers()
    t0 = time.monotonic()
    try:
        if mode == "dsl":
            cleaned = re.sub(r"(?m)^\s*//.*$", "", query)
            import json as _json

            doc = _json.loads(cleaned)
            index = doc.pop("index", "_all")
            resp = httpx.post(
                f"{base}/{index}/_search",
                json=doc,
                auth=auth,
                verify=settings.opensearch_verify_tls,
                timeout=TOOL_TIMEOUT_SECONDS,
            )
        else:
            resp = httpx.post(
                f"{base}/_plugins/_sql",
                json={"query": query},
                auth=auth,
                verify=settings.opensearch_verify_tls,
                timeout=TOOL_TIMEOUT_SECONDS,
            )
    except ValueError as exc:
        raise ToolError(f"DSL query is not valid JSON: {exc}") from exc
    except httpx.HTTPError as exc:
        raise ToolError(f"OpenSearch request failed: {exc}") from exc
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code != 200:
        raise ToolError(f"OpenSearch HTTP {resp.status_code}: {resp.text[:300]}")
    shaped = (shape_dsl if mode == "dsl" else shape_sql)(resp.json())
    rows, truncated = _truncate_rows(shaped.get("rows") or [])
    return {
        "engine": "opensearch",
        "mode": mode,
        "columns": shaped.get("columns"),
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_ms": elapsed_ms,
    }


def _duckdb_stores() -> list[tuple[str, str]]:
    """``(alias, path)`` for every index store, preferring the read-only
    ``.ro.duckdb`` snapshot (no writer-lock contention)."""
    import os
    from pathlib import Path

    root = Path(os.environ.get("HUB_DATA_DIR_HOST", "/data")) / "index"
    out: list[tuple[str, str]] = []
    if not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if (p / "duckdb").is_dir()):
        dd = d / "duckdb"
        ro = dd / f"{d.name}.ro.duckdb"
        live = dd / f"{d.name}.duckdb"
        if ro.is_file():
            out.append((d.name, str(ro)))
        elif live.is_file():
            out.append((d.name, str(live)))
    return out


def _duckdb_cell(v: Any) -> Any:
    """JSON-safe coercion of a DuckDB cell (datetimes, bytes, decimals)."""
    from datetime import date, datetime
    from decimal import Decimal

    if v is None or isinstance(v, (str, int, float, bool, list, dict)):
        return v
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)} bytes>"
    return str(v)


def _run_duckdb(sql: str) -> dict[str, Any]:
    if _DUCKDB_WRITE_RE.search(sql or ""):
        raise ToolError(
            "Rejected: only read-only SELECT / WITH queries are allowed (no "
            "INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/ATTACH/COPY/PRAGMA/INSTALL/"
            "LOAD/CALL)."
        )
    try:
        import duckdb
    except ImportError as exc:
        raise ToolError(f"duckdb is not available: {exc}") from exc

    stores = _duckdb_stores()
    t0 = time.monotonic()
    con = duckdb.connect(":memory:")
    try:
        for alias, path in stores:
            try:
                con.execute(f"ATTACH '{path}' AS \"{alias}\" (READ_ONLY)")
            except Exception:  # noqa: BLE001 — skip a locked / unreadable store
                continue
        cur = con.execute(sql)
        cols = [c[0] for c in (cur.description or [])]
        raw = cur.fetchmany(TOOL_ROW_CAP + 1)
    except Exception as exc:  # noqa: BLE001 — surface to the model, don't 500
        raise ToolError(f"DuckDB error: {exc}") from exc
    finally:
        con.close()
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    truncated = len(raw) > TOOL_ROW_CAP
    rows = [[_duckdb_cell(v) for v in r] for r in raw[:TOOL_ROW_CAP]]
    return {
        "engine": "duckdb",
        "columns": cols,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_ms": elapsed_ms,
        "attached_stores": [a for a, _ in stores],
    }


def run_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch to the named tool. Always returns a dict; raises ``ToolError``
    only for conditions the model should see (write-guard rejection,
    upstream HTTP error, missing driver, etc.) — they get serialised
    into the tool message so the next turn can adapt.
    """
    if name == "run_sparql":
        query = (arguments or {}).get("query") or ""
        if not query.strip():
            raise ToolError("``query`` argument is empty.")
        return _run_sparql(query)
    if name == "run_cypher":
        query = (arguments or {}).get("query") or ""
        if not query.strip():
            raise ToolError("``query`` argument is empty.")
        return _run_cypher(query)
    if name == "run_opensearch":
        query = (arguments or {}).get("query") or ""
        if not query.strip():
            raise ToolError("``query`` argument is empty.")
        return _run_opensearch(query, (arguments or {}).get("mode") or "sql")
    if name == "run_duckdb":
        sql = (arguments or {}).get("sql") or (arguments or {}).get("query") or ""
        if not sql.strip():
            raise ToolError("``sql`` argument is empty.")
        return _run_duckdb(sql)
    raise ToolError(f"Unknown tool: {name}")
