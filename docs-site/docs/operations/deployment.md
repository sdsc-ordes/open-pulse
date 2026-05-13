---
title: Deployment
slug: /operations/deployment
---

# Deployment

This is the canonical end-to-end deployment guide for Open Pulse on a fresh
Linux host. It captures every gotcha and recovery procedure we learned from
the first production-style bring-up: image auth, port remapping,
bind-mount permissions, env-file layout, and the smoke tests that prove
the stack is actually working.

If this is your first time touching the project, read
[Getting Started](../getting-started/index.md) first — it covers the same
flow at a much shallower level. This page is for the operator who needs to
get the stack working on a real host without surprises.

:::tip Audience
You are an operator deploying the full Open Pulse stack on a Linux host
you control. You have `sudo`, you can install packages, and you can edit
files under the repo root. If you only want to point a local CLI at
someone else's Open Pulse infrastructure, you do not need this guide —
fill out `<repo>/.env` and use the package directly.
:::

---

## 1. Prerequisites

### Host operating system

- **Ubuntu 24.04 LTS** is the reference host (this is what we deploy on).
- Any recent Linux distribution with Docker and the Compose plugin will work.
- Windows / macOS via Docker Desktop are supported for development but are
  **not** the target of this guide; some path conventions documented here
  assume Linux.

### Required packages

```bash
# Docker Engine + Compose plugin (Ubuntu 24.04)
sudo apt update
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Verify
docker --version
docker compose version
```

Add your operator user to the `docker` group if you don't want to type
`sudo` for every command:

```bash
sudo usermod -aG docker "$USER"
# Log out and back in for the group change to take effect.
```

### Hardware sizing

The stack is memory-bound — every JVM and search index wants its slice.
The numbers below are steady-state container memory consumption when the
full `--with-grimoire` stack is running and indexing repos.

| Service                | Approx. RSS | Notes                                 |
| ---------------------- | ----------: | ------------------------------------- |
| OpenSearch             |       1.5 G | `-Xms1g -Xmx1g` + Lucene off-heap     |
| Neo4j                  |       2.0 G | 1 G heap + 512 M page cache + native  |
| git-metadata-extractor |       1.5 G | LLM clients + RAG indices             |
| Mordred                |       1.5 G | Perceval workers + sirmordred         |
| Qdrant                 |     ~ 700 M | Per-collection memory grows with data |
| Selenium / Chromium    |     ~ 600 M | 1 session, headless                   |
| MariaDB + Valkey       |     ~ 500 M | Combined                              |
| OpenSearch Dashboards  |     ~ 500 M | Kibana fork                           |
| SortingHat + worker    |     ~ 400 M |                                       |
| Crawler                |     ~ 300 M |                                       |
| Hub + Caddy + nginx    |     ~ 250 M |                                       |
| Portainer              |     ~ 150 M | If `--profile orchestration`          |

Recommended host:

| Resource | Minimum   | Comfortable | Notes                                                |
| -------- | --------- | ----------- | ---------------------------------------------------- |
| RAM      | 8 GiB     | **16 GiB+** | Stack works at 8 GiB but is tight; OOMs under load.  |
| CPU      | 4 vCPU    | 8 vCPU      | Extractor + OpenSearch + Mordred are all CPU-hungry. |
| Disk     | 40 GiB    | 100 GiB+    | Indices grow with repo count.                        |
| Swap     | 30+ GiB   | 30+ GiB     | Safety net for the JVMs under burst load.            |

:::warning Provision swap
Several services (OpenSearch, Neo4j, the extractor) will briefly burst
above their `mem_limit` during startup. Without swap, the kernel OOM
killer will reap them on a 8 GiB host. Provision **at least 30 GiB**
of swap on the host as a safety net.

```bash
sudo fallocate -l 32G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```
:::

---

## 2. Port allocation

Container ports inside the compose network are fixed. Host-published
ports are configurable via `infra/env/.env`. The defaults are
developer-friendly but tend to collide with other things on a shared
host. The mapping below is the **firewall-friendly EPFL layout** we
landed on:

