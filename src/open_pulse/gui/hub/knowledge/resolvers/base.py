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
import logging
import re
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from dataclasses import replace
from typing import Any

from ..agent import narrate
from ..entity import Entity, Fact, Neighbour
from ..normalize import HubRef, parse_ref
from ..qdrant import lookup_for_ref
from ..stores import sparql_describe, sparql_select

log = logging.getLogger(__name__)

# Process-lifetime cache for the agent narrative: composing a
# narrative is a 1–30s call, but the inputs (facts hash) rarely
# change inside a single hub session.
_NARRATIVE_CACHE: dict[str, str] = {}
_NARRATIVE_CACHE_MAX = 256

# When set, ``build_entity`` skips the (slow, 1–30s) LLM narrative so the
# page body can render immediately. The SSE endpoint sets this, ships the
# body, then composes the narrative separately and streams it in. Defaults
# off so non-streaming callers still get a narrative inline.
_SKIP_NARRATIVE: ContextVar[bool] = ContextVar("skip_narrative", default=False)


def set_skip_narrative(value: bool) -> None:
    """Toggle deferral of the LLM narrative for the current context."""
    _SKIP_NARRATIVE.set(bool(value))


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


def probe_sparql(canonical_url: str, *, scoped: bool = True) -> list[dict]:
    """Try every URL alias until something comes back from SPARQL.

    ``scoped`` honours the pinned named graph (default); ``scoped=False`` always
    queries the union across all graphs — used for relationship edges so a
    contributor/owner doesn't disappear when a graph that lacks them is pinned.
    """
    for alias in url_aliases(canonical_url):
        bindings = sparql_describe(alias, scoped=scoped)
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

# Equivalent edges the two stores phrase differently collapse onto one concept
# so the same target merges into a single corroborated row. The *display* verb
# then prefers the RDF wording (see _merge_neighbours) — e.g. a person both
# Neo4j-"contributed by" and RDF-"authored by" merges and shows "authored by".
_REL_CONCEPT: dict[str, str] = {
    "contributed by": "contributor", "authored by": "contributor",
    "contributed": "contributor",
    "owned by": "owner", "owns": "owns",
    "member of": "memberof", "has member": "member",
    "affiliated with": "affiliation", "published by": "publisher",
    "funded by": "funder",
    "fork of": "fork", "forked from": "fork",
    "contributes to": "contributesto", "part of": "partof",
    "created by": "creator", "maintained by": "maintainer",
    "cites": "cites", "references": "references", "based on": "basedon",
}


def _section_for(relation: str, kind: str) -> str:
    return _SECTION_BY_REL.get(relation) or _SECTION_BY_KIND.get(kind, "other")


# Thematic grouping of the Facts card into separate cards. Matched by keyword
# against the (humanised, lower-cased) fact label, first match wins; anything
# unmatched lands in a trailing "Details" card. Order here is display order.
_FACT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Classification", (
        "type", "repositor", "discipline", "licen", "languag", "topic",
        "keyword", "resource", "access", "pipeline", "librar", "categor",
        "sdk", "framework", "tag", "badge",
    )),
    ("Metrics", (
        "issue", "watcher", "subscriber", "follow", "public repo", "size",
        "network", "download", "view", "contribution", "star", "fork",
        "commit", "release", "count", "members",
    )),
    ("Timeline", (
        "creat", "updat", "push", "modif", "date", "publish", "last ",
    )),
)


def fact_groups(facts: list[Fact]) -> list[tuple[str, list[Fact]]]:
    """Bucket facts into thematic groups (Classification / Metrics / Timeline /
    Details) so the page can render one card per group instead of one long
    table. Empty groups are dropped; order is stable."""
    buckets: dict[str, list[Fact]] = {name: [] for name, _ in _FACT_GROUPS}
    details: list[Fact] = []
    for f in facts:
        low = (f.label or "").lower()
        for name, kws in _FACT_GROUPS:
            if any(k in low for k in kws):
                buckets[name].append(f)
                break
        else:
            details.append(f)
    out = [(name, buckets[name]) for name, _ in _FACT_GROUPS if buckets[name]]
    if details:
        out.append(("Details", details))
    return out


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

