---
title: Getting Started
slug: /getting-started
---

# Getting Started

## Prerequisites

- Python 3.11+
- `uv`
- Docker + Docker Compose

## Install and verify

```bash
uv sync --group dev --group test
uv run open-pulse --help
uv run pytest -q
```

## Run core CLI commands

```bash
uv run open-pulse deploy up
uv run open-pulse health
uv run open-pulse quest list-steps
```

## Quest config quick reference

Use `quest.services` for service endpoints.

```yaml
quest:
  services:
    neo4j:
      endpoint: "bolt://localhost:7687"
    tentris:
      endpoint: "http://localhost:7502/sparql"
```

Step-level endpoint fields are not supported.
