---
title: EVERSE Indicators — Coverage Mapping
slug: /reference/everse-indicators
---

# EVERSE Indicators — OpenPulse coverage mapping

Maps the **47 [EVERSE](https://everse.software/indicators/website/indicators.html)
Research Software Quality Indicators** (RSQI, 17 quality dimensions) against what
OpenPulse can compute today. Grounded in the CHAOSS metric registry
(`chaoss/metrics.py`) and a live probe of the RDF graph's per-software predicates
(the GME `git-metadata-extractor#*` triples).

## Headline

| Tier | Count | Meaning |
| --- | --- | --- |
| ✅ **Answerable now** | ~18 | Already a CHAOSS metric, or a raw GME triple already in the graph — just needs surfacing as an indicator. |
| 🟡 **Modest effort** | ~12 | Data is adjacent; needs a small computation, a heuristic, or one new extraction field. |
| 🔴 **New capability** | ~17 | Requires something OpenPulse fundamentally isn't: per-repo **source-code analysis** or **security scanning**. |

**The dividing line is metadata/activity vs. source code.** OpenPulse crawls
repository *metadata* and git/GitHub *activity*, so it natively answers the
**FAIRness, community, sustainability, and repo-hygiene** families. The
**maintainability code-metrics, security, and safety** dimensions need the
source cloned and run through analysers (SonarQube / CodeQL / coverage runners /
fuzzers / secret scanners) — a categorically different pipeline we don't have.

So: **~30 of 47 are reachable** (18 now + 12 with modest work); the remaining
~17 cluster almost entirely in three dimensions (static maintainability metrics,
security, safety).

## ✅ Answerable now

Already computed, or present as a graph triple we can wire into an indicator.

| EVERSE indicator | Dimension | OpenPulse signal |
| --- | --- | --- |
| `software_has_license` | fairness | `license_coverage` / `licenses_declared` — `schema:license`, `gme:license_name`/`license_spdx` |
| `listed_in_registry` | fairness | `gme:pypi_versions` / `npm_versions` / `maven_versions` (published to a package registry) |
| `has_published_package` | flexibility | same registry-version triples |
| `version_control_use` | fairness | definitional — every entity is a git repository |
| `software_is_containerized` | fairness | `gme:compose_files` / `compose_file_count` / `compose_images` |
| `software_has_documentation` | fairness / interaction | `docs_discoverability` — README, homepage, wiki, Pages, `gme:documentation_urls` |
| `descriptive_metadata` | fairness | `gme:description` + `keywords` + `homepage` + `schema:programmingLanguage` + `license` |
| `has_releases` | maintainability / fairness | `release_frequency` — `gme:release_count`, `git_tags`, `git_tag_count` |
| `project_is_active` | maintainability | `activity_dates` / `project_demographics` + `gme:pushed_at`, `archived` flag |
| `repository_workflows` | maintainability | `gme:has_ci` (present on ~19k repos) |
| `has_ci-tests` | maintainability | `gme:has_ci` (CI present; tests-in-CI is a refinement) |
| `support_issue_tracking` | maintainability | `gme:has_issues` |
| `has_contribution_guidelines` | sustainability | `gme:contributing_url` (also `has_code_of_conduct`, `has_issue_template`, `has_pull_request_template`) |
| `dependency_management` | sustainability / security | `upstream_dependencies` — Neo4j `DEPENDS_ON` from manifests |
| `requirements_specified` | fairness / maintainability | dependency manifests (`requirements.txt`, `pyproject.toml`, `package.json`, `go.mod`, cargo) |
| `software_test_coverage` | reliability | `test_coverage` — `gme:test_coverage` triple / README coverage badge (self-reported) |
| `has_active_contributors` | community | `contributors` / `committers` / `inactive_contributors` |
| `response_timeframe_ok` | community | `first_response` / `issue_response_time` (apply a timeframe threshold) |

Bonus signals already in the graph, not EVERSE indicators but adjacent:
`gme:community_health_percentage` (GitHub's composite community-profile score),
`has_code_of_conduct`, `has_issue_template`, `has_pull_request_template`,
`badge_count`.

## 🟡 Modest effort

Data is close; needs a small computation, a heuristic, or one extraction field.

| EVERSE indicator | Dimension | Path to it |
| --- | --- | --- |
| `versioning_standards_use` | fairness | Regex/semver-validate `gme:git_tags` / `latest_version`. |
| `software_has_citation` | fairness | Add a `CITATION.cff` / `codemeta.json` presence flag to GME extraction (`schema:citation` today only links a **publication** DOI). |
| `metadata_is_up_to_date` | maintainability | Freshness from `gme:updated_at` / `pushed_at` vs. a recency threshold. |
| `software_has_tests` | fairness / reliability | Detect a test dir / test files during extraction (today only a coverage *number* is read). |
| `human_code_review_requirement` | functional | `cr_reviews` + inverse `self_merge` measure review *practice* (not a hard "requirement"). |
| `code_churn_ok` | maintainability | `code_lines` (added/removed) exists — needs a "community-convention" threshold. |
| `lines_of_code_ok` | maintainability | `gme:size_kb` + `code_lines` proxy LOC — needs a standard to compare against. |
| `persistent_and_unique_identifier` | fairness | Link a repo → its Zenodo/DOI deposit (DOIs already resolved for records/publications). |
| `archived_in_scholarly_repository` | fairness | Same Zenodo linkage (Zenodo is a first-class entity). |
| `has_active_communication_channels` | maintainability / community | `has_discussions` + homepage + `documentation_urls` (weak proxy for "active"). |
| `uses_tool_for_warnings_and_mistakes` | maintainability | Infer from CI/badges (`has_ci`, badge URLs) — heuristic. |
| `codemeta_completeness` | fairness | Proxy score from existing descriptive metadata (strict codemeta.json parsing → gap). |

## 🔴 New capability required

These need per-repo **source-code analysis** or **security scanning** — cloning
each repo and running tools. OpenPulse has no such pipeline today.

| EVERSE indicator | Dimension | Why it's a gap |
| --- | --- | --- |
| `cyclomatic_complexity_ok` | maintainability | Static AST analysis per file. |
| `maintainability_index_ok` | maintainability | Composite static metric (Radon/SonarQube-style). |
| `code_duplication_ok` | maintainability | Clone detection over source. |
| `code_smells_ok` | maintainability | Linter/analyser rules over source. |
| `internal_cohesion_ok` | maintainability | Module-level static analysis. |
| `coupling_between_objects_ok` | maintainability | Static dependency analysis within the code. |
| `has_no_linting_issues` | maintainability | Run a linter. |
| `code_documentation_coverage_ok` | maintainability / fairness | Docstring/API-doc coverage over source (≠ repo-level docs). |
| `passed_tests_ok` | fairness / functional | Actually build & run the test suite. |
| `functional_correctness` | functional | Execute tests / oracles. |
| `software_has_license_for_file_types` | fairness | Per-file license scanning (explicitly not collected). |
| `static_analysis_common_vulnerabilities` | security | Run SAST (CodeQL/Semgrep). |
| `no_critical_vulnerability` | security | Vulnerability/CVE scan of deps + code. |
| `no_leaked_credentials` | security | Secret scanning. |
| `has_no_binary_artifacts` | security | Tree scan for committed binaries. |
| `uses_fuzzing` | safety | Detect/verify a fuzzing harness (weak metadata proxy at best). |
| `archived_in_software_heritage` | fairness | New integration — query the Software Heritage API per repo (feasible, but a new external source). |

## What building the reachable set would look like

1. **Surface the ✅ tier (~18)** as a new indicator group — mostly reading
   existing `gme:*` triples and CHAOSS metrics into a boolean/scored
   "EVERSE indicator" shape, aligned to the RSQI ontology
   (`@type: SoftwareQualityIndicator`). Low, mechanical.
2. **Add the 🟡 tier (~12)** incrementally — a few new GME extraction fields
   (`CITATION.cff`, `codemeta.json`, test-dir presence, semver check) plus
   Zenodo↔repo linkage. Medium.
3. **The 🔴 tier (~17)** is a separate product decision: stand up a
   source-analysis pipeline (clone → SonarQube/CodeQL/coverage/secret-scan),
   or consume an existing service's results, or simply mark them out of scope.

The natural first deliverable is an **EVERSE indicators endpoint/page** exposing
the reachable ~30, emitted in the RSQI JSON-LD shape so results are
interoperable with the EVERSE ecosystem.
