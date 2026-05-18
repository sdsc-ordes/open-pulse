"""Dataclasses every resolver returns and the template renders.

The contract is intentionally narrow: a resolver fills as many panels
as it has data for and leaves the rest empty. The template iterates
over what's present and skips empty sections, so adding a new resolver
is just a matter of populating the relevant fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Fact:
    """A single key/value row in the facts panel.

    ``href`` is set when the value is itself navigable inside the hub
    (e.g. a license URL → /hub/spdx.org/...) or to an external site.
    ``value_list`` is set for multi-valued facts (keywords, subjects,
    authors) so the template can render them as chips instead of a
    comma-joined string. When set, ``value`` is the fallback prose
    rendering for downstream consumers.
    ``value_links`` upgrades the chips from plain text into clickable
    ``<a>`` chips — one (label, hub_url) per chip. Used for fields
    where each item resolves to its own entity (publication's authors
    → person hub pages, lab → organization hub page, etc.).
    """

    label: str
    value: str
    href: str = ""
    value_list: tuple[str, ...] = ()
    value_links: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Neighbour:
    """One incoming/outgoing edge surfaced from the graph store.

    ``hub_url`` is the in-hub link if the neighbour's identifier maps
    to a known host; otherwise the renderer just shows the label.
    ``kind`` is the neighbour's node label in Neo4j (``Repo``, ``User``,
    ``Org``, …) so the template can pick an icon / source tag.
    """

    label: str
    relation: str
    hub_url: str = ""
    external_url: str = ""
    kind: str = ""
    source_type: str = "GitHub"


@dataclass(frozen=True)
class Mention:
    """A retrieved chunk surfaced from the vector store.

    Used both by the narrative generator (as context) and by the
    "Mentions" panel on the page when no LLM is configured.
    """

    text: str
    source_url: str = ""
    source_label: str = ""
    collection: str = ""
    score: float = 0.0


@dataclass(frozen=True)
class BackLinkItem:
    """One record in another collection that references this entity."""

    label: str
    hub_url: str
    external_url: str = ""


@dataclass(frozen=True)
class RelatedItem:
    """One 'related' card: a sibling repo / record close to this one."""

    label: str
    hub_url: str
    external_url: str = ""
    badge: str = ""
    """One-line meta (e.g. ``1.2k stars · Rust``) shown under the link."""
    source_type: str = ""
    """Human label for the upstream source (``GitHub``, ``Zenodo``,
    ``HuggingFace``, ``ROR``, ``Infoscience``, ``OpenAlex``, …) so a
    visitor can tell at a glance which collection the item is from."""


@dataclass(frozen=True)
class RelatedGroup:
    """Sibling entities grouped by relationship reason.

    Title example: ``More from sdsc-ordes`` or
    ``Other Rust repositories``. Each item navigates to another
    /hub page in the same host, deepening the graph traversal.
    """

    title: str
    items: list[RelatedItem] = field(default_factory=list)


@dataclass(frozen=True)
class ActivityStats:
    """GrimoireLab / OpenSearch activity rollup for a repo URL.

    Empty/None values render as ``—`` so the panel still has a
    consistent shape when OpenSearch is unreachable for one specific
    metric.
    """

    total_commits: int = 0
    contributors: int = 0
    last_commit_date: str = ""
    first_commit_date: str = ""
    active_months: int = 0
    monthly: tuple[tuple[str, int], ...] = ()
    """Commits per month for the recent past — list of
    ``(yyyy-mm, count)`` tuples in chronological order. Drives the
    sparkline in the Activity panel."""


@dataclass(frozen=True)
class BackLinkGroup:
    """Records in a single collection that reference this entity.

    The hub graph payoff: when viewing a GitHub repo, this panel can
    show "5 Infoscience publications cite this", "2 Zenodo deposits
    link to it", etc. Each item navigates to another /hub page so the
    visitor can traverse the network.
    """

    collection: str
    label: str
    items: list[BackLinkItem] = field(default_factory=list)
    truncated: bool = False
    """True when more matches existed beyond the per-collection cap."""


@dataclass
class Entity:
    """Everything one /hub page renders.

    Empty fields render as collapsed/hidden sections, so a partial
    resolver doesn't leave gaping holes — it just shows fewer panels.
    """

    ref_url: str
    """Canonical URL of the page (matches HubRef.canonical_url)."""

    host: str
    """Canonical host (matches HubRef.host)."""

    title: str = ""
    """Display title — repo full name, deposit title, org name, ..."""

    subtitle: str = ""
    """One-line context shown under the title."""

    kind: str = ""
    """Short type label: "GitHub repo", "Zenodo record", "ROR org", ..."""

    description: str = ""
    """Long-form description from the store, if any."""

    identifiers: list[Fact] = field(default_factory=list)
    """Cross-host IDs: DOI, ORCID, ROR, schema:sameAs, ..."""

    facts: list[Fact] = field(default_factory=list)
    """Headline metadata: license, language, last update, ..."""

    neighbours: list[Neighbour] = field(default_factory=list)
    """1-hop edges from Neo4j / SPARQL."""

    mentions: list[Mention] = field(default_factory=list)
    """Retrieved chunks from gme-qdrant."""

    backlinks: list[BackLinkGroup] = field(default_factory=list)
    """Cross-collection references: records in OTHER collections that
    cite this URL. Grouped by collection so the renderer can show
    'N publications cite this · M deposits link to it' style."""

    narrative: str = ""
    """LLM-generated summary. Empty when no agent is configured."""

    sources: list[Fact] = field(default_factory=list)
    """Which stores answered (sparql / neo4j / qdrant / agent)."""

    enriched: bool = True
    """False for resolver stubs — flips on the "not enriched yet" notice."""
