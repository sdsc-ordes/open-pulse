"""CHAOSS metrics computed live against the four open-pulse stores.

Each metric in this module is a tiny pipeline:

1. Build the query text for each store it touches (Neo4j / SPARQL /
   OpenSearch). The query text is kept around verbatim so it
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

from ..knowledge import opensearch as os_mod, stores

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
    # Human-readable unit for the series — shown in the sparkline
    # hover chip after the count. Defaults to "events" but every
    # series-emitting metric overrides it.
    series_unit: str = "events"
    examples: list[dict[str, str]] = field(default_factory=list)
    # One-line explanation of how the queries combine into the
    # headline number — rendered at the bottom of the trace expander
    # under "How values combine". Markdown-flavour (backticks +
    # **bold** + *italic*) gets parsed by the ``md`` Jinja filter.
    unification: str = ""
    # Optional typed visualisation hint. Shape is one of:
    #   {"kind": "donut",       "fraction": 0.0..1.0, "tone": "good|warn|info|danger"}
    #   {"kind": "stacked_bar", "segments": [{"label": str, "value": int, "tone": str}, …]}
    #   {"kind": "rank_bars",   "items": [{"label": str, "value": int, "share": 0..1}, …]}
    # ``tone`` maps to a CSS variable in app.css so light/dark themes stay consistent.
    visual: dict[str, Any] | None = None
    # Severity hint for the headline value (good / warn / info / danger).
    # When set, the template colours the big number accordingly.
    headline_tone: str | None = None
    # Optional language-keyed reproducibility scripts (``python`` /
    # ``bash`` / ``js``). When present, the unification footer
    # renders them as tabs so a visitor can copy a runnable script
    # that fetches every trace and applies the unification end-to-end.
    recipes: dict[str, str] | None = None


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

_API_PATH_BY_ENGINE = {
    "cypher": "/api/databases/cypher/query",
    "sparql": "/api/databases/sparql/query",
    "opensearch": "/api/databases/opensearch/query",
}


def _py_pretty_dict(obj: Any, indent: int = 4) -> str:
    """Format a Python dict / list as a clean multi-line literal.

    ``json.dumps`` already produces valid Python (JSON is a strict
    subset of Python's literal syntax for these types), so we use it
    with indent + ensure_ascii=False, then post-process to swap
    JSON's ``true``/``false``/``null`` for Python's ``True``/
    ``False``/``None`` so the script is paste-runnable.
    """
    s = json.dumps(obj, indent=indent, ensure_ascii=False)
    # Word-boundary swaps so we don't touch strings that happen to
    # *contain* ``true`` etc.
    return (
        s.replace(": true,", ": True,")
        .replace(": false,", ": False,")
        .replace(": null,", ": None,")
        .replace(": true\n", ": True\n")
        .replace(": false\n", ": False\n")
        .replace(": null\n", ": None\n")
        .replace(": true}", ": True}")
        .replace(": false}", ": False}")
        .replace(": null}", ": None}")
    )


def _js_pretty_object(obj: Any, indent: int = 2) -> str:
    """Format a Python dict / list as a JSON literal suitable for
    pasting into a JS file. JSON is valid ES6, so ``json.dumps`` with
    indenting is enough.
    """
    return json.dumps(obj, indent=indent, ensure_ascii=False)


def _build_recipes(
    *,
    label: str,
    traces: list[QueryTrace],
    extracts: list[dict[str, str]],
    combine: dict[str, str],
) -> dict[str, str]:
    """Generate ``python``, ``bash``, and ``js`` scripts that reproduce
    the metric end-to-end.

    Parameters
    ----------
    label
        Human-readable name for the printed result (e.g. ``"contributors"``).
    traces
        The same list rendered above; each carries its engine + query.
    extracts
        ``len(extracts) == len(traces)``. Each entry is a dict with
        ``python`` / ``bash`` / ``js`` keys giving the per-language
        expression that pulls a value out of that trace's response
        (referencing ``r1``, ``r2``, … for response objects).
    combine
        Dict with ``python`` / ``bash`` / ``js`` keys giving the final
        unification logic. Each language's expression must end up
        assigning to a local variable named ``headline``.
    """
    # ── Python ────────────────────────────────────────────────────────
    py: list[str] = [
        f'"""Reproduce the CHAOSS \'{label}\' metric. Set OPENPULSE_TOKEN before running."""',
        "import os",
        "import requests",
        "from requests.auth import HTTPBasicAuth",
        "",
        'BASE = "http://openpulse.epfl.ch:7507"',
        'AUTH = HTTPBasicAuth("openpulse", os.environ["OPENPULSE_TOKEN"])',
        "",
        "def post(path, body):",
        "    r = requests.post(BASE + path, json=body, auth=AUTH, timeout=30)",
        "    r.raise_for_status()",
        "    return r.json()",
        "",
    ]
    for i, (t, ex) in enumerate(zip(traces, extracts), start=1):
        path = _API_PATH_BY_ENGINE.get(t.engine, "/api/databases/cypher/query")
        py.append(f"# ── trace {i}: {t.title} ──")
        if t.engine == "opensearch":
            try:
                qobj = json.loads(t.query)
                body = {"mode": t.mode or "dsl", "query": qobj}
            except Exception:  # noqa: BLE001
                body = {"mode": t.mode or "dsl", "query": t.query}
            py.append(f"body{i} = {_py_pretty_dict(body)}")
            py.append(f"r{i} = post({path!r}, body{i})")
        else:
            # Triple-quoted string literal keeps the query readable.
            triple_q = (
                '"""'
                + t.query.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
                + '"""'
            )
            py.append(f"query{i} = {triple_q}")
            py.append(f'r{i} = post({path!r}, {{"query": query{i}}})')
        py.append(f"v{i} = {ex['python']}")
        py.append("")
    py.append("# ── unification ──")
    py.append(combine["python"])
    py.append(f'print(f"{label} = {{headline}}")')
    python_script = "\n".join(py)

    # ── Bash ──────────────────────────────────────────────────────────
    bash: list[str] = [
        "#!/usr/bin/env bash",
        f"# Reproduce the CHAOSS '{label}' metric (requires curl + jq).",
        "set -euo pipefail",
        "",
        'BASE="http://openpulse.epfl.ch:7507"',
        'AUTH="openpulse:${OPENPULSE_TOKEN:?set OPENPULSE_TOKEN before running}"',
        "",
    ]
    for i, (t, ex) in enumerate(zip(traces, extracts), start=1):
        path = _API_PATH_BY_ENGINE.get(t.engine, "/api/databases/cypher/query")
        if t.engine == "opensearch":
            try:
                qobj = json.loads(t.query)
                body_json = json.dumps(
                    {"mode": t.mode or "dsl", "query": qobj}, indent=2
                )
            except Exception:  # noqa: BLE001
                body_json = json.dumps({"mode": t.mode or "dsl", "query": t.query})
        else:
            body_json = json.dumps({"query": t.query}, indent=2)
        # Single-quoted heredoc ``<<'__BODY__'`` prevents shell from
        # touching ``$`` / ``"`` etc inside the JSON.
        bash.append(f"# ── trace {i}: {t.title} ──")
        bash.append(f"body{i}=$(cat <<'__BODY__'")
        bash.append(body_json)
        bash.append("__BODY__")
        bash.append(")")
        bash.append(
            f'r{i}=$(curl -sf -u "$AUTH" -H "content-type: application/json" \\\n'
            f'        -X POST "$BASE{path}" --data "$body{i}")'
        )
        bash.append(f"v{i}=$(echo \"$r{i}\" | jq -r '{ex['bash']}')")
        bash.append("")
    bash.append("# ── unification ──")
    bash.append(combine["bash"])
    bash.append(f'echo "{label} = $headline"')
    bash_script = "\n".join(bash)

    # ── JavaScript (Node 18+ with built-in fetch) ─────────────────────
    js: list[str] = [
        f"// Reproduce the CHAOSS '{label}' metric. Set OPENPULSE_TOKEN.",
        "// Requires Node 18+ (built-in fetch + Buffer).",
        "",
        'const BASE = "http://openpulse.epfl.ch:7507";',
        "const TOKEN = process.env.OPENPULSE_TOKEN;",
        'if (!TOKEN) { console.error("set OPENPULSE_TOKEN"); process.exit(1); }',
        'const AUTH = "Basic " + Buffer.from(`openpulse:${TOKEN}`).toString("base64");',
        "",
        "async function post(path, body) {",
        "  const r = await fetch(BASE + path, {",
        '    method: "POST",',
        '    headers: { "Authorization": AUTH, "Content-Type": "application/json" },',
        "    body: JSON.stringify(body),",
        "  });",
        '  if (!r.ok) throw new Error("HTTP " + r.status);',
        "  return r.json();",
        "}",
        "",
        "(async () => {",
    ]
    for i, (t, ex) in enumerate(zip(traces, extracts), start=1):
        path = _API_PATH_BY_ENGINE.get(t.engine, "/api/databases/cypher/query")
        js.append(f"  // ── trace {i}: {t.title} ──")
        if t.engine == "opensearch":
            try:
                qobj = json.loads(t.query)
                body_str = _js_pretty_object(
                    {"mode": t.mode or "dsl", "query": qobj}, indent=2
                )
            except Exception:  # noqa: BLE001
                body_str = _js_pretty_object(
                    {"mode": t.mode or "dsl", "query": t.query}
                )
            # Re-indent so the literal sits inside the IIFE.
            body_str = "\n".join("  " + ln for ln in body_str.splitlines())
            js.append(f"  const r{i} = await post({json.dumps(path)},\n{body_str});")
        else:
            # Backtick template literal handles multi-line + embedded
            # quotes naturally; escape backticks if any appear.
            escaped = t.query.replace("\\", "\\\\").replace("`", "\\`")
            js.append(f"  const r{i} = await post({json.dumps(path)}, {{")
            js.append(f"    query: `{escaped}`,")
            js.append("  });")
        js.append(f"  const v{i} = {ex['js']};")
        js.append("")
    js.append("  // ── unification ──")
    for line in combine["js"].splitlines():
        js.append(f"  {line}")
    js.append(f"  console.log(`{label} = ${{headline}}`);")
    js.append("})();")
    js_script = "\n".join(js)

    return {"python": python_script, "bash": bash_script, "js": js_script}


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


