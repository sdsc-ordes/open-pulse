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


# ── Metric 6 · Project Popularity (stars + forks + dependents) ──────────

def _metric_project_popularity(full: str, canonical_url: str, window_days: int) -> MetricResult:
    """Composite popularity snapshot: stars, forks, and — importantly
    for the academic context — the *dependents* count from the
    DEPENDS_ON edges Neo4j keeps. CHAOSS doesn't single out dependents
    as a metric of its own, but they're the closest signal we have to
    "how much software is built on top of this".
    """
    traces: list[QueryTrace] = []
    stars: int | None = None
    forks: int | None = None
    dependents: int = 0
    dependent_names: list[str] = []

    # ── SPARQL — stars + forks in one shot ───────────────────────────
    sparql = (
        "# githubRepoStars + githubRepoForks come from the metadata\n"
        "# extractor and reflect the most recent crawl. They are\n"
        "# snapshots, not series.\n"
        "PREFIX pulse: <https://open-pulse.epfl.ch/ontology#>\n"
        "SELECT ?stars ?forks WHERE {\n"
        f'  ?repo pulse:githubRepositoryHandle "{full}" .\n'
        "  OPTIONAL { ?repo pulse:githubRepoStars ?stars }\n"
        "  OPTIONAL { ?repo pulse:githubRepoForks ?forks }\n"
        "}"
    )
    try:
        rows = stores.sparql_select(sparql)
        if rows:
            stars = int(rows[0]["stars"]["value"]) if rows[0].get("stars") else None
            forks = int(rows[0]["forks"]["value"]) if rows[0].get("forks") else None
            traces.append(QueryTrace(
                store="SPARQL", engine="sparql",
                title=f"Stars + fork count for {full}",
                query=sparql,
                result_summary=f"{stars or 0} stars · {forks or 0} forks",
            ))
        else:
            traces.append(QueryTrace(
                store="SPARQL", engine="sparql",
                title=f"Stars + fork count for {full}",
                query=sparql, result_summary="repo not in graph",
            ))
    except Exception as exc:  # noqa: BLE001
        traces.append(QueryTrace(
            store="SPARQL", engine="sparql",
            title=f"Stars + fork count for {full}",
            query=sparql, result_summary="error", error=str(exc),
        ))

    # ── Neo4j — DEPENDS_ON dependents ────────────────────────────────
    cypher = (
        "// Inbound DEPENDS_ON edges into the target repo. Each edge is\n"
        "// a separate repository that lists this one in its manifest.\n"
        "// The crawler resolves manifests opportunistically, so this\n"
        "// is a lower bound — the *observed* dependent count.\n"
        f"MATCH (dep:Repo)-[:DEPENDS_ON]->(r:Repo {{full_name: '{full}'}})\n"
        "RETURN dep.full_name AS dependent\n"
        "ORDER BY dependent\n"
        "LIMIT 20"
    )
    try:
        rows = stores.neo4j_run(cypher)
        dependent_names = [r["dependent"] for r in rows if r.get("dependent")]
        # Count again without the LIMIT for an accurate total.
        count_cypher = (
            "// Total inbound DEPENDS_ON count (no limit).\n"
            f"MATCH (dep:Repo)-[:DEPENDS_ON]->(r:Repo {{full_name: '{full}'}})\n"
            "RETURN count(DISTINCT dep) AS n"
        )
        count_rows = stores.neo4j_run(count_cypher)
        dependents = int(count_rows[0].get("n") or 0) if count_rows else 0
        traces.append(QueryTrace(
            store="Neo4j", engine="cypher",
            title=f"Top-20 dependents of {full}",
            query=cypher,
            result_summary=f"{dependents} total dependents in graph",
        ))
    except Exception as exc:  # noqa: BLE001
        traces.append(QueryTrace(
            store="Neo4j", engine="cypher",
            title=f"Top-20 dependents of {full}",
            query=cypher, result_summary="error", error=str(exc),
        ))

    # Headline: stars when known (universally recognised), else the
    # dependent count, else forks.
    if stars is not None:
        headline = str(stars)
        label = "stars"
    elif dependents:
        headline = str(dependents)
        label = "dependents"
    elif forks is not None:
        headline = str(forks)
        label = "forks"
    else:
        headline = "—"
        label = "no popularity signal"

    bits: list[str] = []
    if stars is not None:
        bits.append(f"⭐ {stars} stars")
    if forks is not None:
        bits.append(f"⑂ {forks} forks")
    if dependents:
        bits.append(f"↘ {dependents} dependents")

    return MetricResult(
        slug="project_popularity",
        value=headline,
        label=label,
        secondary=" · ".join(bits) if bits else None,
        queries=traces,
        examples=[
            {"label": name, "detail": "depends on this repo", "source": "Neo4j"}
            for name in dependent_names[:8]
        ],
        notes=(
            "Snapshot. Stars and forks come from the latest metadata "
            "crawl; the dependents count comes from inbound DEPENDS_ON "
            "edges resolved from package manifests (npm, pypi, cargo, "
            "go.mod, …) — only as complete as the crawl pipeline that "
            "extracted them."
        ),
    )