| Host port | Container             | Internal port | Env var              |
| --------- | --------------------- | ------------- | -------------------- |
| **7502**  | `sparql-proxy`        | 7878          | `SPARQL_PROXY_PORT`  |
| **7503**  | `neo4j` (Browser/HTTP)| 7474          | `NEO4J_HTTP_PORT`    |
| **7504**  | `neo4j` (Bolt)        | 7687          | `NEO4J_BOLT_PORT`    |
| **7507**  | `open-pulse-hub`      | 8000          | `HUB_PORT`           |
| **7508**  | `grimoirelab nginx`   | 8000          | hard-coded in compose|
| **7509**  | `portainer`           | 9000          | `PORTAINER_PORT`     |

To pick different ports, edit the corresponding env var in
`infra/env/.env`:

```bash
# infra/env/.env
NEO4J_HTTP_PORT=7503
NEO4J_BOLT_PORT=7504
SPARQL_PROXY_PORT=7502
HUB_PORT=7507
PORTAINER_PORT=7509
```

:::warning Update the hub's external URLs too
Some hub config variables refer to the **externally published** port (so
the browser can navigate to them via the firewall). These do **not**
auto-follow `*_PORT` changes — you must update them by hand:

```bash
# infra/env/.env  (use the host's public hostname or IP)
HUB_NEO4J_BROWSER_URL=http://<host>:7503
HUB_SPARQL_BROWSER_URL=http://<host>:7502
```

Without these the hub's quick-link tiles point at the default
`localhost:7474` / `localhost:7878`, which fail from any browser other
than one running on the deploy host.
:::

The GrimoireLab nginx port is hard-coded in
`infra/open-pulse-stack/docker-compose.grimoirelab.yml`
(`7508:8000`). If 7508 is already in use, edit that file directly —
`PORTAINER_PORT` defaults to 7508 in `.env.example` and **must** be
moved to avoid the collision (we use 7509).

---

## 3. Env-file layout

There are two env files in the repo, and they serve different purposes.
Mixing them up is the most common bring-up mistake.

### `infra/env/.env` — the deployment env

This is the authoritative config for the local stack. **Compose loads
only this file.** Every service in `docker-compose.yml`,
`docker-compose.cli.yml`, and `docker-compose.grimoirelab.yml` pulls it
via `env_file:`, so every key set here is visible to every container.

Contents:

- Image references (`OPEN_PULSE_IMAGE`, `CRAWLER_IMAGE`,
  `EXTRACTOR_IMAGE`, `OPEN_PULSE_APPLIER_IMAGE`)
- Host-published port maps (`NEO4J_HTTP_PORT`, `HUB_PORT`, …)
- Resource limits (`NEO4J_MEM_LIMIT`, `OPENSEARCH_JAVA_OPTS`, …)
- Container-internal passwords (`NEO4J_AUTH`, `OPENSEARCH_PASSWORD`,
  `MYSQL_ROOT_PASSWORD`, …)
- Service tokens (`CRAWLER_API_TOKEN`, `EXTRACTOR_API_TOKEN`,
  `EXTRACTOR_GITHUB_TOKEN`, `EXTRACTOR_OPENAI_API_KEY`, …)

### `<repo>/.env` — the tool/client env

This is for the open-pulse Python CLI / hub when running on the host as
a **client** against external infrastructure (someone else's Neo4j,
SPARQL store, crawler). **Compose never loads it.** If you are bringing
infra up locally, this file is irrelevant — ignore it.

:::warning Critical gotcha: the env-file path mismatch
The compose files reference `../.env` (i.e. `infra/.env`), but the
canonical file in this repo lives at `infra/env/.env`. The first
`docker compose up` will fail with:

```
env file /open-pulse/open-pulse/infra/.env not found: stat
  /open-pulse/open-pulse/infra/.env: no such file or directory
```

Fix once, before the first up: symlink it.

```bash
cd /open-pulse/open-pulse/infra
ln -s env/.env .env
ls -l .env   # → .env -> env/.env
```

This makes both `--env-file infra/.env` (host-side compose invocation)
and the in-compose `env_file: ../.env` directives resolve to the same
file. The same trick exposes `infra/.env.example` cleanly to anyone who
wants to bootstrap from the template.
:::

### Seeding from the example

If you are starting fresh:

```bash
cp /open-pulse/open-pulse/infra/.env.example /open-pulse/open-pulse/infra/env/.env
ln -s env/.env /open-pulse/open-pulse/infra/.env
$EDITOR /open-pulse/open-pulse/infra/env/.env
```

Then work through sections 4 and 5 below before bringing the stack up.

---

## 4. Image preparation

