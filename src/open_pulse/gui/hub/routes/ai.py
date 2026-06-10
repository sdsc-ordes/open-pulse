"""AI assistant endpoint — chat proxy + model listing.

Forwards to whatever OpenAI-compatible base URL the hub is configured to
talk to (settings.llm_base_url, default EPFL RCP). Keeps the API key on
the server so it never reaches the browser. Two routes:

- ``GET  /api/ai/models``   — list available models (proxy ``/models``).
- ``POST /api/ai/chat``     — chat completions with SSE streaming.

The chat body accepts an optional ``context`` dict that the route folds
into a system message before forwarding. Today's keys are the
Databases-console-specific ones — ``engine``, ``query``,
``named_graphs``, ``preview_rows`` (head/tail of the last result table).
Future surfaces can add their own keys without changing the wire format.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from ..auth import get_settings, require_auth
from .ai_tools import (
    AGENT_FILES_DIR,
    MAX_TOOL_TURNS,
    MAX_TOOL_TURNS_CEILING,
    ToolError,
    run_tool,
    runtime_tools_spec,
)

router = APIRouter(prefix="/api/ai", tags=["ai"])
log = logging.getLogger(__name__)

_MODELS_TIMEOUT = 10.0
_CHAT_TIMEOUT = 600.0
# Cadence of SSE keepalive comments emitted while a tool runs, so a long
# (e.g. ~20s gme_search) round-trip never leaves the connection idle long
# enough for an intermediary proxy to drop it with a 502.
_HEARTBEAT_SECONDS = 10.0
_SCHEMA_CACHE_TTL = 600.0  # 10 min — store schema changes on quest runs, not minutes
_SCHEMA_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _headers() -> dict[str, str]:
    settings = get_settings()
    h = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        h["Authorization"] = f"Bearer {settings.llm_api_key}"
    return h


def _base_url() -> str:
    settings = get_settings()
    base = (settings.llm_base_url or "").rstrip("/")
    if not base:
        raise HTTPException(
            status_code=503,
            detail=(
                "LLM endpoint is not configured. Set HUB_LLM_BASE_URL or "
                "rely on the RCP_TOKEN fallback."
            ),
        )
    return base


@router.get("/models", dependencies=[Depends(require_auth)])
def list_models() -> dict[str, Any]:
    """Return the model catalog from the configured LLM endpoint.

    Some endpoints (RCP, OpenAI) gate the model list behind auth; others
    (Ollama, vLLM unauthenticated) don't. We pass our configured key
    when present and fall through to ``{"data": []}`` on any failure
    so the UI can still render a "no models" hint.
    """
    base = _base_url()
    url = f"{base}/models"
    try:
        resp = httpx.get(url, headers=_headers(), timeout=_MODELS_TIMEOUT)
    except httpx.HTTPError as exc:
        log.info("models proxy: %s", exc)
        return {"data": [], "error": str(exc)}
    if resp.status_code != 200:
        return {
            "data": [],
            "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
        }
    try:
        body = resp.json()
    except ValueError:
        return {"data": [], "error": "non-JSON response from upstream"}
    # Pass through verbatim — the UI handles both ``{"data":[…]}``
    # (OpenAI-style) and bare lists (some self-hosted servers).
    return body


# ── Schema introspection ──────────────────────────────────────────────
# The static system prompt describes the v3 schema in prose. That's
# the right baseline (it doesn't change with the data) but it can drift
# from reality when the extractor adds predicates, a new month creates
# a snapshot graph, etc. This block introspects the live SPARQL +
# Neo4j stores so the assistant's first turn always sees today's
# actual shape — predicates that exist, type counts, named graphs,
# labels, relationship types, property keys.


def _sparql_run_query(
    query: str, *, timeout: float = 8.0
) -> list[dict[str, Any]] | None:
    """Run a SPARQL SELECT and return the binding list, or None on failure.

    Used only for read-only introspection probes; intentionally
    fail-soft so a slow / unreachable store skips that section of
    the schema dump rather than 500-ing the endpoint.
    """
    settings = get_settings()
    base = settings.sparql_url.rstrip("/")
    if not base.endswith("/query"):
        base += "/query"
    auth = None
    if settings.sparql_user and settings.sparql_password:
        auth = httpx.BasicAuth(settings.sparql_user, settings.sparql_password)
    try:
        resp = httpx.get(
            base,
            params={"query": query},
            headers={"Accept": "application/sparql-results+json"},
            auth=auth,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        log.info("schema sparql probe: %s", exc)
        return None
    if resp.status_code != 200:
        return None
    try:
        return (resp.json().get("results") or {}).get("bindings") or []
    except ValueError:
        return None


def _sparql_schema() -> dict[str, Any]:
    """Snapshot the SPARQL store's shape: named graphs + top predicates + types.

    All probes are independent + fail-soft. We cap result sizes so the
    payload stays small (each section ≲ 1 KB) — the system message has
    a token budget too.
    """
    out: dict[str, Any] = {}

    graphs = _sparql_run_query(
        "SELECT ?g (COUNT(*) AS ?n) "
        "WHERE { GRAPH ?g { ?s ?p ?o } } "
        "GROUP BY ?g ORDER BY DESC(?n)",
    )
    if graphs is not None:
        out["named_graphs"] = [
            {
                "uri": (g.get("g") or {}).get("value"),
                "triples": int((g.get("n") or {}).get("value", 0) or 0),
            }
            for g in graphs[:20]
            if (g.get("g") or {}).get("value")
        ]

    preds = _sparql_run_query(
        "SELECT ?p (COUNT(*) AS ?n) WHERE { ?s ?p ?o } "
        "GROUP BY ?p ORDER BY DESC(?n) LIMIT 40",
    )
    if preds is not None:
        out["top_predicates"] = [
            {
                "predicate": (p.get("p") or {}).get("value"),
                "count": int((p.get("n") or {}).get("value", 0) or 0),
            }
            for p in preds
            if (p.get("p") or {}).get("value")
        ]

    types = _sparql_run_query(
        "SELECT ?t (COUNT(DISTINCT ?s) AS ?n) WHERE { ?s a ?t } "
        "GROUP BY ?t ORDER BY DESC(?n) LIMIT 20",
    )
    if types is not None:
        out["top_types"] = [
            {
                "type": (t.get("t") or {}).get("value"),
                "count": int((t.get("n") or {}).get("value", 0) or 0),
            }
            for t in types
            if (t.get("t") or {}).get("value")
        ]

    return out


def _neo4j_schema() -> dict[str, Any]:
    """Snapshot Neo4j's labels + rel types + property keys + per-label counts.

    Three SHOW commands give us the names; one ``MATCH`` per label
    gives the histogram. Same fail-soft contract: any failure short-
    circuits the section.
    """
    settings = get_settings()
    if not settings.neo4j_password:
        return {}
    try:
        from neo4j import GraphDatabase
    except ImportError:
        return {}
    out: dict[str, Any] = {}
    try:
        driver = GraphDatabase.driver(
            settings.neo4j_url,
            auth=(settings.neo4j_user or "neo4j", settings.neo4j_password),
        )
        try:
            with driver.session() as session:
                # Counts per label in one round-trip.
                rows = list(
                    session.run(
                        "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n "
                        "ORDER BY n DESC"
                    )
                )
                out["label_counts"] = [
                    {"label": r["label"], "count": int(r["n"])}
                    for r in rows
                    if r["label"] is not None
                ]
                rel_rows = list(
                    session.run(
                        "MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS n "
                        "ORDER BY n DESC"
                    )
                )
                out["relationship_counts"] = [
                    {"type": r["rel"], "count": int(r["n"])} for r in rel_rows
                ]
                prop_rows = list(
                    session.run("CALL db.propertyKeys() YIELD propertyKey")
                )
                out["property_keys"] = sorted(r["propertyKey"] for r in prop_rows)
        finally:
            driver.close()
    except Exception as exc:  # noqa: BLE001 — fail-soft schema probe
        log.info("schema neo4j probe: %s", exc)
        return {}
    return out


@router.get("/schema-context", dependencies=[Depends(require_auth)])
def schema_context() -> dict[str, Any]:
    """Return live introspection of the SPARQL + Neo4j stores.

    Cached for ``_SCHEMA_CACHE_TTL`` seconds so opening the chat panel
    isn't gated on a fresh round-trip every time. The cache is keyed
    by ``"all"`` today (we only expose one snapshot); future expansion
    could split per-engine when probes diverge in cost.
    """
    now = time.monotonic()
    cached = _SCHEMA_CACHE.get("all")
    if cached and now - cached[0] < _SCHEMA_CACHE_TTL:
        return cached[1]
    payload: dict[str, Any] = {
        "sparql": _sparql_schema(),
        "neo4j": _neo4j_schema(),
        "generated_at": time.time(),
    }
    _SCHEMA_CACHE["all"] = (now, payload)
    return payload


_SYSTEM_PROMPT = (
    "You are the open-pulse Databases assistant. The user is composing "
    "queries against three back-ends: SPARQL (Oxigraph, default graph "
    "mirrors the latest hybrid extraction at "
    "``https://open-pulse.epfl.ch/graph/{YYYY-MM}/hybrid``), Cypher (Neo4j: "
    "``Repo`` / ``User`` / ``Org`` nodes with ``CONTRIBUTES_TO``, ``OWNS``, "
    "``MEMBER_OF``, ``FORK_OF``, ``DEPENDS_ON`` plus optional PR-8 edges "
    "``FOLLOWS`` / ``STARRED`` / ``WATCHES`` / ``OPENED_ISSUE`` / "
    "``OPENED_PR`` / ``COMMENTED_ON`` / ``REVIEWED_PR``), and OpenSearch "
    "(GrimoireLab git_*_enriched / github_*_enriched indices). "
    "Key SPARQL predicates: ``schema:SoftwareSourceCode`` for repos; "
    "``schema:Person`` for users; ``org:Organization`` for orgs; "
    "``pulse:ownedBy`` (Repo → Org @id ``https://github.com/<handle>``); "
    "``pulse:githubOrganizationHandle`` (Org → GitHub login literal); "
    "``pulse:discipline`` (Repo → Wikidata Q-code); ``schema:citation`` "
    "(Repo → DOI URL or ScholarlyArticle); ``pulse:Contribution`` nodes "
    "carry ``pulse:contributionCount`` + first/last dates. GitHub repo "
    "internals live under ``gme-internal:`` (``bio``, ``location``, "
    "``company``, ``pushed_at``, ``keywords``, ``license_name``, ``archived``, "
    "etc.). When the user asks for a query, return a single fenced code "
    "block tagged with the engine (``sparql`` / ``cypher`` / ``opensearch``) "
    "and put a 1-2 sentence rationale outside the block. Prefer the "
    "canonical predicates above over guessing schema.org variants — they "
    "matter, the v3 extractor doesn't emit many older shorthand fields.\n\n"
    "Formatting requirements (strict — the UI relies on these):\n"
    "* ALWAYS open a code fence with the language tag matching the engine: "
    "```sparql``` for SPARQL, ```cypher``` for Cypher, ```json``` for "
    "OpenSearch DSL, ```sql``` for OpenSearch SQL. Untagged ``` blocks "
    "won't get syntax highlighting in the chat panel.\n"
    "* Use GitHub-flavoured Markdown for prose: tables with ``|`` for "
    "tabular data, ``**bold**`` for emphasis, ``- `` for lists. The "
    "chat renderer supports tables, lists, headings, blockquotes, "
    "task lists, and inline ``code``.\n"
    "* To VISUALISE data, emit a ```vega-lite fenced block containing a "
    "JSON Vega-Lite spec (or ```vega for full Vega) — the agent chat "
    "renders it as a live chart. You may also emit a ```html block "
    "(shown in a sandboxed iframe on demand) and Markdown images. When "
    "the user asks to plot / chart / visualise, prefer a vega-lite chart "
    "(embed the actual rows as inline ``data.values``) over an ASCII table. "
    "For a network / relationship graph use a full ```vega force-directed "
    "spec (not vega-lite); Vega's many-body (repulsion) force is named "
    "``nbody`` and the edge force is ``link`` — D3 names like ``charge`` are "
    "rejected.\n"
    "* Keep responses tight: 1-2 sentence preamble, the code block, "
    "then a 1-2 sentence follow-up if useful. The chat panel is "
    "420px wide so very long paragraphs hurt readability.\n\n"
    "Agentic mode: when tools (``run_sparql`` / ``run_cypher`` / "
    "``run_opensearch`` / ``run_duckdb``) are "
    "exposed in this turn, prefer calling them to verify your answer "
    "before quoting numbers. Use the result rows to refine the query "
    "if needed — you can chain up to a handful of tool calls before "
    "writing the final reply. Always summarise the findings in prose "
    "after the last tool call, citing the actual counts you observed. "
    "Stay read-only: any attempt at a write keyword (INSERT / DELETE / "
    "CREATE / MERGE / SET / …) is rejected by the guardrail before "
    "reaching the store, so don't try."
)


def _build_messages(
    user_messages: list[dict[str, Any]],
    context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Compose the conversation: system prompt + context envelope + history.

    ``context`` is folded into a single ``role: system`` message right
    after the static system prompt. Keeping it as a separate message
    (rather than spliced into the user's last turn) means the model
    sees the page state evolve across turns without the user having
    to repaste it.
    """
    out: list[dict[str, Any]] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if context:
        parts: list[str] = ["# Page context (auto-attached by the hub)"]
        engine = context.get("engine")
        if engine:
            parts.append(f"Active engine: **{engine}**")
        query = context.get("query")
        if query:
            parts.append("Current query in the editor:")
            parts.append(f"```{engine or 'text'}\n{query}\n```")
        named = context.get("named_graphs") or []
        if named:
            parts.append("SPARQL named graphs available:")
            for g in named[:12]:
                if isinstance(g, dict):
                    uri = g.get("uri", "")
                    n = g.get("triples")
                    parts.append(f"- `{uri}` ({n} triples)")
        schema = context.get("schema") or {}
        if schema:
            parts.append(_format_schema_block(schema))
        rows = context.get("preview_rows") or []
        if rows:
            parts.append("Sample rows from the current result table (truncated):")
            parts.append("```json")
            parts.append(json.dumps(rows, indent=2, default=str)[:4000])
            parts.append("```")
        note = context.get("note")
        if note:
            parts.append("User-provided context note:")
            parts.append(str(note)[:4000])
        files = context.get("files") or []
        if files:
            parts.append(
                "Files the user attached (read them with the ``read_file`` / "
                "``list_files`` tools, or query tabular ones with run_duckdb's "
                "``read_csv`` / ``read_parquet`` on the path):"
            )
            for f in files[:20]:
                if isinstance(f, dict):
                    parts.append(
                        f"- `{f.get('name', '')}` "
                        f"({f.get('size', '?')} bytes) → `{f.get('path', '')}`"
                    )
        out.append({"role": "system", "content": "\n\n".join(parts)})
    out.extend(user_messages)
    return out