# ── Metric 7 · Programming Language Distribution ────────────────────────

def _metric_languages(full: str, canonical_url: str, window_days: int) -> MetricResult:
    """Which programming languages does the repo declare? The metadata
    extractor stores one ``schema:programmingLanguage`` triple per
    detected language, so this is a simple set query.
    """
    traces: list[QueryTrace] = []
    languages: list[str] = []

    sparql = (
        "# Every detected language for the repo. The metadata extractor\n"
        "# emits one triple per language (no relative-weight data yet),\n"
        "# so this is a presence-set, not a distribution.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "PREFIX pulse:  <https://open-pulse.epfl.ch/ontology#>\n"
        "SELECT DISTINCT ?lang WHERE {\n"
        f'  ?repo pulse:githubRepositoryHandle "{full}" ;\n'
        "        schema:programmingLanguage ?lang .\n"
        "}\n"
        "ORDER BY ?lang"
    )
    try:
        rows = stores.sparql_select(sparql)
        languages = [r["lang"]["value"] for r in rows]
        traces.append(QueryTrace(
            store="SPARQL", engine="sparql",
            title=f"Programming languages declared for {full}",
            query=sparql,
            result_summary=f"{len(languages)} language" + ("s" if len(languages) != 1 else ""),
        ))
    except Exception as exc:  # noqa: BLE001
        traces.append(QueryTrace(
            store="SPARQL", engine="sparql",
            title=f"Programming languages declared for {full}",
            query=sparql, result_summary="error", error=str(exc),
        ))

    if not languages:
        return MetricResult(
            slug="programming_languages",
            value="—",
            label="no language declared",
            secondary=None,
            queries=traces,
            notes=(
                "The metadata extractor didn't surface any language "
                "for this repo. Re-running enrichment will refresh "
                "the triples."
            ),
        )
    return MetricResult(
        slug="programming_languages",
        value=str(len(languages)),
        label="languages",
        secondary=", ".join(languages[:6]) + (f", +{len(languages) - 6}" if len(languages) > 6 else ""),
        queries=traces,
        examples=[{"label": l, "detail": "", "source": "SPARQL"} for l in languages],
        notes=(
            "Snapshot. The current ontology stores languages as a flat "
            "set — no per-language size weighting. The CHAOSS "
            "'Programming Language Distribution' spec wants byte-level "
            "shares; that's Phase 3 once the extractor emits them."
        ),
    )


# ── Metric 8 · Activity Dates and Times ─────────────────────────────────

