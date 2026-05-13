# Oxigraph (SPARQL store)

Open-source SPARQL 1.1 store (Apache 2.0 + MIT). Replaces the Tentris reference instance
for local-dev and self-hosted deploys. Native JSON-LD parsing, RocksDB-backed persistent
storage, no license file needed.

This directory holds a **standalone** compose for running Oxigraph by itself. The full
Open Pulse stack wires Oxigraph in via `infra/open-pulse-stack/docker-compose.yml` under the
`sparql` profile, behind the Caddy auth proxy at `infra/services/sparql-proxy/`.

## Quick start (standalone)

```bash
docker compose up -d
curl http://localhost:7878/                                    # info page
curl 'http://localhost:7878/query?query=ASK%20%7B%7D'          # ASK {} → {"head":{},"boolean":false}
```

Default port `7878`. Override with `OXIGRAPH_PORT` in the environment.

## Quick start (in the main stack, with auth proxy)

From the `open-pulse/` repo root:

```bash
docker compose -f infra/open-pulse-stack/docker-compose.yml --profile sparql up -d
```

Then read/write through the proxy at `http://localhost:7878/...`. See
`../sparql-proxy/README.md` for the auth setup.

## Endpoints

| Path | Method | Purpose |
| --- | --- | --- |
| `/query` | GET, POST | SPARQL 1.1 query (SELECT, ASK, DESCRIBE, CONSTRUCT) |
| `/update` | POST | SPARQL 1.1 update (INSERT, DELETE, LOAD, CLEAR) |
| `/store` | GET, POST, PUT, DELETE | SPARQL Graph Store HTTP Protocol |
| `/` | GET | basic info / health page |

## Bulk loading

For first-time imports of more than a few hundred files, use the offline bulk loader
instead of HTTP — it's dramatically faster (10–50x):

```bash
docker compose stop oxigraph
docker run --rm \
  -v /path/to/jsonld-or-ttl:/import:ro \
  -v open-pulse_oxigraph_data:/data \
  ghcr.io/oxigraph/oxigraph:0.4.7 \
  load --location /data --file '/import/*.jsonld'
docker compose start oxigraph
```

(Volume name may differ depending on the compose project name; check
`docker volume ls | grep oxigraph` first.)

## Persistence

Data lives in the `oxigraph_data` named volume. To wipe and start fresh:

```bash
docker compose down -v
```

To back up:

```bash
docker run --rm -v open-pulse_oxigraph_data:/data -v $PWD:/backup alpine \
  tar czf /backup/oxigraph-data-$(date +%Y%m%d).tar.gz /data
```

## Why this and not Tentris

- Tentris requires a `tentris-license.toml` file; Oxigraph doesn't.
- Oxigraph parses JSON-LD natively — the previous `rdflib` JSON-LD → Turtle conversion
  step in the upload script can be dropped.
- Oxigraph has no built-in auth. The Caddy proxy in `../sparql-proxy/` handles that.

## Scale notes

Comfortable up to ~100M triples on a single node. For Open Pulse's described data
(roughly 1M graph nodes ≈ 10–50M triples) this is comfortably inside the sweet spot.
If the dataset grows past ~500M triples, consider switching to QLever (also Apache 2.0,
designed for Wikidata-scale).
