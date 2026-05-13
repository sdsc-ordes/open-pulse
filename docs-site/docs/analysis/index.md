---
title: Analysis
slug: /analysis
---

# Analysis

The analysis pipeline is exposed through `open-pulse quest`.

## Commands

- `open-pulse quest start`
- `open-pulse quest run-step <step>`
- `open-pulse quest list-steps`

## Step order

1. `crawler`
2. `neo4j_upload`
3. `metadata_extractor`
4. `sparql_upload`
5. `apply_grimoire_projects` (optional — pushes a projects.json built from
   the SPARQL store to the GrimoireLab projects-applier sidecar)

## Execution model

- Step enable/disable is controlled by `quest.steps.*.enabled`.
- Retry policy comes from `quest.retry`.
- Logging config comes from `quest.logging`.
- External service endpoints come from `quest.services`.
- Checkpoint/resume is handled by `.quest-checkpoints/<quest-name>.json`.

## Step integration contract

Pipeline steps that need external systems should access them via the
run-scoped service container injected as `context["services"]`:

- `context["services"].neo4j`
- `context["services"].sparql_store`
- `context["services"].crawler`
- `context["services"].metadata_extractor`

This keeps integrations centralized in `open_pulse.services` instead of
embedding connection logic inside step modules.
