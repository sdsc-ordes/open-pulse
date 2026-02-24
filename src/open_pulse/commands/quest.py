"""Quest command group -- analysis pipeline execution."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from open_pulse.pipeline.runner import (
    STEP_NAMES,
    run_pipeline,
    run_single_step,
)

console = Console(stderr=True)
app = typer.Typer(help="Run analysis quest pipeline.")


@app.command()
def start(
    config: Path = typer.Option(
        "quest.yml",
        "--config",
        "-c",
        help="Path to quest config YAML.",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        "-r",
        help="Resume from last checkpoint.",
    ),
) -> None:
    """Run the full quest pipeline end-to-end."""
    completed = run_pipeline(config, resume=resume)
    console.print(
        f"[green]Pipeline finished.[/green] "
        f"Completed {len(completed)} step(s): {', '.join(completed)}"
    )


@app.command(name="run-step")
def run_step(
    step: str = typer.Argument(help="Pipeline step name to execute."),
    config: Path = typer.Option(
        "quest.yml",
        "--config",
        "-c",
        help="Path to quest config YAML.",
    ),
) -> None:
    """Run a single pipeline step by name."""
    try:
        run_single_step(config, step)
    except ValueError as exc:
        console.print(f"[red bold]Error:[/red bold] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Step '{step}' completed successfully.[/green]")


@app.command(name="list-steps")
def list_steps() -> None:
    """List available pipeline steps."""
    for name in STEP_NAMES:
        typer.echo(name)
