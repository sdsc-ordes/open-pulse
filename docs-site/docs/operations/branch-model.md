---
title: Branch Model
slug: /operations/branch-model
---

# Branch Model

This repository uses a two-branch documentation model.

## Responsibilities

### `docs` branch

- Stores the documentation source of truth.
- Owns `docs-site/` (Docusaurus source content, config, and build inputs).
- Receives docs-only pull requests.

### `main` branch

- Stores product and infrastructure source used for runtime workloads.
- References documentation outputs and canonical docs links.
- May receive documentation artifacts or links that are generated from `docs`.

## Pull request guidance

- Docs-only changes should target `docs`.
- Code/runtime changes should target `main`.
- Cross-cutting changes can be split into two PRs when needed to keep branch responsibilities clear.

## Package manager policy

Use `pnpm` only for docs tooling and docs CI jobs.
