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
- `infra/compose/` — main stack compose files (image-only, no build context)
- `infra/services/` — per-service deployment assets (`neo4j`, `oxigraph`,
  `sparql-proxy`, `portainer`, `grimoirelab/`)
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

```bash
# Build locally (or pull from ghcr.io/sdsc-ordes/open-pulse:latest)
docker build -f tools/images/Dockerfile-open-pulse -t open-pulse:local .
echo "OPEN_PULSE_IMAGE=open-pulse:local" >> .env

# Bring up the full pipeline + the hub
./scripts/op deploy up --profile crawler --profile extractor --profile sparql --profile hub

# Or a host-side compose invocation:
docker compose -f infra/compose/docker-compose.yml \
               -f infra/compose/docker-compose.cli.yml \
               --env-file .env --profile hub up -d
```

Open <http://localhost:9090>, log in with `admin` / `$HUB_AUTH`.

The hub talks to the host docker daemon via the bind-mounted socket and
shells `open-pulse deploy ...` into the cli orchestrator container, so all
stack management goes through the same code path whether triggered from the
browser or from `./scripts/op deploy ...` on the host.

`./scripts/op` is a small wrapper that runs `docker exec` against the cli
container with `MSYS_NO_PATHCONV=1` (so git-bash on Windows doesn't mangle
leading-slash arguments).

### Profiles

| Profile | What comes up |
|---|---|
| `default` | Neo4j |
| `crawler` | Open Pulse Crawler API |
| `extractor` | GME metadata extractor + Selenium |
| `sparql` | Oxigraph + sparql-proxy |
| `hub` | Open Pulse Hub dashboard (port 9090) |
| `grimoirelab` | GrimoireLab DB & worker (within main compose) |
| `analysis` | Analysis notebook |
| `orchestration` | Portainer |

The full GrimoireLab stack (Mordred + Sortinghat + OpenSearch + nginx +
projects-applier sidecar) lives in a separate compose at
`infra/services/grimoirelab/docker-compose.yml`. Bring it up alongside the
main stack with `--with-grimoire`:

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
      endpoint: "http://extractor:1234"
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
up. Pages:

- **Overview** — live stat cards, service tiles, quick links
- **Stack** — bring profiles up / down via the deploy CLI over the socket
- **Services** — start / stop / restart, tail logs
- **Pipeline** — discover quest YAMLs and run them
- **Projects** — query SPARQL → preview → POST to the projects.json applier
- **Databases** — DuckDB · SPARQL · Cypher consoles, saved queries in SQLite
- **Logs** — per-container tail with auto-refresh

Top marquee polls aggregated stats every 10s. Light + dark theme toggle in
the sidebar (light mirrors the existing projects-ui's design tokens). State
persists in `data/hub/app.db` (SQLite) and `data/hub/scratch.duckdb`.

Set `HUB_AUTH` (any string) in `.env` to gate the dashboard.

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
- Compose model: `infra/compose/README.md`

## Project Links

- Pending follow-ups: `dev/plans/follow-ups.md`
- Changelog: `CHANGELOG.md`
- Contributing: `CONTRIBUTING.md`
- Security: `SECURITY.md`
