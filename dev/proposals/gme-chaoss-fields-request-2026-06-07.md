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

### One thing to confirm: the `releases` shape

List-of-string fields (`keywords`) come through as **repeated triples**,
one per value. `_releases` is a list of **objects**
(`{tag_name, published_at}`). Two questions for you:

1. How does `jsonld_build` emit a list-of-dicts — repeated triples to
   blank nodes, or a single JSON-literal blob (like the `publiccode`
   nested sub-trees)? We need the dates to compute releases-per-year.
2. If a blob is easier on your side, a flat
   `gme-internal:release_count` + `gme-internal:first_release_at` +
   `gme-internal:latest_release_at` triple would let us compute
   frequency without parsing JSON in SPARQL. Either works — your call.

We'll wire **Release Frequency** the moment we can see real `releases`
triples for a re-extracted repo.

## Already wired on our side (PR #103), no GME change needed

Upstream Code Dependencies (Neo4j `DEPENDS_ON`), Documentation
Discoverability (`has_wiki`/`has_pages`/`homepage`/`readme_path`),
License Coverage (`license_spdx`), Committers (`git_*_enriched`),
Issue Response Time (`github_*_enriched`), Test Coverage (README-card
parse today → `gme-internal:test_coverage` once re-extracted).
