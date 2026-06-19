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
import re
from collections.abc import Callable, Iterable
from dataclasses import replace

from ..agent import narrate
from ..entity import Entity, Fact, Neighbour
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
    """Short, machine-ish label for a predicate URL.

    ``http://schema.org/license`` → ``schema:license``. Anything else
    falls back to the last URL segment so the table doesn't show raw
    URIs for unknown predicates.
    """
    if predicate_iri.startswith(_SCHEMA):
        return "schema:" + predicate_iri[len(_SCHEMA) :]
    if "#" in predicate_iri:
        return predicate_iri.rsplit("#", 1)[1]
    return predicate_iri.rstrip("/").rsplit("/", 1)[-1]


def _predicate_localname(predicate_iri: str) -> str:
    """The bare local name (no namespace), e.g. ``githubRepoStars``."""
    return predicate_label(predicate_iri).split(":")[-1]


# Curated, friendly labels for the predicates that show up most across the
# OpenPulse / GME / schema.org vocabularies — so the RDF table reads like the
# rest of the hub instead of raw qnames.
_PREDICATE_LABELS: dict[str, str] = {
    "githubRepoStars": "Stars",
    "githubRepoForks": "Forks",
    "open_issues_count": "Open issues",
    "watchers_count": "Watchers",
    "subscribers_count": "Subscribers",
    "network_count": "Forks (network)",
    "followers_count": "Followers",
    "following_count": "Following",
    "public_repos": "Public repos",
    "contributionCount": "Contributions",
    "size_kb": "Size (KB)",
    "githubUsername": "GitHub username",
    "githubRepositoryHandle": "GitHub repository",
    "repositoryType": "Repository type",
    "discipline": "Discipline",
    "ror_country": "Country",
    "html_url": "URL",
    "avatar_url": "Avatar",
    "pushed_at": "Last push",
    "github_updated_at": "Updated",
    "github_created_at": "Created",
}


def humanize_predicate(predicate_iri: str) -> str:
    """A title-cased, space-separated label for a predicate.

    ``schema:dateCreated`` → ``Date created``; ``githubRepoStars`` → ``Stars``
    (curated). Splits camelCase and snake_case so unknown predicates still
    read cleanly rather than as raw qnames."""
    local = _predicate_localname(predicate_iri)
    if local in _PREDICATE_LABELS:
        return _PREDICATE_LABELS[local]
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", local).replace("_", " ").strip()
    return s[:1].upper() + s[1:] if s else local


def _split_camel(s: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s).replace("_", " ").strip()


def human_url_label(value: str) -> str:
    """A short, human-readable label for a URL / IRI fact value.

    Pointer-style facts (a licence URL, a ``schema.org`` type, a repo or DOI
    link) otherwise show their raw URL, which reads poorly. This collapses them
    to the meaningful tail — ``http://schema.org/SoftwareSourceCode`` →
    ``Software source code``, ``…/licenses/Apache-2.0.html`` → ``Apache-2.0``,
    ``https://github.com/sdsc-ordes/gimie`` → ``sdsc-ordes/gimie``. Non-URL
    values (and anything already short) pass straight through, so it's safe to
    wrap every fact value with it.
    """
    v = (value or "").strip()
    if not v:
        return v
    # Ontology / schema.org term → humanised local name (also catches fragment
    # IRIs like ``…/ontology#EducationalResource``).
    if re.match(r"^https?://(www\.)?schema\.org/", v, re.I) or (
        re.match(r"^https?://", v, re.I) and "#" in v
    ):
        local = re.split(r"[#/]", v.rstrip("/"))[-1]
        s = _split_camel(local)
        return (s[:1].upper() + s[1:]) if s else local
    if not re.match(r"^https?://", v, re.I):
        return v  # not a URL — leave as-is
    # Wikidata entity (e.g. a discipline) → its memoised English label. Pure
    # cache read; warmed during resolution (see build_entity) and by the
    # facets panel. Falls back to the bare Q-id when not yet resolved.
    wd = re.search(r"wikidata\.org/(?:entity|wiki)/(Q\d+)", v, re.I)
    if wd:
        from .. import facets  # lazy — avoid an import cycle at module load
        return facets.cached_qid_label(wd.group(1)) or wd.group(1)
    stripped = re.sub(r"^https?://(www\.)?", "", v, flags=re.I).rstrip("/")
    host = stripped.split("/", 1)[0].lower()
    rest = stripped[len(host):].lstrip("/")
    if "spdx.org" in host:  # licenses/Apache-2.0.html → Apache-2.0
        return re.sub(r"\.(html|json)$", "", rest.split("/")[-1], flags=re.I) or stripped
    if host in ("github.com", "gitlab.com"):
        return rest or host  # owner/repo or owner login
    if host in ("doi.org", "orcid.org", "ror.org", "openalex.org"):
        return rest or stripped
    seg = rest.split("/")[-1] if rest else ""
    return seg or stripped