def _merge_tool_call_delta(acc: list[dict[str, Any]], delta: dict[str, Any]) -> None:
    """Fold a streamed tool-call delta into the accumulator.

    OpenAI sends tool calls in pieces — first frame has ``id`` + name,
    later frames append to ``function.arguments`` as the JSON arrives
    token-by-token. We key by ``index`` (each parallel tool call has
    its own slot) so the merge is order-independent within a call.
    """
    idx = delta.get("index", 0)
    while len(acc) <= idx:
        acc.append(
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
        )
    slot = acc[idx]
    if delta.get("id"):
        slot["id"] = delta["id"]
    if delta.get("type"):
        slot["type"] = delta["type"]
    func = delta.get("function") or {}
    if func.get("name"):
        slot["function"]["name"] = func["name"]
    if func.get("arguments"):
        slot["function"]["arguments"] += func["arguments"]


def _finalize_tool_call(tc: dict[str, Any]) -> dict[str, Any]:
    """Strip the working state and return the OpenAI-shape tool_call."""
    return {
        "id": tc.get("id") or "",
        "type": tc.get("type") or "function",
        "function": {
            "name": (tc.get("function") or {}).get("name", ""),
            "arguments": (tc.get("function") or {}).get("arguments", ""),
        },
    }


def _execute_tool_safely(tc: dict[str, Any]) -> dict[str, Any]:
    """Run the tool, capturing every failure shape into the same envelope
    so the model always sees JSON (never a Python traceback)."""
    name = (tc.get("function") or {}).get("name", "")
    raw_args = (tc.get("function") or {}).get("arguments", "")
    try:
        args = json.loads(raw_args) if raw_args else {}
    except (TypeError, ValueError) as exc:
        return {"error": f"Could not parse tool arguments: {exc}"}
    try:
        return run_tool(name, args)
    except ToolError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — never propagate to the SSE stream
        log.exception("tool %s crashed", name)
        return {"error": f"Tool crashed: {exc}"}


