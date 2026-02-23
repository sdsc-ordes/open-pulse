---
title: Migration from Static Docs Landing
slug: /operations/migration-from-static-docs
---

# Migration from Static Docs Landing

The current `docs/` folder remains the static GitHub Pages landing while content is migrated to Docusaurus in `docs-site/`.

## Mapping

| Current source (`docs/`) | New source (`docs-site/docs/`) | Notes |
| --- | --- | --- |
| `index.html` gateway content | `getting-started/`, `architecture/`, `services/`, `analysis/`, `operations/` | Split monolithic landing into topic pages. |
| Service and repository references | `services/` | Keep links and operational ownership per service. |
| High-level project framing | `getting-started/` + `architecture/` | Preserve entry-point clarity and system context. |
| Team/news/contact blocks | `operations/` (or a dedicated communications section later) | Keep static landing until final cutover. |

## Phased approach

1. Keep static `docs/` as the public landing page.
2. Build and review complete source docs in `docs-site/`.
3. Define cutover strategy in CI (publish Docusaurus output from `docs` branch).
4. Move landing links to the generated docs site once parity is acceptable.

## Exit criteria

- Required sections are present and maintained in `docs-site/`.
- Branch model workflow is active and docs PRs target `docs`.
- Broken-link checks pass in docs build CI.
