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
     "stars": "stargazers_count", "metric2": "forks_count",
     "lang": "primary_language", "license": "license_spdx", "date": "pushed_at"},
    {"collection": "gitlab_epfl_projects", "type": "repo", "title": "name",
     "subtitle": "description", "ref": ["{host}/{full_path}"],
     "stars": "star_count", "metric2": "forks_count", "date": "last_activity_at"},
    {"collection": "gitlab_ethz_projects", "type": "repo", "title": "name",
     "subtitle": "description", "ref": ["{host}/{full_path}"],
     "stars": "star_count", "metric2": "forks_count", "date": "last_activity_at"},
    {"collection": "huggingface_models", "type": "model", "title": "repo_id",
     "subtitle": "author", "ref": ["huggingface.co/{repo_id}"],
     "stars": "likes", "metric2": "downloads", "lang": "pipeline_tag",
     "cat": "library_name", "license": "license", "date": "last_modified"},
    {"collection": "huggingface_datasets", "type": "dataset", "title": "repo_id",
     "subtitle": "author", "ref": ["huggingface.co/datasets/{repo_id}"],
     "stars": "likes", "metric2": "downloads", "license": "license",
     "date": "last_modified"},
    {"collection": "huggingface_spaces", "type": "space", "title": "repo_id",
     "subtitle": "author", "ref": ["huggingface.co/spaces/{repo_id}"],
     "stars": "likes", "lang": "sdk", "cat": "hardware", "license": "license",
     "date": "last_modified"},
    {"collection": "zenodo_records", "type": "dataset", "title": "title",
     "subtitle": "description", "ref": ["{zenodo_id}", "{doi}"],
     "hosts": ["zenodo.org", "doi.org"],
     "stars": "downloads", "metric2": "views", "cat": "resource_type",
     "license": "license_id", "date": "publication_date"},
    {"collection": "works", "type": "paper", "title": "title",
     "subtitle": "abstract", "sub_const": "Scholarly work",
     "ref": ["{doi}", "{openalex_id}"], "hosts": ["doi.org", "openalex.org"],
     "date": "publication_year"},
    {"collection": "infoscience_articles", "type": "paper", "title": "title",
     "subtitle": "journal", "sub_const": "EPFL Infoscience",
     "ref": ["{infoscience_url}", "doi.org/{doi}"],
     "hosts": ["infoscience.epfl.ch", "doi.org"],
     "cat": "publication_type", "lang": "language", "date": "publication_year"},
    {"collection": "dockerhub", "type": "image", "title": "name",
     "subtitle": "description", "ref": ["hub.docker.com/r/{namespace}/{name}"],
     "stars": "star_count", "metric2": "pull_count", "date": "last_updated"},
    {"collection": "github_organizations", "type": "org", "title": "name",
     "subtitle": "description", "sub_const": "GitHub organisation",
     "ref": ["github.com/{login}"], "stars": "followers",
     "metric2": "public_repos", "place": "location", "date": "updated_at"},
    {"collection": "institutions", "type": "org", "title": "display_name",
     "subtitle": "", "sub_const": "Research institution",
     "ref": ["{ror}", "{openalex_id}"], "hosts": ["ror.org", "openalex.org"],
     "place": "country_code"},
    {"collection": "github_users", "type": "person", "title": "name",
     "subtitle": "bio", "sub_const": "GitHub user", "ref": ["github.com/{login}"],
     "stars": "followers", "metric2": "public_repos", "cat": "company",
     "place": "location", "date": "updated_at"},
    {"collection": "authors", "type": "person", "title": "display_name",
     "subtitle": "", "sub_const": "Researcher", "ref": ["{orcid}", "{openalex_id}"],
     "hosts": ["orcid.org", "openalex.org"]},
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

# Emoji shown in the card's type pill (and the entity-page hero).
_TYPE_EMOJI = {
    "repo": "📦", "model": "🤖", "dataset": "🗂️", "space": "🚀",
    "paper": "📄", "image": "🐳", "org": "🏛️", "person": "👤",
}

# Icon for a secondary count metric, keyed by the source column it came from.
_COUNT_ICONS = {
    "forks_count": "⑂", "forks": "⑂",
    "downloads": "↓", "downloads_all_time": "↓",
    "views": "👁", "version_views": "👁",
    "public_repos": "📦", "works_count": "📄",
    "contributors": "🧑", "watchers_count": "👀", "open_issues_count": "◍",
}


