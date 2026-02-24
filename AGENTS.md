# AGENTS.md

Living project map for AI agents and contributors.

## Directory Layout

```
open-pulse-1/
  pyproject.toml              # Package metadata (hatchling, "open-pulse")
  uv.lock                     # Locked dependency resolution
  src/                        # Python package source (src-layout)
    open_pulse/               # Package source
      __init__.py
      cli.py                  # Typer CLI entry point (registers command groups)
      orchestrator.py         # Sequential orchestrator with checkpoint/resume
      tasks.py                # Task protocol and FunctionTask adapter
      registry.py             # Task registry (deterministic execution order)
      commands/               # CLI command groups
        __init__.py
        deploy.py             # (a) Deploy helper (Docker Compose)
        quest.py              # (b) Quest pipeline execution
        grimoire.py           # (c) GrimoireLab config tools
        health.py             # (d) Service health checks
      grimoire/               # GrimoireLab sub-package
        __init__.py
        sparql_config.py      # SPARQL query + GrimoireLab config gen
        streamlit_app.py      # Streamlit UI (password-protected)
        cronjob.py            # Cron-based config watcher installer
      pipeline/               # Quest pipeline steps and runner
        __init__.py
        config.py             # Pydantic config models for quest YAML
        runner.py             # Pipeline runner (retry, logging, orchestrator)
        crawler.py            # Step: crawl source repos (placeholder)
        neo4j_upload.py       # Step: upload to Neo4j (placeholder)
        metadata_extractor.py # Step: extract metadata (placeholder)
        tentris_upload.py     # Step: upload to Tentris (placeholder)
  tests/                      # pytest test suite
    conftest.py               # Shared fixtures (CliRunner, invoke helper)
    test_cli.py               # CLI entry point: help text, --version, subcommand presence
    test_deploy.py            # deploy up/down/ps: Docker checks, profiles, env creation, volumes
    test_quest.py             # Quest config loading, pipeline runner, retry, CLI commands
    test_grimoire.py          # Grimoire CLI commands, SPARQL config builder, cronjob unit tests
    test_health.py            # Health CLI, endpoint probes, container status, smoke tests
    test_orchestrator.py      # Sequential orchestrator: ordering, checkpoint, resume, failure
  config/                     # Example configuration files
    quest.example.yml         # Example quest pipeline config
  infra/
    services/                 # Per-service deployment assets
      neo4j/
        docker-compose.yaml
        .env.dist
        README.md
      tentris-server/
        tentris-server-config.toml
        gen.sh
        README.md
      portainer/
        docker-compose.yaml
        .env.dist
        README.md
    compose/                  # Cross-service compose overrides
      docker-compose.analysis.override.yml
      docker-compose.grimoirelab.override.yml
      docker-compose.orchestration.override.yml
      README.md
    env/
      .env.example            # Root compose environment template
  tools/
    images/                   # Container image definitions
      Dockerfile-open-pulse   # Production container image (Python 3.11 + uv, entrypoint: open-pulse health)
      Dockerfile-airflow      # Airflow inference image (placeholder -- no source yet)
    scripts/
      run-sequential.sh       # Shell wrapper for sequential run
  docs/                       # Legacy static docs landing
  docs-site/                  # Docusaurus documentation site
  docker-compose.yml          # Root compose (profile-aware topology)
  .devcontainer/              # VS Code/Cursor devcontainer config
  .github/
    CODEOWNERS
    workflows/
      ci.yml                  # Baseline CI (lint, test, pre-commit)
      docker-validate.yml     # Docker build + Trivy security scan
      release.yml             # Semver tag release workflow
      docs-build.yml          # Docusaurus build validation
      docs-pages-deploy.yml   # GitHub Pages deployment
```

## Key Commands

```bash
# CLI package (from project root)
uv sync --group dev --group test
uv run open-pulse --help
uv run pytest -q

# Docker stack (from root)
docker compose up -d
docker compose ps

# Build CLI container (from root)
docker build -f tools/images/Dockerfile-open-pulse -t open-pulse:local .
```

## CLI Command Reference

### `deploy` -- Docker infrastructure management

