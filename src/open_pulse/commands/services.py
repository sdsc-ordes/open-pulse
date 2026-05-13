"""Services command group -- service-oriented tooling."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

console = Console()

app = typer.Typer(help="Service-oriented commands.")
grimoire_app = typer.Typer(help="Grimoire services commands.")
app.add_typer(grimoire_app, name="grimoire")


def _split_user_pass(env_value: str) -> tuple[str, str] | None:
    if "/" not in env_value:
        return None
    user, password = env_value.split("/", 1)
    return user, password


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


@grimoire_app.command(name="apply")
def apply_projects(
    sparql_endpoint: Annotated[
        str,
        typer.Option("--sparql", help="SPARQL store base URL or /query endpoint."),
    ] = "http://localhost:7878",
    sparql_auth_env: Annotated[
        str,
        typer.Option(
            "--sparql-auth-env",
            help=(
                "Env var holding 'user/password' for SPARQL Basic Auth. "
                "Leave unset for anonymous reads."
            ),
        ),
    ] = "SPARQL_AUTH",
    query: Annotated[
        Optional[str],
        typer.Option(
            "--query",
            help=(
                "SPARQL query string. Must bind ?repo. "
                "Defaults to all schema:SoftwareSourceCode resources."
            ),
        ),
    ] = None,
    query_file: Annotated[
        Optional[Path],
        typer.Option(
            "--query-file",
            help="Read the SPARQL query from a file instead of --query.",
            exists=True,
        ),
    ] = None,
    group_title: Annotated[
        str,
        typer.Option(
            "--group-title",
            help="Title for the projects.json top-level group.",
        ),
    ] = "Open Pulse SPARQL",
    applier_url: Annotated[
        str,
        typer.Option(
            "--applier",
            help="Applier sidecar base URL.",
        ),
    ] = "http://localhost:1235",
    applier_token_env: Annotated[
        str,
        typer.Option(
            "--applier-token-env",
            help="Env var holding the bearer token for the applier.",
        ),
    ] = "APPLIER_AUTH",
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Print the generated projects.json without sending it.",
        ),
    ] = False,
    output: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            "-o",
            help="Also write the generated projects.json to this path.",
        ),
    ] = None,
) -> None:
    """Query the SPARQL store, build a projects.json, push to the applier.

    Same flow the dashboard UI uses, packaged as a CLI for cron / CI runs.
    Writes the new ``projects.json`` to the Mordred volume and triggers a
    container restart so Mordred picks up the new project list.
    """
    from open_pulse.utils.grimoire.applier_client import (
        DEFAULT_QUERY,
        build_projects_json,
        post_to_applier,
        query_sparql_for_repos,
    )

    if query is not None and query_file is not None:
        console.print("[red]Pass either --query or --query-file, not both.[/red]")
        raise typer.Exit(code=2)
    if query_file is not None:
        query_text = query_file.read_text(encoding="utf-8")
    elif query is not None:
        query_text = query
    else:
        query_text = DEFAULT_QUERY

    auth_raw = os.environ.get(sparql_auth_env, "")
    auth = _split_user_pass(auth_raw) if auth_raw else None

    console.print(f"[bold]Querying[/bold] {sparql_endpoint}")
    repos = query_sparql_for_repos(sparql_endpoint, auth=auth, query=query_text)
    console.print(f"[green]✓[/green] {len(repos)} repo(s) returned")

    payload = build_projects_json(repos, group_title=group_title)

    if output is not None:
        output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        console.print(f"[green]✓[/green] wrote {output}")

    if dry_run:
        console.print("[yellow]--dry-run: not posting to applier.[/yellow]")
        console.print(json.dumps(payload, indent=2))
        return

    token = os.environ.get(applier_token_env, "")
    if not token:
        console.print(
            f"[red]{applier_token_env} is not set; "
            "use --dry-run to skip the applier or export the token.[/red]"
        )
        raise typer.Exit(code=1)

    console.print(f"[bold]Applying[/bold] to {applier_url}")
    response = post_to_applier(applier_url, token, payload)
    console.print(f"[green]✓[/green] applier response: {response}")


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
