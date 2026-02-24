# AGENTS.md

Living project map for AI agents and contributors.

## Directory Layout

```
open-pulse-1/
  src/                        # Unified Python CLI package ("open-pulse")
    open_pulse/               # Package source
      __init__.py
      cli.py                  # argparse-based CLI entry point
      orchestrator.py         # Sequential orchestrator with checkpoint/resume
      tasks.py                # Task protocol and FunctionTask adapter
      registry.py             # Task registry (deterministic execution order)
    tests/                    # pytest test suite
      test_cli.py
    docker/
      Dockerfile              # Production container image (Python 3.11 + uv)
    scripts/
      run-sequential.sh       # Shell wrapper for sequential run
    pyproject.toml            # Package metadata (hatchling, "open-pulse")
    uv.lock                   # Locked dependency resolution
    README.md                 # CLI package documentation
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
  docs/                       # Legacy static docs landing
  docs-site/                  # Docusaurus documentation site
  tools/                      # Utility scripts
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
# CLI package (from src/)
cd src
uv sync --group dev --group test
uv run open-pulse --help
uv run pytest -q

# Docker stack (from root)
docker compose up -d
docker compose ps

# Build CLI container (from root)
docker build -f src/docker/Dockerfile -t open-pulse:local .
```

## Conventions

- **Package name**: `open-pulse` (import as `open_pulse`)
- **Entry point**: `open-pulse` console script → `open_pulse.cli:main`
- **Commit style**: semantic commits (`feat:`, `fix:`, `refactor:`, etc.)
- **Changelog**: Keep a Changelog format in `CHANGELOG.md`
- **Service assets**: live under `infra/services/<name>/`, not `src/`
- **CI triggers**: path-scoped; `src/**` triggers Python CI, `infra/` and
  `docker-compose.yml` trigger Docker validation
- **Pre-commit**: configured in `.pre-commit-config.yaml`, Ruff scoped to `src/`

## Dependencies

- Python >=3.11
- uv (package manager)
- Docker + Docker Compose (for service deployment)
- pnpm + Node.js 20 (for docs-site only)

### CLI package (`src/pyproject.toml`)

Core runtime dependencies:
- `typer` -- CLI framework (replaces argparse)
- `questionary` -- interactive terminal prompts
- `pydantic` -- config validation
- `pyyaml` -- YAML config parsing
- `python-dotenv` -- `.env` file loading
- `rich` -- terminal output formatting (used by typer)

Optional dependency groups:
- `grimoire-ui` (`pip install open-pulse[grimoire-ui]`): `streamlit` for the GrimoireLab config UI
