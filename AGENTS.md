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
  tests/                      # pytest test suite
    test_cli.py
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
      Dockerfile-open-pulse   # Production container image (Python 3.11 + uv)
      Dockerfile-airflow      # Airflow inference image
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
- **Container images**: live under `tools/images/`
- **CI triggers**: path-scoped; `src/**`, `tests/**`, `pyproject.toml`, and
  `uv.lock` trigger Python CI; `tools/images/**`, `infra/`, and
  `docker-compose.yml` trigger Docker validation
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
