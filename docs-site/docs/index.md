---
title: Open Pulse Documentation
slug: /
---

# Open Pulse Documentation

Open Pulse is an open-science monitoring platform. It automates the
discovery and monitoring of open-source software produced inside a
research institution (or programme) and surfaces signals of
**community vitality and engagement** — the work that paper citations
and GitHub stars rarely capture.

## Why Open Pulse

Traditional metrics tell only part of the story. The DeSeq2 case
study is a useful reminder: with **86,569 paper citations** it looks
like an established project, but the underlying community shows up far
more vividly in its engagement signals — over a million Bioconductor
downloads, only 17 GitHub stars, 24 unique contributors, 409 closed
pull requests, thousands of Q&A threads. Many valuable projects stay
*invisible* under star-and-citation counting because they are niche,
early-stage, or low-visibility. Open Pulse aims to map and surface
those hidden contributions across the full continuum from passive
consumer to active maintainer.

Three things make this possible in practice:

- a **graph** of who builds what, with which organisation
  ([Graph & Semantic Data](./concepts/graph-and-semantic-data.md));
- a **semantic metadata store** about every tracked repository
  ([Metadata & Ontology](./concepts/metadata-and-ontology.md));
- a **CHAOSS-grade time-series** of development activity
  ([Metrics & CHAOSS](./concepts/metrics-and-chaoss.md)).

All three are produced by the same pipeline and exposed through open
endpoints so analysts, researchers and policy teams can build on top.

## Find your way around

| If you are…                | Start here                                                    |
| -------------------------- | ------------------------------------------------------------- |
| **Trying it for 5 minutes**| [Getting Started](./getting-started/index.md)                 |
| **A researcher / analyst** | [Use Cases](./use-cases/index.md) + the Concepts pages        |
| **Running a node**         | [Operations](./operations/index.md) — deploy + register-a-node |
| **Contributing code**      | [Contributing](./contributing/index.md) + [Architecture](./architecture/index.md) |
| **Looking up internals**   | [Services](./services/index.md) and [Analysis](./analysis/index.md) |
| **Curious about the project** | [Community](./community/index.md)                          |

For the deep architectural reference, the source of truth is
[`AGENTS.md`](https://github.com/sdsc-ordes/open-pulse/blob/main/AGENTS.md)
in the repository — it tracks ahead of these docs by design.

## Recent changes worth knowing

- The SPARQL store client was renamed from `open_pulse.services.tentris`
  to `open_pulse.services.sparql_store`. It is now technology-agnostic:
  any SPARQL 1.1 + Graph Store HTTP Protocol store works (Oxigraph,
  Tentris, Virtuoso, …). Quest YAML uses
  `quest.services.sparql_store.endpoint`; step-level `endpoint` fields
  are no longer supported.
- The Open Pulse stack lives entirely under `infra/open-pulse-stack/`:
  `docker-compose.yml` (main), `docker-compose.cli.yml` (CLI orchestrator
  overlay), `docker-compose.grimoirelab.yml` (full GrimoireLab — opt-in
  via `--with-grimoire`), plus GrimoireLab supporting assets.
- One Docker image (`tools/images/Dockerfile-open-pulse` →
  `ghcr.io/sdsc-ordes/open-pulse:latest`) plays three roles via compose
  overrides: host install, `open-pulse-cli` (idle, target of
  `docker exec` from `scripts/op`), and `open-pulse-hub` (FastAPI
  dashboard).
- Single-file env model: `infra/.env` is the only file Docker Compose
  loads. Every service `env_file:`-pulls it. `<repo>/.env` is for the
  open-pulse Python CLI / hub when running on the host against EXTERNAL
  infrastructure; compose never reads it.
- The pipeline gained an optional `apply_grimoire_projects` step that
  builds an owner-grouped `projects.json` from Neo4j and POSTs it to the
  GrimoireLab applier sidecar. Off by default.
- The hub default port moved from 9090 to 7507 on EPFL hosts to land
  inside the firewall-open range. `HUB_PORT` in `infra/.env` controls it.
- Default auth simplified to `openpulse` / `replace-me` (rotate before
  any non-local deployment). OpenSearch needs a stronger placeholder
  (`Replace-Me-1!`) to satisfy its security plugin's regex.
