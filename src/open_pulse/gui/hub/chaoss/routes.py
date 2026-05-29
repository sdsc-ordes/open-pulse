r"""HTTP wiring for the CHAOSS metrics surface.

The module also registers a tiny ``md`` Jinja filter that turns
markdown-flavoured inline syntax (``\`code\``, ``**bold**``,
``*italic*``) into HTML inside metric notes / methodology
paragraphs. That keeps the metrics module free to write notes with
natural backticks around field names like ``author_bot`` without the
template having to remember to wrap them in ``<code>`` tags by hand.

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
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Any

import html as _html
import re as _re

from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from ..auth import maybe_require_auth
from . import metrics as metrics_mod


def _md_inline(text: str | None) -> Markup:
    """Tiny inline-markdown → HTML filter.

    Handles only what notes / methodology paragraphs need:
    backtick-delimited inline code, ``**bold**`` and ``*italic*``.
    HTML in the source string is escaped first so the filter is safe
    to apply to anything that came out of a SPARQL / OpenSearch
    result without worrying about injection. Returns ``Markup`` so
    Jinja doesn't double-escape it.
    """
    if not text:
        return Markup("")
    s = _html.escape(str(text), quote=False)
    # Order matters: bold first (two stars) before italic (one star)
    # so ``**x**`` doesn't get mis-tokenised as italic-italic.
    s = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s, flags=_re.DOTALL)
    s = _re.sub(r"(?<![*_])\*([^*\n]+?)\*(?![*])", r"<em>\1</em>", s)
    # Double-backticks first (reST-style, used in our metrics module
    # for field names) so the single-backtick rule doesn't eat just
    # one side and break the pair.
    s = _re.sub(r"``([^`\n]+?)``", r"<code>\1</code>", s)
    s = _re.sub(r"`([^`\n]+?)`", r"<code>\1</code>", s)
    return Markup(s)


log = logging.getLogger(__name__)

_HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_HERE.parent / "templates"))
templates.env.filters["md"] = _md_inline

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
        # Two-person silhouette — keeps the icon readable at 18px.
        "icon": (
            "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z "
            "M2 21v-1a6 6 0 0 1 6-6h2a6 6 0 0 1 6 6v1 "
            "M16 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6z "
            "M22 21v-1a5 5 0 0 0-4-4.9"
        ),
    },
    {
        "name": "Software",
        "css": "pill-accent",
        "blurb": "What can users do with the code itself?",
        "url": "https://chaoss.community/kbtopic/software/",
        # Stacked package / box.
        "icon": (
            "M12 3l9 4.5-9 4.5-9-4.5 9-4.5z M3 12l9 4.5 9-4.5 M3 16.5l9 4.5 9-4.5"
        ),
    },
    {
        "name": "Lifecycle",
        "css": "pill-warn",
        "blurb": "How does work flow through the project?",
        "url": "https://chaoss.community/kbtopic/lifecycle/",
        # Circular arrow.
        "icon": ("M21 12a9 9 0 1 1-3-6.7 M21 4v5h-5"),
    },
    {
        "name": "Organization",
        # ``pill-neutral`` is the styled gray variant — using just
        # ``pill`` would render the chip without a background, since
        # the base ``.pill`` rule alone has no colour fill.
        "css": "pill-neutral",
        "blurb": "How are contributors organised across orgs?",
        "url": "https://chaoss.community/kbtopic/organization/",
        # Office building.
        "icon": ("M3 21V7l9-4 9 4v14 M9 21V13h6v8 M9 9h.01 M15 9h.01"),
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
    so we can build a 'Run query' deep link into the /databases page.
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


@router.get(
    "/chaoss", response_class=HTMLResponse, dependencies=[Depends(maybe_require_auth)]
)
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
        traces.append(
            {
                "store": t.store,
                "engine": t.engine,
                "mode": t.mode,
                "title": t.title,
                "query": t.query,
                "result_summary": t.result_summary,
                "error": t.error,
                "deep_link": _open_in_databases(t.engine, t.query, t.mode),
            }
        )
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


# ═════════════════════════════════════════════════════════════════════════
#                        JSON API · /api/chaoss/v1/*
# ═════════════════════════════════════════════════════════════════════════
#
# Path layout:
#     GET  /api/chaoss/v1/topics
#     GET  /api/chaoss/v1/metrics
#     GET  /api/chaoss/v1/metrics/{slug}
#     GET  /api/chaoss/v1/repositories/github.com/{owner}/{repo}/metrics
#     GET  /api/chaoss/v1/repositories/github.com/{owner}/{repo}/metrics/{slug}
#
# All routes return JSON. The repositories paths keep ``github.com`` as
# an explicit path segment so the URL pattern stays host-agnostic — a
# future ``gitlab.com/...`` resolver can slot in without breaking
# existing clients.

# Fields requested via ``?include=`` that aren't returned by default
# because they bulk up the payload.
_OPTIONAL_FIELDS = {"recipes", "traces", "series"}


def _parse_include(include: str | None) -> set[str]:
    """Parse ``?include=recipes,traces,series`` into a set, ignoring
    unknown tokens. Empty string / missing param → empty set."""
    if not include:
        return set()
    return {
        tok.strip().lower()
        for tok in include.split(",")
        if tok.strip().lower() in _OPTIONAL_FIELDS
    }


def _spec_to_dict(spec: metrics_mod.MetricSpec) -> dict[str, Any]:
    """Stable JSON shape for one MetricSpec entry. Used by /topics, /metrics
    and as the static-spec header on every per-repo result."""
    return {
        "slug": spec.slug,
        "name": spec.name,
        "category": spec.category,
        "question": spec.question,
        "description": spec.description,
        "chaoss_url": spec.chaoss_url,
        "chaoss_level": spec.chaoss_level,
        "is_time_based": spec.is_time_based,
    }


def _trace_to_dict(t: metrics_mod.QueryTrace) -> dict[str, Any]:
    return {
        "store": t.store,
        "engine": t.engine,
        "mode": t.mode,
        "title": t.title,
        "query": t.query,
        "result_summary": t.result_summary,
        "error": t.error,
        "deep_link": _open_in_databases(t.engine, t.query, t.mode),
    }


def _result_to_dict(
    spec: metrics_mod.MetricSpec,
    result: metrics_mod.MetricResult,
    include: set[str],
) -> dict[str, Any]:
    """Serialise a computed MetricResult. ``traces`` / ``recipes`` /
    ``series`` are omitted unless explicitly requested in ``include``."""
    payload: dict[str, Any] = {
        **_spec_to_dict(spec),
        "value": result.value,
        "label": result.label,
        "secondary": result.secondary,
        "headline_tone": result.headline_tone,
        "unification": result.unification or None,
        "notes": result.notes or None,
        "series_unit": result.series_unit,
        "visual": result.visual,
        "examples": result.examples or [],
    }
    if "series" in include and result.series:
        payload["series"] = result.series
    if "traces" in include and result.queries:
        payload["traces"] = [_trace_to_dict(t) for t in result.queries]
    if "recipes" in include and result.recipes:
        payload["recipes"] = result.recipes
    return payload


def _compute_one(
    slug: str, full: str, window: int
) -> tuple[metrics_mod.MetricSpec, metrics_mod.MetricResult]:
    """Look up the spec, compute the metric. Raises 404 for an unknown
    slug. Per-metric upstream failures are caught inside ``compute()``
    and surface as ``queries[*].error`` rather than as HTTP errors."""
    spec = metrics_mod.spec_for(slug)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown metric: {slug}")
    canonical = f"https://github.com/{full}"
    try:
        result = spec.compute(full, canonical, window)
    except Exception as exc:  # noqa: BLE001
        # Hard failure during compute (rather than per-trace) bubbles
        # up as a 500 with the error message attached.
        log.exception("metric %s failed for %s", slug, full)
        raise HTTPException(
            status_code=500,
            detail=f"compute failed for {slug} on {full}: {exc}",
        ) from exc
    return spec, result


@router.get(
    "/api/chaoss/v1/topics",
    dependencies=[Depends(maybe_require_auth)],
)
def chaoss_api_topics() -> dict[str, Any]:
    """List the 4 CHAOSS topic groups (Contributor / Software /
    Lifecycle / Organization) with metric counts."""
    grouped = _grouped(metrics_mod.REGISTRY)
    by_name = {g["name"]: len(g["metrics"]) for g in grouped}
    return {
        "topics": [
            {
                "name": cat["name"],
                "blurb": cat["blurb"],
                "css": cat["css"],
                "url": cat["url"],
                "metric_count": by_name.get(cat["name"], 0),
            }
            for cat in CATEGORIES
        ],
    }


@router.get(
    "/api/chaoss/v1/metrics",
    dependencies=[Depends(maybe_require_auth)],
)
def chaoss_api_metrics(
    category: str | None = Query(
        None,
        description="Filter to one topic — Contributor / Software / Lifecycle / Organization.",
    ),
) -> dict[str, Any]:
    """List every metric spec (catalogue). Pure static data — no
    upstream stores are touched. Optionally filtered by ``category``."""
    specs = metrics_mod.REGISTRY
    if category:
        specs = [m for m in specs if m.category.lower() == category.lower()]
    return {"metrics": [_spec_to_dict(m) for m in specs]}


@router.get(
    "/api/chaoss/v1/metrics/{slug}",
    dependencies=[Depends(maybe_require_auth)],
)
def chaoss_api_metric_spec(slug: str) -> dict[str, Any]:
    """One metric spec by slug. 404 if unknown."""
    spec = metrics_mod.spec_for(slug)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown metric: {slug}")
    return _spec_to_dict(spec)


@router.get(
    "/api/chaoss/v1/repositories/github.com/{owner}/{repo}/metrics",
    dependencies=[Depends(maybe_require_auth)],
)
def chaoss_api_repo_metrics(
    owner: str,
    repo: str,
    window: int = Query(DEFAULT_WINDOW_DAYS, ge=7, le=3650),
    category: str | None = Query(
        None, description="Compute only metrics in this topic."
    ),
    include: str | None = Query(
        None,
        description=(
            "Comma-separated optional fields to include: "
            "``traces``, ``recipes``, ``series``. Default omits all "
            "three to keep payloads small (recipes alone are ~130 KB "
            "for the full 22-metric set)."
        ),
    ),
) -> dict[str, Any]:
    """Compute every metric for a repository and return them as JSON.

    Per-metric upstream-store errors don't fail the request — they end
    up in that metric's ``traces[i].error`` (when ``include=traces``)
    and the metric reports a value of ``"—"``.
    """
    window = _clamp_window(window)
    full = f"{owner}/{repo}"
    fields = _parse_include(include)
    specs = metrics_mod.REGISTRY
    if category:
        specs = [m for m in specs if m.category.lower() == category.lower()]

    metrics_out: list[dict[str, Any]] = []
    for spec in specs:
        canonical = f"https://github.com/{full}"
        try:
            result = spec.compute(full, canonical, window)
        except Exception:  # noqa: BLE001
            # One blown-up metric mustn't take out the others.
            log.exception("metric %s failed for %s", spec.slug, full)
            metrics_out.append(
                {
                    **_spec_to_dict(spec),
                    "value": "—",
                    "label": "compute failed",
                    "secondary": None,
                    "headline_tone": "danger",
                    "unification": None,
                    "notes": None,
                    "series_unit": "events",
                    "visual": None,
                    "examples": [],
                    "error": "internal compute error",
                }
            )
            continue
        metrics_out.append(_result_to_dict(spec, result, fields))

    return {
        "repo": full,
        "canonical_url": f"https://github.com/{full}",
        "window_days": window,
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metric_count": len(metrics_out),
        "metrics": metrics_out,
    }


@router.get(
    "/api/chaoss/v1/repositories/github.com/{owner}/{repo}/metrics/{slug}",
    dependencies=[Depends(maybe_require_auth)],
)
def chaoss_api_repo_metric_one(
    owner: str,
    repo: str,
    slug: str,
    window: int = Query(DEFAULT_WINDOW_DAYS, ge=7, le=3650),
    include: str | None = Query(
        None,
        description="``traces``, ``recipes``, ``series`` (comma-separated).",
    ),
) -> dict[str, Any]:
    """Compute a single metric for a repository. Same payload shape as
    one element of the ``metrics`` array in the all-metrics endpoint."""
    window = _clamp_window(window)
    full = f"{owner}/{repo}"
    fields = _parse_include(include)
    spec, result = _compute_one(slug, full, window)
    return {
        "repo": full,
        "canonical_url": f"https://github.com/{full}",
        "window_days": window,
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **_result_to_dict(spec, result, fields),
    }
