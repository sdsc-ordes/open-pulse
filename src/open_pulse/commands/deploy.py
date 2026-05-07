"""Deploy command group -- Docker infrastructure management."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

_PROFILES = (
    "default",
    "analysis",
    "grimoirelab",
    "crawler",
    "extractor",
    "sparql",
    "hub",
    "orchestration",
)
_PROFILE_DESCRIPTIONS = {
    "default": "Core services only (Neo4j)",
    "analysis": "Core + analysis notebook",
    "grimoirelab": "Core + GrimoireLab DB & worker",
    "crawler": "Core + Open Pulse Crawler API",
    "extractor": "Core + GME metadata extractor + Selenium",
    "sparql": "Core + Oxigraph SPARQL store + sparql-proxy",
    "hub": "Core + Open Pulse Hub dashboard (port 9090)",
    "orchestration": "Core + Portainer management UI",
}

console = Console(stderr=True)
app = typer.Typer(help="Deploy Docker infrastructure.")

_COMPOSE_DIR = Path("infra/compose")
_BASE_COMPOSE_FILE = _COMPOSE_DIR / "docker-compose.yml"
_CLI_COMPOSE_FILE = _COMPOSE_DIR / "docker-compose.cli.yml"
_GRIMOIRE_COMPOSE_FILE = Path("infra/services/grimoirelab/docker-compose.yml")


def _find_project_root() -> Path:
    """Locate the repo root (the directory containing the compose files).

    Resolution order:
      1. ``$OPEN_PULSE_PROJECT_ROOT`` if set and valid (explicit override).
      2. ``$OPEN_PULSE_HOST_PATH`` if set and valid (the cli container's bind
         mount points here, identity-mapped from the host).
      3. The current working directory and its ancestors.
      4. Ancestors of this source file (the develop/editable install case).
    """
    candidates: list[Path] = []

    for var in ("OPEN_PULSE_PROJECT_ROOT", "OPEN_PULSE_HOST_PATH"):
        env_val = os.environ.get(var)
        if env_val:
            candidates.append(Path(env_val))

    cwd = Path.cwd()
    candidates.extend([cwd, *cwd.parents])
    candidates.extend(Path(__file__).resolve().parents)

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / _BASE_COMPOSE_FILE).is_file():
            return resolved

    typer.echo(
        f"Error: cannot locate project root (no {_BASE_COMPOSE_FILE} found "
        f"under cwd, OPEN_PULSE_HOST_PATH, or this package's parent dirs).",
        err=True,
    )
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


def _running_inside_cli_container() -> bool:
    """Detect whether the CLI is running inside the open-pulse-cli container.

    The compose overlay sets ``OPEN_PULSE_RUNNING_IN_CLI_CONTAINER=1`` so nested
    invocations auto-include ``docker-compose.cli.yml`` and avoid the spurious
    "orphan container" warning compose otherwise prints.
    """
    return os.environ.get("OPEN_PULSE_RUNNING_IN_CLI_CONTAINER") == "1"


def _assemble_compose_files(
    project_root: Path,
    *,
    with_cli: bool,
    with_grimoire: bool,
    extra: list[Path] | None,
) -> list[Path]:
    """Build the ordered list of ``-f`` files for a docker compose invocation.

    The base file is always included. ``--with-cli`` adds the CLI overlay; the
    overlay is also added implicitly when running *inside* the CLI container.
    ``--with-grimoire`` adds the standalone grimoirelab compose so the whole
    behemoth (main stack + grimoirelab) can be brought up in one go.
    """
    files: list[Path] = [project_root / _BASE_COMPOSE_FILE]
    if with_cli or _running_inside_cli_container():
        cli_path = project_root / _CLI_COMPOSE_FILE
        if cli_path not in files:
            files.append(cli_path)
    if with_grimoire:
        files.append(project_root / _GRIMOIRE_COMPOSE_FILE)
    if extra:
        files.extend(extra)
    return files


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
    cmd: list[str] = [
        "docker",
        "compose",
        "--project-name",
        "open-pulse",
        "--project-directory",
        str(project_root),
    ]

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
    with_cli: Annotated[
        bool,
        typer.Option(
            "--with-cli",
            help=f"Include {_CLI_COMPOSE_FILE} to run the CLI container.",
        ),
    ] = False,
    with_grimoire: Annotated[
        bool,
        typer.Option(
            "--with-grimoire",
            help=f"Also bring up the grimoirelab stack ({_GRIMOIRE_COMPOSE_FILE}).",
        ),
    ] = False,
) -> None:
    """Deploy services using Docker Compose.

    Without ``--profile`` flags the command opens an interactive selector
    so you can pick which profiles to enable.  The base compose file
    ``infra/compose/docker-compose.yml`` is always included; pass ``--file`` to layer
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

    compose_files = _assemble_compose_files(
        project_root,
        with_cli=with_cli,
        with_grimoire=with_grimoire,
        extra=compose_file,
    )

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
        typer.Option(
            "--volumes", "-v", help="Remove named volumes declared in the Compose file."
        ),
    ] = False,
    with_cli: Annotated[
        bool,
        typer.Option(
            "--with-cli",
            help=f"Include {_CLI_COMPOSE_FILE} when tearing down the stack.",
        ),
    ] = False,
    with_grimoire: Annotated[
        bool,
        typer.Option(
            "--with-grimoire",
            help=f"Also tear down the grimoirelab stack ({_GRIMOIRE_COMPOSE_FILE}).",
        ),
    ] = False,
) -> None:
    """Tear down deployed services."""
    if not _docker_available():
        console.print(
            "[red bold]Error:[/red bold] Docker is not installed or the daemon is not running."
        )
        raise typer.Exit(code=1)

    project_root = _find_project_root()

    files = _assemble_compose_files(
        project_root,
        with_cli=with_cli,
        with_grimoire=with_grimoire,
        extra=compose_file,
    )

    cmd: list[str] = [
        "docker",
        "compose",
        "--project-name",
        "open-pulse",
        "--project-directory",
        str(project_root),
    ]
    for cf in files:
        cmd.extend(["-f", str(cf)])
    default_env = project_root / ".env"
    if default_env.is_file():
        cmd.extend(["--env-file", str(default_env)])
    cmd.append("down")
    if volumes:
        cmd.append("--volumes")

    console.print(f"[bold]Running:[/bold] {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(project_root))
    raise typer.Exit(code=result.returncode)


@app.command()
def ps(
    with_cli: Annotated[
        bool,
        typer.Option(
            "--with-cli",
            help=f"Include {_CLI_COMPOSE_FILE} when listing containers.",
        ),
    ] = False,
    with_grimoire: Annotated[
        bool,
        typer.Option(
            "--with-grimoire",
            help=f"Also list grimoirelab containers ({_GRIMOIRE_COMPOSE_FILE}).",
        ),
    ] = False,
) -> None:
    """Show the status of deployed containers."""
    if not _docker_available():
        console.print(
            "[red bold]Error:[/red bold] Docker is not installed or the daemon is not running."
        )
        raise typer.Exit(code=1)

    project_root = _find_project_root()

    files = _assemble_compose_files(
        project_root,
        with_cli=with_cli,
        with_grimoire=with_grimoire,
        extra=None,
    )

    cmd: list[str] = [
        "docker",
        "compose",
        "--project-name",
        "open-pulse",
        "--project-directory",
        str(project_root),
    ]
    for cf in files:
        cmd.extend(["-f", str(cf)])
    default_env = project_root / ".env"
    if default_env.is_file():
        cmd.extend(["--env-file", str(default_env)])
    cmd.extend(["ps", "-a"])
    result = subprocess.run(cmd, cwd=str(project_root))
    raise typer.Exit(code=result.returncode)
