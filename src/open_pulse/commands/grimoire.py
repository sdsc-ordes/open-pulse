"""Grimoire command group -- GrimoireLab configuration tools."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

console = Console(stderr=True)
app = typer.Typer(help="GrimoireLab configuration tools.")


@app.command(name="prepare-config")
def prepare_config(
    neo4j_endpoint: Annotated[
        str,
        typer.Option(
            "--neo4j",
            help="Neo4j Bolt endpoint.",
        ),
    ] = "bolt://localhost:7687",
    tentris_endpoint: Annotated[
        str,
        typer.Option(
            "--tentris",
            help="Tentris SPARQL endpoint.",
        ),
    ] = "http://localhost:7502/sparql",
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output path for the generated projects.json.",
        ),
    ] = Path("projects.json"),
) -> None:
    """Generate GrimoireLab project configuration via SPARQL queries."""
    from open_pulse.grimoire.sparql_config import generate_config

    generate_config(
        neo4j_endpoint=neo4j_endpoint,
        tentris_endpoint=tentris_endpoint,
        output=output,
    )


@app.command()
def ui() -> None:
    """Launch the Streamlit configuration UI.

    Requires the ``grimoire-ui`` optional dependency group
    (``pip install open-pulse[grimoire-ui]``).
    """
    try:
        import streamlit  # noqa: F401
    except ImportError:
        console.print(
            "[red bold]Error:[/red bold] Streamlit is not installed.\n"
            "Install the grimoire-ui extra:  "
            "[bold]pip install open-pulse\\[grimoire-ui][/bold]"
        )
        raise typer.Exit(code=1) from None

    from open_pulse.grimoire.streamlit_app import launch_streamlit

    launch_streamlit()


@app.command(name="install-watcher")
def install_watcher(
    repo_url: Annotated[
        str,
        typer.Option(
            "--repo",
            "-r",
            help="Git remote URL of the repository to watch.",
        ),
    ],
    config_path: Annotated[
        str,
        typer.Option(
            "--config-path",
            help="Relative path to the config file inside the repo.",
        ),
    ] = "projects.json",
    branch: Annotated[
        str,
        typer.Option(
            "--branch",
            "-b",
            help="Git branch to track.",
        ),
    ] = "main",
    schedule: Annotated[
        str,
        typer.Option(
            "--schedule",
            "-s",
            help="Cron schedule expression.",
        ),
    ] = "*/30 * * * *",
    clone_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--clone-dir",
            help="Local directory to clone the repo into.",
        ),
    ] = None,
) -> None:
    """Install a cron job to watch a git repo for config changes.

    Linux/macOS only.  On Windows, prints guidance for Task Scheduler.
    """
    from open_pulse.grimoire.cronjob import install_watcher as _install

    _install(
        repo_url=repo_url,
        config_path=config_path,
        branch=branch,
        schedule=schedule,
        clone_dir=clone_dir,
    )
