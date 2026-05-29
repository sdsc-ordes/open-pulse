# Open Pulse

Unified CLI, dashboard, and infrastructure repository for graph-analysis
workflows. The Python package lives in `src/open_pulse/`; deployment assets
live under `infra/`; container builds live under `tools/images/`.

## Key Structure

- `src/open_pulse/commands/` — CLI command groups (`deploy`, `quest`, `services`, `gui`, `health`)
- `src/open_pulse/pipeline/` — quest pipeline config, runner, and steps
  (`crawler`, `neo4j_upload`, `metadata_extractor`, `sparql_upload`)
- `src/open_pulse/services/` — shared service clients (Neo4j, SPARQL store,
  GME extractor) + endpoint probes
- `src/open_pulse/utils/grimoire/` — grimoire helpers (SPARQL config gen,
  cron watcher, applier client)
- `src/open_pulse/gui/grimoire_streamlit.py` — Streamlit UI for Grimoire config
- `src/open_pulse/gui/hub/` — **Open Pulse Hub**: FastAPI dashboard / control
  plane (single shared password, light + dark theme, marquee, file-based
  persistence in SQLite + DuckDB)
- `infra/open-pulse-stack/` — the Open Pulse stack: main compose, CLI overlay,
  and the GrimoireLab compose all live here (`docker-compose.yml`,
  `docker-compose.cli.yml`, `docker-compose.grimoirelab.yml`, plus the
  GrimoireLab supporting assets under `grimoirelab/`)
- `infra/services/` — single-service deployment references (`neo4j`,
  `oxigraph`, `sparql-proxy`, `portainer`)
- `tools/images/Dockerfile-open-pulse` — single image used for the CLI, the
  cli-orchestrator container, **and** the hub
- `config/quest.example.yml` — canonical quest config example
- `data/` — bind-mounted runtime state (gitignored, namespaced per service)

## Quick Start

```bash
uv sync --group dev --group test
uv run open-pulse --help
uv run pytest -q
```

## Docker stack

One image (`open-pulse`) plays two roles via compose overrides — the
`open-pulse-cli` orchestrator container sits idle awaiting `docker exec`,
the `open-pulse-hub` container runs the FastAPI dashboard.

### Configuration: where env lives

Two distinct files, two distinct purposes:

- **`<repo>/infra/.env`** — the deployment env. Owns image refs, ports,
  resource limits, storage paths, and **all** container-side credentials and
  per-service knobs. When you bring the stack up from `infra/`, compose
  reads ONLY this file; every service pulls it in via `env_file:`. It is
  auto-seeded from `infra/.env.example` the first time `op deploy up` runs.
- **`<repo>/.env`** — the tool/client env. Consumed by the open-pulse
  Python CLI / hub **only when running on the host against EXTERNAL
  infrastructure** (i.e. someone else's deployed services). Compose never
  loads it. It's a manual copy from `.env.example`.

Both are gitignored. The split honours the principle: when launching from
`infra/`, all env lives in `infra/`; otherwise `<repo>/.env` is just for
the open-pulse tool acting as a client.

### Bring up the stack

```bash
# Build locally (or pull from ghcr.io/sdsc-ordes/open-pulse:latest)
docker build -f tools/images/Dockerfile-open-pulse -t open-pulse:local .

# First run seeds .env and infra/.env from their templates; edit them and
# re-run.
./scripts/op deploy up --profile crawler --profile extractor --profile sparql --profile hub

# Or a host-side compose invocation (note both --env-file flags):
docker compose -f infra/open-pulse-stack/docker-compose.yml \
               -f infra/open-pulse-stack/docker-compose.cli.yml \
               --env-file infra/.env \
               --profile hub up -d
```

Open <http://localhost:9090>, log in with `openpulse` / `$HUB_AUTH` (the
username is free-form; only the password is checked).

The hub talks to the host docker daemon via the bind-mounted socket and
shells `open-pulse deploy ...` into the cli orchestrator container, so all
stack management goes through the same code path whether triggered from the
browser or from `./scripts/op deploy ...` on the host.

`./scripts/op` is a small wrapper that runs `docker exec` against the cli
container with `MSYS_NO_PATHCONV=1` (so git-bash on Windows doesn't mangle
leading-slash arguments).

### Profiles

| Profile | What comes up |
| --- | --- |
| `default` | Neo4j |
| `crawler` | Open Pulse Crawler API |
| `extractor` | GME metadata extractor + Selenium |
| `sparql` | Oxigraph + sparql-proxy |
| `hub` | Open Pulse Hub dashboard (port 9090) |
| `edge` | Caddy TLS-terminating reverse proxy on `:80` + `:443`. Auto Let's Encrypt cert (`HUB_PUBLIC_HOST` is the ACME identifier). Forwards `/` to the hub and `/sparql/*` to the sparql-proxy, so production deploys can serve everything under one HTTPS URL. |
| `grimoirelab` | GrimoireLab DB & worker (within main compose) |
| `orchestration` | Portainer |

The full GrimoireLab stack (Mordred + Sortinghat + OpenSearch + nginx +
projects-applier sidecar) lives alongside the main stack at
`infra/open-pulse-stack/docker-compose.grimoirelab.yml`. Bring it up
alongside the main stack with `--with-grimoire`:

```bash
./scripts/op deploy up --profile hub --with-grimoire
```

## Quest Config

Service endpoints are defined only under `quest.services`. Step-level
endpoint fields are no longer supported. The SPARQL upload step is
technology-agnostic (replaces the old `tentris_upload`):