def _synthetic_tool_frame(tc: dict[str, Any], result: dict[str, Any]) -> str:
    """Emit an SSE frame the chat widget renders as a tool-result card.

    The shape is intentionally distinct from a regular OpenAI delta so
    the client's frame parser can branch on ``op_tool``. We keep the
    payload small — the model already saw the full result via the
    ``role: tool`` message; this is purely for UI display.
    """
    preview = result
    rows = result.get("rows") if isinstance(result, dict) else None
    if isinstance(rows, list) and len(rows) > 25:
        preview = {**result, "rows": rows[:25], "_preview": True}
    body = {
        "op_tool": True,
        "tool_call_id": tc.get("id") or "",
        "name": (tc.get("function") or {}).get("name", ""),
        "arguments": (tc.get("function") or {}).get("arguments", ""),
        "result": preview,
    }
    return f"data: {json.dumps(body, default=str)}\n\n"


def _format_schema_block(schema: dict[str, Any]) -> str:
    """Render the schema dict as a compact Markdown block.

    Kept terse — every line ends up in the LLM's context window so
    we trade verbosity for hit-rate on actual decisions. Predicate /
    type / label lists are bounded by the introspection probe; we just
    layout them here.
    """
    out: list[str] = ["## Live schema (probed at chat-open, cached 10 min)"]
    sparql = schema.get("sparql") or {}
    if sparql:
        out.append("### SPARQL store")
        preds = sparql.get("top_predicates") or []
        if preds:
            out.append("Top predicates (by triple count):")
            for p in preds[:25]:
                out.append(f"- `{p['predicate']}` ({p['count']})")
        types = sparql.get("top_types") or []
        if types:
            out.append("Top RDF types (by distinct subject count):")
            for t in types[:15]:
                out.append(f"- `{t['type']}` ({t['count']})")
    neo = schema.get("neo4j") or {}
    if neo:
        out.append("### Neo4j store")
        labels = neo.get("label_counts") or []
        if labels:
            out.append(
                "Node labels: "
                + ", ".join(f"`{lbl['label']}` ({lbl['count']})" for lbl in labels[:12])
            )
        rels = neo.get("relationship_counts") or []
        if rels:
            out.append(
                "Relationship types: "
                + ", ".join(f"`{r['type']}` ({r['count']})" for r in rels[:20])
            )
        keys = neo.get("property_keys") or []
        if keys:
            out.append("Property keys: " + ", ".join(f"`{k}`" for k in keys[:40]))
    return "\n".join(out)


