"""Cronjob installer for GrimoireLab config watcher.

Installs a cron entry (Linux/macOS) that periodically pulls a git
repository and checks whether a configuration file has changed.

On Windows the function prints an error and exits -- ``schtasks``
support is not yet implemented.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from rich.console import Console

_console = Console(stderr=True)

_DEFAULT_SCHEDULE = "*/30 * * * *"  # every 30 minutes


def install_watcher(
    *,
    repo_url: str,
    config_path: str = "projects.json",
    branch: str = "main",
    schedule: str = _DEFAULT_SCHEDULE,
    clone_dir: Path | None = None,
) -> None:
    """Register a cron job that watches *repo_url* for config changes.

    Parameters
    ----------
    repo_url:
        Git remote URL to clone / pull.
    config_path:
        Relative path inside the repo to the config file to watch.
    branch:
        Git branch to track.
    schedule:
        Cron schedule expression (default: every 30 minutes).
    clone_dir:
        Local directory to clone into.  Defaults to
        ``~/.open-pulse/grimoire-watcher``.
    """
    if platform.system() == "Windows":
        _console.print(
            "[red bold]Error:[/red bold] Cron is not available on Windows.\n"
            "Use Task Scheduler (schtasks) manually, or run inside WSL."
        )
        raise SystemExit(1)

    if clone_dir is None:
        clone_dir = Path.home() / ".open-pulse" / "grimoire-watcher"

    script = _build_watcher_script(
        repo_url=repo_url,
        config_path=config_path,
        branch=branch,
        clone_dir=clone_dir,
    )

    script_path = clone_dir / "watch.sh"
    clone_dir.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o755)

    cron_line = f"{schedule} {script_path}"
    _install_cron_entry(cron_line)

    _console.print(
        f"[green]Installed[/green] watcher cron job.\n"
        f"  Schedule : {schedule}\n"
        f"  Script   : {script_path}\n"
        f"  Repo     : {repo_url}\n"
        f"  Config   : {config_path}  (branch: {branch})"
    )


def _build_watcher_script(
    *,
    repo_url: str,
    config_path: str,
    branch: str,
    clone_dir: Path,
) -> str:
    """Return the shell script that the cron job will execute."""
    repo_dir = (clone_dir / "repo").as_posix()
    return f"""\
#!/usr/bin/env bash
set -eu

REPO_DIR="{repo_dir}"

if [ ! -d "$REPO_DIR/.git" ]; then
    git clone --branch {branch} --single-branch {repo_url} "$REPO_DIR"
fi

cd "$REPO_DIR"
OLD_HASH=$(git rev-parse HEAD:{config_path} 2>/dev/null || echo "none")
git pull --ff-only origin {branch}
NEW_HASH=$(git rev-parse HEAD:{config_path} 2>/dev/null || echo "none")

if [ "$OLD_HASH" != "$NEW_HASH" ]; then
    echo "[open-pulse watcher] Config changed: {config_path}"
    # Placeholder: add notification or reload logic here
fi
"""


def _install_cron_entry(cron_line: str) -> None:
    """Append *cron_line* to the current user's crontab if not already present."""
    try:
        existing = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except FileNotFoundError:
        _console.print(
            "[red bold]Error:[/red bold] crontab command not found."
        )
        raise SystemExit(1) from None

    if cron_line in existing:
        _console.print("[yellow]Cron entry already present — skipping.[/yellow]")
        return

    new_crontab = existing.rstrip("\n") + "\n" + cron_line + "\n"
    proc = subprocess.run(
        ["crontab", "-"],
        input=new_crontab,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        _console.print(
            f"[red bold]Error:[/red bold] failed to install crontab entry.\n{proc.stderr}"
        )
        raise SystemExit(1)