| Sub-command | Description |
|-------------|-------------|
| `open-pulse deploy up` | Deploy services via Docker Compose. Without `--profile` flags, opens an interactive profile selector. Creates `.env` from `infra/env/.env.example` if absent. |
| `open-pulse deploy down` | Tear down deployed services. Pass `--volumes` / `-v` to also remove named volumes. |
| `open-pulse deploy ps` | Show the status of deployed containers. |

**Profiles** (selectable interactively or via `--profile`):
- `default` -- Core services only (Neo4j)
- `analysis` -- Core + analysis notebook
- `grimoirelab` -- Core + GrimoireLab DB & worker
- `orchestration` -- Core + Portainer management UI

**Options for `deploy up`:**
- `--profile` / `-p` (repeatable) -- Compose profiles to activate
- `--env-file` / `-e` -- Path to a `.env` file (default: `<root>/.env`, auto-created from template)
- `--file` / `-f` (repeatable) -- Extra Compose override files to include

### `quest` -- Analysis pipeline execution

| Sub-command | Description |
|-------------|-------------|
| `open-pulse quest start` | Run the full four-step quest pipeline end-to-end. Uses checkpoint files for resume support. |
| `open-pulse quest run-step <step>` | Run a single pipeline step by name (no checkpoint). |
| `open-pulse quest list-steps` | List available pipeline steps. |

**Pipeline steps** (executed in order):
1. `crawler` -- Crawl source repositories for analysis data (placeholder)
2. `neo4j_upload` -- Upload crawled data into the Neo4j knowledge graph (placeholder)
3. `metadata_extractor` -- Extract metadata from graph-stored artefacts (placeholder)
4. `tentris_upload` -- Upload RDF triples into the Tentris SPARQL store (placeholder)

**Options for `quest start`:**
- `--config` / `-c` -- Path to quest config YAML (default: `quest.yml`; uses built-in defaults when file is absent)
- `--resume` / `-r` -- Resume from last checkpoint, skipping already-completed steps

**Options for `quest run-step`:**
- `--config` / `-c` -- Path to quest config YAML (default: `quest.yml`)

**Config file format** (see `config/quest.example.yml`):
```yaml
quest:
  name: "my-quest-run"
  retry:
    max_attempts: 3
    backoff_seconds: 5
  logging:
    level: INFO
    file: logs/quest.log
  steps:
    crawler:
      enabled: true
      script: "placeholder"
    neo4j_upload:
      enabled: true
      endpoint: "bolt://localhost:7687"
    metadata_extractor:
      enabled: true
    tentris_upload:
      enabled: true
      endpoint: "http://localhost:7502"
```

**Architecture notes:**
- Config is validated by Pydantic models in `src/open_pulse/pipeline/config.py`
- The pipeline runner (`src/open_pulse/pipeline/runner.py`) wraps each step with
  configurable retry logic and delegates to the existing sequential orchestrator
  (`src/open_pulse/orchestrator.py`) for checkpoint/resume support
- Checkpoint files are written to `.quest-checkpoints/<quest-name>.json`
- Individual step modules live under `src/open_pulse/pipeline/` and are
  registered in the runner's `STEP_REGISTRY`

### `health` -- Service health checks

| Usage | Description |
|-------|-------------|
| `open-pulse health` | Run all health checks: Docker daemon reachability, container status, endpoint probes, and smoke tests. Reports results in rich tables. Exits with code 1 when any check fails. |

**Options:**
- `--neo4j` -- Neo4j HTTP endpoint to probe (default: `http://localhost:7474`)
- `--neo4j-bolt` -- Neo4j Bolt endpoint to probe (default: `bolt://localhost:7687`)
- `--tentris` -- Tentris SPARQL endpoint to probe (default: `http://localhost:7502/sparql`)
- `--grimoirelab-db` -- GrimoireLab PostgreSQL host:port (default: `localhost:5432`)

**Checks performed:**
1. Docker daemon reachability (`docker info`)
2. Container status via `docker compose ps` (name, service, state, status, ports)
3. Endpoint probes: HTTP for Neo4j browser and Tentris SPARQL; TCP for Neo4j Bolt and GrimoireLab DB
4. Smoke tests: CLI version, pipeline config schema validation, Compose config validation (when Docker is available)