The stack pulls four images that need attention before the first up:

### 4.1 Private GHCR images (crawler + extractor)

```
ghcr.io/sdsc-ordes/open-pulse-crawler:develop
ghcr.io/imaging-plaza/git-metadata-extractor:develop
```

Both repositories are **private**. The first `docker compose up` will
fail with:

```
Error response from daemon: Head "https://ghcr.io/v2/.../manifests/develop":
  denied: denied
unauthorized
```

Fix: log in to GHCR once with a GitHub Personal Access Token that has
the `read:packages` scope.

```bash
# Generate a PAT at https://github.com/settings/tokens
# (classic PAT; scopes: read:packages)
sudo docker login ghcr.io -u <your-gh-username>
# Paste the PAT at the password prompt.
```

The credentials persist in `/root/.docker/config.json` (or
`~/.docker/config.json` for the user invoking `docker login`), so this
is a one-time step. Compose will pick them up automatically on the next
pull.

:::note Pin the tags
`:latest` is not published for these two images. The shipped defaults
already pin `:develop`. If you want a reproducible deploy, override
both via `CRAWLER_IMAGE` / `EXTRACTOR_IMAGE` in `infra/env/.env`.
:::

### 4.2 The Open Pulse image (CLI + hub)

The unified Open Pulse image plays two roles in the stack: the
orchestrator container (`open-pulse-cli`) and the dashboard
(`open-pulse-hub`). Compose flips the role via the `command:` /
`entrypoint:` overrides.

If you have access to GHCR you can use the published tag (default:
`ghcr.io/sdsc-ordes/open-pulse:latest`). Otherwise build it locally:

```bash
cd /open-pulse/open-pulse
docker build -f tools/images/Dockerfile-open-pulse -t open-pulse:local .
```

Then pin the local tag in `infra/env/.env`:

```bash
# infra/env/.env
OPEN_PULSE_IMAGE=open-pulse:local
```

### 4.3 The GrimoireLab applier sidecar (always-local)

The projects-applier sidecar that backs the hub's `/projects` page is
**not published anywhere**. You must build it locally before bringing
the GrimoireLab stack up:

```bash
docker build -t open-pulse-applier:local \
  /open-pulse/open-pulse/infra/open-pulse-stack/grimoirelab/applier
```

Then pin it in `infra/env/.env`:

```bash
# infra/env/.env
OPEN_PULSE_APPLIER_IMAGE=open-pulse-applier:local
```

Without this, the first `--with-grimoire` up fails with:

```
Error response from daemon: pull access denied for open-pulse-applier,
repository does not exist or may require 'docker login'
```

---

## 5. Linux-only env corrections

`infra/env/.env` ships with Windows / Docker Desktop dev defaults. On a
real Linux host, three keys must be changed before the first up:

### 5.1 `OPEN_PULSE_DATA_DIR`

Default: `D:/open-pulse/data` (Windows path).

Set this to an **absolute Linux path** under which all persistent data
will live:

```bash
# infra/env/.env
OPEN_PULSE_DATA_DIR=/open-pulse/open-pulse/data
# or, for a separate disk:
# OPEN_PULSE_DATA_DIR=/srv/open-pulse/data
```

:::warning Use an absolute path
Relative paths (like the shipped `./data`) resolve **differently**
depending on whether the stack is brought up via `op deploy …` or via
raw `docker compose -f infra/open-pulse-stack/…`. Always use an
absolute path here.
:::

### 5.2 `OPEN_PULSE_HOST_PATH`

Default: `/d/open-pulse` (Docker-Desktop-on-Windows form of `D:\open-pulse`).

Set this to the **absolute host path of this repo**. The CLI
orchestrator container uses an identity bind-mount (source == target)
so that nested `docker compose` bind paths inside the orchestrator
resolve to the same place on the host:

```bash
# infra/env/.env
OPEN_PULSE_HOST_PATH=/open-pulse/open-pulse
```

If `OPEN_PULSE_HOST_PATH` is unset or wrong, the `open-pulse-cli`
container fails to start with:

```
working_dir: required key OPEN_PULSE_HOST_PATH is not set
```

### 5.3 `V2_PROVIDER_CACHE_PATH`

Default: `.cache/v2/providers.db` (relative).