@router.post("/chat", dependencies=[Depends(require_auth)])
async def chat(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> StreamingResponse:
    """Stream a chat completion via SSE.

    Body shape: ``{"messages": […], "model": "…", "context": {…},
    "temperature": 0.2}``.  ``messages`` is the OpenAI-format
    conversation history (no system prompt — we add ours). ``context``
    is optional and folded into the system messages by ``_build_messages``.
    The response is a text/event-stream where each event's data is one
    raw OpenAI-style SSE frame from the upstream.
    """
    settings = get_settings()
    base = _base_url()
    user_messages = payload.get("messages") or []
    if not isinstance(user_messages, list) or not user_messages:
        raise HTTPException(status_code=400, detail="messages[] is required")
    model = (payload.get("model") or settings.llm_model or "").strip()
    if not model:
        raise HTTPException(
            status_code=503,
            detail="No model configured — set HUB_LLM_MODEL or pass `model` in the body.",
        )
    context = (
        payload.get("context") if isinstance(payload.get("context"), dict) else None
    )
    temperature = float(payload.get("temperature", 0.3))
    # ``tools_enabled`` defaults False so the existing chat UI (the one
    # that just renders text) keeps working unchanged. The agent path
    # only kicks in when the client opts in.
    tools_enabled = bool(payload.get("tools_enabled", False))
    # Optional per-tool selection — the "tools checkpoints" in the agent UI.
    # A list exposes only those tools; absent → all of TOOLS_SPEC; an empty
    # list disables tools for this turn.
    tool_names = payload.get("tool_names")
    if isinstance(tool_names, list):
        allow = {str(n) for n in tool_names}
    else:
        allow = None
    # Build the spec per-request so descriptions carry live context (e.g. the
    # real OpenSearch index names) — keeps the model from inventing indices.
    active_tools = runtime_tools_spec(allow)
    # How many chained tool rounds the agent may take this reply. Client
    # can raise/lower it in the UI; clamped to a hard ceiling so a runaway
    # loop can't melt the LLM budget.
    try:
        max_turns = int(payload.get("max_tool_turns") or MAX_TOOL_TURNS)
    except (TypeError, ValueError):
        max_turns = MAX_TOOL_TURNS
    max_turns = max(1, min(max_turns, MAX_TOOL_TURNS_CEILING))
    messages = _build_messages(user_messages, context)

    async def event_source():
        # When ``tools_enabled`` is on we run an agentic loop: stream
        # the LLM's reply to the client live, but also parse it
        # server-side. If the model finishes a turn with
        # ``finish_reason == "tool_calls"``, we execute each call,
        # synthesise SSE frames carrying the tool results (so the UI
        # can render them), append the assistant + tool messages, and
        # re-call the LLM. Bounded by ``MAX_TOOL_TURNS`` to keep a
        # runaway loop from melting the LLM budget.
        # Flush an initial comment so the proxy commits the 200 and starts
        # streaming immediately, before the first model token arrives.
        yield ": connected\n\n"
        try:
            async with httpx.AsyncClient(timeout=_CHAT_TIMEOUT) as client:
                for turn in range(max_turns + 1):
                    body = {
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "stream": True,
                    }
                    if tools_enabled and active_tools:
                        body["tools"] = active_tools
                        body["tool_choice"] = "auto"
                    async with client.stream(
                        "POST",
                        f"{base}/chat/completions",
                        headers=_headers(),
                        json=body,
                    ) as resp:
                        if resp.status_code != 200:
                            err = await resp.aread()
                            yield (
                                'data: {"error":'
                                + json.dumps(
                                    f"HTTP {resp.status_code}: "
                                    + err.decode("utf-8", errors="replace")[:400]
                                )
                                + "}\n\n"
                            )
                            return
                        # Accumulate the assistant's content + tool calls
                        # as we forward chunks. ``buf`` carries SSE-frame
                        # fragments across ``aiter_text`` chunks (the
                        # network boundary isn't aligned with ``\n\n``).
                        accumulated_content = ""
                        accumulated_tool_calls: list[dict[str, Any]] = []
                        finish_reason: str | None = None
                        buf = ""
                        async for chunk in resp.aiter_text():
                            yield chunk  # pass through to client first
                            buf += chunk
                            while "\n\n" in buf:
                                frame, buf = buf.split("\n\n", 1)
                                # Frame is a series of ``data: …`` /
                                # ``event: …`` lines; we only care about
                                # ``data:`` to read delta state.
                                payload_text = "".join(
                                    line[5:].strip()
                                    for line in frame.splitlines()
                                    if line.startswith("data:")
                                )
                                if not payload_text or payload_text == "[DONE]":
                                    continue
                                try:
                                    obj = json.loads(payload_text)
                                except ValueError:
                                    continue
                                choice = (obj.get("choices") or [{}])[0]
                                delta = choice.get("delta") or {}
                                if delta.get("content"):
                                    accumulated_content += delta["content"]
                                for tc in delta.get("tool_calls") or []:
                                    _merge_tool_call_delta(accumulated_tool_calls, tc)
                                if choice.get("finish_reason"):
                                    finish_reason = choice["finish_reason"]

                    if (
                        not tools_enabled
                        or finish_reason != "tool_calls"
                        or not accumulated_tool_calls
                    ):
                        return  # plain text reply (or final turn) — done
                    if turn >= max_turns:
                        yield (
                            'data: {"error":'
                            + json.dumps(
                                f"Tool-step budget exhausted after {max_turns} "
                                "rounds. The model is still asking to call tools — "
                                "raise 'Max tool steps' in ⚙ Agent & tools if you "
                                "need a longer chain."
                            )
                            + "}\n\n"
                        )
                        return
                    # Append the assistant turn (with its tool_calls) +
                    # one ``role: tool`` message per call result. The
                    # client also gets a synthetic SSE frame for each
                    # result so the chat panel can render it inline.
                    messages.append(
                        {
                            "role": "assistant",
                            "content": accumulated_content or None,
                            "tool_calls": [
                                _finalize_tool_call(tc) for tc in accumulated_tool_calls
                            ],
                        }
                    )
                    for tc in accumulated_tool_calls:
                        # Tools make blocking DB/HTTP calls (Neo4j, SPARQL,
                        # OpenSearch, DuckDB, GME). Run them off the event
                        # loop so a single-worker hub stays responsive — a
                        # synchronous call here freezes every other request
                        # (and the reverse proxy 502s) for the tool's whole
                        # duration. While it runs (gme_search can take ~20s),
                        # emit SSE keepalive comments so the connection never
                        # goes idle long enough for an intermediary proxy to
                        # drop it with a 502.
                        task = asyncio.ensure_future(
                            asyncio.to_thread(_execute_tool_safely, tc)
                        )
                        while not task.done():
                            done, _ = await asyncio.wait(
                                {task}, timeout=_HEARTBEAT_SECONDS
                            )
                            if not done:
                                yield ": keepalive\n\n"
                        result = task.result()
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.get("id") or "",
                                "content": json.dumps(result, default=str)[:32000],
                            }
                        )
                        yield _synthetic_tool_frame(tc, result)
        except httpx.HTTPError as exc:
            yield 'data: {"error":' + json.dumps(str(exc)) + "}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            # Disable response buffering on intermediaries (nginx/caddy)
            # so tokens reach the client immediately.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )


