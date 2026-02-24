"""Quest command group -- analysis pipeline execution."""

from __future__ import annotations

import typer

app = typer.Typer(help="Run analysis quest pipeline.")


@app.command()
def start(
    config: str = typer.Option("quest.yml", "--config", help="Quest config file."),
) -> None:
    """Run the full quest pipeline end-to-end."""
    typer.echo(f"[quest start] placeholder – not yet implemented (config={config})")


@app.command(name="run-step")
def run_step(
    step: str = typer.Argument(help="Pipeline step name to execute."),
    config: str = typer.Option("quest.yml", "--config", help="Quest config file."),
) -> None:
    """Run a single pipeline step by name."""
    typer.echo(
        f"[quest run-step] placeholder – not yet implemented (step={step}, config={config})"
    )
