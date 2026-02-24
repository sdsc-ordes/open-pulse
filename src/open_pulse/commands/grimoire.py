"""Grimoire command group -- GrimoireLab configuration tools."""

from __future__ import annotations

import typer

app = typer.Typer(help="GrimoireLab configuration tools.")


@app.command(name="prepare-config")
def prepare_config() -> None:
    """Generate GrimoireLab project configuration via SPARQL queries."""
    typer.echo("[grimoire prepare-config] placeholder – not yet implemented")


@app.command()
def ui() -> None:
    """Launch the Streamlit configuration UI."""
    typer.echo("[grimoire ui] placeholder – not yet implemented")


@app.command(name="install-watcher")
def install_watcher() -> None:
    """Install a cron job to watch a git repo for config changes."""
    typer.echo("[grimoire install-watcher] placeholder – not yet implemented")