# Edge sectioning — group the harmonised graph edges into the page's
# "Related" sub-sections, primarily by relation verb (the controlled
# vocabulary), then by neighbour kind as a fallback. ``people`` deliberately
# unifies "contributed by" (Neo4j) and "authored by" (RDF) — see _CANON_REL.
_SECTION_BY_REL: dict[str, str] = {
    "contributed by": "people", "authored by": "people", "created by": "people",
    "contributed": "people", "maintained by": "people", "has member": "people",
    "owned by": "orgs", "affiliated with": "orgs", "member of": "orgs",
    "published by": "orgs", "funded by": "orgs",
    "owns": "repos", "contributes to": "repos", "fork of": "repos",
    "forked from": "repos", "part of": "repos",
    "cites": "works", "references": "works", "based on": "works",
}
_SECTION_BY_KIND: dict[str, str] = {
    "Person": "people", "User": "people",
    "Org": "orgs", "Repo": "repos", "Publication": "works",
}

# Equivalent relation verbs the two stores phrase differently — canonicalised
# so they read consistently and corroborate when they point at the same target.
_CANON_REL: dict[str, str] = {
    "authored by": "contributed by",
    "contributed": "contributed by",
}


def _section_for(relation: str, kind: str) -> str:
    return _SECTION_BY_REL.get(relation) or _SECTION_BY_KIND.get(kind, "other")


# Object-property predicates whose object is another entity — surfaced as
# graph edges (like Neo4j neighbours) rather than key/value rows. Maps the
# predicate local name to (relation verb, neighbour kind).
_RELATION_PREDICATES: dict[str, tuple[str, str]] = {
    "author": ("authored by", "Person"),
    "creator": ("created by", "Person"),
    "contributor": ("contributed by", "Person"),
    "maintainer": ("maintained by", "Person"),
    "contributionTo": ("contributes to", "Repo"),
    "owns": ("owns", "Repo"),
    "ownedBy": ("owned by", "User"),
    "owned_repositories": ("owns", "Repo"),
    "affiliation": ("affiliated with", "Org"),
    "memberOf": ("member of", "Org"),
    "publisher": ("published by", "Org"),
    "funder": ("funded by", "Org"),
    "isPartOf": ("part of", ""),
    # Cited / referenced works — usually DOI IRIs that resolve to a
    # publication entity, so surface them as graph edges rather than a
    # wall of external DOI links in the Facts table.
    "citation": ("cites", "Publication"),
    "cites": ("cites", "Publication"),
    "references": ("references", "Publication"),
    "isBasedOn": ("based on", "Publication"),
}

# Hosts that resolve to a hub entity page — these get an in-hub link from an
# RDF object IRI; everything else stays an external link (still a graph edge).
# doi.org / openalex.org / orcid.org resolve via the publication + author
# stores (DuckDB ``works`` / ``infoscience_articles`` / ``authors`` are keyed
# on the full DOI / OpenAlex / ORCID URL).
_RESOLVABLE_HOSTS = frozenset({
    "github.com", "gitlab.com", "zenodo.org", "ror.org",
    "infoscience.epfl.ch", "huggingface.co",
    "doi.org", "openalex.org", "orcid.org",
})

