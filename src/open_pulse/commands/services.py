"""Services command group -- service-oriented tooling."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer

app = typer.Typer(help="Service-oriented commands.")
grimoire_app = typer.Typer(help="Grimoire services commands.")
app.add_typer(grimoire_app, name="grimoire")


@grimoire_app.command(name="prepare-config")
def prepare_config(
    neo4j_endpoint: Annotated[
        str,
        typer.Option(
            "--neo4j",
            help="Neo4j Bolt endpoint.",
        ),
    ] = "bolt://localhost:7687",
    sparql_endpoint: Annotated[
        str,
        typer.Option(
            "--sparql",
            help="SPARQL store base URL.",
        ),
    ] = "http://localhost:7878",
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
    from open_pulse.utils.grimoire.sparql_config import generate_config

    generate_config(
        neo4j_endpoint=neo4j_endpoint,
        sparql_endpoint=sparql_endpoint,
        output=output,
    )


@grimoire_app.command(name="install-watcher")
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
    from open_pulse.utils.grimoire.cronjob import install_watcher as _install

    _install(
        repo_url=repo_url,
        config_path=config_path,
        branch=branch,
        schedule=schedule,
        clone_dir=clone_dir,
    )
