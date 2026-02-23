# Compose Overrides

This directory contains optional Compose override files grouped by profile.
The canonical stack topology remains in the root `docker-compose.yml`.

## Usage

Run with only the root compose file:

```bash
docker compose up -d
```

Run with profile-specific overrides:

```bash
docker compose -f docker-compose.yml -f infra/compose/docker-compose.analysis.override.yml --profile analysis up -d
docker compose -f docker-compose.yml -f infra/compose/docker-compose.grimoirelab.override.yml --profile grimoirelab up -d
docker compose -f docker-compose.yml -f infra/compose/docker-compose.orchestration.override.yml --profile orchestration up -d
```