The extractor's V2 RAG path tries to open this file at startup. With
the relative default it lands at `/app/.cache/v2/providers.db` inside
the container, but `/app` is **owned by root** and the extractor runs
as `non-root-user` — so SQLite can't open it and every `/v2/extract`
request returns HTTP 500.

Set the cache path to a location inside the writable `/app/data`
bind-mount:

```bash
# infra/env/.env
V2_PROVIDER_CACHE_PATH=/app/data/.cache/v2/providers.db
```

This pairs with the `${OPEN_PULSE_DATA_DIR}/extractor:/app/data` bind
mount declared in the extractor service, so the cache survives
container recreations.

---

## 6. Bring-up sequence

### 6.1 First up — bypass the wrapper

`scripts/op` is a thin wrapper that does `docker exec` into the
`open-pulse-cli` orchestrator container. On a fresh host that container
doesn't exist yet, so the first up must be done via raw
`docker compose`:

```bash
cd /open-pulse/open-pulse

docker compose \
  -f infra/open-pulse-stack/docker-compose.yml \
  -f infra/open-pulse-stack/docker-compose.cli.yml \
  --env-file infra/.env \
  --profile crawler \
  --profile extractor \
  --profile sparql \
  --profile hub \
  up -d
```

This starts:

- `neo4j` (default profile, always included)
- `open-pulse-crawler` (`--profile crawler`)
- `git-metadata-extractor` + `gme-qdrant` + `selenium` (`--profile extractor`)
- `oxigraph` + `sparql-proxy` (`--profile sparql`)
- `open-pulse-hub` (`--profile hub`)
- `open-pulse-cli` (always included via the `cli.yml` overlay)

### 6.2 Subsequent ups — use the wrapper

Once `open-pulse-cli` is up, `scripts/op` works:

```bash
./scripts/op deploy ps
./scripts/op deploy up --profile crawler --profile extractor --profile sparql --profile hub
./scripts/op deploy down
```

The orchestrator auto-detects that it's running inside the cli
container (via `OPEN_PULSE_RUNNING_IN_CLI_CONTAINER=1`) and
auto-includes the cli overlay, so you don't have to repeat `--with-cli`.

### 6.3 Including GrimoireLab

Add `-f infra/open-pulse-stack/docker-compose.grimoirelab.yml` (raw
compose) or `--with-grimoire` (via `scripts/op`) to bring the full
GrimoireLab stack up alongside:

```bash
docker compose \
  -f infra/open-pulse-stack/docker-compose.yml \
  -f infra/open-pulse-stack/docker-compose.cli.yml \
  -f infra/open-pulse-stack/docker-compose.grimoirelab.yml \
  --env-file infra/.env \
  --profile crawler --profile extractor --profile sparql --profile hub \
  up -d
```

Or:

```bash
./scripts/op deploy up --with-grimoire \
  --profile crawler --profile extractor --profile sparql --profile hub
```

This adds `mariadb`, `valkey`, `opensearch-node1`,
`opensearch-dashboards`, `mordred`, `sortinghat`, `sortinghat_worker`,
`nginx`, `projects-applier`, and the init sidecars
(`prepare-grimoire-config`, `prepare-opensearch`).

:::tip Profile semantics
The hub stays on `--profile hub`; GrimoireLab services have **no**
profile and come up automatically when the grimoirelab compose file is
included. You can mix and match — bringing up `--with-grimoire`
without `--profile hub` runs the dashboards without the open-pulse
control plane.
:::

---

## 7. Permission fixes

Every service runs as a different in-image UID. Docker auto-creates
missing bind-mount source directories as `root:root`, which then
**blocks** the service user from writing. The first symptom is usually
`PermissionError: [Errno 13] Permission denied` in the service's logs
and a `restarting (1)` loop.

### UID matrix

| Service                | Image                                | Runtime user (UID:GID) |
| ---------------------- | ------------------------------------ | ---------------------- |
| OpenSearch             | `opensearchproject/opensearch:3`     | `opensearch` (1000:1000)|
| OpenSearch Dashboards  | `opensearchproject/opensearch-dashboards:3` | (1000:1000) |
| SortingHat             | `grimoirelab/sortinghat`             | `sortinghat` (999:999) |
| Mordred                | `grimoirelab/grimoirelab:latest`     | `grimoire` (1000:1000) |
| MariaDB                | `mariadb:11.8`                       | `mysql` (999:999)      |
| Valkey                 | `valkey/valkey:8`                    | `valkey` (999:999)     |
| Neo4j                  | `neo4j:2025.05.1`                    | `neo4j` (7474:7474)    |
| Extractor              | `git-metadata-extractor:develop`     | `non-root-user` (1000:1000 in published image)|
| Qdrant                 | `qdrant/qdrant:latest`               | `qdrant` (1000:1000)   |
| GrimoireLab Postgres   | `postgres:16-alpine`                 | `postgres` (999:999)   |
| sparql-proxy (Caddy)   | `caddy:2.8-alpine`                   | `root` (0:0)           |
| Hub                    | `open-pulse:*`                       | (1000:1000)            |
| Portainer              | `portainer/portainer-ce`             | `root` (0:0)           |