def _metric_activity_dates(full: str, canonical_url: str, window_days: int) -> MetricResult:
    """Monthly commit activity from GrimoireLab's git index. Returns a
    series so the card can render a sparkline.
    """
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []
    series: list[dict[str, Any]] = []
    total = 0

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
            "by_month": {
                "date_histogram": {
                    "field": "grimoire_creation_date",
                    "calendar_interval": "month",
                    "min_doc_count": 0,
                }
            }
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/git_*_enriched/_search", body)
    if raw is not None:
        buckets = (raw.get("aggregations") or {}).get("by_month", {}).get("buckets", [])
        for b in buckets:
            ts_ms = int(b.get("key") or 0)
            count = int(b.get("doc_count") or 0)
            total += count
            series.append({
                "date": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()[:7],
                "value": count,
            })
        traces.append(QueryTrace(
            store="OpenSearch", engine="opensearch", mode="dsl",
            title=f"Monthly commit histogram on {origin} since {cutoff_iso[:10]}",
            query=body_text,
            result_summary=f"{total} commits in {len(series)} months",
        ))
    else:
        traces.append(QueryTrace(
            store="OpenSearch", engine="opensearch", mode="dsl",
            title=f"Monthly commit histogram on {origin} since {cutoff_iso[:10]}",
            query=body_text, result_summary="no response",
            error="OpenSearch unreachable or empty",
        ))

    if total == 0:
        return MetricResult(
            slug="activity_dates",
            value="0",
            label="no commits in window",
            secondary=None,
            queries=traces,
            notes=(
                "Either this repo isn't indexed by GrimoireLab or it "
                "had no commits in the selected window. The /hub entity "
                "page's sparkline runs the same agg over all time."
            ),
        )

    busiest = max(series, key=lambda r: r["value"])
    return MetricResult(
        slug="activity_dates",
        value=str(total),
        label=f"commits (last {window_days} days)",
        secondary=f"busiest month: {busiest['date']} ({busiest['value']} commits)",
        series=series,
        queries=traces,
        notes=(
            "Date histogram on GrimoireLab's ``grimoire_creation_date`` "
            "field. Each bar = one calendar month. Bursty patterns "
            "(release-tag spikes) tell a different story from steady "
            "monthly contributions."
        ),
    )


# ── Metric 9 · Change Request Closure Ratio ─────────────────────────────

