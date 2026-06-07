# GME → hub: the repo signals just need a re-extract (no schema change)

**Date:** 2026-06-07 (revised)
**From:** Open Pulse hub (CHAOSS metrics)
**To:** GME devs
**TL;DR:** Scratch the earlier "add index columns" ask — it's unnecessary.
The develop GME's internal `_`-prefixed signals **already auto-promote
to Oxigraph** as `gme-internal:<field>` triples (`jsonld_build.py`, with
`include_internal_fields=True` on our load). The hub reads those via
SPARQL exactly like `pulse:githubRepoForks`. So the only thing needed to
light up the remaining CHAOSS metrics is a **re-extract of the repos
with the develop GME** — the new signals land in the graph for free.

## What we found

The current Oxigraph already carries a rich `gme-internal:` vocabulary
(namespace `https://openpulse.science/git-metadata-extractor#`), attached
to the same repo subject as `pulse:githubRepositoryHandle`, as typed
literals:

```
?repo pulse:githubRepositoryHandle "sdsc-ordes/gimie" ;
      gme-internal:has_wiki        true ;
      gme-internal:has_pages       true ;
      gme-internal:homepage        "https://sdsc-ordes.github.io/gimie/" ;
      gme-internal:license_name    "Apache License 2.0" ;
      gme-internal:primary_language "Python" ;
      gme-internal:keywords        "fair-data" , "git" , … ;     # list → repeated triples
      gme-internal:size_kb         3562 .
```

But the signals the develop GME added (`_repo_signals.py`:
`parse_test_coverage`, `detect_has_ci`, `parse_docker_hub_url`, plus
`_releases` / `_latest_version`) are **0 triples** today — because the
graph was extracted with the *previous* GME. Re-extraction is all that's
missing.

## What the hub will read (once re-extracted)

| Internal field → graph predicate | Type | CHAOSS metric |
|---|---|---|
| `gme-internal:test_coverage` | `"87%"` literal | **Test Coverage** (already wired to prefer this, README-card fallback) |
| `gme-internal:has_ci` | boolean | **CI presence** (Quality) — wire on request |
| `gme-internal:docker_hub_url` | literal | **Container distribution** — wire on request |
| `gme-internal:releases` | list (shape TBD) | **Release Frequency** — see note |
| `gme-internal:latest_version` | literal | release header chip |

### RESOLVED — the develop image (2026-06-07 13:13) shipped the flat scalars

The devs took option 2: the raw `_releases` list-of-objects collapses to
empty blank nodes on JSON-LD expansion, so `summarize_releases()` now
emits flat, queryable scalars (`_repo_signals.py`):

- `gme-internal:release_count` (int)
- `gme-internal:first_release_date` (ISO 8601)
- `gme-internal:latest_release_date` (ISO 8601)

Plus a wave of related flat scalars: `package_*` (GHCR containers),
`npm_*` / `pypi_*` / `conda_*` / `crates_*` / `rubygems_*` (registry
packages: package / latest_version / versions / latest_release_date /
registry_url / link), and `badge_count`.

**Release Frequency is now wired** (releases/year over
first→latest span) and will light up as soon as repos are re-extracted
with this image. The hub container running today is still the previous
digest and the graph carries 0 of these triples — so the remaining step
is purely operational: bump the GME image + re-extract (your call /
domain, since that writes Oxigraph).

## Already wired on our side (PR #103), no GME change needed

Upstream Code Dependencies (Neo4j `DEPENDS_ON`), Documentation
Discoverability (`has_wiki`/`has_pages`/`homepage`/`readme_path`),
License Coverage (`license_spdx`), Committers (`git_*_enriched`),
Issue Response Time (`github_*_enriched`), Test Coverage (README-card
parse today → `gme-internal:test_coverage` once re-extracted).