### The helper script

`scripts/fix-data-perms.sh` chowns each known bind-mount source to the
right UID. It is idempotent and safe to run before or after `docker
compose up`.

```bash
# Preview what would change
sudo bash /open-pulse/open-pulse/scripts/fix-data-perms.sh --dry-run

# Apply chowns + bounce affected containers
sudo bash /open-pulse/open-pulse/scripts/fix-data-perms.sh --restart
```

The script discovers exact UIDs by inspecting the running containers
(`docker exec <name> id`) and falls back to image lookups
(`docker run --rm <image> id <user>`) when a container isn't running.
When even that fails, it uses the defaults documented in the table above.

:::note When to re-run
Run with `--restart` once on first deploy. Re-run whenever you add or
recreate a service (Docker may recreate bind-mount sources as root),
or whenever a service starts looping with `PermissionError` in its logs.
:::

### `--sparql-users`: the missing Caddy users file

The sparql-proxy uses Caddy with HTTP Basic Auth on write requests.
The auth credentials live in
`infra/services/sparql-proxy/users/sparql_users.caddy`. This file is
**not** in the repo (a different per-deploy secret), so on a fresh
clone Caddy starts up with:

```
Error: ... reading import file /etc/caddy/users: read /etc/caddy/users: is a directory
```

The wrapper generates the file from `SPARQL_AUTH` in `infra/env/.env`
and patches the Caddyfile if it still imports the directory directly
(it should import `/etc/caddy/users/*` — the helper migrates the
single-line form to the glob form automatically):

```bash
sudo bash /open-pulse/open-pulse/scripts/fix-data-perms.sh --sparql-users
```

The script reads `SPARQL_AUTH=user/pass`, hashes the password via a
disposable `caddy:2-alpine` container (`caddy hash-password`), writes
`user <bcrypt-hash>` to `sparql_users.caddy`, and restarts
`sparql-proxy-open-pulse` if it's running.

---

## 8. Quest YAML requirements

The quest pipeline runner reads service tokens from environment
variables named by the YAML config. When the crawler and extractor are
deployed with bearer-auth enabled (the default — both services refuse
HTTP 503 / 401 when their `API_TOKEN` is unset), the quest YAML must
specify the env var to source the token from. **These keys are
mandatory; without them every request returns 401.**

```yaml
# config/quest.example.yml (excerpt)
services:
  crawler:
    base_url: http://crawler:8000
    api_token_env: "CRAWLER_API_TOKEN"   # MANDATORY when bearer auth is on
  metadata_extractor:
    base_url: http://git-metadata-extractor:1234
    api_token_env: "EXTRACTOR_API_TOKEN" # MANDATORY when bearer auth is on
```

The env vars themselves come from `infra/env/.env` via the cli
container's `env_file:` directive. If you change a token, restart
`open-pulse-cli` so the new value is read into its environment.

---

## 9. Verification and smoke tests

### 9.1 Stack-wide health

The CLI ships a one-shot health command that probes Docker, container
status, every service's HTTP endpoint, and runs lightweight per-service
smoke tests:

```bash
./scripts/op health
# or, equivalently:
docker exec open-pulse-cli open-pulse health
```

Expected output: every line ends in `ok` / `healthy`. Any `unhealthy`
or `down` line points at a specific service to investigate.

### 9.2 Per-service smoke tests

The extractor has a dedicated test script that posts one `/v2/extract`
request from inside the extractor container and prints the full
response (plus a tail of recent error logs):

```bash
sudo bash /open-pulse/open-pulse/scripts/extractor-test.sh
# or, with a custom repo:
sudo bash /open-pulse/open-pulse/scripts/extractor-test.sh sdsc-ordes open-pulse
```