# Cap RDF-derived graph edges so an org that ``owns`` thousands of repos can't
# flood the page (the DESCRIBE returns one triple per owned repo).
_RDF_NEIGHBOUR_CAP = 40


def facts_from_bindings(bindings: Iterable[dict]) -> list[Fact]:
    """Render literal ``?p ?o`` rows as RDF Fact entries.

    Object IRIs whose predicate denotes a relationship (author, owns, …) are
    skipped here — they're surfaced as graph edges by
    :func:`neighbours_from_bindings` instead, unifying RDF and Neo4j display.
    Multiple values for the same predicate are kept as separate rows so the
    page preserves their original order.
    """
    out: list[Fact] = []
    for row in bindings:
        p = row.get("p", {}).get("value", "")
        o_node = row.get("o") or {}
        value = o_node.get("value", "")
        if not p or not value:
            continue
        is_uri = o_node.get("type") == "uri"
        if is_uri and _predicate_localname(p) in _RELATION_PREDICATES:
            continue  # → graph edge, not a fact row
        href = value if is_uri and value.startswith("http") else ""
        out.append(Fact(label=humanize_predicate(p), value=value, href=href, source="rdf"))
    return out


def neighbours_from_bindings(bindings: Iterable[dict]) -> list[Neighbour]:
    """Surface RDF object-properties (author, owns, contributionTo, …) as
    graph edges, so triples like ``schema:author`` render alongside the Neo4j
    ``contributed by`` edges with an "RDF" source tag."""
    out: list[Neighbour] = []
    seen: set[tuple[str, str]] = set()
    for row in bindings:
        if len(out) >= _RDF_NEIGHBOUR_CAP:
            break
        p = row.get("p", {}).get("value", "")
        o_node = row.get("o") or {}
        value = o_node.get("value", "")
        if not p or not value or o_node.get("type") != "uri":
            continue
        mapped = _RELATION_PREDICATES.get(_predicate_localname(p))
        if not mapped:
            continue
        relation, kind = mapped
        ref = parse_ref(value)
        if ref.host in _RESOLVABLE_HOSTS:
            hub_url = "/hub/" + ref.display
            label = ref.display
        else:
            hub_url = ""
            label = value
        external = value if value.startswith("http") else ""
        key = (relation, value)
        if key in seen:
            continue
        seen.add(key)
        out.append(Neighbour(
            label=label, relation=relation, hub_url=hub_url,
            external_url=external, kind=kind, source_type="RDF",
            sources=("RDF",),
        ))
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
                source_type="Neo4j",
                sources=("Neo4j",),
            )
        )
    return out


def _merge_facts(facts: list[Fact]) -> list[Fact]:
    """Collapse facts sharing an exact (label, value) into one row, unioning
    their provenance tags so corroboration across stores reads as a single
    multi-source fact. Genuinely different values for the same label stay as
    separate rows (so a SPARQL/Neo4j conflict is shown, not hidden). Order is
    first-seen."""
    order: list[tuple[str, str]] = []
    by_key: dict[tuple[str, str], Fact] = {}
    for f in facts:
        key = (f.label, f.value)
        srcs = list(f.sources) or ([f.source] if f.source else [])
        if key in by_key:
            ex = by_key[key]
            merged = list(ex.sources or ([ex.source] if ex.source else []))
            for s in srcs:
                if s and s not in merged:
                    merged.append(s)
            by_key[key] = replace(ex, sources=tuple(merged),
                                  source=merged[0] if merged else ex.source)
        else:
            uniq = tuple(dict.fromkeys(s for s in srcs if s))
            by_key[key] = replace(f, sources=uniq)
            order.append(key)
    return [by_key[k] for k in order]