def _metric_contributors(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
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
        traces.append(
            QueryTrace(
                store="Neo4j",
                engine="cypher",
                title="All-time distinct contributors in the community graph",
                query=cypher,
                result_summary=f"{n} distinct users",
            )
        )
    except Exception as exc:  # noqa: BLE001
        traces.append(
            QueryTrace(
                store="Neo4j",
                engine="cypher",
                title="All-time distinct contributors in the community graph",
                query=cypher,
                result_summary="error",
                error=str(exc),
            )
        )

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
        traces.append(
            QueryTrace(
                store="SPARQL",
                engine="sparql",
                title=f"Contributors with activity since {cutoff_iso[:10]}",
                query=sparql,
                result_summary=f"{n} distinct persons",
            )
        )
    except Exception as exc:  # noqa: BLE001
        traces.append(
            QueryTrace(
                store="SPARQL",
                engine="sparql",
                title=f"Contributors with activity since {cutoff_iso[:10]}",
                query=sparql,
                result_summary="error",
                error=str(exc),
            )
        )

    # ── OpenSearch — distinct git authors in the window + monthly
    # trend so the card can render a sparkline. One round-trip; the
    # date_histogram has a cardinality sub-agg per bucket plus the
    # outer cardinality stays for the headline number.
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
            "by_author": {"cardinality": {"field": "author_name"}},
            "by_month": {
                "date_histogram": {
                    "field": "grimoire_creation_date",
                    "calendar_interval": "month",
                    "min_doc_count": 0,
                },
                "aggs": {"unique_authors": {"cardinality": {"field": "author_name"}}},
            },
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/git_*_enriched/_search", body)
    contrib_series: list[dict[str, Any]] = []
    if raw is not None:
        aggs = raw.get("aggregations") or {}
        n = int((aggs.get("by_author") or {}).get("value") or 0)
        values["opensearch"] = n
        for b in (aggs.get("by_month") or {}).get("buckets", []):
            ts_ms = int(b.get("key") or 0)
            authors = int((b.get("unique_authors") or {}).get("value") or 0)
            contrib_series.append(
                {
                    "date": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                    .date()
                    .isoformat()[:7],
                    "value": authors,
                }
            )
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"Cardinality of git authors on {origin} since {cutoff_iso[:10]} (with monthly trend)",
                query=body_text,
                result_summary=f"{n} distinct authors · {len(contrib_series)} months",
            )
        )
    else:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"Cardinality of git authors on {origin} since {cutoff_iso[:10]}",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or empty",
            )
        )

    # Headline: prefer the largest observed non-zero value among the
    # windowed stores (OpenSearch / SPARQL), falling back to Neo4j's
    # all-time count. We avoid picking a 0 when another store has a
    # signal — a zero from GrimoireLab usually means "this repo isn't
    # indexed yet", not "this repo has no contributors".
    candidates = [
        ("opensearch", values["opensearch"], f"last {window_days} days · OpenSearch"),
        ("sparql", values["sparql"], f"last {window_days} days · SPARQL"),
        ("neo4j", values["neo4j"], "all-time · Neo4j community graph"),
    ]
    winner = next(((v, lbl) for _, v, lbl in candidates if v), None)
    if winner is None:
        # Every store either errored or returned None. Fall back to
        # whatever we *did* get, even if zero.
        observed = [(v, lbl) for _, v, lbl in candidates if v is not None]
        if not observed:
            return MetricResult(
                slug="contributors",
                value="—",
                label="no contributor data",
                secondary=None,
                queries=traces,
                notes="None of the three stores returned a value for this repo.",
                unification=(
                    "Largest non-zero of three stores: **OpenSearch** (windowed git-cardinality) · **SPARQL** (windowed contribution graph) · **Neo4j** (all-time edges). Fallback ladder skips zeros so an empty index can't hide a real signal."
                ),
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

    # Recipe: three extracts (Neo4j count, SPARQL count, OS cardinality)
    # combined by "largest non-zero" with a fallback to Neo4j's all-time.
    contributors_recipes = _build_recipes(
        label="contributors",
        traces=traces,
        extracts=[
            # trace 1 — Neo4j ``count(DISTINCT u)``
            {
                "python": "r1['rows'][0][0] if r1.get('rows') else 0",
                "bash": ".rows[0][0] // 0",
                "js": "(r1.rows && r1.rows[0]) ? r1.rows[0][0] : 0",
            },
            # trace 2 — SPARQL ``COUNT(DISTINCT ?person)``
            {
                "python": "int(r2['rows'][0][0]) if r2.get('rows') else 0",
                "bash": ".rows[0][0] | tonumber? // 0",
                "js": "(r2.rows && r2.rows[0]) ? Number(r2.rows[0][0]) : 0",
            },
            # trace 3 — OpenSearch ``cardinality(author_name)``
            {
                "python": "r3.get('raw', {}).get('aggregations', {}).get('by_author', {}).get('value', 0)",
                "bash": ".raw.aggregations.by_author.value // 0",
                "js": "r3.raw?.aggregations?.by_author?.value ?? 0",
            },
        ],
        combine={
            "python": "headline = next((v for v in (v3, v2, v1) if v), 0)",
            "bash": (
                'if [ "$v3" != "0" ]; then headline=$v3;'
                ' elif [ "$v2" != "0" ]; then headline=$v2;'
                " else headline=$v1; fi"
            ),
            "js": "const headline = [v3, v2, v1].find(v => v) ?? 0;",
        },
    )
    return MetricResult(
        slug="contributors",
        value=str(headline),
        label=label,
        recipes=contributors_recipes,
        series=contrib_series,
        series_unit="contributors",
        secondary=" · ".join(bits),
        queries=traces,
        notes=(
            "OpenSearch counts distinct ``author_name`` values on commits "
            "indexed by GrimoireLab in the window. SPARQL uses the typed "
            "``pulse:lastContributionDate`` on the Contribution node. "
            "Neo4j is an all-time edge count — it ignores the window."
        ),
        unification=(
            "Largest non-zero of three stores: **OpenSearch** (windowed git-cardinality) · **SPARQL** (windowed contribution graph) · **Neo4j** (all-time edges). Fallback ladder skips zeros so an empty index can't hide a real signal."
        ),
    )


# ── Metric 2 · New Contributors ───────────────────────────────────────────


def _metric_new_contributors(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    """Contributors whose *first* contribution to this repo happened
    inside the window.
    """
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []
    nc_recipes = None
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
        traces.append(
            QueryTrace(
                store="SPARQL",
                engine="sparql",
                title=f"Persons whose first contribution is after {cutoff_iso[:10]}",
                query=sparql,
                result_summary=f"{len(rows)} new contributors",
            )
        )
    except Exception as exc:  # noqa: BLE001
        traces.append(
            QueryTrace(
                store="SPARQL",
                engine="sparql",
                title=f"Persons whose first contribution is after {cutoff_iso[:10]}",
                query=sparql,
                result_summary="error",
                error=str(exc),
            )
        )

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
                "terms": {"field": "author_name", "size": 1000},
                "aggs": {"first_commit": {"min": {"field": "grimoire_creation_date"}}},
            }
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/git_*_enriched/_search", body)
    if raw is not None:
        buckets = (
            (raw.get("aggregations") or {}).get("by_author", {}).get("buckets", [])
        )
        cutoff_ms = int(cutoff.timestamp() * 1000)
        new = [
            b
            for b in buckets
            if (b.get("first_commit", {}).get("value") or 0) >= cutoff_ms
        ]
        values["opensearch"] = len(new)
        for b in new[:8]:
            ts = int(b.get("first_commit", {}).get("value") or 0) // 1000
            examples.append(
                {
                    "label": b.get("key", ""),
                    "detail": datetime.fromtimestamp(ts, tz=timezone.utc)
                    .date()
                    .isoformat(),
                    "source": "OpenSearch",
                }
            )
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=(
                    f"First commit per author on {origin}; filter buckets "
                    f"with first ≥ {cutoff_iso[:10]} client-side"
                ),
                query=body_text,
                result_summary=f"{len(new)} new authors out of {len(buckets)} total",
            )
        )
    else:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"First commit per author on {origin}",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or empty",
            )
        )

    # Same "skip zeros if another store has a signal" rule as
    # ``_metric_contributors`` — protects against GrimoireLab simply
    # not having ingested this repo's commits.
    candidates = [
        ("sparql", values["sparql"], f"last {window_days} days · SPARQL"),
        ("opensearch", values["opensearch"], f"last {window_days} days · OpenSearch"),
    ]
    winner = next(((v, lbl) for _, v, lbl in candidates if v), None)
    if winner is None:
        observed = [(v, lbl) for _, v, lbl in candidates if v is not None]
        if not observed:
            return MetricResult(
                recipes=nc_recipes,
                slug="new_contributors",
                value="—",
                label="no data",
                secondary=None,
                queries=traces,
                notes="Couldn't reach either store with first-contribution data.",
                unification=(
                    "Largest non-zero of windowed **SPARQL** (`pulse:firstContributionDate` filter) and **OpenSearch** (terms agg + min commit date per author, filtered client-side)."
                ),
            )
        winner = observed[0]
    headline, label = winner

    bits = []
    if values["sparql"] is not None:
        bits.append(f"SPARQL: {values['sparql']}")
    if values["opensearch"] is not None:
        bits.append(f"OpenSearch: {values['opensearch']}")

    _ci = cutoff if isinstance(cutoff, str) else cutoff_iso
    nc_recipes = _build_recipes(
        label="new_contributors",
        traces=traces,
        extracts=[
            {
                "python": "len(r1.get('rows', []))",
                "bash": "[.rows[]] | length",
                "js": "(r1.rows || []).length",
            },
            {
                "python": "r2",
                "bash": ".",
                "js": "r2",
            },
        ],
        combine={
            "python": "cutoff_ms = int(_dt.datetime.fromisoformat('{cutoff_iso}').timestamp() * 1000) if '{cutoff_iso}' else 0\nbuckets = v2.get('raw', {}).get('aggregations', {}).get('by_author', {}).get('buckets', [])\nv2_count = sum(1 for b in buckets if (b.get('first_commit', {}).get('value') or 0) >= cutoff_ms)\nheadline = next((v for v in (v1, v2_count) if v), 0)".replace(
                "{cutoff_iso}", _ci
            ),
            "bash": 'cutoff_ms=$(python3 -c \'import datetime as d; print(int(d.datetime.fromisoformat("{cutoff_iso}").timestamp()*1000))\')\nv2_count=$(echo "$v2" | jq --argjson c "$cutoff_ms" \'[.raw.aggregations.by_author.buckets[] | select((.first_commit.value // 0) >= $c)] | length\')\nif [ "$v1" -gt 0 ]; then headline=$v1; elif [ "$v2_count" -gt 0 ]; then headline=$v2_count; else headline=0; fi'.replace(
                "{cutoff_iso}", _ci
            ),
            "js": "const cutoffMs = new Date('{cutoff_iso}').getTime();\nconst buckets = v2.raw?.aggregations?.by_author?.buckets || [];\nconst v2Count = buckets.filter(b => (b.first_commit?.value || 0) >= cutoffMs).length;\nconst headline = [v1, v2Count].find(v => v) || 0;".replace(
                "{cutoff_iso}", _ci
            ),
        },
    )
    return MetricResult(
        recipes=nc_recipes,
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


def _metric_technical_fork(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    """Snapshot: how many forks exist? Two views — the *observed* fork
    count from inbound FORK_OF edges in Neo4j, and the GitHub-reported
    count materialised as ``pulse:githubRepoForks`` in the RDF graph.
    """
    traces: list[QueryTrace] = []
    tf_recipes = None
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
        traces.append(
            QueryTrace(
                store="Neo4j",
                engine="cypher",
                title=f"Forks observed in graph for {full}",
                query=cypher,
                result_summary=f"{n} forks in graph",
            )
        )
    except Exception as exc:  # noqa: BLE001
        traces.append(
            QueryTrace(
                store="Neo4j",
                engine="cypher",
                title=f"Forks observed in graph for {full}",
                query=cypher,
                result_summary="error",
                error=str(exc),
            )
        )

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
            traces.append(
                QueryTrace(
                    store="SPARQL",
                    engine="sparql",
                    title=f"GitHub-reported fork count for {full}",
                    query=sparql,
                    result_summary=f"{n} forks (GitHub)",
                )
            )
        else:
            traces.append(
                QueryTrace(
                    store="SPARQL",
                    engine="sparql",
                    title=f"GitHub-reported fork count for {full}",
                    query=sparql,
                    result_summary="no triple matched",
                )
            )
    except Exception as exc:  # noqa: BLE001
        traces.append(
            QueryTrace(
                store="SPARQL",
                engine="sparql",
                title=f"GitHub-reported fork count for {full}",
                query=sparql,
                result_summary="error",
                error=str(exc),
            )
        )

    headline = next(
        (values[k] for k in ("sparql", "neo4j") if values[k] is not None), None
    )
    if headline is None:
        return MetricResult(
            recipes=tf_recipes,
            slug="technical_fork",
            value="—",
            label="no fork data",
            secondary=None,
            queries=traces,
            notes="No fork count available from either store.",
            unification=(
                "**SPARQL** GitHub-reported takes precedence; **Neo4j** in-graph observed-fork count is shown as a secondary signal."
            ),
        )
    bits = []
    if values["sparql"] is not None:
        bits.append(f"GitHub-reported: {values['sparql']}")
    if values["neo4j"] is not None:
        bits.append(f"in graph: {values['neo4j']}")

    tf_recipes = _build_recipes(
        label="technical_fork",
        traces=traces,
        extracts=[
            {
                "python": "r1['rows'][0][0] if r1.get('rows') else 0",
                "bash": ".rows[0][0] // 0",
                "js": "(r1.rows && r1.rows[0]) ? r1.rows[0][0] : 0",
            },
            {
                "python": "int(r2['rows'][0][0]) if r2.get('rows') and r2['rows'] else 0",
                "bash": ".rows[0][0] | tonumber? // 0",
                "js": "(r2.rows && r2.rows[0]) ? Number(r2.rows[0][0]) : 0",
            },
        ],
        combine={
            "python": "headline = v2 if v2 else v1",
            "bash": 'if [ "$v2" != "0" ]; then headline=$v2; else headline=$v1; fi',
            "js": "const headline = v2 || v1;",
        },
    )
    return MetricResult(
        recipes=tf_recipes,
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
    lic_recipes = None

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
        traces.append(
            QueryTrace(
                store="SPARQL",
                engine="sparql",
                title=f"License IRIs / strings declared for {full}",
                query=sparql,
                result_summary=(
                    f"{len(licenses)} declared license"
                    + ("s" if len(licenses) != 1 else "")
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001
        traces.append(
            QueryTrace(
                store="SPARQL",
                engine="sparql",
                title=f"License IRIs / strings declared for {full}",
                query=sparql,
                result_summary="error",
                error=str(exc),
            )
        )

    if not licenses:
        return MetricResult(
            recipes=lic_recipes,
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
            unification=(
                "Presence flag: ✓ if **SPARQL** returns ≥ 1 `schema:license` triple, ✗ otherwise."
            ),
        )

    display = ", ".join(_short_license(lic) for lic in licenses[:3])
    if len(licenses) > 3:
        display += f", +{len(licenses) - 3}"

    lic_recipes = _build_recipes(
        label="licenses_declared",
        traces=traces,
        extracts=[
            {
                "python": "[row[0] for row in r1.get('rows', [])]",
                "bash": "[.rows[][0]]",
                "js": "(r1.rows || []).map(row => row[0])",
            },
        ],
        combine={
            "python": "headline = '✓' if v1 else '✗'",
            "bash": 'if [ "$(echo "$v1" | jq length)" -gt 0 ]; then headline="✓"; else headline="✗"; fi',
            "js": "const headline = v1.length ? '✓' : '✗';",
        },
    )
    return MetricResult(
        recipes=lic_recipes,
        slug="licenses_declared",
        value="✓",
        label=display,
        secondary=f"{len(licenses)} declared",
        queries=traces,
        examples=[
            {"label": _short_license(lic), "detail": lic, "source": "SPARQL"}
            for lic in licenses
        ],
        notes=(
            "Snapshot — the SPARQL store keeps the latest crawl's "
            "license triple. Re-enriching this repo will update it. "
            "A ✓ here doesn't guarantee the license is OSI-approved "
            "(that's a separate CHAOSS metric)."
        ),
    )


# ── Metric 6 · Project Popularity (stars + forks + dependents) ──────────


def _metric_project_popularity(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
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
            traces.append(
                QueryTrace(
                    store="SPARQL",
                    engine="sparql",
                    title=f"Stars + fork count for {full}",
                    query=sparql,
                    result_summary=f"{stars or 0} stars · {forks or 0} forks",
                )
            )
        else:
            traces.append(
                QueryTrace(
                    store="SPARQL",
                    engine="sparql",
                    title=f"Stars + fork count for {full}",
                    query=sparql,
                    result_summary="repo not in graph",
                )
            )
    except Exception as exc:  # noqa: BLE001
        traces.append(
            QueryTrace(
                store="SPARQL",
                engine="sparql",
                title=f"Stars + fork count for {full}",
                query=sparql,
                result_summary="error",
                error=str(exc),
            )
        )

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
        traces.append(
            QueryTrace(
                store="Neo4j",
                engine="cypher",
                title=f"Top-20 dependents of {full}",
                query=cypher,
                result_summary=f"{dependents} total dependents in graph",
            )
        )
    except Exception as exc:  # noqa: BLE001
        traces.append(
            QueryTrace(
                store="Neo4j",
                engine="cypher",
                title=f"Top-20 dependents of {full}",
                query=cypher,
                result_summary="error",
                error=str(exc),
            )
        )

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

    # Three-tile mini stat row so each component is glanceable.
    tiles = []
    if stars is not None:
        tiles.append({"label": "stars", "value": stars, "icon": "★", "tone": "warn"})
    if forks is not None:
        tiles.append({"label": "forks", "value": forks, "icon": "⑂", "tone": "info"})
    if dependents:
        tiles.append(
            {"label": "dependents", "value": dependents, "icon": "↘", "tone": "good"}
        )

    popularity_recipes = _build_recipes(
        label="project_popularity",
        traces=traces,
        extracts=[
            # trace 1 — SPARQL stars + forks (two columns, ?stars + ?forks)
            {
                "python": "(int(r1['rows'][0][0]) if r1.get('rows') and r1['rows'][0][0] is not None else None,"
                " int(r1['rows'][0][1]) if r1.get('rows') and r1['rows'][0][1] is not None else None)",
                "bash": "[(.rows[0][0] // 0), (.rows[0][1] // 0)]",
                "js": "[r1.rows?.[0]?.[0] != null ? Number(r1.rows[0][0]) : null,"
                "  r1.rows?.[0]?.[1] != null ? Number(r1.rows[0][1]) : null]",
            },
            # trace 2 — Neo4j list of dependents (top 20)
            {
                "python": "[row[0] for row in r2.get('rows', [])]",
                "bash": "[.rows[][0]]",
                "js": "(r2.rows ?? []).map(row => row[0])",
            },
        ],
        combine={
            "python": (
                "stars, forks = v1\n"
                "dependents = len(v2)\n"
                "headline = stars if stars is not None else (dependents or forks or 0)"
            ),
            "bash": (
                "stars=$(echo \"$v1\" | jq '.[0]'); forks=$(echo \"$v1\" | jq '.[1]')\n"
                "dependents=$(echo \"$v2\" | jq 'length')\n"
                'if [ "$stars" != "0" ] && [ "$stars" != "null" ]; then headline=$stars;\n'
                'elif [ "$dependents" != "0" ];                  then headline=$dependents;\n'
                "else                                                  headline=$forks; fi"
            ),
            "js": (
                "const [stars, forks] = v1;\n"
                "const dependents = v2.length;\n"
                "const headline = stars ?? dependents ?? forks ?? 0;"
            ),
        },
    )
    return MetricResult(
        slug="project_popularity",
        value=headline,
        label=label,
        recipes=popularity_recipes,
        secondary=" · ".join(bits) if bits else None,
        queries=traces,
        examples=[
            {"label": name, "detail": "depends on this repo", "source": "Neo4j"}
            for name in dependent_names[:8]
        ],
        visual={"kind": "stat_tiles", "tiles": tiles},
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
    pl_recipes = None
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
        traces.append(
            QueryTrace(
                store="SPARQL",
                engine="sparql",
                title=f"Programming languages declared for {full}",
                query=sparql,
                result_summary=f"{len(languages)} language"
                + ("s" if len(languages) != 1 else ""),
            )
        )
    except Exception as exc:  # noqa: BLE001
        traces.append(
            QueryTrace(
                store="SPARQL",
                engine="sparql",
                title=f"Programming languages declared for {full}",
                query=sparql,
                result_summary="error",
                error=str(exc),
            )
        )

    if not languages:
        return MetricResult(
            recipes=pl_recipes,
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
            unification=(
                "Distinct `schema:programmingLanguage` triples in **SPARQL** (presence, not byte share — that's a Phase-3 ontology upgrade)."
            ),
        )

    pl_recipes = _build_recipes(
        label="programming_languages",
        traces=traces,
        extracts=[
            {
                "python": "[row[0] for row in r1.get('rows', [])]",
                "bash": "[.rows[][0]]",
                "js": "(r1.rows || []).map(row => row[0])",
            },
        ],
        combine={
            "python": "headline = len(v1)",
            "bash": 'headline=$(echo "$v1" | jq length)',
            "js": "const headline = v1.length;",
        },
    )
    return MetricResult(
        recipes=pl_recipes,
        slug="programming_languages",
        value=str(len(languages)),
        label="languages",
        secondary=None,
        queries=traces,
        examples=[
            {"label": lang, "detail": "", "source": "SPARQL"} for lang in languages
        ],
        visual={"kind": "pill_cloud", "pills": languages},
        notes=(
            "Snapshot. The current ontology stores languages as a flat "
            "set — no per-language size weighting. The CHAOSS "
            "'Programming Language Distribution' spec wants byte-level "
            "shares; that's Phase 3 once the extractor emits them."
        ),
    )


# ── Metric 8 · Activity Dates and Times ─────────────────────────────────


def _metric_activity_dates(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    """Monthly commit activity from GrimoireLab's git index. Returns a
    series so the card can render a sparkline.
    """
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []
    ad_recipes = None
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
            series.append(
                {
                    "date": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                    .date()
                    .isoformat()[:7],
                    "value": count,
                }
            )
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"Monthly commit histogram on {origin} since {cutoff_iso[:10]}",
                query=body_text,
                result_summary=f"{total} commits in {len(series)} months",
            )
        )
    else:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"Monthly commit histogram on {origin} since {cutoff_iso[:10]}",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or empty",
            )
        )

    if total == 0:
        return MetricResult(
            recipes=ad_recipes,
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
            unification=(
                "Sum of monthly bucket counts from one **OpenSearch** `date_histogram` on `grimoire_creation_date`."
            ),
        )

    busiest = max(series, key=lambda r: r["value"])

    ad_recipes = _build_recipes(
        label="activity_dates",
        traces=traces,
        extracts=[
            {
                "python": "sum(b['doc_count'] for b in r1.get('raw', {}).get('aggregations', {}).get('by_month', {}).get('buckets', []))",
                "bash": "[.raw.aggregations.by_month.buckets[].doc_count] | add // 0",
                "js": "(r1.raw?.aggregations?.by_month?.buckets || []).reduce((s, b) => s + b.doc_count, 0)",
            },
        ],
        combine={
            "python": "headline = v1",
            "bash": "headline=$v1",
            "js": "const headline = v1;",
        },
    )
    return MetricResult(
        recipes=ad_recipes,
        slug="activity_dates",
        value=str(total),
        label=f"commits (last {window_days} days)",
        secondary=f"busiest month: {busiest['date']} ({busiest['value']} commits)",
        series=series,
        series_unit="commits",
        queries=traces,
        notes=(
            "Date histogram on GrimoireLab's ``grimoire_creation_date`` "
            "field. Each bar = one calendar month. Bursty patterns "
            "(release-tag spikes) tell a different story from steady "
            "monthly contributions."
        ),
    )


# ── Metric 9 · Change Request Closure Ratio ─────────────────────────────


def _metric_closure_ratio(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
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
    # from rejected closes. track_total_hits gives an exact total even
    # past 10 000 PRs (the default trade-off in OpenSearch is to cap
    # the count there for speed).
    body = {
        "size": 0,
        "track_total_hits": True,
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
            "merged": {"filter": {"term": {"merged": True}}},
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
        merged = int(
            ((raw.get("aggregations") or {}).get("merged") or {}).get("doc_count") or 0
        )
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"PR state breakdown on {origin} since {cutoff_iso[:10]}",
                query=body_text,
                result_summary=f"{total} PRs · {closed} closed · {merged} merged · {open_} open",
            )
        )
    else:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"PR state breakdown on {origin} since {cutoff_iso[:10]}",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or github index empty",
            )
        )

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
            unification=(
                "`closed / total` from one **OpenSearch** PR aggregation (`state.terms` + `merged` filter on PRs created in window)."
            ),
        )

    ratio = closed / total
    closure_recipes = _build_recipes(
        label="closure_ratio",
        traces=traces,
        extracts=[
            # trace 1 — OpenSearch PR aggregation (one trace, multiple values)
            # We pull the whole response and let combine() destructure it.
            {"python": "r1", "bash": ".", "js": "r1"},
        ],
        combine={
            "python": (
                "total = v1.get('raw', {}).get('hits', {}).get('total', {}).get('value', 0)\n"
                "closed = 0\n"
                "for b in v1.get('raw', {}).get('aggregations', {}).get('by_state', {}).get('buckets', []):\n"
                "    if (b.get('key') or '').lower() == 'closed':\n"
                "        closed = b.get('doc_count', 0)\n"
                "headline = f'{(closed/total):.0%}' if total else '—'"
            ),
            "bash": (
                "total=$(echo \"$v1\" | jq '.raw.hits.total.value // 0')\n"
                'closed=$(echo "$v1" | jq \'[.raw.aggregations.by_state.buckets[] | select(.key == "closed") | .doc_count][0] // 0\')\n'
                'if [ "$total" -gt 0 ]; then\n'
                '  headline=$(awk -v c="$closed" -v t="$total" \'BEGIN { printf("%.0f%%", c/t*100) }\')\n'
                'else headline="—"; fi'
            ),
            "js": (
                "const total = v1.raw?.hits?.total?.value ?? 0;\n"
                "const closed = (v1.raw?.aggregations?.by_state?.buckets ?? [])\n"
                "  .find(b => (b.key || '').toLowerCase() === 'closed')?.doc_count ?? 0;\n"
                "const headline = total ? `${Math.round(closed/total*100)}%` : '—';"
            ),
        },
    )
    return MetricResult(
        slug="closure_ratio",
        value=f"{ratio:.0%}",
        label=f"closed (last {window_days} days)",
        recipes=closure_recipes,
        secondary=(
            f"{closed} closed of {total} PRs · {merged} merged · {open_} still open"
        ),
        queries=traces,
        visual={"kind": "donut", "fraction": ratio, "tone": "info"},
        notes=(
            "Closure ratio = closed / total. CHAOSS distinguishes "
            "*acceptance* (merged) from *closure* (closed without "
            "merge); the secondary line shows both so you can tell "
            "an active-reviewers project from a graveyard."
        ),
    )


# ── Metric 10 · Organizational Diversity ────────────────────────────────


def _metric_org_diversity(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    """How many distinct organisations does the contributor pool span?

    We use Path 1 — the ``pulse:ownedBy`` ownership chain — exclusively.
    Path 2 (Person → ``org:hasMembership`` → Org → ``schema:name``)
    pulls in self-declared affiliation strings whose canonicalisation
    is still upstream of SortingHat; "Swiss Data Science Center" vs
    "Swiss Data Science Centre" and SDSC-acronym collisions made the
    headline overcount. Ownership handles are GitHub-issued
    identifiers, no duplicates by construction.

    Concretely: count the distinct organisations that own *any*
    repository a contributor of this repo also touches. That's a
    "where do these people work, GitHub-wise" view, derived purely
    from clean ownership edges.
    """
    traces: list[QueryTrace] = []
    od_recipes = None
    org_names: list[str] = []
    org_count = 0

    sparql = (
        "# Path 1 — pure ``pulse:ownedBy`` ownership chain.\n"
        "# Each contributor of THIS repo contributes to other repos\n"
        "# too; each of those other repos has an org:Organization\n"
        "# owner (clean GitHub handle, no duplicates). Count distinct.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "PREFIX pulse:  <https://open-pulse.epfl.ch/ontology#>\n"
        "PREFIX org:    <http://www.w3.org/ns/org#>\n"
        "SELECT ?orgName (COUNT(DISTINCT ?person) AS ?n) WHERE {\n"
        f'  ?repo pulse:githubRepositoryHandle "{full}" .\n'
        "  ?person pulse:hasContribution ?c1 .\n"
        "  ?c1 pulse:contributionTo ?repo .\n"
        "  ?person pulse:hasContribution ?c2 .\n"
        "  ?c2 pulse:contributionTo ?otherRepo .\n"
        "  ?otherRepo pulse:ownedBy ?org .\n"
        "  ?org a org:Organization ;\n"
        "       schema:name ?orgName .\n"
        "}\n"
        "GROUP BY ?orgName\n"
        "ORDER BY DESC(?n) ?orgName"
    )
    try:
        rows = stores.sparql_select(sparql)
        # rows look like [{"orgName": {"value": "..."}, "n": {"value": "..."}}, …]
        org_names = [r["orgName"]["value"] for r in rows]
        org_count = len(org_names)
        traces.append(
            QueryTrace(
                store="SPARQL",
                engine="sparql",
                title=f"Distinct ownership orgs reachable from {full} contributors",
                query=sparql,
                result_summary=f"{org_count} distinct organisation"
                + ("s" if org_count != 1 else ""),
            )
        )
    except Exception as exc:  # noqa: BLE001
        traces.append(
            QueryTrace(
                store="SPARQL",
                engine="sparql",
                title=f"Distinct ownership orgs reachable from {full} contributors",
                query=sparql,
                result_summary="error",
                error=str(exc),
            )
        )

    if org_count == 0:
        return MetricResult(
            recipes=od_recipes,
            slug="org_diversity",
            value="—",
            label="no organisations reachable",
            secondary=None,
            queries=traces,
            notes=(
                "No contributor of this repo owns or contributes to "
                "any other repo whose owner is an ``org:Organization``. "
                "For a personal-account repo with no cross-org "
                "contribution, this is the expected zero."
            ),
            unification=(
                "Distinct organisations reachable from the repo's "
                "contributors via the **Path-1** ownership chain: "
                "`Contributor → other repos they touch → pulse:ownedBy → Org`."
            ),
        )

    od_recipes = _build_recipes(
        label="org_diversity",
        traces=traces,
        extracts=[
            {
                "python": "[row[0] for row in r1.get('rows', [])]",
                "bash": "[.rows[][0]]",
                "js": "(r1.rows || []).map(row => row[0])",
            },
        ],
        combine={
            "python": "headline = len(v1)",
            "bash": 'headline=$(echo "$v1" | jq length)',
            "js": "const headline = v1.length;",
        },
    )
    return MetricResult(
        recipes=od_recipes,
        slug="org_diversity",
        value=str(org_count),
        label="ownership orgs reachable",
        secondary=", ".join(org_names[:5])
        + (f", +{org_count - 5}" if org_count > 5 else ""),
        queries=traces,
        examples=[{"label": n, "detail": "", "source": "SPARQL"} for n in org_names],
        notes=(
            "Snapshot. Counts distinct ``org:Organization`` entities "
            "that own any repository the contributors of *this* repo "
            "also work on. CHAOSS reads this as a sustainability "
            "signal: a high number = the project's contributor base "
            "spans many GitHub organisations.\n\n"
            "Uses **Path 1** (ownership) only — the affiliation chain "
            "via ``org:hasMembership`` produced noisier counts because "
            "self-declared institutional names are not yet "
            "canonicalised upstream."
        ),
        unification=(
            "Distinct organisations reachable from the repo's "
            "contributors via the **Path-1** ownership chain: "
            "`Contributor → other repos they touch → pulse:ownedBy → Org`. "
            "Ignores self-declared affiliations (Path 2) until upstream "
            "name-canonicalisation lands."
        ),
    )


def _short_license(iri: str) -> str:
    """Squeeze an SPDX IRI down to its short id where possible."""
    if "/" not in iri:
        return iri
    tail = iri.rstrip("/").rsplit("/", 1)[-1]
    return tail.replace(".html", "").replace(".json", "")


# ── Metric 5 · Academic OS Project Impact ────────────────────────────────


def _metric_academic_impact(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    """Academic-impact proxy via the SPARQL graph.

    The current ontology has no direct article→repo predicate (e.g.
    schema:isBasedOn / schema:codeRepository / schema:citation are
    not emitted), so we lean on the strongest indirect link the data
    plane gives us: a *shared author*. We list ScholarlyArticles
    whose schema:author also has a pulse:hasContribution to the
    target repo. That answers the question "what academic work has
    the contributor community published?" — a weaker but transparent
    stand-in for "what cites the software".

    No vector store / RAG. Everything is reproducible by pasting the
    query into the ``/databases`` console.
    """
    traces: list[QueryTrace] = []
    ai_recipes = None

    sparql = (
        "# Shared-author chain: scholarly articles whose author has\n"
        "# contributed to the target repo. The OPTIONAL clauses pull\n"
        "# title and publication date so the examples row stays\n"
        "# readable and the secondary line can show the year span.\n"
        "PREFIX schema: <http://schema.org/>\n"
        "PREFIX pulse:  <https://open-pulse.epfl.ch/ontology#>\n"
        "SELECT DISTINCT ?article ?title ?datePublished WHERE {\n"
        f'  ?repo pulse:githubRepositoryHandle "{full}" .\n'
        "  ?person pulse:hasContribution ?contrib .\n"
        "  ?contrib pulse:contributionTo ?repo .\n"
        "  ?article a schema:ScholarlyArticle ;\n"
        "           schema:author ?person .\n"
        "  OPTIONAL { ?article schema:name ?title }\n"
        "  OPTIONAL { ?article schema:datePublished ?datePublished }\n"
        "}\n"
        "ORDER BY DESC(?datePublished)"
    )
    rows: list[dict[str, Any]] = []
    try:
        rows = stores.sparql_select(sparql) or []
        traces.append(
            QueryTrace(
                store="SPARQL",
                engine="sparql",
                title=f"Scholarly articles authored by contributors of {full}",
                query=sparql,
                result_summary=f"{len(rows)} article" + ("s" if len(rows) != 1 else ""),
            )
        )
    except Exception as exc:  # noqa: BLE001
        traces.append(
            QueryTrace(
                store="SPARQL",
                engine="sparql",
                title=f"Scholarly articles authored by contributors of {full}",
                query=sparql,
                result_summary="error",
                error=str(exc),
            )
        )

    if not rows:
        return MetricResult(
            recipes=ai_recipes,
            slug="academic_impact",
            value="0",
            label="no linked academic articles",
            secondary=None,
            queries=traces,
            notes=(
                "The current ontology has no direct article ↔ repo "
                "predicate, so this metric uses a shared-author chain: "
                "papers whose author also has a pulse:hasContribution "
                "to this repo. A zero can mean the contributors haven't "
                "linked publications in Infoscience, the publications "
                "are crawled but with un-matched author IDs, or simply "
                "that there are none. It does NOT mean no paper cites "
                "the software."
            ),
            unification=(
                "Count of **SPARQL** shared-author chain results: `Article → schema:author → Person → pulse:hasContribution → Repo` (no direct article↔repo predicate exists yet)."
            ),
        )

    examples: list[dict[str, str]] = []
    for r in rows[:8]:
        title = (r.get("title") or {}).get("value") or "(no title)"
        date = ((r.get("datePublished") or {}).get("value") or "")[:10]
        examples.append(
            {
                "label": title[:90],
                "detail": date,
                "source": "SPARQL",
            }
        )

    years: list[str] = []
    for r in rows:
        v = (r.get("datePublished") or {}).get("value") or ""
        if len(v) >= 4 and v[:4].isdigit():
            years.append(v[:4])
    span = ""
    yearly_series: list[dict[str, Any]] = []
    if years:
        yrs = sorted(years)
        span = f"{yrs[0]} → {yrs[-1]}" if yrs[0] != yrs[-1] else yrs[0]
        # Build a contiguous year-by-year series so the sparkline
        # reflects gaps (no-publication years) instead of compressing
        # them out.
        from collections import Counter

        counts = Counter(yrs)
        first_y, last_y = int(yrs[0]), int(yrs[-1])
        for y in range(first_y, last_y + 1):
            yearly_series.append({"date": str(y), "value": counts.get(str(y), 0)})

    ai_recipes = _build_recipes(
        label="academic_impact",
        traces=traces,
        extracts=[
            {
                "python": "len(r1.get('rows', []))",
                "bash": "[.rows[]] | length",
                "js": "(r1.rows || []).length",
            },
        ],
        combine={
            "python": "headline = v1",
            "bash": "headline=$v1",
            "js": "const headline = v1;",
        },
    )
    return MetricResult(
        recipes=ai_recipes,
        slug="academic_impact",
        value=str(len(rows)),
        label="papers by contributors",
        secondary=(f"publication span: {span}" if span else None),
        series=yearly_series,
        series_unit="papers",
        queries=traces,
        examples=examples,
        notes=(
            "Proxy metric: scholarly articles whose authors have a "
            "pulse:hasContribution to this repo. Computed entirely in "
            "SPARQL — no vector search, no RAG. Once the ontology "
            "emits a direct article↔repo predicate (schema:isBasedOn / "
            "schema:codeRepository / schema:citation), this metric "
            "will switch to it and become exact."
        ),
    )


# ── Metric 11 · Time to First Response ──────────────────────────────────


def _metric_first_response(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    """Median hours from PR/issue creation to the first response by
    someone other than the author. GrimoireLab enriches every github
    document with ``time_to_first_attention_without_bot`` (or
    ``time_to_first_attention_hours``) precomputed.
    """
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []
    fr_recipes = None

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
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"P50/P90 time to first response on {origin}",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or github index empty",
            )
        )
        return MetricResult(
            recipes=fr_recipes,
            slug="first_response",
            value="—",
            label="no data",
            secondary=None,
            queries=traces,
            notes=(
                "GrimoireLab hasn't indexed pull-request/issue traffic "
                "for this repo. The query above is what we'd run once "
                "github_*_enriched starts receiving documents."
            ),
            unification=(
                "P50 of GrimoireLab's pre-computed `time_to_first_attention_without_bot` enrichment via an **OpenSearch** percentiles agg."
            ),
        )

    aggs = raw.get("aggregations") or {}
    n = int(((aggs.get("count_with_response") or {}).get("doc_count") or 0))
    p50_raw = ((aggs.get("median") or {}).get("values") or {}).get("50.0")
    p90_raw = ((aggs.get("p90") or {}).get("values") or {}).get("90.0")
    p50 = float(p50_raw) if p50_raw is not None else None
    p90 = float(p90_raw) if p90_raw is not None else None

    traces.append(
        QueryTrace(
            store="OpenSearch",
            engine="opensearch",
            mode="dsl",
            title=f"P50/P90 time to first response on {origin} since {cutoff_iso[:10]}",
            query=body_text,
            result_summary=(
                f"{n} responses · P50 {p50:.1f} h · P90 {p90:.1f} h"
                if p50 is not None and p90 is not None
                else f"{n} responses, no percentile available"
            ),
        )
    )

    if not n or p50 is None:
        return MetricResult(
            recipes=fr_recipes,
            slug="first_response",
            value="—",
            label="no responses in window",
            secondary=None,
            queries=traces,
            notes=(
                "No github documents in the window carried a "
                "time_to_first_attention_without_bot value — either "
                "no PRs/issues opened, or none have been responded "
                "to yet."
            ),
            unification=(
                "P50 of GrimoireLab's pre-computed `time_to_first_attention_without_bot` enrichment via an **OpenSearch** percentiles agg."
            ),
        )

    fr_recipes = _build_recipes(
        label="first_response",
        traces=traces,
        extracts=[
            {
                "python": "r1.get('raw', {}).get('aggregations', {}).get('median', {}).get('values', {}).get('50.0')",
                "bash": '.raw.aggregations.median.values."50.0" // 0',
                "js": "r1.raw?.aggregations?.median?.values?.['50.0'] ?? 0",
            },
        ],
        combine={
            "python": "headline = f'{v1:.1f} h' if v1 else '—'",
            "bash": 'if [ "$v1" != "null" ] && [ "$v1" != "0" ]; then headline=$(awk -v v="$v1" \'BEGIN { printf("%.1f h", v) }\'); else headline="—"; fi',
            "js": "const headline = v1 ? `${v1.toFixed(1)} h` : '—';",
        },
    )
    return MetricResult(
        recipes=fr_recipes,
        slug="first_response",
        value=f"{p50:.1f} h",
        label=f"median response (last {window_days} days)",
        secondary=f"{n} responses · P90 {p90:.1f} h"
        if p90 is not None
        else f"{n} responses",
        queries=traces,
        notes=(
            "Median hours from PR/issue creation to the first comment "
            "by someone other than the author (bot comments excluded). "
            "GrimoireLab precomputes the per-document value; we just "
            "ask for the percentile."
        ),
    )


# ── Metric 12 · Issue Resolution Duration ───────────────────────────────


def _metric_issue_resolution(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    """Median days to close an issue (excludes PRs)."""
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []
    ir_recipes = None

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
        "track_total_hits": True,
        "aggs": {
            "median_days": {
                "percentiles": {"field": "time_open_days", "percents": [50]}
            },
            "p90_days": {"percentiles": {"field": "time_open_days", "percents": [90]}},
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/github_*_enriched/_search", body)
    if raw is None:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"P50/P90 issue close duration on {origin}",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or github index empty",
            )
        )
        return MetricResult(
            recipes=ir_recipes,
            slug="issue_resolution",
            value="—",
            label="no data",
            secondary=None,
            queries=traces,
            notes="github_*_enriched has no documents for this repo yet.",
            unification=(
                "P50 of GrimoireLab's `time_open_days` for issues (`pull_request:false`) that closed in the window — via **OpenSearch** percentiles agg."
            ),
        )

    total = int(((raw.get("hits") or {}).get("total") or {}).get("value") or 0)
    aggs = raw.get("aggregations") or {}
    p50_raw = ((aggs.get("median_days") or {}).get("values") or {}).get("50.0")
    p90_raw = ((aggs.get("p90_days") or {}).get("values") or {}).get("90.0")
    p50 = float(p50_raw) if p50_raw is not None else None
    p90 = float(p90_raw) if p90_raw is not None else None

    traces.append(
        QueryTrace(
            store="OpenSearch",
            engine="opensearch",
            mode="dsl",
            title=f"Issue close duration on {origin} since {cutoff_iso[:10]}",
            query=body_text,
            result_summary=(
                f"{total} closed issues · P50 {p50:.1f} d · P90 {p90:.1f} d"
                if p50 is not None and p90 is not None
                else f"{total} closed issues, no percentile"
            ),
        )
    )

    if not total or p50 is None:
        return MetricResult(
            recipes=ir_recipes,
            slug="issue_resolution",
            value="—",
            label="no closed issues in window",
            secondary=None,
            queries=traces,
            notes="No issues closed in the window for this repo.",
            unification=(
                "P50 of GrimoireLab's `time_open_days` for issues (`pull_request:false`) that closed in the window — via **OpenSearch** percentiles agg."
            ),
        )

    ir_recipes = _build_recipes(
        label="issue_resolution",
        traces=traces,
        extracts=[
            {
                "python": "r1.get('raw', {}).get('aggregations', {}).get('median_days', {}).get('values', {}).get('50.0')",
                "bash": '.raw.aggregations.median_days.values."50.0" // 0',
                "js": "r1.raw?.aggregations?.median_days?.values?.['50.0'] ?? 0",
            },
        ],
        combine={
            "python": "headline = f'{v1:.1f} d' if v1 else '—'",
            "bash": 'if [ "$v1" != "null" ] && [ "$v1" != "0" ]; then headline=$(awk -v v="$v1" \'BEGIN { printf("%.1f d", v) }\'); else headline="—"; fi',
            "js": "const headline = v1 ? `${v1.toFixed(1)} d` : '—';",
        },
    )
    return MetricResult(
        recipes=ir_recipes,
        slug="issue_resolution",
        value=f"{p50:.1f} d",
        label=f"median time to close (last {window_days} days)",
        secondary=(
            f"{total} closed · P90 {p90:.1f} d"
            if p90 is not None
            else f"{total} closed"
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
    sm_recipes = None

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
        "track_total_hits": True,
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/github_*_enriched/_search", body)
    if raw is None:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"Self-merged vs merged PRs on {origin}",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or github index empty",
            )
        )
        return MetricResult(
            recipes=sm_recipes,
            slug="self_merge",
            value="—",
            label="no data",
            secondary=None,
            queries=traces,
            notes="github_*_enriched has no documents for this repo yet.",
            unification=(
                "`self-merged / total-merged` — Painless script compares `user_login` vs `merge_author_login` inside an **OpenSearch** filter agg."
            ),
        )

    total_merged = int(((raw.get("hits") or {}).get("total") or {}).get("value") or 0)
    self_merged = int(
        ((raw.get("aggregations") or {}).get("self_merged") or {}).get("doc_count") or 0
    )

    traces.append(
        QueryTrace(
            store="OpenSearch",
            engine="opensearch",
            mode="dsl",
            title=f"Self-merged vs merged PRs on {origin} since {cutoff_iso[:10]}",
            query=body_text,
            result_summary=f"{self_merged} self / {total_merged} merged",
        )
    )

    if not total_merged:
        return MetricResult(
            recipes=sm_recipes,
            slug="self_merge",
            value="—",
            label="no merged PRs in window",
            secondary=None,
            queries=traces,
            notes="No PRs merged on this repo in the window.",
            unification=(
                "`self-merged / total-merged` — Painless script compares `user_login` vs `merge_author_login` inside an **OpenSearch** filter agg."
            ),
        )
    ratio = self_merged / total_merged
    # Self-merge is a signal we read inversely: high → weak review
    # gate, low → strong. Tone the donut accordingly.
    tone = "danger" if ratio >= 0.5 else "warn" if ratio >= 0.2 else "good"

    sm_recipes = _build_recipes(
        label="self_merge",
        traces=traces,
        extracts=[
            {
                "python": "r1",
                "bash": ".",
                "js": "r1",
            },
        ],
        combine={
            "python": "total = v1.get('raw', {}).get('hits', {}).get('total', {}).get('value', 0)\nself_merged = v1.get('raw', {}).get('aggregations', {}).get('self_merged', {}).get('doc_count', 0)\nheadline = f'{(self_merged/total):.0%}' if total else '—'",
            "bash": 'total=$(echo "$v1" | jq \'.raw.hits.total.value // 0\')\nself_merged=$(echo "$v1" | jq \'.raw.aggregations.self_merged.doc_count // 0\')\nif [ "$total" -gt 0 ]; then headline=$(awk -v s="$self_merged" -v t="$total" \'BEGIN { printf("%.0f%%", s/t*100) }\'); else headline="—"; fi',
            "js": "const total = v1.raw?.hits?.total?.value ?? 0;\nconst selfMerged = v1.raw?.aggregations?.self_merged?.doc_count ?? 0;\nconst headline = total ? `${Math.round(selfMerged/total*100)}%` : '—';",
        },
    )
    return MetricResult(
        recipes=sm_recipes,
        slug="self_merge",
        value=f"{ratio:.0%}",
        label=f"self-merged (last {window_days} days)",
        secondary=f"{self_merged} of {total_merged} merged PRs",
        queries=traces,
        visual={"kind": "donut", "fraction": ratio, "tone": tone},
        headline_tone=tone,
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
    burst_recipes = None

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
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"Daily commit histogram on {origin} for burstiness",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or git index empty",
            )
        )
        return MetricResult(
            recipes=burst_recipes,
            slug="burstiness",
            value="—",
            label="no data",
            secondary=None,
            queries=traces,
            notes="No git activity indexed for this repo.",
            unification=(
                "B = (σ − μ) / (σ + μ) on inter-arrival days between commit-days. Daily histogram from **OpenSearch**, post-processed in Python (`statistics.pstdev` + mean)."
            ),
        )

    buckets = (raw.get("aggregations") or {}).get("by_day", {}).get("buckets", [])
    active_days = len(buckets)
    if active_days < 3:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"Daily commit histogram on {origin} for burstiness",
                query=body_text,
                result_summary=f"{active_days} active days — too few for burstiness",
            )
        )
        return MetricResult(
            recipes=burst_recipes,
            slug="burstiness",
            value="—",
            label=f"only {active_days} active days",
            secondary=None,
            queries=traces,
            notes=(
                "Burstiness needs at least three active days in the "
                "window to compute inter-arrival gaps. Widen the "
                "window or pick a more active repo."
            ),
            unification=(
                "B = (σ − μ) / (σ + μ) on inter-arrival days between commit-days. Daily histogram from **OpenSearch**, post-processed in Python (`statistics.pstdev` + mean)."
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

    # Friendly label for the score's regime + a tone the gauge picks
    # up. Tone is purely cosmetic — no value judgement on bursty vs
    # periodic, just a different colour per regime.
    if burstiness > 0.3:
        regime = "bursty (irregular bursts)"
        tone = "warn"
    elif burstiness < -0.3:
        regime = "periodic (steady cadence)"
        tone = "good"
    else:
        regime = "Poisson-like (random)"
        tone = "info"

    traces.append(
        QueryTrace(
            store="OpenSearch",
            engine="opensearch",
            mode="dsl",
            title=f"Daily commit histogram on {origin} for burstiness",
            query=body_text,
            result_summary=(
                f"{active_days} active days · mean gap {mu:.2f} d · "
                f"σ {sigma:.2f} d → B={burstiness:.3f}"
            ),
        )
    )

    # Gauge visual: mark where on the [-1, +1] spectrum we sit. The
    # marker position (in %) is (B + 1) / 2 — so −1 = 0 % (far left),
    # 0 = 50 % (middle), +1 = 100 % (far right).
    marker_pct = round((burstiness + 1) / 2 * 100, 1)

    burst_recipes = _build_recipes(
        label="burstiness",
        traces=traces,
        extracts=[
            {
                "python": "r1",
                "bash": ".",
                "js": "r1",
            },
        ],
        combine={
            "python": "import statistics\nbuckets = v1.get('raw', {}).get('aggregations', {}).get('by_day', {}).get('buckets', [])\nif len(buckets) < 3:\n    headline = '—'\nelse:\n    ts = [b['key'] for b in buckets]\n    gaps = [(ts[i+1]-ts[i])/1000/86400 for i in range(len(ts)-1)]\n    mu = statistics.fmean(gaps)\n    sigma = statistics.pstdev(gaps) if len(gaps) > 1 else 0\n    B = (sigma - mu) / (sigma + mu) if (sigma + mu) else 0\n    headline = f'{B:+.2f}'",
            "bash": 'buckets=$(echo "$v1" | jq -c \'.raw.aggregations.by_day.buckets // []\')\nn=$(echo "$buckets" | jq length)\nif [ "$n" -lt 3 ]; then headline="—"; else\n  headline=$(echo "$buckets" | python3 -c \'import sys, json, statistics; bs=json.load(sys.stdin); ts=[b["key"] for b in bs]; gaps=[(ts[i+1]-ts[i])/1000/86400 for i in range(len(ts)-1)]; mu=statistics.fmean(gaps); sigma=statistics.pstdev(gaps) if len(gaps)>1 else 0; B=(sigma-mu)/(sigma+mu) if (sigma+mu) else 0; print(f"{B:+.2f}")\')\nfi',
            "js": "const buckets = v1.raw?.aggregations?.by_day?.buckets || [];\nlet headline;\nif (buckets.length < 3) { headline = '—'; }\nelse {\n  const ts = buckets.map(b => b.key);\n  const gaps = ts.slice(1).map((t, i) => (t - ts[i]) / 1000 / 86400);\n  const mu = gaps.reduce((a, b) => a + b, 0) / gaps.length;\n  const variance = gaps.reduce((s, g) => s + (g - mu) ** 2, 0) / gaps.length;\n  const sigma = Math.sqrt(variance);\n  const B = (sigma + mu) ? (sigma - mu) / (sigma + mu) : 0;\n  headline = (B >= 0 ? '+' : '') + B.toFixed(2);\n}",
        },
    )
    return MetricResult(
        recipes=burst_recipes,
        slug="burstiness",
        value=f"{burstiness:+.2f}",
        label=regime,
        secondary=(
            f"{active_days} active days · mean gap {mu:.1f} d · σ {sigma:.1f} d"
        ),
        queries=traces,
        visual={
            "kind": "gauge",
            "tone": tone,
            "marker_pct": marker_pct,
            "left_label": "−1 periodic",
            "right_label": "+1 bursty",
        },
        notes=(
            "B = (σ − μ) / (σ + μ) on inter-arrival days between "
            "commits, per Goh & Barabási (2008). Range: −1 (strictly "
            "periodic) through 0 (random Poisson) to +1 (heavy bursts "
            "with long silences). Computed client-side from a daily "
            "histogram — multiple commits in a single calendar day "
            "collapse to one event, so the score is a day-granularity "
            "approximation of the per-commit measure CHAOSS defines. "
            "The query shown above is exactly what ran."
        ),
    )


# ── Metric 15 · Contributor Absence Factor (a.k.a. bus factor) ──────────


def _metric_absence_factor(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    """CHAOSS-defined Contributor Absence Factor: the smallest number
    of distinct authors whose *combined* commit count is at least 50 %
    of the total. Low values mean the project depends on a tiny
    handful of people; high values mean the work is distributed.
    """
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []
    abs_recipes = None

    origin = f"https://github.com/{full}"
    # 500 author buckets is more than enough for any single-repo
    # window — once a repo has 500+ committers, the bus factor is
    # almost certainly large and the long tail doesn't change the
    # answer.
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
        "aggs": {"by_author": {"terms": {"field": "author_name", "size": 500}}},
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/git_*_enriched/_search", body)
    if raw is None:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"Per-author commit counts on {origin} for bus-factor",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or git index empty",
            )
        )
        return MetricResult(
            recipes=abs_recipes,
            slug="absence_factor",
            value="—",
            label="no data",
            secondary=None,
            queries=traces,
            notes="No git activity indexed for this repo.",
            unification=(
                "Walk descending **OpenSearch** terms agg until cumulative commits ≥ 50 %. Headline = N at threshold; example list shows each top contributor's share."
            ),
        )

    buckets = (raw.get("aggregations") or {}).get("by_author", {}).get("buckets", [])
    total = sum(int(b.get("doc_count") or 0) for b in buckets)
    if total == 0:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"Per-author commit counts on {origin}",
                query=body_text,
                result_summary="0 commits in window",
            )
        )
        return MetricResult(
            recipes=abs_recipes,
            slug="absence_factor",
            value="—",
            label="no commits in window",
            secondary=None,
            queries=traces,
            notes="Widen the window — the bus factor needs at least one commit.",
            unification=(
                "Walk descending **OpenSearch** terms agg until cumulative commits ≥ 50 %. Headline = N at threshold; example list shows each top contributor's share."
            ),
        )

    # Walk the (already-sorted-desc) bucket list and accumulate.
    factor = 0
    cumul = 0
    top_share: list[dict[str, str]] = []
    half = 0.5 * total
    for i, b in enumerate(buckets):
        cnt = int(b.get("doc_count") or 0)
        cumul += cnt
        factor = i + 1
        top_share.append(
            {
                "label": b.get("key") or "(anonymous)",
                "detail": f"{cnt} commits · {cnt / total:.0%}",
                "source": "OpenSearch",
            }
        )
        if cumul >= half:
            break

    # Friendly regime label — CHAOSS guidance suggests 1-2 is fragile.
    if factor <= 2:
        regime = "fragile · one or two key people"
        tone = "danger"
    elif factor <= 5:
        regime = "concentrated · few key contributors"
        tone = "warn"
    else:
        regime = "distributed"
        tone = "good"

    traces.append(
        QueryTrace(
            store="OpenSearch",
            engine="opensearch",
            mode="dsl",
            title=f"Per-author commit counts on {origin} since {cutoff_iso[:10]}",
            query=body_text,
            result_summary=(
                f"{len(buckets)} contributors · top {factor} = "
                f"{cumul / total:.0%} of {total} commits"
            ),
        )
    )

    # Rank-bar data — top-N contributors with their per-author share.
    # Use the full bucket list (truncated to 8) so the bars span the
    # whole contributor pool, not just the ones in the 50 % cut.
    rank_items: list[dict[str, Any]] = []
    if buckets:
        top = buckets[0].get("doc_count", 0) or 1
        for b in buckets[:8]:
            cnt = int(b.get("doc_count") or 0)
            rank_items.append(
                {
                    "label": b.get("key") or "(anonymous)",
                    "value": cnt,
                    "share": cnt / total if total else 0,
                    "bar": cnt / top,  # 0..1 for the bar width
                }
            )

    abs_recipes = _build_recipes(
        label="absence_factor",
        traces=traces,
        extracts=[
            {
                "python": "r1",
                "bash": ".",
                "js": "r1",
            },
        ],
        combine={
            "python": "buckets = v1.get('raw', {}).get('aggregations', {}).get('by_author', {}).get('buckets', [])\ntotal = sum(b['doc_count'] for b in buckets)\nhalf = 0.5 * total\nheadline = 0\ncumul = 0\nfor i, b in enumerate(buckets):\n    cumul += b['doc_count']\n    headline = i + 1\n    if cumul >= half:\n        break",
            "bash": 'buckets=$(echo "$v1" | jq -c \'.raw.aggregations.by_author.buckets // []\')\ntotal=$(echo "$buckets" | jq \'[.[] | .doc_count] | add // 0\')\nheadline=$(echo "$buckets" | python3 -c \'import sys, json; bs=json.load(sys.stdin); t=sum(b["doc_count"] for b in bs); half=t*0.5; c=0; f=0\nfor i,b in enumerate(bs):\n    c+=b["doc_count"]; f=i+1\n    if c>=half: break\nprint(f)\')',
            "js": "const buckets = v1.raw?.aggregations?.by_author?.buckets || [];\nconst total = buckets.reduce((s, b) => s + b.doc_count, 0);\nconst half = total * 0.5;\nlet headline = 0, cumul = 0;\nfor (let i = 0; i < buckets.length; i++) {\n  cumul += buckets[i].doc_count;\n  headline = i + 1;\n  if (cumul >= half) break;\n}",
        },
    )
    return MetricResult(
        recipes=abs_recipes,
        slug="absence_factor",
        value=str(factor),
        label=regime,
        secondary=(
            f"top {factor} of {len(buckets)} contributors carry "
            f"{cumul / total:.0%} of {total} commits"
        ),
        queries=traces,
        examples=top_share[:8],
        visual={"kind": "rank_bars", "bars": rank_items, "tone": tone},
        headline_tone=tone,
        notes=(
            "Also known as the 'bus factor'. CHAOSS defines it as the "
            "smallest N for which the top-N contributors' commits sum "
            "to at least half of the project's total. A factor of 1 "
            "means a single person could disappear and the project "
            "loses half its bandwidth overnight."
        ),
    )


