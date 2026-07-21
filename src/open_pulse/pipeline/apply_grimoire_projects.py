"""Owner-grouped projects.json — pipeline step + reusable builder.

Pulls every ``Repo`` node from Neo4j (the c4dt crawl populates this), groups
the repos by their ``owner`` property (the GitHub user/org that owns each
repo), wraps the result in a GrimoireLab ``projects.json`` envelope, and
posts it to the projects-applier sidecar.

The output is "generic" in the sense Carlos meant — one group per owner,
no curation. It's the natural slice for tracking everything reachable from
a multi-org crawl. Used both as the last step of a quest and via
``POST /api/projects/build-by-owner`` from the hub.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from open_pulse.services.container import ServiceContainer

logger = logging.getLogger(__name__)


def _slug(s: str) -> str:
    """projects.json keys must look like Python identifiers — lower-case
    alphanum + underscores. We lossily flatten dashes / dots / spaces here."""
    s = re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")
    return s or "group"


def build_owner_grouped_projects(
    *,
    neo4j_endpoint: str,
    neo4j_auth: tuple[str, str],
    include_unexplored: bool = False,
    min_repos_per_owner: int = 1,
    title_prefix: str = "",
) -> tuple[dict[str, Any], int]:
    """Run the Cypher query, return ``(projects_json, repo_count)``.

    ``include_unexplored=False`` filters to repos the BFS actually visited
    (``r.is_explored = true`` — the post-c4dt-crawl number was 2,369 explored
    out of 19,890 total Repo nodes). ``min_repos_per_owner`` drops owners
    too small to be interesting; default 1 keeps everyone.
    """
    from neo4j import GraphDatabase

    where = "" if include_unexplored else "WHERE r.is_explored = true"
    cypher = (
        f"MATCH (r:Repo) {where} RETURN r.owner AS owner, collect(r.full_name) AS repos"
    )

    driver = GraphDatabase.driver(neo4j_endpoint, auth=neo4j_auth)
    try:
        with driver.session() as session:
            rows = [(r["owner"], list(r["repos"] or [])) for r in session.run(cypher)]
    finally:
        driver.close()

    projects: dict[str, Any] = {}
    total_repos = 0
    for owner, names in rows:
        if not owner:
            continue
        urls = sorted({f"https://github.com/{n}" for n in names if n})
        if len(urls) < min_repos_per_owner:
            continue
        projects[_slug(owner)] = {
            "meta": {"title": f"{title_prefix}{owner}" if title_prefix else owner},
            "git": urls,
        }
        total_repos += len(urls)
    return projects, total_repos


def post_to_applier(
    *,
    applier_url: str,
    bearer_token: str,
    payload: dict[str, Any],
    timeout: float = 60.0,
) -> dict[str, Any]:
    """POST a built projects.json envelope to the applier sidecar."""
    url = applier_url.rstrip("/") + "/apply"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=timeout) as c:
        resp = c.post(url, json=payload, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(
            f"applier returned HTTP {resp.status_code}: {resp.text[:200]}"
        )
    return resp.json()


def fetch_current_projects(
    *, applier_url: str, bearer_token: str, timeout: float = 60.0
) -> dict[str, Any]:
    """GET the applier's current projects.json (so we can merge, not clobber)."""
    url = applier_url.rstrip("/") + "/current"
    with httpx.Client(timeout=timeout) as c:
        resp = c.get(url, headers={"Authorization": f"Bearer {bearer_token}"})
    if resp.status_code != 200:
        raise RuntimeError(
            f"applier /current returned HTTP {resp.status_code}: {resp.text[:200]}"
        )
    return resp.json()


def _sparql_select_repo_urls(
    sparql_endpoint: str, query: str, timeout: float = 600.0
) -> list[str]:
    """Run a SPARQL SELECT returning a single ``?repo`` column of GitHub URLs."""
    import urllib.parse
    import urllib.request

    url = sparql_endpoint.rstrip("/") + "/query?query=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={"Accept": "text/csv"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        lines = r.read().decode("utf-8", "replace").splitlines()
    out: list[str] = []
    for ln in lines[1:]:  # skip the CSV header row
        v = ln.strip().strip('"')
        if v.startswith("https://github.com/"):
            out.append(v)
    return out


