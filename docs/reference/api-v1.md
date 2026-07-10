# OpenPulse Hub API — v1 design

**Status:** approved design · **Root:** `/api/v1/` · **Legacy sunset:** 2026-09-15

The definitive contract for the hub API. One versioned, JSON-only surface,
organized by resource. Everything legacy keeps working behind a deprecation
shim (see [Versioning & deprecation](#versioning--deprecation)) until the
sunset date, then is removed.

A shareable rendering of this document lives as a Claude artifact; this file is
the source of truth in-repo.

## Principles

1. **One versioned root.** All machine traffic under `/api/v1/`. The version
   covers the whole contract; a future `/api/v2/` coexists rather than replaces.
2. **JSON-only API.** The API returns data. HTML template partials (the entity
   panels, the collections grid, the leaderboard fragments) move to an
   internal, unversioned `/ui/*` namespace.
3. **Pages & edges stay top-level.** Human pages (`/hub`, `/chaoss`, `/canvas`)
   are unversioned; `/healthz`, `/version`, `/login`, `/logout` stay at the root.
4. **Resources, not RPC.** Plural nouns; ids in the path; state changes are
   explicit sub-path verbs (`/runs/{id}/stop`) — never a body-dispatched
   `/action`.
5. **Two planes, one version.** The *data plane* (stable, public — what tokens
   and the webkit skills consume) and the *control plane* (operator, internal)
   are separated by OpenAPI tag and auth role, not by prefix.
6. **Entities keyed by `?ref=`.** Entity ids move from greedy `{ref:path}`
   catch-alls to a `?ref=` query param, unifying the split HTML/JSON facet
   families into one JSON family.

## Conventions

| Concern | Rule |
|---|---|
| Collection root | `GET /api/v1/things` — no trailing slash (route path `""`) |
| Item | `/things/{id}` |
| Sub-resource | `/things/{id}/parts` |
| Action (non-CRUD) | `POST /things/{id}/{verb}` — e.g. `/stop`, `/restart`, `/revoke` |
| Pagination | `?page=&size=` → `{items, page, size, total}` |
| Errors | FastAPI `{detail}`, everywhere |
| Streaming | named SSE endpoints — `/entities/resolve`, `/ai/chat` |
| Entity id | `?ref=<url-id>` query param, never a path catch-all |

## Versioning & deprecation

A middleware (`deprecation.DeprecationMiddleware`) rewrites every legacy path to
its canonical target internally, so old callers keep working unchanged. On each
legacy hit it:

- stamps `Deprecation: true`, `Sunset: 2026-09-15`,
  `Link: <new>; rel="successor-version"` on the response;
- increments a counter table `api_deprecation_hits(path, canonical, count,
  first_seen, last_seen)` in `app.db`.

`GET /api/v1/system/deprecations` (admin) reads out the counter + active
mappings so we can watch traffic drain. The same migration repoints the hub's
own frontend, the webkit `.env` skills, and the token docs — so remaining hits
are genuinely external. **After the sunset date** legacy returns `410 Gone` with
the `Link` header, then the shim and mapping are deleted.

The mapping registry is `deprecation.LEGACY_MAP` — a mapping is added only once
its canonical target exists.

## Data plane

### Catalog & search
| Method | Canonical | Replaces |
|---|---|---|
| GET | `/api/v1/catalog` | `/api/hub/catalog` |
| GET | `/api/v1/catalog/featured` | `/api/hub/catalog/featured` |
| GET | `/api/v1/catalog/facets` | `/api/hub/facets` |
| GET | `/api/v1/catalog/autocomplete` | `/api/hub/autocomplete` |

### Collections *(kills the `/c/` abbreviation; grid becomes JSON)*
| Method | Canonical | Replaces |
|---|---|---|
| GET | `/api/v1/collections` | `/api/hub/collections` (was HTML) |
| GET | `/api/v1/collections/{name}` | *new — data of `/hub/c/{name}`* |
| GET | `/api/v1/collections/{name}/rows` | `/api/hub/c/{name}/rows` |
| GET | `/api/v1/collections/{name}/export` | `/api/hub/c/{name}/export` |
| GET | `/api/v1/collections/{name}/stats` | `/api/hub/c/{name}/stats` |

### Entities *(one JSON family, `?ref=`)*
| Method | Canonical | Replaces |
|---|---|---|
| SSE | `/api/v1/entities/resolve?ref=` | `/api/hub/resolve-stream/{ref}` |
| GET | `/api/v1/entities/preview?ref=` | `/api/hub/preview/{ref}` |
| GET | `/api/v1/entities/relations?ref=&source=` | `/api/hub/expand` + 6× `/api/hub/expand/*` + `/backlinks` + `/related` |
| GET | `/api/v1/entities/presence?ref=` | `/api/hub/presence/{ref}` |
| GET | `/api/v1/entities/community?ref=` | `/api/hub/community/{ref}` |
| GET | `/api/v1/entities/connected?ref=` | `/api/hub/connected/{ref}` |
| GET | `/api/v1/entities/activity?ref=` | `/api/hub/activity/{ref}` |
| POST | `/api/v1/entities/enrich` | `POST /api/hub/enrich/{ref}` |
| GET | `/api/v1/graphs` | `/api/hub/graphs` |

The HTML facet panels move to `/ui/entity/*`. The `/api/hub/chaoss/{ref}` teaser
is **removed** — its data comes from the Metrics API.

### Wanted backlog
| Method | Canonical | Replaces |
|---|---|---|
| GET | `/api/v1/wanted` | *new — data of `/hub/wanted`* |
| POST | `/api/v1/wanted/{id}/resolve` | `/api/hub/wanted/{id}/resolve` |
| DELETE | `/api/v1/wanted/{id}` | `/api/hub/wanted/{id}` |

### Query consoles
| Method | Canonical | Replaces |
|---|---|---|
| POST | `/api/v1/query/sparql` | `/api/databases/sparql/query` **+** `/api/projects/sparql/query` |
| POST | `/api/v1/query/cypher` | `/api/databases/cypher/query` |
| POST | `/api/v1/query/opensearch` | `/api/databases/opensearch/query` |
| POST | `/api/v1/query/duckdb` | `/api/databases/duckdb/query` |
| GET/POST/DELETE | `/api/v1/query/saved`, `/saved/{name}` | `/api/databases/saved` (+ allow `opensearch` engine) |
| GET | `/api/v1/query/history` | `/api/databases/history` |
| GET | `/api/v1/query/examples` | `/api/databases/examples` |

### Metrics *(already versioned — kept verbatim)*
All `/api/v1/metrics/chaoss/*` endpoints stay unchanged (`topics`, catalogue,
`metrics/{slug}`, per-repo, per-project, `overview`, `repos`). The older
`/api/chaoss/v1/*` aliases fold into the deprecation harness.

## Control plane

### Pipeline *(the `run-*` soup becomes a `runs` resource)*
| Method | Canonical | Replaces |
|---|---|---|
| GET/POST/DELETE | `/api/v1/pipeline/quests`, `/quests/{id}` | `/quests`, `/quest?path=`, `/create`, `DELETE /quest` |
| POST | `/api/v1/pipeline/runs` | `/run` |
| GET | `/api/v1/pipeline/runs[?job_id=]`, `/runs/{id}` | `/runs`, `/run-status`, `/run-by-job` |
| POST | `/api/v1/pipeline/runs/{id}/stop` | `/run-stop` |
| GET | `/api/v1/pipeline/frontier` | `/frontier-preview` |
| GET/DELETE | `/api/v1/pipeline/archives`, `/archives/{name}` | *(shape unchanged)* |

### Projects *(three `build-*` verbs → one discriminated build)*
| Method | Canonical | Replaces |
|---|---|---|
| POST | `/api/v1/projects/build` `{source: repos\|filters\|owner}` | `/build`, `/build-from-filters`, `/build-by-owner` |
| POST | `/api/v1/projects/apply` | `/apply` |
| GET | `/api/v1/projects/templates`, `/facets` | *(shape unchanged)* |
| — | *removed* → `/api/v1/query/sparql` | `/api/projects/sparql/query` |

### Services & stack
| Method | Canonical | Replaces |
|---|---|---|
| GET | `/api/v1/services`, `/services/{name}/logs` | `/api/services/`, `/logs` |
| POST | `/api/v1/services/{name}/start\|stop\|restart` | `POST /api/services/{name}/action` |
| GET/POST | `/api/v1/stack/profiles`, `/up`, `/down`, `/status` | `/api/stack/*` (`ps` → `status`) |

### Proxies *(both standardize on `/upstream/{path}`)*
| Method | Canonical | Replaces |
|---|---|---|
| ANY | `/api/v1/crawler/upstream/{path}` | `/api/crawler/api/v1/{path}` |
| GET/POST/DELETE | `/api/v1/crawler/jobs/{id}[/pause\|resume\|cancel]` | hand-coded `/jobs/*` (legacy dup) |
| ANY | `/api/v1/extractor/upstream/{path}` | `/api/extractor/v2/{path}` |
| GET | `/api/v1/{crawler,extractor}/docs`, `/openapi.json` | *(shape unchanged)* |

### System · AI · Tokens
| Method | Canonical | Replaces |
|---|---|---|
| GET | `/api/v1/system/resources` | `/api/admin/resources` |
| GET | `/api/v1/system/stats`, `/stats/history` | `/api/stats/`, `/api/stats/history` |
| GET | `/api/v1/system/deprecations` | *new — legacy-hit readout* |
| GET/POST/DELETE | `/api/v1/ai/models`, `/schema-context`, `/chat`, `/files` | `/api/ai/*` *(shape unchanged)* |
| GET/POST | `/api/v1/tokens` | `/api/users` |
| PATCH | `/api/v1/tokens/{id}` `{graphs}` | `POST /api/users/{id}/scope` |
| DELETE | `/api/v1/tokens/{id}` | `POST /api/users/{id}/revoke` |
| GET | `/api/v1/tokens/{id}/activity` | `/api/users/{id}/activity` |

## Meta & auth *(unversioned)*
| Method | Path | Note |
|---|---|---|
| GET | `/healthz`, `/version` | unchanged |
| GET/POST | `/login`, `/logout` | browser form + cookie; the duplicate `/logout` in `main.py` is removed (login.py's wins) |

## Removed & merged

These do not get aliases — callers move to the named target.

- **Three SPARQL runners → one.** `/api/databases/sparql/query` +
  `/api/projects/sparql/query` + `/api/hub/expand/sparql` → `/api/v1/query/sparql`
  (console) and `/api/v1/entities/relations?source=sparql` (entity view).
- **Seven `expand/*` + JSON/HTML twins → one.** The `expand` bundle, its six
  sub-routes, and the duplicate `backlinks`/`related` pairs collapse into
  `/api/v1/entities/relations`.
- **CHAOSS teaser removed.** `/api/hub/chaoss/{ref}` → the Metrics API; the
  ad-hoc `_CHAOSS_PANEL_CACHE` goes with it.
- **Crawler legacy job routes removed.** Hand-coded `/jobs/*` → typed
  `/api/v1/crawler/jobs/*`.
- **Duplicate `/logout` removed**; `services /{name}/action` de-RPC'd;
  `ps → status`; `opensearch` added to the saved-query engine allow-list.

## Rollout

Ships as independent PRs to `develop`, each safe on its own.

1. **Deprecation harness.** Middleware + `Deprecation/Sunset/Link` headers +
   `api_deprecation_hits` counter + `/api/v1/system/deprecations` readout. The
   existing CHAOSS `/api/chaoss/v1/*` aliases are the first mappings. *No new
   routes move.* — **this PR.**
2. **Data plane cutover.** Rename catalog / collections / entities / query /
   graphs to canonical; register legacy as shims. Repoint frontend + skills.
3. **Fragment split.** Move HTML facet partials to `/ui/entity/*`; entity API
   becomes JSON-only.
4. **Control plane cutover.** pipeline / projects / services / stack / proxies /
   system / tokens. De-RPC and de-dup as they move.
5. **Drain & delete.** Watch the counter to zero external hits; at sunset flip
   legacy to `410`, then remove the shims.
