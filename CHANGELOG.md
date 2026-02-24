# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Implemented `quest` command group with `start`, `run-step`, and `list-steps` sub-commands for analysis pipeline execution.
- Added Pydantic config schema (`pipeline/config.py`) for quest YAML validation with retry, logging, and per-step configuration.
- Added pipeline runner (`pipeline/runner.py`) with configurable retry/backoff logic, Python logging setup, and integration with the existing sequential orchestrator for checkpoint/resume support.
- Added placeholder pipeline step modules: `crawler`, `neo4j_upload`, `metadata_extractor`, and `tentris_upload` under `src/open_pulse/pipeline/`.
- Added `config/quest.example.yml` with documented example quest configuration.
- Added quest pipeline tests covering config loading, task building, disabled-step filtering, retry behaviour, checkpoint resume, CLI commands, and unknown-step error handling.

- Implemented `deploy up` command with Docker availability check, interactive profile selection via `questionary`, `.env` loading/generation from `infra/env/.env.example`, and `docker compose up -d` invocation with profile flags.
- Added `deploy down` command to tear down services with optional `--volumes` flag.
- Added `deploy ps` command to show container status.
- Added deploy command tests covering Docker-not-available error, profile flag pass-through, `.env` template creation, `down`, and `ps` sub-commands.
- Added CLI Command Reference section to `AGENTS.md` with `deploy` sub-command docs.

- Added core CLI dependencies to `pyproject.toml`: `typer`, `questionary`, `pydantic`, `pyyaml`, `python-dotenv`, and `rich`.
- Added `grimoire-ui` optional dependency group with `streamlit` for the GrimoireLab config UI.
- Replaced argparse CLI with a Typer-based entry point (`cli.py`) exposing four command groups: `deploy`, `quest`, `grimoire`, and `health` (all stubs).
- Added `src/open_pulse/commands/` package with stub modules `deploy.py`, `quest.py`, `grimoire.py`, and `health.py`.
- Added Typer CliRunner tests for every stub command; kept pure orchestrator tests unchanged.

### Changed

- Adopted standard Python src-layout: moved `pyproject.toml` and `uv.lock` from `src/` to project root so `uv` commands run from the root directory.
- Moved Dockerfile from `src/docker/Dockerfile` to `tools/images/Dockerfile-open-pulse`, matching the existing `tools/images/` convention.
- Moved tests from `src/tests/` to root-level `tests/`.
- Moved `src/scripts/run-sequential.sh` to `tools/scripts/run-sequential.sh`.
- Removed `src/README.md` (root README is sufficient).
- Updated all CI workflows, `.pre-commit-config.yaml`, `.devcontainer/devcontainer.json`, `AGENTS.md`, and root `README.md` to reference new paths.

- Moved service deployment configs (`neo4j/`, `tentris-server/`, `portainer/`) from `src/` to `infra/services/` so `src/` is reserved for CLI source code.
- Moved analysis package from `analysis/src/openpulse_analysis/` into `src/open_pulse/`, renaming the package from `openpulse-analysis` to `open-pulse`.
- Moved analysis tests from `analysis/tests/` to `src/tests/`.
- Moved analysis Dockerfile from `analysis/docker/` to `src/docker/`.
- Moved `analysis/pyproject.toml`, `analysis/uv.lock`, and `analysis/README.md` into `src/`.
- Moved `analysis/scripts/run-sequential.sh` to `src/scripts/run-sequential.sh`.
- Updated all CI workflows, `.pre-commit-config.yaml`, `.devcontainer/devcontainer.json`, `.github/CODEOWNERS`, `.gitignore`, and root `README.md` to reference new paths.
- Renamed CLI entry point from `openpulse-analysis` to `open-pulse`.

### Added

- Added `AGENTS.md` documenting the new directory layout, key commands, and conventions.

### Removed

- Removed `analysis/` directory (contents migrated to `src/`).

### Added

- Added `.github/workflows/docs-build.yml` to run Docusaurus validation with `pnpm install --frozen-lockfile` and `pnpm build` so broken links fail CI.
- Added PR docs preview artifact publishing in `docs-build` so pull requests provide downloadable static docs output.
- Added `.github/workflows/docs-pages-deploy.yml` to build validated docs artifacts from the `docs` branch and deploy them to GitHub Pages.
- Added `.github/workflows/release.yml` to trigger on stable semver tags (`vX.Y.Z`), build release assets (image archives, checksums, analysis wheel), and create draft GitHub releases with generated notes.
- Added `docs-site/docs/operations/release-checklist.md` covering `main` branch protection baseline, release execution steps, and release finalization checks.
- Added root `.pre-commit-config.yaml` with hooks for trailing whitespace, EOF fixes, YAML/JSON validation, Ruff lint/format on `analysis/`, and optional Markdown linting.
- Added `.github/workflows/ci.yml` baseline CI with path-scoped triggers and split jobs for analysis lint/tests, YAML/Markdown validation, and shell script sanity checks.
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
- Added `analysis/docker/Dockerfile` with a slim Python base, pinned `uv` version, non-root runtime user, and `openpulse-analysis` CLI entrypoint.
- Added `.devcontainer/` configuration for analysis development with Python 3.11, `uv` bootstrapping, and recommended VS Code extensions/settings.
- Added `.github/workflows/docker-validate.yml` with Docker Compose config validation, CI image builds for analysis/devcontainer, and Trivy-based critical vulnerability gating with scan artifacts.

### Changed

- Updated `docs-site/docs/operations/index.md` to include the release checklist in operations navigation.
- Updated `CONTRIBUTING.md` with `main` branch protection requirements, required merge checks (`ci`, `docker-validate`, `docs-build`), and semver-tag release strategy guidance.
- Updated `CONTRIBUTING.md` with pre-commit installation and all-files execution guidance for local quality checks before PRs.
- Updated `.github/workflows/ci.yml` to execute `pre-commit run --all-files` in CI for local/CI quality-gate parity.
- Normalized `.gitignore` for Python artifacts, Docker/runtime data, local data, and secret-like files.
- Updated `docs/README.md` to mark static landing as legacy and point to new docs migration/branch-model documentation.
- Updated `.gitignore` with docs tooling artifacts (`node_modules/`, `docs-site/build/`).
- Rewrote root `README.md` for onboarding with project purpose, architecture overview, DB stack quick start, `uv`-based analysis quick start, documentation navigation links, and release/contribution references.
- Refactored root `docker-compose.yml` into a profile-aware topology with default Neo4j plus opt-in `analysis`, `grimoirelab`, and `orchestration` services.
- Added healthchecks and dependency readiness gates for key profile services (`neo4j`, `analysis-notebook`, and `grimoirelab-db`).
- Expanded `analysis/README.md` with sequential orchestration usage and checkpoint resume guidance.
- Expanded `analysis/README.md` with container build/smoke/non-root checks and devcontainer setup guidance.
