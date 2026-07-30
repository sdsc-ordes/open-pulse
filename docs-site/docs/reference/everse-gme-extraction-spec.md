---
title: EVERSE Indicators — GME Extraction Spec
slug: /reference/everse-gme-extraction-spec
---

# EVERSE indicators — GME extraction spec

A proposal for where the [EVERSE](https://everse.software) Research Software
Quality Indicators (RSQI) should be **produced**. Companion to the
[coverage mapping](./everse-indicators.md), which established that ~30 of the 47
indicators are reachable. This document says **who computes them** and hands the
git-metadata-extractor (GME) team a concrete extraction list.

## Principle: extract once, at crawl time, as triples

EVERSE indicators are overwhelmingly **per-repository facts** — the exact thing
GME already crawls over the GitHub API and emits as `gme:*` triples. They should
be:

- **computed once during extraction**, not fetched live per request (the hub's
  current `/api/v1/indicators/everse/...` endpoint fetches OpenSSF Scorecard on
  every call — fine as a bridge, wrong as the durable design);
- **persisted in the graph as first-class `SoftwareQualityIndicator` nodes** in
  the EVERSE RSQI vocabulary, so results are cached, queryable, and
  interoperable with the wider EVERSE ecosystem;
- **produced by whoever owns the source signal** — which splits three ways
  (below), but with GME owning the majority.

### Division of responsibility

| Producer | Owns | Why |
| --- | --- | --- |
| **GME** (per-repo extraction) | The bulk — license, metadata, releases, CI, containerization, citation, tests, packaging, **and the OpenSSF Scorecard security cluster** | Per-repo facts over the GitHub API — GME's core job; already emits ~half. |
| **Knowledge graph** (hub) | Cross-entity indicators — academic impact, scholarly/Zenodo archival, resolved dependents | Needs the graph; GME sees each repo in isolation. |
| **GrimoireLab / CHAOSS** (hub) | Activity indicators — rich contributor/response/closure metrics | Time-series from the OpenSearch activity indices. |

The **hub** stops computing extraction: it queries these persisted indicators,
joins the three producers, and serves the unified RSQI response.

## Emitted shape

Each indicator GME determines for a repo should attach as an RSQI node, e.g.:

```json
{
  "@context": "https://w3id.org/everse/rsqi#",
  "@type": "SoftwareQualityIndicatorResult",
  "indicator": { "@id": "https://w3id.org/everse/i/indicators/software_has_license" },
  "abbreviation": "software_has_license",
  "qualityDimension": "fairness",
  "value": true,
  "evidence": "schema:license = MIT",
  "source": "gme",
  "checkedAt": "2026-07-30T00:00:00Z"
}
```

`value` is a boolean for pass/fail indicators or a number (e.g. a 0–10 Scorecard
score, a completeness %). `source` distinguishes `gme` / `scorecard` / `graph` /
`grimoirelab` so provenance is explicit.

## GME work list

### A. Emit an indicator from a signal GME already extracts (low)

No new extraction — GME already has the fact; just emit the RSQI node.

| Indicator | Dimension | Existing GME signal |
| --- | --- | --- |
| `software_has_license` | fairness | `gme:license_name` / `schema:license` |
| `has_published_package` | flexibility | `gme:pypi_versions` / `npm_versions` / `maven_versions` |
| `listed_in_registry` | fairness | same registry-version signals |
| `software_is_containerized` | fairness | `gme:compose_files` / `compose_images` |
| `software_has_documentation` | fairness | `gme:documentation_urls`, `homepage`, `has_wiki`, `has_pages` |
| `descriptive_metadata` | fairness | `gme:description` + `keywords` + `homepage` + `schema:programmingLanguage` + license |
| `has_releases` | maintainability | `gme:release_count` / `releases` / `git_tags` |
| `repository_workflows` | maintainability | `gme:has_ci` |
| `support_issue_tracking` | maintainability | `gme:has_issues` |
| `has_contribution_guidelines` | sustainability | `gme:contributing_url` (+ `has_code_of_conduct`, templates) |
| `version_control_use` | fairness | definitional (git host) |
| `requirements_specified` | maintainability | dependency manifests GME already parses |
| `software_test_coverage` | reliability | `gme:test_coverage` (self-reported) |
| `metadata_is_up_to_date` | maintainability | `gme:updated_at` / `pushed_at` vs. recency threshold |

### B. New lightweight extraction (GitHub API / file presence — GME's wheelhouse)

| Indicator | Dimension | New signal to extract |
| --- | --- | --- |
| `software_has_citation` | fairness | Presence of `CITATION.cff` (and parse DOI if present) |
| `codemeta_completeness` | fairness | Presence + field completeness of `codemeta.json` |
| `versioning_standards_use` | fairness | Semver-validate `gme:git_tags` / `latest_version` |
| `software_has_tests` | reliability | Detect a test directory / test files (not just a coverage number) |
| `uses_tool_for_warnings_and_mistakes` | maintainability | Linter config presence (`.eslintrc`, `ruff.toml`, `.flake8`, pre-commit) |
| `has_active_communication_channels` | community | `has_discussions` + community files |
| `archived_in_software_heritage` | fairness | Per-repo Software Heritage API lookup (`archive.softwareheritage.org`) |

### C. Via an OpenSSF Scorecard sub-step (recommended)

Rather than hand-roll each security check, GME should run **OpenSSF Scorecard**
per repo as part of extraction. Scorecard runs over the **GitHub API with no
source checkout or build**, so it fits GME's model, and one run yields the whole
security/practice cluster. Emit each mapped check as an indicator:

| Indicator | Dimension | Scorecard check |
| --- | --- | --- |
| `static_analysis_common_vulnerabilities` | security | SAST |
| `no_critical_vulnerability` | security | Vulnerabilities (OSV-backed) |
| `has_no_binary_artifacts` | security | Binary-Artifacts |
| `uses_fuzzing` | safety | Fuzzing |
| `human_code_review_requirement` | functional_suitability | Code-Review |
| `has_ci-tests` | maintainability | CI-Tests |
| `dependency_management` | sustainability | Dependency-Update-Tool |

Scorecard's `License` / `Maintained` / `Packaging` / `Contributors` checks
reinforce the tier-A signals and can serve as fallbacks. (A working prototype of
this mapping already runs hub-side at `GET /api/v1/indicators/everse/...`; the
proposal is to move the computation into GME and persist it.)

### D. Heavy / source analysis — out of GME's current model

These need the source **cloned and analysed** (compile / run tools), which GME
doesn't do today. Either GME grows a source-analysis stage, or a separate
analyser service owns them, or they're marked out of scope.

`has_no_linting_issues`, `code_documentation_coverage_ok`,
`software_has_license_for_file_types`, `passed_tests_ok`,
`functional_correctness`, and the static-maintainability family
(`cyclomatic_complexity_ok`, `code_duplication_ok`, `code_smells_ok`,
`internal_cohesion_ok`, `coupling_between_objects_ok`, `maintainability_index_ok`,
`lines_of_code_ok`).

## Not GME's job (hub / graph / GrimoireLab)

| Indicator | Owner | Signal |
| --- | --- | --- |
| `academic_impact`* | graph | ScholarlyArticle → shared-author → repo |
| `archived_in_scholarly_repository` | graph | Zenodo deposit ↔ repo linkage |
| `persistent_and_unique_identifier` (resolved) | graph | resolved DOI/PID linkage (declared DOI in `CITATION.cff` is GME-side) |
| `has_active_contributors` (rich) | GrimoireLab | contributor activity (Scorecard's Contributors is a coarse fallback) |
| `response_timeframe_ok` | GrimoireLab | first-response / issue-response percentiles |

\* Not an EVERSE indicator per se but the same cross-entity pattern; listed for completeness.

## Phasing

1. **GME tier A** — emit RSQI nodes from signals GME already extracts (~14). Pure emission, no new crawling.
2. **GME Scorecard sub-step (tier C)** — one integration, yields the security/practice cluster (~7) plus reinforcement.
3. **GME tier B** — the lightweight new extractions (~7).
4. **Hub** — switch `/api/v1/indicators/everse/...` to read persisted indicators from the graph (live Scorecard only as fallback), and join the graph + GrimoireLab indicators into the unified RSQI response.
5. **Tier D** — a separate product decision (source-analysis stage or out of scope).

After 1–4, a repo returns the full reachable EVERSE set (~30) from persisted
data, provenance-tagged by producer, in the RSQI vocabulary.
