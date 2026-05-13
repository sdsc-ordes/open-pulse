---
title: Open Pulse Documentation
slug: /
---

# Open Pulse Documentation

This site is the source of truth for Open Pulse docs.

## What changed recently

- The Open Pulse stack now lives entirely under `infra/open-pulse-stack/`:
  main compose, CLI orchestrator overlay, full GrimoireLab compose, and
  GrimoireLab supporting assets (applier source, sigils, scripts).
- Single-file env model: `infra/.env` is the only file Docker Compose
  loads. Every service `env_file:`-pulls it, so any per-service knob set
  there reaches the container without an explicit `environment:` mapping.
  `<repo>/.env` is for the open-pulse Python CLI / hub when running on
  the host against EXTERNAL infrastructure; compose never reads it.
- Default auth simplified to `openpulse` / `replace-me` (rotate before
  any non-local deployment). OpenSearch needs a stronger placeholder
  (`Replace-Me-1!`) to satisfy its security plugin's regex.
- The git-metadata-extractor v2 path now requires a Bearer token; the
  open-pulse client sends it from `EXTRACTOR_API_TOKEN`. Auth-free deploys
  keep working when the env var is empty.
- The shared `open_pulse.services` layer owns Neo4j / SPARQL store /
  Crawler / metadata-extractor clients, config defaults, and health
  probes. Quest pipeline runs use a run-scoped `ServiceContainer`
  injected into step context. Quest config is service-centric:
  `quest.services.*.endpoint`.

## Start here

- [Getting Started](./getting-started/index.md)
- [Architecture](./architecture/index.md)
- [Services](./services/index.md)
- [Analysis](./analysis/index.md)
- [Operations](./operations/index.md)
