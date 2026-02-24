"""Health command -- service health checks."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Annotated
from urllib.error import URLError
from urllib.request import Request, urlopen

import typer
from rich.console import Console
from rich.table import Table

console = Console()

_CONNECT_TIMEOUT = 5


def _find_project_root() -> Path | None:
    """Walk up from this file to find the repo root (contains docker-compose.yml)."""
    candidate = Path(__file__).resolve().parent
    for _ in range(10):
        if (candidate / "docker-compose.yml").is_file():
            return candidate
        candidate = candidate.parent
    return None


def _docker_available() -> bool:
    """Return *True* when the ``docker`` CLI is reachable and the daemon responds."""
    docker = shutil.which("docker")
    if docker is None:
        return False
    try:
        subprocess.run([docker, "info"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return True


def _get_container_statuses(project_root: Path) -> list[dict[str, str]]:
    """Query ``docker compose ps`` and return per-container info dicts."""
    cmd = [
        "docker",
        "compose",
        "-f",
        str(project_root / "docker-compose.yml"),
        "ps",
        "-a",
        "--format",
        "json",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(project_root)
        )
        if result.returncode != 0:
            return []
        containers: list[dict[str, str]] = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                containers.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return containers
    except (FileNotFoundError, subprocess.SubprocessError):
        return []


def _probe_http(url: str) -> tuple[bool, str]:
    """Try an HTTP GET against *url*. Returns ``(ok, detail)``."""
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=_CONNECT_TIMEOUT) as resp:  # noqa: S310
            return True, f"HTTP {resp.status}"
    except URLError as exc:
        return False, str(getattr(exc, "reason", exc))
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _probe_tcp(host: str, port: int) -> tuple[bool, str]:
    """Try to open a TCP socket to *host*:*port*. Returns ``(ok, detail)``."""
    try:
        with socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT):
            return True, "connection established"
    except OSError as exc:
        return False, str(exc)


def _parse_host_port(address: str, default_port: int) -> tuple[str, int]:
    """Split ``host:port`` with a fallback *default_port*."""
    if ":" in address:
        host, port_str = address.rsplit(":", 1)
        try:
            return host, int(port_str)
        except ValueError:
            pass
    return address, default_port


def _probe_endpoints(
    neo4j_http: str,
    neo4j_bolt: str,
    tentris: str,
    grimoirelab_db: str,
) -> list[tuple[str, str, bool, str]]:
    """Probe all known service endpoints and return results."""
    bolt_addr = neo4j_bolt.removeprefix("bolt://").removeprefix("neo4j://")
    bolt_host, bolt_port = _parse_host_port(bolt_addr, 7687)

    db_host, db_port = _parse_host_port(grimoirelab_db, 5432)

    results: list[tuple[str, str, bool, str]] = []

    ok, detail = _probe_http(neo4j_http)
    results.append(("Neo4j (HTTP)", neo4j_http, ok, detail))

    ok, detail = _probe_tcp(bolt_host, bolt_port)
    results.append(("Neo4j (Bolt)", neo4j_bolt, ok, detail))

    ok, detail = _probe_http(tentris)
    results.append(("Tentris (SPARQL)", tentris, ok, detail))

    ok, detail = _probe_tcp(db_host, db_port)
    results.append(("GrimoireLab DB", grimoirelab_db, ok, detail))

    return results


def _smoke_tests(
    project_root: Path | None, docker_ok: bool
) -> list[tuple[str, bool, str]]:
    """Run lightweight internal smoke tests."""
    results: list[tuple[str, bool, str]] = []

    try:
        from open_pulse import __version__

        results.append(("CLI version", True, f"v{__version__}"))
    except Exception as exc:  # noqa: BLE001
        results.append(("CLI version", False, str(exc)))

    try:
        from open_pulse.pipeline.config import QuestFileConfig

        QuestFileConfig()
        results.append(("Pipeline config schema", True, "default config validates"))
    except Exception as exc:  # noqa: BLE001
        results.append(("Pipeline config schema", False, str(exc)))

    if docker_ok and project_root is not None:
        compose_file = project_root / "docker-compose.yml"
        try:
            proc = subprocess.run(
                ["docker", "compose", "-f", str(compose_file), "config", "--quiet"],
                capture_output=True,
                text=True,
                cwd=str(project_root),
            )
            if proc.returncode == 0:
                results.append(
                    ("Compose config", True, "docker-compose.yml is valid")
                )
            else:
                msg = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "invalid"
                results.append(("Compose config", False, msg))
        except Exception as exc:  # noqa: BLE001
            results.append(("Compose config", False, str(exc)))

    return results


def _render_container_table(containers: list[dict[str, str]]) -> Table:
    table = Table(title="Docker Containers", show_lines=True)
    table.add_column("Container", style="cyan")
    table.add_column("Service", style="blue")
    table.add_column("State", style="bold")
    table.add_column("Status")
    table.add_column("Ports", style="dim")

    for c in containers:
        name = c.get("Name", c.get("name", "?"))
        service = c.get("Service", c.get("service", "?"))
        state = c.get("State", c.get("state", "?"))
        status = c.get("Status", c.get("status", "?"))
        ports = c.get("Ports", c.get("ports", ""))

        state_style = "green" if state == "running" else "red"
        table.add_row(
            name,
            service,
            f"[{state_style}]{state}[/{state_style}]",
            status,
            ports,
        )

    return table


def _render_endpoint_table(
    results: list[tuple[str, str, bool, str]],
) -> Table:
    table = Table(title="Endpoint Health", show_lines=True)
    table.add_column("Service", style="cyan")
    table.add_column("Endpoint", style="blue")
    table.add_column("Status", style="bold")
    table.add_column("Detail")

    for name, addr, ok, detail in results:
        if ok:
            status = "[green]✓ reachable[/green]"
        else:
            status = "[red]✗ unreachable[/red]"
        table.add_row(name, addr, status, detail)

    return table


def check(
    neo4j: Annotated[
        str,
        typer.Option(help="Neo4j HTTP endpoint to probe."),
    ] = "http://localhost:7474",
    neo4j_bolt: Annotated[
        str,
        typer.Option("--neo4j-bolt", help="Neo4j Bolt endpoint to probe."),
    ] = "bolt://localhost:7687",
    tentris: Annotated[
        str,
        typer.Option(help="Tentris SPARQL endpoint to probe."),
    ] = "http://localhost:7502/sparql",
    grimoirelab_db: Annotated[
        str,
        typer.Option("--grimoirelab-db", help="GrimoireLab PostgreSQL host:port."),
    ] = "localhost:5432",
) -> None:
    """Check the health of all deployed services.

    Verifies Docker daemon reachability, inspects running container states,
    probes service endpoints (Neo4j, Tentris, GrimoireLab DB), and runs
    lightweight smoke tests.  Exits with code 1 when any check fails.
    """
    all_ok = True

    # -- Docker daemon --------------------------------------------------------
    console.print()
    docker_ok = _docker_available()
    if docker_ok:
        console.print("[green]✓[/green] Docker daemon is reachable")
    else:
        console.print("[red]✗[/red] Docker daemon is [bold]not[/bold] reachable")
        all_ok = False

    # -- Container statuses ---------------------------------------------------
    project_root = _find_project_root()
    containers: list[dict[str, str]] = []
    if docker_ok and project_root is not None:
        containers = _get_container_statuses(project_root)

    console.print()
    if containers:
        console.print(_render_container_table(containers))
        for c in containers:
            state = c.get("State", c.get("state", ""))
            if state != "running":
                all_ok = False
    elif docker_ok:
        console.print(
            "[yellow]No containers found.[/yellow] "
            "Run [bold]open-pulse deploy up[/bold] first."
        )

    # -- Endpoint probes ------------------------------------------------------
    endpoint_results = _probe_endpoints(neo4j, neo4j_bolt, tentris, grimoirelab_db)

    console.print()
    console.print(_render_endpoint_table(endpoint_results))

    for _name, _addr, ok, _detail in endpoint_results:
        if not ok:
            all_ok = False

    # -- Smoke tests ----------------------------------------------------------
    smoke_results = _smoke_tests(project_root, docker_ok)

    console.print()
    console.print("[bold]Smoke tests[/bold]")
    for label, passed, detail in smoke_results:
        icon = "[green]✓[/green]" if passed else "[red]✗[/red]"
        console.print(f"  {icon} {label}: {detail}")
        if not passed:
            all_ok = False

    # -- Summary --------------------------------------------------------------
    console.print()
    if all_ok:
        console.print("[bold green]All checks passed.[/bold green]")
    else:
        console.print(
            "[bold yellow]Some checks failed.[/bold yellow] See details above."
        )
        raise typer.Exit(code=1)