# ── Attached files (Agent chat) ─────────────────────────────────────────────
# Uploads land in AGENT_FILES_DIR (under /tmp — ephemeral). The agent reads
# them back through the read_file / list_files tools, or queries tabular ones
# with run_duckdb's read_csv / read_parquet on the returned path.

_MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB / file


def _sanitize_filename(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name or "").name).strip("._")
    return (base or "file")[:120]


@router.get("/files", dependencies=[Depends(require_auth)])
def list_agent_files() -> dict[str, Any]:
    """List the files attached to the agent chat (scratch dir under /tmp)."""
    files: list[dict[str, Any]] = []
    if AGENT_FILES_DIR.is_dir():
        for p in sorted(AGENT_FILES_DIR.iterdir()):
            if p.is_file():
                files.append(
                    {"name": p.name, "size": p.stat().st_size, "path": str(p)}
                )
    return {"files": files, "dir": str(AGENT_FILES_DIR)}


@router.post("/files", dependencies=[Depends(require_auth)])
async def upload_agent_file(file: UploadFile = File(...)) -> dict[str, Any]:
    """Save an uploaded file to the agent scratch dir, streaming to disk."""
    AGENT_FILES_DIR.mkdir(parents=True, exist_ok=True)
    name = _sanitize_filename(file.filename or "file")
    target = AGENT_FILES_DIR / name
    # Don't clobber a different existing upload — suffix the stem.
    if target.exists():
        stem = target.stem
        suffix = target.suffix
        i = 1
        while target.exists():
            name = f"{stem}-{i}{suffix}"
            target = AGENT_FILES_DIR / name
            i += 1
    size = 0
    try:
        with target.open("wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > _MAX_FILE_BYTES:
                    raise HTTPException(
                        status_code=413, detail="File too large (max 25 MB)."
                    )
                out.write(chunk)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    return {"name": name, "size": size, "path": str(target)}


@router.delete("/files/{name}", dependencies=[Depends(require_auth)])
def delete_agent_file(name: str) -> dict[str, str]:
    """Remove one attached file (name is confined to the scratch dir)."""
    base = AGENT_FILES_DIR.resolve()
    target = (base / Path(name).name).resolve()
    if base not in target.parents:
        raise HTTPException(status_code=400, detail="Invalid file name.")
    target.unlink(missing_ok=True)
    return {"status": "deleted", "name": target.name}
