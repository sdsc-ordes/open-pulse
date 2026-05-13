---
title: Getting Started
slug: /getting-started
---

# Getting Started

## Prerequisites

- Docker + Docker Compose (the only hard requirement for running the stack)
- Python 3.11+ and [`uv`](https://github.com/astral-sh/uv) — only needed for
  hacking on the package itself

## Bring up the stack

The fastest path is to let `op deploy` seed the env and bring up the hub:

```bash
docker build -f tools/images/Dockerfile-open-pulse -t open-pulse:local .
./scripts/op deploy up --profile crawler --profile extractor --profile sparql --profile hub
```

Or directly via Docker Compose (single env file, single project name):

```bash
docker compose -f infra/open-pulse-stack/docker-compose.yml \
               -f infra/open-pulse-stack/docker-compose.cli.yml \
               --env-file infra/.env --project-name open-pulse \
               --profile crawler --profile extractor --profile sparql --profile hub \
               up -d
```

Then open <http://localhost:9090>, log in with `openpulse` /
`$HUB_AUTH` (the username is free-form; only the password is checked).

## Configuration

- `<repo>/infra/.env` — the **deployment env**. All container-side knobs:
  image refs, ports, resource limits, storage paths, every credential the
  stack needs. Compose loads only this file. Auto-seeded from
  `infra/.env.example` on first `op deploy up`.
- `<repo>/.env` — the **tool/client env**. Consumed by the open-pulse
  Python CLI / hub when running on the host against EXTERNAL infra.
  Compose never reads it.

Default credentials: `openpulse` / `replace-me`. OpenSearch (in the
`--with-grimoire` stack) needs a stronger password — the placeholder is
`Replace-Me-1!`. Rotate before any non-local deployment.

### `.env` wizard

Don't want to hand-edit the file? The static wizard at
[`/env-wizard/`](/env-wizard/) walks through the questions, generates
strong tokens locally with the Web Crypto API (nothing leaves the
browser), and outputs a complete `infra/.env` you can paste or download.

## Hack on the package

```bash
uv sync --group dev --group test
uv run open-pulse --help
uv run pytest -q
```

## Quest config quick reference

A quest declares a `services:` block listing every endpoint the pipeline
talks to, plus a `steps:` block enabling pipeline stages.

```yaml
quest:
  name: "my-quest-run"
  services:
    crawler:
      endpoint: "http://crawler:8000"
      api_token_env: CRAWLER_API_TOKEN
    neo4j:
      endpoint: "bolt://neo4j:7687"
      auth_env: NEO4J_AUTH
    metadata_extractor:
      endpoint: "http://git-metadata-extractor:1234"
      api_token_env: EXTRACTOR_API_TOKEN
    sparql_store:
      endpoint: "http://sparql-proxy:7878"
      auth_env: SPARQL_AUTH
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
inside the stack, `localhost` from the host) — quests written for in-stack
execution can omit the entire `services:` block.
