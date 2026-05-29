# Open Pulse Stack — Compose Topology

This directory holds the entire Open Pulse stack: the main services, the
CLI orchestrator overlay, and the GrimoireLab stack. All compose files at
this level share the same env-file layering and data-dir conventions.

| File | Role |
| --- | --- |
| `docker-compose.yml` | Main stack. Always include. Holds neo4j, oxigraph, sparql-proxy, crawler, extractor + selenium, hub, grimoirelab-db, portainer, … (most behind `--profile`). |
| `docker-compose.cli.yml` | Overlay that adds `open-pulse-cli` (idle orchestrator container; mounts `/var/run/docker.sock` and the host repo). |
| `docker-compose.grimoirelab.yml` | Full GrimoireLab stack: mariadb, valkey, opensearch, mordred, sortinghat, nginx, projects-applier. Opt in via `--with-grimoire`. |
| `grimoirelab/` | Supporting assets for the GrimoireLab compose: applier source, config templates, sigils, one-shot scripts. |

All compose files are **image-only** — no `build:` blocks. Build artifacts
live in `tools/images/`. To use locally without a published GHCR image,
build the single image and pin it in `infra/.env`:

```bash
docker build -f tools/images/Dockerfile-open-pulse -t open-pulse:local .
# In <repo>/infra/.env:
#   OPEN_PULSE_IMAGE=open-pulse:local
```

(`OPEN_PULSE_IMAGE` defaults to `ghcr.io/sdsc-ordes/open-pulse:latest`.)

## Env files

Compose loads ONE env file: `<repo>/infra/.env`. Every service in the
stack also `env_file:`-pulls it so any per-service knob set there reaches
the container without an explicit `environment:` mapping. Auto-seeded
from `infra/.env.example` on first `op deploy up`.

`<repo>/.env` is the tool/client env, consumed only by the open-pulse
Python CLI / hub when running on the host against EXTERNAL infrastructure.
Compose never reads it.

## Usage

Most users go through the CLI wrapper, which auto-includes the cli overlay
when invoked from inside the orchestrator container:

```bash
./scripts/op deploy up --profile crawler --profile extractor --profile sparql --profile hub
./scripts/op deploy ps
./scripts/op deploy down
```

Or directly via host-side compose (note the two `--env-file` flags):

```bash
# Main stack only
docker compose -f docker-compose.yml \
               --env-file ../.env up -d

# Main stack + CLI orchestrator container
docker compose -f docker-compose.yml -f docker-compose.cli.yml \
               --env-file ../.env \
               --profile hub up -d

# Tear down (matching set of files)
docker compose -f docker-compose.yml -f docker-compose.cli.yml \
               --env-file ../.env down

# Bring grimoirelab up alongside the main stack (--with-grimoire on the CLI;
# raw compose just adds another -f)
docker compose -f docker-compose.yml \
               -f docker-compose.grimoirelab.yml \
               --env-file ../.env up -d
```

## Profiles

| Profile | Adds |
| --- | --- |
| `default` | Neo4j (always present; the implicit "no profile" set) |
| `crawler` | `open-pulse-crawler` |
| `extractor` | `git-metadata-extractor` + `open-pulse-selenium` |
| `sparql` | `oxigraph-open-pulse` + `sparql-proxy-open-pulse` |
| `hub` | `open-pulse-hub` (dashboard on `HUB_PORT`, default 7507) |
| `edge` | `openpulse-edge-proxy` — Caddy on `:80` + `:443`. Terminates TLS via Let's Encrypt (HTTP-01 challenge on `:80`) and reverse-proxies `/` → `hub:8000`, `/sparql/*` → `sparql-proxy:7878`. Caddyfile + cert state live under `infra/services/edge-proxy/`. |
| `grimoirelab` | `grimoirelab-db` + `grimoirelab-worker` (the main-compose lightweight pair) |
| `orchestration` | `portainer` |

For the full GrimoireLab stack (Mordred + Sortinghat + OpenSearch + nginx +
applier), use `--with-grimoire` (CLI) or add
`-f docker-compose.grimoirelab.yml` to the host compose invocation.

## Image references

Both services reference a single image var so a tag bump only edits one place:

```yaml
hub:
  image: "${OPEN_PULSE_IMAGE:-ghcr.io/sdsc-ordes/open-pulse:latest}"
  command: ["gui", "hub", "serve", "--host", "0.0.0.0", "--port", "8000"]

open-pulse-cli:
  image: "${OPEN_PULSE_IMAGE:-ghcr.io/sdsc-ordes/open-pulse:latest}"
  entrypoint: ["sleep", "infinity"]
```

## Bind-mount story

All persistent state lives under `${OPEN_PULSE_DATA_DIR}` (set in
`infra/.env` to an absolute path; defaults to `<repo>/data` when the CLI
seeds the file). Each service writes to its own subdirectory:
`<data>/neo4j/`, `<data>/hub/`, `<data>/oxigraph/`, `<data>/grimoirelab/`,
etc. Override the env var to point at a dedicated partition / SSD.

The cli orchestrator container additionally bind-mounts the **host repo at
its own absolute host path** inside the container (via `OPEN_PULSE_HOST_PATH`)
so when the in-container CLI shells out to `docker compose`, relative bind
paths in the compose file resolve to the **same** filesystem location on
both sides of the docker socket. This is what makes
`open-pulse deploy up` from inside the cli container actually work without
path-translation surprises.
