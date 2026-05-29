"""Trigger crawler + GME extractor jobs for a URL.

When the hub shows a "not yet enriched" placeholder (or a Connected-
on-GitHub item marked ``not yet indexed``), the visitor can click an
Enrich button which calls this module. The crawler does a one-hop
BFS into Neo4j; GME does a v2 extract that lands JSON-LD in the
SPARQL store + chunks in gme-qdrant. The two run independently — the
hub fires them off and immediately returns the job IDs.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

_TIMEOUT = 30.0
"""Both crawler and GME return their job_id in under a second once
warm, but the GME extract path can take 5–15 s on a cold container
(DNS + LLM-provider client init). The hub returns the job IDs as
soon as the upstream responds, so a slower-than-default timeout
just buys us reliability on first hit."""

# Service URLs are stable within the compose network. The hub env
# could one day override these via HUB_CRAWLER_URL / HUB_GME_URL but
# the defaults match the docker-compose service names so no extra
# config is needed today.
_CRAWLER_URL = os.environ.get("HUB_CRAWLER_URL", "http://open-pulse-crawler:8000")
_GME_URL = os.environ.get("HUB_GME_URL", "http://git-metadata-extractor:1234")


@dataclass(frozen=True)
class EnrichResult:
    """Outcome of an enrich request. Both job IDs are optional —
    each call can succeed independently."""

    crawler_job_id: str = ""
    gme_job_id: str = ""
    crawler_error: str = ""
    gme_error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.crawler_job_id or self.gme_job_id)


def _bearer(env_var: str) -> dict[str, str]:
    token = (os.environ.get(env_var) or "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _enqueue_crawl(canonical_url: str) -> tuple[str, str]:
    """POST /api/v1/crawl. Returns (job_id, error_message)."""
    url = f"{_CRAWLER_URL.rstrip('/')}/api/v1/crawl"
    body = {
        "seeds": [canonical_url],
        "max_rounds": 1,
        "crawl_dependencies": False,
        "crawl_dependents": False,
    }
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.post(url, json=body, headers=_bearer("CRAWLER_API_TOKEN"))
    except httpx.HTTPError as exc:
        return "", f"crawler unreachable: {exc}"
    if r.status_code not in (200, 201, 202):
        return "", f"crawler HTTP {r.status_code}: {r.text[:160]}"
    try:
        payload = r.json()
    except ValueError:
        return "", "crawler returned non-JSON"
    job_id = payload.get("job_id") or payload.get("id") or ""
    return str(job_id), ""


def _enqueue_gme(canonical_url: str, *, agent_runtime: str = "llm") -> tuple[str, str]:
    """POST /v2/extract. Returns (job_id, error_message).

    ``agent_runtime`` defaults to the LLM path so we get rich
    JSON-LD even for repos without a gimie-friendly README; callers
    can pass ``rule_based`` to skip the LLM dependency.
    """
    url = f"{_GME_URL.rstrip('/')}/v2/extract"
    body = {
        "source_url": canonical_url,
        "agent_runtime": agent_runtime,
        "output_format": "jsonld",
    }
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.post(url, json=body, headers=_bearer("EXTRACTOR_API_TOKEN"))
    except httpx.HTTPError as exc:
        return "", f"extractor unreachable: {exc}"
    if r.status_code not in (200, 201, 202):
        return "", f"extractor HTTP {r.status_code}: {r.text[:160]}"
    try:
        payload = r.json()
    except ValueError:
        return "", "extractor returned non-JSON"
    job_id = payload.get("job_id") or payload.get("id") or ""
    return str(job_id), ""


def enrich(canonical_url: str) -> EnrichResult:
    """Kick off both crawler and GME jobs for the URL.

    Fires the two HTTP calls sequentially (each is non-blocking on
    the upstream side — both return immediately with a job ID).
    Each side's failure is reported independently so the visitor can
    see partial success.
    """
    log.info("enrich requested for %s", canonical_url)
    crawler_id, crawler_err = _enqueue_crawl(canonical_url)
    gme_id, gme_err = _enqueue_gme(canonical_url)
    return EnrichResult(
        crawler_job_id=crawler_id,
        gme_job_id=gme_id,
        crawler_error=crawler_err,
        gme_error=gme_err,
    )
