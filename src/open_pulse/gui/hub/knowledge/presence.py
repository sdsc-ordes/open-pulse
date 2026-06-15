"""Where-it-lives presence — which stores hold a given entity.

Probes each data-plane store cheaply for the entity's URL so the page can
show a "Present in" panel: RDF named graphs (Oxigraph), the Neo4j graph,
the GrimoireLab OpenSearch indices, and the GME Qdrant collections. Every
check degrades to "absent" on error — nothing here ever blocks the page,
and it runs as a lazy panel off the critical render path.
"""

from __future__ import annotations

import logging
from typing import Any

from . import opensearch as os_mod
from . import qdrant
from .normalize import HubRef
from .stores import neo4j_run, sparql_select

log = logging.getLogger(__name__)


def _aliases(url: str) -> list[str]:
    """Canonical URL plus the http:// and trailing-slash variants that
    different pipelines store as the subject IRI."""
    out = [url]
    if url.startswith("https://"):
        out.append("http://" + url[len("https://"):])
    if not url.endswith("/"):
        out.append(url + "/")
    return [u for u in out if "://" in u]


def _short_graph(iri: str) -> str:
    """A compact label for a named-graph IRI (the part after ``/graph/``)."""
    s = (iri or "").rstrip("/")
    if "/graph/" in s:
        return s.split("/graph/", 1)[-1]
    return s.rsplit("/", 1)[-1] or s


def _rdf(ref: HubRef) -> dict[str, Any]:
    url = ref.canonical_url
    vals = " ".join(f"<{u}>" for u in _aliases(url))
    present, summary, items = False, "not in the RDF store", []
    try:
        rows = sparql_select(
            f"SELECT DISTINCT ?g WHERE {{ GRAPH ?g {{ ?s ?p ?o }} "
            f"VALUES ?s {{ {vals} }} }} LIMIT 25"
        )
        graphs = [r["g"]["value"] for r in rows if r.get("g", {}).get("value")]
        if graphs:
            present = True
            items = [{"label": _short_graph(g), "detail": g} for g in graphs]
            summary = f"{len(graphs)} named graph" + ("" if len(graphs) == 1 else "s")
        else:
            cnt = sparql_select(
                f"SELECT (COUNT(*) AS ?c) WHERE {{ VALUES ?s {{ {vals} }} ?s ?p ?o }}"
            )
            n = int((cnt[0].get("c", {}).get("value") or 0)) if cnt else 0
            if n > 0:
                present, summary = True, "default graph"
    except Exception as exc:  # noqa: BLE001
        log.info("presence rdf failed: %s", exc)
    return {"key": "rdf", "label": "RDF graph (Oxigraph)",
            "present": present, "summary": summary, "items": items}


def _neo4j(ref: HubRef) -> dict[str, Any]:
    url = ref.canonical_url
    present, summary = False, "not a node in the graph"
    try:
        rows = neo4j_run(
            "MATCH (n) WHERE n.full_name = $u OR n.login = $u "
            "WITH n LIMIT 1 OPTIONAL MATCH (n)-[r]-() "
            "RETURN head(labels(n)) AS label, count(r) AS degree",
            {"u": url},
        )
        if rows:
            label = rows[0].get("label") or "node"
            degree = int(rows[0].get("degree") or 0)
            present = True
            summary = f"{label} node · {degree} edge" + ("" if degree == 1 else "s")
    except Exception as exc:  # noqa: BLE001
        log.info("presence neo4j failed: %s", exc)
    return {"key": "neo4j", "label": "Neo4j graph",
            "present": present, "summary": summary, "items": []}


def _opensearch(ref: HubRef) -> dict[str, Any]:
    present, summary, items = False, "no GrimoireLab ingest", []
    try:
        idx = os_mod.index_presence(ref.canonical_url)
        if idx:
            present = True
            total = sum(i["count"] for i in idx)
            items = [{"label": i["index"], "detail": f"{i['count']:,} docs"} for i in idx]
            summary = (
                f"{total:,} doc" + ("" if total == 1 else "s")
                + f" · {len(idx)} ind" + ("ex" if len(idx) == 1 else "ices")
            )
    except Exception as exc:  # noqa: BLE001
        log.info("presence opensearch failed: %s", exc)
    return {"key": "opensearch", "label": "OpenSearch (GrimoireLab)",
            "present": present, "summary": summary, "items": items}


def _qdrant(ref: HubRef) -> dict[str, Any]:
    present, summary, items = False, "not in any vector collection", []
    try:
        cols = qdrant.collections_for_ref(ref)
        if cols:
            present = True
            items = [{"label": qdrant.label_for_collection(c), "detail": c} for c in cols]
            summary = f"{len(cols)} collection" + ("" if len(cols) == 1 else "s")
    except Exception as exc:  # noqa: BLE001
        log.info("presence qdrant failed: %s", exc)
    return {"key": "qdrant", "label": "Vector index (Qdrant)",
            "present": present, "summary": summary, "items": items}


def _duckdb(ref: HubRef) -> dict[str, Any]:
    present, summary, items = False, "not in the catalog index", []
    try:
        from . import catalog as catalog_mod  # local import — avoid a cycle

        cols = catalog_mod.duckdb_collections_for_ref(ref.host, ref.path)
        if cols:
            present = True
            items = [{"label": c.replace("_", " "), "detail": c} for c in cols]
            summary = f"{len(cols)} table" + ("" if len(cols) == 1 else "s")
    except Exception as exc:  # noqa: BLE001
        log.info("presence duckdb failed: %s", exc)
    return {"key": "duckdb", "label": "Catalog index (DuckDB)",
            "present": present, "summary": summary, "items": items}


def gather(ref: HubRef) -> list[dict[str, Any]]:
    """A presence row per store: RDF, Neo4j, OpenSearch, Qdrant, DuckDB.

    Each row is ``{key, label, present, summary, items}``. Returns ``[]``
    only for an unknown host — otherwise always five rows, so the panel can
    state plainly which stores do and don't hold the entity (e.g. a repo
    that's only in the catalog index shows the other stores as absent)."""
    if not ref.is_known_host:
        return []
    return [_rdf(ref), _neo4j(ref), _opensearch(ref), _qdrant(ref), _duckdb(ref)]
