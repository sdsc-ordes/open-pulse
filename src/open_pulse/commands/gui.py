"""GUI command group -- interactive UIs."""

from __future__ import annotations

import typer
from rich.console import Console

console = Console(stderr=True)
app = typer.Typer(help="Interactive UI commands.")
hub_app = typer.Typer(help="Open Pulse Hub — control-plane dashboard.")
app.add_typer(hub_app, name="hub")


@app.command(name="grimoire")
def grimoire_ui() -> None:
    """Launch the Streamlit Grimoire configuration UI.

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

    from open_pulse.gui.grimoire_streamlit import launch_streamlit

    launch_streamlit()


@hub_app.command(name="serve")
def hub_serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind address."),
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on."),
    reload: bool = typer.Option(False, "--reload", help="Enable hot reload (dev only)."),
) -> None:
    """Run the Open Pulse Hub dashboard.

    Single-password gate via ``HUB_AUTH``. Requires the ``hub`` optional
    dependency group (``pip install open-pulse[hub]``) and a docker socket
    bind-mounted at ``/var/run/docker.sock`` for service control.
    """
    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red bold]Error:[/red bold] uvicorn is not installed.\n"
            "Install the hub extra:  "
            "[bold]pip install open-pulse\\[hub][/bold]"
        )
        raise typer.Exit(code=1) from None

    uvicorn.run(
        "open_pulse.gui.hub.main:app",
        host=host,
        port=port,
        reload=reload,
        access_log=True,
    )
