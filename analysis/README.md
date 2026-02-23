# openpulse-analysis

Installable analysis package for Open Pulse.

## Quick Start

```bash
uv sync --group dev --group test
uv run openpulse-analysis --help
uv run pytest -q
uv build
```

## Sequential orchestration CLI

```bash
# Show configured tasks in execution order
uv run openpulse-analysis list-tasks

# Execute the full task pipeline with checkpoint state
uv run openpulse-analysis run --checkpoint-path .openpulse-analysis.checkpoint.json

# Resume from the last successful task after a failure
uv run openpulse-analysis run --resume --checkpoint-path .openpulse-analysis.checkpoint.json

# Validate local runtime prereqs
uv run openpulse-analysis doctor
```

## Containerized runtime

Build the analysis image from the repository root:

```bash
docker build -f analysis/docker/Dockerfile -t openpulse-analysis:local .
```

Run the default smoke check (`doctor`):

```bash
docker run --rm openpulse-analysis:local
```

Run a specific CLI command:

```bash
docker run --rm openpulse-analysis:local list-tasks
```

Verify the container process is non-root:

```bash
docker run --rm --entrypoint id openpulse-analysis:local
```

## Devcontainer workflow

The repository includes `.devcontainer/` for analysis development with Python 3.11 and `uv` preinstalled.

1. Open the project in VS Code/Cursor.
2. Run "Dev Containers: Reopen in Container".
3. Wait for post-create setup (`cd analysis && uv sync --group dev --group test`).
4. Run checks:

```bash
cd analysis
uv run pytest -q
uv run openpulse-analysis doctor
```

To enable Docker CLI access from inside the devcontainer, add the Docker feature in `.devcontainer/devcontainer.json`:

```json
"features": {
  "ghcr.io/devcontainers/features/docker-outside-of-docker:1": {}
}
```
