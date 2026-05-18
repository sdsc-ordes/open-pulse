"""Shared building blocks every resolver leans on.

A resolver typically does the same dance:

1. Probe Oxigraph for ``?p ?o`` of the canonical URL (and a couple of
   URL aliases — schemes / trailing slashes — because not every
   pipeline normalises the subject IRI the same way).
2. Pull 1-hop neighbours from Neo4j.
3. Look up matching chunks in one or more ``gme-qdrant`` collections.
4. Optionally run the OpenAI-compatible agent to write a narrative.

Steps 2–4 are uniform; this module owns them. The host-specific logic
in each resolver narrows itself to query strings + the
human-facing label/kind/identifier mapping.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import replace

from ..agent import narrate
from ..entity import Entity, Fact, Mention, Neighbour
from ..normalize import HubRef, parse_ref
from ..qdrant import lookup_for_ref
from ..stores import sparql_describe, sparql_select

# Process-lifetime cache for the agent narrative: composing a
# narrative is a 1–30s call, but the inputs (facts hash) rarely
# change inside a single hub session.
_NARRATIVE_CACHE: dict[str, str] = {}
_NARRATIVE_CACHE_MAX = 256


# Predicates we expand from short qnames to full IRIs in fact labels.
_SCHEMA = "http://schema.org/"


def url_aliases(canonical_url: str) -> list[str]:
    """Reasonable variants of the same URL.

    Pipelines disagree about trailing slashes and ``http`` vs ``https``,
    so the DESCRIBE has to try every plausible form. We only emit
    scheme-full URLs — Oxigraph (and SPARQL 1.1 in strict mode) reject
    relative IRIs inside ``<>``, so scheme-less aliases would 400 the
    whole query.
    """
    out: list[str] = [canonical_url]
    if canonical_url.startswith("https://"):
        out.append("http://" + canonical_url[len("https://") :])
    out.append(canonical_url.rstrip("/") + "/")
    seen: set[str] = set()
    deduped: list[str] = []
    for u in out:
        if "://" not in u:
            continue  # never emit a relative IRI
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def probe_sparql(canonical_url: str) -> list[dict]:
    """Try every URL alias until something comes back from SPARQL."""
    for alias in url_aliases(canonical_url):
        bindings = sparql_describe(alias)
        if bindings:
            return bindings
    return []


def predicate_label(predicate_iri: str) -> str:
    """Short, human-friendly label for a predicate URL.

    ``http://schema.org/license`` → ``schema:license``. Anything else
    falls back to the last URL segment so the table doesn't show raw
    URIs for unknown predicates.
    """
    if predicate_iri.startswith(_SCHEMA):
        return "schema:" + predicate_iri[len(_SCHEMA) :]
    if "#" in predicate_iri:
        return predicate_iri.rsplit("#", 1)[1]
    return predicate_iri.rstrip("/").rsplit("/", 1)[-1]


def facts_from_bindings(bindings: Iterable[dict]) -> list[Fact]:
    """Render ``?p ?o`` rows as Fact entries, lightly grouped.

    Multiple values for the same predicate are kept as separate rows
    (rather than merged) so the page preserves their original order.
    """
    out: list[Fact] = []
    for row in bindings:
        p = row.get("p", {}).get("value", "")
        o_node = row.get("o") or {}
        value = o_node.get("value", "")
        if not p or not value:
            continue
        out.append(Fact(label=predicate_label(p), value=value))
    return out


def neighbours_from_neo4j(
    canonical_url: str, *, slug: str = "", limit: int = 25
) -> list[Neighbour]:
    """1-hop Cypher neighbours from the crawler's GitHub graph.

    The graph schema is Repo / User / Org with relationships
    CONTRIBUTES_TO, OWNS, FORK_OF, MEMBER_OF. We dispatch on the
    canonical URL's shape — github.com/owner/repo → Repo lookup;
    github.com/owner → User/Org lookup. Other hosts get nothing here
    (the graph doesn't index them); their neighbours come via the
    Connected-on-GitHub panel instead.
    """
    from ..stores import (
        neo4j_rel_label,
        neo4j_repo_neighbours,
        neo4j_user_or_org_neighbours,
    )

    rows: list[dict] = []
    if slug and "/" in slug:
        rows = neo4j_repo_neighbours(slug, limit=limit)
    elif canonical_url.startswith("https://github.com/") and slug:
        # Single-segment github URL → user or org login.
        rows = neo4j_user_or_org_neighbours(slug, limit=limit)

    out: list[Neighbour] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        rel = row.get("rel")
        if not rel:
            continue
        kind = (row.get("kind") or "").strip()
        outgoing = bool(row.get("outgoing"))

        if kind == "Repo":
            ident = row.get("full_name") or ""
            label = ident or row.get("name") or ""
            hub_url = f"/hub/github.com/{ident}" if ident else ""
            external = f"https://github.com/{ident}" if ident else ""
        elif kind in ("User", "Org"):
            ident = row.get("login") or ""
            display = row.get("name") or ""
            label = display or ident
            hub_url = f"/hub/github.com/{ident}" if ident else ""
            external = f"https://github.com/{ident}" if ident else ""
        else:
            ident = row.get("full_name") or row.get("login") or row.get("name") or ""
            label = ident
            hub_url = ""
            external = ""

        if not label:
            continue
        key = (kind, label)
        if key in seen:
            continue
        seen.add(key)

        # Encode direction in the relation label so the user can tell
        # "contributor of THIS" vs "this CONTRIBUTES TO that".
        rel_label = neo4j_rel_label(rel)
        if not outgoing and rel in ("CONTRIBUTES_TO", "MEMBER_OF", "OWNS"):
            # Incoming edge: invert the verb so it makes sense from
            # the entity's perspective.
            rel_label = {
                "CONTRIBUTES_TO": "contributed by",
                "MEMBER_OF": "has member",
                "OWNS": "owned by",
            }.get(rel, rel_label)

        out.append(
            Neighbour(
                label=label,
                relation=rel_label,
                hub_url=hub_url,
                external_url=external,
                kind=kind,
                source_type="GitHub",
            )
        )
    return out


def maybe_narrate(entity: Entity) -> Entity:
    """Attach an LLM narrative if a model is configured.

    Cached per (URL, facts-hash) so revisits don't re-pay the latency.
    """
    if not entity.facts and not entity.mentions:
        return entity
    cache_key = _cache_key(entity)
    cached = _NARRATIVE_CACHE.get(cache_key)
    if cached is not None:
        return replace(entity, narrative=cached)

    text = narrate(entity)
    if text:
        if len(_NARRATIVE_CACHE) >= _NARRATIVE_CACHE_MAX:
            # Drop an arbitrary half — the cache is bounded, not LRU,
            # because the hub process is short-lived.
            for k in list(_NARRATIVE_CACHE)[: _NARRATIVE_CACHE_MAX // 2]:
                _NARRATIVE_CACHE.pop(k, None)
        _NARRATIVE_CACHE[cache_key] = text
        return replace(entity, narrative=text)
    return entity


def _cache_key(entity: Entity) -> str:
    h = hashlib.sha256()
    h.update(entity.ref_url.encode("utf-8"))
    for f in entity.facts:
        h.update(b"|")
        h.update(f.label.encode("utf-8"))
        h.update(b"=")
        h.update(f.value.encode("utf-8"))
    for m in entity.mentions[:3]:
        h.update(b"|m=")
        h.update((m.collection + ":" + m.source_url).encode("utf-8"))
    return h.hexdigest()


StatusCallback = Callable[[str], None]


def _emit(on_status: StatusCallback | None, msg: str) -> None:
    if on_status is None:
        return
    try:
        on_status(msg)
    except Exception:  # noqa: BLE001
        # Status emission must never break a lookup.
        pass


_DEFAULT_TITLE_STRATEGY: tuple[str, ...] = (
    "title",
    "name",
    "full_name",
    "display_name",
)


def build_entity(
    canonical_ref: HubRef,
    *,
    collections: list[str],
    kind: str,
    title_fallback: str,
    enriched: bool = True,
    identifiers_fn: "Callable[[list[dict]], list[Fact]] | None" = None,
    on_status: StatusCallback | None = None,
    title_strategy: tuple[str, ...] = _DEFAULT_TITLE_STRATEGY,
) -> Entity | None:
    """The shape every host resolver wants.

    Probes SPARQL + Neo4j + Qdrant + agent, merges what comes back,
    and tags the sources audit row. Returns None when every store
    came up empty so the route can queue the URL in hub_wanted.

    ``on_status`` is called with human-readable progress messages at
    each step; the SSE endpoint forwards them to the browser as live
    status updates.
    """
    canonical = canonical_ref.canonical_url

    _emit(on_status, f"Probing SPARQL store for {canonical}")
    bindings = probe_sparql(canonical)
    facts = facts_from_bindings(bindings)
    _emit(on_status, f"SPARQL: {len(bindings)} facts")

    _emit(on_status, "Querying Neo4j for 1-hop neighbours")
    neighbours = neighbours_from_neo4j(canonical, slug=canonical_ref.path)
    _emit(on_status, f"Neo4j: {len(neighbours)} neighbours")

    coll_label = (
        ", ".join(collections)
        if len(collections) <= 4
        else f"{len(collections)} collections"
    )
    _emit(on_status, f"Searching Qdrant ({coll_label})")
    mentions, qdrant_facts = lookup_for_ref(collections, canonical_ref)
    _emit(
        on_status,
        f"Qdrant: {len(mentions)} mentions, {len(qdrant_facts)} payload facts",
    )

    # Backlinks are loaded lazily by the browser after the entity
    # body lands — see /api/hub/backlinks/{ref}. Keeping the resolver
    # synchronous wins ~20s on the initial page load when Qdrant is
    # under load.
    if not facts and not neighbours and not mentions and not qdrant_facts:
        return None

    seen = {f.label for f in facts}
    for f in qdrant_facts:
        if f.label in seen:
            continue
        facts.append(f)
        seen.add(f.label)

    identifiers = identifiers_fn(bindings) if identifiers_fn else []

    # Title resolution order:
    # 1. SPARQL label (curated; rare for new sources).
    # 2. Qdrant payload fields from ``title_strategy`` — per-resolver
    #    so e.g. github keeps its ``owner/repo`` fallback (informative)
    #    instead of getting downgraded to bare ``name`` ("s6-overlay").
    # 3. The host-specific title_fallback (URL slug).
    label = first_label(canonical)
    if not label and title_strategy:
        for f in qdrant_facts:
            if f.label in title_strategy:
                label = f.value
                break
    if not label:
        label = title_fallback

    entity = Entity(
        ref_url=canonical,
        host=canonical_ref.host,
        title=label,
        subtitle=canonical,
        kind=kind,
        identifiers=identifiers,
        facts=facts,
        neighbours=neighbours,
        mentions=mentions,
        enriched=enriched,
    )

    # Only announce the narrative step when an LLM is configured;
    # otherwise the message is misleading (maybe_narrate is a no-op).
    from ...auth import get_settings  # local import — keep settings load lazy

    settings = get_settings()
    if (
        (entity.facts or entity.mentions)
        and settings.llm_model
        and settings.llm_api_key
    ):
        _emit(on_status, "Composing narrative with the agent")
        entity = maybe_narrate(entity)
        if entity.narrative:
            _emit(on_status, "Narrative ready")
    else:
        entity = maybe_narrate(entity)  # cheap no-op when unconfigured

    entity.sources = sources_summary(
        sparql_hits=len(bindings),
        neighbours=len(neighbours),
        mentions=len(mentions),
        narrative=bool(entity.narrative),
    )
    return entity


def attach_qdrant_data(
    entity: Entity, collections: list[str], ref: HubRef
) -> Entity:
    """Look up matching points and fold the payload into the entity.

    Beyond the existing "mentions" panel this also drains every useful
    payload field into the facts list — the GME collections carry
    rich, ready-to-render metadata (license, language, downloads,
    authors, keywords, …) that we'd otherwise leave on the floor.
    Facts already present from SPARQL are preserved; only new labels
    coming from Qdrant get appended.
    """
    mentions, qdrant_facts = lookup_for_ref(collections, ref)
    if not mentions and not qdrant_facts:
        return entity

    existing_labels = {f.label for f in entity.facts}
    merged_facts = list(entity.facts)
    for f in qdrant_facts:
        if f.label in existing_labels:
            continue
        merged_facts.append(f)
        existing_labels.add(f.label)

    return replace(entity, mentions=mentions, facts=merged_facts)


def attach_mentions(entity: Entity, collections: list[str]) -> Entity:
    """Backward-compat shim — prefer :func:`attach_qdrant_data`."""
    return attach_qdrant_data(entity, collections, parse_ref(entity.ref_url))


def sources_summary(
    *,
    sparql_hits: int,
    neighbours: int,
    mentions: int,
    narrative: bool,
) -> list[Fact]:
    """Tiny audit row at the bottom of every entity page.

    Backlinks aren't reported here — they're loaded lazily after the
    entity body renders, so the count isn't known at this point.
    """
    return [
        Fact(label="SPARQL", value=f"{sparql_hits} facts"),
        Fact(label="Neo4j", value=f"{neighbours} neighbours"),
        Fact(label="Qdrant", value=f"{mentions} mentions"),
        Fact(label="Agent", value="yes" if narrative else "—"),
    ]


def sparql_label_query(canonical_url: str) -> str:
    """Try schema:name / schema:headline / rdfs:label for a title."""
    aliases = " ".join(f"<{u}>" for u in url_aliases(canonical_url))
    return (
        "PREFIX schema: <http://schema.org/> "
        "PREFIX rdfs:   <http://www.w3.org/2000/01/rdf-schema#> "
        f"SELECT ?label WHERE {{ VALUES ?s {{ {aliases} }} "
        "?s (schema:name|schema:headline|rdfs:label) ?label } LIMIT 1"
    )


def first_label(canonical_url: str) -> str:
    rows = sparql_select(sparql_label_query(canonical_url))
    if not rows:
        return ""
    return rows[0].get("label", {}).get("value", "")
