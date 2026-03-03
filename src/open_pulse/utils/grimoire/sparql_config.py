"""SPARQL-based GrimoireLab configuration generator.

Queries Neo4j and/or Tentris to discover repositories and produces a
GrimoireLab ``projects.json`` configuration file that can drive
Perceval data-collection runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console

_console = Console(stderr=True)

PLACEHOLDER_SPARQL = """\
PREFIX schema: <http://schema.org/>
SELECT ?repo ?name WHERE {
  ?repo a schema:SoftwareSourceCode ;
        schema:name ?name .
}
LIMIT 100
"""


def generate_config(
    *,
    neo4j_endpoint: str = "bolt://localhost:7687",
    tentris_endpoint: str = "http://localhost:7502/sparql",
    output: Path = Path("projects.json"),
) -> Path:
    """Query knowledge-graph endpoints and write a GrimoireLab config file.

    Currently executes a **placeholder** SPARQL query and writes a
    skeleton ``projects.json``.  Replace the query and the result
    mapping once the upstream graph schema is finalised.

    Returns the path to the generated file.
    """
    _console.print(
        f"[bold]SPARQL config generator[/bold] "
        f"(neo4j={neo4j_endpoint}, tentris={tentris_endpoint})"
    )
    _console.print(f"[dim]Query:[/dim]\n{PLACEHOLDER_SPARQL.strip()}")

    repos = _run_placeholder_query()

    config = _build_projects_json(repos)
    output.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    _console.print(f"[green]Wrote[/green] {output}  ({len(repos)} repo(s))")
    return output


def _run_placeholder_query() -> list[dict[str, str]]:
    """Simulate SPARQL query results.

    In production this will issue HTTP requests to the Tentris SPARQL
    endpoint and/or Bolt queries to Neo4j.  For now we return an empty
    list so the rest of the pipeline can be exercised end-to-end.
    """
    _console.print(
        "[yellow]Warning:[/yellow] using placeholder query — "
        "no real SPARQL endpoint contacted."
    )
    return []


def _build_projects_json(repos: list[dict[str, str]]) -> dict[str, Any]:
    """Build a minimal GrimoireLab ``projects.json`` structure."""
    projects: dict[str, Any] = {}
    for repo in repos:
        name = repo.get("name", "unknown")
        url = repo.get("repo", "")
        projects[name] = {"git": [url]}
    return projects
