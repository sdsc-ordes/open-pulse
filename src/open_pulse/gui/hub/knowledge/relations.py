"""Aggregate relation lookups for the Canvas expand modal.

The hub has three relationship-aware stores beyond Qdrant + DuckDB:

* **Neo4j** — graph of ``Repo`` / ``User`` / ``Org`` / ``Topic`` nodes
  with typed edges (``CONTRIBUTES_TO``, ``OWNS``, ``FORK_OF``, …).
* **SPARQL / Oxigraph** — RDF triples in schema.org form.
* **OpenSearch / GrimoireLab** — git commits enriched per-author so we
  can rank commit-level contributors with names + email-derived
  identities the Neo4j contributor list doesn't carry.

Each helper here returns a list of :class:`RelatedGroup` so they can
be merged with the Qdrant-shaped ``related`` / ``backlinks`` /
``people`` lists in :func:`routes.hub.hub_expand_json`.

These calls are best-effort: if a store is unreachable or returns
nothing useful we return ``[]`` rather than raising, so the canvas
still works when only some sub-systems are alive.
"""

from __future__ import annotations

import logging
from typing import Any

from .entity import RelatedGroup, RelatedItem
from .normalize import HubRef
from . import stores

log = logging.getLogger(__name__)


# ── Neo4j: 1-hop neighbours bucketed by relationship type ────────────────


