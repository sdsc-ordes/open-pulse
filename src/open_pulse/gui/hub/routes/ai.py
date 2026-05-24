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

import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..auth import get_settings, require_auth

router = APIRouter(prefix="/api/ai", tags=["ai"])
log = logging.getLogger(__name__)

_MODELS_TIMEOUT = 10.0
_CHAT_TIMEOUT = 600.0


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
    "* Keep responses tight: 1-2 sentence preamble, the code block, "
    "then a 1-2 sentence follow-up if useful. The chat panel is "
    "420px wide so very long paragraphs hurt readability."
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
        rows = context.get("preview_rows") or []
        if rows:
            parts.append("Sample rows from the current result table (truncated):")
            parts.append("```json")
            parts.append(json.dumps(rows, indent=2, default=str)[:4000])
            parts.append("```")
        out.append({"role": "system", "content": "\n\n".join(parts)})
    out.extend(user_messages)
    return out


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
    upstream_payload = {
        "model": model,
        "messages": _build_messages(user_messages, context),
        "temperature": temperature,
        "stream": True,
    }

    async def event_source():
        # Forward the upstream SSE stream chunk-by-chunk. We don't try
        # to parse — the browser-side handler expects raw OpenAI delta
        # frames so the chat widget can render token streaming as-is.
        try:
            async with httpx.AsyncClient(timeout=_CHAT_TIMEOUT) as client:
                async with client.stream(
                    "POST",
                    f"{base}/chat/completions",
                    headers=_headers(),
                    json=upstream_payload,
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
                    async for chunk in resp.aiter_text():
                        yield chunk
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
