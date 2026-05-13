# AGENTS.md

Living project map for AI agents and contributors.

## Directory Layout

```
open-pulse/
  pyproject.toml              # Package metadata (hatchling, "open-pulse")
  uv.lock                     # Locked dependency resolution (regen pending — see dev/plans/follow-ups.md)
  scripts/
    op                        # Host-side wrapper: `docker exec` into open-pulse-cli
                              # with MSYS_NO_PATHCONV=1 (git-bash on Windows safe)

  src/                        # Python package source (src-layout)
    open_pulse/
      __init__.py
      cli.py                  # Typer CLI entry point
      orchestrator.py         # Sequential orchestrator with checkpoint/resume
      tasks.py                # Task protocol and FunctionTask adapter
      registry.py             # Task registry (deterministic execution order)

      commands/               # CLI command groups
        deploy.py             # Docker Compose orchestration (auto-detects in-cli)
        quest.py              # Quest pipeline execution
        services.py           # Service-oriented commands (incl. grimoire apply)
        gui.py                # Interactive UIs: streamlit + hub
        health.py             # Service health checks

      utils/grimoire/
        sparql_config.py      # SPARQL → GrimoireLab projects.json generator
        cronjob.py            # Cron-based config watcher installer
        applier_client.py     # SPARQL → projects.json → applier glue (CLI + hub)

      gui/                    # GUI surfaces (ship with the package)
        grimoire_streamlit.py # Streamlit UI (password-protected)
        hub/                  # FastAPI control-plane dashboard
          __init__.py
          main.py             # FastAPI entry, page routes
          auth.py             # HTTP Basic + cookie session (single password)
          config.py           # Env-driven settings
          docker_client.py    # docker SDK wrapper (uses /var/run/docker.sock)
          routes/
            services.py       # list/start/stop/restart/tail logs
            stack.py          # `open-pulse deploy {up,down,ps}` over the socket
            pipeline.py       # `open-pulse quest run <yaml>` over the socket
            projects.py       # SPARQL → projects.json → applier
            databases.py      # DuckDB + SPARQL + Cypher consoles, saved queries
            stats.py          # Aggregated counts for the marquee + tiles
          templates/          # Jinja2 (Tailwind via CDN, Alpine.js, no build step)
            base.html         # Marquee, sidebar, theme switcher
            home.html · stack.html · services.html · pipeline.html
            projects.html · databases.html · logs.html
          static/app.css      # Light + dark theme tokens

      pipeline/               # Quest pipeline steps and runner
        config.py             # Pydantic config models
        runner.py             # Pipeline runner (retry, logging)
        crawler.py            # Step: crawl source repos
        neo4j_upload.py       # Step: upload to Neo4j
        metadata_extractor.py # Step: extract metadata (v1 gimie + v2 endpoint)
        sparql_upload.py      # Step: upload triples to the SPARQL store

      services/               # Shared service clients/config/health probes
        config.py             # Service endpoint defaults + config models
        base.py               # Service protocols
        neo4j.py              # Neo4j service client + upload()
        sparql_store.py       # Technology-agnostic SPARQL store client + upload()
        metadata_extractor.py # GME extractor client (v1 + v2)
        health.py             # Endpoint probe helpers
        container.py          # Run-scoped service container

  tests/                      # pytest test suite (run inside Docker)
    test_grimoire_applier.py  # SPARQL → applier CLI + util tests
    ...

  config/
    quest.example.yml         # Example quest pipeline config

  data/                       # Bind-mounted runtime data (gitignored)
    {neo4j,oxigraph,sparql-proxy,extractor,portainer,grimoirelab-db}/...
    grimoirelab/{mariadb,valkey,opensearch-data,projects-conf,mordred,sortinghat}/...
    hub/{app.db,scratch.duckdb}

  infra/
    .env.example              # Deployment env template (auto-seeded → infra/.env)
    open-pulse-stack/         # Compose stack — all files reference image tags (no build)
      docker-compose.yml      # Main stack (neo4j, oxigraph, sparql-proxy,
                              #             crawler, extractor, hub, …)
      docker-compose.cli.yml  # Overlay for the open-pulse-cli orchestrator
      docker-compose.grimoirelab.yml  # Overlay for the full GrimoireLab stack
      grimoirelab/            # GrimoireLab assets (applier sidecar, config templates,
                              # sigils, scripts)
        applier/{Dockerfile,main.py}  # FastAPI applier sidecar
        config/ · python-scripts/ · scripts/ · README.md
      README.md
    services/                 # Per-service standalone recipes (opt-in, not used by op deploy)
      neo4j/ · oxigraph/ · portainer/ · sparql-proxy/
        sparql-proxy/projects-ui/     # Standalone BYOK projects.json builder

  tools/
    images/
      Dockerfile-open-pulse   # SINGLE image: CLI + orchestrator + hub
                              # → ghcr.io/sdsc-ordes/open-pulse:latest
      README.md               # How to build / publish / run the image

  dev/                        # Carlos's planning workspace
    plans/                    # Plan documents (this file: follow-ups.md)
    tasks/                    # Task lists per plan
    advise/                   # Long-form advisories

  docs/                       # Legacy static docs landing
  docs-site/                  # Docusaurus documentation site
  .devcontainer/              # VS Code/Cursor devcontainer config (Python + uv)
  .github/workflows/          # ci.yml · docker-validate.yml · release.yml · docs-*
```

