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
    raise ToolError(f"Unknown tool: {name}")