# Predicates whose values are opaque internal IDs (content-hash references to
# sub-objects the store doesn't expose as triples) — never worth showing as a
# fact. ``releases`` lists one md5-ish id per release; ``badges`` does the same
# per badge. The human-readable info lives in ``latest_version`` / ``git_tags``
# and ``badge_image_urls`` respectively. ``badge_count`` is redundant once the
# badges render.
_NOISE_PREDICATES = frozenset({"releases", "badges", "badge_count"})


def facts_from_bindings(bindings: Iterable[dict]) -> list[Fact]:
    """Render literal ``?p ?o`` rows as RDF Fact entries.

    Object IRIs whose predicate denotes a relationship (author, owns, …) are
    skipped here — they're surfaced as graph edges by
    :func:`neighbours_from_bindings` instead, unifying RDF and Neo4j display.
    Opaque-id predicates (:data:`_NOISE_PREDICATES`) are dropped entirely.
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
        local = _predicate_localname(p)
        if local in _NOISE_PREDICATES:
            continue  # opaque hash id — no display value
        is_uri = o_node.get("type") == "uri"
        if is_uri and local in _RELATION_PREDICATES:
            continue  # → graph edge, not a fact row
        href = value if is_uri and value.startswith("http") else ""
        out.append(Fact(label=humanize_predicate(p), value=value, href=href, source="rdf"))
    return out


# Fact labels (humanised) whose value is a git tag / version we can deep-link
# to the GitHub release page for that tag.
_RELEASE_TAG_LABELS = frozenset({"Git tags", "Latest version"})


def _link_release_tags(facts: list[Fact], slug: str) -> list[Fact]:
    """For a github repo, turn release-tag facts into links to the matching
    GitHub release page (``…/releases/tag/<tag>``). ``Git tags`` get an ``href``
    so the aggregator renders them as linked chips; ``Latest version`` becomes a
    single linked chip directly."""
    out: list[Fact] = []
    for f in facts:
        v = (f.value or "").strip()
        if (
            f.label not in _RELEASE_TAG_LABELS
            or not v or v.startswith("http")
            or f.value_links or f.value_list
        ):
            out.append(f)
            continue
        url = f"https://github.com/{slug}/releases/tag/{v}"
        if f.label == "Latest version":
            out.append(replace(f, value_links=((v, url),)))
        else:  # Git tags — let the aggregator collapse them into linked chips
            out.append(replace(f, href=url))
    return out


# Badge / status images live inside markdown blobs (a user/org ``profile_readme``,
# a repo README) as ``[![alt](img)](link)`` or ``<img src>`` — not as their own
# triples. These extract the badge image URLs (+ their click target) so they
# render as actual badges instead of a wall of raw markdown.
_MD_LINKED_IMG = re.compile(r"\[!\[[^\]]*\]\(\s*([^)\s]+)[^)]*\)\]\(\s*([^)\s]+)")
_MD_IMG = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)")
_HTML_IMG = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)


def _is_badge_url(url: str) -> bool:
    ul = (url or "").lower()
    return (
        "shields.io" in ul or "badgen.net" in ul or "badge.fury" in ul
        or "forthebadge" in ul or ul.endswith(".svg") or "badge" in ul
    )


def _badge_label(url: str) -> str:
    """A short, reliable label for a badge — derived from its own image URL
    (host / workflow file), since the store's ``badge_labels`` can't be aligned
    to specific images. Used as the badge's hover tooltip + alt text."""
    u = (url or "").lower()
    if "coveralls" in u or "codecov" in u or "coverage" in u:
        return "Coverage"
    if "badge.fury" in u or "/pypi/" in u or "pypistats" in u or "pepy" in u:
        return "PyPI"
    if "readthedocs" in u or "sphinx" in u or "/docs" in u:
        return "Docs"
    if "license" in u:
        return "License"
    m = re.search(r"/workflows/([^/]+?)\.ya?ml", url or "")
    if m:
        return m.group(1).replace("-", " ").replace("_", " ").title()
    # shields.io/badge/<label>-<message>-<color> → just the label
    m = re.search(r"shields\.io/badge/([^?/]+)", url or "")
    if m:
        import urllib.parse
        seg = urllib.parse.unquote(m.group(1))
        seg = re.sub(r"-[0-9A-Fa-f]{3,8}$", "", seg)  # drop trailing hex colour
        seg = seg.split("-", 1)[0].replace("_", " ").strip()
        if seg:
            return seg[:40]
    if "actions" in u or "/test" in u or "pytest" in u or "ci" in u:
        return "CI"
    seg = re.sub(r"\?.*$", "", url or "").rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"\.(svg|png|jpg)$", "", seg, flags=re.I) or "badge"