### `grimoire` -- GrimoireLab configuration tools

| Sub-command | Description |
|-------------|-------------|
| `open-pulse grimoire prepare-config` | Run SPARQL queries against Neo4j/Tentris and generate a GrimoireLab `projects.json` config file. Currently uses a placeholder query. |
| `open-pulse grimoire ui` | Launch a password-protected Streamlit app for visual GrimoireLab config creation. Requires the `grimoire-ui` optional extra. |
| `open-pulse grimoire install-watcher` | Install a cron job that periodically pulls a git repo and detects config file changes. Linux/macOS only. |

**Options for `grimoire prepare-config`:**
- `--neo4j` -- Neo4j Bolt endpoint (default: `bolt://localhost:7687`)
- `--tentris` -- Tentris SPARQL endpoint (default: `http://localhost:7502/sparql`)
- `--output` / `-o` -- Output path for `projects.json` (default: `projects.json`)

**Options for `grimoire install-watcher`:**
- `--repo` / `-r` (required) -- Git remote URL of the repository to watch
- `--config-path` -- Relative path to the config file inside the repo (default: `projects.json`)
- `--branch` / `-b` -- Git branch to track (default: `main`)
- `--schedule` / `-s` -- Cron schedule expression (default: `*/30 * * * *`)
- `--clone-dir` -- Local directory to clone the repo into (default: `~/.open-pulse/grimoire-watcher`)

**Streamlit UI notes:**
- Password protection: set `GRIMOIRE_UI_PASSWORD` env var or add `password` to `.streamlit/secrets.toml`
- Install the optional dependency: `pip install open-pulse[grimoire-ui]`

## Conventions

- **Package name**: `open-pulse` (import as `open_pulse`)
- **Entry point**: `open-pulse` console script → `open_pulse.cli:main` (Typer app)
- **CLI structure**: four command groups registered via `app.add_typer()`:
  `deploy` (Docker deploy), `quest` (pipeline), `grimoire` (GrimoireLab),
  and `health` (top-level service-health command)
- **Commit style**: semantic commits (`feat:`, `fix:`, `refactor:`, etc.)
- **Changelog**: Keep a Changelog format in `CHANGELOG.md`
- **Service assets**: live under `infra/services/<name>/`, not `src/`
- **Project layout**: standard Python src-layout (`pyproject.toml` at root,
  source in `src/open_pulse/`, tests in `tests/`)
- **Container images**: live under `tools/images/`; `Dockerfile-open-pulse` is
  the production image (builds from root context, copies `pyproject.toml`,
  `uv.lock`, and `src/open_pulse/`; default CMD is `health`);
  `Dockerfile-airflow` is a placeholder with no source yet
- **Devcontainer**: `.devcontainer/Dockerfile` installs Python 3.11 + uv;
  `devcontainer.json` runs `uv sync --group dev --group test` on creation
- **CI triggers**: path-scoped; `src/**`, `tests/**`, `pyproject.toml`, and
  `uv.lock` trigger Python CI; `tools/images/**`, `.devcontainer/**`,
  `pyproject.toml`, `uv.lock`, and `docker-compose.yml` trigger Docker
  validation; `docs-site/**` triggers docs build
- **Pre-commit**: configured in `.pre-commit-config.yaml`, Ruff scoped to `src/`

## Dependencies

- Python >=3.11
- uv (package manager)
- Docker + Docker Compose (for service deployment)
- pnpm + Node.js 20 (for docs-site only)

### CLI package (`pyproject.toml`)

Core runtime dependencies:
- `typer` -- CLI framework (replaces argparse)
- `questionary` -- interactive terminal prompts
- `pydantic` -- config validation
- `pyyaml` -- YAML config parsing
- `python-dotenv` -- `.env` file loading
- `rich` -- terminal output formatting (used by typer)

Optional dependency groups:
- `grimoire-ui` (`pip install open-pulse[grimoire-ui]`): `streamlit` for the GrimoireLab config UI
