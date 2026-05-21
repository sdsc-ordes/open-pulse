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
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from ..auth import get_settings, maybe_require_auth
from ..knowledge import duckdb_browser
from ..knowledge import enrich as enrich_mod
from ..knowledge import normalize, opensearch, qdrant, registry, stats, stores, wanted

router = APIRouter(tags=["hub"])
log = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# Featured / example URLs surfaced on the landing page. Picked so a
# visitor can click straight into a working entity even before they
# know what to search for.
_HOME_EXAMPLES: tuple[tuple[str, str], ...] = (
    ("github.com/LuckySB/s6-overlay", "GitHub repository"),
    ("zenodo.org/records/4905618", "Zenodo deposit"),
    ("ror.org/02s6k3f65", "EPFL (ROR)"),
    ("huggingface.co/bigscience/bloom", "HuggingFace model"),
    (
        "infoscience.epfl.ch/entities/publication/fced83fa-5977-4b2e-b7b1-dad4ee87a8e1",
        "EPFL publication",
    ),
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
    "zenodo_records": ("Zenodo records", "zenodo.org"),
    "hf_models": ("HuggingFace models", "huggingface.co"),
    "hf_datasets": ("HuggingFace datasets", "huggingface.co"),
    "hf_spaces": ("HuggingFace spaces", "huggingface.co"),
    "hf_orgs": ("HuggingFace organizations", "huggingface.co"),
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
}

# Tiny module-level cache for the collection stats. The home page is
# the main visitor surface so we refresh once a minute, not every hit.
_STATS_CACHE: dict[str, object] = {"at": 0.0, "rows": []}
_STATS_TTL_SECONDS = 60.0


def _collection_stats() -> list[dict[str, object]]:
    now = time.monotonic()
    if now - float(_STATS_CACHE["at"]) < _STATS_TTL_SECONDS and _STATS_CACHE["rows"]:
        return list(_STATS_CACHE["rows"])  # type: ignore[arg-type]

    names = qdrant.list_collections()
    rows: list[dict[str, object]] = []
    for name in names:
        label, host = _COLLECTION_LABELS.get(name, (name, ""))
        meta = _SOURCE_METADATA.get(host, {})
        rows.append(
            {
                "name": name,
                "label": label,
                "host": host,
                "count": qdrant.count_points(name),
                "logo_url": _logo_for_host(host),
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
    import math

    counts = [int(r["count"] or 0) for r in rows if r.get("count")]
    log_max = math.log10(max(counts) + 1) if counts else 1.0
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

    _STATS_CACHE["at"] = now
    _STATS_CACHE["rows"] = rows
    return list(rows)


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
def hub_collection(request: Request, name: str) -> HTMLResponse:
    """Collection landing page — label, count, and sample entries.

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
            "page": "hub",
            "collection_name": name,
            "label": label,
            "host": host,
            "count": count,
            "samples": samples,
            "source_type": qdrant._source_type_for(name),
            "logo_url": _logo_for_host(host),
            "homepage": meta.get("homepage", ""),
            "description": meta.get("description", ""),
            "browsable": duckdb_browser.is_browsable(name),
        },
    )


@router.get(
    "/api/hub/c/{name}/rows",
    dependencies=[Depends(maybe_require_auth)],
)
def hub_collection_rows(
    name: str,
    page: int = 1,
    size: int = duckdb_browser.DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """Paginated rows from the DuckDB table backing collection ``name``.

    Returns ``{columns, rows, total, page, size, pages, ...}`` for the
    UI to render a Rows tab on the collection landing page. Throws a
    404 when the collection has no registered DuckDB backing (most
    collections don't yet — this is an opt-in surface).
    """
    payload = duckdb_browser.list_rows(name, page=page, size=size)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail=f"collection {name!r} has no DuckDB backing registered",
        )
    return payload


@router.get(
    "/hub",
    response_class=HTMLResponse,
    dependencies=[Depends(maybe_require_auth)],
)
def hub_home(request: Request) -> HTMLResponse:
    """Front-door for the knowledge surface — search + examples + stats."""
    return templates.TemplateResponse(
        request,
        "hub/home.html",
        {
            "page": "hub",
            "examples": _HOME_EXAMPLES,
            "collections": _collection_stats(),
        },
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
async def resolve_stream(request: Request, ref: str) -> StreamingResponse:
    """Stream resolution progress as Server-Sent Events.

    The resolver itself is synchronous (calls into SPARQL / Neo4j /
    Qdrant clients with their own blocking I/O), so we run it in a
    worker thread and bridge status callbacks back to the asyncio
    event loop via :func:`loop.call_soon_threadsafe`.
    """
    parsed = normalize.parse_ref(ref)
    if not parsed.is_known_host:
        raise HTTPException(status_code=400, detail="hub URLs must include a host")

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

    def on_status(msg: str) -> None:
        # Called from the worker thread; ``asyncio.Queue`` is not
        # threadsafe so we schedule the put on the event loop.
        loop.call_soon_threadsafe(queue.put_nowait, ("status", msg))

    async def run_resolver() -> None:
        try:
            entity = await asyncio.to_thread(registry.resolve, parsed, on_status)
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
                        entity=entity, ref=parsed
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