def _host(url: str) -> str:
    return re.sub(r"^https?://", "", (url or "").lower()).split("/", 1)[0]


def _match_badge_link(img: str, links: list[str]) -> str:
    """Pick the click target for a badge image from the available links —
    prefer one on the same host (coveralls↔coveralls, badge.fury↔badge.fury),
    else one sharing a distinctive path token (a workflow name), else none."""
    ih = _host(img)
    for k in links:  # exact host match wins
        if k and _host(k) == ih and ih:
            return k
    itoks = {t for t in re.split(r"[^a-z0-9]+", img.lower()) if len(t) > 3}
    best, best_score = "", 0
    for k in links:
        if not k:
            continue
        ktoks = {t for t in re.split(r"[^a-z0-9]+", k.lower()) if len(t) > 3}
        score = len(itoks & ktoks - {"github", "https", "actions", "workflows", "badge"})
        if score > best_score:
            best, best_score = k, score
    return best


def _structured_badges(facts: list[Fact]) -> list[Fact]:
    """Fold a repo's structured badge predicates into one rendered ``Badges``
    fact. ``badge_image_urls`` carries the images (the opaque ``badges`` hashes
    + ``badge_count`` are already dropped as noise); each image gets a label
    derived from its URL and a click target host-matched from ``badge_links``.
    The raw ``Badge image urls`` / ``Badge labels`` / ``Badge links`` rows are
    consumed. Only absolute-URL images are kept (relative repo assets like a
    logo or schema diagram aren't status badges)."""
    imgs = [f.value for f in facts if f.label == "Badge image urls"
            and str(f.value).startswith("http")]
    if not imgs:
        return facts
    links = [f.value for f in facts if f.label == "Badge links"]
    consumed = {"Badge image urls", "Badge labels", "Badge links"}
    out = [f for f in facts if f.label not in consumed]
    seen: set[str] = set()
    badges: list[tuple[str, str, str]] = []
    for img in imgs:
        if img in seen:
            continue
        seen.add(img)
        badges.append((img, _badge_label(img), _match_badge_link(img, links) or img))
    out.append(Fact(
        label="Badges", value=f"{len(badges)} badges",
        badges=tuple(badges[:16]), source="rdf",
    ))
    return out


