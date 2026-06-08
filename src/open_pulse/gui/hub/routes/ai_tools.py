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
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from ..auth import get_settings

log = logging.getLogger(__name__)

# Scratch space for files attached in the Agent chat. Lives in /tmp by
# default (ephemeral — wiped on host reboot). ``read_file`` / ``list_files``
# are confined to this directory; path traversal out of it is rejected, so
# nothing else on the host filesystem is reachable through the tools.
AGENT_FILES_DIR = Path(os.environ.get("HUB_AGENT_FILES_DIR", "/tmp/op-agent-files"))
FILE_READ_CAP = 20_000  # bytes returned to the model per read_file call

# Hard caps the agent operates under regardless of what the model
# requests. Per-tool timeout is short so a slow / hung query doesn't
# stall the chat indefinitely; the row cap keeps the JSON body small
# enough that round-tripping it into the model's context window
# doesn't blow the token budget on a single turn.
TOOL_ROW_CAP = 1000
TOOL_TIMEOUT_SECONDS = 20.0
# Semantic search (embed query + vector search + rerank) is much slower
# than a SQL/SPARQL round-trip, so gme_search gets its own longer budget.
SEARCH_TIMEOUT_SECONDS = 60.0
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
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "List the files the user attached to this chat (they live in a "
                "scratch dir under /tmp). Returns each file's name, size, and "
                "absolute path. Use the path with run_duckdb's read_csv / "
                "read_parquet / read_json for tabular files, or read_file for text."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the (text) contents of an attached file by name — at most "
                f"{FILE_READ_CAP} bytes, UTF-8 with replacement. For CSV / Parquet "
                "/ JSON prefer run_duckdb (``SELECT * FROM "
                "read_csv('<path>')``) so you get typed rows instead of raw text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The attached file's name (see list_files).",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gme_search",
            "description": (
                "Semantic search one of the GME's federated index stores (vector "
                "search + rerank). Pick an ``index`` and pass a free-text "
                "``query``. Valid indices: github_repos, github_users, "
                "github_organizations, zenodo_records, zenodo_communities, "
                "openalex, orcid, ror, infoscience, snsf, swissubase, "
                "ethz_research_collection, renkulab, epfl_graph, oamonitor, "
                "dockerhub, huggingface_models, huggingface_datasets, "
                "huggingface_spaces, huggingface_organizations, "
                "huggingface_papers. Multi-entity indices (openalex, "
                "huggingface_*, ethz_research_collection) take an optional "
                "``target`` to pick the entity type. Returns ranked hits "
                "(id / title / url / score / payload)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "string", "description": "Index store name (see list)."},
                    "query": {"type": "string", "description": "Free-text query."},
                    "top_k": {"type": "integer", "description": "Max hits (1-50, default 10)."},
                    "target": {"type": "string", "description": "Entity type for multi-entity indices (optional)."},
                },
                "required": ["index", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_metadata",
            "description": (
                "ACTION (kicks off a job): run the GME metadata extractor on a "
                "GitHub repo / user / org URL — this is gimie under the hood. "
                "``runtime``: ``rule_based`` = deterministic gimie extraction "
                "(fast), ``hybrid`` = gimie + LLM agent refinement, ``llm`` = "
                "agent-only. Returns a job_id (the extraction runs "
                "asynchronously). Only call this when the user explicitly asks "
                "to extract / enrich a repository."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_url": {"type": "string", "description": "GitHub repo / user / org URL or handle."},
                    "runtime": {
                        "type": "string",
                        "enum": ["rule_based", "hybrid", "llm"],
                        "description": "Extraction runtime; rule_based = gimie-only (default).",
                    },
                },
                "required": ["source_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_crawler",
            "description": (
                "ACTION (kicks off a job): start an open-pulse crawl from seed "
                "GitHub orgs / users / ``owner/repo`` handles. BFS over the "
                "community graph for ``max_rounds`` rounds; optionally follow "
                "dependency / dependent edges. Returns a crawl job_id (runs "
                "asynchronously). Only call this when the user explicitly asks "
                "to crawl / seed new repositories."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "seeds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Seed nodes: GitHub orgs, users, or owner/repo.",
                    },
                    "max_rounds": {"type": "integer", "description": "BFS rounds (1-5, default 2)."},
                    "crawl_dependencies": {"type": "boolean", "description": "Also crawl each repo's dependencies."},
                    "crawl_dependents": {"type": "boolean", "description": "Also crawl each repo's dependents."},
                },
                "required": ["seeds"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chaoss_metrics",
            "description": (
                "CHAOSS community-health metrics for a GitHub repository. Call "
                "with NO arguments to list the metric catalogue (slug, name, "
                "category, question). Call with ``metric`` (a slug) + ``owner`` "
                "+ ``repo`` to compute one metric for that repo — e.g. "
                "metric='contributors', owner='sdsc-ordes', repo='gimie'. "
                "``window`` is the lookback in days (default 365) for "
                "time-based metrics. Returns the headline value, label, an "
                "optional time series, and methodology notes. Read-only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "description": "Metric slug (omit to list the catalogue)."},
                    "owner": {"type": "string", "description": "GitHub owner / org."},
                    "repo": {"type": "string", "description": "GitHub repository name."},
                    "window": {"type": "integer", "description": "Lookback window in days (default 365)."},
                },
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


def _safe_agent_file(name: str) -> Path:
    """Resolve ``name`` inside :data:`AGENT_FILES_DIR`, rejecting traversal."""
    base = AGENT_FILES_DIR.resolve()
    target = (base / Path(name).name).resolve()
    if base not in target.parents and target != base:
        raise ToolError("Invalid file name.")
    return target


def _list_files() -> dict[str, Any]:
    base = AGENT_FILES_DIR
    files: list[dict[str, Any]] = []
    if base.is_dir():
        for p in sorted(base.iterdir()):
            if p.is_file():
                files.append(
                    {"name": p.name, "size": p.stat().st_size, "path": str(p)}
                )
    return {"files": files, "count": len(files), "dir": str(base)}


def _read_file(name: str) -> dict[str, Any]:
    target = _safe_agent_file(name)
    if not target.is_file():
        raise ToolError(f"No attached file named {name!r}. Call list_files first.")
    data = target.read_bytes()
    truncated = len(data) > FILE_READ_CAP
    text = data[:FILE_READ_CAP].decode("utf-8", errors="replace")
    return {
        "name": target.name,
        "bytes": len(data),
        "truncated": truncated,
        "text": text,
    }


def _gme_base() -> str:
    return os.environ.get(
        "HUB_EXTRACTOR_URL", "http://git-metadata-extractor:1234"
    ).rstrip("/")


def _gme_headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    tok = os.environ.get("EXTRACTOR_API_TOKEN", "")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _crawler_base() -> str:
    return os.environ.get("HUB_CRAWLER_URL", "http://crawler:8000").rstrip("/")


def _crawler_headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    tok = os.environ.get("CRAWLER_API_TOKEN", "")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _run_gme_search(
    index: str, query: str, top_k: int = 10, target: str | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"query": query, "top_k": max(1, min(int(top_k or 10), 50))}
    if target:
        body["target"] = target
    try:
        resp = httpx.post(
            f"{_gme_base()}/v2/indices/{index}/search",
            json=body,
            headers=_gme_headers(),
            # Semantic search embeds the query + reranks, so it's much slower
            # than a SQL round-trip — give it a longer budget than the
            # shared TOOL_TIMEOUT_SECONDS.
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException as exc:
        raise ToolError(
            "GME search timed out — the index embedder/reranker may be cold "
            "or unavailable on this deployment. Try a SQL/DuckDB query instead."
        ) from exc
    except httpx.HTTPError as exc:
        raise ToolError(f"GME search request failed: {exc}") from exc
    if resp.status_code == 404:
        raise ToolError(f"Unknown index {index!r}. See the index list in the tool description.")
    if resp.status_code != 200:
        raise ToolError(f"GME search HTTP {resp.status_code}: {resp.text[:300]}")
    hits = (resp.json() or {}).get("hits") or []
    out = []
    for h in hits[:TOOL_ROW_CAP]:
        pay = h.get("payload") or {}
        out.append(
            {
                "id": h.get("id"),
                "score": h.get("rerank_score") if h.get("rerank_score") is not None else h.get("vector_score"),
                "title": pay.get("title") or pay.get("name") or pay.get("full_name"),
                "url": pay.get("url") or pay.get("html_url"),
                "payload": pay,
            }
        )
    return {
        "engine": "gme_search",
        "index": index,
        "target": target,
        "query": query,
        "hits": out,
        "count": len(out),
    }


_EXTRACT_RUNTIMES = {"rule_based", "llm", "hybrid"}


def _run_extract(source_url: str, runtime: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"source_url": source_url}
    if runtime:
        rt = str(runtime).lower()
        if rt not in _EXTRACT_RUNTIMES:
            raise ToolError(
                "runtime must be rule_based (gimie-only) / hybrid / llm."
            )
        body["agent_runtime"] = rt
    try:
        resp = httpx.post(
            f"{_gme_base()}/v2/extract",
            json=body,
            headers=_gme_headers(),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise ToolError(f"extract request failed: {exc}") from exc
    if resp.status_code not in (200, 202):
        raise ToolError(f"extract HTTP {resp.status_code}: {resp.text[:300]}")
    j = resp.json()
    return {
        "engine": "gme_extract",
        "submitted": True,
        "source_url": source_url,
        "agent_runtime": body.get("agent_runtime", "server default"),
        "job_id": j.get("job_id"),
        "status": j.get("status"),
        "note": "Extraction runs asynchronously — poll the job or watch the Pipeline page.",
    }


def _run_crawler(
    seeds: Any,
    max_rounds: int = 2,
    crawl_dependencies: bool = False,
    crawl_dependents: bool = False,
) -> dict[str, Any]:
    if isinstance(seeds, str):
        seeds = [s.strip() for s in re.split(r"[\s,]+", seeds) if s.strip()]
    if not seeds:
        raise ToolError("``seeds`` (GitHub orgs / users / owner/repo) is required.")
    body = {
        "seeds": list(seeds),
        "max_rounds": max(1, min(int(max_rounds or 2), 5)),
        "crawl_dependencies": bool(crawl_dependencies),
        "crawl_dependents": bool(crawl_dependents),
    }
    try:
        resp = httpx.post(
            f"{_crawler_base()}/api/v1/crawl",
            json=body,
            headers=_crawler_headers(),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise ToolError(f"crawler request failed: {exc}") from exc
    if resp.status_code not in (200, 201, 202):
        raise ToolError(f"crawler HTTP {resp.status_code}: {resp.text[:300]}")
    j = resp.json()
    return {
        "engine": "crawler",
        "submitted": True,
        "seeds": body["seeds"],
        "max_rounds": body["max_rounds"],
        "job_id": j.get("job_id") or j.get("id"),
        "status": j.get("status"),
        "note": "Crawl runs asynchronously — watch the Pipeline page for progress.",
    }


def _run_chaoss(
    metric: str | None = None,
    owner: str | None = None,
    repo: str | None = None,
    window: int = 365,
) -> dict[str, Any]:
    from ..chaoss import metrics as M  # heavy module — import lazily

    if not metric:
        cat = [
            {
                "slug": s.slug,
                "name": s.name,
                "category": s.category,
                "question": s.question,
                "time_based": s.is_time_based,
            }
            for s in M.REGISTRY
        ]
        return {
            "engine": "chaoss",
            "catalogue": cat,
            "count": len(cat),
            "note": "Pass metric=<slug> + owner + repo to compute one for a repo.",
        }

    spec = M.spec_for(metric)
    if spec is None:
        avail = ", ".join(s.slug for s in M.REGISTRY)
        raise ToolError(f"Unknown metric {metric!r}. Available slugs: {avail}")
    if not (owner and repo):
        raise ToolError("``owner`` and ``repo`` are required to compute a metric.")

    full = f"{owner}/{repo}"
    try:
        res = spec.compute(full, f"https://github.com/{full}", int(window or 365))
    except Exception as exc:  # noqa: BLE001 — surface to the model
        raise ToolError(f"chaoss metric {metric!r} failed for {full}: {exc}") from exc

    series = (res.series or [])[:60]
    return {
        "engine": "chaoss",
        "metric": res.slug,
        "name": spec.name,
        "category": spec.category,
        "repo": full,
        "window_days": int(window or 365),
        "value": res.value,
        "label": res.label,
        "secondary": res.secondary,
        "series_unit": res.series_unit,
        "series": series,
        "notes": res.notes,
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
    if name == "list_files":
        return _list_files()
    if name == "read_file":
        fname = (arguments or {}).get("name") or ""
        if not fname.strip():
            raise ToolError("``name`` argument is empty.")
        return _read_file(fname)
    if name == "gme_search":
        a = arguments or {}
        index = (a.get("index") or "").strip()
        query = (a.get("query") or "").strip()
        if not index or not query:
            raise ToolError("``index`` and ``query`` are required.")
        return _run_gme_search(index, query, a.get("top_k", 10), a.get("target"))
    if name == "extract_metadata":
        a = arguments or {}
        url = (a.get("source_url") or a.get("url") or "").strip()
        if not url:
            raise ToolError("``source_url`` is required.")
        return _run_extract(url, a.get("runtime"))
    if name == "run_crawler":
        a = arguments or {}
        return _run_crawler(
            a.get("seeds"),
            a.get("max_rounds", 2),
            a.get("crawl_dependencies", False),
            a.get("crawl_dependents", False),
        )
    if name == "chaoss_metrics":
        a = arguments or {}
        return _run_chaoss(
            a.get("metric"), a.get("owner"), a.get("repo"), a.get("window", 365)
        )
    raise ToolError(f"Unknown tool: {name}")
