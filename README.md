# Open Pulse

Open Pulse is a repository for runtime graph-data services and analysis workflows.
It combines a unified CLI package in `src/` with deployable service assets in
`infra/services/` and shared infrastructure controls in `infra/`.

## Project Purpose

- Provide reproducible graph data services for development and operations.
- Support data/analysis workflows via a unified Python CLI package.
- Keep infrastructure and documentation discoverable for new contributors.

## System Architecture

The repository is organized around three boundaries:

- `src/`: Python package source code (`open-pulse`) using the standard
  src-layout, with `pyproject.toml` and `uv.lock` at the project root.
- `infra/services/`: service-specific deployment assets (Docker Compose files,
  env templates, READMEs) for Neo4j, Tentris, Portainer, etc.
- `infra/`: shared infrastructure configuration (cross-service compose overrides,
  environment templates).

### Decision Note: Boundaries

Use `src/` for CLI source code, `infra/services/` for
per-service deployment assets, and `infra/` for cross-cutting operational
configuration. This separation keeps the CLI release cadence independent of
service deployment changes.

## Service Catalog

| Service | Path | Ports | Compose Profile | Status |
| --- | --- | --- | --- | --- |
| Tentris DB | `infra/services/tentris-server/` | `7502 -> 9080` | `default` | active |
| Neo4j | `infra/services/neo4j/` | `7474`, `7687` | `service-local` | available |
| Portainer | `infra/services/portainer/` | configurable | `service-local` | available |

## Quick Start: DB Stack

From repository root:

```bash
docker compose up -d
```

Check service status:

```bash
docker compose ps
```

Stop the stack:

```bash
docker compose down
```

The root `docker-compose.yml` currently starts Tentris DB (`hackathon-db`) on
`http://localhost:7502`.

## Quick Start: CLI with `uv`

```bash
uv sync
uv run open-pulse --help
```

## Documentation Links

- Docs architecture and migration: `docs/README.md`
- Docusaurus docs root: `docs-site/docs/index.md`
- Getting started docs: `docs-site/docs/getting-started/index.md`
- Architecture docs: `docs-site/docs/architecture/index.md`
- Services docs: `docs-site/docs/services/index.md`
- Analysis docs: `docs-site/docs/analysis/index.md`
- Operations docs: `docs-site/docs/operations/index.md`

## Release and Contribution Links

- Changelog: `CHANGELOG.md`
- Contributing guide: `CONTRIBUTING.md`
- Security policy: `SECURITY.md`
