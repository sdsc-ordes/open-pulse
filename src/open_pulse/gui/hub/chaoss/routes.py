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
import os
import time
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

from concurrent.futures import ThreadPoolExecutor

from ..auth import maybe_require_auth
from . import metrics as metrics_mod
from . import projects as projects_mod


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

# Presentation axis: three plain-language buckets that answer the
# question a visitor actually has when they land on a repo. Each maps
# onto several CHAOSS topics under the hood — the original CHAOSS topic
# of a metric still lives on ``MetricSpec.category`` and links out to the
# catalogue per-card. The ``css`` field maps to the existing pill classes
# in app.css.
CATEGORIES: tuple[dict[str, str], ...] = (
    {
        "name": "Community",
        "css": "pill-info",
        "blurb": "Is the project alive & kicking?",
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
        "name": "Popularity",
        "css": "pill-accent",
        "blurb": "Who sees, uses & reuses it?",
        "url": "https://chaoss.community/kbtopic/software/",
        # Upward star / spark.
        "icon": (
            "M12 2l2.9 6.3 6.9.8-5.1 4.7 1.4 6.8L12 17.8 "
            "5.9 21.4l1.4-6.8L2.2 9.9l6.9-.8L12 2z"
        ),
    },
    {
        "name": "Quality",
        "css": "pill-warn",
        "blurb": "Can others understand & reuse it?",
        "url": "https://chaoss.community/kbtopic/common/",
        # Shield-check.
        "icon": ("M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z M9 12l2 2 4-4"),
    },
)

# Per-slug presentation bucket. The MetricSpec keeps its original CHAOSS
# topic (Contributor / Software / Lifecycle / Organization) for the
# per-card catalogue link; this maps each metric onto one of the three
# visitor-facing buckets above. Anything not listed falls back to
# "Community" via ``_category``.
_SLUG_CATEGORY: dict[str, str] = {
    # Popularity — who sees / uses / reuses the work.
    "technical_fork": "Popularity",
    "academic_impact": "Popularity",
    "project_popularity": "Popularity",
    # Quality — can others understand, review and legally reuse it.
    "licenses_declared": "Quality",
    "programming_languages": "Quality",
    "code_lines": "Quality",
    "self_merge": "Quality",
    "bot_activity": "Quality",
    "cr_reviews": "Quality",
    "cr_accepted": "Quality",
    "cr_declined": "Quality",
    "upstream_dependencies": "Quality",
    "docs_discoverability": "Quality",
    "license_coverage": "Quality",
    "test_coverage": "Quality",
    "release_frequency": "Quality",
    # Everything else (contributors, activity, responsiveness, issues,
    # bus-factor, review duration) is Community — handled by the default.
}


def _category(spec) -> str:
    """Visitor-facing bucket for a metric (Community / Popularity / Quality)."""
    return _SLUG_CATEGORY.get(spec.slug, "Community")


def _grouped(specs: list) -> list[dict[str, Any]]:
    """Re-shape the flat metrics registry into the order CATEGORIES
    declares so templates can render one section per bucket.
    """
    out = []
    for cat in CATEGORIES:
        bucket = [m for m in specs if _category(m) == cat["name"]]
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


# Engines the /databases console can actually run. Index reads (DuckDB)
# are transparent in the trace but have no console tab, so they get no
# "Run query" deep link.
_CONSOLE_ENGINES = {"cypher", "sparql", "opensearch"}


