# Contributing

Thanks for helping improve this project.

## Branching Rules

- Use short-lived feature branches from `main`.
- Name branches with clear intent, for example: `feat/<topic>`, `fix/<topic>`, `docs/<topic>`, or `chore/<topic>`.
- Keep pull requests focused on one concern.

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

## Review Rules

- At least one reviewer approval is required before merge.
- Changes under owned paths must be reviewed by matching `CODEOWNERS`.
- Address review comments with follow-up commits (avoid force-push rewrites during active review unless agreed).

## Local Development Expectations

- Do not commit secrets, credentials, runtime data, or large generated artifacts.
- Follow `.editorconfig` and repository lint/format conventions.