def _metric_closure_ratio(full: str, canonical_url: str, window_days: int) -> MetricResult:
    """Of every pull request created on this repo in the window, what
    fraction was closed (merged or rejected)?
    """
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []

    origin = f"https://github.com/{full}"
    # GrimoireLab's enriched github_issues index stamps every doc with
    # ``pull_request: bool`` (true for PRs) and ``state: keyword``
    # ("open" / "closed"). ``merged: bool`` differentiates accepted
    # from rejected closes.
    body = {
        "size": 0,
        "query": {
            "bool": {
                "must": [
                    {"term": {"origin": origin}},
                    {"term": {"pull_request": True}},
                    {"range": {"created_at": {"gte": cutoff_iso}}},
                ]
            }
        },
        "aggs": {
            "by_state": {"terms": {"field": "state", "size": 5}},
            "merged":   {"filter": {"term": {"merged": True}}},
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/github_*_enriched/_search", body)
    total = closed = merged = open_ = 0
    if raw is not None:
        total = int(((raw.get("hits") or {}).get("total") or {}).get("value") or 0)
        for b in (raw.get("aggregations") or {}).get("by_state", {}).get("buckets", []):
            key = (b.get("key") or "").lower()
            if key == "closed":
                closed = int(b.get("doc_count") or 0)
            elif key == "open":
                open_ = int(b.get("doc_count") or 0)
        merged = int(((raw.get("aggregations") or {}).get("merged") or {}).get("doc_count") or 0)
        traces.append(QueryTrace(
            store="OpenSearch", engine="opensearch", mode="dsl",
            title=f"PR state breakdown on {origin} since {cutoff_iso[:10]}",
            query=body_text,
            result_summary=f"{total} PRs · {closed} closed · {merged} merged · {open_} open",
        ))
    else:
        traces.append(QueryTrace(
            store="OpenSearch", engine="opensearch", mode="dsl",
            title=f"PR state breakdown on {origin} since {cutoff_iso[:10]}",
            query=body_text, result_summary="no response",
            error="OpenSearch unreachable or github index empty",
        ))

    if total == 0:
        return MetricResult(
            slug="closure_ratio",
            value="—",
            label="no PRs in window",
            secondary=None,
            queries=traces,
            notes=(
                "Either no pull requests were opened in this window, "
                "or GrimoireLab hasn't ingested this repo's github "
                "stream yet."
            ),
        )

    ratio = closed / total
    return MetricResult(
        slug="closure_ratio",
        value=f"{ratio:.0%}",
        label=f"closed (last {window_days} days)",
        secondary=(
            f"{closed} closed of {total} PRs · {merged} merged · {open_} still open"
        ),
        queries=traces,
        notes=(
            "Closure ratio = closed / total. CHAOSS distinguishes "
            "*acceptance* (merged) from *closure* (closed without "
            "merge); the secondary line shows both so you can tell "
            "an active-reviewers project from a graveyard."
        ),
    )


# ── Metric 10 · Organizational Diversity ────────────────────────────────

def _metric_org_diversity(full: str, canonical_url: str, window_days: int) -> MetricResult:
    """How many distinct organisations does the contributor pool span?
    CHAOSS treats single-vendor projects very differently from
    cross-org communities.
    """
    traces: list[QueryTrace] = []
    org_names: list[str] = []
    org_count = 0

    # Hop: ?repo → schema:author / pulse:hasContribution → ?person →
    # org:hasMembership → ?m → org:organization → ?org → schema:name.
    sparql = (
        "# Distinct organisations whose members contributed to this\n"
        "# repository, going through the Person → Membership → Org\n"
        "# chain. Counts each org once even if many of its members\n"
        "# contributed.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "PREFIX pulse:  <https://open-pulse.epfl.ch/ontology#>\n"
        "PREFIX org:    <http://www.w3.org/ns/org#>\n"
        "SELECT DISTINCT ?orgName WHERE {\n"
        f'  ?repo pulse:githubRepositoryHandle "{full}" .\n'
        "  { ?repo schema:author ?person }\n"
        "  UNION\n"
        "  { ?person pulse:hasContribution/pulse:contributionTo ?repo }\n"
        "  ?person org:hasMembership ?m .\n"
        "  ?m org:organization ?org .\n"
        "  ?org schema:name ?orgName .\n"
        "}\n"
        "ORDER BY ?orgName"
    )
    try:
        rows = stores.sparql_select(sparql)
        org_names = [r["orgName"]["value"] for r in rows]
        org_count = len(org_names)
        traces.append(QueryTrace(
            store="SPARQL", engine="sparql",
            title=f"Distinct contributor organisations for {full}",
            query=sparql,
            result_summary=f"{org_count} distinct organisation"
                          + ("s" if org_count != 1 else ""),
        ))
    except Exception as exc:  # noqa: BLE001
        traces.append(QueryTrace(
            store="SPARQL", engine="sparql",
            title=f"Distinct contributor organisations for {full}",
            query=sparql, result_summary="error", error=str(exc),
        ))

    if org_count == 0:
        return MetricResult(
            slug="org_diversity",
            value="—",
            label="no org affiliations linked",
            secondary=None,
            queries=traces,
            notes=(
                "No contributor has an org:hasMembership triple "
                "pointing at this repo. This is common when "
                "contributors don't publicise their EPFL/SDSC "
                "affiliation on GitHub."
            ),
        )

    return MetricResult(
        slug="org_diversity",
        value=str(org_count),
        label="contributor organisations",
        secondary=", ".join(org_names[:5]) + (f", +{org_count - 5}" if org_count > 5 else ""),
        queries=traces,
        examples=[{"label": n, "detail": "", "source": "SPARQL"} for n in org_names],
        notes=(
            "Snapshot. A higher number = more diverse contributor "
            "base, which CHAOSS treats as a sustainability signal. "
            "Counts orgs once regardless of how many of their members "
            "contributed."
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


# ── Metric 11 · Time to First Response ──────────────────────────────────

def _metric_first_response(full: str, canonical_url: str, window_days: int) -> MetricResult:
    """Median hours from PR/issue creation to the first response by
    someone other than the author. GrimoireLab enriches every github
    document with ``time_to_first_attention_without_bot`` (or
    ``time_to_first_attention_hours``) precomputed.
    """
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []

    origin = f"https://github.com/{full}"
    body = {
        "size": 0,
        "query": {
            "bool": {
                "must": [
                    {"term": {"origin": origin}},
                    {"range": {"created_at": {"gte": cutoff_iso}}},
                ]
            }
        },
        "aggs": {
            "median": {
                "percentiles": {
                    "field": "time_to_first_attention_without_bot",
                    "percents": [50],
                }
            },
            "p90": {
                "percentiles": {
                    "field": "time_to_first_attention_without_bot",
                    "percents": [90],
                }
            },
            "count_with_response": {
                "filter": {"exists": {"field": "time_to_first_attention_without_bot"}}
            },
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/github_*_enriched/_search", body)
    if raw is None:
        traces.append(QueryTrace(
            store="OpenSearch", engine="opensearch", mode="dsl",
            title=f"P50/P90 time to first response on {origin}",
            query=body_text, result_summary="no response",
            error="OpenSearch unreachable or github index empty",
        ))
        return MetricResult(
            slug="first_response", value="—", label="no data",
            secondary=None, queries=traces,
            notes=(
                "GrimoireLab hasn't indexed pull-request/issue traffic "
                "for this repo. The query above is what we'd run once "
                "github_*_enriched starts receiving documents."
            ),
        )

    aggs = raw.get("aggregations") or {}
    n = int(((aggs.get("count_with_response") or {}).get("doc_count") or 0))
    p50_raw = ((aggs.get("median") or {}).get("values") or {}).get("50.0")
    p90_raw = ((aggs.get("p90") or {}).get("values") or {}).get("90.0")
    p50 = float(p50_raw) if p50_raw is not None else None
    p90 = float(p90_raw) if p90_raw is not None else None

    traces.append(QueryTrace(
        store="OpenSearch", engine="opensearch", mode="dsl",
        title=f"P50/P90 time to first response on {origin} since {cutoff_iso[:10]}",
        query=body_text,
        result_summary=(
            f"{n} responses · P50 {p50:.1f} h · P90 {p90:.1f} h"
            if p50 is not None and p90 is not None
            else f"{n} responses, no percentile available"
        ),
    ))

    if not n or p50 is None:
        return MetricResult(
            slug="first_response", value="—", label="no responses in window",
            secondary=None, queries=traces,
            notes=(
                "No github documents in the window carried a "
                "time_to_first_attention_without_bot value — either "
                "no PRs/issues opened, or none have been responded "
                "to yet."
            ),
        )

    return MetricResult(
        slug="first_response",
        value=f"{p50:.1f} h",
        label=f"median response (last {window_days} days)",
        secondary=f"{n} responses · P90 {p90:.1f} h" if p90 is not None else f"{n} responses",
        queries=traces,
        notes=(
            "Median hours from PR/issue creation to the first comment "
            "by someone other than the author (bot comments excluded). "
            "GrimoireLab precomputes the per-document value; we just "
            "ask for the percentile."
        ),
    )


# ── Metric 12 · Issue Resolution Duration ───────────────────────────────

def _metric_issue_resolution(full: str, canonical_url: str, window_days: int) -> MetricResult:
    """Median days to close an issue (excludes PRs)."""
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []

    origin = f"https://github.com/{full}"
    body = {
        "size": 0,
        "query": {
            "bool": {
                "must": [
                    {"term": {"origin": origin}},
                    {"term": {"pull_request": False}},
                    {"term": {"state": "closed"}},
                    {"range": {"closed_at": {"gte": cutoff_iso}}},
                ]
            }
        },
        "aggs": {
            "median_days": {
                "percentiles": {"field": "time_open_days", "percents": [50]}
            },
            "p90_days": {
                "percentiles": {"field": "time_open_days", "percents": [90]}
            },
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/github_*_enriched/_search", body)
    if raw is None:
        traces.append(QueryTrace(
            store="OpenSearch", engine="opensearch", mode="dsl",
            title=f"P50/P90 issue close duration on {origin}",
            query=body_text, result_summary="no response",
            error="OpenSearch unreachable or github index empty",
        ))
        return MetricResult(
            slug="issue_resolution", value="—", label="no data",
            secondary=None, queries=traces,
            notes="github_*_enriched has no documents for this repo yet.",
        )

    total = int(((raw.get("hits") or {}).get("total") or {}).get("value") or 0)
    aggs = raw.get("aggregations") or {}
    p50_raw = ((aggs.get("median_days") or {}).get("values") or {}).get("50.0")
    p90_raw = ((aggs.get("p90_days") or {}).get("values") or {}).get("90.0")
    p50 = float(p50_raw) if p50_raw is not None else None
    p90 = float(p90_raw) if p90_raw is not None else None

    traces.append(QueryTrace(
        store="OpenSearch", engine="opensearch", mode="dsl",
        title=f"Issue close duration on {origin} since {cutoff_iso[:10]}",
        query=body_text,
        result_summary=(
            f"{total} closed issues · P50 {p50:.1f} d · P90 {p90:.1f} d"
            if p50 is not None and p90 is not None
            else f"{total} closed issues, no percentile"
        ),
    ))

    if not total or p50 is None:
        return MetricResult(
            slug="issue_resolution", value="—",
            label="no closed issues in window",
            secondary=None, queries=traces,
            notes="No issues closed in the window for this repo.",
        )
    return MetricResult(
        slug="issue_resolution",
        value=f"{p50:.1f} d",
        label=f"median time to close (last {window_days} days)",
        secondary=(
            f"{total} closed · P90 {p90:.1f} d" if p90 is not None else f"{total} closed"
        ),
        queries=traces,
        notes=(
            "Excludes pull requests (``pull_request: false``). Uses "
            "GrimoireLab's ``time_open_days`` enrichment so the metric "
            "stays consistent across multiple GitHub backends."
        ),
    )


# ── Metric 13 · Self Merge Rate ─────────────────────────────────────────

def _metric_self_merge(full: str, canonical_url: str, window_days: int) -> MetricResult:
    """Fraction of merged PRs where the author also performed the merge."""
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []

    origin = f"https://github.com/{full}"
    body = {
        "size": 0,
        "query": {
            "bool": {
                "must": [
                    {"term": {"origin": origin}},
                    {"term": {"pull_request": True}},
                    {"term": {"merged": True}},
                    {"range": {"merge_date": {"gte": cutoff_iso}}},
                ]
            }
        },
        "aggs": {
            "self_merged": {
                "filter": {
                    # Painless script: comparison of ``user_login`` vs
                    # ``merge_author_login``. Wrapped in try/catch so a
                    # missing-field doc doesn't blow up the aggregation.
                    "script": {
                        "source": (
                            "try { return doc['user_login'].value == "
                            "doc['merge_author_login'].value; } catch "
                            "(Exception e) { return false; }"
                        )
                    }
                }
            }
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/github_*_enriched/_search", body)
    if raw is None:
        traces.append(QueryTrace(
            store="OpenSearch", engine="opensearch", mode="dsl",
            title=f"Self-merged vs merged PRs on {origin}",
            query=body_text, result_summary="no response",
            error="OpenSearch unreachable or github index empty",
        ))
        return MetricResult(
            slug="self_merge", value="—", label="no data",
            secondary=None, queries=traces,
            notes="github_*_enriched has no documents for this repo yet.",
        )

    total_merged = int(((raw.get("hits") or {}).get("total") or {}).get("value") or 0)
    self_merged = int(((raw.get("aggregations") or {}).get("self_merged") or {}).get("doc_count") or 0)

    traces.append(QueryTrace(
        store="OpenSearch", engine="opensearch", mode="dsl",
        title=f"Self-merged vs merged PRs on {origin} since {cutoff_iso[:10]}",
        query=body_text,
        result_summary=f"{self_merged} self / {total_merged} merged",
    ))

    if not total_merged:
        return MetricResult(
            slug="self_merge", value="—", label="no merged PRs in window",
            secondary=None, queries=traces,
            notes="No PRs merged on this repo in the window.",
        )
    ratio = self_merged / total_merged
    return MetricResult(
        slug="self_merge",
        value=f"{ratio:.0%}",
        label=f"self-merged (last {window_days} days)",
        secondary=f"{self_merged} of {total_merged} merged PRs",
        queries=traces,
        notes=(
            "CHAOSS treats self-merge rate as a code-review-culture "
            "signal: a high number suggests there is no reviewer "
            "gate. Some projects intentionally allow it (trusted "
            "maintainers, automation merges, single-author repos) so "
            "interpret in context."
        ),
    )


# ── Metric 14 · Burstiness ──────────────────────────────────────────────

def _metric_burstiness(full: str, canonical_url: str, window_days: int) -> MetricResult:
    """CHAOSS Burstiness B = (σ - μ) / (σ + μ) of the inter-arrival
    times between commits. B ranges from -1 (perfectly periodic)
    through 0 (Poisson-distributed) to +1 (very bursty).

    Computed from a daily date_histogram on git_*_enriched. We only
    keep days that *had* commits and compute the gaps between those.
    """
    import statistics

    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []

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
            "by_day": {
                "date_histogram": {
                    "field": "grimoire_creation_date",
                    "calendar_interval": "day",
                    "min_doc_count": 1,
                }
            }
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/git_*_enriched/_search", body)
    if raw is None:
        traces.append(QueryTrace(
            store="OpenSearch", engine="opensearch", mode="dsl",
            title=f"Daily commit histogram on {origin} for burstiness",
            query=body_text, result_summary="no response",
            error="OpenSearch unreachable or git index empty",
        ))
        return MetricResult(
            slug="burstiness", value="—", label="no data",
            secondary=None, queries=traces,
            notes="No git activity indexed for this repo.",
        )

    buckets = (raw.get("aggregations") or {}).get("by_day", {}).get("buckets", [])
    active_days = len(buckets)
    if active_days < 3:
        traces.append(QueryTrace(
            store="OpenSearch", engine="opensearch", mode="dsl",
            title=f"Daily commit histogram on {origin} for burstiness",
            query=body_text,
            result_summary=f"{active_days} active days — too few for burstiness",
        ))
        return MetricResult(
            slug="burstiness", value="—",
            label=f"only {active_days} active days",
            secondary=None, queries=traces,
            notes=(
                "Burstiness needs at least three active days in the "
                "window to compute inter-arrival gaps. Widen the "
                "window or pick a more active repo."
            ),
        )

    # Compute inter-arrival times in days. ``key`` on the bucket is
    # ms-since-epoch; we convert and difference consecutive entries.
    timestamps = [int(b.get("key") or 0) for b in buckets]
    gaps_days = [
        (timestamps[i + 1] - timestamps[i]) / 1000 / 86400.0
        for i in range(len(timestamps) - 1)
    ]
    mu = statistics.fmean(gaps_days)
    sigma = statistics.pstdev(gaps_days) if len(gaps_days) > 1 else 0.0

    if mu + sigma == 0:
        burstiness = 0.0
    else:
        burstiness = (sigma - mu) / (sigma + mu)

    # Friendly label for the score's regime.
    if burstiness > 0.3:
        regime = "bursty (irregular bursts)"
    elif burstiness < -0.3:
        regime = "periodic (steady cadence)"
    else:
        regime = "Poisson-like (random)"

    traces.append(QueryTrace(
        store="OpenSearch", engine="opensearch", mode="dsl",
        title=f"Daily commit histogram on {origin} for burstiness",
        query=body_text,
        result_summary=(
            f"{active_days} active days · mean gap {mu:.2f} d · "
            f"σ {sigma:.2f} d → B={burstiness:.3f}"
        ),
    ))

    return MetricResult(
        slug="burstiness",
        value=f"{burstiness:+.2f}",
        label=regime,
        secondary=(
            f"{active_days} active days · mean gap {mu:.1f} d · σ {sigma:.1f} d"
        ),
        queries=traces,
        notes=(
            "B = (σ − μ) / (σ + μ) on inter-arrival days between "
            "commits, per Goh & Barabási (2008). Range: −1 (strictly "
            "periodic) through 0 (random Poisson) to +1 (heavy bursts "
            "with long silences). Computed client-side from the daily "
            "histogram so the query shown above is exactly what ran."
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
    # ── Phase 2 additions ────────────────────────────────────────────
    MetricSpec(
        slug="project_popularity",
        name="Project Popularity",
        category="Popularity",
        chaoss_level="Level 0 · Implemented",
        chaoss_url="https://chaoss.community/kb/metric-project-popularity/",
        question="How visible is the project?",
        description=(
            "Composite snapshot: stars + forks from the SPARQL store, "
            "and *dependents* from inbound DEPENDS_ON edges in Neo4j — "
            "i.e. other repositories that list this one in their "
            "package manifest."
        ),
        is_time_based=False,
        compute=_metric_project_popularity,
    ),
    MetricSpec(
        slug="programming_languages",
        name="Programming Language Distribution",
        category="FAIR / quality",
        chaoss_level="Phase 2 · Would-like-to-have",
        chaoss_url=(
            "https://chaoss.community/kb/metric-programming-language-distribution/"
        ),
        question="Which languages does the codebase use?",
        description=(
            "Distinct schema:programmingLanguage values declared for "
            "this repo. Per-language byte shares aren't in the "
            "ontology yet, so this is a presence-set."
        ),
        is_time_based=False,
        compute=_metric_languages,
    ),
    MetricSpec(
        slug="activity_dates",
        name="Activity Dates and Times",
        category="Community",
        chaoss_level="Level 0 · Must-have",
        chaoss_url="https://chaoss.community/kb/metric-activity-dates-and-times/",
        question="What is the engagement pattern?",
        description=(
            "Monthly commit histogram from GrimoireLab. The card "
            "renders it as an inline sparkline so you can see steady "
            "vs bursty activity at a glance."
        ),
        is_time_based=True,
        compute=_metric_activity_dates,
    ),
    MetricSpec(
        slug="closure_ratio",
        name="Change Request Closure Ratio",
        category="Community",
        chaoss_level="Level 0 · Must-have",
        chaoss_url=(
            "https://chaoss.community/kb/metric-change-request-closure-ratio/"
        ),
        question="Are pull requests being acted on?",
        description=(
            "Closed / total pull requests in the window, with merged "
            "vs simply-closed broken out in the secondary line — "
            "tells an active-reviewers project from a stalled one."
        ),
        is_time_based=True,
        compute=_metric_closure_ratio,
    ),
    MetricSpec(
        slug="org_diversity",
        name="Organizational Diversity",
        category="Community",
        chaoss_level="Level 0 · Must-have",
        chaoss_url="https://chaoss.community/kb/metric-organizational-diversity/",
        question="Is the contributor base single-vendor or distributed?",
        description=(
            "Distinct organisations whose members appear in the "
            "repo's contributor pool, walked through the "
            "Person → Membership → Org chain in SPARQL."
        ),
        is_time_based=False,
        compute=_metric_org_diversity,
    ),
    # ── Phase 3 additions ────────────────────────────────────────────
    MetricSpec(
        slug="first_response",
        name="Time to First Response",
        category="Community",
        chaoss_level="Phase 2 · Would-like-to-have",
        chaoss_url="https://chaoss.community/kb/metric-time-to-first-response/",
        question="How quickly does the project respond?",
        description=(
            "Median hours from PR / issue creation to the first "
            "non-bot, non-author comment. Uses GrimoireLab's "
            "precomputed ``time_to_first_attention_without_bot``."
        ),
        is_time_based=True,
        compute=_metric_first_response,
    ),
    MetricSpec(
        slug="issue_resolution",
        name="Issue Resolution Duration",
        category="Community",
        chaoss_level="Phase 2 · Would-like-to-have",
        chaoss_url=(
            "https://chaoss.community/kb/metric-issue-resolution-duration/"
        ),
        question="How long do issues stay open?",
        description=(
            "Median days from issue creation to close (PRs excluded). "
            "Uses GrimoireLab's ``time_open_days`` enrichment so the "
            "answer stays consistent across GitHub backends."
        ),
        is_time_based=True,
        compute=_metric_issue_resolution,
    ),
    MetricSpec(
        slug="self_merge",
        name="Self Merge Rate",
        category="Community",
        chaoss_level="Phase 2 · Would-like-to-have",
        chaoss_url="https://chaoss.community/kb/metric-self-merge-rate/",
        question="How strong is the code-review culture?",
        description=(
            "Fraction of merged PRs in the window where the author "
            "also performed the merge. High = no reviewer gate; low "
            "= reviewed by someone else before landing."
        ),
        is_time_based=True,
        compute=_metric_self_merge,
    ),
    MetricSpec(
        slug="burstiness",
        name="Burstiness",
        category="Community",
        chaoss_level="Phase 2 · Would-like-to-have",
        chaoss_url="https://chaoss.community/kb/metric-burstiness/",
        question="Is contribution steady or in bursts?",
        description=(
            "B = (σ − μ) / (σ + μ) on inter-arrival days between "
            "commits — Goh & Barabási's burstiness measure. −1 = "
            "strictly periodic; 0 = Poisson-like random; +1 = heavy "
            "bursts with long silences between them."
        ),
        is_time_based=True,
        compute=_metric_burstiness,
    ),
]


def spec_for(slug: str) -> MetricSpec | None:
    """Look up a registered metric by its URL slug."""
    return next((m for m in REGISTRY if m.slug == slug), None)