A successful run prints `status : 200` and the structured metadata JSON.
A 500 here almost always means the V2 provider cache is mis-pathed —
see section 5.3.

### 9.3 The hub

Open the hub in a browser:

```
http://<host>:7507
```

Log in with:

- **Username:** `admin` (the username is free-form; only the password
  is checked)
- **Password:** the value of `HUB_AUTH` from `infra/env/.env`

The home page should show green tiles for every running service plus a
marquee at the top with live SPARQL repo / Neo4j node / Neo4j relation
counts.

### 9.4 The SPARQL proxy

```bash
# Read (no auth required) — should return SPARQL XML / JSON.
curl -s 'http://<host>:7502/query?query=SELECT%20*%20WHERE%20%7B%3Fs%20%3Fp%20%3Fo%7D%20LIMIT%201'

# Write (auth required) — should return 200.
curl -s -u openpulse:<SPARQL_PASS> -X POST \
  -H 'Content-Type: application/sparql-update' \
  --data 'INSERT DATA { <http://example/test> <http://example/p> "v" }' \
  http://<host>:7502/update
```

### 9.5 Neo4j

```bash
# Bolt port (via cypher-shell, if installed)
cypher-shell -a bolt://<host>:7504 -u neo4j -p <NEO4J_PASS> 'RETURN 1'

# HTTP Browser
xdg-open http://<host>:7503
```

---

## 10. Common errors and fixes

| Symptom                                                                       | Cause                                                                              | Fix                                                                                                          |
| ----------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `unauthorized` / `denied` from `ghcr.io` on pull                              | Crawler / extractor images are private; daemon has no GHCR credentials             | `sudo docker login ghcr.io -u <gh-user>` with a PAT that has `read:packages` scope (section 4.1)             |
| `pull access denied for open-pulse-applier`                                   | Applier sidecar image is not published                                             | Build locally: `docker build -t open-pulse-applier:local infra/open-pulse-stack/grimoirelab/applier` (4.3)   |
| `env file ... infra/.env not found`                                           | Compose `env_file: ../.env` expects `infra/.env`, but real file is `infra/env/.env`| `ln -s env/.env /open-pulse/open-pulse/infra/.env` (section 3)                                               |
| Extractor returns HTTP 500 on `/v2/extract`; logs show `.cache/v2/providers.db` | `V2_PROVIDER_CACHE_PATH` is relative; lands in root-owned `/app/.cache`            | Set `V2_PROVIDER_CACHE_PATH=/app/data/.cache/v2/providers.db` in `infra/env/.env` (section 5.3)              |
| Extractor returns HTTP 401 on every quest request                             | Quest YAML missing `api_token_env`                                                 | Add `api_token_env: "EXTRACTOR_API_TOKEN"` under `services.metadata_extractor` (section 8)                   |
| Crawler returns HTTP 401 on every quest request                               | Quest YAML missing `api_token_env` for the crawler                                 | Add `api_token_env: "CRAWLER_API_TOKEN"` under `services.crawler` (section 8)                                |
| OpenSearch unhealthy; logs show `AccessDeniedException: data/nodes`           | `data/grimoirelab/opensearch-data` auto-created as root; opensearch user is UID 1000| `sudo chown -R 1000:1000 data/grimoirelab/opensearch-data` (or run `fix-data-perms.sh --restart`)            |
| SortingHat exits 1; logs show `PermissionError: '/opt/venv/.../sortinghat/static'`| Static dir auto-created as root; sortinghat user is UID 999                    | `sudo chown -R 999:999 data/grimoirelab/sortinghat` (or run `fix-data-perms.sh --restart`)                   |
| Mordred restart loop; logs show `PermissionError: '/home/grimoire/logs'`      | mordred bind dirs auto-created as root; grimoire user is UID 1000                  | `sudo chown -R 1000:1000 data/grimoirelab/mordred data/grimoirelab/projects-conf` (or `fix-data-perms.sh`)    |
| Qdrant healthy but every collection appears empty                             | Pre-loaded Qdrant snapshot ended up at the old path `data/extractor/qdrant/storage`| Move it: `mv data/extractor/qdrant/storage/* data/qdrant/storage/ && docker restart gme-qdrant`              |
| `sparql-proxy` restart loop; logs show `Could not import /etc/caddy/users: is a directory` | Caddy users file missing; Caddy is importing the dir, not a file                | `sudo bash scripts/fix-data-perms.sh --sparql-users` (section 7)                                             |
| `open-pulse-cli` won't start; `working_dir: required key OPEN_PULSE_HOST_PATH is not set` | Linux-uncorrected env file still has the empty / Windows default                 | Set `OPEN_PULSE_HOST_PATH=/open-pulse/open-pulse` in `infra/env/.env` (section 5.2)                          |
| `MYSQL_ROOT_PASSWORD` errors on first MariaDB start                           | OpenSearch / MariaDB strong-password requirements not met                          | Use `Replace-Me-1!`-style passwords (upper + lower + digit + special, ≥ 8 chars) in `infra/env/.env`         |
| Hub `/api/projects/*` always returns 502                                      | `HUB_APPLIER_URL` resolves to a service that doesn't exist (no `--with-grimoire`)   | Either bring the grimoire stack up (`--with-grimoire`) or unset `HUB_APPLIER_URL`                            |