## Stack architecture

The full open-pulse stack runs in two compose files, both image-only (no
build context). One image (`open-pulse`) plays two roles via compose
overrides:

```
                           open-pulse:local / ghcr.io/sdsc-ordes/open-pulse:latest
                           ├── command: gui hub serve …          → open-pulse-hub
                           └── entrypoint: sleep infinity        → open-pulse-cli
                                  (mounts /var/run/docker.sock and host repo
                                   identity-mapped, so nested `docker compose`
                                   resolves bind paths the same on both sides)

  infra/open-pulse-stack/docker-compose.yml   ┐
   ├── neo4j  oxigraph  sparql-proxy           │ image-only refs;
   ├── crawler  extractor  selenium            │ OPEN_PULSE_IMAGE pulls
   ├── hub  (--profile hub)                    │ from GHCR by default;
   └── grimoirelab-db  portainer …            ─┘ HUB_AUTH gates the hub

  infra/open-pulse-stack/docker-compose.cli.yml
   └── open-pulse-cli (overlay; auto-included when CLI runs inside it)

  infra/open-pulse-stack/docker-compose.grimoirelab.yml
   └── full GrimoireLab stack (mariadb, valkey, opensearch, mordred,
       sortinghat, nginx, projects-applier sidecar) — opt in via
       `open-pulse deploy up --with-grimoire`. Supporting assets at
       `infra/open-pulse-stack/grimoirelab/`.

  data/                                ← single root for all bind mounts
   ├── neo4j/  oxigraph/  …            ← main stack writes here
   └── grimoirelab/{mariadb,valkey,…}  ← grimoire stack writes here
```

## Key Commands

```bash
# Package (host install, optional)
uv sync --group dev --group test
uv run open-pulse --help
uv run pytest -q

# Build the unified image (one image for CLI / orchestrator / hub)
docker build -f tools/images/Dockerfile-open-pulse -t open-pulse:local .
echo "OPEN_PULSE_IMAGE=open-pulse:local" >> infra/.env  # otherwise pulls GHCR

# Bring up the stack (interactive profile picker if no --profile flags)
./scripts/op deploy up --profile crawler --profile extractor --profile sparql --profile hub
# or directly via the host's docker compose (only infra/.env is loaded):
docker compose -f infra/open-pulse-stack/docker-compose.yml \
               -f infra/open-pulse-stack/docker-compose.cli.yml \
               --env-file infra/.env \
               --profile hub up -d

# Hub at http://localhost:9090 — log in with openpulse / $HUB_AUTH

# Talk to the cli orchestrator from the host (handles git-bash path mangling):
./scripts/op deploy ps
./scripts/op deploy ps --with-grimoire
./scripts/op services grimoire apply --sparql http://sparql-proxy:7878
./scripts/op quest run quest-smoke.yml
```

## CLI Command Reference

### `deploy` — Docker infrastructure management

| Sub-command | Description |
| --- | --- |
| `open-pulse deploy up` | Deploy services via Docker Compose. Without `--profile` flags, opens an interactive selector. Compose loads only `<repo>/infra/.env` (auto-seeded from `infra/.env.example` if missing). The tool/client `<repo>/.env` is for the open-pulse Python CLI / hub against external infra and is not a compose input. |
| `open-pulse deploy down` | Tear down deployed services. `--volumes` / `-v` also removes named volumes. |
| `open-pulse deploy ps` | Show the status of deployed containers. |

