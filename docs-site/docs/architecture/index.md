---
title: Architecture
slug: /architecture
---

# Architecture

## Repository boundaries

- `src/open_pulse/`: CLI and runtime code
- `infra/open-pulse-stack/`: the Open Pulse stack (main + cli + grimoirelab)
- `infra/services/`: single-service deployment references

## Runtime structure

- `commands/`: user-facing CLI entrypoints
- `pipeline/`: quest orchestration and steps
- `services/`: service clients/config/health probes
- `orchestrator.py`: sequential execution with checkpoint/resume

## Pipeline execution flow

1. Load and validate quest config.
2. Build run-scoped `ServiceContainer` from `quest.services`.
3. Wrap enabled steps with retry policy.
4. Run sequentially with checkpoint/resume support.
5. Pass shared context to steps, including `context["services"]`.
6. Close all services at the end (success or failure).

## Health execution flow

1. Check Docker daemon reachability.
2. Read container status from `docker compose ps`.
3. Probe endpoints through `open_pulse.services.health`.
4. Run smoke tests.
5. Exit non-zero if any check fails.