# ── Metric 16 · Project Demographics ────────────────────────────────────


def _metric_demographics(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    """A simple population breakdown of the contributor pool: total,
    'core' (top contributors covering 80 % of commits), 'recent
    arrivals' (first commit in last 90 days), and 'dormant' (had
    activity earlier but none in the last 180 days).
    """
    traces: list[QueryTrace] = []
    dem_recipes = None

    origin = f"https://github.com/{full}"
    # Single query that gives us per-author first + last commit dates
    # AND commit counts — enough to compute every demographic bucket
    # we need.
    body = {
        "size": 0,
        "query": {"term": {"origin": origin}},
        "aggs": {
            "by_author": {
                "terms": {"field": "author_name", "size": 500},
                "aggs": {
                    "first": {"min": {"field": "grimoire_creation_date"}},
                    "last": {"max": {"field": "grimoire_creation_date"}},
                },
            }
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/git_*_enriched/_search", body)
    if raw is None:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"Per-author first/last commit + count on {origin}",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or git index empty",
            )
        )
        return MetricResult(
            recipes=dem_recipes,
            slug="project_demographics",
            value="—",
            label="no data",
            secondary=None,
            queries=traces,
            notes="No git activity indexed for this repo.",
            unification=(
                "**OpenSearch** terms agg with `min`/`max` sub-aggs per author. Buckets (`core`/`active`/`recent`/`dormant`) partitioned client-side."
            ),
        )

    buckets = (raw.get("aggregations") or {}).get("by_author", {}).get("buckets", [])
    total_contribs = len(buckets)
    if total_contribs == 0:
        return MetricResult(
            recipes=dem_recipes,
            slug="project_demographics",
            value="0",
            label="no contributors",
            secondary=None,
            queries=traces,
            notes="No commits indexed for this repo.",
            unification=(
                "**OpenSearch** terms agg with `min`/`max` sub-aggs per author. Buckets (`core`/`active`/`recent`/`dormant`) partitioned client-side."
            ),
        )

    total_commits = sum(int(b.get("doc_count") or 0) for b in buckets)

    # "Core" = smallest set covering 80 % of commits (sorted desc by
    # default). The bus factor uses 50 %; this is the wider "who
    # carries the work" view.
    core = 0
    cumul = 0
    for i, b in enumerate(buckets):
        cumul += int(b.get("doc_count") or 0)
        if cumul >= 0.8 * total_commits:
            core = i + 1
            break

    # Recent arrivals — first commit in last 90 days.
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    ninety_days_ms = 90 * 86400 * 1000
    one_eighty_days_ms = 180 * 86400 * 1000
    recent = sum(
        1
        for b in buckets
        if int((b.get("first") or {}).get("value") or 0) >= now_ms - ninety_days_ms
    )
    dormant = sum(
        1
        for b in buckets
        if int((b.get("last") or {}).get("value") or 0) < now_ms - one_eighty_days_ms
    )

    traces.append(
        QueryTrace(
            store="OpenSearch",
            engine="opensearch",
            mode="dsl",
            title=f"Per-author first/last commit + count on {origin}",
            query=body_text,
            result_summary=(
                f"{total_contribs} contributors · core {core} · "
                f"recent {recent} · dormant {dormant}"
            ),
        )
    )

    # Compose a 4-segment stacked bar covering the whole pool. The
    # buckets overlap conceptually (a "recent arrival" can also be
    # "active"), so we partition cleanly into:
    #   core    — top N by commit count
    #   recent  — first commit in last 90 d AND not already core
    #   dormant — last commit predates 180 d AND not core
    #   other   — middle of the pack
    core_set = {b.get("key") for b in buckets[:core]}
    recent_set: set[str] = set()
    dormant_set: set[str] = set()
    for b in buckets:
        key = b.get("key")
        if key in core_set:
            continue
        first_val = int((b.get("first") or {}).get("value") or 0)
        last_val = int((b.get("last") or {}).get("value") or 0)
        if first_val >= now_ms - ninety_days_ms:
            recent_set.add(key)
        elif last_val < now_ms - one_eighty_days_ms:
            dormant_set.add(key)
    other = total_contribs - len(core_set) - len(recent_set) - len(dormant_set)
    if other < 0:
        other = 0
    segments = [
        {"label": "core", "value": len(core_set), "tone": "good"},
        {"label": "active", "value": other, "tone": "info"},
        {"label": "recent", "value": len(recent_set), "tone": "warn"},
        {"label": "dormant", "value": len(dormant_set), "tone": "danger"},
    ]

    dem_recipes = _build_recipes(
        label="project_demographics",
        traces=traces,
        extracts=[
            {
                "python": "len(r1.get('raw', {}).get('aggregations', {}).get('by_author', {}).get('buckets', []))",
                "bash": ".raw.aggregations.by_author.buckets | length",
                "js": "(r1.raw?.aggregations?.by_author?.buckets || []).length",
            },
        ],
        combine={
            "python": "headline = v1",
            "bash": "headline=$v1",
            "js": "const headline = v1;",
        },
    )
    return MetricResult(
        recipes=dem_recipes,
        slug="project_demographics",
        value=str(total_contribs),
        label="total contributors (all-time, OpenSearch)",
        secondary=(
            f"core {core} (80 % of {total_commits} commits) · "
            f"recent arrivals {recent} (last 90 d) · "
            f"dormant {dormant} (no commit in 180 d)"
        ),
        queries=traces,
        visual={"kind": "stacked_bar", "segments": segments},
        notes=(
            "Demographics rolled up from the same git_*_enriched index "
            "that powers the activity sparkline. 'Core' uses CHAOSS's "
            "80 % threshold (vs the bus-factor's 50 %); 'recent' marks "
            "first-time contributors in the last 90 days; 'dormant' "
            "marks people whose last commit on this repo predates the "
            "last 180 days."
        ),
    )


# ── Metric 17 · Bot Activity ─────────────────────────────────────────────

# Author-name substrings that almost certainly indicate a bot. The
# match is case-insensitive via the wildcard-on-keyword pattern below.
_BOT_PATTERNS: tuple[str, ...] = (
    "*[bot]*",
    "*-bot",
    "bot-*",
    "*github-actions*",
    "*dependabot*",
    "*renovate*",
    "*pre-commit-ci*",
    "*release-please*",
    "*stale*",
    "*mergify*",
)


def _metric_bot_activity(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    """Fraction of commits authored by recognised bots — high values
    are not inherently bad (security bots, lockfile bots, CI signers
    are all useful) but they distort raw commit counts."""
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []
    bot_recipes = None

    origin = f"https://github.com/{full}"
    # Bot signal = GrimoireLab's typed enrichment (author_bot: true)
    # OR an author_name that matches one of our well-known wildcards.
    # Using both is belt-and-braces: GrimoireLab catches bots that
    # don't match our patterns (e.g. a custom CI signing identity),
    # and the wildcards catch bots GrimoireLab hasn't yet flagged.
    bot_should = [{"term": {"author_bot": True}}] + [
        {"wildcard": {"author_name": p}} for p in _BOT_PATTERNS
    ]
    body = {
        "size": 0,
        "track_total_hits": True,
        "query": {
            "bool": {
                "must": [
                    {"term": {"origin": origin}},
                    {"range": {"grimoire_creation_date": {"gte": cutoff_iso}}},
                ]
            }
        },
        "aggs": {
            "bots": {
                "filter": {"bool": {"should": bot_should, "minimum_should_match": 1}},
                "aggs": {"by_bot": {"terms": {"field": "author_name", "size": 10}}},
            },
            "by_month": {
                "date_histogram": {
                    "field": "grimoire_creation_date",
                    "calendar_interval": "month",
                    "min_doc_count": 0,
                },
                "aggs": {
                    "bot_count": {
                        "filter": {
                            "bool": {"should": bot_should, "minimum_should_match": 1}
                        }
                    }
                },
            },
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/git_*_enriched/_search", body)
    if raw is None:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"Commits matching known bot patterns on {origin}",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or git index empty",
            )
        )
        return MetricResult(
            recipes=bot_recipes,
            slug="bot_activity",
            value="—",
            label="no data",
            secondary=None,
            queries=traces,
            notes="No git activity indexed for this repo.",
            unification=(
                "bot-matching commits / total. Detection = **OpenSearch** `author_bot:true` OR any of 10 wildcard author-name patterns (`*[bot]*`, `*dependabot*`, …)."
            ),
        )

    total = int(((raw.get("hits") or {}).get("total") or {}).get("value") or 0)
    bot_doc_count = int(
        ((raw.get("aggregations") or {}).get("bots") or {}).get("doc_count") or 0
    )
    bot_buckets = (
        ((raw.get("aggregations") or {}).get("bots") or {})
        .get("by_bot", {})
        .get("buckets", [])
    )

    traces.append(
        QueryTrace(
            store="OpenSearch",
            engine="opensearch",
            mode="dsl",
            title=f"Bot vs human commits on {origin} since {cutoff_iso[:10]}",
            query=body_text,
            result_summary=f"{bot_doc_count} bot commits of {total} total",
        )
    )

    if not total:
        return MetricResult(
            recipes=bot_recipes,
            slug="bot_activity",
            value="—",
            label="no commits in window",
            secondary=None,
            queries=traces,
            notes="Widen the window to compute bot share.",
            unification=(
                "bot-matching commits / total. Detection = **OpenSearch** `author_bot:true` OR any of 10 wildcard author-name patterns (`*[bot]*`, `*dependabot*`, …)."
            ),
        )

    ratio = bot_doc_count / total
    bot_series: list[dict[str, Any]] = []
    for b in ((raw.get("aggregations") or {}).get("by_month") or {}).get("buckets", []):
        ts_ms = int(b.get("key") or 0)
        cnt = int((b.get("bot_count") or {}).get("doc_count") or 0)
        bot_series.append(
            {
                "date": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                .date()
                .isoformat()[:7],
                "value": cnt,
            }
        )

    bot_recipes = _build_recipes(
        label="bot_activity",
        traces=traces,
        extracts=[
            {
                "python": "r1",
                "bash": ".",
                "js": "r1",
            },
        ],
        combine={
            "python": "total = v1.get('raw', {}).get('hits', {}).get('total', {}).get('value', 0)\nbots = v1.get('raw', {}).get('aggregations', {}).get('bots', {}).get('doc_count', 0)\nheadline = f'{(bots/total):.0%}' if total else '—'",
            "bash": 'total=$(echo "$v1" | jq \'.raw.hits.total.value // 0\')\nbots=$(echo "$v1" | jq \'.raw.aggregations.bots.doc_count // 0\')\nif [ "$total" -gt 0 ]; then headline=$(awk -v b="$bots" -v t="$total" \'BEGIN { printf("%.0f%%", b/t*100) }\'); else headline="—"; fi',
            "js": "const total = v1.raw?.hits?.total?.value ?? 0;\nconst bots = v1.raw?.aggregations?.bots?.doc_count ?? 0;\nconst headline = total ? `${Math.round(bots/total*100)}%` : '—';",
        },
    )
    return MetricResult(
        recipes=bot_recipes,
        slug="bot_activity",
        value=f"{ratio:.0%}",
        label=f"bot share (last {window_days} days)",
        secondary=f"{bot_doc_count} of {total} commits matched a bot pattern",
        series=bot_series,
        series_unit="bot commits",
        visual={"kind": "donut", "fraction": ratio, "tone": "info"},
        examples=[
            {
                "label": b.get("key") or "(unnamed)",
                "detail": f"{int(b.get('doc_count') or 0)} commits",
                "source": "OpenSearch",
            }
            for b in bot_buckets[:8]
        ],
        queries=traces,
        notes=(
            "Primary signal is GrimoireLab's ``author_bot: true`` "
            "enrichment flag — set by the SortingHat / Perceval "
            "pipeline based on its own heuristics. We also OR-match "
            "well-known wildcard patterns ("
            + ", ".join(repr(p) for p in _BOT_PATTERNS)
            + ") so a custom CI identity that GrimoireLab hasn't "
            "seen is still picked up. A high share is not bad in "
            "itself — it usually means Dependabot is active or "
            "release-please is signing tags."
        ),
    )


# ── Metric 18-20 · Issues lifecycle (New / Active / Closed) ─────────────


def _issues_count(
    full: str,
    window_days: int,
    *,
    slug: str,
    title: str,
    extra_must: list[dict[str, Any]],
    date_field: str,
    label: str,
    notes: str,
) -> MetricResult:
    """Shared helper for the three issues-lifecycle metrics. They all
    look the same shape — a filtered count of github documents in the
    window — so we keep one implementation and parameterise the bits
    that differ (which date field to range on, what extra clauses to
    add, what to tell the user).
    """
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []

    origin = f"https://github.com/{full}"
    body = {
        "size": 0,
        "track_total_hits": True,
        "query": {
            "bool": {
                "must": [
                    {"term": {"origin": origin}},
                    {"term": {"pull_request": False}},
                    {"range": {date_field: {"gte": cutoff_iso}}},
                    *extra_must,
                ]
            }
        },
        "aggs": {
            # Monthly histogram drives the sparkline. The same date
            # field used for the range filter is the bucketing field
            # so the bars line up with the headline count.
            "by_month": {
                "date_histogram": {
                    "field": date_field,
                    "calendar_interval": "month",
                    "min_doc_count": 0,
                }
            }
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/github_*_enriched/_search", body)
    if raw is None:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"{title} on {origin}",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or github index empty",
            )
        )
        return MetricResult(
            slug=slug,
            value="—",
            label="no data",
            secondary=None,
            queries=traces,
            notes="github_*_enriched has no documents for this repo yet.",
        )
    total = int(((raw.get("hits") or {}).get("total") or {}).get("value") or 0)
    monthly: list[dict[str, Any]] = []
    for b in ((raw.get("aggregations") or {}).get("by_month") or {}).get("buckets", []):
        ts_ms = int(b.get("key") or 0)
        cnt = int(b.get("doc_count") or 0)
        monthly.append(
            {
                "date": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                .date()
                .isoformat()[:7],
                "value": cnt,
            }
        )
    traces.append(
        QueryTrace(
            store="OpenSearch",
            engine="opensearch",
            mode="dsl",
            title=f"{title} on {origin} since {cutoff_iso[:10]}",
            query=body_text,
            result_summary=f"{total} issues · {len(monthly)} months in series",
        )
    )
    if not total:
        return MetricResult(
            slug=slug,
            value="0",
            label=label,
            secondary=None,
            queries=traces,
            notes=notes,
        )
    return MetricResult(
        slug=slug,
        value=str(total),
        label=label,
        secondary=None,
        queries=traces,
        notes=notes,
        series=monthly,
        series_unit="issues",
    )


def _metric_issues_new(full: str, canonical_url: str, window_days: int) -> MetricResult:
    return _issues_count(
        full,
        window_days,
        slug="issues_new",
        title="Issues newly opened",
        extra_must=[],
        date_field="created_at",
        label=f"opened (last {window_days} days)",
        notes=(
            "All issues created on this repo in the window, excluding "
            "pull requests. A spike here without a matching spike in "
            "issues_closed indicates growing backlog."
        ),
    )


def _metric_issues_active(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    return _issues_count(
        full,
        window_days,
        slug="issues_active",
        title="Issues with any activity",
        extra_must=[],
        date_field="updated_at",
        label=f"touched (last {window_days} days)",
        notes=(
            "Issues that received any update (new comment, label, "
            "state change) in the window. Excludes PRs."
        ),
    )


def _metric_issues_closed(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    return _issues_count(
        full,
        window_days,
        slug="issues_closed",
        title="Issues closed",
        extra_must=[{"term": {"state": "closed"}}],
        date_field="closed_at",
        label=f"closed (last {window_days} days)",
        notes=(
            "Issues whose ``state`` flipped to closed inside the "
            "window. Pair with issues_new for an inflow vs outflow "
            "view of the backlog."
        ),
    )


# ── Metric 21 · Change Request Reviews ──────────────────────────────────


def _metric_cr_reviews(full: str, canonical_url: str, window_days: int) -> MetricResult:
    """How many pull requests received any review in the window?
    GrimoireLab enriches every PR doc with ``num_review_comments``
    and ``num_review_comments_without_bot``; we ask how many PRs
    have at least one review comment from a non-bot.
    """
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []
    cr_recipes = None

    origin = f"https://github.com/{full}"
    body = {
        "size": 0,
        "track_total_hits": True,
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
            "reviewed": {
                "filter": {"range": {"num_review_comments_without_bot": {"gt": 0}}}
            }
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/github_*_enriched/_search", body)
    if raw is None:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"Reviewed PR count on {origin}",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or github index empty",
            )
        )
        return MetricResult(
            recipes=cr_recipes,
            slug="cr_reviews",
            value="—",
            label="no data",
            secondary=None,
            queries=traces,
            notes="github_*_enriched has no documents for this repo yet.",
            unification=(
                "PRs where **OpenSearch** `num_review_comments_without_bot > 0` — filter agg inside a windowed PR query."
            ),
        )
    total = int(((raw.get("hits") or {}).get("total") or {}).get("value") or 0)
    reviewed = int(
        ((raw.get("aggregations") or {}).get("reviewed") or {}).get("doc_count") or 0
    )
    traces.append(
        QueryTrace(
            store="OpenSearch",
            engine="opensearch",
            mode="dsl",
            title=f"PRs with at least one non-bot review on {origin} since {cutoff_iso[:10]}",
            query=body_text,
            result_summary=f"{reviewed} reviewed of {total} PRs",
        )
    )
    if not total:
        return MetricResult(
            recipes=cr_recipes,
            slug="cr_reviews",
            value="—",
            label="no PRs in window",
            secondary=None,
            queries=traces,
            notes="No pull requests opened on this repo in the window.",
            unification=(
                "PRs where **OpenSearch** `num_review_comments_without_bot > 0` — filter agg inside a windowed PR query."
            ),
        )
    ratio = reviewed / total
    # High review-rate = healthy review culture; low = concerning.
    tone = "good" if ratio >= 0.6 else "warn" if ratio >= 0.3 else "danger"

    cr_recipes = _build_recipes(
        label="cr_reviews",
        traces=traces,
        extracts=[
            {
                "python": "r1.get('raw', {}).get('aggregations', {}).get('reviewed', {}).get('doc_count', 0)",
                "bash": ".raw.aggregations.reviewed.doc_count // 0",
                "js": "r1.raw?.aggregations?.reviewed?.doc_count ?? 0",
            },
        ],
        combine={
            "python": "headline = v1",
            "bash": "headline=$v1",
            "js": "const headline = v1;",
        },
    )
    return MetricResult(
        recipes=cr_recipes,
        slug="cr_reviews",
        value=str(reviewed),
        label=f"reviewed PRs (last {window_days} days)",
        secondary=f"{reviewed} of {total} PRs ({ratio:.0%}) had a non-bot review",
        queries=traces,
        visual={"kind": "donut", "fraction": ratio, "tone": tone},
        notes=(
            "Counts PRs whose ``num_review_comments_without_bot`` is "
            "positive — i.e. at least one review comment from a human. "
            "Pair with self_merge to read code-review culture."
        ),
        unification=(
            "PRs where **OpenSearch** `num_review_comments_without_bot > 0` — filter agg inside a windowed PR query."
        ),
    )


# ── Metric 22 · Code Changes Lines ──────────────────────────────────────


def _metric_code_lines(full: str, canonical_url: str, window_days: int) -> MetricResult:
    """Sum of lines added + removed across commits in the window."""
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []
    cl_recipes = None

    origin = f"https://github.com/{full}"
    body = {
        "size": 0,
        "track_total_hits": True,
        "query": {
            "bool": {
                "must": [
                    {"term": {"origin": origin}},
                    {"range": {"grimoire_creation_date": {"gte": cutoff_iso}}},
                ]
            }
        },
        "aggs": {
            "lines_added": {"sum": {"field": "lines_added"}},
            "lines_removed": {"sum": {"field": "lines_removed"}},
            "files": {"sum": {"field": "files"}},
            "by_month": {
                "date_histogram": {
                    "field": "grimoire_creation_date",
                    "calendar_interval": "month",
                    "min_doc_count": 0,
                },
                "aggs": {
                    "added": {"sum": {"field": "lines_added"}},
                    "removed": {"sum": {"field": "lines_removed"}},
                },
            },
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/git_*_enriched/_search", body)
    if raw is None:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"Lines added + removed on {origin}",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or git index empty",
            )
        )
        return MetricResult(
            recipes=cl_recipes,
            slug="code_lines",
            value="—",
            label="no data",
            secondary=None,
            queries=traces,
            notes="No git activity indexed for this repo.",
            unification=(
                "`sum(lines_added) + sum(lines_removed)` across commits in window from **OpenSearch**. Monthly churn histogram drives the sparkline."
            ),
        )
    commits = int(((raw.get("hits") or {}).get("total") or {}).get("value") or 0)
    aggs = raw.get("aggregations") or {}
    added = int((aggs.get("lines_added") or {}).get("value") or 0)
    removed = int((aggs.get("lines_removed") or {}).get("value") or 0)
    files = int((aggs.get("files") or {}).get("value") or 0)
    delta = added + removed
    # Median lines-per-commit — gives the user a sense of commit size
    # (a few giant commits vs many small ones produce the same total).
    avg_per_commit = (delta // commits) if commits else 0
    monthly_churn: list[dict[str, Any]] = []
    for b in (aggs.get("by_month") or {}).get("buckets", []):
        ts_ms = int(b.get("key") or 0)
        a = int((b.get("added") or {}).get("value") or 0)
        r = int((b.get("removed") or {}).get("value") or 0)
        monthly_churn.append(
            {
                "date": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                .date()
                .isoformat()[:7],
                "value": a + r,
            }
        )
    traces.append(
        QueryTrace(
            store="OpenSearch",
            engine="opensearch",
            mode="dsl",
            title=f"Lines changed on {origin} since {cutoff_iso[:10]}",
            query=body_text,
            result_summary=f"+{added} −{removed} across {commits} commits / {files} files",
        )
    )
    if not commits:
        return MetricResult(
            recipes=cl_recipes,
            slug="code_lines",
            value="—",
            label="no commits in window",
            secondary=None,
            queries=traces,
            notes="Widen the window to compute line churn.",
            unification=(
                "`sum(lines_added) + sum(lines_removed)` across commits in window from **OpenSearch**. Monthly churn histogram drives the sparkline."
            ),
        )

    cl_recipes = _build_recipes(
        label="code_lines",
        traces=traces,
        extracts=[
            {
                "python": "(int(r1.get('raw', {}).get('aggregations', {}).get('lines_added', {}).get('value', 0)), int(r1.get('raw', {}).get('aggregations', {}).get('lines_removed', {}).get('value', 0)))",
                "bash": "{added: (.raw.aggregations.lines_added.value // 0), removed: (.raw.aggregations.lines_removed.value // 0)}",
                "js": "({added: r1.raw?.aggregations?.lines_added?.value ?? 0, removed: r1.raw?.aggregations?.lines_removed?.value ?? 0})",
            },
        ],
        combine={
            "python": "added, removed = v1\nheadline = f'{added + removed:,}'",
            "bash": 'added=$(echo "$v1" | jq \'.added\'); removed=$(echo "$v1" | jq \'.removed\')\nheadline=$(awk -v a="$added" -v r="$removed" \'BEGIN { printf("%\\047d", a + r) }\')',
            "js": "const headline = (v1.added + v1.removed).toLocaleString();",
        },
    )
    return MetricResult(
        recipes=cl_recipes,
        slug="code_lines",
        value=f"{delta:,}",
        label=f"lines changed (last {window_days} days)",
        secondary=(
            f"+{added:,} added · −{removed:,} removed · "
            f"{commits} commits · {files} file-changes · "
            f"~{avg_per_commit:,} lines / commit"
        ),
        series=monthly_churn,
        series_unit="lines",
        queries=traces,
        notes=(
            "Sums GrimoireLab's per-commit ``lines_added`` and "
            "``lines_removed``. Vendored or generated files inflate "
            "this — a one-line refactor can still touch thousands of "
            "lines if a lockfile lives in the repo."
        ),
        unification=(
            "`sum(lines_added) + sum(lines_removed)` across commits in window from **OpenSearch**. Monthly churn histogram drives the sparkline."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────
#  Phase 6 — six more CHAOSS Level-0 metrics: contributor-cadence
#  partitions (inactive / occasional) and PR-lifecycle counts
#  (accepted / declined / duration / time-to-close).
# ─────────────────────────────────────────────────────────────────────────


# ── Metric 23 · Inactive Contributors ────────────────────────────────────


def _metric_inactive_contributors(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    """Distinct authors who *have* contributed to this repo but whose
    most recent commit predates the window. Mirrors the "dormant"
    bucket in ``project_demographics`` as a standalone metric.
    """
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    traces: list[QueryTrace] = []
    ic_recipes = None

    origin = f"https://github.com/{full}"
    body = {
        "size": 0,
        "query": {"term": {"origin": origin}},
        "aggs": {
            "by_author": {
                "terms": {"field": "author_name", "size": 1000},
                "aggs": {"last": {"max": {"field": "grimoire_creation_date"}}},
            }
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/git_*_enriched/_search", body)
    if raw is None:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"Last-commit per author on {origin}",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or git index empty",
            )
        )
        return MetricResult(
            slug="inactive_contributors",
            value="—",
            label="no data",
            secondary=None,
            queries=traces,
            notes="No git activity indexed for this repo.",
            unification="Single OpenSearch terms agg + max-date sub-agg.",
        )

    buckets = (raw.get("aggregations") or {}).get("by_author", {}).get("buckets", [])
    inactive = [
        b for b in buckets if int((b.get("last") or {}).get("value") or 0) < cutoff_ms
    ]
    n_total = len(buckets)
    n_inactive = len(inactive)
    ratio = (n_inactive / n_total) if n_total else 0.0

    traces.append(
        QueryTrace(
            store="OpenSearch",
            engine="opensearch",
            mode="dsl",
            title=f"Authors whose last commit predates {cutoff_iso[:10]}",
            query=body_text,
            result_summary=f"{n_inactive} inactive of {n_total} all-time contributors",
        )
    )

    examples: list[dict[str, str]] = []
    for b in inactive[:8]:
        ts_s = int((b.get("last") or {}).get("value") or 0) // 1000
        examples.append(
            {
                "label": b.get("key") or "(anonymous)",
                "detail": datetime.fromtimestamp(ts_s, tz=timezone.utc)
                .date()
                .isoformat()
                if ts_s
                else "",
                "source": "OpenSearch",
            }
        )

    # Inverse tone — high inactive share is a red flag.
    tone = "good" if ratio < 0.3 else ("warn" if ratio < 0.7 else "danger")
    ic_recipes = _build_recipes(
        label="inactive_contributors",
        traces=traces,
        extracts=[{"python": "r1", "bash": ".", "js": "r1"}],
        combine={
            "python": (
                "cutoff_ms = int((__import__('time').time() - "
                + str(window_days)
                + " * 86400) * 1000)\n"
                "buckets = v1.get('raw', {}).get('aggregations', {}).get('by_author', {}).get('buckets', [])\n"
                "inactive = [b for b in buckets if int((b.get('last') or {}).get('value') or 0) < cutoff_ms]\n"
                "headline = len(inactive)"
            ),
            "bash": (
                f'cutoff_ms=$(python3 -c "import time; print(int((time.time() - {window_days} * 86400) * 1000))")\n'
                'headline=$(echo "$v1" | jq --argjson c "$cutoff_ms" '
                "'[.raw.aggregations.by_author.buckets[] | select((.last.value // 0) < $c)] | length')"
            ),
            "js": (
                f"const cutoffMs = Date.now() - {window_days} * 86400 * 1000;\n"
                "const buckets = v1.raw?.aggregations?.by_author?.buckets || [];\n"
                "const headline = buckets.filter(b => (b.last?.value || 0) < cutoffMs).length;"
            ),
        },
    )

    return MetricResult(
        slug="inactive_contributors",
        value=str(n_inactive),
        label=f"no commit in last {window_days} days",
        secondary=f"{n_inactive} of {n_total} all-time contributors · {ratio:.0%}",
        queries=traces,
        examples=examples,
        recipes=ic_recipes,
        visual={"kind": "donut", "fraction": ratio, "tone": tone},
        headline_tone=tone,
        unification=(
            "Single **OpenSearch** terms agg on ``author_name`` with a "
            "``max(grimoire_creation_date)`` sub-agg. Bucket is "
            "inactive if its max-date predates the cutoff (now − "
            f"{window_days}d), counted client-side."
        ),
        notes=(
            "Counts authors who **have** contributed at some point but "
            "whose most recent commit is older than the window. Pair "
            "with ``contributors`` for the live-side balance. A rising "
            "inactive share is a sustainability warning signal."
        ),
    )


# ── Metric 24 · Occasional Contributors ──────────────────────────────────

_OCCASIONAL_THRESHOLD = 4


def _metric_occasional_contributors(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    """Authors with ≤ N commits in window — the "drive-by" segment.

    Threshold (4) matches CHAOSS's stock Occasional-Contributors
    definition. Counts distinct authors; a single author with 3
    commits counts once.
    """
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []
    oc_recipes = None
    threshold = _OCCASIONAL_THRESHOLD

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
        "aggs": {"by_author": {"terms": {"field": "author_name", "size": 1000}}},
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/git_*_enriched/_search", body)
    if raw is None:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"Per-author commit counts on {origin}",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or git index empty",
            )
        )
        return MetricResult(
            slug="occasional_contributors",
            value="—",
            label="no data",
            secondary=None,
            queries=traces,
            notes="No git activity indexed for this repo.",
            unification=(
                f"Terms agg on ``author_name`` filtered to last {window_days} d, "
                f"client-side filter to ``doc_count ≤ {threshold}``."
            ),
        )

    buckets = (raw.get("aggregations") or {}).get("by_author", {}).get("buckets", [])
    occasional = [b for b in buckets if int(b.get("doc_count") or 0) <= threshold]
    n_total = len(buckets)
    n_occasional = len(occasional)
    ratio = (n_occasional / n_total) if n_total else 0.0

    traces.append(
        QueryTrace(
            store="OpenSearch",
            engine="opensearch",
            mode="dsl",
            title=f"Authors with ≤ {threshold} commits in last {window_days} d",
            query=body_text,
            result_summary=f"{n_occasional} occasional of {n_total} active contributors",
        )
    )

    examples: list[dict[str, str]] = []
    for b in occasional[:8]:
        cnt = int(b.get("doc_count") or 0)
        examples.append(
            {
                "label": b.get("key") or "(anonymous)",
                "detail": f"{cnt} commit" + ("s" if cnt != 1 else ""),
                "source": "OpenSearch",
            }
        )

    oc_recipes = _build_recipes(
        label="occasional_contributors",
        traces=traces,
        extracts=[{"python": "r1", "bash": ".", "js": "r1"}],
        combine={
            "python": (
                "buckets = v1.get('raw', {}).get('aggregations', {}).get('by_author', {}).get('buckets', [])\n"
                f"headline = sum(1 for b in buckets if int(b.get('doc_count') or 0) <= {threshold})"
            ),
            "bash": (
                'headline=$(echo "$v1" | jq '
                f"'[.raw.aggregations.by_author.buckets[] | select(.doc_count <= {threshold})] | length')"
            ),
            "js": (
                "const buckets = v1.raw?.aggregations?.by_author?.buckets || [];\n"
                f"const headline = buckets.filter(b => (b.doc_count || 0) <= {threshold}).length;"
            ),
        },
    )

    return MetricResult(
        slug="occasional_contributors",
        value=str(n_occasional),
        label=f"≤ {threshold} commits in window",
        secondary=f"{n_occasional} of {n_total} active contributors · {ratio:.0%}",
        queries=traces,
        examples=examples,
        recipes=oc_recipes,
        unification=(
            f"Terms agg on ``author_name`` filtered to the window, then "
            f"a client-side count of buckets whose ``doc_count`` is at "
            f"most {threshold}."
        ),
        notes=(
            f"Threshold {threshold} mirrors the CHAOSS catalogue's "
            "**Occasional Contributors** definition (drive-by "
            "contributors). A high occasional-vs-core ratio means "
            "outreach is working but retention isn't."
        ),
    )


# ── Helpers for PR-lifecycle metrics ─────────────────────────────────────


def _pr_count_metric(
    full: str,
    window_days: int,
    *,
    slug: str,
    label: str,
    extra_must: list[dict[str, Any]],
    date_field: str,
    series_unit: str,
    notes: str,
    unification: str,
) -> MetricResult:
    """Shared shape for the two PR-count metrics (accepted / declined).
    Both filter on ``pull_request:true`` plus an extra clause and a
    date range; both expose a monthly histogram sparkline.
    """
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []

    origin = f"https://github.com/{full}"
    body = {
        "size": 0,
        "track_total_hits": True,
        "query": {
            "bool": {
                "must": [
                    {"term": {"origin": origin}},
                    {"term": {"pull_request": True}},
                    *extra_must,
                    {"range": {date_field: {"gte": cutoff_iso}}},
                ]
            }
        },
        "aggs": {
            "by_month": {
                "date_histogram": {
                    "field": date_field,
                    "calendar_interval": "month",
                    "min_doc_count": 0,
                }
            }
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/github_*_enriched/_search", body)
    if raw is None:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"{label} on {origin}",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or github index empty",
            )
        )
        return MetricResult(
            slug=slug,
            value="—",
            label="no data",
            secondary=None,
            queries=traces,
            notes="github_*_enriched has no documents for this repo yet.",
            unification=unification,
        )
    total = int(((raw.get("hits") or {}).get("total") or {}).get("value") or 0)
    monthly: list[dict[str, Any]] = []
    for b in ((raw.get("aggregations") or {}).get("by_month") or {}).get("buckets", []):
        ts_ms = int(b.get("key") or 0)
        cnt = int(b.get("doc_count") or 0)
        monthly.append(
            {
                "date": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                .date()
                .isoformat()[:7],
                "value": cnt,
            }
        )
    traces.append(
        QueryTrace(
            store="OpenSearch",
            engine="opensearch",
            mode="dsl",
            title=f"{label} on {origin} since {cutoff_iso[:10]}",
            query=body_text,
            result_summary=f"{total} {series_unit}",
        )
    )
    recipes = _build_recipes(
        label=slug,
        traces=traces,
        extracts=[
            {
                "python": "r1.get('raw', {}).get('hits', {}).get('total', {}).get('value', 0)",
                "bash": ".raw.hits.total.value // 0",
                "js": "r1.raw?.hits?.total?.value ?? 0",
            }
        ],
        combine={
            "python": "headline = v1",
            "bash": "headline=$v1",
            "js": "const headline = v1;",
        },
    )
    return MetricResult(
        slug=slug,
        value=str(total),
        label=f"{label.lower()} (last {window_days} d)",
        secondary=None,
        queries=traces,
        series=monthly,
        series_unit=series_unit,
        recipes=recipes,
        unification=unification,
        notes=notes,
    )


# ── Metric 25 · Change Request Accepted ──────────────────────────────────


def _metric_cr_accepted(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    return _pr_count_metric(
        full,
        window_days,
        slug="cr_accepted",
        label="Merged PRs",
        extra_must=[{"term": {"merged": True}}],
        date_field="merge_date",
        series_unit="merged PRs",
        unification=(
            "Count of **OpenSearch** github docs with ``pull_request:true`` "
            "+ ``merged:true`` + ``merge_date`` in the window."
        ),
        notes=(
            "The acceptance half of ``closure_ratio`` — PRs that "
            "actually shipped. Pair with ``cr_declined`` to read the "
            "accept/reject split."
        ),
    )


# ── Metric 26 · Change Request Declined ──────────────────────────────────


def _metric_cr_declined(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    return _pr_count_metric(
        full,
        window_days,
        slug="cr_declined",
        label="Declined PRs",
        extra_must=[{"term": {"state": "closed"}}, {"term": {"merged": False}}],
        date_field="closed_at",
        series_unit="declined PRs",
        unification=(
            "Count of **OpenSearch** github docs with ``pull_request:true`` "
            "+ ``state:closed`` + ``merged:false`` + ``closed_at`` in window."
        ),
        notes=(
            "Closed-without-merge PRs — explicit rejections or stale "
            "work the maintainers closed without shipping. The other "
            "half of ``cr_accepted``."
        ),
    )


# ── Shared PR percentile metric (cr_duration, pr_time_to_close) ──────────


def _pr_percentile_metric(
    full: str,
    window_days: int,
    *,
    slug: str,
    label: str,
    extra_must: list[dict[str, Any]],
    field: str,
    unit: str,
    notes: str,
    unification: str,
) -> MetricResult:
    """Shared P50/P90 over a GrimoireLab-precomputed duration field
    (``time_open_days`` or similar) for a filtered PR set."""
    cutoff = _now_minus_days(window_days)
    cutoff_iso = _iso(cutoff)
    traces: list[QueryTrace] = []

    origin = f"https://github.com/{full}"
    body = {
        "size": 0,
        "track_total_hits": True,
        "query": {
            "bool": {
                "must": [
                    {"term": {"origin": origin}},
                    {"term": {"pull_request": True}},
                    *extra_must,
                ]
            }
        },
        "aggs": {
            "p50": {"percentiles": {"field": field, "percents": [50]}},
            "p90": {"percentiles": {"field": field, "percents": [90]}},
        },
    }
    body_text = json.dumps(body, indent=2)
    raw = os_mod._post("/github_*_enriched/_search", body)
    if raw is None:
        traces.append(
            QueryTrace(
                store="OpenSearch",
                engine="opensearch",
                mode="dsl",
                title=f"{label} percentiles on {origin}",
                query=body_text,
                result_summary="no response",
                error="OpenSearch unreachable or github index empty",
            )
        )
        return MetricResult(
            slug=slug,
            value="—",
            label="no data",
            secondary=None,
            queries=traces,
            notes="github_*_enriched has no documents for this repo yet.",
            unification=unification,
        )
    total = int(((raw.get("hits") or {}).get("total") or {}).get("value") or 0)
    aggs = raw.get("aggregations") or {}
    p50_raw = ((aggs.get("p50") or {}).get("values") or {}).get("50.0")
    p90_raw = ((aggs.get("p90") or {}).get("values") or {}).get("90.0")
    p50 = float(p50_raw) if p50_raw is not None else None
    p90 = float(p90_raw) if p90_raw is not None else None

    traces.append(
        QueryTrace(
            store="OpenSearch",
            engine="opensearch",
            mode="dsl",
            title=f"P50/P90 {label} on {origin} since {cutoff_iso[:10]}",
            query=body_text,
            result_summary=(
                f"{total} PRs · P50 {p50:.1f} {unit} · P90 {p90:.1f} {unit}"
                if p50 is not None and p90 is not None
                else f"{total} PRs · no percentile"
            ),
        )
    )
    if not total or p50 is None:
        return MetricResult(
            slug=slug,
            value="—",
            label="no PRs in window",
            secondary=None,
            queries=traces,
            notes=notes,
            unification=unification,
        )

    recipes = _build_recipes(
        label=slug,
        traces=traces,
        extracts=[
            {
                "python": "r1.get('raw', {}).get('aggregations', {}).get('p50', {}).get('values', {}).get('50.0')",
                "bash": '.raw.aggregations.p50.values."50.0" // 0',
                "js": "r1.raw?.aggregations?.p50?.values?.['50.0'] ?? 0",
            }
        ],
        combine={
            "python": f"headline = f'{{v1:.1f}} {unit}' if v1 else '—'",
            "bash": f'if [ "$v1" != "null" ] && [ "$v1" != "0" ]; then headline=$(awk -v v="$v1" \'BEGIN {{ printf("%.1f {unit}", v) }}\'); else headline="—"; fi',
            "js": f"const headline = v1 ? `${{v1.toFixed(1)}} {unit}` : '—';",
        },
    )
    return MetricResult(
        slug=slug,
        value=f"{p50:.1f} {unit}",
        label=f"median {label.lower()} (last {window_days} d)",
        secondary=(
            f"{total} PRs · P90 {p90:.1f} {unit}" if p90 is not None else f"{total} PRs"
        ),
        queries=traces,
        recipes=recipes,
        notes=notes,
        unification=unification,
    )


# ── Metric 27 · Change Request Duration ──────────────────────────────────


def _metric_cr_duration(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    return _pr_percentile_metric(
        full,
        window_days,
        slug="cr_duration",
        label="Days from PR open to merge",
        extra_must=[
            {"term": {"merged": True}},
            {"range": {"merge_date": {"gte": _iso(_now_minus_days(window_days))}}},
        ],
        field="time_open_days",
        unit="d",
        unification=(
            "P50 of GrimoireLab's ``time_open_days`` on PRs where "
            "``merged:true`` and ``merge_date`` falls inside the window."
        ),
        notes=(
            "Acceptance speed — the time from PR open to merge for "
            "PRs that actually shipped. A rising number can signal a "
            "review-capacity squeeze."
        ),
    )


# ── Metric 28 · Time to Close (PRs) ──────────────────────────────────────


def _metric_pr_time_to_close(
    full: str, canonical_url: str, window_days: int
) -> MetricResult:
    return _pr_percentile_metric(
        full,
        window_days,
        slug="pr_time_to_close",
        label="Days from PR open to close",
        extra_must=[
            {"term": {"state": "closed"}},
            {"range": {"closed_at": {"gte": _iso(_now_minus_days(window_days))}}},
        ],
        field="time_open_days",
        unit="d",
        unification=(
            "P50 of GrimoireLab's ``time_open_days`` on PRs where "
            "``state:closed`` (merged or declined) and ``closed_at`` "
            "is in the window."
        ),
        notes=(
            "Time-to-close considers *all* closed PRs — merged + "
            "declined. Differs from ``cr_duration`` which is "
            "acceptance-speed only. A wide gap between the two means "
            "the project closes-without-merging slowly."
        ),
    )


# ── Registry ─────────────────────────────────────────────────────────────

REGISTRY: list[MetricSpec] = [
    MetricSpec(
        slug="contributors",
        name="Contributors",
        category="Contributor",
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
        category="Contributor",
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
        category="Software",
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
        category="Software",
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
        category="Software",
        chaoss_level="Level 0 · Must-have",
        chaoss_url=(
            "https://chaoss.community/kb/metric-academic-open-source-project-impact/"
        ),
        question="How much does the software influence academic outputs?",
        description=(
            "Proxy via the SPARQL graph: scholarly articles whose "
            "authors also have a pulse:hasContribution to this repo. "
            "No RAG / vector search — fully reproducible by hand from "
            "the /databases console."
        ),
        is_time_based=False,
        compute=_metric_academic_impact,
    ),
    # ── Phase 2 additions ────────────────────────────────────────────
    MetricSpec(
        slug="project_popularity",
        name="Project Popularity",
        category="Software",
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
        category="Software",
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
        category="Lifecycle",
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
        category="Lifecycle",
        chaoss_level="Level 0 · Must-have",
        chaoss_url=("https://chaoss.community/kb/metric-change-request-closure-ratio/"),
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
        category="Organization",
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
        category="Lifecycle",
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
        category="Lifecycle",
        chaoss_level="Phase 2 · Would-like-to-have",
        chaoss_url=("https://chaoss.community/kb/metric-issue-resolution-duration/"),
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
        category="Lifecycle",
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
        category="Lifecycle",
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
    # ── Phase 4 additions ────────────────────────────────────────────
    MetricSpec(
        slug="absence_factor",
        name="Contributor Absence Factor",
        category="Contributor",
        chaoss_level="Level 0 · Must-have",
        chaoss_url=("https://chaoss.community/kb/metric-contributor-absence-factor/"),
        question="Does the project depend on a few people?",
        description=(
            "The 'bus factor' — smallest N contributors whose combined "
            "commits make up at least 50 % of the project's total. "
            "Low values flag a sustainability risk."
        ),
        is_time_based=True,
        compute=_metric_absence_factor,
    ),
    MetricSpec(
        slug="project_demographics",
        name="Project Demographics",
        category="Contributor",
        chaoss_level="Level 0 · Implemented",
        chaoss_url="https://chaoss.community/kb/metric-project-demographics/",
        question="Who are the contributors and how active are they?",
        description=(
            "Population breakdown of the all-time contributor pool: "
            "total / 'core' (top N covering 80 % of commits) / "
            "recent arrivals (first commit in last 90 d) / dormant "
            "(no commit in 180 d)."
        ),
        is_time_based=False,
        compute=_metric_demographics,
    ),
    MetricSpec(
        slug="bot_activity",
        name="Bot Activity",
        category="Software",
        chaoss_level="Level 0 · Implemented",
        chaoss_url="https://chaoss.community/kb/metric-bot-activity/",
        question="How much of the activity is automated?",
        description=(
            "Share of commits authored by recognised bots "
            "(dependabot, renovate, github-actions, *[bot]*, …). "
            "High is not bad — it usually means active automation — "
            "but it distorts raw commit counts."
        ),
        is_time_based=True,
        compute=_metric_bot_activity,
    ),
    # ── Phase 5 additions ────────────────────────────────────────────
    MetricSpec(
        slug="issues_new",
        name="Issues New",
        category="Lifecycle",
        chaoss_level="Phase 2 · Would-like-to-have",
        chaoss_url="https://chaoss.community/kb/metric-issues-new/",
        question="How fast is the backlog growing?",
        description=(
            "Issues opened on the repo in the window (excludes PRs). "
            "Compare with Issues Closed to see whether inflow is "
            "outpacing the team's outflow."
        ),
        is_time_based=True,
        compute=_metric_issues_new,
    ),
    MetricSpec(
        slug="issues_active",
        name="Issues Active",
        category="Lifecycle",
        chaoss_level="Phase 2 · Would-like-to-have",
        chaoss_url="https://chaoss.community/kb/metric-issues-active/",
        question="How alive is the issue tracker?",
        description=(
            "Issues with any kind of update (comment, label, state "
            "change) inside the window — a liveness signal stronger "
            "than just 'were issues opened'."
        ),
        is_time_based=True,
        compute=_metric_issues_active,
    ),
    MetricSpec(
        slug="issues_closed",
        name="Issues Closed",
        category="Lifecycle",
        chaoss_level="Phase 2 · Would-like-to-have",
        chaoss_url="https://chaoss.community/kb/metric-issues-closed/",
        question="How much backlog is the team clearing?",
        description=("Issues whose state flipped to closed inside the window."),
        is_time_based=True,
        compute=_metric_issues_closed,
    ),
    MetricSpec(
        slug="cr_reviews",
        name="Change Request Reviews",
        category="Lifecycle",
        chaoss_level="Level 0 · Must-have",
        chaoss_url=("https://chaoss.community/kb/metric-change-request-reviews/"),
        question="Is work being reviewed before it lands?",
        description=(
            "Pull requests that received at least one non-bot review "
            "comment in the window. Pair with self_merge to read "
            "the code-review culture."
        ),
        is_time_based=True,
        compute=_metric_cr_reviews,
    ),
    MetricSpec(
        slug="code_lines",
        name="Code Changes Lines",
        category="Software",
        chaoss_level="Phase 2 · Would-like-to-have",
        chaoss_url="https://chaoss.community/kb/metric-code-changes-lines/",
        question="How much code is being touched?",
        description=(
            "Sum of lines added and removed across commits in the "
            "window, with the file-change count surfaced as secondary."
        ),
        is_time_based=True,
        compute=_metric_code_lines,
    ),
    # ── Phase 6 — Level-0 quick wins ────────────────────────────────
    MetricSpec(
        slug="inactive_contributors",
        name="Inactive Contributors",
        category="Contributor",
        chaoss_level="Level 0 · Implemented",
        chaoss_url="https://chaoss.community/kb/metric-inactive-contributors/",
        question="Who has stopped contributing?",
        description=(
            "Distinct people who *have* contributed to the repo at "
            "some point but whose most recent commit predates the "
            "selected window. Pair with `contributors` for the active "
            "side of the same ledger."
        ),
        is_time_based=True,
        compute=_metric_inactive_contributors,
    ),
    MetricSpec(
        slug="occasional_contributors",
        name="Occasional Contributors",
        category="Contributor",
        chaoss_level="Level 0 · Implemented",
        chaoss_url="https://chaoss.community/kb/metric-occasional-contributors/",
        question="How many drive-by contributors does the project attract?",
        description=(
            "Distinct authors with at most 4 commits in the window — "
            "the CHAOSS catalogue's drive-by threshold. A community-"
            "health signal: high outreach vs low retention."
        ),
        is_time_based=True,
        compute=_metric_occasional_contributors,
    ),
    MetricSpec(
        slug="cr_accepted",
        name="Change Request Accepted",
        category="Lifecycle",
        chaoss_level="Level 0 · Implemented",
        chaoss_url="https://chaoss.community/kb/metric-change-request-accepted/",
        question="How many PRs shipped?",
        description=(
            "Merged pull requests in the window. The acceptance half "
            "of `closure_ratio`; pair with `cr_declined` to read the "
            "merge-vs-reject split."
        ),
        is_time_based=True,
        compute=_metric_cr_accepted,
    ),
    MetricSpec(
        slug="cr_declined",
        name="Change Request Declined",
        category="Lifecycle",
        chaoss_level="Level 0 · Implemented",
        chaoss_url="https://chaoss.community/kb/metric-change-request-declined/",
        question="How many PRs were rejected?",
        description=(
            "Pull requests closed without merging — explicit "
            "rejections or stale work cleaned up by maintainers."
        ),
        is_time_based=True,
        compute=_metric_cr_declined,
    ),
    MetricSpec(
        slug="cr_duration",
        name="Change Request Duration",
        category="Lifecycle",
        chaoss_level="Level 0 · Implemented",
        chaoss_url="https://chaoss.community/kb/metric-change-request-duration/",
        question="How fast do PRs that ship actually ship?",
        description=(
            "Median days from PR creation to merge for PRs that "
            "merged in the window. Acceptance-speed view."
        ),
        is_time_based=True,
        compute=_metric_cr_duration,
    ),
    MetricSpec(
        slug="pr_time_to_close",
        name="Time to Close (PRs)",
        category="Lifecycle",
        chaoss_level="Level 0 · Implemented",
        chaoss_url="https://chaoss.community/kb/metric-time-to-close/",
        question="How long do PRs stay open?",
        description=(
            "Median days from PR creation to close — counts ALL "
            "closed PRs (merged + declined). Differs from "
            "`cr_duration` which is merge-only."
        ),
        is_time_based=True,
        compute=_metric_pr_time_to_close,
    ),
]


def spec_for(slug: str) -> MetricSpec | None:
    """Look up a registered metric by its URL slug."""
    return next((m for m in REGISTRY if m.slug == slug), None)