**Profiles:**
- `default` — Core services only (Neo4j)
- `crawler` — Open Pulse Crawler API
- `extractor` — GME extractor + Selenium
- `sparql` — Oxigraph + sparql-proxy
- `hub` — Open Pulse Hub dashboard (port 9090)
- `grimoirelab` — GrimoireLab DB & worker (within the main compose)
- `orchestration` — Portainer

**Flags applicable to `up` / `down` / `ps`:**
- `--with-cli` — Include `infra/open-pulse-stack/docker-compose.cli.yml`
  (auto-included when the CLI itself runs inside the cli container, via
  the `OPEN_PULSE_RUNNING_IN_CLI_CONTAINER=1` marker).
- `--with-grimoire` — Also include
  `infra/open-pulse-stack/docker-compose.grimoirelab.yml`.

**Project root resolution** (`_find_project_root`):
1. `$OPEN_PULSE_PROJECT_ROOT` if set
2. `$OPEN_PULSE_HOST_PATH` (the cli container's bind-mount root)
3. `cwd` and ancestors
4. `__file__` ancestors (editable / host install)

### `quest` — Analysis pipeline execution

| Sub-command | Description |
| --- | --- |
| `open-pulse quest start` | Run the full four-step pipeline. Checkpoint files in `.quest-checkpoints/`. |
| `open-pulse quest run <yaml>` | Run a quest YAML directly (synchronous; the hub Pipeline page invokes this). |
| `open-pulse quest run-step <step>` | Run a single step. |
| `open-pulse quest list-steps` | List available steps. |

**Pipeline steps** (in order): `crawler` → `neo4j_upload` → `metadata_extractor` → `sparql_upload`.

The `services.sparql_store` block in quest YAML drives the SPARQL upload (renamed
from `tentris` — the service is now technology-agnostic).

### `services grimoire` — GrimoireLab service tools

| Sub-command | Description |
| --- | --- |
| `open-pulse services grimoire prepare-config` | Run SPARQL queries and generate a `projects.json`. |
| `open-pulse services grimoire apply` | Query SPARQL, build `projects.json`, POST to the applier sidecar (which writes the file and restarts mordred). |
| `open-pulse services grimoire install-watcher` | Install a cron job that pulls a git repo and detects config changes (Linux/macOS only). |

`apply` lives in `src/open_pulse/utils/grimoire/applier_client.py` (shared by
the CLI and the hub `/api/projects/*` routes).

### `gui` — Interactive UIs

| Sub-command | Description |
| --- | --- |
| `open-pulse gui grimoire` | Launch the password-protected Streamlit Grimoire UI (requires `[grimoire-ui]`). |
| `open-pulse gui hub serve` | Run the Open Pulse Hub dashboard. Requires `[hub]`. |

**`gui hub serve` options:** `--host`, `--port`, `--reload`. Default port 8000;
the compose `hub` service publishes 9090 → 8000.

### `health` — Service health checks

| Usage | Description |
| --- | --- |
| `open-pulse health` | Docker reachability + container status + endpoint probes + smoke tests. |

## Open Pulse Hub

Single FastAPI app, one shared password (`HUB_AUTH`), HTTP Basic on first
request → 12h cookie session. All Python ships with the package
(`src/open_pulse/gui/hub/`); templates and static assets are package data
(hatchling auto-includes everything under `src/open_pulse/`).

| Path | Purpose |
| --- | --- |
| `/` | Stat cards (services healthy/total, longest uptime, SPARQL repos, Neo4j nodes/rels) · service tiles · quick links |
| `/stack` | Profile checkboxes → `open-pulse deploy {up,down,ps}` exec'd inside the cli container |
| `/services` | Per-container start / stop / restart + logs drawer |
| `/pipeline` | Discover quest YAMLs and run `open-pulse quest run` (sync, with detach option) |
| `/projects` | SPARQL → projects.json → applier (the BYOK builder) |
| `/databases` | Tabbed DuckDB / SPARQL / Cypher consoles + saved queries (SQLite) |
| `/logs` | Per-container log tail with auto-refresh |

Marquee at the top polls `/api/stats/` every 10s.

**Theme system.** Two themes share CSS variables in `static/app.css`. Light
mirrors the existing projects-ui (cobalt accent, Apple-ish neutrals); dark is
the original ink/sky. Toggle in the sidebar footer; persists via
`localStorage.op-hub-theme`.

**Persistence.** `data/hub/app.db` (SQLite — saved queries, history),
`data/hub/scratch.duckdb` (DuckDB scratch). The shared `data/` is mounted
read-only at `/data` inside the hub so DuckDB can read other services' files.

## Conventions

- **Package name**: `open-pulse` (import as `open_pulse`)
- **Entry point**: `open-pulse` console script → `open_pulse.cli:main`
- **CLI structure**: five command groups: `deploy`, `quest`, `services`, `gui`, `health`
- **Commit style**: semantic commits (`feat:`, `fix:`, `refactor:`, etc.)
- **Changelog**: `CHANGELOG.md` (Keep a Changelog format)
- **Service code that gets installed lives in `src/`.** UI templates / static
  assets ship as package data. Deploy / runtime configs live in
  `infra/services/<name>/`. Container build artifacts live in `tools/images/`.
- **Single image.** `tools/images/Dockerfile-open-pulse` builds
  `open-pulse[hub]` + `docker-ce-cli` + `docker-compose-plugin`. Compose
  flips the role with `command:` / `entrypoint:` overrides:
  - hub service → `command: ["gui","hub","serve","--host","0.0.0.0","--port","8000"]`
  - cli orchestrator → `entrypoint: ["sleep","infinity"]` (with the host repo
    bind-mounted at the same absolute path inside, so nested `docker compose`
    bind paths resolve identically on both sides).
- **Image var:** `OPEN_PULSE_IMAGE` (default
  `ghcr.io/sdsc-ordes/open-pulse:latest`). Build local with
  `-t open-pulse:local` and override.
- **Data root:** all persistent state under `data/` at the repo root, namespaced
  per service. Main stack writes to `data/<service>/`; grimoirelab to
  `data/grimoirelab/<service>/`. Configurable via `OPEN_PULSE_DATA_DIR` /
  `GRIMOIRE_DATA_DIR`; `data/` is `.gitignore`d at the repo root.
- **Marker for nested compose**: the cli compose sets
  `OPEN_PULSE_RUNNING_IN_CLI_CONTAINER=1`; the deploy CLI auto-includes the
  cli overlay when it sees this, avoiding spurious "orphan container"
  warnings.
- **Host-identity bind**: the cli compose mounts `${OPEN_PULSE_HOST_PATH}` to
  the same path inside the container so docker-in-docker bind paths resolve
  through Docker Desktop's mount translation.
- **CI triggers** (path-scoped):
  - `src/**`, `tests/**`, `pyproject.toml`, `uv.lock` → Python CI
  - `tools/images/**`, `.devcontainer/**`, `pyproject.toml`, `uv.lock`,
    `infra/open-pulse-stack/docker-compose*.yml` → Docker validation
  - `docs-site/**` → docs build
- **Pre-commit**: `.pre-commit-config.yaml`, Ruff scoped to `src/`

## Dependencies

- Python ≥ 3.11
- uv
- Docker + Docker Compose
- pnpm + Node 20 (docs-site only)

### Optional dependency groups

- `grimoire-ui` (`pip install open-pulse[grimoire-ui]`): `streamlit` for the
  GrimoireLab Streamlit UI.
- `hub` (`pip install open-pulse[hub]`): `fastapi`, `uvicorn`, `jinja2`,
  `python-multipart`, `docker`, `duckdb` for the FastAPI hub dashboard.
  Already pulled by the unified container image.

## Pending / known follow-ups

See `dev/plans/follow-ups.md` for the live list. High-impact entries:
- Publish `ghcr.io/sdsc-ordes/open-pulse:*` from CI.
- Regenerate `uv.lock` to include the `[hub]` extra so the Dockerfile can
  re-add `--frozen` for reproducible builds.
- Pull the grimoire applier sidecar into the main compose (currently the hub
  default `HUB_APPLIER_URL=http://projects-applier:8000` only resolves when
  `--with-grimoire` is up).
