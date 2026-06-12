"""Catalog — a visual, browsable view over the hub's entities.

Reuses the existing data plane wholesale:

* :mod:`.duckdb_browser` (``list_rows`` / ``is_browsable`` / the ``.ro.duckdb``
  snapshots) supplies the rows for the **browse grid**.
* ``routes.hub`` collection labels + ``_logo_for_host`` supply card chrome
  (lazy-imported to avoid an import cycle).
* The curated Cypher (most-starred / EPFL-flagship repos) supplies the
  **featured** row, run through the agent's ``run_cypher`` tool.

Every entity, whatever store it lives in, is normalised to one ``CatalogItem``
so a single card component renders them all. Each source declares which columns
map to title / subtitle / badges and a ``ref`` template that builds the
canonical ``host/path`` the entity-detail route (``/hub/<ref>``) resolves.
Anything a source doesn't declare falls back to a column heuristic, so a new
store still yields a usable card with zero config.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from . import duckdb_browser as ddb

log = logging.getLogger(__name__)


# ── catalog sources ───────────────────────────────────────────────────────
# ``ref`` is a list of "host/{col}" templates tried in order; the first whose
# every {placeholder} is present in the row wins (so DOI → OpenAlex fallbacks
# work). A row that fills no template links to the collection browser instead.
# Stores not listed here are still browsable via the generic heuristics below.
_SOURCES: list[dict[str, Any]] = [
    {"collection": "github_repos", "type": "repo", "title": "name",
     "subtitle": "description", "ref": ["github.com/{owner}/{name}"],
     "stars": "stargazers_count", "lang": "primary_language",
     "license": "license_spdx", "date": "pushed_at"},
    {"collection": "gitlab_epfl_projects", "type": "repo", "title": "name",
     "subtitle": "description", "ref": ["{host}/{full_path}"],
     "stars": "star_count", "date": "last_activity_at"},
    {"collection": "gitlab_ethz_projects", "type": "repo", "title": "name",
     "subtitle": "description", "ref": ["{host}/{full_path}"],
     "stars": "star_count", "date": "last_activity_at"},
    {"collection": "huggingface_models", "type": "model", "title": "repo_id",
     "subtitle": "author", "ref": ["huggingface.co/{repo_id}"],
     "stars": "likes", "lang": "pipeline_tag", "license": "license",
     "date": "last_modified"},
    {"collection": "huggingface_datasets", "type": "dataset", "title": "repo_id",
     "subtitle": "author", "ref": ["huggingface.co/datasets/{repo_id}"],
     "stars": "likes", "license": "license", "date": "last_modified"},
    {"collection": "huggingface_spaces", "type": "space", "title": "repo_id",
     "subtitle": "author", "ref": ["huggingface.co/spaces/{repo_id}"],
     "stars": "likes", "lang": "sdk", "license": "license", "date": "last_modified"},
    {"collection": "zenodo_records", "type": "dataset", "title": "title",
     "subtitle": "description", "ref": ["{zenodo_id}", "{doi}"],
     "stars": "downloads", "license": "license_id", "date": "publication_date"},
    {"collection": "works", "type": "paper", "title": "title",
     "subtitle": "abstract", "ref": ["{doi}", "{openalex_id}"],
     "date": "publication_year"},
    {"collection": "infoscience_articles", "type": "paper", "title": "title",
     "subtitle": "journal", "ref": ["{infoscience_url}", "doi.org/{doi}"],
     "lang": "language", "date": "publication_year"},
    {"collection": "dockerhub", "type": "image", "title": "name",
     "subtitle": "description", "ref": ["hub.docker.com/r/{namespace}/{name}"],
     "stars": "star_count", "date": "last_updated"},
    {"collection": "github_organizations", "type": "org", "title": "name",
     "subtitle": "description", "ref": ["github.com/{login}"],
     "stars": "followers", "date": "updated_at"},
    {"collection": "institutions", "type": "org", "title": "display_name",
     "subtitle": "country_code", "ref": ["{ror}", "{openalex_id}"]},
    {"collection": "github_users", "type": "person", "title": "name",
     "subtitle": "bio", "ref": ["github.com/{login}"],
     "stars": "followers", "date": "updated_at"},
    {"collection": "authors", "type": "person", "title": "display_name",
     "subtitle": "openalex_id", "ref": ["{orcid}", "{openalex_id}"]},
]

# Heuristic fallbacks for any field a source doesn't declare.
_TITLE_COLS = ("name", "title", "display_name", "repo_id", "full_name", "login", "slug", "id")
_SUBTITLE_COLS = ("description", "summary", "abstract", "headline", "bio", "tagline", "author")
_URL_COLS = ("html_url", "url", "web_url", "infoscience_url", "homepage", "landing_page_url", "uri")
_STAR_COLS = ("stargazers_count", "stars", "star_count", "likes", "downloads", "followers")
_LANG_COLS = ("primary_language", "language", "pipeline_tag", "lang", "sdk")
_LICENSE_COLS = ("license_spdx", "license", "license_name", "license_id", "spdx_id")
_DATE_COLS = ("pushed_at", "last_modified", "updated_at", "last_activity_at",
              "publication_date", "publication_year", "last_updated", "created_at")

_STAR_ICON = {"repo": "★", "model": "♥", "dataset": "↓", "image": "↓", "paper": "❝"}


# ── public API ─────────────────────────────────────────────────────────────
def available_sources() -> list[dict[str, Any]]:
    """The configured sources that are browsable right now, with a label,
    logo and row count (for the source facet)."""
    out: list[dict[str, Any]] = []
    for s in _SOURCES:
        coll = s["collection"]
        if not ddb.is_browsable(coll):
            continue
        label, logo, _host = _source_chrome(coll)
        out.append({
            "collection": coll, "type": s["type"], "label": label,
            "logo": logo, "count": ddb.row_count_for(coll) or 0,
        })
    return out


def facets() -> dict[str, Any]:
    """Filter-chip options: entity types + sources."""
    srcs = available_sources()
    types: dict[str, int] = {}
    for s in srcs:
        types[s["type"]] = types.get(s["type"], 0) + (s["count"] or 0)
    return {
        "types": [{"type": t, "count": n} for t, n in sorted(types.items())],
        "sources": srcs,
    }


def browse(
    *, type: str = "", source: str = "", q: str = "", page: int = 1, page_size: int = 24
) -> dict[str, Any]:
    """A page of normalised catalog items across the in-scope stores.

    ``source`` pins to one collection; ``type`` narrows the source set; ``q`` is
    the per-store substring search. With no ``source`` the page pages each store
    independently and interleaves — approximate (not a single global sort),
    fine for a browse catalog and keeps every store reachable.
    """
    page = max(1, int(page or 1))
    page_size = max(1, min(60, int(page_size or 24)))
    scope = [
        s for s in _SOURCES
        if ddb.is_browsable(s["collection"])
        and (not source or s["collection"] == source)
        and (not type or s["type"] == type)
    ]
    if not scope:
        return {"items": [], "page": page, "has_more": False, "total": 0}

    if len(scope) == 1:
        s = scope[0]
        res = ddb.list_rows(s["collection"], page=page, size=page_size, q=q) or {}
        items = [_item(s, r, res.get("columns", [])) for r in res.get("rows", [])]
        return {"items": items, "page": page,
                "has_more": page < (res.get("pages") or 1),
                "total": res.get("matched", res.get("total", 0))}

    per = max(1, -(-page_size // len(scope)))  # ceil
    buckets: list[list[dict[str, Any]]] = []
    total, has_more = 0, False
    for s in scope:
        res = ddb.list_rows(s["collection"], page=page, size=per, q=q) or {}
        cols = res.get("columns", [])
        buckets.append([_item(s, r, cols) for r in res.get("rows", [])])
        total += res.get("matched", res.get("total", 0)) or 0
        has_more = has_more or page < (res.get("pages") or 1)
    items: list[dict[str, Any]] = []
    for i in range(per):
        for b in buckets:
            if i < len(b):
                items.append(b[i])
    return {"items": items[:page_size], "page": page, "has_more": has_more, "total": total}


def featured(limit: int = 8) -> list[dict[str, Any]]:
    """Curated highlights — most-starred EPFL-affiliated repos, from the graph.
    Returns [] if Neo4j is unreachable (page degrades to the browse grid)."""
    cypher = (
        "MATCH (o:Org)-[:OWNS]->(r:Repo) WHERE toLower(o.login) CONTAINS 'epfl' "
        "OPTIONAL MATCH (r)<-[:STARRED]-(u:User) "
        "WITH o, r, count(DISTINCT u) AS stars WHERE stars > 0 "
        "RETURN o.name AS org, r.full_name AS repo, stars "
        f"ORDER BY stars DESC, repo LIMIT {int(limit)}"
    )
    try:
        from ..routes.ai_tools import run_tool  # lazy: avoid import cycle

        res = run_tool("run_cypher", {"query": cypher})
        rows = res.get("rows", []) if isinstance(res, dict) else []
    except Exception as exc:  # noqa: BLE001 — featured is best-effort
        log.info("catalog featured query failed: %s", exc)
        return []

    items: list[dict[str, Any]] = []
    for r in rows:
        full = str(r.get("repo") or "").replace("https://github.com/", "")
        if not full:
            continue
        items.append({
            "id": full, "title": full.rsplit("/", 1)[-1], "subtitle": str(r.get("org") or ""),
            "type": "repo", "source": "github_repos", "source_label": "GitHub",
            "logo_url": _logo_for("github.com"), "url": "/hub/github.com/" + full,
            "badges": [{"icon": "★", "label": _num(r.get("stars"))}], "featured": True,
        })
    return items


# ── normalisation ─────────────────────────────────────────────────────────
def _item(source: dict[str, Any], row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    cols = {c.lower(): c for c in columns}
    title = _field(source, "title", row, cols, _TITLE_COLS) or "(untitled)"
    ref = _build_ref(source.get("ref"), row)
    label, logo, _host = _source_chrome(source["collection"])
    if not ref:
        # generic URL column, else the collection browser pre-filtered.
        generic = _field(source, "url", row, cols, _URL_COLS)
        ref = _strip_scheme(generic) if generic else None
    url = "/hub/" + ref if ref else f"/hub/c/{source['collection']}?q={title}"
    return {
        "id": str(ref or title)[:200],
        "title": str(title)[:140],
        "subtitle": str(_field(source, "subtitle", row, cols, _SUBTITLE_COLS) or "")[:160],
        "type": source["type"],
        "source": source["collection"],
        "source_label": label,
        "logo_url": logo,
        "url": url,
        "badges": _badges(source, row, cols),
    }


def _field(source: dict, key: str, row: dict, cols: dict, fallback: tuple[str, ...]) -> Any:
    """Explicit column for ``key`` if declared, else first heuristic hit."""
    col = source.get(key)
    if col:
        return _val(row, col)
    for cand in fallback:
        if cand in cols:
            v = _val(row, cols[cand])
            if v is not None:
                return v
    return None


def _badges(source: dict, row: dict, cols: dict) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    stars = _field(source, "stars", row, cols, _STAR_COLS)
    if isinstance(stars, (int, float)) and stars > 0:
        out.append({"icon": _STAR_ICON.get(source["type"], "★"), "label": _num(stars)})
    lang = _field(source, "lang", row, cols, _LANG_COLS)
    if lang:
        out.append({"icon": "", "label": str(lang)[:18], "kind": "lang"})
    lic = _field(source, "license", row, cols, _LICENSE_COLS)
    if lic and str(lic).lower() not in {"none", "null", "other", "unknown"}:
        out.append({"icon": "", "label": str(lic)[:16], "kind": "license"})
    dt = _field(source, "date", row, cols, _DATE_COLS)
    if dt:
        out.append({"icon": "⏱", "label": str(dt)[:10]})
    return out[:4]


def _build_ref(templates: list[str] | None, row: dict) -> str | None:
    """First template whose every {placeholder} is non-empty → ``host/path``."""
    if not templates:
        return None
    keys = {k.lower(): k for k in row}
    for tmpl in templates:
        ph = re.findall(r"\{(\w+)\}", tmpl)
        vals: dict[str, str] = {}
        ok = True
        for p in ph:
            v = _val(row, keys.get(p.lower()))
            if v is None:
                ok = False
                break
            vals[p] = str(v)
        if ok:
            return _strip_scheme(tmpl.format(**vals))
    return None


def _strip_scheme(s: Any) -> str:
    s = str(s or "").strip()
    s = re.sub(r"^https?://", "", s, flags=re.I)
    s = re.sub(r"^www\.", "", s, flags=re.I)
    return s.strip("/")


def _val(row: dict, col: str | None) -> Any:
    if not col:
        return None
    v = row.get(col)
    if v is None:
        return None
    if isinstance(v, str) and v.strip().lower() in {"", "none", "null", "nan"}:
        return None
    return v


def _num(v: Any) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}k".replace(".0k", "k")
    return str(int(n))


# ── chrome (labels + logos), lazy-imported from routes.hub to avoid a cycle ─
_CHROME_CACHE: dict[str, tuple[str, str, str]] = {}


def _source_chrome(collection: str) -> tuple[str, str, str]:
    """``(label, logo_url, host)`` for a collection."""
    if collection in _CHROME_CACHE:
        return _CHROME_CACHE[collection]
    label, host = collection.replace("_", " ").title(), ""
    try:
        from ..routes.hub import _COLLECTION_LABELS  # lazy

        if collection in _COLLECTION_LABELS:
            label, host = _COLLECTION_LABELS[collection]
    except Exception:  # noqa: BLE001
        pass
    out = (label, _logo_for(host) if host else "", host)
    _CHROME_CACHE[collection] = out
    return out


def _logo_for(host: str) -> str:
    if not host:
        return ""
    try:
        from ..routes.hub import _SOURCE_METADATA, _logo_for_host  # lazy

        meta = _SOURCE_METADATA.get(host, {})
        return meta.get("logo_url") or _logo_for_host(host)
    except Exception:  # noqa: BLE001
        return f"https://www.google.com/s2/favicons?domain={host}&sz=32"