def _extract_badges(facts: list[Fact]) -> list[Fact]:
    """Pull badge images out of markdown blob facts (profile_readme / readme)
    into a single ``Badges`` fact, and drop the unreadable raw blob. Each badge
    is ``(image_url, label, link_url)`` — the image links to its target
    (``[![alt](img)](link)``; unlinked → the image) with a URL-derived label."""
    out: list[Fact] = []
    badges: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for f in facts:
        v = f.value or ""
        is_md_blob = len(v) > 120 and ("![" in v or "<img" in v)
        if not is_md_blob:
            out.append(f)
            continue
        for img, link in _MD_LINKED_IMG.findall(v):
            if _is_badge_url(img) and img not in seen:
                seen.add(img)
                badges.append((img, _badge_label(img), link))
        rest = _MD_LINKED_IMG.sub("", v)
        for img in _MD_IMG.findall(rest) + _HTML_IMG.findall(v):
            if _is_badge_url(img) and img not in seen:
                seen.add(img)
                badges.append((img, _badge_label(img), img))
        # Drop the raw markdown blob fact (unreadable as a key/value row).
    if badges:
        out.append(Fact(
            label="Badges", value=f"{len(badges)} badges",
            badges=tuple(badges[:16]), source="rdf",
        ))
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
            sources=("RDF",), rdf_predicate=predicate_label(p),
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
                neo4j_rel=rel,
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


def _is_numeric(value: str) -> bool:
    return value.replace(",", "").replace(".", "", 1).isdigit()


def _aggregate_facts(facts: list[Fact]) -> list[Fact]:
    """Collapse same-label multi-value facts into a single chip row.

    A predicate that repeats (keywords, topics, disciplines, git tags,
    releases, package versions, …) reads as one fact rendered as chips rather
    than N near-identical rows. URL values become *linked* chips with a human
    label; plain literals become plain chips. All-numeric repeats (a metric the
    two stores disagree on) are left as separate rows so the discrepancy stays
    visible. Single-value facts pass through untouched."""
    order: list[str] = []
    grouped: dict[str, list[Fact]] = {}
    for f in facts:
        if f.value_list or f.value_links:  # already a chip fact — keep as-is
            order.append(f"\x00keep\x00{len(order)}")
            grouped[order[-1]] = [f]
            continue
        grouped.setdefault(f.label, []).append(f)
        if f.label not in order:
            order.append(f.label)

    out: list[Fact] = []
    for key in order:
        fs = grouped[key]
        # de-dup identical values, preserve order
        seen: set[str] = set()
        items = [f for f in fs if not (f.value in seen or seen.add(f.value))]
        if len(items) == 1 or all(_is_numeric(str(f.value)) for f in items):
            out.extend(items)
            continue
        srcs: list[str] = []
        for f in items:
            for s in (f.sources or ([f.source] if f.source else [])):
                if s and s not in srcs:
                    srcs.append(s)
        label = items[0].label
        if any(f.href or str(f.value).startswith("http") for f in items):
            # Build (label, url) chips, deduped by the human label so a slug +
            # its full-URL variant ("owner/repo" + ".../owner/repo") collapse.
            seen_lbl: set[str] = set()
            links_list: list[tuple[str, str]] = []
            for f in items:
                lbl = human_url_label(f.value)
                if lbl in seen_lbl:
                    continue
                seen_lbl.add(lbl)
                links_list.append((lbl, f.href or f.value))
            links = tuple(links_list)
            out.append(Fact(
                label=label, value=", ".join(lbl for lbl, _ in links),
                value_links=links, source=srcs[0] if srcs else "", sources=tuple(srcs),
            ))
        else:
            vals = tuple(str(f.value) for f in items)
            out.append(Fact(
                label=label, value=", ".join(vals), value_list=vals,
                source=srcs[0] if srcs else "", sources=tuple(srcs),
            ))
    return out


# Fact labels whose value is a project URL worth resolving to a page title.
_TITLE_FACT_LABELS = frozenset({"homepage", "Homepage", "url", "URL", "Homepage url"})
_PAGE_TITLES: dict[str, str] = {}


def _page_title(url: str) -> str:
    """The ``<title>`` of an HTML page, memoised. Best-effort: short timeout,
    capped read, empty string on any failure (caller falls back to the URL)."""
    if url in _PAGE_TITLES:
        return _PAGE_TITLES[url]
    title = ""
    try:
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": "open-pulse-hub/1.0"})
        with urllib.request.urlopen(req, timeout=4) as r:  # noqa: S310
            ctype = r.headers.get("Content-Type", "")
            if "html" in ctype.lower() or not ctype:
                html = r.read(20000).decode("utf-8", "replace")
                m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
                if m:
                    title = re.sub(r"\s+", " ", m.group(1)).strip()[:80]
    except Exception as exc:  # noqa: BLE001
        log.info("page title fetch failed (%s): %s", url, exc)
    _PAGE_TITLES[url] = title
    return title


