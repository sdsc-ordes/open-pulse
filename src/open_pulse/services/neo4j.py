"""Neo4j service client.

Two responsibilities:

1. **Health probe** (``check_bolt``) — TCP-only reachability check used by
   ``open-pulse health``. Doesn't require credentials.
2. **Graph upload** (``upload``) — pushes the crawler's JSON graph dict
   (``{"users": {...}, "orgs": {...}, "repos": {...}}``) into Neo4j as
   ``User``/``Org``/``Repo`` nodes plus ``OWNS`` / ``CONTRIBUTES_TO`` /
   ``MEMBER_OF`` / ``FORK_OF`` edges. Uses batched ``UNWIND`` queries so
   the round-trip cost is constant per node-type rather than per node.

Auth is resolved at call time from the env var named in
``CrawlerServiceConfig.auth_env`` (default ``NEO4J_AUTH``), formatted as
``username/password`` to match what the Neo4j container expects.
"""

from __future__ import annotations

import logging
import os
import socket
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 5
_DEFAULT_AUTH_ENV = "NEO4J_AUTH"


class Neo4jService:
    """Lightweight Neo4j client wrapper."""

    def __init__(self, endpoint: str, auth_env: str = _DEFAULT_AUTH_ENV) -> None:
        self.endpoint = endpoint
        self.auth_env = auth_env
        self._driver: Any = None
        self._closed = False

    # -- Auth --------------------------------------------------------------

    def _resolved_auth(self) -> tuple[str, str]:
        raw = os.environ.get(self.auth_env, "")
        if not raw or "/" not in raw:
            raise RuntimeError(
                f"{self.auth_env} is not set or malformed; expected 'username/password'."
            )
        username, password = raw.split("/", 1)
        return username, password

    def _get_driver(self) -> Any:
        if self._driver is None:
            from neo4j import GraphDatabase

            username, password = self._resolved_auth()
            self._driver = GraphDatabase.driver(
                self.endpoint, auth=(username, password)
            )
        return self._driver

    # -- Upload ------------------------------------------------------------

    def upload(self, graph: dict[str, Any]) -> dict[str, int]:
        """Push the crawler graph into Neo4j.

        ``graph`` is the inner dict written by the crawler step
        (``{"users": {...}, "orgs": {...}, "repos": {...}}``).
        Idempotent — every write is ``MERGE`` so re-running against the
        same data is a no-op.

        Returns a dict with the merged-row counts for each node-type.
        """
        users: dict[str, dict[str, Any]] = graph.get("users") or {}
        orgs: dict[str, dict[str, Any]] = graph.get("orgs") or {}
        repos: dict[str, dict[str, Any]] = graph.get("repos") or {}

        user_rows = [_user_row(login, u) for login, u in users.items()]
        org_rows = [_org_row(login, o) for login, o in orgs.items()]
        repo_rows = [_repo_row(full_name, r) for full_name, r in repos.items()]

        owner_repo_edges = _owner_edges(users, orgs)
        member_org_edges = _member_edges(orgs)
        contributor_edges = _contributor_edges(repos)
        fork_edges = _fork_edges(repos)
        dependency_edges = _dependency_edges(repos)

        with self._get_driver().session() as session:
            session.execute_write(_merge_users, user_rows)
            session.execute_write(_merge_orgs, org_rows)
            session.execute_write(_merge_repos, repo_rows)
            session.execute_write(_merge_owner_edges, owner_repo_edges)
            session.execute_write(_merge_member_edges, member_org_edges)
            session.execute_write(_merge_contributor_edges, contributor_edges)
            session.execute_write(_merge_fork_edges, fork_edges)
            session.execute_write(_merge_dependency_edges, dependency_edges)

        counts = {
            "users": len(user_rows),
            "orgs": len(org_rows),
            "repos": len(repo_rows),
            "owner_edges": len(owner_repo_edges),
            "member_edges": len(member_org_edges),
            "contributor_edges": len(contributor_edges),
            "fork_edges": len(fork_edges),
            "dependency_edges": len(dependency_edges),
        }
        logger.info(
            "neo4j: merged users=%d orgs=%d repos=%d "
            "(owner=%d member=%d contributor=%d fork=%d depends_on=%d)",
            counts["users"],
            counts["orgs"],
            counts["repos"],
            counts["owner_edges"],
            counts["member_edges"],
            counts["contributor_edges"],
            counts["fork_edges"],
            counts["dependency_edges"],
        )
        return counts

    # -- Health probe ------------------------------------------------------

    def check_bolt(self) -> tuple[bool, str]:
        """Probe Neo4j Bolt endpoint reachability via TCP."""
        host, port = _parse_bolt_host_port(self.endpoint)
        try:
            with socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT):
                return True, "connection established"
        except OSError as exc:
            return False, str(exc)

    # -- Lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Release driver resources."""
        if self._driver is not None:
            try:
                self._driver.close()
            except Exception:  # noqa: BLE001
                pass
            self._driver = None
        self._closed = True


