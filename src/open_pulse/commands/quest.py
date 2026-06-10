"""Quest command group -- analysis pipeline execution."""

from __future__ import annotations

import os
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

# Run-quest configs live in a gitignored exchange folder (under ``data/``),
# overridable via ``OPEN_PULSE_QUEST_DIR``. ``config/`` holds only committed
# example templates. A bare ``--config name`` resolves to
# ``$OPEN_PULSE_QUEST_DIR/name.yml``; an explicit path is used verbatim.
QUEST_DIR_ENV = "OPEN_PULSE_QUEST_DIR"
DEFAULT_QUEST_DIR = "data/quests"


def _resolve_quest_config(config: Path) -> Path:
    """Resolve a bare quest name to ``$OPEN_PULSE_QUEST_DIR/<name>.yml``.

    A path with a directory component (``config/x.yml``, ``./x.yml``,
    ``/abs/x.yml``) or one that already exists is used verbatim — only a
    bare name (no separator) is looked up in the run-quest dir.
    """
    if config.is_absolute() or config.parent != Path(".") or config.exists():
        return config
    quest_dir = Path(os.environ.get(QUEST_DIR_ENV, DEFAULT_QUEST_DIR))
    name = config.name
    if not name.endswith((".yml", ".yaml")):
        name += ".yml"
    return quest_dir / name


@app.command()
def start(
    config: Path = typer.Option(
        "quest.yml",
        "--config",
        "-c",
        help=(
            "Quest config: a bare name (resolved under $OPEN_PULSE_QUEST_DIR, "
            "default data/quests/) or an explicit path."
        ),
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        "-r",
        help="Resume from last checkpoint.",
    ),
) -> None:
    """Run the full quest pipeline end-to-end."""
    config = _resolve_quest_config(config)
    console.print(f"[dim]quest config:[/dim] {config}")
    try:
        completed = run_pipeline(config, resume=resume)
    except FileNotFoundError as exc:
        console.print(f"[red bold]Error:[/red bold] {exc}")
        raise typer.Exit(code=1) from exc
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
        help=(
            "Quest config: a bare name (resolved under $OPEN_PULSE_QUEST_DIR, "
            "default data/quests/) or an explicit path."
        ),
    ),
) -> None:
    """Run a single pipeline step by name."""
    config = _resolve_quest_config(config)
    try:
        run_single_step(config, step)
    except (ValueError, FileNotFoundError) as exc:
        console.print(f"[red bold]Error:[/red bold] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Step '{step}' completed successfully.[/green]")


@app.command(name="list-steps")
def list_steps() -> None:
    """List available pipeline steps."""
    for name in STEP_NAMES:
        typer.echo(name)
