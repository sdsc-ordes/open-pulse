# tools/images

Container build artifacts for `open-pulse`. The compose files in
`infra/open-pulse-stack/` only reference image tags — they don't build.

## Images

| File | Image | Purpose |
| --- | --- | --- |
| `Dockerfile-open-pulse` | `ghcr.io/sdsc-ordes/open-pulse` | Single image. CLI, orchestrator container, and hub dashboard all run from here — the role is selected by `command:` / `entrypoint:` overrides in compose. |

## The unified `open-pulse` image

One image installs the `open-pulse-science[hub]` package + `docker-ce-cli` +
`docker-compose-plugin`. Default `ENTRYPOINT` is `open-pulse`, default `CMD`
is `--help`. Compose flips the role:

- **Hub** (`infra/open-pulse-stack/docker-compose.yml`, `--profile hub`):
  `command: ["gui", "hub", "serve", "--host", "0.0.0.0", "--port", "8000"]`
- **Orchestrator / CLI container** (`infra/open-pulse-stack/docker-compose.cli.yml`):
  `entrypoint: ["sleep", "infinity"]` (sits idle so the hub or a host user
  can `docker exec open-pulse-cli open-pulse …`).

### Build (local dev)

From the repo root:

```bash
docker build -f tools/images/Dockerfile-open-pulse -t open-pulse:local .
```

Pin it in `infra/.env`:

```env
OPEN_PULSE_IMAGE=open-pulse:local
```

Otherwise compose pulls `ghcr.io/sdsc-ordes/open-pulse:latest`.

### Hub usage

The hub Python lives in `src/open_pulse/gui/hub/` and ships with the
package. Templates and static assets are package data, so a host install
works too:

```bash
pip install open-pulse-science[hub]
HUB_AUTH=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=') \
  open-pulse gui hub serve --port 9090
```

For service control (Stack / Services pages) the docker daemon needs to be
reachable; the default `docker.from_env()` reads `DOCKER_HOST` or the
standard `/var/run/docker.sock`.

### Pages

| Path | Purpose |
| --- | --- |
| `/` | Live stat cards · service tiles · quick links to Neo4j / SPARQL / crawler / GME / Kibiter |
| `/stack` | Bring profiles up / down via `open-pulse deploy …` over the socket |
| `/services` | Inspect, start / stop / restart, tail container logs |
| `/pipeline` | Discover quest YAMLs and run `open-pulse quest run` |
| `/projects` | Query SPARQL → preview → POST projects.json to the applier |
| `/databases` | DuckDB · SPARQL · Cypher consoles, saved queries in SQLite |
| `/logs` | Per-container log tail with auto-refresh |

A right-to-left marquee polls `/api/stats/` every 10s.

### File-based persistence

Everything that needs to survive a hub restart lives under `data/hub/`:

- `data/hub/app.db` — SQLite: saved queries, query history
- `data/hub/scratch.duckdb` — DuckDB scratch DB

The shared `data/` is bind-mounted read-only at `/data` inside the hub so
DuckDB can read other services' files (e.g. `read_csv_auto('/data/...')`).

### Auth model

- Single shared password in `HUB_AUTH`.
- HTTP Basic on first request → on success, the hub issues an
  `op_hub_session` cookie (12h) so subsequent requests skip the credential
  check.
- `POST /logout` clears the session.
- `/healthz` is the only public route (used by the compose healthcheck).

### Configuring upstreams

The hub reaches the rest of the stack by service name on the compose network:

| Env var | Default | What it points at |
| --- | --- | --- |
| `HUB_APPLIER_URL` | `http://projects-applier:8000` | grimoire applier sidecar |
| `HUB_SPARQL_URL` | `http://sparql-proxy:7878` | SPARQL store |
| `HUB_NEO4J_URL` | `bolt://neo4j:7687` | Neo4j Bolt |
| `HUB_KIBITER_URL` | `http://localhost:7508` | GrimoireLab Kibiter (rendered on the home tile) |
