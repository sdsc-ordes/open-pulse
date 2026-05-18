"""OpenAI-compatible chat completion — the "little agent".

A single ``narrate(entity)`` call composes a prompt from the entity's
facts + retrieved mentions and asks the configured LLM for a short
plain-English summary. Works against anything that speaks the OpenAI
chat-completions schema:

* OpenAI proper (``https://api.openai.com/v1``)
* OpenRouter (``https://openrouter.ai/api/v1``)
* Ollama (``http://localhost:11434/v1``)
* LM Studio, vLLM, llama.cpp server, …

Returns the empty string when no model is configured or the endpoint
is unreachable — every call site has to tolerate this because the
page must still render without the LLM.
"""

from __future__ import annotations

import logging

import httpx

from ..auth import get_settings
from .entity import Entity, Mention

log = logging.getLogger(__name__)

_AGENT_TIMEOUT = 30.0
_MAX_FACTS = 16
_MAX_MENTION_CHARS = 600


def narrate(entity: Entity) -> str:
    """Compose a short narrative paragraph for the entity page."""
    settings = get_settings()
    if not settings.llm_model:
        return ""

    prompt = _build_prompt(entity)
    payload = {
        "model": settings.llm_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the open-pulse research-knowledge assistant. "
                    "Write a concise one-paragraph summary of the resource "
                    "described by the user, grounded strictly in the facts "
                    "and excerpts provided. Avoid speculation; if a piece "
                    "is unknown, say so briefly."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 300,
    }
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    url = f"{settings.llm_base_url}/chat/completions"
    try:
        r = httpx.post(url, json=payload, headers=headers, timeout=_AGENT_TIMEOUT)
    except httpx.HTTPError as exc:
        log.info("agent request failed: %s", exc)
        return ""
    if r.status_code != 200:
        log.info("agent HTTP %s: %s", r.status_code, r.text[:200])
        return ""
    try:
        body = r.json()
    except ValueError:
        return ""
    choices = body.get("choices") or []
    if not choices:
        return ""
    msg = (choices[0].get("message") or {}).get("content") or ""
    return str(msg).strip()


def _build_prompt(entity: Entity) -> str:
    lines: list[str] = []
    lines.append(f"Resource: {entity.title or entity.ref_url}")
    if entity.kind:
        lines.append(f"Kind: {entity.kind}")
    if entity.ref_url:
        lines.append(f"Canonical URL: {entity.ref_url}")
    if entity.description:
        lines.append("Description:")
        lines.append(entity.description.strip())

    if entity.facts:
        lines.append("Facts:")
        for f in entity.facts[:_MAX_FACTS]:
            lines.append(f"- {f.label}: {f.value}")

    if entity.identifiers:
        lines.append("Cross-references:")
        for f in entity.identifiers[:_MAX_FACTS]:
            lines.append(f"- {f.label}: {f.value}")

    if entity.mentions:
        lines.append("Excerpts from the knowledge base:")
        for m in entity.mentions[:6]:
            lines.append(_format_mention(m))

    lines.append(
        "Write a single paragraph (3–5 sentences). Plain prose, no bullet list."
    )
    return "\n".join(lines)


def _format_mention(m: Mention) -> str:
    body = m.text.strip().replace("\n", " ")
    if len(body) > _MAX_MENTION_CHARS:
        body = body[:_MAX_MENTION_CHARS].rsplit(" ", 1)[0] + "…"
    src = m.source_label or m.source_url or m.collection
    return f"- [{src}] {body}" if body else f"- [{src}]"
