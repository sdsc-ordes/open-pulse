"""CHAOSS metrics computed live against the four open-pulse stores.

Each metric in this module is a tiny pipeline:

1. Build the query text for each store it touches (Neo4j / SPARQL /
   OpenSearch / Qdrant). The query text is kept around verbatim so it
   can be displayed on the page — the *transparency* property is the
   whole point of this section: a visitor must be able to see the
   exact statement that produced the number, copy it, and run it
   themselves in the ``/databases`` console.
2. Execute it.
3. Collapse the raw responses into a single ``MetricResult`` carrying
   the headline value, a secondary breakdown, an optional time
   series, and the trace of every query that ran (text + summary +
   error).

The compute functions are intentionally written in long form rather
than via a clever DSL — the verbosity is the point. Future authors
will read these to learn what a CHAOSS metric *means* and what the
open-pulse data plane looks like.

The metrics implemented here are the first five from the SDSC
"Level 0" list (see Open_Pulse_CHAOSS_Metrics.pdf, page 5):

* ``contributors`` – Community / windowed
* ``new_contributors`` – Community / windowed
* ``technical_fork`` – Popularity / snapshot
* ``licenses_declared`` – FAIR-quality / snapshot
* ``academic_impact`` – Popularity / hybrid (publications cite the
  software; the citation list is a snapshot but publication dates
  form a series)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..knowledge import opensearch as os_mod, qdrant, stores

log = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────────────

@dataclass
class QueryTrace:
    """One transparent record of how a metric reached its number.

    ``query`` carries the literal text. ``engine`` is the value the
    ``/databases`` console understands (``cypher`` / ``sparql`` /
    ``opensearch``) so the page can ship users straight there with a
    pre-filled editor. ``mode`` only applies to OpenSearch (``sql`` or
    ``dsl``).
    """

    store: str
    engine: str
    title: str
    query: str
    result_summary: str
    mode: str | None = None
    error: str | None = None


@dataclass
class MetricResult:
    slug: str
    value: str
    label: str
    secondary: str | None
    queries: list[QueryTrace]
    notes: str
    series: list[dict[str, Any]] = field(default_factory=list)
    examples: list[dict[str, str]] = field(default_factory=list)


@dataclass
class MetricSpec:
    """Static description of a CHAOSS metric this hub implements."""

    slug: str
    name: str
    category: str  # "Popularity" / "Community" / "FAIR-quality"
    chaoss_level: str  # "Level 0 – Must-have" / etc.
    chaoss_url: str
    question: str
    description: str
    is_time_based: bool
    compute: Callable[[str, str, int], MetricResult]


# ── Helpers ──────────────────────────────────────────────────────────────

def _now_minus_days(days: int) -> datetime:
    """The cutoff that bounds windowed metrics. ``days`` is the
    requested window length; we hand back an aware UTC datetime.
    """
    return datetime.now(timezone.utc) - timedelta(days=days)


def _iso(dt: datetime) -> str:
    """ISO-8601 cutoff string with explicit UTC offset — both Oxigraph
    and OpenSearch accept this form.
    """
    return dt.replace(microsecond=0).isoformat()


def _xsd_datetime(dt: datetime) -> str:
    """SPARQL literal for a date filter (``"..."^^xsd:dateTime``)."""
    return f'"{_iso(dt)}"^^xsd:dateTime'


# ── Metric 1 · Contributors ───────────────────────────────────────────────

def _metric_contributors(full: str, canonical_url: str, window_days: int) -> MetricResult:
    """How many distinct people contributed to this repo? Counted both
    over the configured time window (OpenSearch / SPARQL) and as an
    all-time community total (Neo4j).
    """
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []
    values: dict[str, int | None] = {"neo4j": None, "sparql": None, "opensearch": None}

    # ── Neo4j — distinct contributors all-time ────────────────────────
    cypher = (
        f"// Distinct users with an inbound CONTRIBUTES_TO edge into the\n"
        f"// repository. The crawler aggregates per-user contribution so\n"
        f"// this is an all-time count, not windowed.\n"
        f"MATCH (u:User)-[:CONTRIBUTES_TO]->(r:Repo {{full_name: '{full}'}})\n"
        f"RETURN count(DISTINCT u) AS contributors"
    )
    try:
        rows = stores.neo4j_run(cypher)
        n = int(rows[0].get("contributors") or 0) if rows else 0
        values["neo4j"] = n
        traces.append(QueryTrace(
            store="Neo4j", engine="cypher",
            title="All-time distinct contributors in the community graph",
            query=cypher,
            result_summary=f"{n} distinct users",
        ))
    except Exception as exc:  # noqa: BLE001
        traces.append(QueryTrace(
            store="Neo4j", engine="cypher",
            title="All-time distinct contributors in the community graph",
            query=cypher, result_summary="error", error=str(exc),
        ))

    # ── SPARQL — distinct contributors active in the window ──────────
    sparql = (
        "# Counts Person nodes whose Contribution to this repo had its\n"
        "# last activity inside the configured window — i.e. people who\n"
        "# touched the project recently.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "PREFIX pulse:  <https://open-pulse.epfl.ch/ontology#>\n"
        "PREFIX xsd:    <http://www.w3.org/2001/XMLSchema#>\n"
        "SELECT (COUNT(DISTINCT ?person) AS ?n) WHERE {\n"
        f'  ?repo pulse:githubRepositoryHandle "{full}" .\n'
        "  ?person pulse:hasContribution ?contrib .\n"
        "  ?contrib pulse:contributionTo ?repo ;\n"
        "           pulse:lastContributionDate ?last .\n"
        f"  FILTER(?last >= {_xsd_datetime(cutoff)})\n"
        "}"
    )
    try:
        rows = stores.sparql_select(sparql)
        n = int(rows[0]["n"]["value"]) if rows else 0
        values["sparql"] = n
        traces.append(QueryTrace(
            store="SPARQL", engine="sparql",
            title=f"Contributors with activity since {cutoff_iso[:10]}",
            query=sparql,
            result_summary=f"{n} distinct persons",
        ))
    except Exception as exc:  # noqa: BLE001
        traces.append(QueryTrace(
            store="SPARQL", engine="sparql",
            title=f"Contributors with activity since {cutoff_iso[:10]}",
            query=sparql, result_summary="error", error=str(exc),
        ))

    # ── OpenSearch — distinct git authors in the window ──────────────
    origin = f"https://github.com/{full}"
    body = {
        "size": 0,
        "query": {
            "bool": {
                "must": [
                    {"term": {"origin": origin}},
                    {"range": {"grimoire_creation_date": {"gte": cutoff_iso}}},
                ]
            }
        },
        "aggs": {
            "by_author": {
                "cardinality": {"field": "author_name.keyword"}
            }
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/git_*_enriched/_search", body)
    if raw is not None:
        n = int(((raw.get("aggregations") or {}).get("by_author") or {}).get("value") or 0)
        values["opensearch"] = n
        traces.append(QueryTrace(
            store="OpenSearch", engine="opensearch", mode="dsl",
            title=f"Cardinality of git authors on {origin} since {cutoff_iso[:10]}",
            query=body_text,
            result_summary=f"{n} distinct authors",
        ))
    else:
        traces.append(QueryTrace(
            store="OpenSearch", engine="opensearch", mode="dsl",
            title=f"Cardinality of git authors on {origin} since {cutoff_iso[:10]}",
            query=body_text, result_summary="no response", error="OpenSearch unreachable or empty",
        ))

    # Headline: prefer the largest observed non-zero value among the
    # windowed stores (OpenSearch / SPARQL), falling back to Neo4j's
    # all-time count. We avoid picking a 0 when another store has a
    # signal — a zero from GrimoireLab usually means "this repo isn't
    # indexed yet", not "this repo has no contributors".
    candidates = [
        ("opensearch", values["opensearch"], f"last {window_days} days · OpenSearch"),
        ("sparql",     values["sparql"],     f"last {window_days} days · SPARQL"),
        ("neo4j",      values["neo4j"],      "all-time · Neo4j community graph"),
    ]
    winner = next(((v, lbl) for _, v, lbl in candidates if v), None)
    if winner is None:
        # Every store either errored or returned None. Fall back to
        # whatever we *did* get, even if zero.
        observed = [(v, lbl) for _, v, lbl in candidates if v is not None]
        if not observed:
            return MetricResult(
                slug="contributors", value="—", label="no contributor data",
                secondary=None, queries=traces,
                notes="None of the three stores returned a value for this repo.",
            )
        winner = observed[0]
    headline, label = winner

    bits = []
    if values["neo4j"] is not None:
        bits.append(f"Neo4j (all-time): {values['neo4j']}")
    if values["sparql"] is not None:
        bits.append(f"SPARQL (windowed): {values['sparql']}")
    if values["opensearch"] is not None:
        bits.append(f"OpenSearch (windowed): {values['opensearch']}")

    return MetricResult(
        slug="contributors",
        value=str(headline),
        label=label,
        secondary=" · ".join(bits),
        queries=traces,
        notes=(
            "OpenSearch counts distinct ``author_name`` values on commits "
            "indexed by GrimoireLab in the window. SPARQL uses the typed "
            "``pulse:lastContributionDate`` on the Contribution node. "
            "Neo4j is an all-time edge count — it ignores the window."
        ),
    )


# ── Metric 2 · New Contributors ───────────────────────────────────────────

def _metric_new_contributors(full: str, canonical_url: str, window_days: int) -> MetricResult:
    """Contributors whose *first* contribution to this repo happened
    inside the window.
    """
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []
    values: dict[str, int | None] = {"sparql": None, "opensearch": None}
    examples: list[dict[str, str]] = []

    # ── SPARQL — first-contribution-date filter ──────────────────────
    sparql = (
        "# Persons whose Contribution to this repo has its FIRST activity\n"
        "# inside the window. The crawler stamps every Contribution node\n"
        "# with both first- and last- dates, so this is exact.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "PREFIX pulse:  <https://open-pulse.epfl.ch/ontology#>\n"
        "PREFIX xsd:    <http://www.w3.org/2001/XMLSchema#>\n"
        "SELECT ?login ?first WHERE {\n"
        f'  ?repo pulse:githubRepositoryHandle "{full}" .\n'
        "  ?person pulse:hasContribution ?contrib ;\n"
        "          pulse:githubUsername ?login .\n"
        "  ?contrib pulse:contributionTo ?repo ;\n"
        "           pulse:firstContributionDate ?first .\n"
        f"  FILTER(?first >= {_xsd_datetime(cutoff)})\n"
        "}\n"
        "ORDER BY ?first"
    )
    try:
        rows = stores.sparql_select(sparql)
        values["sparql"] = len(rows)
        examples.extend(
            {
                "label": (r.get("login") or {}).get("value", ""),
                "detail": (r.get("first") or {}).get("value", "")[:10],
                "source": "SPARQL",
            }
            for r in rows[:8]
        )
        traces.append(QueryTrace(
            store="SPARQL", engine="sparql",
            title=f"Persons whose first contribution is after {cutoff_iso[:10]}",
            query=sparql,
            result_summary=f"{len(rows)} new contributors",
        ))
    except Exception as exc:  # noqa: BLE001
        traces.append(QueryTrace(
            store="SPARQL", engine="sparql",
            title=f"Persons whose first contribution is after {cutoff_iso[:10]}",
            query=sparql, result_summary="error", error=str(exc),
        ))

    # ── OpenSearch — terms agg + min(date) sub-agg, filter in Python ──
    # bucket_selector / having would push the filter into the cluster
    # but isn't always enabled; doing it client-side keeps this code
    # readable and the response payload tiny (one bucket per author).
    origin = f"https://github.com/{full}"
    body = {
        "size": 0,
        "query": {"term": {"origin": origin}},
        "aggs": {
            "by_author": {
                "terms": {"field": "author_name.keyword", "size": 1000},
                "aggs": {
                    "first_commit": {"min": {"field": "grimoire_creation_date"}}
                },
            }
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/git_*_enriched/_search", body)
    if raw is not None:
        buckets = (raw.get("aggregations") or {}).get("by_author", {}).get("buckets", [])
        cutoff_ms = int(cutoff.timestamp() * 1000)
        new = [b for b in buckets if (b.get("first_commit", {}).get("value") or 0) >= cutoff_ms]
        values["opensearch"] = len(new)
        for b in new[:8]:
            ts = int(b.get("first_commit", {}).get("value") or 0) // 1000
            examples.append({
                "label": b.get("key", ""),
                "detail": datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat(),
                "source": "OpenSearch",
            })
        traces.append(QueryTrace(
            store="OpenSearch", engine="opensearch", mode="dsl",
            title=(
                f"First commit per author on {origin}; filter buckets "
                f"with first ≥ {cutoff_iso[:10]} client-side"
            ),
            query=body_text,
            result_summary=f"{len(new)} new authors out of {len(buckets)} total",
        ))
    else:
        traces.append(QueryTrace(
            store="OpenSearch", engine="opensearch", mode="dsl",
            title=f"First commit per author on {origin}",
            query=body_text, result_summary="no response",
            error="OpenSearch unreachable or empty",
        ))

    # Same "skip zeros if another store has a signal" rule as
    # ``_metric_contributors`` — protects against GrimoireLab simply
    # not having ingested this repo's commits.
    candidates = [
        ("sparql",     values["sparql"],     f"last {window_days} days · SPARQL"),
        ("opensearch", values["opensearch"], f"last {window_days} days · OpenSearch"),
    ]
    winner = next(((v, lbl) for _, v, lbl in candidates if v), None)
    if winner is None:
        observed = [(v, lbl) for _, v, lbl in candidates if v is not None]
        if not observed:
            return MetricResult(
                slug="new_contributors", value="—", label="no data",
                secondary=None, queries=traces,
                notes="Couldn't reach either store with first-contribution data.",
            )
        winner = observed[0]
    headline, label = winner

    bits = []
    if values["sparql"] is not None:
        bits.append(f"SPARQL: {values['sparql']}")
    if values["opensearch"] is not None:
        bits.append(f"OpenSearch: {values['opensearch']}")
    return MetricResult(
        slug="new_contributors",
        value=str(headline),
        label=label,
        secondary=" · ".join(bits),
        queries=traces,
        notes=(
            "A *new* contributor is one whose first contribution to "
            "this repo falls inside the window — not necessarily their "
            "first ever contribution to anything. Counts can differ "
            "between SPARQL and OpenSearch because the crawler "
            "deduplicates by GitHub login while GrimoireLab buckets by "
            "git author_name (which may include several aliases per "
            "person)."
        ),
        examples=examples,
    )


# ── Metric 3 · Technical Fork ─────────────────────────────────────────────

def _metric_technical_fork(full: str, canonical_url: str, window_days: int) -> MetricResult:
    """Snapshot: how many forks exist? Two views — the *observed* fork
    count from inbound FORK_OF edges in Neo4j, and the GitHub-reported
    count materialised as ``pulse:githubRepoForks`` in the RDF graph.
    """
    traces: list[QueryTrace] = []
    values: dict[str, int | None] = {"neo4j": None, "sparql": None}

    # ── Neo4j — inbound FORK_OF edges actually observed ──────────────
    cypher = (
        "// Inbound FORK_OF edges into the repo. Counts the forks the\n"
        "// crawler has actually visited — typically smaller than the\n"
        "// GitHub-reported total because not every fork is interesting\n"
        "// enough to enqueue.\n"
        f"MATCH (fork:Repo)-[:FORK_OF]->(r:Repo {{full_name: '{full}'}})\n"
        "RETURN count(DISTINCT fork) AS observed_forks"
    )
    try:
        rows = stores.neo4j_run(cypher)
        n = int(rows[0].get("observed_forks") or 0) if rows else 0
        values["neo4j"] = n
        traces.append(QueryTrace(
            store="Neo4j", engine="cypher",
            title=f"Forks observed in graph for {full}",
            query=cypher, result_summary=f"{n} forks in graph",
        ))
    except Exception as exc:  # noqa: BLE001
        traces.append(QueryTrace(
            store="Neo4j", engine="cypher",
            title=f"Forks observed in graph for {full}",
            query=cypher, result_summary="error", error=str(exc),
        ))

    # ── SPARQL — what GitHub said the fork count was at crawl time ──
    sparql = (
        "# pulse:githubRepoForks is the value GitHub reported when the\n"
        "# metadata extractor last ran — it can be higher than the\n"
        "# observed-fork count if the crawler hasn't enqueued every\n"
        "# fork.\n"
        "PREFIX pulse: <https://open-pulse.epfl.ch/ontology#>\n"
        "SELECT ?forks WHERE {\n"
        f'  ?repo pulse:githubRepositoryHandle "{full}" ;\n'
        "        pulse:githubRepoForks ?forks .\n"
        "}"
    )
    try:
        rows = stores.sparql_select(sparql)
        if rows:
            n = int(rows[0]["forks"]["value"])
            values["sparql"] = n
            traces.append(QueryTrace(
                store="SPARQL", engine="sparql",
                title=f"GitHub-reported fork count for {full}",
                query=sparql, result_summary=f"{n} forks (GitHub)",
            ))
        else:
            traces.append(QueryTrace(
                store="SPARQL", engine="sparql",
                title=f"GitHub-reported fork count for {full}",
                query=sparql, result_summary="no triple matched",
            ))
    except Exception as exc:  # noqa: BLE001
        traces.append(QueryTrace(
            store="SPARQL", engine="sparql",
            title=f"GitHub-reported fork count for {full}",
            query=sparql, result_summary="error", error=str(exc),
        ))

    headline = next((values[k] for k in ("sparql", "neo4j") if values[k] is not None), None)
    if headline is None:
        return MetricResult(
            slug="technical_fork", value="—", label="no fork data",
            secondary=None, queries=traces,
            notes="No fork count available from either store.",
        )
    bits = []
    if values["sparql"] is not None:
        bits.append(f"GitHub-reported: {values['sparql']}")
    if values["neo4j"] is not None:
        bits.append(f"in graph: {values['neo4j']}")
    return MetricResult(
        slug="technical_fork",
        value=str(headline),
        label="forks (GitHub-reported)",
        secondary=" · ".join(bits),
        queries=traces,
        notes=(
            "A snapshot, not a time-series. The GitHub-reported count "
            "(SPARQL) is authoritative; the Neo4j count is the subset "
            "the crawler has actually enqueued and walked."
        ),
    )


# ── Metric 4 · Licenses Declared ──────────────────────────────────────────

def _metric_licenses(full: str, canonical_url: str, window_days: int) -> MetricResult:
    """FAIR/quality snapshot: does the repo declare a license, and
    which one(s)?
    """
    traces: list[QueryTrace] = []

    sparql = (
        "# Pulls every schema:license object attached to the repo. The\n"
        "# value is an IRI (e.g. https://spdx.org/licenses/MIT.html) or\n"
        "# a string from the GitHub API when the gimie extractor\n"
        "# couldn't normalise it.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "PREFIX pulse:  <https://open-pulse.epfl.ch/ontology#>\n"
        "SELECT DISTINCT ?license WHERE {\n"
        f'  ?repo pulse:githubRepositoryHandle "{full}" ;\n'
        "        schema:license ?license .\n"
        "}"
    )
    licenses: list[str] = []
    try:
        rows = stores.sparql_select(sparql)
        licenses = [r["license"]["value"] for r in rows]
        traces.append(QueryTrace(
            store="SPARQL", engine="sparql",
            title=f"License IRIs / strings declared for {full}",
            query=sparql,
            result_summary=(
                f"{len(licenses)} declared license"
                + ("s" if len(licenses) != 1 else "")
            ),
        ))
    except Exception as exc:  # noqa: BLE001
        traces.append(QueryTrace(
            store="SPARQL", engine="sparql",
            title=f"License IRIs / strings declared for {full}",
            query=sparql, result_summary="error", error=str(exc),
        ))

    if not licenses:
        return MetricResult(
            slug="licenses_declared",
            value="✗",
            label="no license declared",
            secondary=None,
            queries=traces,
            notes=(
                "An undeclared license isn't the same as 'no license' — "
                "it just means the metadata extractor didn't find one. "
                "Triggering enrichment may surface it."
            ),
        )

    display = ", ".join(_short_license(l) for l in licenses[:3])
    if len(licenses) > 3:
        display += f", +{len(licenses) - 3}"
    return MetricResult(
        slug="licenses_declared",
        value="✓",
        label=display,
        secondary=f"{len(licenses)} declared",
        queries=traces,
        examples=[{"label": _short_license(l), "detail": l, "source": "SPARQL"} for l in licenses],
        notes=(
            "Snapshot — the SPARQL store keeps the latest crawl's "
            "license triple. Re-enriching this repo will update it. "
            "A ✓ here doesn't guarantee the license is OSI-approved "
            "(that's a separate CHAOSS metric)."
        ),
    )


def _short_license(iri: str) -> str:
    """Squeeze an SPDX IRI down to its short id where possible."""
    if "/" not in iri:
        return iri
    tail = iri.rstrip("/").rsplit("/", 1)[-1]
    return tail.replace(".html", "").replace(".json", "")


# ── Metric 5 · Academic OS Project Impact ────────────────────────────────

def _metric_academic_impact(full: str, canonical_url: str, window_days: int) -> MetricResult:
    """Hybrid metric: which papers cite / mention this software?

    There's no direct ``schema:isBasedOn`` triple in the current
    ontology between scholarly articles and repos, so we go through
    the Qdrant vector store: collections of Infoscience articles,
    ETHZ research-collection articles, OpenAlex works, and Zenodo
    records are searched for points whose payload mentions the
    repo's GitHub URL.
    """
    traces: list[QueryTrace] = []
    impact_collections = (
        "infoscience_articles",
        "ethz_research_collection_articles",
        "works",  # OpenAlex
        "zenodo_records",
    )

    # We reuse the same backlinks-style filter used elsewhere in the
    # hub, then prune to the academic collections.
    qdrant_query = {
        "filter": {
            "should": [
                {"key": "url",         "match": {"text": canonical_url}},
                {"key": "html_url",    "match": {"text": canonical_url}},
                {"key": "homepage",    "match": {"text": canonical_url}},
                {"key": "code_repository", "match": {"text": canonical_url}},
                {"key": "text",        "match": {"text": full}},
            ]
        },
        "limit": 50,
        "with_payload": True,
    }
    query_text = json.dumps(qdrant_query, indent=2)
    examples: list[dict[str, str]] = []
    total = 0
    by_kind: dict[str, int] = {}

    for col in impact_collections:
        try:
            points = qdrant._autocomplete_one(
                col,
                ("url", "html_url", "homepage", "code_repository", "text",
                 "title", "name"),
                full,
                limit=5,
                timeout=2.0,
            )
        except Exception as exc:  # noqa: BLE001
            traces.append(QueryTrace(
                store=f"Qdrant · {col}",
                engine="opensearch", mode="dsl",
                title=f"Mentions of {full} in {col}",
                query=query_text, result_summary="error", error=str(exc),
            ))
            continue
        by_kind[col] = len(points)
        total += len(points)
        for p in points[:3]:
            payload = p.get("payload") or {}
            title = (
                payload.get("title")
                or payload.get("name")
                or payload.get("display_name")
                or "(untitled)"
            )
            doi = payload.get("doi") or payload.get("identifier") or ""
            examples.append({
                "label": str(title)[:90],
                "detail": str(doi)[:80] if doi else col,
                "source": col,
            })
        traces.append(QueryTrace(
            store=f"Qdrant · {col}",
            engine="opensearch", mode="dsl",
            title=f"Text-match for {full} in {col}",
            query=query_text,
            result_summary=f"{len(points)} mention" + ("s" if len(points) != 1 else ""),
        ))

    if total == 0:
        return MetricResult(
            slug="academic_impact",
            value="0",
            label="no academic mentions found",
            secondary=None,
            queries=traces,
            notes=(
                "We searched four scholarly collections in Qdrant for "
                "any payload field that contains the repository's "
                "owner/name slug. A zero here means none was indexed — "
                "not that no paper cites this repo (e.g. a paper that "
                "only links via DOI wouldn't match)."
            ),
        )

    bits = [f"{k}: {v}" for k, v in by_kind.items() if v]
    return MetricResult(
        slug="academic_impact",
        value=str(total),
        label="academic mentions across 4 stores",
        secondary=" · ".join(bits),
        queries=traces,
        examples=examples,
        notes=(
            "Counts Qdrant points (papers, records, works) whose "
            "payload mentions this repository. Each collection covers "
            "a different surface: Infoscience for EPFL papers, ETHZ "
            "research-collection for ETHZ outputs, OpenAlex (works) "
            "globally, and Zenodo for software releases that picked up "
            "citations."
        ),
    )


# ── Registry ─────────────────────────────────────────────────────────────

REGISTRY: list[MetricSpec] = [
    MetricSpec(
        slug="contributors",
        name="Contributors",
        category="Community",
        chaoss_level="Level 0 · Must-have",
        chaoss_url="https://chaoss.community/kb/metric-contributors/",
        question="Is there a community at all?",
        description=(
            "Distinct people who contributed to the repository. Computed "
            "against all three stores so the page exposes both the "
            "windowed view (OpenSearch / SPARQL) and the all-time graph "
            "view (Neo4j)."
        ),
        is_time_based=True,
        compute=_metric_contributors,
    ),
    MetricSpec(
        slug="new_contributors",
        name="New Contributors",
        category="Community",
        chaoss_level="Level 0 · Must-have",
        chaoss_url="https://chaoss.community/kb/metric-new-contributors/",
        question="Is the community growing?",
        description=(
            "People whose *first* contribution to this repository falls "
            "inside the chosen window. SPARQL uses the typed first-date "
            "on the Contribution node; OpenSearch computes the min commit "
            "date per author client-side."
        ),
        is_time_based=True,
        compute=_metric_new_contributors,
    ),
    MetricSpec(
        slug="technical_fork",
        name="Technical Fork",
        category="Popularity",
        chaoss_level="Level 0 · Must-have",
        chaoss_url="https://chaoss.community/kb/metric-technical-fork/",
        question="How often is the project being reused / developed from?",
        description=(
            "Total fork count. The GitHub-reported number comes from "
            "SPARQL (latest crawl). Neo4j gives the in-graph count — "
            "the subset of those forks the crawler has actually "
            "walked."
        ),
        is_time_based=False,
        compute=_metric_technical_fork,
    ),
    MetricSpec(
        slug="licenses_declared",
        name="Licenses Declared",
        category="FAIR / quality",
        chaoss_level="Level 0 · Must-have",
        chaoss_url="https://chaoss.community/kb/metric-licenses-declared/",
        question="Is the software legally usable and reusable?",
        description=(
            "Whether the repository declares one or more licenses "
            "(schema:license triple). Doesn't yet check the LICENSE "
            "file directly — that's the *License Coverage* metric "
            "(Phase 2 / would-like-to-have)."
        ),
        is_time_based=False,
        compute=_metric_licenses,
    ),
    MetricSpec(
        slug="academic_impact",
        name="Academic OS Project Impact",
        category="Popularity",
        chaoss_level="Level 0 · Must-have",
        chaoss_url=(
            "https://chaoss.community/kb/metric-academic-open-source-project-impact/"
        ),
        question="How much does the software influence academic outputs?",
        description=(
            "Searches the Qdrant scholarly collections (Infoscience, "
            "ETHZ research-collection, OpenAlex works, Zenodo records) "
            "for points whose payload mentions this repository's URL "
            "or owner/name slug."
        ),
        is_time_based=False,
        compute=_metric_academic_impact,
    ),
]


def spec_for(slug: str) -> MetricSpec | None:
    """Look up a registered metric by its URL slug."""
    return next((m for m in REGISTRY if m.slug == slug), None)
