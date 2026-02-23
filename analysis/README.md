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
