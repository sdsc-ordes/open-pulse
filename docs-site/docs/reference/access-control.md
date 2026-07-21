---
title: Access Control
slug: /reference/access-control
---

# Access Control

The Hub is the front door to the data plane, and it owns authorization.
There are two roles, and — for direct store access — a matching set of
read-only credentials. Everything is HTTP Basic auth unless noted; for the
Hub the **username is ignored** and the password selects the role.

## Roles

| Role | Set via | What it can do |
| --- | --- | --- |
| **Admin** | `HUB_AUTH` | Full UI: browse, query, **and** every operator/configuration surface and write action. |
| **Reader** | `HUB_AUTH_READER` | A viewer: browse and run **read-only** queries. No operator pages, no writes. |

A reader is enforced two ways — the operator surfaces are hidden in the
nav **and** blocked at the route (`require_admin`), so a reader gets `403`
by direct URL, not just a missing link:

| Surface | Reader | Admin |
| --- | --- | --- |
| Knowledge pages (Hub, Sources, entity pages), CHAOSS metrics | ✅ | ✅ |
| Query consoles — SPARQL / Cypher / OpenSearch / DuckDB | read-only | read + write |
| Cypher write (`CREATE` / `MERGE` / `DELETE` / `SET`) | ✗ 403 (read transaction) | ✅ |
| Operator pages — Status, Services, Logs, Resources, Stack, Quests, Projects | ✗ 403 | ✅ |
| Settings | viewer panel (RDF graph + backup) | full |
| Landing page | Hub (catalog) | Overview (metrics) |

The Cypher console runs a reader's statement inside a Neo4j **read
transaction**, so write clauses are rejected server-side while admins keep
the full console.

## Reader credentials for direct store access

Because the stores can also be reached directly (not only through the Hub),
each has its own read-only credential where the engine supports one.

| Store | Endpoint | Reader credential |
| --- | --- | --- |
| **Hub** (CHAOSS metrics, knowledge/search API, `/chaoss`) | `HUB_PORT` (7507) | `HUB_AUTH_READER` — password only, any username |
| **SPARQL / Oxigraph** (Caddy proxy) | `SPARQL_PROXY_PORT` (7502) `/query`, or `/sparql/` on 443 | `SPARQL_READER_AUTH` = `reader/<password>` **or any Hub reader token** (the proxy delegates read-auth to the Hub); reads allowed, `/update` denied |
| **OpenSearch** | `:9200` | user `openpulse_reader`, role `openpulse_reader` — read + monitor + mappings, no writes ([provisioning](#opensearch-reader-role)) |
| **Neo4j** | Bolt `:7687` | — none. Community Edition has a single user and no role-based access. |

A Hub reader credential is either the shared env value `HUB_AUTH_READER`, or a
**managed reader token** — one of many labelled, individually revocable tokens
minted in **Admin → Users**, each with its own activity log. Either works
anywhere a reader is accepted (password only, any username). Because the
SPARQL proxy delegates read-auth to the Hub, a reader token also works
**directly** at the SPARQL `/query` endpoint — not only through the Hub API.
See [CHAOSS Metrics API](./chaoss-api.md). Client `.env` form:

```dotenv
CHAOSS_ENDPOINT=https://openpulse.epfl.ch
CHAOSS_AUTH=dev/<reader-token-or-HUB_AUTH_READER>   # dev/ is a cosmetic username
OPENPULSE_ENDPOINT=https://openpulse.epfl.ch
OPENPULSE_AUTH=dev/<reader-token-or-HUB_AUTH_READER>
```

### OpenSearch reader role

Unlike SPARQL, OpenSearch enforces its own role-based access, so the reader
needs a matching user + role inside OpenSearch (the Hub connects as
`OPENSEARCH_READER_USERNAME` / `OPENSEARCH_READER_PASSWORD` to serve the
OpenSearch console). The `openpulse_reader` role grants, on every index:

- `read` — `indices:data/read/*` (search, get, scroll, …);
- `indices:monitor/*` + `cluster_composite_ops_ro`, `cluster:monitor/*`;
- `indices:admin/mappings/get` — **required by the SQL plugin** (`_plugins/_sql`)
  and *not* included in the built-in `read` action group. Without it, SQL
  queries fail with `no permissions for [indices:admin/mappings/get]` while DSL
  search still works.

The role + user are provisioned idempotently by
`infra/open-pulse-stack/grimoirelab/scripts/create_opensearch_reader.sh`, run
automatically by `prepare-opensearch.sh` on stack bring-up (it reads the
`OPENSEARCH_*` values from `infra/.env`). Re-run it after changing the reader
password to re-sync the stored credential.

## Global read-only mode

Set `HUB_READONLY=true` to disable **every** mutating endpoint for all
sessions (a kill-switch, independent of the role split) — useful for a
public or demo deployment where nobody should change server-side state.
