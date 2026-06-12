---
title: The Hub
slug: /hub
---

# The Hub

The Open Pulse Hub is the FastAPI dashboard and control plane shipped in
the unified image (`open-pulse-hub` container, `--profile hub`). It is
the main way humans interact with a deployment: every data plane the
stack runs — Neo4j, the SPARQL store, OpenSearch, the GME indices — is
queryable from the browser, and all stack management goes through the
same `open-pulse` CLI code path whether triggered from the Hub or from
the host.

On the EPFL node the Hub is public at
[openpulse.epfl.ch](https://openpulse.epfl.ch); locally it serves on
`HUB_PORT` (see [Getting Started](../getting-started/index.md)).

## Pages

| Page | What it does |
| ---- | ------------ |
| **Overview** | Live status tiles per service, marquee with store counts, time-series charts sampled every minute (30-day retention). |
| **Query console** | Four engines side by side — **SPARQL**, **Cypher**, **OpenSearch**, **DuckDB** — with a curated, research-focused example library, saved queries, and query history. DuckDB mounts `/data/` read-only, so CSV/Parquet/JSON files on disk are directly queryable. |
| **CHAOSS metrics** | 35 community-health metrics per repository or per GrimoireLab project, each with the exact upstream queries it ran (full transparency), time series, and a public [REST API](../reference/chaoss-api.md). |
| **Knowledge hub** | Entity pages for repos, people and organizations, resolved live from a dozen providers (GitHub, Zenodo, HuggingFace, ROR, Infoscience, OpenAlex, ORCID, …) with semantic search over the GME indices, collections, and a "wanted" backlog. |
| **Projects** | Faceted SPARQL filtering (organization, license, language, discipline, …) → build a `projects.json` → apply it to GrimoireLab via the applier sidecar. |
| **Quests** | Create, run and follow quest YAMLs — executed inside the `open-pulse-cli` container, with live log tailing. |
| **Stack / Services** | Compose profile up/down, per-container start/stop/restart, log tailing. |
| **Agent** | An LLM chat assistant with read-only tools over every store (SPARQL, Cypher, OpenSearch, DuckDB, GME search, CHAOSS, crawler, extractor). Auto-configures against EPFL RCP inference when `RCP_TOKEN` is set; bring-your-own key supported. |
| **Resources** | Disk, RAM, CPU and Docker usage monitoring. |

## Authentication and roles

The login form accepts any username; only the password is checked,
against two env vars:

| Env var | Role | Unlocks |
| --- | --- | --- |
| `HUB_AUTH` | **admin** (required) | Full sidebar, every mutating endpoint (stack control, pipeline runs, projects apply). |
| `HUB_AUTH_READER` | **reader** (optional) | Read-only sidebar (Status · Services · Logs · Resources · Knowledge); mutating endpoints return 403. |

Sessions are cookies with a 12-hour expiry. API clients sending
`Accept: application/json` get a plain `401 + WWW-Authenticate`
challenge instead of a redirect, so `curl` / SDK automation works with
HTTP Basic auth directly.

Two deployment-wide switches:

- `HUB_READONLY=true` — every mutating endpoint returns 403 even for
  admins (change freezes, public read-only deploys).
- `HUB_PUBLIC_KNOWLEDGE=true` — the knowledge-hub pages and the CHAOSS
  API become public (no login); the rest of the dashboard stays gated.

## Proxied API surfaces

The Hub forwards the upstream APIs under stable path prefixes, so
external users never need direct port access — and bearer tokens are
auto-injected server-side:

| Path | Upstream |
| --- | --- |
| `/api/crawler/docs`, `/api/crawler/api/v1/*` | Open Pulse Crawler (bearer from `CRAWLER_API_TOKEN`) |
| `/api/extractor/docs`, `/api/extractor/v2/*` | git-metadata-extractor (bearer from `EXTRACTOR_API_TOKEN`) |
| `/api/v1/metrics/chaoss/*` | [CHAOSS metrics API](../reference/chaoss-api.md) (served by the Hub itself) |

## Persistence

The Hub keeps its state in files under `data/hub/`:

- `app.db` (SQLite) — saved queries, query history, minute-sampled
  metrics history.
- `scratch.duckdb` — the query console's DuckDB workspace.
- `chaoss-cache/` — disk-persisted project-metric cache (survives
  restarts; see
  [Metrics & CHAOSS](../concepts/metrics-and-chaoss.md) for the weekly
  warm job).

## How it controls the stack

The Hub mounts the host Docker socket and `docker exec`s
`open-pulse deploy …` / `open-pulse quest …` into the idle
`open-pulse-cli` orchestrator container. Browser-triggered and
host-triggered operations therefore run exactly the same code — see
[Architecture](../architecture/index.md).
