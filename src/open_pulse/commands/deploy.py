"""Deploy command group -- Docker infrastructure management."""

from __future__ import annotations

import typer

app = typer.Typer(help="Deploy Docker infrastructure.")


@app.command()
def up() -> None:
    """Deploy services using Docker Compose with an interactive profile selector."""
    typer.echo("[deploy up] placeholder – not yet implemented")
