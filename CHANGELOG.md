# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added `LICENSE` with Apache-2.0 terms.
- Added `CONTRIBUTING.md` with branching, PR, semantic commit, and review rules.
- Added `SECURITY.md` with private vulnerability reporting and disclosure workflow.
- Added `.github/CODEOWNERS` ownership boundaries for `src/`, `analysis/`, `infra/`, and docs paths.
- Added `.editorconfig` and `.gitattributes` for baseline repository consistency.
- Added `docs-site/` with a Docusaurus scaffold and `pnpm` scripts for docs development/build.
- Added documentation information architecture in `docs-site/docs/` with `getting-started`, `architecture`, `services`, `analysis`, and `operations` sections.
- Added explicit docs branch responsibilities in `docs-site/docs/operations/branch-model.md` (`docs` as source of truth, `main` as reference/output consumer).
- Added migration mapping from static `docs/` landing to Docusaurus source in `docs-site/docs/operations/migration-from-static-docs.md`.
- Added a root README service catalog with ports, compose profile, and status columns.
- Added a root README decision note defining `src/`, `analysis/`, and `infra/` boundaries.
- Added `infra/env/.env.example` documenting required root Compose environment variables.
- Added `infra/compose/` profile override assets for analysis, grimoirelab, and orchestration stacks.
- Added `analysis/` as an installable Python package scaffold managed by `uv`, including `pyproject.toml`, `README.md`, `src/openpulse_analysis/`, and `tests/`.
- Added `openpulse-analysis` console entry point and baseline `dev`/`test` dependency groups.
- Added `analysis/uv.lock` and initial CLI/test scaffolding to support package install, smoke runs, and packaging validation.
- Added sequential orchestration modules in `analysis/src/openpulse_analysis/` for task contracts, registry ordering, checkpoint persistence, and failure propagation.
- Added `run`, `list-tasks`, and `doctor` CLI commands with checkpoint/resume behavior and explicit non-zero failure exits.
- Added `analysis/scripts/run-sequential.sh` wrapper to invoke the sequential runner and preserve process exit codes.
- Added orchestration-focused tests in `analysis/tests/test_cli.py` for task order, failure behavior, resume flow, CLI command contracts, and wrapper semantics.

### Changed

- Normalized `.gitignore` for Python artifacts, Docker/runtime data, local data, and secret-like files.
- Updated `docs/README.md` to mark static landing as legacy and point to new docs migration/branch-model documentation.
- Updated `.gitignore` with docs tooling artifacts (`node_modules/`, `docs-site/build/`).
- Rewrote root `README.md` for onboarding with project purpose, architecture overview, DB stack quick start, `uv`-based analysis quick start, documentation navigation links, and release/contribution references.
- Refactored root `docker-compose.yml` into a profile-aware topology with default Neo4j plus opt-in `analysis`, `grimoirelab`, and `orchestration` services.
- Added healthchecks and dependency readiness gates for key profile services (`neo4j`, `analysis-notebook`, and `grimoirelab-db`).
- Expanded `analysis/README.md` with sequential orchestration usage and checkpoint resume guidance.