def _merge_neighbours(neighbours: list[Neighbour]) -> list[Neighbour]:
    """Collapse edges pointing at the same target into one, unioning the graph
    stores that assert them — so an ``owned by`` edge known to both Neo4j and
    the RDF store reads as one corroborated edge tagged with both.

    The identity is ``(relation, hub_url | external_url | label)`` rather than
    the display label, because Neo4j carries the human name ("Swiss Data
    Science Center") while the RDF store carries the URL slug
    ("github.com/sdsc-ordes") for the *same* entity — keying on the resolved
    target lets them merge, and the first (Neo4j) row keeps its richer label.
    """
    order: list[tuple[str, str]] = []
    by_key: dict[tuple[str, str], Neighbour] = {}
    for n in neighbours:
        # Canonicalise equivalent verbs (authored by → contributed by) so the
        # two stores' phrasings collapse and corroborate on a shared target.
        rel = _CANON_REL.get(n.relation, n.relation)
        ident = n.hub_url or n.external_url or n.label
        key = (rel, ident)
        srcs = list(n.sources) or ([n.source_type] if n.source_type else [])
        if key in by_key:
            ex = by_key[key]
            merged = list(ex.sources or ([ex.source_type] if ex.source_type else []))
            for s in srcs:
                if s and s not in merged:
                    merged.append(s)
            by_key[key] = replace(ex, sources=tuple(merged))
        else:
            uniq = tuple(dict.fromkeys(s for s in srcs if s))
            by_key[key] = replace(
                n, relation=rel, sources=uniq,
                category=_section_for(rel, n.kind),
            )
            order.append(key)
    return [by_key[k] for k in order]


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
    rdf_neighbours = neighbours_from_bindings(bindings)
    _emit(on_status, f"SPARQL: {len(facts)} facts, {len(rdf_neighbours)} edges")

    _emit(on_status, "Querying Neo4j for 1-hop neighbours")
    neighbours = neighbours_from_neo4j(canonical, slug=canonical_ref.path)
    _emit(on_status, f"Neo4j: {len(neighbours)} neighbours")

    # Harmonise the two graphs: Neo4j edges first, then RDF edges, collapsed
    # on (relation, label) so an edge both stores assert reads as one
    # corroborated row tagged with both ("Neo4j" + "RDF") rather than appearing
    # twice or hiding the agreement.
    neighbours = _merge_neighbours([*neighbours, *rdf_neighbours])

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

    # Harmonise SPARQL (rdf) + Qdrant-payload (index) facts: identical
    # (label, value) pairs collapse into one row tagged with every store that
    # carries them; differing values for the same label stay as distinct rows
    # so a real conflict surfaces instead of being silently dropped.
    facts = _merge_facts([*facts, *qdrant_facts])

    # Warm Wikidata labels for any discipline-style entity values so the facts
    # card can show human names (e.g. Q428691 → "computer engineering") instead
    # of bare Q-ids. Batched + memoised; the render path only reads the cache.
    qids = []
    for f in facts:
        m = re.search(r"wikidata\.org/(?:entity|wiki)/(Q\d+)", f.value)
        if m:
            qids.append(m.group(1))
    if qids:
        try:
            from .. import facets as _facets

            _facets.warm_qid_labels(qids)
        except Exception:  # noqa: BLE001
            pass

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


def attach_qdrant_data(entity: Entity, collections: list[str], ref: HubRef) -> Entity:
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
    """Try schema:name / schema:headline / rdfs:label for a title.

    Scoped to the pinned named graph when one is active (same per-request
    selection that scopes :func:`stores.sparql_describe`)."""
    from ..stores import get_active_graph

    aliases = " ".join(f"<{u}>" for u in url_aliases(canonical_url))
    pattern = "?s (schema:name|schema:headline|rdfs:label) ?label"
    graph = get_active_graph()
    where = (
        f"GRAPH <{graph}> {{ {pattern} }}" if graph else pattern
    )
    return (
        "PREFIX schema: <http://schema.org/> "
        "PREFIX rdfs:   <http://www.w3.org/2000/01/rdf-schema#> "
        f"SELECT ?label WHERE {{ VALUES ?s {{ {aliases} }} {where} }} LIMIT 1"
    )


def first_label(canonical_url: str) -> str:
    rows = sparql_select(sparql_label_query(canonical_url))
    if not rows:
        return ""
    return rows[0].get("label", {}).get("value", "")