def _apply_single_group(
    services: ServiceContainer, step_cfg: dict[str, Any], params: dict[str, Any]
) -> None:
    """Build ONE named group from a SPARQL repo-filter and MERGE it into the
    live projects.json (preserving every other group). Config under ``params``:

    * ``group_slug``  — projects.json key (slugified). e.g. ``EPFL`` -> ``epfl``.
    * ``group_title`` — display title (default = group_slug).
    * ``sparql_query``— SELECT returning one ``?repo`` column of GitHub URLs.
    * ``merge``       — merge into /current (default true) vs replace-all (false).
    """
    slug = _slug(str(params.get("group_slug") or params.get("group_name") or "group"))
    title = str(params.get("group_title") or params.get("group_slug") or slug)
    query = params.get("sparql_query")
    literal = params.get("repo_urls")  # optional materialized list (union'd with the query)
    if not query and not literal:
        raise RuntimeError(
            "apply_grimoire_projects[single_group]: need 'sparql_query' and/or 'repo_urls'."
        )
    merge = bool(params.get("merge", True))

    repos_set: set[str] = set()
    if query:
        repos_set.update(_sparql_select_repo_urls(services.sparql_store.endpoint, str(query)))
    if literal:
        repos_set.update(u for u in literal if str(u).startswith("https://github.com/"))
    repos = sorted(repos_set)
    group = {"meta": {"title": title}, "git": repos}

    applier_url = str(
        step_cfg.get("applier_url")
        or os.environ.get("APPLIER_URL")
        or "http://projects-applier:8000"
    )
    auth_env = str(step_cfg.get("applier_auth_env", "APPLIER_AUTH"))
    bearer = os.environ.get(auth_env, "").strip()
    if not bearer:
        raise RuntimeError(
            f"apply_grimoire_projects[single_group]: env var {auth_env!r} is empty."
        )

    if merge:
        payload = fetch_current_projects(applier_url=applier_url, bearer_token=bearer)
        payload[slug] = group  # add or replace only this one group
    else:
        payload = {slug: group}

    result = post_to_applier(applier_url=applier_url, bearer_token=bearer, payload=payload)
    logger.info(
        "apply_grimoire_projects[single_group]: group=%s repos=%d merge=%s -> %d total groups (%s)",
        slug, len(repos), merge, len(payload), result.get("status", ""),
    )


# ── Pipeline step ──────────────────────────────────────────────────────────


def _services_from_context(context: dict[str, object]) -> ServiceContainer:
    services = context.get("services")
    if not isinstance(services, ServiceContainer):
        raise RuntimeError(
            "Pipeline context missing ServiceContainer under 'services'."
        )
    return services


def run_apply_grimoire_projects(context: dict[str, object]) -> None:
    """Pipeline step: build owner-grouped projects.json, POST to applier.

    Step config keys (all optional):

    * ``include_unexplored`` (bool, default false)
    * ``min_repos_per_owner`` (int, default 1)
    * ``title_prefix`` (str, default "")
    * ``applier_url`` (str, default ``http://projects-applier:8000``)
    * ``applier_auth_env`` (str, default ``APPLIER_AUTH``)

    Reads the Neo4j endpoint and credentials from the run-scoped
    ``ServiceContainer`` so the step inherits whatever the rest of the
    quest is using (compose-network DNS or localhost depending on
    ``OPEN_PULSE_RUNNING_IN_CLI_CONTAINER``).
    """
    services = _services_from_context(context)
    step_cfg = context.get("step_config") or {}
    if not isinstance(step_cfg, dict):
        raise RuntimeError("Pipeline context 'step_config' must be a dict.")

    # Single named group built from a SPARQL repo-filter, MERGED into the live
    # projects.json (does not overwrite). Opt-in via
    # ``params: {mode: single_group, group_slug, group_title, sparql_query, merge}``.
    if (step_cfg.get("params") or {}).get("mode") == "single_group":
        _apply_single_group(services, step_cfg, step_cfg["params"])
        return

    auth_env = services.neo4j.auth_env or "NEO4J_AUTH"
    raw = os.environ.get(auth_env, "")
    if "/" not in raw:
        raise RuntimeError(
            f"apply_grimoire_projects: {auth_env!r} is unset or malformed "
            "(expected 'username/password')."
        )
    user, password = raw.split("/", 1)

    include_unexplored = bool(step_cfg.get("include_unexplored", False))
    min_repos_per_owner = int(step_cfg.get("min_repos_per_owner", 1) or 1)
    title_prefix = str(step_cfg.get("title_prefix", "") or "")

    projects, total = build_owner_grouped_projects(
        neo4j_endpoint=services.neo4j.endpoint,
        neo4j_auth=(user, password),
        include_unexplored=include_unexplored,
        min_repos_per_owner=min_repos_per_owner,
        title_prefix=title_prefix,
    )

    if not projects:
        logger.warning(
            "apply_grimoire_projects: no owners matched (include_unexplored=%s, "
            "min_repos_per_owner=%d) — skipping applier POST",
            include_unexplored,
            min_repos_per_owner,
        )
        return

    applier_url = str(
        step_cfg.get("applier_url")
        or os.environ.get("APPLIER_URL")
        or "http://projects-applier:8000"
    )
    auth_env = str(step_cfg.get("applier_auth_env", "APPLIER_AUTH"))
    bearer = os.environ.get(auth_env, "").strip()
    if not bearer:
        raise RuntimeError(
            f"apply_grimoire_projects: env var {auth_env!r} is empty; "
            "set APPLIER_AUTH (matching the projects-applier sidecar's bearer)."
        )

    result = post_to_applier(
        applier_url=applier_url,
        bearer_token=bearer,
        payload=projects,
    )
    logger.info(
        "apply_grimoire_projects: applied %d owners · %d repos · groups=%s",
        len(projects),
        total,
        result.get("groups", []),
    )