---

## 11. Day-2 operations

### Tearing down

```bash
# Stop and remove containers, keep data on disk:
./scripts/op deploy down

# Stop, remove containers, AND remove named volumes (rare — bind mounts survive):
./scripts/op deploy down --volumes
```

Bind-mount data under `${OPEN_PULSE_DATA_DIR}` is **never** removed by
`down` — you have to `rm -rf` it explicitly if you want a clean slate.

### Rotating secrets

1. Edit `infra/env/.env`.
2. Restart affected containers:

   ```bash
   docker compose -f infra/open-pulse-stack/docker-compose.yml \
                  -f infra/open-pulse-stack/docker-compose.cli.yml \
                  --env-file infra/.env \
                  --profile crawler --profile extractor --profile sparql --profile hub \
                  up -d --force-recreate <service>
   ```

3. For `SPARQL_AUTH` specifically, re-run
   `fix-data-perms.sh --sparql-users` to regenerate the bcrypt-hashed
   users file Caddy reads from.

### Updating images

```bash
docker compose -f infra/open-pulse-stack/docker-compose.yml \
               -f infra/open-pulse-stack/docker-compose.cli.yml \
               --env-file infra/.env --profile <…> pull
docker compose -f infra/open-pulse-stack/docker-compose.yml \
               -f infra/open-pulse-stack/docker-compose.cli.yml \
               --env-file infra/.env --profile <…> up -d
```

For locally built images (`open-pulse:local`,
`open-pulse-applier:local`), `docker build` again with the same tag —
compose's `up -d` will recreate any container whose image SHA changed.

### Backups

The full state of the deploy is bind-mounted under
`${OPEN_PULSE_DATA_DIR}`. A working backup strategy is to stop the
relevant services, `tar` the directory, and restart:

```bash
sudo systemctl stop docker        # or `./scripts/op deploy down`
sudo tar -czf /backups/open-pulse-$(date +%F).tgz \
  -C / open-pulse/open-pulse/data
sudo systemctl start docker
```

For online backups, OpenSearch supports
[snapshots](https://opensearch.org/docs/latest/tuning-your-cluster/availability-and-recovery/snapshots/),
and Neo4j supports
[`neo4j-admin backup`](https://neo4j.com/docs/operations-manual/current/backup/). Both
are out of scope for this guide — use them on hosts where the cost of
a full stop is too high.

---

## 12. See also

- [Getting Started](../getting-started/index.md) — the same flow at a
  higher level
- [Architecture overview](../architecture/index.md) — what each service
  does and how they wire together
- [Services](../services/index.md) — per-service deployment assets and
  configuration knobs
- [Release checklist](./release-checklist.md) — what to verify before
  cutting a release
- [Branch model](./branch-model.md) — `docs` vs `main` responsibilities

Source files referenced by this guide:

- `infra/.env.example` — the canonical deploy env template
- `infra/open-pulse-stack/docker-compose.yml` — main stack
- `infra/open-pulse-stack/docker-compose.cli.yml` — orchestrator overlay
- `infra/open-pulse-stack/docker-compose.grimoirelab.yml` — GrimoireLab stack
- `scripts/op` — host-side wrapper that `docker exec`s into the cli container
- `scripts/fix-data-perms.sh` — bind-mount UID fixer
- `scripts/extractor-test.sh` — extractor smoke test
- `AGENTS.md` — project map and command reference
