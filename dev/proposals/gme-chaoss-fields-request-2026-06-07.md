# GME → hub: persist the repo signals so CHAOSS can read them

**Date:** 2026-06-07
**From:** Open Pulse hub (CHAOSS metrics)
**To:** GME devs
**TL;DR:** The develop GME already *computes* the signals we need
(`_test_coverage`, `_releases`, `_has_ci`, `_docker_hub_url`, …) but they
live only as internal `_`-prefixed fields in the `/v2/extract` response.
The hub can't read those. Please **persist them as columns on the
`github_repos.repos` index table** (the table we already read for
`license_spdx`, `homepage`, …). Each one then unblocks one CHAOSS metric.

## Why the API flag isn't enough

The hub computes metrics live against the stores it already reads:
Neo4j, SPARQL/Oxigraph, OpenSearch (`git_*_enriched`), and the GME
`github_repos` DuckDB index. It does **not** call `/v2/extract`
per-repo — that path is async + LLM and far too slow for a dashboard
that fans out across every repo in a project.

So `include_internal_fields=true` doesn't help us: it only changes the
**extract response**, not what's **persisted**. The fields have to land
in a store we query. The lowest-friction option is the
`github_repos.repos` table — we already read it for `license_spdx`,
`homepage`, `readme_path`, `topics`, etc.

Good news: the signal parsers
(`src/v2/agents/rule_based/_repo_signals.py`) are pure functions over
README text + the repo root listing, and the index ingest path
(`src/index/github_repos/ingest/repos.py → build_record`) already has
`readme_text` in hand. So most of these can be computed at ingest with
no extra GitHub call.

## The ask — columns on `github_repos.repos`

| Internal field | Proposed column | Type | Unblocks (CHAOSS metric) | Ingest cost |
|---|---|---|---|---|
| `_test_coverage` | `test_coverage` | `TEXT` (`"87%"` / NULL) | **Test Coverage** | free — README already fetched |
| `_has_ci` | `has_ci` | `BOOLEAN` | **CI presence / Quality** | needs root listing (1 extra call or from tree) |
| `_docker_hub_url` | `docker_hub_url` | `TEXT` | **Packaging / distribution** | free — README + aux files |
| `_container_images` | `container_images` | `JSON` | (same, richer) | free |
| `_releases` | `releases` | `JSON` (`[{tag_name, published_at}]`) | **Release Frequency** | 1 extra call (`GET /repos/{o}/{n}/releases`) |
| `_latest_version` | `latest_version` | `TEXT` | (header chip) | derived from `releases` |

All nullable; NULL = "not extracted / not applicable" (the hub already
treats NULL as "no data", never as zero).

For **Release Frequency** specifically, a JSON list of
`{tag_name, published_at}` is ideal — the hub derives releases-per-year
over any window from the dates. A flat `release_count` +
`first_release_at` + `latest_release_at` triple would also work if the
full list is too heavy.

## Notes

- **Test Coverage**: the hub already re-parses the README cards
  (`<data>/index/github/cards/<owner>/<name>/README.md`) with a verbatim
  port of your `parse_test_coverage` regex, as a stopgap. A persisted
  `test_coverage` column lets us drop the re-parse and stay in lockstep
  with whatever the extractor decides (incl. a future LLM fallback).
- **Alternative path**: promoting these to canonical `pulse:`/`schema:`
  predicates in the JSON-LD graph (→ Oxigraph) also works — we'd read
  them via SPARQL like `schema:license`. The `repository_agent.py`
  comment already hints at this ("enrichment stage promotes them to
  canonical terms"). Index columns are just lower-friction for us; your
  call which surface.
- We don't need all six at once — even `releases` alone lands Release
  Frequency. Ship in whatever order is cheapest for the ingest path.

## What's already wired on our side

Shipped on `feat/chaoss-project-metrics` (PR #103), reading only stores
we already have — no GME change required:
Upstream Code Dependencies (Neo4j `DEPENDS_ON`), Documentation
Discoverability (index `homepage`/`readme_path`/`has_wiki`/`has_pages`),
License Coverage (`license_spdx`), Committers (`git_*_enriched`),
Issue Response Time (`github_*_enriched`), and Test Coverage (README
card re-parse). The six fields above are the remaining menu items that
need data only the extractor produces.
