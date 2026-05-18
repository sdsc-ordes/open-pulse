"""HTTP wiring for the CHAOSS metrics surface.

Three handlers live here:

* ``GET /chaoss`` – landing page with a repo selector + the metric
  catalogue. Anonymous-friendly when ``HUB_PUBLIC_KNOWLEDGE`` is on.
* ``GET /chaoss/github.com/{owner}/{repo}`` – per-repository
  dashboard. Renders the metric cards as skeletons; each card lazy-
  loads its value via the API below so a slow store (e.g. SPARQL on
  a cold start) doesn't block the rest of the page.
* ``GET /api/chaoss/github.com/{owner}/{repo}/{slug}`` – HTML
  fragment that renders one metric card with the live value, the
  queries that produced it, and any examples.

The route prefixes match the existing ``/hub/...`` hub URL pattern so
the chain breadcrumb keeps working across surfaces — when a visitor
clicks a repository in /hub and then jumps to its CHAOSS page, the
trail stays continuous.
"""

from __future__ import annotations

import logging
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Any

from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..auth import maybe_require_auth
from . import metrics as metrics_mod

log = logging.getLogger(__name__)

_HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_HERE.parent / "templates"))

router = APIRouter(tags=["chaoss"])

# Default time window for the time-based metrics. The page lets the
# user override it via ?window=<days>.
DEFAULT_WINDOW_DAYS = 365
# 30 day / 90 day / 6 month / 1 year / 2 year / 5 year / 10 year — the
# longer choices matter for older repos whose only contributors were
# before the standard "last year" window.
ALLOWED_WINDOWS = (30, 90, 180, 365, 730, 1825, 3650)

# Topic axis: the official CHAOSS taxonomy used to browse the
# catalogue at https://chaoss.community/kbtopic/. We surface the four
# topics that have at least one open-pulse metric implemented. The
# ``css`` field maps to the existing pill classes in app.css; the
# ``url`` jumps the visitor straight to the CHAOSS catalogue's
# corresponding topic page.
CATEGORIES: tuple[dict[str, str], ...] = (
    {
        "name": "Contributor",
        "css": "pill-info",
        "blurb": "Who builds and maintains the project?",
        "url": "https://chaoss.community/kbtopic/contributor/",
    },
    {
        "name": "Software",
        "css": "pill-accent",
        "blurb": "What can users do with the code itself?",
        "url": "https://chaoss.community/kbtopic/software/",
    },
    {
        "name": "Lifecycle",
        "css": "pill-warn",
        "blurb": "How does work flow through the project?",
        "url": "https://chaoss.community/kbtopic/lifecycle/",
    },
    {
        "name": "Organization",
        "css": "pill",
        "blurb": "How are contributors organised across orgs?",
        "url": "https://chaoss.community/kbtopic/organization/",
    },
)


def _grouped(specs: list) -> list[dict[str, Any]]:
    """Re-shape the flat metrics registry into the order CATEGORIES
    declares so templates can render one section per topic.
    """
    out = []
    for cat in CATEGORIES:
        bucket = [m for m in specs if m.category == cat["name"]]
        if bucket:
            out.append({**cat, "metrics": bucket})
    return out


def _clamp_window(value: int) -> int:
    """Snap an arbitrary number of days to one of the offered choices."""
    if value in ALLOWED_WINDOWS:
        return value
    return min(ALLOWED_WINDOWS, key=lambda w: abs(w - value))


def _engine_to_databases_tab(engine: str) -> str:
    """Map a metric's engine id onto the URL the /databases page wants
    so we can build a 'Open in /databases' deep link.
    """
    return {
        "cypher": "cypher",
        "sparql": "sparql",
        "opensearch": "opensearch",
    }.get(engine, "cypher")


def _open_in_databases(engine: str, query: str, mode: str | None = None) -> str:
    """Build a `/databases#…` deep link that pre-fills the editor.

    The /databases page reads ``engine`` / ``q`` / ``os_mode`` from the
    hash fragment; storing them in the fragment (not the query string)
    keeps long bodies out of the URL bar's history while still being
    shareable.
    """
    parts = {"engine": _engine_to_databases_tab(engine), "q": query}
    if mode:
        parts["os_mode"] = mode
    return "/databases#" + urllib.parse.urlencode(parts)


@router.get("/chaoss", response_class=HTMLResponse, dependencies=[Depends(maybe_require_auth)])
def chaoss_landing(request: Request) -> HTMLResponse:
    """Catalogue + repo entry point."""
    return templates.TemplateResponse(
        request,
        "chaoss/landing.html",
        {
            "page": "chaoss",
            "metrics": metrics_mod.REGISTRY,
            "groups": _grouped(metrics_mod.REGISTRY),
            "categories": CATEGORIES,
            "window_choices": ALLOWED_WINDOWS,
            "default_window": DEFAULT_WINDOW_DAYS,
        },
    )


@router.get(
    "/chaoss/github.com/{owner}/{repo}",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def chaoss_repo(
    request: Request,
    owner: str,
    repo: str,
    window: int = Query(DEFAULT_WINDOW_DAYS, ge=7, le=3650),
) -> HTMLResponse:
    """Per-repo dashboard. The five metric cards are skeleton-rendered
    and fill in via the API endpoint below — keeps cold-start latency
    bounded by the slowest store.
    """
    window = _clamp_window(window)
    full = f"{owner}/{repo}"
    return templates.TemplateResponse(
        request,
        "chaoss/repo.html",
        {
            "page": "chaoss",
            "owner": owner,
            "repo": repo,
            "full": full,
            "canonical_url": f"https://github.com/{full}",
            "metrics": metrics_mod.REGISTRY,
            "groups": _grouped(metrics_mod.REGISTRY),
            "categories": CATEGORIES,
            "window": window,
            "window_choices": ALLOWED_WINDOWS,
        },
    )


@router.get(
    "/api/chaoss/github.com/{owner}/{repo}/{slug}",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def chaoss_metric_card(
    request: Request,
    owner: str,
    repo: str,
    slug: str,
    window: int = Query(DEFAULT_WINDOW_DAYS, ge=7, le=3650),
) -> HTMLResponse:
    """One metric, fully rendered. Called by the skeleton on the repo
    page; returns the inner HTML of the card so Alpine can swap it in.
    """
    spec = metrics_mod.spec_for(slug)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown metric: {slug}")
    window = _clamp_window(window)
    full = f"{owner}/{repo}"
    canonical = f"https://github.com/{full}"
    try:
        result = spec.compute(full, canonical, window)
    except Exception as exc:  # noqa: BLE001
        log.exception("metric %s failed for %s", slug, full)
        return templates.TemplateResponse(
            request,
            "chaoss/_metric_error.html",
            {
                "spec": spec,
                "error": str(exc),
            },
            status_code=200,
        )
    # Attach a /databases deep-link to every trace.
    traces = []
    for t in result.queries:
        traces.append({
            "store": t.store,
            "engine": t.engine,
            "mode": t.mode,
            "title": t.title,
            "query": t.query,
            "result_summary": t.result_summary,
            "error": t.error,
            "deep_link": _open_in_databases(t.engine, t.query, t.mode),
        })
    return templates.TemplateResponse(
        request,
        "chaoss/_metric_card_body.html",
        {
            "spec": spec,
            "result": result,
            "traces": traces,
            "window": window,
        },
    )