# -- Row builders -------------------------------------------------------------


def _user_row(login: str, u: dict[str, Any]) -> dict[str, Any]:
    return {
        "login": login,
        "name": u.get("name", "") or "",
        "id": u.get("id", 0) or 0,
        "type": u.get("type", "User"),
        "is_explored": bool(u.get("is_explored", False)),
        "exploration_timestamp": u.get("exploration_timestamp"),
    }


def _org_row(login: str, o: dict[str, Any]) -> dict[str, Any]:
    return {
        "login": login,
        "name": o.get("name", "") or "",
        "id": o.get("id", 0) or 0,
        "type": o.get("type", "Organization"),
        "is_explored": bool(o.get("is_explored", False)),
        "exploration_timestamp": o.get("exploration_timestamp"),
    }


def _repo_row(full_name: str, r: dict[str, Any]) -> dict[str, Any]:
    return {
        "full_name": full_name,
        "name": r.get("name", "") or "",
        "id": r.get("id", 0) or 0,
        "type": r.get("type", "Repository"),
        "owner": r.get("owner", "") or "",
        "is_explored": bool(r.get("is_explored", False)),
        "exploration_timestamp": r.get("exploration_timestamp"),
    }


def _owner_edges(
    users: dict[str, dict[str, Any]],
    orgs: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build (User|Org)-[:OWNS]->(Repo) edges from authored_repositories[]."""
    rows: list[dict[str, Any]] = []
    for login, u in users.items():
        for repo in u.get("authored_repositories") or []:
            rows.append({"owner_login": login, "owner_label": "User", "repo": repo})
        for repo in u.get("forked_repositories") or []:
            rows.append({"owner_login": login, "owner_label": "User", "repo": repo})
    for login, o in orgs.items():
        for repo in o.get("authored_repositories") or []:
            rows.append({"owner_login": login, "owner_label": "Org", "repo": repo})
        for repo in o.get("forked_repositories") or []:
            rows.append({"owner_login": login, "owner_label": "Org", "repo": repo})
    return rows


def _member_edges(orgs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Build (User)-[:MEMBER_OF]->(Org) edges from org.members[]."""
    rows: list[dict[str, Any]] = []
    for login, o in orgs.items():
        for member in o.get("members") or []:
            rows.append({"member": member, "org": login})
    return rows


def _contributor_edges(
    repos: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build (User)-[:CONTRIBUTES_TO]->(Repo) edges from repo.contributors[]."""
    rows: list[dict[str, Any]] = []
    for full_name, r in repos.items():
        for contrib in r.get("contributors") or []:
            rows.append({"contributor": contrib, "repo": full_name})
    return rows


def _fork_edges(repos: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Build (Repo)-[:FORK_OF]->(Repo) edges when fork info present."""
    rows: list[dict[str, Any]] = []
    for full_name, r in repos.items():
        parent = (
            r.get("parent_full_name")
            or r.get("source_full_name")
            or r.get("forked_from")
        )
        if isinstance(parent, str) and parent and parent != full_name:
            rows.append({"fork": full_name, "parent": parent})
    return rows


def _dependency_edges(
    repos: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build (Repo)-[:DEPENDS_ON]->(Repo) edges from both directions.

    The crawler stores both ``dependencies`` (what this repo uses) and
    ``dependents`` (what uses this repo). Both populate the same edge type;
    we just flip the direction when reading from ``dependents``.
    """
    rows: list[dict[str, Any]] = []
    for full_name, r in repos.items():
        for dep in r.get("dependencies") or []:
            rows.append({"consumer": full_name, "package": dep})
        for dependent in r.get("dependents") or []:
            rows.append({"consumer": dependent, "package": full_name})
    return rows


# -- Cypher transactions ------------------------------------------------------
#
# Each function takes a Neo4j ``ManagedTransaction`` and a list of row dicts.
# Runs a single batched ``UNWIND`` query — one round-trip per node-type.


def _merge_users(tx: Any, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    tx.run(
        """
        UNWIND $rows AS row
        MERGE (n:User {login: row.login})
        SET n.name = row.name,
            n.id = row.id,
            n.type = row.type,
            n.is_explored = row.is_explored,
            n.exploration_timestamp = row.exploration_timestamp
        """,
        rows=rows,
    )


def _merge_orgs(tx: Any, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    tx.run(
        """
        UNWIND $rows AS row
        MERGE (n:Org {login: row.login})
        SET n.name = row.name,
            n.id = row.id,
            n.type = row.type,
            n.is_explored = row.is_explored,
            n.exploration_timestamp = row.exploration_timestamp
        """,
        rows=rows,
    )


def _merge_repos(tx: Any, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    tx.run(
        """
        UNWIND $rows AS row
        MERGE (n:Repo {full_name: row.full_name})
        SET n.name = row.name,
            n.id = row.id,
            n.type = row.type,
            n.owner = row.owner,
            n.is_explored = row.is_explored,
            n.exploration_timestamp = row.exploration_timestamp
        """,
        rows=rows,
    )


def _merge_owner_edges(tx: Any, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    # Two queries because Cypher MERGE requires concrete labels — branch on
    # owner_label to pick the right one. UNWIND handles the per-row dispatch
    # via a CASE-like filter.
    tx.run(
        """
        UNWIND [r IN $rows WHERE r.owner_label = 'User'] AS row
        MERGE (a:User {login: row.owner_login})
        MERGE (r:Repo {full_name: row.repo})
        MERGE (a)-[:OWNS]->(r)
        """,
        rows=rows,
    )
    tx.run(
        """
        UNWIND [r IN $rows WHERE r.owner_label = 'Org'] AS row
        MERGE (a:Org {login: row.owner_login})
        MERGE (r:Repo {full_name: row.repo})
        MERGE (a)-[:OWNS]->(r)
        """,
        rows=rows,
    )


def _merge_member_edges(tx: Any, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    tx.run(
        """
        UNWIND $rows AS row
        MERGE (m:User {login: row.member})
        MERGE (o:Org {login: row.org})
        MERGE (m)-[:MEMBER_OF]->(o)
        """,
        rows=rows,
    )


def _merge_contributor_edges(tx: Any, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    tx.run(
        """
        UNWIND $rows AS row
        MERGE (c:User {login: row.contributor})
        MERGE (r:Repo {full_name: row.repo})
        MERGE (c)-[:CONTRIBUTES_TO]->(r)
        """,
        rows=rows,
    )


def _merge_fork_edges(tx: Any, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    tx.run(
        """
        UNWIND $rows AS row
        MERGE (f:Repo {full_name: row.fork})
        MERGE (p:Repo {full_name: row.parent})
        MERGE (f)-[:FORK_OF]->(p)
        """,
        rows=rows,
    )


def _merge_dependency_edges(tx: Any, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    tx.run(
        """
        UNWIND $rows AS row
        MERGE (c:Repo {full_name: row.consumer})
        MERGE (p:Repo {full_name: row.package})
        MERGE (c)-[:DEPENDS_ON]->(p)
        """,
        rows=rows,
    )


# -- Helpers ------------------------------------------------------------------


def _parse_bolt_host_port(endpoint: str) -> tuple[str, int]:
    parsed = urlparse(endpoint)
    host = parsed.hostname or "localhost"
    port = parsed.port or 7687
    return host, port