def type_emoji(entity_type: str) -> str:
    """Public accessor — the emoji for a catalog ``type`` (or a neutral dot)."""
    return _TYPE_EMOJI.get((entity_type or "").lower(), "◆")


def kind_emoji(kind: str) -> str:
    """Emoji for an entity-page ``kind`` label (free text like "GitHub repo").

    Used by the entity-detail hero, which only has the resolver's prose label
    to go on — so we keyword-match rather than look up a catalog ``type``."""
    k = (kind or "").lower()
    rules = (
        (("repo", "gitlab", "project"), "📦"),
        (("model",), "🤖"),
        (("dataset",), "🗂️"),
        (("space",), "🚀"),
        (("paper", "article", "publication", "work"), "📄"),
        (("record", "deposit", "zenodo"), "🗂️"),
        (("organization", "organisation", "institution", " org"), "🏛️"),
        (("user", "person", "author", "researcher"), "👤"),
        (("image", "container", "docker"), "🐳"),
    )
    for needles, emoji in rules:
        if any(n in k for n in needles):
            return emoji
    return "🔗"


def cover_gradient(seed: str) -> str:
    """Deterministic 2-tone gradient for ``seed`` — mirrors the JS ``cover()``
    in catalog.html so an entity's hero matches its catalog card."""
    h = 2166136261
    for ch in seed or "":
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    a = h % 360
    b = (a + 35 + ((h >> 9) % 90)) % 360
    ang = (h >> 4) % 360
    return f"linear-gradient({ang}deg, hsl({a} 62% 52%), hsl({b} 58% 38%))"


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


# UI sort key → which declared source column to order by, and direction.
_SORT_FIELDS = {
    "stars": ("stars", "desc"),
    "recent": ("date", "desc"),
    "name": ("title", "asc"),
}


def _sort_clause(source: dict[str, Any], sort: str) -> str:
    """Map a UI sort key to a ``list_rows`` ``"col[:desc]"`` clause for this
    source, using its declared stars / date / title column. Returns ``""`` for
    the default order or when the source can't honour the key."""
    field, direction = _SORT_FIELDS.get(sort, ("", ""))
    if not field:
        return ""
    col = source.get(field)
    if not col:
        return ""
    return f"{col}:desc" if direction == "desc" else str(col)


def _lang_filter(source: dict[str, Any], langs: list[str]) -> dict[str, Any] | None:
    """``{lang_column: [values]}`` for a source that declares a language
    column, else ``None`` (caller then excludes the source — a language filter
    only makes sense for code sources). Empty ``langs`` → no constraint."""
    if not langs:
        return {}
    col = source.get("lang")
    return {col: langs} if col else None


def _as_list(v: Any) -> list[str]:
    """Coerce a scalar / list / None into a clean list of non-empty strings."""
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    return [str(x).strip() for x in v if str(x).strip()]


# Graph facets resolve to github repos, hydrated from this collection.
_GRAPH_COLLECTION = "github_repos"
_GRAPH_ID_COL = "repo_id"


