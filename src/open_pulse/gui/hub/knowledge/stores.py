"""Tiny SPARQL + Neo4j helpers used by every resolver.

We don't reach for a heavy abstraction here — each resolver builds its
own query strings — but the network plumbing (timeouts, error
handling, auth) lives once in this module so the resolvers stay
readable.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..auth import get_settings

log = logging.getLogger(__name__)

# Neo4j 5.x emits a notification for every property name in a WHERE
# clause that no node in the database carries. Our defensive OR-filter
# hits five of those per query, so the log fills up fast. The
# notifications are informational only (the query still runs), so we
# silence them at the logger level.
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
logging.getLogger("neo4j").setLevel(logging.ERROR)

_SPARQL_TIMEOUT = 6.0
_NEO4J_TIMEOUT = 6.0


def sparql_select(query: str) -> list[dict[str, Any]]:
    """Run a SPARQL SELECT against the configured store.

    Returns the ``bindings`` list (each row is a ``{var: {"value": ...}}``
    map), or an empty list when the store is unreachable / returns
    a non-200. Errors are logged, never raised — a missing store
    must not block the hub from rendering the rest of the page.
    """
    settings = get_settings()
    url = settings.sparql_url.rstrip("/")
    if not url.endswith("/query"):
        url += "/query"

    auth = None
    if settings.sparql_user or settings.sparql_password:
        auth = (settings.sparql_user, settings.sparql_password)

    try:
        r = httpx.get(
            url,
            params={"query": query},
            headers={"Accept": "application/sparql-results+json"},
            auth=auth,
            timeout=_SPARQL_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        log.warning("sparql request failed: %s", exc)
        return []
    if r.status_code != 200:
        log.warning("sparql HTTP %s: %s", r.status_code, r.text[:200])
        return []
    try:
        body = r.json()
    except ValueError:
        return []
    return list((body.get("results") or {}).get("bindings") or [])


def neo4j_run(
    cypher: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Run a Cypher query and return rows as dicts.

    Importing the neo4j driver is deferred so the hub can boot without
    the extra dep (mostly relevant in unit tests where the driver
    isn't installed). Failures degrade to an empty result.
    """
    settings = get_settings()
    if not settings.neo4j_url:
        return []

    try:
        from neo4j import GraphDatabase  # type: ignore[import-untyped]
    except ImportError:
        log.info("neo4j driver not installed; skipping Cypher lookup")
        return []

    try:
        driver = GraphDatabase.driver(
            settings.neo4j_url,
            auth=(
                (settings.neo4j_user, settings.neo4j_password)
                if settings.neo4j_user
                else None
            ),
            connection_timeout=_NEO4J_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("neo4j driver init failed: %s", exc)
        return []

    try:
        with driver.session() as session:
            result = session.run(cypher, params or {})
            return [dict(record) for record in result]
    except Exception as exc:  # noqa: BLE001
        log.warning("neo4j query failed: %s", exc)
        return []
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
            pass


def sparql_describe(subject_iri: str, limit: int = 200) -> list[dict[str, Any]]:
    """Convenience: every ``?p ?o`` for a subject, capped at ``limit``.

    Used by every resolver as the first probe — if the store has any
    statements about the canonical URL, the entity counts as known.
    """
    query = f"SELECT ?p ?o WHERE {{ <{subject_iri}> ?p ?o }} LIMIT {int(limit)}"
    return sparql_select(query)


# ── Neo4j graph helpers ──────────────────────────────────────────────────

# Pretty-print the relationship names from the crawler's schema.
# Kept in sync with the upload-side edge types in
# ``open_pulse/services/neo4j.py`` so the canvas expand modal renders
# friendly group titles instead of raw uppercase Cypher edge names.
_REL_LABELS: dict[str, str] = {
    # Original crawler edges
    "CONTRIBUTES_TO": "contributor",
    "OWNS": "owner",
    "FORK_OF": "fork of",
    "MEMBER_OF": "member of",
    "DEPENDS_ON": "depends on",
    # PR-8 extra-edges crawler payload
    "FOLLOWS": "follows",
    "STARRED": "starred",
    "WATCHES": "watches",
    "OPENED_ISSUE": "opened issue in",
    "OPENED_PR": "opened PR in",
    "COMMENTED_ON": "commented on",
    "REVIEWED_PR": "reviewed PR in",
}


def neo4j_repo_neighbours(slug: str, *, limit: int = 25) -> list[dict[str, Any]]:
    """1-hop neighbours of a GitHub Repo node, identified by full_name.

    Uses the actual crawler schema: nodes are ``Repo`` / ``User`` /
    ``Org`` with identifiers ``full_name`` / ``login`` / ``login``
    respectively. Returns the neighbour's label, login, relationship
    type, and direction so the renderer can build a clean per-edge
    link to another hub page.
    """
    if not slug or "/" not in slug:
        return []
    cypher = (
        "MATCH (n:Repo {full_name: $slug}) "
        "OPTIONAL MATCH (n)-[r]-(m) "
        "WHERE m IS NOT NULL "
        "RETURN type(r) AS rel, "
        "       startNode(r) = n AS outgoing, "
        "       labels(m)[0] AS kind, "
        "       m.full_name AS full_name, "
        "       m.login AS login, "
        "       m.name AS name "
        "LIMIT $limit"
    )
    return neo4j_run(cypher, {"slug": slug, "limit": limit})


def neo4j_user_or_org_neighbours(
    login: str, *, limit: int = 25
) -> list[dict[str, Any]]:
    """1-hop neighbours of a User or Org node, identified by login."""
    if not login:
        return []
    cypher = (
        "MATCH (n) "
        "WHERE (n:User OR n:Org) AND n.login = $login "
        "OPTIONAL MATCH (n)-[r]-(m) "
        "WHERE m IS NOT NULL "
        "RETURN type(r) AS rel, "
        "       startNode(r) = n AS outgoing, "
        "       labels(m)[0] AS kind, "
        "       m.full_name AS full_name, "
        "       m.login AS login, "
        "       m.name AS name "
        "LIMIT $limit"
    )
    return neo4j_run(cypher, {"login": login, "limit": limit})


def neo4j_rel_label(rel: str) -> str:
    return _REL_LABELS.get(rel, rel.lower().replace("_", " "))


def neo4j_user_org_profile(login: str) -> dict[str, Any] | None:
    """Aggregate stats for a Neo4j User or Org node.

    Returns ``None`` when the login isn't indexed in the graph; that
    lets the github resolver fall back to its bare slug fallback when
    Neo4j has nothing on the visitor's target.
    """
    if not login:
        return None
    cypher = (
        "MATCH (n) WHERE (n:User OR n:Org) AND n.login = $login "
        "OPTIONAL MATCH (n)-[:OWNS]->(r:Repo) "
        "OPTIONAL MATCH (n)-[:MEMBER_OF]->(parent_org:Org) "
        "OPTIONAL MATCH (n)<-[:MEMBER_OF]-(member:User) "
        "OPTIONAL MATCH (n)-[:CONTRIBUTES_TO]->(cr:Repo) "
        "RETURN labels(n)[0] AS kind, "
        "       n.name AS name, "
        "       n.login AS login, "
        "       count(DISTINCT r) AS owned_repos, "
        "       count(DISTINCT parent_org) AS org_memberships, "
        "       count(DISTINCT member) AS members, "
        "       count(DISTINCT cr) AS contributed_to "
        "LIMIT 1"
    )
    rows = neo4j_run(cypher, {"login": login})
    if not rows:
        return None
    row = rows[0]
    return {
        "kind": row.get("kind") or "",
        "name": (row.get("name") or "").strip(),
        "login": row.get("login") or login,
        "owned_repos": int(row.get("owned_repos") or 0),
        "org_memberships": int(row.get("org_memberships") or 0),
        "members": int(row.get("members") or 0),
        "contributed_to": int(row.get("contributed_to") or 0),
    }


def neo4j_repo_community(slugs: list[str], *, limit: int = 25) -> dict[str, Any]:
    """Aggregate contributors + owning orgs across many Repo nodes.

    Used by the Community panel on non-github entity pages: for a
    publication that cites N github repos, this returns the union of
    their contributors (ranked by how many of the cited repos they
    touch) and the set of orgs/users that own any of them.

    Two Cypher queries to keep each shape tight; total round-trip is
    a few hundred ms even on the local Neo4j.
    """
    if not slugs:
        return {"contributors": [], "owners": []}

    contributors_cypher = (
        "MATCH (r:Repo) WHERE r.full_name IN $slugs "
        "MATCH (r)<-[:CONTRIBUTES_TO]-(u:User) "
        "WITH u, count(DISTINCT r) AS n_repos, collect(DISTINCT r.full_name) AS repos "
        "RETURN u.login AS login, u.name AS name, n_repos, repos "
        "ORDER BY n_repos DESC, login "
        "LIMIT $limit"
    )
    owner_cypher = (
        "MATCH (r:Repo) WHERE r.full_name IN $slugs "
        "MATCH (r)<-[:OWNS]-(o) "
        "WITH o, count(DISTINCT r) AS n_repos, "
        "     collect(DISTINCT r.full_name) AS repos, labels(o)[0] AS kind "
        "RETURN o.login AS login, o.name AS name, n_repos, repos, kind "
        "ORDER BY n_repos DESC, login "
        "LIMIT $limit"
    )
    c_rows = neo4j_run(contributors_cypher, {"slugs": slugs, "limit": limit})
    o_rows = neo4j_run(owner_cypher, {"slugs": slugs, "limit": limit})
    return {
        "contributors": [
            {
                "login": r.get("login") or "",
                "name": (r.get("name") or "").strip(),
                "n_repos": int(r.get("n_repos") or 0),
                "repos": list(r.get("repos") or []),
            }
            for r in c_rows
            if r.get("login")
        ],
        "owners": [
            {
                "login": r.get("login") or "",
                "name": (r.get("name") or "").strip(),
                "kind": r.get("kind") or "",
                "n_repos": int(r.get("n_repos") or 0),
                "repos": list(r.get("repos") or []),
            }
            for r in o_rows
            if r.get("login")
        ],
    }


def neo4j_repo_stats(slugs: list[str]) -> dict[str, dict[str, Any]]:
    """Batch lookup: for each ``owner/repo`` slug, return its community
    stats (contributor count + owning org name).

    Issued as one Cypher query with ``UNWIND $slugs`` so a list of 10
    repos is a single round-trip to Neo4j instead of 10. Used by the
    Connected-on-GitHub panel to decorate non-github entity pages
    with "→ N contributors · org X" sublines per cited repo.
    """
    if not slugs:
        return {}
    cypher = (
        "UNWIND $slugs AS slug "
        "OPTIONAL MATCH (r:Repo {full_name: slug}) "
        "OPTIONAL MATCH (r)<-[:CONTRIBUTES_TO]-(u:User) "
        "WITH slug, r, count(DISTINCT u) AS contributors "
        "OPTIONAL MATCH (r)<-[:OWNS]-(o) "
        "RETURN slug, "
        "       contributors, "
        "       coalesce(o.login, '') AS owner_login, "
        "       coalesce(o.name, '')  AS owner_name, "
        "       CASE WHEN o:Org THEN 'Org' WHEN o:User THEN 'User' ELSE '' END AS owner_kind, "
        "       r IS NOT NULL AS indexed"
    )
    rows = neo4j_run(cypher, {"slugs": slugs})
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        out[row["slug"]] = {
            "indexed": bool(row.get("indexed")),
            "contributors": int(row.get("contributors") or 0),
            "owner_login": row.get("owner_login") or "",
            "owner_name": row.get("owner_name") or "",
            "owner_kind": row.get("owner_kind") or "",
        }
    return out
