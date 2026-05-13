"""SPARQL → projects.json → applier glue.

The CLI ``open-pulse services grimoire apply`` and the projects-ui dashboard
share the same transformation: query a SPARQL endpoint for the list of
``schema:SoftwareSourceCode`` repos, build a GrimoireLab ``projects.json``,
post it to the applier sidecar (which writes the file and restarts Mordred).

This module isolates the three steps so they're independently testable.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

DEFAULT_QUERY = """\
PREFIX schema: <http://schema.org/>
SELECT ?repo WHERE {
  ?repo a schema:SoftwareSourceCode .
}
ORDER BY ?repo
"""


def _slugify(s: str) -> str:
    """Title → ``open_pulse_sparql``-style slug."""
    s = s.strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    return s or "open_pulse_sparql"


def query_sparql_for_repos(
    sparql_endpoint: str,
    *,
    auth: tuple[str, str] | None = None,
    query: str = DEFAULT_QUERY,
    timeout: float = 30.0,
    client: httpx.Client | None = None,
) -> list[str]:
    """Run ``query`` against ``sparql_endpoint`` and return the repo URLs.

    The query must bind a ``?repo`` variable to a URL. Other bindings are
    ignored. Duplicates are removed; results are sorted lexically.

    Pass ``client`` (with a ``MockTransport``) from tests; production
    callers leave it ``None`` and a fresh client is created per call.
    """
    url = sparql_endpoint.rstrip("/")
    if not url.endswith("/query"):
        # Allow callers to pass either the base URL or the full /query path.
        url = url + "/query"

    headers = {"Accept": "application/sparql-results+json"}
    params = {"query": query}
    request_kwargs: dict[str, Any] = {"params": params, "headers": headers}
    if auth is not None:
        request_kwargs["auth"] = httpx.BasicAuth(*auth)

    if client is None:
        with httpx.Client(timeout=timeout) as fresh:
            resp = fresh.get(url, **request_kwargs)
    else:
        resp = client.get(url, **request_kwargs)

    if resp.status_code != 200:
        raise RuntimeError(
            f"SPARQL query failed: HTTP {resp.status_code} on {url} — {resp.text[:200]}"
        )
    body = resp.json()
    bindings = (body.get("results") or {}).get("bindings") or []

    repos: set[str] = set()
    for row in bindings:
        repo_cell = row.get("repo") or {}
        value = repo_cell.get("value")
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            repos.add(value)
    return sorted(repos)


def build_projects_json(
    repos: list[str],
    group_title: str = "Open Pulse SPARQL",
) -> dict[str, Any]:
    """Wrap ``repos`` in the GrimoireLab ``projects.json`` envelope."""
    slug = _slugify(group_title)
    return {
        slug: {
            "meta": {"title": group_title},
            "git": list(repos),
        }
    }


def post_to_applier(
    applier_url: str,
    bearer_token: str,
    payload: dict[str, Any],
    *,
    timeout: float = 60.0,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """POST a projects.json payload to the applier sidecar."""
    url = applier_url.rstrip("/") + "/apply"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
    }
    if client is None:
        with httpx.Client(timeout=timeout) as fresh:
            resp = fresh.post(url, json=payload, headers=headers)
    else:
        resp = client.post(url, json=payload, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(
            f"applier POST failed: HTTP {resp.status_code} on {url} — {resp.text[:200]}"
        )
    return resp.json()
