"""GrimoireLab / OpenSearch helper — the 4th data source.

The Grimoire pipelines index git commits, GitHub issues, PRs, and a
handful of community-health enrichments into OpenSearch. The hub
queries these for live activity numbers (commits / contributors /
recency) on entity pages — see ``/api/hub/activity/{ref}``.

We reuse the existing ``HUB_OPENSEARCH_*`` settings already plumbed
through :mod:`config`; no new env vars.

Index conventions we rely on (from inspecting the live stack):

* ``git_*_enriched`` — one document per commit, with ``origin``
  carrying the repository URL (term-indexed) and ``author_name``,
  ``date`` as keyword/date fields.
* ``github_*_enriched`` — issues / PRs / comments; ``origin`` again.

Everything degrades to :data:`None` on failure; the panel template
shows a "no activity data" message in that case.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..auth import get_settings
from .entity import ActivityStats

log = logging.getLogger(__name__)

_OS_TIMEOUT = 5.0
_GIT_INDEX_PATTERN = "git_*_enriched"


def _client() -> tuple[httpx.Client, tuple[str, str], bool] | None:
    settings = get_settings()
    if not settings.opensearch_url:
        return None
    auth = (settings.opensearch_username, settings.opensearch_password)
    verify = settings.opensearch_verify_tls
    return httpx.Client(timeout=_OS_TIMEOUT, verify=verify), auth, verify


def _post(path: str, body: dict[str, Any]) -> dict[str, Any] | None:
    pair = _client()
    if pair is None:
        return None
    client, auth, _ = pair
    settings = get_settings()
    url = f"{settings.opensearch_url.rstrip('/')}{path}"
    try:
        r = client.post(url, json=body, auth=auth, headers={"Content-Type": "application/json"})
    except httpx.HTTPError as exc:
        log.info("opensearch POST %s failed: %s", path, exc)
        return None
    finally:
        client.close()
    if r.status_code != 200:
        log.info("opensearch POST %s HTTP %s", path, r.status_code)
        return None
    try:
        return r.json()
    except ValueError:
        return None


def repo_activity(canonical_url: str) -> ActivityStats | None:
    """Return commit-level activity for a repository URL.

    Hits the git-enriched indices with a single aggregation query so
    the round-trip is short (one search call, no scrolls).
    """
    # The git ingest puts a typed ``date`` value at
    # ``grimoire_creation_date``; the ``date`` field on the document
    # isn't always mapped as a date type, so date-aggregations against
    # it silently return zero buckets.
    body = {
        "size": 0,
        "query": {"term": {"origin": canonical_url}},
        "aggs": {
            "contributors": {
                "cardinality": {"field": "author_name.keyword"}
            },
            "first": {"min": {"field": "grimoire_creation_date"}},
            "last": {"max": {"field": "grimoire_creation_date"}},
            "by_month": {
                "date_histogram": {
                    "field": "grimoire_creation_date",
                    "calendar_interval": "month",
                    "min_doc_count": 1,
                }
            },
        },
    }
    result = _post(f"/{_GIT_INDEX_PATTERN}/_search", body)
    if result is None:
        return None
    hits = (result.get("hits") or {}).get("total") or {}
    total = int(hits.get("value") or 0) if isinstance(hits, dict) else 0
    if total == 0:
        return ActivityStats()  # all zeros — caller can decide to hide

    aggs = result.get("aggregations") or {}
    contributors = int((aggs.get("contributors") or {}).get("value") or 0)
    first = ((aggs.get("first") or {}).get("value_as_string") or "")[:10]
    last = ((aggs.get("last") or {}).get("value_as_string") or "")[:10]
    buckets = (aggs.get("by_month") or {}).get("buckets") or []
    months = len(buckets)
    # Compact tuple of (yyyy-mm, count) for the sparkline. We keep
    # only the calendar bucket key so the front-end doesn't have to
    # parse full timestamps.
    monthly = tuple(
        (
            (b.get("key_as_string") or "")[:7],
            int(b.get("doc_count") or 0),
        )
        for b in buckets
        if b.get("doc_count")
    )
    return ActivityStats(
        total_commits=total,
        contributors=contributors,
        last_commit_date=last,
        first_commit_date=first,
        active_months=months,
        monthly=monthly,
    )