def _deep_link(engine: str, query: str, mode: str | None = None) -> str | None:
    """Console deep link for a trace, or None when the engine isn't one
    the /databases page can execute (e.g. ``duckdb`` index reads)."""
    if engine not in _CONSOLE_ENGINES:
        return None
    return _open_in_databases(engine, query, mode)


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
            "projects": projects_mod.list_projects(),
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
    "/chaoss/projects/{project}",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def chaoss_project_dashboard(
    request: Request,
    project: str,
    window: int = Query(DEFAULT_WINDOW_DAYS, ge=7, le=3650),
) -> HTMLResponse:
    """Project dashboard — CHAOSS metrics aggregated across a GrimoireLab
    project's repos. Cards fill in from the JSON API (cached per project)."""
    repos = projects_mod.resolve_project_repos(project)
    if repos is None:
        raise HTTPException(status_code=404, detail=f"unknown project: {project}")
    window = _clamp_window(window)
    title = next(
        (p["title"] for p in projects_mod.list_projects() if p["project"] == project),
        project,
    )
    return templates.TemplateResponse(
        request,
        "chaoss/project.html",
        {
            "page": "chaoss",
            "project": project,
            "title": title,
            "repo_count": len(repos),
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
                "deep_link": _deep_link(t.engine, t.query, t.mode),
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
        "category": _category(spec),
        # ``chaoss_topic`` keeps the underlying CHAOSS taxonomy term for
        # anyone who wants it; the visitor-facing bucket is ``category``.
        "chaoss_topic": spec.category,
        "question": spec.question,
        "description": spec.description,
        "chaoss_url": spec.chaoss_url,
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
        "deep_link": _deep_link(t.engine, t.query, t.mode),
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


@router.get("/api/v1/metrics/chaoss/topics", dependencies=[Depends(maybe_require_auth)])
@router.get(
    "/api/chaoss/v1/topics",  # deprecated alias
    dependencies=[Depends(maybe_require_auth)],
)
def chaoss_api_topics() -> dict[str, Any]:
    """List the 3 metric buckets (Community / Popularity / Quality)
    with metric counts."""
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


@router.get("/api/v1/metrics/chaoss", dependencies=[Depends(maybe_require_auth)])
@router.get(
    "/api/chaoss/v1/metrics",  # deprecated alias
    dependencies=[Depends(maybe_require_auth)],
)
def chaoss_api_metrics(
    category: str | None = Query(
        None,
        description="Filter to one bucket — Community / Popularity / Quality.",
    ),
) -> dict[str, Any]:
    """List every metric spec (catalogue). Pure static data — no
    upstream stores are touched. Optionally filtered by ``category``."""
    specs = metrics_mod.REGISTRY
    if category:
        specs = [m for m in specs if _category(m).lower() == category.lower()]
    return {"metrics": [_spec_to_dict(m) for m in specs]}


@router.get(
    "/api/v1/metrics/chaoss/metrics/{slug}", dependencies=[Depends(maybe_require_auth)]
)
@router.get(
    "/api/chaoss/v1/metrics/{slug}",  # deprecated alias
    dependencies=[Depends(maybe_require_auth)],
)
def chaoss_api_metric_spec(slug: str) -> dict[str, Any]:
    """One metric spec by slug. 404 if unknown."""
    spec = metrics_mod.spec_for(slug)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown metric: {slug}")
    return _spec_to_dict(spec)


@router.get(
    "/api/v1/metrics/chaoss/repositories/github.com/{owner}/{repo}/metrics",
    dependencies=[Depends(maybe_require_auth)],
)
@router.get(
    "/api/chaoss/v1/repositories/github.com/{owner}/{repo}/metrics",  # deprecated alias
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
        specs = [m for m in specs if _category(m).lower() == category.lower()]

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
    "/api/v1/metrics/chaoss/repositories/github.com/{owner}/{repo}/metrics/{slug}",
    dependencies=[Depends(maybe_require_auth)],
)
@router.get(
    "/api/chaoss/v1/repositories/github.com/{owner}/{repo}/metrics/{slug}",  # alias
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


# ═════════════════════════════════════════════════════════════════════════
#         Project-scoped metrics · /api/v1/metrics/chaoss/projects/*
# ═════════════════════════════════════════════════════════════════════════
#
# A GrimoireLab "project" (projects.json) is a named set of repos. We compute
# the per-repo metric across every repo in the project (in parallel) and return
# both the exact per-repo breakdown and a labelled roll-up.

# Headline roll-up rule per metric. Default "sum" (additive counts); ratios
# average. Distinct-people counts are summed but flagged ``approx`` — a
# contributor active in N repos is counted N times (true dedup needs a
# project-native query, a follow-up).
_AGG_RULE: dict[str, str] = {
    "closure_ratio": "mean",
    "technical_fork": "mean",
    # Avg direct deps per repo; donut-fraction metrics average to a share.
    "upstream_dependencies": "mean",
    "docs_discoverability": "mean",
    "license_coverage": "mean",
    "test_coverage": "mean",
    "release_frequency": "mean",
    # Median response time averages across repos; committers is a distinct
    # head-count (summed, flagged approx below).
    "issue_response_time": "mean",
}
_AGG_APPROX = {"contributors", "new_contributors", "org_diversity", "committers"}
# Bound on fan-out per request; larger projects are truncated (reported)
# until snapshot caching lands.
_PROJECT_REPO_CAP = 150
_PROJECT_WORKERS = 4


def _metric_numeric(r: metrics_mod.MetricResult) -> float | None:
    """Best-effort headline number from a MetricResult, for aggregation.
    Prefers the structured ``visual`` payload; falls back to parsing the
    display ``value``. None for non-numeric metrics (e.g. a license name)."""
    v = r.visual or {}
    kind = v.get("kind")
    if kind == "stacked_bar":
        nums = [s.get("value") for s in (v.get("segments") or [])
                if isinstance(s.get("value"), (int, float))]
        return float(sum(nums)) if nums else None
    if kind == "rank_bars":
        nums = [i.get("value") for i in (v.get("items") or [])
                if isinstance(i.get("value"), (int, float))]
        return float(sum(nums)) if nums else None
    if kind == "donut" and isinstance(v.get("fraction"), (int, float)):
        return float(v["fraction"])
    s = (r.value or "").strip().replace(",", "")
    if not s or s in {"—", "-", "n/a", "N/A"}:
        return None
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0
        except ValueError:
            return None
    m = _re.match(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


def _project_or_404(project: str) -> list[str]:
    repos = projects_mod.resolve_project_repos(project)
    if repos is None:
        raise HTTPException(status_code=404, detail=f"unknown project: {project}")
    if not repos:
        raise HTTPException(
            status_code=404, detail=f"project {project!r} has no github repositories"
        )
    return repos


def _compute_repo_specs(full: str, window: int, specs: list) -> dict:
    """Compute ``specs`` for one repo; per-metric failures become ``None``."""
    out: dict[str, Any] = {}
    canonical = f"https://github.com/{full}"
    for spec in specs:
        try:
            out[spec.slug] = spec.compute(full, canonical, window)
        except Exception:  # noqa: BLE001
            log.exception("metric %s failed for %s", spec.slug, full)
            out[spec.slug] = None
    return out


def _aggregate(slug: str, per_repo: list[tuple[str, float | None]]) -> dict[str, Any]:
    """Roll up one metric's per-repo numbers into a labelled aggregate."""
    nums = [n for _, n in per_repo if n is not None]
    rule = _AGG_RULE.get(slug, "sum")
    agg: dict[str, Any] = {
        "rule": rule,
        "n_repos": len(per_repo),
        "n_with_value": len(nums),
        "sum": sum(nums) if nums else None,
        "mean": (sum(nums) / len(nums)) if nums else None,
        "min": min(nums) if nums else None,
        "max": max(nums) if nums else None,
    }
    agg["value"] = agg.get(rule)
    if slug in _AGG_APPROX:
        agg["approx"] = True
        agg["approx_note"] = (
            "summed per-repo distinct counts; someone active in N repos is "
            "counted N times (true dedup needs a project-native query)"
        )
    return agg


def _compute_project(window: int, repos: list[str], specs: list) -> list[tuple[str, dict]]:
    """Compute ``specs`` for every repo in parallel. Returns ``[(full, {slug: result})]``."""
    def _one(full: str) -> tuple[str, dict]:
        return full, _compute_repo_specs(full, window, specs)

    with ThreadPoolExecutor(max_workers=_PROJECT_WORKERS) as ex:
        return list(ex.map(_one, repos))


# In-process TTL cache for the (expensive) per-project compute, keyed by
# (project, window, slugs). The lazy-loading UI re-hits the same project and
# the all-metrics view re-runs on each visit, so repeats return instantly
# within the TTL. Tune/disable via ``CHAOSS_PROJECT_CACHE_TTL_S`` (0 = off).
_PROJECT_CACHE_TTL = float(os.environ.get("CHAOSS_PROJECT_CACHE_TTL_S", "1800"))
_PROJECT_CACHE: dict[tuple, tuple[float, Any]] = {}


def _compute_project_cached(
    project: str, window: int, repos: list[str], specs: list, *, refresh: bool = False
) -> tuple[list[tuple[str, dict]], str | None]:
    """``_compute_project`` behind a TTL cache. Returns ``(computed, cached_at)``
    where ``cached_at`` is the cache timestamp on a hit, else ``None``."""
    if _PROJECT_CACHE_TTL <= 0:
        return _compute_project(window, repos, specs), None
    key = (project, window, tuple(s.slug for s in specs))
    now = time.time()
    hit = _PROJECT_CACHE.get(key)
    if hit and not refresh and hit[0] + _PROJECT_CACHE_TTL > now:
        return hit[1], datetime.fromtimestamp(hit[0], timezone.utc).isoformat(
            timespec="seconds"
        )
    computed = _compute_project(window, repos, specs)
    _PROJECT_CACHE[key] = (now, computed)
    if len(_PROJECT_CACHE) > 256:  # crude size bound — evict the oldest
        for k, _ in sorted(_PROJECT_CACHE.items(), key=lambda kv: kv[1][0])[:64]:
            _PROJECT_CACHE.pop(k, None)
    return computed, None


@router.get(
    "/api/v1/metrics/chaoss/projects", dependencies=[Depends(maybe_require_auth)]
)
def chaoss_api_projects() -> dict[str, Any]:
    """List GrimoireLab projects (projects.json) with their github repo counts."""
    return {"projects": projects_mod.list_projects()}


@router.get(
    "/api/v1/metrics/chaoss/projects/{project}/metrics",
    dependencies=[Depends(maybe_require_auth)],
)
def chaoss_api_project_metrics(
    project: str,
    window: int = Query(DEFAULT_WINDOW_DAYS, ge=7, le=3650),
    category: str | None = Query(None, description="Compute only this topic."),
    refresh: bool = Query(False, description="Bypass the project metric cache."),
) -> dict[str, Any]:
    """Every CHAOSS metric aggregated across a GrimoireLab project's repos."""
    repos = _project_or_404(project)
    truncated = len(repos) > _PROJECT_REPO_CAP
    repos = repos[:_PROJECT_REPO_CAP]
    window = _clamp_window(window)
    specs = metrics_mod.REGISTRY
    if category:
        specs = [m for m in specs if _category(m).lower() == category.lower()]
    computed, cached_at = _compute_project_cached(
        project, window, repos, specs, refresh=refresh
    )
    metrics_out: list[dict[str, Any]] = []
    for spec in specs:
        per_repo = [(full, _metric_numeric(by[spec.slug]) if by.get(spec.slug) else None)
                    for full, by in computed]
        metrics_out.append({**_spec_to_dict(spec), "aggregate": _aggregate(spec.slug, per_repo)})
    return {
        "project": project,
        "repo_count": len(repos),
        "truncated": truncated,
        "window_days": window,
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cached_at": cached_at,
        "metric_count": len(metrics_out),
        "metrics": metrics_out,
    }


@router.get(
    "/api/v1/metrics/chaoss/projects/{project}/metrics/{slug}",
    dependencies=[Depends(maybe_require_auth)],
)
def chaoss_api_project_metric_one(
    project: str,
    slug: str,
    window: int = Query(DEFAULT_WINDOW_DAYS, ge=7, le=3650),
    refresh: bool = Query(False, description="Bypass the project metric cache."),
) -> dict[str, Any]:
    """One CHAOSS metric across a project: aggregate + per-repo breakdown."""
    spec = metrics_mod.spec_for(slug)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown metric: {slug}")
    repos = _project_or_404(project)
    truncated = len(repos) > _PROJECT_REPO_CAP
    repos = repos[:_PROJECT_REPO_CAP]
    window = _clamp_window(window)
    computed, cached_at = _compute_project_cached(
        project, window, repos, [spec], refresh=refresh
    )
    per_repo: list[tuple[str, float | None]] = []
    rows: list[dict[str, Any]] = []
    for full, by in computed:
        res = by.get(slug)
        num = _metric_numeric(res) if res else None
        per_repo.append((full, num))
        rows.append({"repo": full, "value": res.value if res else "—", "numeric": num})
    rows.sort(key=lambda r: (r["numeric"] is None, -(r["numeric"] or 0)))
    return {
        "project": project,
        **_spec_to_dict(spec),
        "window_days": window,
        "repo_count": len(repos),
        "truncated": truncated,
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cached_at": cached_at,
        "aggregate": _aggregate(slug, per_repo),
        "repositories": rows,
    }


@router.get(
    "/api/v1/metrics/chaoss/projects/{project}/repositories",
    dependencies=[Depends(maybe_require_auth)],
)
def chaoss_api_project_repositories(
    project: str,
    slug: str | None = Query(None, description="One metric (default: all)."),
    window: int = Query(DEFAULT_WINDOW_DAYS, ge=7, le=3650),
    category: str | None = Query(None),
    refresh: bool = Query(False, description="Bypass the project metric cache."),
) -> dict[str, Any]:
    """Per-repo metric matrix for a project — for ranking / comparison."""
    repos = _project_or_404(project)
    truncated = len(repos) > _PROJECT_REPO_CAP
    repos = repos[:_PROJECT_REPO_CAP]
    window = _clamp_window(window)
    if slug:
        spec = metrics_mod.spec_for(slug)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"unknown metric: {slug}")
        specs = [spec]
    else:
        specs = metrics_mod.REGISTRY
        if category:
            specs = [m for m in specs if _category(m).lower() == category.lower()]
    computed, cached_at = _compute_project_cached(
        project, window, repos, specs, refresh=refresh
    )
    rows = [
        {
            "repo": full,
            "metrics": [
                {
                    "slug": spec.slug,
                    "value": by[spec.slug].value if by.get(spec.slug) else "—",
                    "numeric": _metric_numeric(by[spec.slug]) if by.get(spec.slug) else None,
                }
                for spec in specs
            ],
        }
        for full, by in computed
    ]
    return {
        "project": project,
        "repo_count": len(repos),
        "truncated": truncated,
        "window_days": window,
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cached_at": cached_at,
        "metrics": [_spec_to_dict(s) for s in specs],
        "repositories": rows,
    }
