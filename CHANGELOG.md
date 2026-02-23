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

### Changed

- Normalized `.gitignore` for Python artifacts, Docker/runtime data, local data, and secret-like files.
- Updated `docs/README.md` to mark static landing as legacy and point to new docs migration/branch-model documentation.
- Updated `.gitignore` with docs tooling artifacts (`node_modules/`, `docs-site/build/`).
