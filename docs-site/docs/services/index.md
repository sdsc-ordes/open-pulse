---
title: Services
slug: /services
---

# Services

The `open_pulse.services` package centralizes service integration logic used by CLI commands and pipeline steps.

## Modules

- `open_pulse.services.config`
  - canonical endpoint defaults
  - typed `ServicesConfig`
- `open_pulse.services.base`
  - service protocols/contracts
- `open_pulse.services.neo4j`
  - Neo4j service wrapper (`upload`, `check_bolt`, `close`)
- `open_pulse.services.tentris`
  - Tentris service wrapper (`upload`, `check_sparql`, `close`)
- `open_pulse.services.health`
  - shared endpoint probe helpers used by `open-pulse health`
- `open_pulse.services.container`
  - run-scoped `ServiceContainer`

## Configuration contract

Quest config must include:

```yaml
quest:
  services:
    neo4j:
      endpoint: "bolt://localhost:7687"
    tentris:
      endpoint: "http://localhost:7502/sparql"
```

Step-level endpoint fields are removed.

## Lifecycle contract

- Create one `ServiceContainer` per quest run (or single-step run).
- Inject into step context as `context["services"]`.
- Always call `close_all()` after execution.
