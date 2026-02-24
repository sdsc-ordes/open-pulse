"""Deploy command group -- Docker infrastructure management."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

_PROFILES = ("default", "analysis", "grimoirelab", "orchestration")
_PROFILE_DESCRIPTIONS = {
    "default": "Core services only (Neo4j)",
    "analysis": "Core + analysis notebook",
    "grimoirelab": "Core + GrimoireLab DB & worker",
    "orchestration": "Core + Portainer management UI",
}

console = Console(stderr=True)
app = typer.Typer(help="Deploy Docker infrastructure.")


def _find_project_root() -> Path:
    """Walk up from this file to find the repo root (contains docker-compose.yml)."""
    candidate = Path(__file__).resolve().parent
    for _ in range(10):
        if (candidate / "docker-compose.yml").is_file():
            return candidate
        candidate = candidate.parent
    typer.echo("Error: cannot locate project root (no docker-compose.yml found).", err=True)
    raise typer.Exit(code=1)


def _docker_available() -> bool:
    """Return *True* when the ``docker`` CLI is reachable and the daemon responds."""
    docker = shutil.which("docker")
    if docker is None:
        return False
    try:
        subprocess.run(
            [docker, "info"],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return True


def _ensure_env_file(project_root: Path, env_file: Path | None) -> Path:
    """Return the resolved ``.env`` path, creating one from the template if needed."""
    if env_file is not None:
        resolved = env_file if env_file.is_absolute() else project_root / env_file
        if not resolved.is_file():
            typer.echo(f"Error: supplied env file does not exist: {resolved}", err=True)
            raise typer.Exit(code=1)
        return resolved

    default_env = project_root / ".env"
    if default_env.is_file():
        return default_env

    template = project_root / "infra" / "env" / ".env.example"
    if not template.is_file():
        typer.echo(
            "Warning: no .env file found and infra/env/.env.example is missing. "
            "Proceeding without an env file.",
            err=True,
        )
        return default_env  # docker compose will simply not load it

    shutil.copy2(template, default_env)
    console.print(
        f"[green]Created[/green] .env from {template.relative_to(project_root)}"
    )
    return default_env


def _select_profiles_interactive() -> list[str]:
    """Prompt the user for which Compose profiles to activate."""
    import questionary

    choices = [
        questionary.Choice(
            title=f"{name}  –  {_PROFILE_DESCRIPTIONS[name]}",
            value=name,
            checked=(name == "default"),
        )
        for name in _PROFILES
    ]
    selected: list[str] | None = questionary.checkbox(
        "Select deployment profiles:", choices=choices
    ).ask()

    if selected is None:
        raise typer.Abort()

    if not selected or selected == ["default"]:
        return []

    return [p for p in selected if p != "default"]


def _compose_up(
    project_root: Path,
    profiles: list[str],
    env_file: Path,
    compose_files: list[Path],
    extra_args: list[str],
) -> None:
    """Run ``docker compose up -d`` with the given profiles and overrides."""
    cmd: list[str] = ["docker", "compose"]

    for cf in compose_files:
        cmd.extend(["-f", str(cf)])

    if env_file.is_file():
        cmd.extend(["--env-file", str(env_file)])

    for profile in profiles:
        cmd.extend(["--profile", profile])

    cmd.extend(["up", "-d", *extra_args])

    console.print(f"[bold]Running:[/bold] {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(project_root))
    raise typer.Exit(code=result.returncode)


@app.command()
def up(
    profile: Annotated[
        Optional[list[str]],
        typer.Option(
            "--profile",
            "-p",
            help=(
                "Compose profiles to activate (repeatable). "
                "Omit to select interactively."
            ),
        ),
    ] = None,
    env_file: Annotated[
        Optional[Path],
        typer.Option(
            "--env-file",
            "-e",
            help="Path to a .env file. Defaults to <project-root>/.env (created from template if absent).",
        ),
    ] = None,
    compose_file: Annotated[
        Optional[list[Path]],
        typer.Option(
            "--file",
            "-f",
            help="Extra Compose file(s) to include (repeatable).",
        ),
    ] = None,
) -> None:
    """Deploy services using Docker Compose.

    Without ``--profile`` flags the command opens an interactive selector
    so you can pick which profiles to enable.  The root
    ``docker-compose.yml`` is always included; pass ``--file`` to layer
    additional overrides.
    """
    if not _docker_available():
        console.print(
            "[red bold]Error:[/red bold] Docker is not installed or the daemon is not running.\n"
            "Install Docker Desktop or start the Docker service and try again."
        )
        raise typer.Exit(code=1)

    project_root = _find_project_root()

    env_path = _ensure_env_file(project_root, env_file)

    root_compose = project_root / "docker-compose.yml"
    compose_files: list[Path] = [root_compose]
    if compose_file:
        compose_files.extend(compose_file)

    if profile is not None:
        profiles = [p for p in profile if p != "default"]
    else:
        profiles = _select_profiles_interactive()

    _compose_up(project_root, profiles, env_path, compose_files, [])


@app.command()
def down(
    compose_file: Annotated[
        Optional[list[Path]],
        typer.Option(
            "--file",
            "-f",
            help="Extra Compose file(s) to include (repeatable).",
        ),
    ] = None,
    volumes: Annotated[
        bool,
        typer.Option("--volumes", "-v", help="Remove named volumes declared in the Compose file."),
    ] = False,
) -> None:
    """Tear down deployed services."""
    if not _docker_available():
        console.print(
            "[red bold]Error:[/red bold] Docker is not installed or the daemon is not running."
        )
        raise typer.Exit(code=1)

    project_root = _find_project_root()

    root_compose = project_root / "docker-compose.yml"
    files: list[Path] = [root_compose]
    if compose_file:
        files.extend(compose_file)

    cmd: list[str] = ["docker", "compose"]
    for cf in files:
        cmd.extend(["-f", str(cf)])
    cmd.append("down")
    if volumes:
        cmd.append("--volumes")

    console.print(f"[bold]Running:[/bold] {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(project_root))
    raise typer.Exit(code=result.returncode)


@app.command()
def ps() -> None:
    """Show the status of deployed containers."""
    if not _docker_available():
        console.print(
            "[red bold]Error:[/red bold] Docker is not installed or the daemon is not running."
        )
        raise typer.Exit(code=1)

    project_root = _find_project_root()

    cmd = ["docker", "compose", "-f", str(project_root / "docker-compose.yml"), "ps", "-a"]
    result = subprocess.run(cmd, cwd=str(project_root))
    raise typer.Exit(code=result.returncode)
