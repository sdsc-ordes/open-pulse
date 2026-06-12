---
title: Quest Pipeline
slug: /pipeline
---

# Quest Pipeline

A **quest** is a YAML-configured run of the analysis pipeline: crawl a
software ecosystem, land the graph in Neo4j, extract semantic metadata,
upload it to the SPARQL store, and optionally sync the result into
GrimoireLab. Quests are executed by `open-pulse quest` — from the CLI,
or from the [Hub's](../hub/index.md) Quests page (which runs the same
command inside the `open-pulse-cli` container).

## Commands

```bash
open-pulse quest start <quest>        # run the full pipeline (--resume to continue)
open-pulse quest run-step <step>      # run a single step
open-pulse quest list-steps           # list registered steps
```

Bare quest names resolve against `$OPEN_PULSE_QUEST_DIR`
(default `data/quests/`); paths are used as-is. The canonical annotated
config lives at
[`config/quest.example.yml`](https://github.com/sdsc-ordes/open-pulse/blob/main/config/quest.example.yml);
[`config/quest.protein-ai-ecosystem.yml`](https://github.com/sdsc-ordes/open-pulse/blob/main/config/quest.protein-ai-ecosystem.yml)
is a real production example.

## Steps

Steps run sequentially in this order. Each is enabled/disabled via
`quest.steps.<name>.enabled`; the optional ones are off by default.

| # | Step | What it does |
| - | ---- | ------------ |
| 1 | `crawler` | Submits a crawl to the Open Pulse Crawler API (GraphQL endpoint by default), polls the job, writes `crawler-graph.json`. Key options: `seeds`, `max_rounds` (1–10; ≥ 2 materialises linked entities such as dependents and contributors as nodes), `crawl_dependencies` / `crawl_dependents`, `crawl_issues` / `crawl_prs`, `min_stars`, `timeout_seconds` (`null` = no timeout). |
| 2 | `frontier_extend` *(optional)* | Computes the unexplored frontier of an existing crawler graph, re-seeds the crawler with it, and merges the result back idempotently. |
| 3 | `neo4j_upload` | Batched, idempotent Cypher `MERGE` of users / orgs / repos and their relationships (`OWNS`, `CONTRIBUTES_TO`, `DEPENDS_ON`, `STARRED`, `FORK_OF`, …). |
| 4 | `metadata_extractor` | Sends each repo to the git-metadata-extractor (v2 async API; `v2_agent_runtime`: `rule_based` or `hybrid`), writes per-repo JSON-LD. Supports `extract_users` / `extract_orgs`, `include_internal_fields` (keeps `gme:`-namespaced signals), `max_workers`, and progressive `stream_to_sparql` uploads. |
| 5 | `sparql_upload` | Loads the JSON-LD files into the SPARQL store via the Graph Store HTTP Protocol. With `auto_named_graph`, data lands in a monthly named graph `{base}/{YYYY-MM}/{runtime}`; `publish_to_default` mirrors it to the default graph (auto-on for `hybrid` runs). |
| 6 | `apply_grimoire_projects` *(optional)* | Builds an owner-grouped `projects.json` from the Neo4j repos and POSTs it to the GrimoireLab projects-applier sidecar. |
| 7 | `archive_outputs` *(optional)* | Zips the extractor output (CRC-verified, atomic write) into `data/hub/archives/`. |

## Execution model

- **Validation.** Quest YAML is parsed into Pydantic models
  (`pipeline/config.py`); unknown keys are rejected.
- **Retry.** Every step is wrapped with `quest.retry`
  (`max_attempts`, default 3; `backoff_seconds`, default 5).
- **Checkpoint / resume.** Progress persists to
  `.quest-checkpoints/<quest-name>.json`; `quest start --resume` skips
  completed steps.
- **Services.** A run-scoped `ServiceContainer` is built from
  `quest.services` at start, injected into each step as
  `context["services"]`, and closed on exit (success or failure).

## The services block

A quest declares the endpoints it talks to and the **names of the env
vars** holding their credentials (never the credentials themselves).
Endpoints live only under `quest.services.*` — step-level `endpoint`
fields are not supported.

```yaml
quest:
  name: "my-quest-run"
  services:
    crawler:
      endpoint: "http://crawler:8000"
      api_token_env: CRAWLER_API_TOKEN     # mandatory when bearer auth is on
    neo4j:
      endpoint: "bolt://neo4j:7687"
      auth_env: NEO4J_AUTH                 # 'username/password' format
    metadata_extractor:
      endpoint: "http://git-metadata-extractor:1234"
      api_token_env: EXTRACTOR_API_TOKEN   # mandatory when bearer auth is on
    sparql_store:
      endpoint: "http://sparql-proxy:7878"
      auth_env: SPARQL_AUTH                # Basic Auth: 'username/password'
  steps:
    crawler:
      seeds: ["sdsc-ordes"]
      max_rounds: 2
    neo4j_upload: { enabled: true }
    metadata_extractor:
      enabled: true
      max_repos: 8
    sparql_upload: { enabled: true }
```

Endpoint defaults adapt to where the CLI runs (compose-network names
inside the stack, `localhost` from the host), so quests written for
in-stack execution can omit the whole `services:` block.

## Service clients

The `open_pulse.services` package centralizes all integration logic;
pipeline steps never construct their own connections.

- `services.config` — typed `ServicesConfig` + endpoint defaults
  (host vs. in-stack auto-detection).
- `services.neo4j` — Neo4j wrapper (`upload`, `check_bolt`, `close`).
- `services.sparql_store` — technology-agnostic SPARQL client:
  JSON-LD → N-Triples → Graph Store HTTP Protocol, optional Basic Auth.
  Works with Oxigraph, Tentris, or any SPARQL 1.1 store.
- `services.crawler` — crawler client (`submit_crawl`, `get_status`,
  `wait_for_completion`, `get_graph`); tolerates transient poll
  failures.
- `services.metadata_extractor` — git-metadata-extractor client
  (async v2 path; a synchronous v1 path is kept for older extractor
  deployments).
- `services.health` — endpoint probes shared with `open-pulse health`.
- `services.container` — the run-scoped `ServiceContainer`
  (`from_quest_config()`, context-manager support, `close_all()`).

See [Architecture](../architecture/index.md) for how the pipeline fits
into the wider system, and
[Metadata & Ontology](../concepts/metadata-and-ontology.md) for what
the extractor + SPARQL upload actually produce.
