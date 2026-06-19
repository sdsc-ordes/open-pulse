"""``/hub/**`` — URL-as-identifier knowledge pages.

* ``GET /hub/wanted`` — backlog of URLs visitors have hit that the
  data plane doesn't enrich yet.
* ``GET /hub/{ref:path}`` — entity page. Returns a skeleton synchronously
  (sub-100ms) that opens an SSE to fetch the actual content; the user
  sees a spinner with live status updates instead of waiting on a
  multi-second blocking response.
* ``GET /api/hub/resolve-stream/{ref:path}`` — the SSE feed. Emits
  ``status`` events from the resolver and a final ``result`` event
  carrying the rendered HTML body.
* ``POST /api/hub/wanted/{id}/resolve`` / ``DELETE`` — backlog mutations.

Auth is gated by :func:`maybe_require_auth` so a single env flag
(``HUB_PUBLIC_KNOWLEDGE``) flips the entire knowledge surface between
"public catalog" and "password-locked dashboard".
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates

from ..auth import get_settings, maybe_require_auth
from ..knowledge import catalog as catalog_mod
from ..knowledge import facets as facets_mod
from ..knowledge import duckdb_browser
from ..knowledge import enrich as enrich_mod
from ..knowledge import (
    manifest,
    normalize,
    opensearch,
    presence,
    qdrant,
    registry,
    relations,
    stats,
    stores,
    wanted,
)

router = APIRouter(tags=["hub"])
log = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# Human-readable label for URL/IRI fact values (licence URLs, schema.org
# types, repo/DOI pointers) so the facts card shows readable text, not raw
# URLs. Defined in the resolver base alongside the predicate humaniser.
from ..knowledge.resolvers.base import human_url_label as _human_url_label  # noqa: E402

templates.env.globals["human_url_label"] = _human_url_label


def _url_host(url: str) -> str:
    """Bare host of a URL (``https://orcid.org/0000-… `` → ``orcid.org``),
    or ``""`` for a non-URL. Used to badge a value with its source."""
    m = re.match(r"^https?://([^/]+)", (url or "").strip(), re.I)
    return m.group(1).lower().lstrip("www.") if m else ""


def _source_logo(url: str) -> str:
    """A small source logo for a URL's host (ORCID / GitHub / ROR / DOI / …),
    reusing the catalog's per-host overrides + favicon fallback. ``""`` when
    the value isn't a URL so the template can skip the icon."""
    host = _url_host(url)
    if not host:
        return ""
    meta = _SOURCE_METADATA.get(host, {})
    return meta.get("logo_url") or _logo_for_host(host)


templates.env.globals["url_host"] = _url_host
templates.env.globals["source_logo"] = _source_logo


def _short_date(value: str) -> str:
    """Trim an ISO datetime to just its date (``2022-12-07T10:19:30Z`` →
    ``2022-12-07``); pass anything else through unchanged."""
    s = str(value or "")
    return s[:10] if re.match(r"^\d{4}-\d{2}-\d{2}[T ]", s) else s


templates.env.globals["short_date"] = _short_date

# Thematic grouping of the Facts card into separate cards (Classification /
# Metrics / Timeline / Details).
from ..knowledge.resolvers.base import fact_groups as _fact_groups  # noqa: E402

templates.env.globals["fact_groups"] = _fact_groups


# Featured / example URLs surfaced on the landing page. A small, diverse
# set of SDSC entities so a visitor can click straight into a working
# entity — software, dataset, model, deposit — before they know what to
# search for.
_HOME_EXAMPLES: tuple[tuple[str, str], ...] = (
    ("github.com/sdsc-ordes/gimie", "Software"),
    ("huggingface.co/datasets/SDSC/digiwild-dataset", "Dataset"),
    ("huggingface.co/SDSC/open-pulse-graph-classifier", "Model"),
    ("zenodo.org/records/17225715", "Zenodo deposit"),
)

# Per-source presentation: homepage link + short description used
# on tile hover and on the collection landing page. The favicon URL
# is derived from the host directly (via Google's S2 favicon service
# for uniform 32px icons across providers).
_SOURCE_METADATA: dict[str, dict[str, str]] = {
    "github.com": {
        "homepage": "https://github.com",
        "description": "Software repositories — code, contributors, releases.",
    },
    "zenodo.org": {
        "homepage": "https://zenodo.org",
        "description": "Open-science deposits with DOIs (datasets, software, posters).",
        # Google's S2 favicon service returns a tiny generic icon for
        # zenodo.org; override with the official Zenodo wordmark so
        # the tile is recognisable at a glance.
        "logo_url": "https://data-repository-finder.ll.mit.edu/repo_logos/Zenodo.png",
    },
    "huggingface.co": {
        "homepage": "https://huggingface.co",
        "description": "Models, datasets, spaces, and orgs across the ML community.",
    },
    "ror.org": {
        "homepage": "https://ror.org",
        "description": "Research Organization Registry — persistent org IDs.",
    },
    "infoscience.epfl.ch": {
        "homepage": "https://infoscience.epfl.ch",
        "description": "EPFL publications, theses, and people.",
    },
    "openalex.org": {
        "homepage": "https://openalex.org",
        "description": "Scholarly works, authors, institutions, concepts.",
    },
    "renkulab.io": {
        "homepage": "https://renkulab.io",
        "description": "Reproducible-research projects, users, data connectors.",
    },
    "orcid.org": {
        "homepage": "https://orcid.org",
        "description": "Researcher identifiers — ORCID iDs.",
    },
    "data.snf.ch": {
        "homepage": "https://data.snf.ch",
        "description": "Swiss National Science Foundation funded grants and outcomes.",
    },
    "www.research-collection.ethz.ch": {
        "homepage": "https://www.research-collection.ethz.ch",
        "description": "ETH Zürich's institutional research repository.",
    },
    "www.swissubase.ch": {
        "homepage": "https://www.swissubase.ch",
        "description": "Swiss social-science data archive.",
    },
    "graphsearch.epfl.ch": {
        "homepage": "https://graphsearch.epfl.ch",
        "description": "EPFL's category graph — disciplines and concepts.",
    },
    "hub.docker.com": {
        "homepage": "https://hub.docker.com",
        "description": "Docker Hub — container images, namespaces, pulls.",
    },
    "gitlab.epfl.ch": {
        "homepage": "https://gitlab.epfl.ch",
        "description": "EPFL's GitLab — projects, groups, and users.",
    },
    "gitlab.ethz.ch": {
        "homepage": "https://gitlab.ethz.ch",
        "description": "ETH Zürich's GitLab — projects, groups, and users.",
    },
    "gitlab.datascience.ch": {
        "homepage": "https://gitlab.datascience.ch",
        "description": "SDSC DataScience GitLab — projects, groups, and users.",
        # Per request: show the SDSC brand mark on the DataScience tiles
        # instead of the generic GitLab tanuki. Pinned to a first-party
        # static asset (the SDSC webclip logo) so it doesn't depend on an
        # external favicon service.
        "logo_url": "/static/img/gitlab-datascience.png",
    },
}


def _logo_for_host(host: str) -> str:
    """Resolve a host to a small icon URL.

    Google's S2 favicon service returns a uniform 32×32 ico for any
    domain — handy when we don't want to ship per-source assets.
    """
    if not host:
        return ""
    return f"https://www.google.com/s2/favicons?domain={host}&sz=32"


# Display-friendly labels for the collection tiles on the home page —
# the raw qdrant names are uppercase-y / under-scored.
_COLLECTION_LABELS: dict[str, tuple[str, str]] = {
    "github_repos": ("GitHub repositories", "github.com"),
    "github_users": ("GitHub users", "github.com"),
    "github_organizations": ("GitHub organizations", "github.com"),
    "zenodo_records": ("Zenodo records", "zenodo.org"),
    "communities": ("Zenodo communities", "zenodo.org"),
    # Dedicated DuckDB-only store (GME v3 split-store layout). Surfaced as
    # a tile via the federated manifest's surface_as_source allowlist
    # rather than a Qdrant collection — see _compute_collection_stats.
    "zenodo_communities": ("Zenodo communities", "zenodo.org"),
    "hf_models": ("HuggingFace models", "huggingface.co"),
    "hf_datasets": ("HuggingFace datasets", "huggingface.co"),
    "hf_spaces": ("HuggingFace spaces", "huggingface.co"),
    "hf_orgs": ("HuggingFace organizations", "huggingface.co"),
    # Split HuggingFace stores (GME 3.0.0rc1 layout) — same brand, new names.
    "huggingface_models": ("HuggingFace models", "huggingface.co"),
    "huggingface_datasets": ("HuggingFace datasets", "huggingface.co"),
    "huggingface_spaces": ("HuggingFace spaces", "huggingface.co"),
    "huggingface_organizations": ("HuggingFace organizations", "huggingface.co"),
    "ror_worldwide": ("ROR organizations (world)", "ror.org"),
    "ror_europe": ("ROR organizations (Europe)", "ror.org"),
    "ror_switzerland": ("ROR organizations (CH)", "ror.org"),
    "ror_epfl_ethz": ("ROR organizations (EPFL/ETHZ)", "ror.org"),
    "infoscience_articles": ("Infoscience publications", "infoscience.epfl.ch"),
    "infoscience_persons": ("Infoscience persons", "infoscience.epfl.ch"),
    "infoscience_organizations": ("Infoscience organizations", "infoscience.epfl.ch"),
    "infoscience_chunks": ("Infoscience chunks", "infoscience.epfl.ch"),
    "works": ("OpenAlex works", "openalex.org"),
    "authors": ("OpenAlex authors", "openalex.org"),
    "institutions": ("OpenAlex institutions", "openalex.org"),
    "concepts": ("OpenAlex concepts", "openalex.org"),
    "topics": ("OpenAlex topics", "openalex.org"),
    "sources": ("OpenAlex sources", "openalex.org"),
    "renkulab_projects": ("Renku projects", "renkulab.io"),
    "renkulab_users": ("Renku users", "renkulab.io"),
    "renkulab_groups": ("Renku groups", "renkulab.io"),
    "renkulab_data_connectors": ("Renku data connectors", "renkulab.io"),
    "orcid_epfl_persons": ("ORCID EPFL researchers", "orcid.org"),
    "orcid_epfl_employments": ("ORCID EPFL employments", "orcid.org"),
    "orcid_epfl_educations": ("ORCID EPFL educations", "orcid.org"),
    "orcid_switzerland_persons": ("ORCID Switzerland researchers", "orcid.org"),
    "orcid_switzerland_employments": ("ORCID Switzerland employments", "orcid.org"),
    "snsf_epfl": ("SNSF EPFL grants", "data.snf.ch"),
    "snsf_ethz": ("SNSF ETHZ grants", "data.snf.ch"),
    "snsf_switzerland": ("SNSF Switzerland grants", "data.snf.ch"),
    "ethz_research_collection_articles": (
        "ETHZ Research Collection articles",
        "www.research-collection.ethz.ch",
    ),
    "ethz_research_collection_persons": (
        "ETHZ Research Collection persons",
        "www.research-collection.ethz.ch",
    ),
    "ethz_research_collection_organizations": (
        "ETHZ Research Collection orgs",
        "www.research-collection.ethz.ch",
    ),
    "ethz_research_collection_chunks": (
        "ETHZ Research Collection chunks",
        "www.research-collection.ethz.ch",
    ),
    "swissubase_entities": ("SWISSUbase studies", "www.swissubase.ch"),
    "epfl_graph_disciplines": ("EPFL Graph disciplines", "graphsearch.epfl.ch"),
    # OAM Monitor: publications carry openalex IDs as URLs, organisations
    # carry ROR URLs — both surface as clickable samples via the
    # entity_id-is-a-URL fallback in qdrant._canonical_url_for_point.
    # Journals + publishers carry internal numeric IDs with no canonical
    # landing page, but the OA Monitor corpus is OpenAlex-derived, so we
    # point their host at openalex.org for a recognisable tile logo.
    "oamonitor_publications": ("OAM Monitor publications", "openalex.org"),
    "oamonitor_organisations": ("OAM Monitor organisations", "ror.org"),
    "oamonitor_journals": ("OAM Monitor journals", "openalex.org"),
    "oamonitor_publishers": ("OAM Monitor publishers", "openalex.org"),
    # DockerHub container registry.
    "dockerhub": ("DockerHub images", "hub.docker.com"),
    # HuggingFace Daily Papers (arXiv-linked).
    "huggingface_papers": ("HuggingFace papers", "huggingface.co"),
    # GitLab instances — one set of stores per host (groups / projects /
    # users). EPFL, ETH Zürich, and the SDSC DataScience GitLab.
    "gitlab_epfl_groups": ("GitLab EPFL groups", "gitlab.epfl.ch"),
    "gitlab_epfl_projects": ("GitLab EPFL projects", "gitlab.epfl.ch"),
    "gitlab_epfl_users": ("GitLab EPFL users", "gitlab.epfl.ch"),
    "gitlab_ethz_groups": ("GitLab ETHZ groups", "gitlab.ethz.ch"),
    "gitlab_ethz_projects": ("GitLab ETHZ projects", "gitlab.ethz.ch"),
    "gitlab_ethz_users": ("GitLab ETHZ users", "gitlab.ethz.ch"),
    "gitlab_datascience_groups": ("GitLab DataScience groups", "gitlab.datascience.ch"),
    "gitlab_datascience_projects": ("GitLab DataScience projects", "gitlab.datascience.ch"),
    "gitlab_datascience_users": ("GitLab DataScience users", "gitlab.datascience.ch"),
}

# Tiny module-level cache for the collection stats. The home page is
# the main visitor surface so we refresh once a minute, not every hit.
_STATS_CACHE: dict[str, object] = {"at": 0.0, "rows": []}
_STATS_TTL_SECONDS = 60.0

# Coalesce concurrent recomputes of the collection stats. The home page
# now lazy-loads the Sources grid, so a single page view fires the shell
# GET /hub *and* the fragment GET /api/hub/collections; on a cold or
# TTL-expired cache, both would otherwise run the full count gather at
# once. The lock makes the losers wait and pick up the winner's freshly
# cached rows instead of duplicating the serial DuckDB/Qdrant scan.
_STATS_REFRESH_LOCK = threading.Lock()
# Thread-pool width for the per-collection count fan-out below. Mirrors
# the autocomplete scroll pool in qdrant.py.
_STATS_COUNT_WORKERS = 8


def _cached_collection_stats() -> list[dict[str, object]] | None:
    """Return the memoised rows if still within the TTL, else ``None``."""
    now = time.monotonic()
    if now - float(_STATS_CACHE["at"]) < _STATS_TTL_SECONDS and _STATS_CACHE["rows"]:
        return list(_STATS_CACHE["rows"])  # type: ignore[arg-type]
    return None


def _collection_stats() -> list[dict[str, object]]:
    # Fast path: serve memoised rows without taking the refresh lock.
    cached = _cached_collection_stats()
    if cached is not None:
        return cached

    # Slow path: single-flight. Only one thread runs the gather; the rest
    # block on the lock, then return the now-fresh cache on the re-check.
    with _STATS_REFRESH_LOCK:
        cached = _cached_collection_stats()
        if cached is not None:
            return cached
        rows = _compute_collection_stats()
        _STATS_CACHE["at"] = time.monotonic()
        _STATS_CACHE["rows"] = rows
        return list(rows)


# DuckDB-only stores to show on the home grid even when they have no
# Qdrant collection yet (no vectors). Listed explicitly so we surface only
# these intentional ones — e.g. the GitLab user stores, which are created
# by the GitLab ingest but stay empty until the user-crawl step runs; the
# operator still wants them visible (tiling with a 0 count) rather than
# hidden. They click through to the (currently empty) row browser.
_ALWAYS_SURFACE: tuple[str, ...] = (
    "gitlab_epfl_users",
    "gitlab_ethz_users",
    "gitlab_datascience_users",
)


def _compute_collection_stats() -> list[dict[str, object]]:
    """Gather per-collection counts + presentation metadata (uncached).

    The per-collection counts are the expensive bit — a cold DuckDB
    ``COUNT(*)`` (some over joins / filtered scans) or a Qdrant
    ``/points/count`` fallback. They're independent and I/O-bound, so we
    fan them out across a small thread pool (mirroring the autocomplete /
    backlinks scrolls in ``qdrant.py``) rather than summing them serially.
    Each DuckDB count opens its own read-only connection and runs a plain
    ``COUNT(*)``, so concurrent reads are safe.
    """
    names = list(qdrant.list_collections())
    _seen = set(names)
    # Append the intentionally-surfaced DuckDB-only stores (e.g. GitLab
    # users) that aren't in Qdrant yet, so they tile even with 0 rows.
    for extra in _ALWAYS_SURFACE:
        if extra not in _seen and duckdb_browser.is_browsable(extra):
            names.append(extra)
            _seen.add(extra)
    # Manifest-driven extra tiles: DuckDB-only stores the GME explicitly
    # allowlists (surface_as_source) that have no Qdrant collection — e.g.
    # zenodo_communities. Best-effort: an empty/unreachable manifest leaves
    # the grid Qdrant-only (see knowledge/manifest.py).
    for _store in manifest.surfaced_duckdb_stores():
        if _store["name"] not in _seen:
            names.append(_store["name"])
            _seen.add(_store["name"])

    def _count_for(name: str) -> int | None:
        # Prefer the source-of-truth DuckDB row count over Qdrant
        # ``count_points`` when the collection has a registered backing.
        # Qdrant points include text chunks (a single repo with a long
        # README becomes 3+ points), which inflates the tile number 3-4×
        # and confuses visitors who then click into the row browser and
        # see a smaller table. Collections with no DuckDB backing fall
        # back to the Qdrant count as before.
        ddb_count = duckdb_browser.row_count_for(name)
        if ddb_count is not None:
            return ddb_count
        return qdrant.count_points(name)

    counts: list[int | None] = []
    if names:
        with ThreadPoolExecutor(
            max_workers=min(_STATS_COUNT_WORKERS, len(names))
        ) as pool:
            counts = list(pool.map(_count_for, names))

    rows: list[dict[str, object]] = []
    for name, count in zip(names, counts):
        label, host = _COLLECTION_LABELS.get(name, (name, ""))
        meta = _SOURCE_METADATA.get(host, {})
        rows.append(
            {
                "name": name,
                "label": label,
                "host": host,
                "count": count,
                # Per-source override wins (lets us pin the official
                # Zenodo wordmark and similar branded icons); fall
                # back to Google's S2 favicon service for hosts that
                # don't ship a curated logo.
                "logo_url": meta.get("logo_url") or _logo_for_host(host),
                "homepage": meta.get("homepage", ""),
                "description": meta.get("description", ""),
            }
        )
    # Stable sort: known labels first (in the order they appear in
    # _COLLECTION_LABELS), then anything new the deployment added.
    label_order = list(_COLLECTION_LABELS.keys())

    def _rank(r: dict[str, object]) -> tuple[int, str]:
        name = str(r["name"])
        try:
            return (label_order.index(name), name)
        except ValueError:
            return (len(label_order), name)

    rows.sort(key=_rank)

    # Heat-tier per tile: bucket the per-collection counts by their
    # log10 position relative to the deployment's largest collection.
    # Tiles get t1 (coldest, near-empty) → t4 (hottest, top decile).
    # Computed across the whole set (needs the global max), which is why
    # the lazy fragment renders all tiles in one server-side pass rather
    # than fanning out one fetch per tile.
    import math

    present = [int(r["count"] or 0) for r in rows if r.get("count")]
    log_max = math.log10(max(present) + 1) if present else 1.0
    for r in rows:
        c = int(r["count"] or 0)
        frac = math.log10(c + 1) / log_max if log_max > 0 else 0.0
        r["fraction"] = round(frac, 3)
        if c == 0:
            r["heat_tier"] = 0
        elif frac >= 0.85:
            r["heat_tier"] = 4
        elif frac >= 0.65:
            r["heat_tier"] = 3
        elif frac >= 0.4:
            r["heat_tier"] = 2
        else:
            r["heat_tier"] = 1

    return rows


@router.get(
    "/api/hub/autocomplete",
    dependencies=[Depends(maybe_require_auth)],
)
def hub_autocomplete(q: str = "", limit: int = 10) -> dict[str, list[dict[str, str]]]:
    """Typeahead suggestions for the home search box.

    Runs Qdrant text-match across the high-value collections (github,
    hf, zenodo, infoscience, ror, openalex …) in parallel. Returns
    JSON ready to consume from Alpine.
    """
    return {"suggestions": qdrant.autocomplete(q, limit=limit)}


@router.get(
    "/api/hub/stats/{topic}",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def hub_top_stats(request: Request, topic: str) -> HTMLResponse:
    """Render one Top-N leaderboard for the hub home.

    Results are DuckDB-cached for an hour, so even though the
    underlying Cypher / Qdrant scrolls aren't free, repeated hits
    are sub-100 ms.
    """
    data = stats.fetch_top(get_settings().data_dir, topic, limit=10)
    return templates.TemplateResponse(
        request,
        "hub/_top_body.html",
        {"data": data},
    )


@router.get(
    "/hub/c/{name}",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def hub_collection(request: Request, name: str, q: str = "") -> HTMLResponse:
    """Collection landing page — label, count, and sample entries.

    ``q`` pre-fills the row browser's search box (used by cross-table links
    that jump here from another collection with a value to filter on).

    Each sample resolves to a hub URL the visitor can click straight
    into. Declared BEFORE the catch-all ``/hub/{ref:path}`` so the
    ``c/`` prefix is reserved for this view.
    """
    label, host = _COLLECTION_LABELS.get(name, (name, ""))
    meta = _SOURCE_METADATA.get(host, {})
    count = qdrant.count_points(name)
    raw_points = qdrant.sample_points(name, limit=24)

    samples: list[dict[str, str]] = []
    for p in raw_points:
        payload = p.get("payload") or {}
        # Reuse the same canonical-URL derivation as the backlinks
        # panel so each sample lands on a real hub page.
        canonical = qdrant._canonical_url_for_point(name, payload)
        if not canonical:
            continue
        stripped = canonical
        if stripped.startswith("https://"):
            stripped = stripped[len("https://") :]
        elif stripped.startswith("http://"):
            stripped = stripped[len("http://") :]
        if stripped.startswith("www."):
            stripped = stripped[len("www.") :]
        hub_url = f"/hub/{stripped.rstrip('/')}"
        title = qdrant._label_for_point(payload) or stripped
        badge = qdrant._badge_for_repo(payload)
        samples.append(
            {
                "title": title,
                "hub_url": hub_url,
                "external_url": canonical,
                "badge": badge,
            }
        )
        if len(samples) >= 12:
            break

    return templates.TemplateResponse(
        request,
        "hub/collection.html",
        {
            "page": "sources",
            "collection_name": name,
            "initial_q": q,
            "label": label,
            "host": host,
            "count": count,
            "samples": samples,
            "source_type": qdrant._source_type_for(name),
            "logo_url": meta.get("logo_url") or _logo_for_host(host),
            "homepage": meta.get("homepage", ""),
            "description": meta.get("description", ""),
            "browsable": duckdb_browser.is_browsable(name),
        },
    )


# ── Sources — per-store index cards + network leaderboards ─────────────────
# Declared before the catch-all ``/hub/{ref:path}`` so ``/hub/sources`` is
# reserved for this page.
@router.get(
    "/hub/sources",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def hub_sources(request: Request) -> HTMLResponse:
    """Sources landing — the per-collection index cards (lazy
    ``/api/hub/collections``) and the "Top across the network"
    leaderboards. Collection detail lives at ``/hub/c/<name>``."""
    return templates.TemplateResponse(request, "hub/sources.html", {"page": "sources"})


# The catalog now lives on the Hub home (under the search). Keep the old
# URL working for bookmarks / deep links — redirect to /hub.
@router.get("/hub/catalog", include_in_schema=False)
def hub_catalog() -> RedirectResponse:
    return RedirectResponse(url="/hub", status_code=307)


def _csv(v: str) -> list[str]:
    """Split a comma-separated multi-select param into a clean list."""
    return [p.strip() for p in (v or "").split(",") if p.strip()]


@router.get("/api/hub/catalog", dependencies=[Depends(maybe_require_auth)])
def api_catalog(
    type: str = "", source: str = "", q: str = "", sort: str = "",
    lang: str = "", graph: str = "", page: int = 1,
) -> dict[str, Any]:
    """A page of normalised catalog items across the in-scope stores.

    ``type`` / ``source`` / ``lang`` are comma-separated multi-selects.
    ``graph`` is a JSON ``{facet_key: [values]}`` of GME graph-property
    selections (licence / owner / discipline / repository type / cited works).
    """
    try:
        graph_sel = json.loads(graph) if graph else {}
        if not isinstance(graph_sel, dict):
            graph_sel = {}
    except (ValueError, TypeError):
        graph_sel = {}
    return catalog_mod.browse(
        types=_csv(type), sources=_csv(source), q=q, sort=sort,
        langs=_csv(lang), graph=graph_sel, page=page,
    )


@router.get("/api/hub/catalog/featured", dependencies=[Depends(maybe_require_auth)])
def api_catalog_featured() -> dict[str, Any]:
    """Curated research highlights, grouped into themed featured strips."""
    return {"sections": catalog_mod.featured()}


@router.get("/api/hub/facets", dependencies=[Depends(maybe_require_auth)])
def api_facets(refresh: bool = False) -> dict[str, Any]:
    """Cached top values of the main GME properties — powers the catalog
    Filters modal (licenses, languages, repo types, owners, disciplines,
    citations, ROR orgs, ORCID people). ``refresh=1`` recomputes on demand."""
    return {"facets": facets_mod.gather(refresh=refresh)}


@router.get(
    "/api/hub/c/{name}/rows",
    dependencies=[Depends(maybe_require_auth)],
)
def hub_collection_rows(
    name: str,
    page: int = 1,
    size: int = duckdb_browser.DEFAULT_PAGE_SIZE,
    q: str = "",
    sort: str = "",
) -> dict[str, Any]:
    """Paginated rows from the DuckDB table backing collection ``name``.

    ``q`` is a case-insensitive substring filter applied to the columns
    listed in the collection's ``search_cols``. Empty ``q`` returns the
    unfiltered slice. ``sort`` is ``"col"`` (asc) or ``"col:desc"``.
    Throws a 404 when the collection isn't registered.
    """
    payload = duckdb_browser.list_rows(name, page=page, size=size, q=q, sort=sort)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail=f"collection {name!r} has no DuckDB backing registered",
        )
    return payload


@router.get(
    "/api/hub/c/{name}/export",
    dependencies=[Depends(maybe_require_auth)],
)
def hub_collection_export(
    name: str,
    fmt: str = "csv",
    q: str = "",
    sort: str = "",
) -> PlainTextResponse:
    """Download the full filtered+sorted dataset as ``fmt``.

    Supported formats: ``csv``, ``tsv``, ``md``, ``json-rec``, ``json-col``.
    Capped at ``duckdb_browser.MAX_EXPORT_ROWS`` rows; the response body
    silently truncates beyond that.
    """
    rendered = duckdb_browser.render_export(name, fmt, q=q, sort=sort)
    if rendered is None:
        raise HTTPException(
            status_code=404,
            detail=f"collection {name!r} has no DuckDB backing registered (or fmt {fmt!r} unknown)",
        )
    body, mime = rendered
    ext_map = {
        "csv": "csv",
        "tsv": "tsv",
        "md": "md",
        "json-rec": "json",
        "json-col": "json",
    }
    ext = ext_map.get(fmt, "txt")
    filename = f"{name}-{fmt}.{ext}"
    return PlainTextResponse(
        content=body,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/api/hub/c/{name}/stats",
    dependencies=[Depends(maybe_require_auth)],
)
def hub_collection_stats(name: str) -> dict[str, Any]:
    """Headline scalar stats for the collection landing page."""
    stats = duckdb_browser.top_stats(name)
    if stats is None:
        raise HTTPException(
            status_code=404,
            detail=f"collection {name!r} has no DuckDB backing registered",
        )
    return {
        "collection": name,
        "stats": stats,
        "search": duckdb_browser.search_info(name) or {},
    }


@router.get(
    "/hub",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def hub_home(request: Request) -> HTMLResponse:
    """Front-door for the knowledge surface — search + the visual catalog.

    The search box + examples render immediately; the catalog section
    (featured strips + browse grid) lazy-loads its cards client-side. The
    per-source index cards and network leaderboards live on the Sources
    page (see :func:`hub_sources`).
    """
    return templates.TemplateResponse(
        request,
        "hub/home.html",
        {
            "page": "hub",
            "examples": _HOME_EXAMPLES,
            "facets": catalog_mod.facets(),
        },
    )


@router.get(
    "/api/hub/collections",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def hub_collections(request: Request) -> HTMLResponse:
    """Render the Sources grid as a standalone fragment.

    Fetched lazily by the hub home after first paint. Returns the whole
    ranked tile set in one response (the heat-tier math needs the global
    max across all collections), or the "Qdrant unreachable" warn card
    when the count gather came back empty. Counts are memoised for
    ``_STATS_TTL_SECONDS`` so repeat hits are sub-100 ms.
    """
    return templates.TemplateResponse(
        request,
        "hub/_sources_body.html",
        {"collections": _collection_stats()},
    )


@router.get("/hub/", include_in_schema=False)
def hub_home_slash() -> RedirectResponse:
    return RedirectResponse(url="/hub", status_code=301)


@router.get(
    "/hub/wanted",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def wanted_list(request: Request) -> HTMLResponse:
    rows = wanted.list_wanted(get_settings().data_dir)
    return templates.TemplateResponse(
        request,
        "hub/wanted_list.html",
        {"page": "hub-wanted", "rows": rows},
    )


@router.get(
    "/hub/{ref:path}",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def hub_entity(request: Request, ref: str) -> HTMLResponse:
    """Skeleton page — the SSE stream does the real work."""
    parsed = normalize.parse_ref(ref)
    if not parsed.is_known_host:
        raise HTTPException(
            status_code=400,
            detail="hub URLs must include a host, e.g. /hub/github.com/<owner>/<repo>",
        )

    # The EventSource URL goes through the same path-converter, so each
    # path segment gets quoted but slashes stay intact.
    encoded_path = (
        "/".join(quote(p, safe="") for p in parsed.path.split("/"))
        if parsed.path
        else ""
    )
    encoded = f"{parsed.host}/{encoded_path}" if encoded_path else parsed.host
    ref_payload = {
        "host": parsed.host,
        "path": parsed.path,
        "canonical_url": parsed.canonical_url,
        "encoded_path": encoded,
    }
    return templates.TemplateResponse(
        request,
        "hub/entity.html",
        {
            "page": "hub",
            "ref": parsed,
            "ref_payload": ref_payload,
        },
    )


# ── SSE feed ──────────────────────────────────────────────────────────────


@router.get(
    "/api/hub/resolve-stream/{ref:path}",
    dependencies=[Depends(maybe_require_auth)],
)
async def resolve_stream(
    request: Request, ref: str, graph: str = ""
) -> StreamingResponse:
    """Stream resolution progress as Server-Sent Events.

    The resolver itself is synchronous (calls into SPARQL / Neo4j /
    Qdrant clients with their own blocking I/O), so we run it in a
    worker thread and bridge status callbacks back to the asyncio
    event loop via :func:`loop.call_soon_threadsafe`.

    ``graph`` (query param) pins the RDF named graph the SPARQL probes
    scope to — set per-browser from Settings. It travels as a query
    param (not a header) because the client is an EventSource, which
    can't send custom headers. Validated + applied via
    :func:`stores.set_active_graph`; an empty / unknown value keeps the
    store's default-graph behaviour.
    """
    parsed = normalize.parse_ref(ref)
    if not parsed.is_known_host:
        raise HTTPException(status_code=400, detail="hub URLs must include a host")

    # Pin the graph in *this* async context so the copy that
    # ``asyncio.to_thread`` hands the worker carries it into the
    # SPARQL probes. Harmless no-op when unset / malformed.
    stores.set_active_graph(graph)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

    def on_status(msg: str) -> None:
        # Called from the worker thread; ``asyncio.Queue`` is not
        # threadsafe so we schedule the put on the event loop.
        loop.call_soon_threadsafe(queue.put_nowait, ("status", msg))

    async def run_resolver() -> None:
        try:
            entity = await asyncio.to_thread(registry.resolve, parsed, on_status)
            if entity is None:
                # No resolver knew this URL — fall back to a basic page
                # built straight from the catalog's DuckDB row, if any
                # (Docker images, GitLab projects, datasets, …).
                on_status("No resolver match — checking the catalog index")
                entity = await asyncio.to_thread(
                    catalog_mod.entity_from_ref, parsed.host, parsed.path
                )
        except Exception as exc:  # noqa: BLE001
            log.exception("resolve stream failed for %s", parsed.canonical_url)
            loop.call_soon_threadsafe(
                queue.put_nowait, ("error", f"resolver error: {exc}")
            )
            return
        loop.call_soon_threadsafe(queue.put_nowait, ("entity", entity))

    task = asyncio.create_task(run_resolver())

    async def gen():
        # First packet — reverse proxies sometimes buffer the response
        # until they see one. Sending immediately keeps the spinner
        # responsive.
        yield _sse_event("status", "Connecting to data plane")
        try:
            while True:
                if await request.is_disconnected():
                    task.cancel()
                    return

                kind, payload = await queue.get()

                if kind == "status":
                    yield _sse_event("status", str(payload))
                    continue

                if kind == "error":
                    yield _sse_event("error", str(payload))
                    return

                # kind == "entity" — render and ship the final fragment.
                entity = payload
                if entity is None:
                    row = wanted.record_miss(
                        get_settings().data_dir,
                        url=parsed.canonical_url,
                        host=parsed.host,
                        path=parsed.path,
                    )
                    html = templates.get_template("hub/_wanted_body.html").render(
                        ref=parsed,
                        row=row,
                        newly_queued=row.hits == 1,
                    )
                    found = False
                    title = parsed.display
                    kind = ""
                    yield _sse_event("status", "No matches — queued in the wanted list")
                else:
                    html = templates.get_template("hub/_entity_body.html").render(
                        entity=entity,
                        ref=parsed,
                        hero_emoji=catalog_mod.kind_emoji(entity.kind),
                        hero_gradient=catalog_mod.cover_gradient(
                            entity.ref_url or entity.title
                        ),
                    )
                    found = True
                    title = entity.title or parsed.display
                    kind = entity.kind or ""
                    yield _sse_event("status", "Done")
                # ``result`` carries JSON so the skeleton can decide
                # whether to kick off the lazy panels AND swap the
                # page heading from the URL slug to the entity title.
                yield _sse_event(
                    "result",
                    json.dumps(
                        {
                            "found": found,
                            "html": html,
                            "title": title,
                            "kind": kind,
                            "url": parsed.canonical_url,
                        }
                    ),
                )
                return
        finally:
            task.cancel()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            # Nginx in front of the hub buffers responses by default;
            # this hint disables it for this endpoint specifically.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )


def _sse_event(event: str, data: str) -> str:
    """Format one SSE frame.

    The data field is newline-sensitive: every embedded ``\\n`` has to
    be re-emitted as a fresh ``data:`` line, otherwise the browser cuts
    the message at the first newline.
    """
    lines = data.split("\n")
    body = "\n".join(f"data: {ln}" for ln in lines)
    return f"event: {event}\n{body}\n\n"


# ── Lazy backlinks ────────────────────────────────────────────────────────

# Per-host primary collections — used as the ``exclude_collections``
# argument to lookup_backlinks so a GitHub URL doesn't surface itself
# from github_repos as a "backlink".
_PRIMARY_COLLECTIONS_BY_HOST: dict[str, list[str]] = {
    "github.com": ["github_repos"],
    "gitlab.com": ["renkulab_projects"],
    "zenodo.org": ["zenodo_records"],
    "ror.org": [
        "ror_epfl_ethz",
        "ror_switzerland",
        "ror_europe",
        "ror_worldwide",
    ],
    "huggingface.co": ["hf_models", "hf_datasets", "hf_spaces", "hf_orgs"],
    "infoscience.epfl.ch": [
        "infoscience_articles",
        "infoscience_chunks",
        "infoscience_persons",
        "infoscience_organizations",
    ],
}


@router.get(
    "/api/hub/backlinks/{ref:path}",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def hub_backlinks(request: Request, ref: str) -> HTMLResponse:
    """Render the backlinks panel for a URL — fetched lazily from
    the entity page after the main body has rendered.

    Returns an empty body when the scan came up empty so the
    front-end can hide the panel without further branching.
    """
    parsed = normalize.parse_ref(ref)
    if not parsed.is_known_host:
        raise HTTPException(status_code=400, detail="hub URLs must include a host")

    exclude = _PRIMARY_COLLECTIONS_BY_HOST.get(parsed.host, [])
    groups = qdrant.cached_panel(
        "backlinks",
        parsed.canonical_url,
        lambda: qdrant.lookup_backlinks(parsed, exclude_collections=exclude),
    )
    return templates.TemplateResponse(
        request,
        "hub/_backlinks_body.html",
        {"groups": groups, "ref": parsed},
    )


@router.get(
    "/api/hub/related/{ref:path}",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def hub_related(request: Request, ref: str) -> HTMLResponse:
    """Render the 'Related' panel — siblings from the same Qdrant
    collection that share an axis with this entity (owner, language,
    license, author, ...). Empty body when no axis produces hits.
    """
    parsed = normalize.parse_ref(ref)
    if not parsed.is_known_host:
        raise HTTPException(status_code=400, detail="hub URLs must include a host")

    groups = qdrant.cached_panel(
        "related",
        parsed.canonical_url,
        lambda: qdrant.lookup_related(parsed),
    )
    return templates.TemplateResponse(
        request,
        "hub/_related_body.html",
        {"groups": groups, "ref": parsed},
    )


# ── Expand: shared helpers for both the all-in-one endpoint and the
# per-source ones the canvas calls in parallel. Each per-source route
# is wafer-thin so the canvas can render whichever source returns
# first instead of waiting for the slowest one (typically the
# Qdrant cross-collection backlink scroll). All routes share
# ``qdrant.cached_panel`` for a 5-minute server-side TTL.


def _expand_parse(ref: str) -> "normalize.HubRef":
    parsed = normalize.parse_ref(ref)
    if not parsed.is_known_host:
        raise HTTPException(status_code=400, detail="ref must be a known hub URL")
    return parsed


def _expand_it(it: Any) -> dict[str, str]:
    return {
        "label": getattr(it, "label", ""),
        "hub_url": getattr(it, "hub_url", ""),
        "external_url": getattr(it, "external_url", "") or "",
        "badge": getattr(it, "badge", "") or "",
        "source_type": getattr(it, "source_type", "") or "",
    }


def _expand_grp(g: Any) -> dict[str, Any]:
    return {"title": g.title, "items": [_expand_it(it) for it in g.items]}


@router.get(
    "/api/hub/expand/people",
    dependencies=[Depends(maybe_require_auth)],
)
def hub_expand_people(ref: str) -> dict[str, Any]:
    """People-shaped relations (contributors, authors) for the canvas."""
    parsed = _expand_parse(ref)
    groups = qdrant.cached_panel(
        "people", parsed.canonical_url, lambda: qdrant.lookup_people(parsed)
    )
    return {"groups": [_expand_grp(g) for g in groups if g.items]}


@router.get(
    "/api/hub/expand/related",
    dependencies=[Depends(maybe_require_auth)],
)
def hub_expand_related(ref: str) -> dict[str, Any]:
    """Qdrant siblings on the same axis (owner, language, author, …)."""
    parsed = _expand_parse(ref)
    groups = qdrant.cached_panel(
        "related", parsed.canonical_url, lambda: qdrant.lookup_related(parsed)
    )
    return {"groups": [_expand_grp(g) for g in groups if g.items]}


@router.get(
    "/api/hub/expand/backlinks",
    dependencies=[Depends(maybe_require_auth)],
)
def hub_expand_backlinks(ref: str) -> dict[str, Any]:
    """Cross-collection points that reference this entity's canonical URL."""
    parsed = _expand_parse(ref)
    exclude = _PRIMARY_COLLECTIONS_BY_HOST.get(parsed.host, [])
    groups = qdrant.cached_panel(
        "backlinks",
        parsed.canonical_url,
        lambda: qdrant.lookup_backlinks(parsed, exclude_collections=exclude),
    )
    out = []
    for g in groups:
        if not g.items:
            continue
        out.append(
            {
                "title": g.label,
                "collection": g.collection,
                "items": [_expand_it(it) for it in g.items],
            }
        )
    return {"groups": out}


@router.get(
    "/api/hub/expand/neo4j",
    dependencies=[Depends(maybe_require_auth)],
)
def hub_expand_neo4j(ref: str) -> dict[str, Any]:
    """1-hop neighbours from the Neo4j graph, bucketed by edge type."""
    parsed = _expand_parse(ref)
    groups = qdrant.cached_panel(
        "neo4j", parsed.canonical_url, lambda: relations.from_neo4j(parsed)
    )
    return {"groups": [_expand_grp(g) for g in groups if g.items]}


@router.get(
    "/api/hub/expand/sparql",
    dependencies=[Depends(maybe_require_auth)],
)
def hub_expand_sparql(ref: str) -> dict[str, Any]:
    """RDF triples about this URL, bucketed by predicate."""
    parsed = _expand_parse(ref)
    groups = qdrant.cached_panel(
        "sparql", parsed.canonical_url, lambda: relations.from_sparql(parsed)
    )
    return {"groups": [_expand_grp(g) for g in groups if g.items]}


@router.get(
    "/api/hub/expand/opensearch",
    dependencies=[Depends(maybe_require_auth)],
)
def hub_expand_opensearch(ref: str) -> dict[str, Any]:
    """Top commit authors from the GrimoireLab-enriched git index."""
    parsed = _expand_parse(ref)
    groups = qdrant.cached_panel(
        "opensearch", parsed.canonical_url, lambda: relations.from_opensearch(parsed)
    )
    return {"groups": [_expand_grp(g) for g in groups if g.items]}


@router.get(
    "/api/hub/expand",
    dependencies=[Depends(maybe_require_auth)],
)
def hub_expand_json(ref: str) -> dict[str, Any]:
    """JSON sibling/backlink groups for the Canvas expand-node modal.

    Mirrors the data that powers ``/api/hub/related`` and
    ``/api/hub/backlinks`` (both of which return HTML for the entity
    page panels) but returns plain JSON so the canvas can render a
    filterable checklist client-side. Each group becomes one row the
    user can tick to splat all its items onto the canvas as connected
    children.
    """
    parsed = normalize.parse_ref(ref)
    if not parsed.is_known_host:
        raise HTTPException(status_code=400, detail="ref must be a known hub URL")

    related = qdrant.cached_panel(
        "related", parsed.canonical_url, lambda: qdrant.lookup_related(parsed)
    )
    parsed_host_exclude = _PRIMARY_COLLECTIONS_BY_HOST.get(parsed.host, [])
    backlinks = qdrant.cached_panel(
        "backlinks",
        parsed.canonical_url,
        lambda: qdrant.lookup_backlinks(
            parsed, exclude_collections=parsed_host_exclude
        ),
    )
    people = qdrant.cached_panel(
        "people", parsed.canonical_url, lambda: qdrant.lookup_people(parsed)
    )
    # The three additional graph stores. Each helper is best-effort:
    # if a store is unreachable the helper just returns [] so the
    # canvas still gets whatever the others surfaced.
    neo4j_groups = qdrant.cached_panel(
        "neo4j", parsed.canonical_url, lambda: relations.from_neo4j(parsed)
    )
    sparql_groups = qdrant.cached_panel(
        "sparql", parsed.canonical_url, lambda: relations.from_sparql(parsed)
    )
    os_groups = qdrant.cached_panel(
        "opensearch", parsed.canonical_url, lambda: relations.from_opensearch(parsed)
    )

    def _it(it: Any) -> dict[str, str]:
        return {
            "label": getattr(it, "label", ""),
            "hub_url": getattr(it, "hub_url", ""),
            "external_url": getattr(it, "external_url", "") or "",
            "badge": getattr(it, "badge", "") or "",
            "source_type": getattr(it, "source_type", "") or "",
        }

    def _grp(g: Any) -> dict[str, Any]:
        return {"title": g.title, "items": [_it(it) for it in g.items]}

    return {
        "ref": parsed.canonical_url,
        "people": [_grp(g) for g in people if g.items],
        "neo4j": [_grp(g) for g in neo4j_groups if g.items],
        "sparql": [_grp(g) for g in sparql_groups if g.items],
        "opensearch": [_grp(g) for g in os_groups if g.items],
        "related": [_grp(g) for g in related if g.items],
        "backlinks": [
            {
                "title": g.label,
                "collection": g.collection,
                "items": [_it(it) for it in g.items],
            }
            for g in backlinks
            if g.items
        ],
    }


@router.get(
    "/api/hub/preview/{ref:path}",
    dependencies=[Depends(maybe_require_auth)],
)
def hub_preview(ref: str) -> dict[str, str]:
    """Light-weight metadata for a hub URL, served to hover tooltips.

    Fetches one chunk from the entity's primary Qdrant collection
    (or falls back to the canonical URL / slug fallback when nothing
    is indexed). Returns title + kind + a short description +
    inline badge — enough to fill a 300px floating preview.
    """
    parsed = normalize.parse_ref(ref)
    if not parsed.is_known_host:
        return {
            "title": parsed.display or ref,
            "kind": "",
            "description": "",
            "badge": "",
            "url": parsed.canonical_url,
        }
    return qdrant.cached_panel(
        "preview", parsed.canonical_url, lambda: _build_preview(parsed)
    )


def _build_preview(parsed: normalize.HubRef) -> dict[str, str]:
    """The actual preview-payload builder (cached above)."""
    collection = qdrant._related_primary_collection(parsed.host, parsed.path)
    payload: dict = {}
    if collection:
        candidates = qdrant._candidate_keys(parsed)
        if candidates:
            should = [{"key": f, "match": {"value": v}} for f, v in candidates]
            points = qdrant._scroll_with_timeout(
                collection, {"should": should}, 1, timeout=3.0
            )
            if points:
                payload = points[0].get("payload") or {}

    title = (
        payload.get("title") or payload.get("name") or payload.get("full_name") or ""
    ).strip()
    description = (
        payload.get("abstract")
        or payload.get("summary")
        or payload.get("description")
        or payload.get("text")
        or ""
    ).strip()
    badge = qdrant._badge_for_repo(payload)

    # Slug-style fallback for the repo-shaped hosts.
    if not title and parsed.host in ("github.com", "huggingface.co", "gitlab.com"):
        parts = parsed.path.split("/")
        if (
            parsed.host == "huggingface.co"
            and parts
            and parts[0].lower()
            in (
                "datasets",
                "spaces",
            )
        ):
            parts = parts[1:]
        if len(parts) >= 2:
            title = "/".join(parts[:2])
        elif parts:
            title = parts[0]
    if not title:
        # Final fallback to the URL slug.
        title = parsed.display

    if len(description) > 280:
        description = description[:280].rsplit(" ", 1)[0] + "…"

    kind = ""
    if parsed.host == "github.com":
        parts = parsed.path.split("/")
        kind = "GitHub repository" if len(parts) >= 2 else "GitHub user or organization"
    elif parsed.host == "gitlab.com":
        kind = "GitLab project"
    elif parsed.host == "zenodo.org":
        kind = "Zenodo record"
    elif parsed.host == "ror.org":
        kind = "Research organization (ROR)"
    elif parsed.host == "huggingface.co":
        kind = "HuggingFace resource"
    elif parsed.host == "infoscience.epfl.ch":
        parts = parsed.path.split("/")
        if len(parts) >= 2 and parts[0].lower() == "entities":
            kind = f"EPFL {parts[1].lower()}"
        else:
            kind = "EPFL Infoscience entity"

    return {
        "title": title,
        "kind": kind,
        "description": description,
        "badge": badge or "",
        "url": parsed.canonical_url,
    }


@router.get(
    "/api/hub/community/{ref:path}",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def hub_community(request: Request, ref: str) -> HTMLResponse:
    """Aggregate contributors + owning orgs across the github repos
    cited by a non-github entity.

    Empty for github URLs (their own Graph-neighbours panel already
    surfaces the community) and when none of the connected repos
    are indexed in Neo4j.
    """
    parsed = normalize.parse_ref(ref)
    if not parsed.is_known_host or parsed.host == "github.com":
        return templates.TemplateResponse(
            request,
            "hub/_community_body.html",
            {"data": {"contributors": [], "owners": []}, "ref": parsed},
        )

    def _compute() -> dict[str, object]:
        items = qdrant.lookup_connected_github(parsed, limit=20)
        slugs = [it.label for it in items if "/" in it.label]
        if not slugs:
            return {"contributors": [], "owners": []}
        return stores.neo4j_repo_community(slugs, limit=20)

    data = qdrant.cached_panel("community", parsed.canonical_url, _compute)
    return templates.TemplateResponse(
        request,
        "hub/_community_body.html",
        {"data": data, "ref": parsed},
    )


@router.get(
    "/api/hub/connected/{ref:path}",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def hub_connected(request: Request, ref: str) -> HTMLResponse:
    """Render the 'Connected on GitHub' panel — GitHub repos
    referenced from a non-github entity's Qdrant payload.

    Empty body for github.com URLs (they already show their own
    Neo4j neighbours / Qdrant siblings) and when no github links
    appear in the payload.
    """
    parsed = normalize.parse_ref(ref)
    if not parsed.is_known_host:
        raise HTTPException(status_code=400, detail="hub URLs must include a host")

    if parsed.host == "github.com":
        return templates.TemplateResponse(
            request, "hub/_connected_body.html", {"items": [], "ref": parsed}
        )

    items = qdrant.cached_panel(
        "connected",
        parsed.canonical_url,
        lambda: qdrant.lookup_connected_github(parsed),
    )
    return templates.TemplateResponse(
        request,
        "hub/_connected_body.html",
        {"items": items, "ref": parsed},
    )


@router.get(
    "/api/hub/activity/{ref:path}",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def hub_activity(request: Request, ref: str) -> HTMLResponse:
    """Render the OpenSearch activity panel for a repository URL.

    Currently only github.com URLs map to GrimoireLab git ingest
    data; other hosts get an empty body and the panel hides itself.
    """
    parsed = normalize.parse_ref(ref)
    if not parsed.is_known_host:
        raise HTTPException(status_code=400, detail="hub URLs must include a host")

    if parsed.host != "github.com":
        # No git ingest for non-GitHub hosts today; render empty so
        # the lazy slot hides itself.
        return templates.TemplateResponse(
            request, "hub/_activity_body.html", {"stats": None, "ref": parsed}
        )

    stats = qdrant.cached_panel(
        "activity",
        parsed.canonical_url,
        lambda: opensearch.repo_activity(parsed.canonical_url),
    )
    return templates.TemplateResponse(
        request,
        "hub/_activity_body.html",
        {"stats": stats, "ref": parsed},
    )


# Compact CHAOSS headline set for the software-page panel — one per axis
# (community / popularity / quality). The full 20-metric dashboard lives at
# /chaoss/github.com/<owner>/<repo>; this is the at-a-glance teaser.
_CHAOSS_PANEL_SLUGS = (
    "contributors",
    "project_popularity",
    "technical_fork",
    "academic_impact",
    "release_frequency",
    "licenses_declared",
)
_CHAOSS_PANEL_WINDOW = 3650  # match the dashboard default (≈all-time)
_CHAOSS_PANEL_CACHE: dict[str, list[dict[str, Any]]] = {}


@router.get(
    "/api/hub/chaoss/{ref:path}",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
async def hub_chaoss(request: Request, ref: str) -> HTMLResponse:
    """Compact CHAOSS health panel for a github repository URL.

    Computes a small headline set of metrics (concurrently) and links to the
    full per-repo dashboard. Non-repo URLs get an empty body so the lazy slot
    hides itself.
    """
    parsed = normalize.parse_ref(ref)
    is_repo = parsed.host == "github.com" and parsed.path.count("/") == 1
    if not is_repo:
        return templates.TemplateResponse(
            request, "hub/_chaoss_body.html",
            {"metrics": [], "full": "", "window": _CHAOSS_PANEL_WINDOW, "window_label": ""},
        )

    from ..chaoss import metrics as chaoss_metrics  # lazy — heavy import

    full = parsed.path
    canonical = parsed.canonical_url

    metrics = _CHAOSS_PANEL_CACHE.get(canonical)
    if metrics is None:
        def _compute(slug: str) -> dict[str, Any] | None:
            spec = chaoss_metrics.spec_for(slug)
            if spec is None:
                return None
            try:
                r = spec.compute(full, canonical, _CHAOSS_PANEL_WINDOW)
            except Exception as exc:  # noqa: BLE001
                log.info("chaoss panel metric %s failed for %s: %s", slug, full, exc)
                return None
            return {
                "slug": slug, "name": spec.name, "category": spec.category,
                "value": r.value, "label": r.label,
                "secondary": r.secondary, "tone": r.headline_tone or "",
            }

        results = await asyncio.gather(
            *(asyncio.to_thread(_compute, s) for s in _CHAOSS_PANEL_SLUGS)
        )
        metrics = [m for m in results if m]
        if len(_CHAOSS_PANEL_CACHE) > 256:
            _CHAOSS_PANEL_CACHE.clear()
        _CHAOSS_PANEL_CACHE[canonical] = metrics

    return templates.TemplateResponse(
        request, "hub/_chaoss_body.html",
        {
            "metrics": metrics, "full": full,
            "window": _CHAOSS_PANEL_WINDOW,
            "window_label": chaoss_metrics.window_label(_CHAOSS_PANEL_WINDOW),
        },
    )


@router.get("/api/hub/graphs", dependencies=[Depends(maybe_require_auth)])
def hub_graphs() -> dict[str, Any]:
    """List the SPARQL store's named graphs for the hub graph picker.

    Each entry: ``{iri, count, label}`` where ``label`` is the compact
    part after ``/graph/``. Cached briefly via the panel cache."""
    def _gather() -> list[dict[str, Any]]:
        out = []
        for g in stores.list_named_graphs():
            iri = g["iri"]
            label = iri.split("/graph/", 1)[-1] if "/graph/" in iri else iri
            out.append({"iri": iri, "count": g["count"], "label": label})
        return out

    graphs = qdrant.cached_panel("graphs", "*", _gather)
    # The panel cache also caches empties — a transient SPARQL blip (e.g. just
    # after a restart) would otherwise hide the graph picker for the whole TTL.
    # Recompute once when the cache is empty so the picker self-heals.
    if not graphs:
        graphs = qdrant.cached_panel("graphs", "*", _gather, force=True)
    return {"graphs": graphs}


@router.get(
    "/api/hub/presence/{ref:path}",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def hub_presence(request: Request, ref: str) -> HTMLResponse:
    """Render the "Present in" panel — which data-plane stores hold this
    URL (RDF named graphs, Neo4j, OpenSearch, Qdrant). Mounts even when the
    entity didn't resolve, so a not-yet-ingested URL gets a clear answer."""
    parsed = normalize.parse_ref(ref)
    if not parsed.is_known_host:
        raise HTTPException(status_code=400, detail="hub URLs must include a host")
    rows = qdrant.cached_panel(
        "presence", parsed.canonical_url, lambda: presence.gather(parsed)
    )
    return templates.TemplateResponse(
        request, "hub/_presence_body.html", {"rows": rows, "ref": parsed}
    )


# ── Wanted-list mutations (small JSON API) ─────────────────────────────────


api = APIRouter(prefix="/api/hub", tags=["hub"])


@api.post(
    "/enrich/{ref:path}",
    dependencies=[Depends(maybe_require_auth)],
)
def hub_enrich(ref: str) -> dict[str, str]:
    """Fire crawler + GME jobs for the URL.

    Returns the two job IDs (one each) so the UI can show
    confirmation. Either side may fail independently — both errors
    are surfaced.
    """
    parsed = normalize.parse_ref(ref)
    if not parsed.is_known_host:
        raise HTTPException(status_code=400, detail="hub URLs must include a host")
    result = enrich_mod.enrich(parsed.canonical_url)
    return {
        "url": parsed.canonical_url,
        "crawler_job_id": result.crawler_job_id,
        "gme_job_id": result.gme_job_id,
        "crawler_error": result.crawler_error,
        "gme_error": result.gme_error,
        "ok": "true" if result.ok else "false",
    }


@api.post(
    "/wanted/{wanted_id}/resolve",
    dependencies=[Depends(maybe_require_auth)],
)
def wanted_resolve(wanted_id: int) -> dict[str, str]:
    wanted.mark_resolved(get_settings().data_dir, wanted_id)
    return {"status": "resolved"}


@api.delete(
    "/wanted/{wanted_id}",
    dependencies=[Depends(maybe_require_auth)],
)
def wanted_delete(wanted_id: int) -> dict[str, str]:
    wanted.delete_wanted(get_settings().data_dir, wanted_id)
    return {"status": "deleted"}
