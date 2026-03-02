# Compose Topology

The repository now uses a 2-file Compose model:

- `docker-compose.yml`: all infrastructure services (no CLI container)
- `docker-compose.cli.yml`: optional `open-pulse-cli` service overlay

## Usage

Start infra services only:

```bash
docker compose -f infra/compose/docker-compose.yml up -d
```

Start infra + CLI container:

```bash
docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.cli.yml up -d
```

Stop infra only:

```bash
docker compose -f infra/compose/docker-compose.yml down
```

Stop infra + CLI:

```bash
docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.cli.yml down
```

## CLI image source

`docker-compose.cli.yml` uses a registry image and does not build locally.
Override the image with:

```bash
OPEN_PULSE_CLI_IMAGE=ghcr.io/<org>/<repo>:<tag>
```
