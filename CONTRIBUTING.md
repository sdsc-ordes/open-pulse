# Contributing

Thanks for helping improve this project.

## Branching Rules

- Use short-lived feature branches from `develop`.
- Name branches with clear intent, for example: `feat/<topic>`, `fix/<topic>`, `docs/<topic>`, or `chore/<topic>`.
- Keep pull requests focused on one concern.
- `main` is protected: direct pushes are blocked and required checks must pass before merge.
- Required checks for merge to `main`:
  - `ci`
  - `docker-validate`
  - `docs-build`

## Commit Rules

- Use semantic commits:
  - `feat:` for new functionality
  - `fix:` for bug fixes
  - `docs:` for documentation-only changes
  - `refactor:`, `test:`, `chore:`, `ci:` where appropriate
- Keep commit messages in imperative voice and scoped to the why.

## Pull Request Rules

- Open a pull request against `main` unless a release branch is explicitly requested.
- Link any related issue or task in the PR description.
- Include:
  - a short summary of intent
  - validation steps (commands run, manual checks)
  - screenshots/logs when UI or behavior changes
- Ensure CI checks pass before requesting review.
- Keep your branch up to date with `main` before merge.

## Review Rules

- At least one reviewer approval is required before merge.
- Changes under owned paths must be reviewed by matching `CODEOWNERS`.
- Address review comments with follow-up commits (avoid force-push rewrites during active review unless agreed).

## Release Strategy

- Stable releases are cut from semver tags only (`vX.Y.Z`).
- Pushing a matching tag triggers `.github/workflows/release.yml`.
- The release workflow builds and attaches:
  - open-pulse image archive
  - devcontainer image archive
  - release checksums
  - open-pulse wheel artifact
- Follow `docs-site/docs/operations/release-checklist.md` before publishing any draft release.

## Local Development Expectations

- Do not commit secrets, credentials, runtime data, or large generated artifacts.
- Follow `.editorconfig` and repository lint/format conventions.

## Pre-commit Quality Gates

- Install pre-commit once in your local environment:
  - `python -m pip install pre-commit`
  - `pre-commit install`
- Run all configured checks before opening a PR:
  - `pre-commit run --all-files`
- When hooks report fixes (for example formatting), review and re-stage those changes before committing.
- CI runs the same `.pre-commit-config.yaml` hooks to keep local and remote quality gates aligned.