def browse(
    *, types: Any = None, sources: Any = None, q: str = "", sort: str = "",
    langs: Any = None, graph: dict[str, Any] | None = None,
    page: int = 1, page_size: int = 24,
) -> dict[str, Any]:
    """A page of normalised catalog items across the in-scope stores.

    ``sources`` pins to a set of collections; ``types`` narrows the source set;
    ``q`` is the per-store substring search; ``sort`` reorders by stars /
    recency / name (mapped to each store's own column); ``langs`` filters to
    one-or-more languages (case-insensitive match on each store's declared
    language column — sources without one are dropped).

    ``graph`` is a ``{facet_key: [values]}`` map of GME graph-property
    selections (licence / owner / discipline / repository type / cited works).
    When any graph facet is active the result set is the github repositories
    matching *all* of them (resolved + sorted + paged in SPARQL, then hydrated
    from the ``github_repos`` index); ``langs`` is folded in as a further graph
    constraint. With no graph facet the page interleaves each in-scope store
    independently — approximate, but keeps every store reachable.
    """
    page = max(1, int(page or 1))
    page_size = max(1, min(60, int(page_size or 24)))
    types, sources, langs = _as_list(types), _as_list(sources), _as_list(langs)
    graph = {k: _as_list(v) for k, v in (graph or {}).items()}
    graph = {k: v for k, v in graph.items() if v}

    if graph:
        return _browse_graph(graph, langs, q=q, sort=sort, page=page, page_size=page_size)

    scope = [
        s for s in _SOURCES
        if ddb.is_browsable(s["collection"])
        and (not sources or s["collection"] in sources)
        and (not types or s["type"] in types)
        # A language filter only applies to sources that carry a language col.
        and (not langs or s.get("lang"))
    ]
    if not scope:
        return {"items": [], "page": page, "has_more": False, "total": 0}

    if len(scope) == 1:
        s = scope[0]
        res = ddb.list_rows(
            s["collection"], page=page, size=page_size, q=q,
            sort=_sort_clause(s, sort), filters=_lang_filter(s, langs),
        ) or {}
        items = [_item(s, r, res.get("columns", [])) for r in res.get("rows", [])]
        return {"items": items, "page": page,
                "has_more": page < (res.get("pages") or 1),
                "total": res.get("matched", res.get("total", 0))}

    per = max(1, -(-page_size // len(scope)))  # ceil
    buckets: list[list[dict[str, Any]]] = []
    total, has_more = 0, False
    for s in scope:
        res = ddb.list_rows(
            s["collection"], page=page, size=per, q=q,
            sort=_sort_clause(s, sort), filters=_lang_filter(s, langs),
        ) or {}
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


def _browse_graph(
    graph: dict[str, list[str]], langs: list[str], *,
    q: str, sort: str, page: int, page_size: int,
) -> dict[str, Any]:
    """Graph-facet result set: github repos matching every selected graph
    property, resolved + paged in SPARQL, hydrated from ``github_repos``."""
    from . import facets as facets_mod  # lazy — avoids an import cycle

    selections = dict(graph)
    if langs:  # language has a graph predicate too — AND it in.
        selections["language"] = langs
    res = facets_mod.graph_repo_page(
        selections, sort=sort, page=page, size=page_size
    )
    refs = res.get("refs") or []
    total = res.get("total") or 0
    s = _SOURCE_BY_COLLECTION.get(_GRAPH_COLLECTION)
    if not refs or s is None:
        return {"items": [], "page": page, "has_more": page * page_size < total,
                "total": total}

    hydrated = ddb.rows_for_refs(_GRAPH_COLLECTION, _GRAPH_ID_COL, refs) or {}
    cols = hydrated.get("columns", [])
    rows = hydrated.get("rows", [])
    if q:  # post-filter the page by the free-text query (title / desc / owner)
        ql = q.lower()
        rows = [
            r for r in rows
            if any(ql in str(r.get(c, "")).lower()
                   for c in ("repo_id", "name", "owner", "description"))
        ]
    items = [_item(s, r, cols) for r in rows]
    return {"items": items, "page": page,
            "has_more": page * page_size < total, "total": total}


# SDSC featured plan: (collection, search term, sort clause, how many to take).
# Mixes entity types so the strip shows SDSC's software, datasets and models
# — not only repositories. Repos are filtered to SDSC-owned (see below).
_SDSC_FEATURED_PLAN: list[tuple[str, str, str, int]] = [
    ("github_repos", "sdsc", "stargazers_count:desc", 6),
    ("huggingface_datasets", "SDSC", "", 2),
    ("huggingface_models", "SDSC", "", 2),
    ("zenodo_records", "sdsc", "downloads:desc", 2),
]

_SOURCE_BY_COLLECTION = {s["collection"]: s for s in _SOURCES}


def featured(limit: int = 10) -> list[dict[str, Any]]:
    """One curated highlight strip showcasing SDSC output across entity types.

    Pulls SDSC-affiliated software, datasets and models straight from the
    index and interleaves them so the row reads as a mix of types rather than
    a wall of repositories. Returns ``[]`` (page degrades to the browse grid)
    when nothing matches."""
    buckets: list[list[dict[str, Any]]] = []
    for coll, q, sort, take in _SDSC_FEATURED_PLAN:
        s = _SOURCE_BY_COLLECTION.get(coll)
        if not s or not ddb.is_browsable(coll):
            continue
        res = ddb.list_rows(coll, page=1, size=max(take * 4, take), q=q, sort=sort) or {}
        cols = res.get("columns", [])
        items = [_item(s, r, cols) for r in res.get("rows", [])]
        if coll == "github_repos":
            # ``q=sdsc`` also matches the description — keep only SDSC-*owned*
            # repos so the strip stays on-brand.
            items = [it for it in items if "sdsc" in it.get("url", "").lower()]
        for it in items[:take]:
            it["featured"] = True
        buckets.append(items[:take])

    merged: list[dict[str, Any]] = []
    for i in range(max((len(b) for b in buckets), default=0)):
        for b in buckets:
            if i < len(b):
                merged.append(b[i])
    merged = merged[:limit]
    if not merged:
        return []
    return [{
        "title": "From the Swiss Data Science Center",
        "subtitle": "Software, datasets and models from SDSC",
        "items": merged,
    }]


# ── DuckDB fallback entity (for items no resolver knows) ───────────────────
# Catalog ``type`` → a human "kind" label for the entity-page hero.
_KIND_LABELS = {
    "repo": "Repository", "model": "Model", "dataset": "Dataset",
    "space": "Space", "paper": "Publication", "image": "Container image",
    "org": "Organisation", "person": "Person",
}
# Columns never worth showing as a fact on the fallback page.
_FACT_SKIP_COLS = frozenset({
    "raw", "ingested_at", "card_data", "dataset_info", "languages_json",
    "keywords_json", "node_id", "sha", "readme_path",
})


def _candidate_sources(host: str) -> list[dict[str, Any]]:
    """Sources that could own an entity on ``host`` — by a static template
    prefix, a ``{host}`` placeholder, or an explicit ``hosts`` hint."""
    host = (host or "").lower()
    out: list[dict[str, Any]] = []
    for s in _SOURCES:
        if not ddb.is_browsable(s["collection"]):
            continue
        if host in {h.lower() for h in s.get("hosts", [])}:
            out.append(s)
            continue
        for tmpl in s.get("ref") or []:
            if tmpl.startswith("{"):
                if "{host}" in tmpl:  # dynamic host (gitlab) — verify by row
                    out.append(s)
                    break
            elif tmpl.split("/", 1)[0].lower() == host:
                out.append(s)
                break
    return out


def entity_from_ref(host: str, path: str) -> Any | None:
    """Best-effort fallback entity built straight from a DuckDB row.

    When no resolver knows a URL (Docker images, GitLab projects on a custom
    host, HF datasets not yet in SPARQL/Qdrant, …) but the catalog *does* have
    the row, render a basic page from that row instead of the wanted-list
    placeholder. Returns an ``Entity`` or ``None`` if nothing matches."""
    target = f"{host}/{path}".strip("/").lower()
    # Each store searches a different column (HF on author, Docker on name,
    # …), so try several path segments as the search token, most-distinctive
    # first, until a rebuilt ref matches exactly.
    skip = {"datasets", "spaces", "records", "r", "items", "server", "api", "core"}
    tokens: list[str] = []
    for seg in re.split(r"/", path):
        if seg and seg.lower() not in skip and seg not in tokens:
            tokens.append(seg)
    if not tokens:
        tokens = [host]
    for s in _candidate_sources(host):
        for token in tokens[:4]:
            res = ddb.list_rows(s["collection"], page=1, size=50, q=token[:64]) or {}
            cols = res.get("columns", [])
            for row in res.get("rows", []):
                ref = _build_ref(s.get("ref"), row)
                if ref and _strip_scheme(ref).lower() == target:
                    return _entity_from_row(s, row, cols, host)
    return None


def _ref_tokens(path: str) -> list[str]:
    """Distinctive path segments to use as DuckDB search tokens (stores
    index different columns), most-specific first."""
    skip = {"datasets", "spaces", "records", "r", "items", "server", "api", "core"}
    tokens: list[str] = []
    for seg in re.split(r"/", path):
        if seg and seg.lower() not in skip and seg not in tokens:
            tokens.append(seg)
    return tokens


def duckdb_collections_for_ref(host: str, path: str) -> list[str]:
    """Which catalog (DuckDB) collections hold a row for this URL — the
    presence-panel counterpart of :func:`entity_from_ref`."""
    target = f"{host}/{path}".strip("/").lower()
    tokens = _ref_tokens(path) or [host]
    found: list[str] = []
    for s in _candidate_sources(host):
        matched = False
        for token in tokens[:4]:
            res = ddb.list_rows(s["collection"], page=1, size=50, q=token[:64]) or {}
            for row in res.get("rows", []):
                ref = _build_ref(s.get("ref"), row)
                if ref and _strip_scheme(ref).lower() == target:
                    found.append(s["collection"])
                    matched = True
                    break
            if matched:
                break
    return found


def _entity_from_row(
    source: dict[str, Any], row: dict[str, Any], columns: list[str], host: str
) -> Any:
    from .entity import Entity, Fact  # local import — avoid an import cycle

    item = _item(source, row, columns)
    ext = item["url"]
    canonical = ext if ext.startswith("http") else "https://" + ext.lstrip("/")
    if ext.startswith("/hub/"):
        canonical = "https://" + ext[len("/hub/"):]

    facts: list[Fact] = []
    cols = {c.lower(): c for c in columns}
    for col in columns:
        if col.lower() in _FACT_SKIP_COLS or col.startswith("_"):
            continue
        v = _val(row, cols.get(col.lower()))
        if v is None:
            continue
        sval = str(v)
        if not sval or len(sval) > 400:
            continue
        href = sval if sval.startswith("http") else ""
        facts.append(Fact(label=col, value=sval[:400], href=href, source="index"))

    return Entity(
        ref_url=canonical,
        host=host,
        title=item["title"],
        subtitle=item["subtitle"],
        kind=_KIND_LABELS.get(source["type"], source["type"].title()),
        facts=facts,
        enriched=True,
    )


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
    subtitle = _field(source, "subtitle", row, cols, _SUBTITLE_COLS)
    subtitle = str(subtitle)[:160] if subtitle else source.get("sub_const", "")
    return {
        "id": str(ref or title)[:200],
        "title": str(title)[:140],
        "subtitle": subtitle,
        "type": source["type"],
        "emoji": _TYPE_EMOJI.get(source["type"], "◆"),
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

    # Primary metric (stars / likes / followers / downloads).
    stars = _field(source, "stars", row, cols, _STAR_COLS)
    if isinstance(stars, (int, float)) and stars > 0:
        out.append({"icon": _STAR_ICON.get(source["type"], "★"), "label": _num(stars)})

    # Secondary count metric (forks, downloads, public repos, views, …).
    m2col = source.get("metric2")
    m2 = _val(row, cols.get(str(m2col).lower())) if m2col else None
    if isinstance(m2, (int, float)) and m2 > 0:
        out.append({"icon": _COUNT_ICONS.get(m2col, "#"), "label": _num(m2)})

    # Language / framework / SDK.
    lang = _field(source, "lang", row, cols, _LANG_COLS)
    if lang:
        out.append({"icon": "", "label": str(lang)[:18], "kind": "lang"})

    # Category — resource type, publication type, library, hardware, company.
    catcol = source.get("cat")
    cat = _val(row, cols.get(str(catcol).lower())) if catcol else None
    if cat and str(cat).lower() not in {"none", "null", "other", "unknown"}:
        out.append({"icon": "", "label": str(cat)[:20], "kind": "cat"})

    # License.
    lic = _field(source, "license", row, cols, _LICENSE_COLS)
    if lic and str(lic).lower() not in {"none", "null", "other", "unknown"}:
        out.append({"icon": "", "label": str(lic)[:16], "kind": "license"})

    # Place — a location string or a country code.
    placecol = source.get("place")
    place = _val(row, cols.get(str(placecol).lower())) if placecol else None
    if place:
        is_country = "country" in str(placecol).lower()
        out.append({
            "icon": "🌍" if is_country else "📍",
            "label": (str(place).upper() if is_country else str(place))[:22],
        })

    # Last-updated / publication date.
    dt = _field(source, "date", row, cols, _DATE_COLS)
    if dt:
        out.append({"icon": "⏱", "label": str(dt)[:10]})

    return out[:5]


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
