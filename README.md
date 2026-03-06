# Open Pulse

Open Pulse is a unified CLI and infrastructure repository for graph-analysis workflows.
The Python package lives in `src/open_pulse/`; deployment assets live in `infra/services/`.

## Key Structure

- `src/open_pulse/commands/`: CLI command groups (`deploy`, `quest`, `services`, `gui`, `health`)
- `src/open_pulse/pipeline/`: quest pipeline config, runner, and steps
- `src/open_pulse/services/`: shared service clients/config/probes used by pipeline and health
- `src/open_pulse/utils/grimoire/`: grimoire utilities (SPARQL config generation, cron watcher installer)
- `src/open_pulse/gui/grimoire_streamlit.py`: Streamlit UI for Grimoire config
- `infra/services/`: per-service deployment artifacts (Neo4j, Tentris, Portainer)
- `config/quest.example.yml`: canonical quest config example

## Quick Start

```bash
uv sync --group dev --group test
uv run open-pulse --help
uv run pytest -q
```

## Docker Compose Topology

- `infra/compose/docker-compose.yml`: all infra services
- `infra/compose/docker-compose.cli.yml`: optional CLI container overlay (registry image)

Start infra only:

```bash
docker compose -f infra/compose/docker-compose.yml up -d
```

Start infra + CLI container:

```bash
docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.cli.yml up -d
```

## Quest Config (Breaking Change)

Quest service endpoints are now defined only under `quest.services`.
Step-level endpoint fields were removed and are no longer supported.

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
    neo4j:
      endpoint: "bolt://localhost:7687"
    tentris:
      endpoint: "http://localhost:7502/sparql"
  steps:
    crawler:
      enabled: true
      script: "placeholder"
    neo4j_upload:
      enabled: true
    metadata_extractor:
      enabled: true
    tentris_upload:
      enabled: true
```

## Service Layer

`open_pulse.services` now contains:

- typed service config defaults
- Neo4j and Tentris service wrappers
- shared endpoint probe utilities used by `open-pulse health`
- run-scoped `ServiceContainer` used by quest pipeline runs

## Health Command Defaults

`open-pulse health` now sources Neo4j/Tentris defaults from shared service config:

- Neo4j HTTP: `http://localhost:7474`
- Neo4j Bolt: `bolt://localhost:7687`
- Tentris SPARQL: `http://localhost:7502/sparql`
- GrimoireLab DB: `localhost:5432`

## Grimoire Commands

- `open-pulse services grimoire prepare-config`
- `open-pulse services grimoire install-watcher`
- `open-pulse gui grimoire`

## Docs

- Docusaurus source: `docs-site/`
- Docs index: `docs-site/docs/index.md`
- Legacy static landing: `docs/` (see `docs/README.md`)

## Project Links

- Changelog: `CHANGELOG.md`
- Contributing: `CONTRIBUTING.md`
- Security: `SECURITY.md`
