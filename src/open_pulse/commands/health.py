"""Health command -- service health checks."""

from __future__ import annotations

import typer


def check() -> None:
    """Check the health of all deployed services."""
    typer.echo("[health] placeholder – not yet implemented")
