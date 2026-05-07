# Compose Topology

Two compose files at this level, plus one alongside the grimoirelab service:

| File | Role |
| --- | --- |
| `infra/compose/docker-compose.yml` | Main stack. Always include. Holds neo4j, oxigraph, sparql-proxy, crawler, extractor + selenium, hub, grimoirelab-db, portainer, … (most behind `--profile`). |
| `infra/compose/docker-compose.cli.yml` | Overlay that adds `open-pulse-cli` (idle orchestrator container; mounts `/var/run/docker.sock` and the host repo). |
| `infra/services/grimoirelab/docker-compose.yml` | Standalone grimoirelab stack (mariadb, valkey, opensearch, mordred, sortinghat, nginx, projects-applier). Opt in via `--with-grimoire`. |

All three are **image-only** — no `build:` blocks. Build artifacts live in
`tools/images/`. To use locally without a published GHCR image, build the
single image and pin it in `.env`:

```bash
docker build -f tools/images/Dockerfile-open-pulse -t open-pulse:local .
echo "OPEN_PULSE_IMAGE=open-pulse:local" >> .env
```

(`OPEN_PULSE_IMAGE` defaults to `ghcr.io/sdsc-ordes/open-pulse:latest`.)

## Usage

Most users go through the CLI wrapper, which auto-includes the cli overlay
when invoked from inside the orchestrator container:

```bash
./scripts/op deploy up --profile crawler --profile extractor --profile sparql --profile hub
./scripts/op deploy ps
./scripts/op deploy down
```

Or directly via host-side compose:

```bash
# Main stack only
docker compose -f docker-compose.yml --env-file ../../.env up -d

# Main stack + CLI orchestrator container
docker compose -f docker-compose.yml -f docker-compose.cli.yml \
               --env-file ../../.env --profile hub up -d

# Tear down (matching set of files)
docker compose -f docker-compose.yml -f docker-compose.cli.yml \
               --env-file ../../.env down

# Bring grimoirelab up alongside the main stack (--with-grimoire on the CLI;
# raw compose just adds another -f)
docker compose -f docker-compose.yml \
               -f ../services/grimoirelab/docker-compose.yml \
               --env-file ../../.env up -d
```

## Profiles

| Profile | Adds |
|---|---|
| `default` | Neo4j (always present; the implicit "no profile" set) |
| `analysis` | Analysis notebook |
| `crawler` | `open-pulse-crawler` |
| `extractor` | `open-pulse-extractor` + `open-pulse-selenium` |
| `sparql` | `oxigraph-open-pulse` + `sparql-proxy-open-pulse` |
| `hub` | `open-pulse-hub` (dashboard at port 9090) |
| `grimoirelab` | `grimoirelab-db` + `grimoirelab-worker` (the main-compose lightweight pair) |
| `orchestration` | `portainer` |

For the full GrimoireLab stack (Mordred + Sortinghat + OpenSearch + nginx +
applier), use `--with-grimoire` (CLI) or include
`../services/grimoirelab/docker-compose.yml` (host compose).

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

All persistent state lives under the repo's `data/` directory (gitignored,
namespaced per service). Defaults resolve via `${OPEN_PULSE_DATA_DIR:-../../data}`
for the main compose and `${GRIMOIRE_DATA_DIR:-../../../data/grimoirelab}` for
the grimoirelab compose. Override either to point at a dedicated partition.

The cli orchestrator container additionally bind-mounts the **host repo at
its own absolute host path** inside the container (via `OPEN_PULSE_HOST_PATH`)
so when the in-container CLI shells out to `docker compose`, relative bind
paths in the compose file resolve to the **same** filesystem location on
both sides of the docker socket. This is what makes
`open-pulse deploy up` from inside the cli container actually work without
path-translation surprises.
