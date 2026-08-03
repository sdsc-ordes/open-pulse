# Hub Catalog — design

## Goal
A new **Catalog** section in the hub: a visual, browsable catalog of the data
plane's items/entities. Two parts:

1. **Featured highlights** (curated) — a hero row of standout research software.
2. **Browse grid** (all entity types) — a responsive, filterable card grid.

Cards use a **generated cover** (deterministic gradient + SVG pattern seeded by
the item id — no external assets) with the **source/org logo** and **data
badges** overlaid. Lives at `/hub/catalog`, linked from the **Explore** sidebar
group.

Approach **C (hybrid)**: featured from the graph (Neo4j), grid from the
browsable DuckDB stores. **Reuse as much existing machinery as possible.**

## What we reuse (no new data infra)
- `knowledge/duckdb_browser.py` `_BACKING` / `_build_auto_backing` / `_connect`
  — the registry of browsable stores, the `.ro.duckdb` snapshot preference, and
  per-store column/stat detection. The catalog enumerates and reads stores
  through this, so every store the collection browser already shows is
  catalog-able for free.
- `routes/hub.py` `_COLLECTION_LABELS`, `_SOURCE_METADATA`, `_logo_for_host`
  — source labels + logos for the card chrome.
- The entity resolve flow (`/hub/<ref>`) — each card links to the existing
  detail page; no new detail UI.
- The curated Cypher from `db_examples.py` (most-starred / EPFL-flagship
  repos) — drives the featured row.
- Existing CSS: `.card`, the chaoss/collection grid + skeleton patterns, the
  themed scrollbars, pill badges. Cover art is pure CSS/SVG.

## Data: `CatalogItem` (one uniform shape)
```
{
  id:          str   # stable key (also the cover-art seed)
  title:       str
  subtitle:    str   # 1-line (description / authors+year / handle)
  type:        str   # repo | dataset | paper | model | org | person
  source:      str   # collection key (github_repos, zenodo_records, …)
  source_label:str
  logo_url:    str
  url:         str   # entity detail page ("/hub/<ref>")
  badges:      [ {icon, label} ]   # ⭐ stars · lang · license · ⏱ updated · discipline
}
```

## Backend
`knowledge/catalog.py`:
- `SOURCES` — a small ordered list of catalog-worthy collections (github_repos,
  zenodo_records, openalex works, huggingface_models, infoscience, ror, …),
  each with `{type, title_col?, subtitle_col?, badge_cols?}`. Unspecified
  columns fall back to a **generic adapter** that reuses the browser's detected
  title/description/text columns — so adding a store needs zero or one line.
- `featured()` — runs the curated Cypher → `CatalogItem`s (with stars).
- `browse(type?, source?, q?, page, page_size)` — for each in-scope store,
  reuses the collection row read (`_connect` + the backing's select_sql/search)
  to pull a page of rows, maps each to a `CatalogItem` via its adapter, and
  interleaves sources. Filtering by `source` queries just that store; `type`
  filters the source set; `q` reuses the per-store search columns.

`routes/hub.py`:
- `GET /hub/catalog` → `hub/catalog.html` (page shell, featured rendered
  server-side or lazy).
- `GET /api/hub/catalog` (q/type/source/page) → `{items, facets, page,…}`.
- `GET /api/hub/catalog/featured` → the curated row.

## Frontend (`templates/hub/catalog.html`)
- Header: title + search box + result count.
- Featured: horizontally-scrollable hero cards (lazy-fetched).
- Filters: facet chips — **type** + **source** (cheap, from the registry);
  language/license/discipline as a v2 nicety.
- Grid: Alpine component lazy-fetches `/api/hub/catalog`, renders the **card
  component** (cover + logo + badges), paginates (infinite scroll / "load
  more"). Card click → `item.url`.
- **Cover art**: a small JS helper hashes `item.id` → two hues + a pattern
  index → a CSS `linear-gradient` + an inline SVG pattern. Deterministic, every
  card distinct, zero assets, works on both themes.
- Sidebar: add `("/hub/catalog", "catalog", "Catalog", <icon path>)` to
  `nav_explore`.

## Scope (v1) / YAGNI
- **In:** featured row + filterable grid; type+source facets; generated cover +
  logo + a few badges; pagination; the ~6–8 stores already browsable.
- **Out (later):** language/license/discipline facets, saved views, per-card
  hover previews, server-side full-text across all stores at once.
- No new ingestion, no new store, no entity-detail rewrite.

## Risks / notes
- Stores have uneven columns → the generic adapter + explicit overrides for the
  few high-value stores (github_repos, zenodo, openalex) keep cards sensible.
- "Interleave + paginate across stores" is approximate (per-store paging, not a
  global sort) — acceptable for a browse catalog; documented in the endpoint.
- DuckDB reads already go through `.ro.duckdb` (read-only, no lock contention).
