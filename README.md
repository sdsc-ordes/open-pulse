# Open Pulse

Open Pulse is a repository for runtime graph-data services and analysis workflows.
It combines deployable service assets in `src/` with analysis-oriented code in
`analysis/` and shared infrastructure controls in `infra/`.

## Project Purpose

- Provide reproducible graph data services for development and operations.
- Support data/analysis workflows as a separate Python package lifecycle.
- Keep infrastructure and documentation discoverable for new contributors.

## System Architecture

The repository is organized around three boundaries:

- `src/`: runtime services and service-specific deployment assets.
- `analysis/`: standalone Python analysis package (target path:
  `analysis/src/openpulse_analysis`).
- `infra/`: shared infrastructure configuration (cross-service and environment
  concerns).

### Decision Note: Boundaries

Use `src/` for long-running service runtime concerns, `analysis/` for
reproducible research/orchestration code and package metadata, and `infra/` for
cross-cutting operational configuration. This separation avoids coupling analysis
release cadence to service deployment changes.

## Service Catalog

| Service | Path | Ports | Compose Profile | Status |
| --- | --- | --- | --- | --- |
| Tentris DB | `src/tentris-server/` | `7502 -> 9080` | `default` | active |
| Neo4j | `src/neo4j/` | `7474`, `7687` | `service-local` | available |
| GraphDB | `src/graphdb/` | configurable | `service-local` | available |
| Airflow | `src/airflow/` | `8080` | `service-local` | available |
| Portainer | `src/portainer/` | configurable | `service-local` | available |
| GrimoireLab (optional) | `src/` | configurable | `grimoire` | planned |

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

## Quick Start: Analysis with `uv`

Analysis package layout and commands are centered in `analysis/`.

```bash
cd analysis
uv sync
uv run python -m openpulse_analysis --help
```

If `analysis/` is not present in your checkout yet, follow the analysis docs for
the current scaffold status before running commands.

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
