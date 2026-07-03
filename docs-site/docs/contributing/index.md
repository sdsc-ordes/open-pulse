---
title: Contributing
slug: /contributing
---

# Contributing

Thanks for considering a contribution. This page is a hands-on guide
for setting up a development environment, running the same gates CI
runs, and shipping a change. Repository conventions
(branching, commit style, PR rules) live alongside the code in
[`CONTRIBUTING.md`](https://github.com/sdsc-ordes/open-pulse/blob/main/CONTRIBUTING.md);
the source-of-truth architectural map for the codebase is
[`AGENTS.md`](https://github.com/sdsc-ordes/open-pulse/blob/main/AGENTS.md).

Open Pulse is a **monorepo** — the CLI, the FastAPI hub, the
service clients, the quest pipeline and the docs site all live in
[`sdsc-ordes/open-pulse`](https://github.com/sdsc-ordes/open-pulse).
External services such as the crawler and the metadata extractor are
consumed as container images; there is nothing else to clone.

## Prerequisites

- **Git** and a GitHub account.
- **Python 3.11+** managed by [`uv`](https://github.com/astral-sh/uv).
  The project supports 3.11, 3.12 and 3.13.
- **Docker** + Docker Compose for running the stack (and for the
  `docker-validate` CI job).
- **Node.js 18+** with `pnpm` if you intend to touch `docs-site/`.
- **`just`** (optional but recommended) — runs the same composite gates
  CI runs.

## First-time setup

```bash
git clone https://github.com/sdsc-ordes/open-pulse
cd open-pulse

# 1. Python environment, locked.
uv sync --group dev --group test

# 2. Pre-commit hooks (markdown lint, formatters, license headers, …).
uv run --with pre-commit pre-commit install
```

That is enough to run the test suite and the formatters. If you also
want to bring up the stack locally, see
[Getting Started](../getting-started/index.md) — the rest of this page
focuses on the code contribution path.

## Running the gates locally

CI runs three composite jobs on every push: `python-tests`,
`pre-commit-quality-gates` and `shell-script-sanity`. The `justfile`
mirrors them so a green run on your laptop predicts a green PR.

```bash
# Run all three CI gates (single Python version).
just pre-commit

# Run pytest across the full Python matrix (3.11 / 3.12 / 3.13).
# Slower; use before pushing if you want full parity.
just pre-commit-matrix
```

Without `just`, the equivalent calls are:

```bash
npx markdownlint-cli2 --config .markdownlint.jsonc --fix \
    "**/*.md" "#docs-site/node_modules/**" "#.venv/**" "#.venv-matrix/**"
uv run --with pre-commit pre-commit run --all-files
uv sync --group dev --group test
uv run --with pytest-cov pytest -q --cov=src \
    --cov-report=term-missing --cov-report=xml
```

## Working on the docs site

The Docusaurus site under `docs-site/` is independent of the Python
project. Install deps once and you can iterate with hot reload:

```bash
cd docs-site
pnpm install
pnpm start                          # http://localhost:3000
DOCS_BASE_URL=/ pnpm build          # what CI builds
```

The deployed site lives under `/open-pulse/docs/` so CI sets
`DOCS_BASE_URL=/open-pulse/docs/`. Locally, use `/` so internal links
resolve.

## Branching, commits, PRs

Read the full rules in
[`CONTRIBUTING.md`](https://github.com/sdsc-ordes/open-pulse/blob/main/CONTRIBUTING.md);
the short version:

- Branch from `develop` with a clear intent prefix:
  `feat/…`, `fix/…`, `docs/…`, `chore/…`.
- Commit messages use Conventional Commits — `release-please` reads
  them to compute the next semver bump
  (see [Release checklist](../operations/release-checklist.md)).
- Open PRs against `develop` (release PRs against `main` use a
  separate flow).
- One concern per PR. Link the related issue.
- Required CI gates on `main`: `ci`, `docker-validate`, `docs-build`.

## Repository tour

A high-level orientation; the authoritative map is
[`AGENTS.md`](https://github.com/sdsc-ordes/open-pulse/blob/main/AGENTS.md).

| Path                                | What lives here                                                                                  |
| ----------------------------------- | ------------------------------------------------------------------------------------------------ |
| `src/open_pulse/`                   | Python package: CLI, orchestrator, command groups, service clients, hub.                         |
| `src/open_pulse/cli.py`             | Typer entry point that mounts the `deploy`, `quest`, `services`, `gui`, `health` command groups. |
| `src/open_pulse/orchestrator.py`    | Sequential quest orchestrator with checkpoint/resume.                                            |
| `src/open_pulse/services/`          | Tech-agnostic service clients (Neo4j, sparql_store, crawler, metadata_extractor, …).             |
| `src/open_pulse/gui/hub/`           | FastAPI control-plane dashboard.                                                                 |
| `infra/open-pulse-stack/`           | `docker-compose.yml` + overlays (`cli`, `grimoirelab`) and GrimoireLab assets.                   |
| `infra/services/`                   | Single-service deployment references (Neo4j, Oxigraph, sparql-proxy, …).                         |
| `tools/images/Dockerfile-open-pulse`| The unified image used as cli / orchestrator / hub.                                              |
| `docs-site/`                        | This Docusaurus documentation site.                                                              |
| `docs/`                             | Static landing + the `node-builder` and `env-wizard` browser apps.                               |
| `scripts/op`                        | Host-side wrapper that `docker exec`s into `open-pulse-cli`.                                     |
| `tests/`                            | Pytest suites (mock-based; no live-container integration tests yet).                             |

## Sharing analyses, data, or research

Open Pulse is meant to be used by researchers, not only operators. If
you have built something on top of the data — a notebook, a paper, a
visualisation — open a PR adding it under
[`docs-site/docs/use-cases/`](../use-cases/index.md) or open a
[Discussion](https://github.com/sdsc-ordes/open-pulse/discussions). The
maintainers will help shape it into a documented use case.

## Where to ask

- **Bug or feature.**
  [Open an issue](https://github.com/sdsc-ordes/open-pulse/issues).
- **Discussion / research.**
  [GitHub Discussions](https://github.com/sdsc-ordes/open-pulse/discussions).
- **Direct contact.** See [Community](../community/index.md).
