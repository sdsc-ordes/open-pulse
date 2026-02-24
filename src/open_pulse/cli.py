"""Command line interface for open-pulse."""

from __future__ import annotations

import typer

from open_pulse import __version__
from open_pulse.commands import deploy, grimoire, quest
from open_pulse.commands.health import check

app = typer.Typer(name="open-pulse", help="Open Pulse CLI.")

app.add_typer(deploy.app, name="deploy")
app.add_typer(quest.app, name="quest")
app.add_typer(grimoire.app, name="grimoire")
app.command(name="health")(check)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _callback(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print the open-pulse package version.",
    ),
) -> None:
    """Open Pulse CLI."""


def main() -> None:
    app()


if __name__ == "__main__":
    main()
