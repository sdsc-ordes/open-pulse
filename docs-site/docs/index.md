---
title: Open Pulse Documentation
slug: /
---

# Open Pulse Documentation

This site is the source of truth for Open Pulse docs.

## What changed recently

- A shared `open_pulse.services` layer now owns Neo4j/Tentris service clients, config defaults, and health probe utilities.
- Quest pipeline runs now use a run-scoped service container injected into step context.
- Quest config is now service-centric: use `quest.services.*.endpoint`.

## Start here

- [Getting Started](./getting-started/index.md)
- [Architecture](./architecture/index.md)
- [Services](./services/index.md)
- [Analysis](./analysis/index.md)
- [Operations](./operations/index.md)
