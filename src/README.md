# open-pulse

Unified CLI package for Open Pulse.

## Quick Start

```bash
uv sync --group dev --group test
uv run open-pulse --help
uv run pytest -q
uv build
```

## Sequential orchestration CLI

```bash
# Show configured tasks in execution order
uv run open-pulse list-tasks

# Execute the full task pipeline with checkpoint state
uv run open-pulse run --checkpoint-path .openpulse.checkpoint.json

# Resume from the last successful task after a failure
uv run open-pulse run --resume --checkpoint-path .openpulse.checkpoint.json

# Validate local runtime prereqs
uv run open-pulse doctor
```

## Containerized runtime

Build the image from the repository root:

```bash
docker build -f src/docker/Dockerfile -t open-pulse:local .
```

Run the default smoke check (`doctor`):

```bash
docker run --rm open-pulse:local
```

Run a specific CLI command:

```bash
docker run --rm open-pulse:local list-tasks
```

Verify the container process is non-root:

```bash
docker run --rm --entrypoint id open-pulse:local
```

## Devcontainer workflow

The repository includes `.devcontainer/` for development with Python 3.11 and `uv` preinstalled.

1. Open the project in VS Code/Cursor.
2. Run "Dev Containers: Reopen in Container".
3. Wait for post-create setup (`cd src && uv sync --group dev --group test`).
4. Run checks:

```bash
cd src
uv run pytest -q
uv run open-pulse doctor
```

To enable Docker CLI access from inside the devcontainer, add the Docker feature in `.devcontainer/devcontainer.json`:

```json
"features": {
  "ghcr.io/devcontainers/features/docker-outside-of-docker:1": {}
}
```