def _enrich_url_titles(facts: list[Fact]) -> list[Fact]:
    """For homepage-style pointer facts, fetch the page title and render it as a
    linked chip (``title → url``) so the value reads as a name, not a bare URL."""
    out: list[Fact] = []
    for f in facts:
        if (
            f.label in _TITLE_FACT_LABELS
            and not f.value_links and not f.value_list
            and str(f.value).startswith("http")
        ):
            title = _page_title(str(f.value))
            if title:
                out.append(replace(f, value_links=((title, str(f.value)),)))
                continue
        out.append(f)
    return out


def _is_slug_label(label: str) -> bool:
    """True for a URL-ish / ``owner/repo`` label — i.e. not a human name."""
    return (not label) or label.startswith("http") or "/" in label


def _merge_neighbours(neighbours: list[Neighbour]) -> list[Neighbour]:
    """Collapse edges pointing at the same target into one corroborated row.

    Edges merge on ``(concept, target)`` where *concept* unifies the two
    stores' phrasings (Neo4j "contributed by" ≡ RDF "authored by") and *target*
    is the resolved ``hub_url`` / ``external_url`` (Neo4j carries the human name
    while RDF carries the URL slug for the *same* entity). The merged row:

    * **displays the RDF verb** when an RDF edge contributed it (the user wants
      the RDF vocabulary surfaced), else the Neo4j verb;
    * keeps the richest label (a human name over a slug);
    * carries every store in ``sources`` plus the raw ``neo4j_rel`` /
      ``rdf_predicate`` for the per-chip tooltips.
    """
    order: list[str] = []
    acc: dict[str, dict[str, Any]] = {}
    for n in neighbours:
        concept = _REL_CONCEPT.get(n.relation, n.relation)
        ident = n.hub_url or n.external_url or n.label
        key = f"{concept}\x00{ident}"
        is_rdf = n.source_type == "RDF" or "RDF" in n.sources
        a = acc.get(key)
        if a is None:
            a = {
                "base": n, "sources": [], "neo4j_rel": "", "rdf_predicate": "",
                "rdf_rel": "", "neo4j_rel_label": "", "label": n.label,
                "hub_url": n.hub_url, "external_url": n.external_url, "kind": n.kind,
                "orcid_url": "", "ror_url": "",
            }
            acc[key] = a
            order.append(key)
        for s in (list(n.sources) or ([n.source_type] if n.source_type else [])):
            if s and s not in a["sources"]:
                a["sources"].append(s)
        a["neo4j_rel"] = a["neo4j_rel"] or n.neo4j_rel
        a["rdf_predicate"] = a["rdf_predicate"] or n.rdf_predicate
        if is_rdf:
            a["rdf_rel"] = a["rdf_rel"] or n.relation
        else:
            a["neo4j_rel_label"] = a["neo4j_rel_label"] or n.relation
        a["hub_url"] = a["hub_url"] or n.hub_url
        a["external_url"] = a["external_url"] or n.external_url
        a["kind"] = a["kind"] or n.kind
        a["orcid_url"] = a["orcid_url"] or n.orcid_url
        a["ror_url"] = a["ror_url"] or n.ror_url
        # Prefer a human name over a URL-ish slug.
        if _is_slug_label(a["label"]) and not _is_slug_label(n.label):
            a["label"] = n.label
    out: list[Neighbour] = []
    for key in order:
        a = acc[key]
        rel = a["rdf_rel"] or a["neo4j_rel_label"] or a["base"].relation
        out.append(replace(
            a["base"], relation=rel, label=a["label"],
            hub_url=a["hub_url"], external_url=a["external_url"],
            sources=tuple(a["sources"]),
            neo4j_rel=a["neo4j_rel"], rdf_predicate=a["rdf_predicate"],
            orcid_url=a["orcid_url"], ror_url=a["ror_url"],
            category=_section_for(rel, a["kind"]),
        ))
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
    # Relationship edges (contributors / owners / cited works) are resolved from
    # the union across all graphs — not the pinned graph — so they don't vanish
    # when a graph that lacks them is pinned (the descriptive facts above stay
    # graph-scoped). When no graph is pinned the scoped probe is already the
    # union, so reuse it instead of querying twice.
    from ..stores import get_active_graph  # local — keep import lazy

    union_bindings = probe_sparql(canonical, scoped=False) if get_active_graph() else bindings
    rdf_neighbours = neighbours_from_bindings(union_bindings)
    _emit(on_status, f"SPARQL: {len(facts)} facts, {len(rdf_neighbours)} edges")

    _emit(on_status, "Querying Neo4j for 1-hop neighbours")
    neighbours = neighbours_from_neo4j(canonical, slug=canonical_ref.path)
    _emit(on_status, f"Neo4j: {len(neighbours)} neighbours")

    # Harmonise the two graphs: Neo4j edges first, then RDF edges, collapsed
    # on (relation, label) so an edge both stores assert reads as one
    # corroborated row tagged with both ("Neo4j" + "RDF") rather than appearing
    # twice or hiding the agreement.
    neighbours = _merge_neighbours([*neighbours, *rdf_neighbours])
    # Person identity bridge: an RDF author keyed by ORCID is the same person as
    # a Neo4j contributor keyed by github login when their names match (local
    # lookup, no API). Rewrite the matched ORCID authors onto the github
    # identity and re-merge so the contributor gets an RDF chip + ORCID link.
    try:
        from .. import identity as _identity

        # Identity harmonization, then one re-merge to collapse the rewritten
        # rows: people bridge an RDF ORCID author onto its Neo4j github
        # contributor; orgs bridge an RDF ROR institution onto its Neo4j github
        # org (same org, two identifiers) → one corroborated row carrying both
        # references. Works just relabel cited DOIs by title.
        harmonized = _identity.harmonize_people(neighbours)
        harmonized = _identity.harmonize_works(harmonized)
        harmonized = _identity.harmonize_orgs(harmonized)
        if harmonized != neighbours:
            neighbours = _merge_neighbours(harmonized)
        # Decorate contributors with their per-repo commit count + date range
        # (RDF Contribution nodes) — github repos only.
        if canonical_ref.host == "github.com" and "/" in canonical_ref.path:
            neighbours = _identity.attach_contributions(neighbours, canonical)
    except Exception as exc:  # noqa: BLE001
        log.info("identity harmonization skipped: %s", exc)

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

    # Deep-link git tags / latest version to their GitHub release page (github
    # repos only) before aggregation, so they collapse into clickable version
    # chips. Resolve a human page title for homepage-style pointer facts, then
    # aggregate same-label multi-value facts (keywords, tags, versions, …).
    if canonical_ref.host == "github.com" and "/" in canonical_ref.path:
        facts = _link_release_tags(facts, canonical_ref.path)
    facts = _structured_badges(facts)
    facts = _extract_badges(facts)
    facts = _enrich_url_titles(facts)
    facts = _aggregate_facts(facts)

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
    if _SKIP_NARRATIVE.get():
        # Deferred — the SSE endpoint will compose + stream it after the
        # body lands, so the page doesn't wait on the LLM call.
        pass
    elif (
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

    Scoped to the pinned named graph(s) when active (same per-request
    selection that scopes :func:`stores.sparql_describe`)."""
    from ..stores import _graph_scoped

    aliases = " ".join(f"<{u}>" for u in url_aliases(canonical_url))
    pattern = "?s (schema:name|schema:headline|rdfs:label) ?label"
    where = _graph_scoped(pattern)
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
