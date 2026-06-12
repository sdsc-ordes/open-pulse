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
| **A researcher / analyst** | [Use Cases](./use-cases/index.md), the Concepts pages, and the [CHAOSS Metrics API](./reference/chaoss-api.md) |
| **Exploring a deployment** | [The Hub](./hub/index.md) — query consoles, metrics, knowledge graph |
| **Running a node**         | [Operations → Deployment](./operations/deployment.md) + [Register a node](./operations/register-a-node.md) |
| **Running analyses**       | [Quest Pipeline](./pipeline/index.md)                         |
| **Contributing code**      | [Contributing](./contributing/index.md) + [Architecture](./architecture/index.md) |
| **Curious about the project** | [Community](./community/index.md)                          |
