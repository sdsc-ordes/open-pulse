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
    "grimoirelab",
    "crawler",
    "extractor",
    "sparql",
    "hub",
    "orchestration",
)
_PROFILE_DESCRIPTIONS = {
    "default": "Core services only (Neo4j)",
    "grimoirelab": "Core + GrimoireLab DB & worker",
    "crawler": "Core + Open Pulse Crawler API",
    "extractor": "Core + GME metadata extractor + Selenium",
    "sparql": "Core + Oxigraph SPARQL store + sparql-proxy",
    "hub": "Core + Open Pulse Hub dashboard (port 9090)",
    "orchestration": "Core + Portainer management UI",
}

console = Console(stderr=True)
app = typer.Typer(help="Deploy Docker infrastructure.")

_COMPOSE_DIR = Path("infra/open-pulse-stack")
_BASE_COMPOSE_FILE = _COMPOSE_DIR / "docker-compose.yml"
_CLI_COMPOSE_FILE = _COMPOSE_DIR / "docker-compose.cli.yml"
_GRIMOIRE_COMPOSE_FILE = _COMPOSE_DIR / "docker-compose.grimoirelab.yml"


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


# Env model:
#   - <repo-root>/infra/.env — the deployment env. Owns image refs, ports,
#                              resource limits, storage paths, ALL container-
#                              side credentials and per-service knobs. This is
#                              the SOLE file compose loads when bringing local
#                              infra up.
#   - <repo-root>/.env       — the open-pulse tool/client env. Only relevant
#                              when running the open-pulse CLI / hub against
#                              EXTERNAL infrastructure (i.e. not the local
#                              compose stack). Compose never loads it.
# This separation matches the principle: when launching from infra, all env
# lives in infra; otherwise <repo>/.env is just for the open-pulse tool.
_INFRA_ENV_REL = Path("infra") / ".env"
_INFRA_TEMPLATE_REL = Path("infra") / ".env.example"


def _ensure_env_file(project_root: Path, env_file: Path | None) -> Path:
    """Compatibility shim for callers that still expect a single env file."""
    files = _ensure_env_files(project_root, env_file)
    return files[0] if files else project_root / _INFRA_ENV_REL


def _ensure_env_files(project_root: Path, override: Path | None) -> list[Path]:
    """Return the env files compose should load, seeding from templates.

    If ``override`` is given it replaces the convention (single file only).
    Otherwise compose loads only ``infra/.env`` (the deployment env). The
    tool/client ``<repo>/.env`` is consumed by the open-pulse Python CLI/hub
    when running against external infra and is *not* a compose input.
    """
    if override is not None:
        resolved = override if override.is_absolute() else project_root / override
        if not resolved.is_file():
            typer.echo(f"Error: supplied env file does not exist: {resolved}", err=True)
            raise typer.Exit(code=1)
        return [resolved]

    env_path = project_root / _INFRA_ENV_REL
    template = project_root / _INFRA_TEMPLATE_REL
    if not env_path.is_file():
        if not template.is_file():
            typer.echo(
                f"Warning: {_INFRA_ENV_REL.as_posix()} not found and "
                f"{_INFRA_TEMPLATE_REL.as_posix()} is missing. Skipping.",
                err=True,
            )
            return []
        env_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(template, env_path)
        _absolutize_paths(env_path, project_root)
        console.print(
            f"[green]Created[/green] {_INFRA_ENV_REL.as_posix()} from "
            f"{_INFRA_TEMPLATE_REL.as_posix()}"
        )
    return [env_path]


def _absolutize_paths(env_file: Path, project_root: Path) -> None:
    """Rewrite path-shaped placeholders in a freshly-seeded env file.

    Specifically: ``OPEN_PULSE_DATA_DIR=./data`` becomes the absolute
    repo-root data path, and ``OPEN_PULSE_HOST_PATH=`` (empty) is filled
    with the absolute repo root. Avoids the relative-path ambiguity where
    ``./data`` resolves differently between ``op deploy`` and raw
    ``docker compose -f infra/open-pulse-stack/…``.
    """
    abs_root = project_root.as_posix()
    abs_data = (project_root / "data").as_posix()
    text = env_file.read_text(encoding="utf-8")
    new_text = text.replace(
        "OPEN_PULSE_DATA_DIR=./data", f"OPEN_PULSE_DATA_DIR={abs_data}"
    )
    new_text = new_text.replace(
        "OPEN_PULSE_HOST_PATH=\n", f"OPEN_PULSE_HOST_PATH={abs_root}\n"
    )
    if new_text != text:
        env_file.write_text(new_text, encoding="utf-8")


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


def _compose_base_cmd(
    project_root: Path,
    compose_files: list[Path],
    env_files: list[Path],
) -> list[str]:
    """Build the common ``docker compose`` prefix shared by up/down/ps."""
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
    for ef in env_files:
        if ef.is_file():
            cmd.extend(["--env-file", str(ef)])
    return cmd


def _compose_up(
    project_root: Path,
    profiles: list[str],
    env_files: list[Path],
    compose_files: list[Path],
    extra_args: list[str],
) -> None:
    """Run ``docker compose up -d`` with the given profiles and overrides."""
    cmd = _compose_base_cmd(project_root, compose_files, env_files)

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
            help="Path to a .env file. Defaults to <project-root>/infra/.env (created from infra/.env.example if absent).",
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
    ``infra/open-pulse-stack/docker-compose.yml`` is always included; pass ``--file`` to layer
    additional overrides.
    """
    if not _docker_available():
        console.print(
            "[red bold]Error:[/red bold] Docker is not installed or the daemon is not running.\n"
            "Install Docker Desktop or start the Docker service and try again."
        )
        raise typer.Exit(code=1)

    project_root = _find_project_root()

    env_paths = _ensure_env_files(project_root, env_file)

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

    _compose_up(project_root, profiles, env_paths, compose_files, [])


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

    env_files = [project_root / _INFRA_ENV_REL]
    cmd = _compose_base_cmd(project_root, files, env_files)
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

    env_files = [project_root / _INFRA_ENV_REL]
    cmd = _compose_base_cmd(project_root, files, env_files)
    cmd.extend(["ps", "-a"])
    result = subprocess.run(cmd, cwd=str(project_root))
    raise typer.Exit(code=result.returncode)
