---
title: Services
slug: /services
---

# Services

The `open_pulse.services` package centralizes service-integration logic
used by CLI commands and pipeline steps.

## Modules

- `open_pulse.services.config`
  - canonical endpoint defaults (auto-adapt to host vs. in-stack)
  - typed `ServicesConfig` and per-service config models
- `open_pulse.services.base`
  - service protocols / contracts
- `open_pulse.services.neo4j`
  - Neo4j wrapper (`upload`, `check_bolt`, `close`)
- `open_pulse.services.sparql_store`
  - Technology-agnostic SPARQL store wrapper (works with Oxigraph, Tentris,
    …): JSON-LD → N-Triples → Graph Store HTTP Protocol upload, with
    optional HTTP Basic Auth for writes (read from `auth_env`)
- `open_pulse.services.crawler`
  - Open Pulse Crawler client (`submit_crawl`, `get_status`, `wait_for_completion`,
    `get_graph`); bearer from `api_token_env`
- `open_pulse.services.metadata_extractor`
  - git-metadata-extractor client (gimie v1 + rule-based v2 paths); bearer
    from `api_token_env` when set, omitted otherwise so auth-free deploys
    keep working
- `open_pulse.services.health`
  - shared endpoint-probe helpers used by `open-pulse health`
- `open_pulse.services.container`
  - run-scoped `ServiceContainer` constructed from a quest's services block

## Configuration contract

A quest declares the services it talks to and the env-var names that hold
their credentials:

```yaml
quest:
  services:
    neo4j:
      endpoint: "bolt://neo4j:7687"
      auth_env: NEO4J_AUTH
    sparql_store:
      endpoint: "http://sparql-proxy:7878"
      auth_env: SPARQL_AUTH
    crawler:
      endpoint: "http://crawler:8000"
      api_token_env: CRAWLER_API_TOKEN
    metadata_extractor:
      endpoint: "http://git-metadata-extractor:1234"
      api_token_env: EXTRACTOR_API_TOKEN
```

Step-level endpoint fields are not supported — the `services:` block is
the single source.

## Lifecycle contract

- Create one `ServiceContainer` per quest run (or single-step run) via
  `ServiceContainer.from_quest_config(quest)` or
  `ServiceContainer.from_services_config(services)`.
- Inject into step context as `context["services"]`.
- Always call `close_all()` after execution (or use the container as a
  context manager — its `__exit__` calls `close_all()`).