```yaml
quest:
  name: "my-quest-run"
  retry:
    max_attempts: 3
    backoff_seconds: 5
  logging:
    level: INFO
    file: logs/quest.log
  services:
    crawler:
      endpoint: "http://crawler:8000"
      api_token_env: CRAWLER_API_TOKEN
    neo4j:
      endpoint: "bolt://neo4j:7687"
      auth_env: NEO4J_AUTH
    metadata_extractor:
      endpoint: "http://git-metadata-extractor:1234"
    sparql_store:
      endpoint: "http://sparql-proxy:7878"
      auth_env: SPARQL_AUTH
  steps:
    crawler:
      seeds: ["sdsc-ordes"]
      max_rounds: 2
    neo4j_upload: { enabled: true }
    metadata_extractor:
      enabled: true
      max_repos: 8
    sparql_upload:
      enabled: true
```

## Service Layer

`open_pulse.services`:

- typed service config defaults
- `Neo4jService.upload()` — batched UNWIND MERGE into Neo4j
- `SparqlStoreService.upload()` — JSON-LD → N-Triples → Graph Store HTTP
  Protocol (technology-agnostic; works with Oxigraph, Tentris, …)
- `MetadataExtractorService` — v1 (gimie) + v2 (rule-based) clients
- shared endpoint probe utilities for `open-pulse health`
- run-scoped `ServiceContainer` used by quest pipeline runs

## Open Pulse Hub

Single-page control plane at <http://localhost:9090> when `--profile hub` is
up, or at `https://<HUB_PUBLIC_HOST>/` once the `edge` profile is up too
(see the profile table above). Pages:

- **Overview** — live stat cards, service tiles, quick links
- **Stack** — bring profiles up / down via the deploy CLI over the socket
- **Services** — start / stop / restart, tail logs
- **Pipeline** — discover quest YAMLs and run them
- **Projects** — query SPARQL → preview → POST to the projects.json applier
- **Databases** — DuckDB · SPARQL · Cypher consoles, saved queries in SQLite
- **Knowledge** — paginated browser over every DuckDB store the GME builds
  (github · openalex · snsf · zenodo · communities · oamonitor · …)
- **Logs** — per-container tail with auto-refresh

The hub also proxies the upstream API surfaces so external users only need
the hub URL — no direct port exposure for the GME or crawler:

- `/api/crawler/docs`         — Swagger UI for the Open Pulse Crawler
- `/api/crawler/api/v1/*`     — every crawler endpoint, bearer auto-injected
- `/api/extractor/docs`       — Swagger UI for the GME
- `/api/extractor/v2/*`       — every GME endpoint, bearer auto-injected
- `/sparql/`                  — YASGUI + Oxigraph (via the `edge` proxy)

Top marquee polls aggregated stats every 10s. Light + dark theme toggle in
the sidebar (light mirrors the existing projects-ui's design tokens). State
persists in `data/hub/app.db` (SQLite) and `data/hub/scratch.duckdb`.

### Auth

The hub is a single-tenant app gated by password(s) in `infra/.env`. Two
roles, two env vars:

| Env var | Role | Sidebar / UI |
| --- | --- | --- |
| `HUB_AUTH` | **admin** — required | Full UI; every mutating endpoint allowed |
| `HUB_AUTH_READER` | **reader** — optional | Basic sidebar only (Status · Services · Logs · Resources · Knowledge); mutating endpoints return 403 |

`https://<HUB_PUBLIC_HOST>/` serves a custom HTML login form that accepts
either password; the server stamps the role on the session cookie. Direct
API clients (curl, the v2 SDK, anything sending `Accept: application/json`)
keep getting the bare `401 + WWW-Authenticate: Basic` so existing
automation isn't affected.

Two extra knobs for production deploys:

- `HUB_READONLY=true` — global kill-switch. Mutating endpoints return 403
  even for admin sessions; the sidebar drops Stack + Settings tabs. Use
  during change-freezes or for a public read-only deploy.
- `HUB_MEM_LIMIT=2g` — bump from the 512m default when many DuckDB
  stores (snsf, openalex, swissubase, …) are loaded. The cumulative
  connection pool blows past 512m with the GME 2.1.0 inventory.

## Health Command Defaults

`open-pulse health` sources defaults from shared service config:

- Neo4j HTTP: `http://localhost:7474`
- Neo4j Bolt: `bolt://localhost:7687`
- SPARQL store: `http://localhost:7878`
- GME extractor: `http://localhost:1234`
- GrimoireLab DB: `localhost:5432`

## Grimoire Commands

- `open-pulse services grimoire prepare-config` — generate `projects.json` from SPARQL
- `open-pulse services grimoire apply` — query SPARQL → POST to applier (writes
  `projects.json`, restarts mordred)
- `open-pulse services grimoire install-watcher` — cron-based config watcher
- `open-pulse gui grimoire` — Streamlit UI
- `open-pulse gui hub serve` — FastAPI Hub dashboard

## Docs

- Docusaurus source: `docs-site/`
- Docs index: `docs-site/docs/index.md`
- Legacy static landing: `docs/`
- Container build / hub usage: `tools/images/README.md`
- Compose model: `infra/open-pulse-stack/README.md`

## Project Links

- Pending follow-ups: `dev/plans/follow-ups.md`
- Changelog: `CHANGELOG.md`
- Contributing: `CONTRIBUTING.md`
- Security: `SECURITY.md`
