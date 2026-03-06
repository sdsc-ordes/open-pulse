"""GUI command group -- interactive UIs."""

from __future__ import annotations

import typer
from rich.console import Console

console = Console(stderr=True)
app = typer.Typer(help="Interactive UI commands.")


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