def from_neo4j(ref: HubRef, *, per_group_limit: int = 12) -> list[RelatedGroup]:
    """Pull every 1-hop neighbour of the entity's Neo4j node.

    The crawler stores ``Repo`` keyed by ``full_name`` (``owner/repo``)
    and ``User`` / ``Org`` keyed by ``login``. We dispatch on the hub
    ref's host + path and return one :class:`RelatedGroup` per
    relationship type so users can pick (e.g.) "all contributors"
    without also pulling forks.
    """
    if ref.host != "github.com":
        return []

    path = (ref.path or "").strip("/")
    if not path:
        return []
    parts = path.split("/")

    rows: list[dict[str, Any]] = []
    if len(parts) >= 2:
        slug = f"{parts[0]}/{parts[1]}"
        try:
            rows = stores.neo4j_repo_neighbours(slug, limit=per_group_limit * 6)
        except Exception as exc:  # noqa: BLE001
            log.warning("neo4j_repo_neighbours failed for %s: %s", slug, exc)
            return []
    elif len(parts) == 1 and parts[0]:
        try:
            rows = stores.neo4j_user_or_org_neighbours(
                parts[0], limit=per_group_limit * 6
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("neo4j_user_or_org_neighbours failed for %s: %s", parts[0], exc)
            return []

    if not rows:
        return []

    # Bucket by relationship type, ignoring null edges.
    buckets: dict[str, list[RelatedItem]] = {}
    for row in rows:
        rel = (row.get("rel") or "").strip()
        if not rel:
            continue
        kind = (row.get("kind") or "").strip()
        login = (row.get("login") or "").strip()
        full_name = (row.get("full_name") or "").strip()
        name = (row.get("name") or "").strip()
        if full_name and kind == "Repo":
            hub_url = f"/hub/github.com/{full_name}"
            external = f"https://github.com/{full_name}"
            label = full_name
            source_type = "GitHub"
        elif login:
            hub_url = f"/hub/github.com/{login}"
            external = f"https://github.com/{login}"
            label = name or login
            source_type = kind or "GitHub"
        else:
            continue
        bucket_title = stores.neo4j_rel_label(rel)
        buckets.setdefault(bucket_title, []).append(
            RelatedItem(
                label=label,
                hub_url=hub_url,
                external_url=external,
                badge="",
                source_type=source_type,
            )
        )

    return [
        RelatedGroup(title=title, items=items[:per_group_limit])
        for title, items in buckets.items()
        if items
    ]


# ── SPARQL: object predicates pointing to other hub resources ────────────


# Predicates worth surfacing on the canvas — must point to *another*
# IRI we can render. Free-form literals (titles, descriptions) live in
# the entity's own card already.
_SPARQL_LABELS: dict[str, str] = {
    "http://schema.org/author": "Authors",
    "http://schema.org/contributor": "Contributors",
    "http://schema.org/creator": "Creators",
    "http://schema.org/maintainer": "Maintainers",
    "http://schema.org/publisher": "Publishers",
    "http://schema.org/funder": "Funders",
    "http://schema.org/cites": "Cites",
    "http://schema.org/citation": "Cited works",
    "http://schema.org/isPartOf": "Part of",
    "http://schema.org/hasPart": "Has part",
    "http://schema.org/codeRepository": "Code repository",
    "http://schema.org/sameAs": "Same as",
    "http://schema.org/about": "Topics",
    "http://schema.org/keywords": "Keywords",
    "http://schema.org/license": "License",
    "http://schema.org/affiliation": "Affiliation",
    "http://www.w3.org/2000/01/rdf-schema#seeAlso": "See also",
}


def _strip_scheme(u: str) -> str:
    s = u or ""
    if s.startswith("https://"):
        return s[len("https://") :]
    if s.startswith("http://"):
        return s[len("http://") :]
    return s


def from_sparql(ref: HubRef, *, per_group_limit: int = 12) -> list[RelatedGroup]:
    """Group SPARQL triples by predicate, surfacing object IRIs as items.

    ``sparql_describe`` returns every ``?p ?o`` row about the
    canonical URL. We keep only predicates that map to a known
    "renderable as a relation" label, and objects that look like IRIs
    (free-form literals are not actionable on the canvas).
    """
    if not ref.canonical_url:
        return []
    try:
        rows = stores.sparql_describe(ref.canonical_url, limit=400)
    except Exception as exc:  # noqa: BLE001
        log.warning("sparql_describe failed for %s: %s", ref.canonical_url, exc)
        return []
    if not rows:
        return []

    buckets: dict[str, list[RelatedItem]] = {}
    seen_per_bucket: dict[str, set[str]] = {}
    for r in rows:
        # SPARQL JSON bindings: each cell is ``{"type": "uri", "value": "..."}``.
        p_cell = r.get("p") or {}
        o_cell = r.get("o") or {}
        p_iri = (p_cell.get("value") if isinstance(p_cell, dict) else str(p_cell)) or ""
        o_type = (o_cell.get("type") if isinstance(o_cell, dict) else "") or ""
        o = (o_cell.get("value") if isinstance(o_cell, dict) else str(o_cell)) or ""
        p_iri = p_iri.strip()
        o = o.strip()
        if not p_iri or not o:
            continue
        title = _SPARQL_LABELS.get(p_iri)
        if title is None:
            continue
        # Only IRI-typed objects render as actionable relations —
        # literals (titles, descriptions) live on the entity's card.
        if o_type and o_type != "uri":
            continue
        if not (o.startswith("http://") or o.startswith("https://")):
            continue
        stripped = _strip_scheme(o).rstrip("/")
        hub_url = f"/hub/{stripped}"
        seen = seen_per_bucket.setdefault(title, set())
        if hub_url in seen:
            continue
        seen.add(hub_url)
        # Use the last URI segment as a readable label fallback.
        label = stripped.rsplit("/", 1)[-1] or stripped
        buckets.setdefault(title, []).append(
            RelatedItem(
                label=label,
                hub_url=hub_url,
                external_url=o,
                badge="",
                source_type="SPARQL",
            )
        )

    return [
        RelatedGroup(title=title, items=items[:per_group_limit])
        for title, items in buckets.items()
        if items
    ]


# ── OpenSearch: top commit authors for a repository ───────────────────────


def from_opensearch(ref: HubRef, *, per_group_limit: int = 20) -> list[RelatedGroup]:
    """Top commit authors for a repository, sourced from the
    GrimoireLab-enriched git index.

    GitHub-only for now (the git ingest pipeline indexes git
    repositories' commits by their ``origin`` URL).
    """
    if ref.host != "github.com" or not ref.canonical_url:
        return []
    try:
        from . import opensearch as os_mod

        client_bits = os_mod._client()
    except Exception as exc:  # noqa: BLE001
        log.warning("opensearch client init failed: %s", exc)
        return []
    if client_bits is None:
        return []

    body = {
        "size": 0,
        "query": {"term": {"origin": ref.canonical_url}},
        "aggs": {
            "by_author": {
                "terms": {
                    "field": "author_name.keyword",
                    "size": per_group_limit,
                }
            }
        },
    }
    try:
        result = os_mod._post(f"/{os_mod._GIT_INDEX_PATTERN}/_search", body)
    except Exception as exc:  # noqa: BLE001
        log.warning("opensearch by_author failed: %s", exc)
        return []
    if not result:
        return []
    buckets = ((result.get("aggregations") or {}).get("by_author") or {}).get(
        "buckets"
    ) or []
    items: list[RelatedItem] = []
    for b in buckets:
        name = (b.get("key") or "").strip()
        if not name:
            continue
        commits = int(b.get("doc_count") or 0)
        items.append(
            RelatedItem(
                label=name,
                hub_url="",  # commit authors don't always resolve to a hub page
                external_url="",
                badge=f"{commits:,} commits",
                source_type="Commit author",
            )
        )
    if not items:
        return []
    return [RelatedGroup(title="Commit authors (GrimoireLab)", items=items)]
