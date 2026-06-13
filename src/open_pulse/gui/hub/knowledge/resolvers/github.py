"""github.com resolver — repos / users / orgs.

GitHub is the densest data source: every crawled repository is a node
in Neo4j, a ``schema:SoftwareSourceCode`` in Oxigraph once the
metadata extractor has run, and a chunked document in the
``github_repos`` Qdrant collection (which carries ``entity_id`` /
``repo_id`` as the ``owner/repo`` slug — the match key, not the URL).

Single-segment paths (``github.com/<login>``) are *also* known to
Neo4j as ``User`` or ``Org`` nodes — we pull the org/user profile
into the page so visitors landing there from a contributor click
see a meaningful summary instead of a bare "we know nothing" page.
"""

from __future__ import annotations

from ..entity import Entity, Fact
from ..normalize import HubRef
from ..stores import neo4j_user_org_profile
from . import base

_COLLECTIONS = ["github_repos"]


def resolve(ref: HubRef, *, on_status=None) -> Entity | None:
    parts = ref.path.split("/") if ref.path else []
    if not parts:
        return None

    if len(parts) >= 2:
        slug = f"{parts[0]}/{parts[1]}"
        kind = "GitHub repository"
        title = slug
    else:
        slug = parts[0]
        kind = "GitHub user or organization"
        title = slug
    canonical = f"https://github.com/{slug}"

    entity = base.build_entity(
        HubRef(host=ref.host, path=slug, canonical_url=canonical),
        collections=_COLLECTIONS,
        kind=kind,
        title_fallback=title,
        identifiers_fn=_extract_identifiers,
        on_status=on_status,
        # Skip Qdrant title promotion — bare ``name`` ("s6-overlay")
        # is less informative than the slug.
        title_strategy=(),
    )

    if len(parts) >= 2 and entity is not None and "/" not in entity.title:
        # SPARQL might have returned ``schema:name = "s6-overlay"``;
        # for a repo the ``owner/repo`` slug reads better as a heading.
        entity.title = slug

    if len(parts) == 1:
        # Single-segment github URL → user or org. Pull the Neo4j
        # profile to promote name + repo / member counts. If neither
        # Neo4j nor build_entity returned anything, return None so
        # the route queues into the wanted list.
        entity = _enrich_user_or_org(slug, entity)

    return entity


def _enrich_user_or_org(login: str, entity: Entity | None) -> Entity | None:
    """Promote the Neo4j ``User`` / ``Org`` profile onto the entity.

    Called for single-segment github URLs. When the login isn't in
    Neo4j either (and ``entity`` came back None), the caller queues
    the URL in the wanted list as usual.
    """
    profile = neo4j_user_org_profile(login)
    if profile is None:
        return entity

    if entity is None:
        # Build a minimal entity from Neo4j alone. ``build_entity``
        # already attached graph neighbours when it ran — if it
        # returned None, neighbours_from_neo4j must have been empty,
        # meaning Neo4j has the node but no edges. Render anyway.

        canonical = f"https://github.com/{login}"
        entity = Entity(
            ref_url=canonical,
            host="github.com",
            title=profile["name"] or login,
            subtitle=canonical,
            kind=_kind_label(profile["kind"]),
            facts=[],
            neighbours=[],
            enriched=True,
        )

    # Title: prefer the Neo4j ``name`` when it's meaningful, else the
    # login. Users frequently have empty names so we accept either.
    if profile["name"]:
        entity.title = profile["name"]
    else:
        entity.title = login

    entity.kind = _kind_label(profile["kind"])

    extra: list[Fact] = []
    # Order matches the headline list in _entity_body.html so the
    # tiles show up at the top of the page. All sourced from Neo4j.
    if profile["kind"] == "Org":
        if profile["owned_repos"]:
            extra.append(Fact(label="owned_repos", value=str(profile["owned_repos"]), source="graph"))
        if profile["members"]:
            extra.append(Fact(label="members", value=str(profile["members"]), source="graph"))
    else:  # User
        if profile["contributed_to"]:
            extra.append(
                Fact(label="contributed_to", value=str(profile["contributed_to"]), source="graph")
            )
        if profile["owned_repos"]:
            extra.append(Fact(label="owned_repos", value=str(profile["owned_repos"]), source="graph"))
        if profile["org_memberships"]:
            extra.append(
                Fact(
                    label="org_memberships",
                    value=str(profile["org_memberships"]),
                    source="graph",
                )
            )
    # Add login as a fact too if the title is the name (otherwise the
    # login disappears).
    if profile["name"] and profile["login"]:
        extra.append(
            Fact(
                label="login",
                value=profile["login"],
                href=f"https://github.com/{profile['login']}",
                source="graph",
            )
        )

    # Front-load the Neo4j tiles so they take the headline slot.
    entity.facts = extra + list(entity.facts)
    return entity


def _kind_label(neo4j_kind: str) -> str:
    if neo4j_kind == "Org":
        return "GitHub organization"
    if neo4j_kind == "User":
        return "GitHub user"
    return "GitHub user or organization"


def _extract_identifiers(bindings: list[dict]) -> list[Fact]:
    wanted = {
        "http://schema.org/sameAs": "sameAs",
        "http://schema.org/identifier": "identifier",
        "http://schema.org/codeRepository": "codeRepository",
    }
    out: list[Fact] = []
    for row in bindings:
        p = row.get("p", {}).get("value", "")
        if p not in wanted:
            continue
        v = row.get("o", {}).get("value", "")
        if not v:
            continue
        out.append(
            Fact(label=wanted[p], value=v, href=v if v.startswith("http") else "")
        )
    return out
