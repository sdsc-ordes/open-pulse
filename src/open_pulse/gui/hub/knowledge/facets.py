"""Cached facet values for the catalog Filters modal.

The hub catalog only facets by type + source. This module precomputes the
*top values* of the main GME properties — licence, language, repository
type, owning org, discipline, citation — plus ROR organisations and ORCID
people, so a catalog-style Filters sidebar can show "the main ones" without
hammering the stores on every open.

Cheap per-predicate ``GROUP BY`` queries against the SPARQL store cover the
property facets; ROR / ORCID are sourced from the DuckDB ``institutions`` /
``authors`` stores (a full-store IRI scan in SPARQL times out). Everything is
memoised — the underlying data changes only when GME re-ingests.

Future (not here): ``/license``-style search-bar commands + multi-select.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from . import duckdb_browser as ddb
from . import qdrant, stores

log = logging.getLogger(__name__)

_TOP_N = 12

# Hosts whose IRIs resolve to a hub entity page → link in-hub. Others
# (wikidata) open externally.
_HUB_HOSTS = ("github.com", "doi.org", "ror.org", "orcid.org", "huggingface.co")


def _strip_scheme(iri: str) -> str:
    s = re.sub(r"^https?://", "", (iri or "").strip(), flags=re.I)
    return re.sub(r"^www\.", "", s, flags=re.I).rstrip("/")


def _iri_value(iri: str, *, label_tail: bool = True) -> dict[str, Any]:
    """Shape an IRI object as a facet value: hub link if resolvable, else
    external, with a readable label."""
    stripped = _strip_scheme(iri)
    host = stripped.split("/", 1)[0].lower() if stripped else ""
    if host in _HUB_HOSTS:
        url, external = "/hub/" + stripped, False
    else:
        url, external = iri, True
    label = stripped.rsplit("/", 1)[-1] if label_tail else stripped
    return {"label": label or stripped, "url": url, "external": external}


# (key, label, predicate, kind) — ``kind`` drives rendering on the client.
# kind "language" is the one wired to filter the grid this pass.
_SPARQL_FACETS: list[tuple[str, str, str, str]] = [
    ("license", "License", "https://openpulse.science/git-metadata-extractor#license_name", "literal"),
    ("language", "Language", "https://openpulse.science/git-metadata-extractor#primary_language", "language"),
    ("repository_type", "Repository type", "https://open-pulse.epfl.ch/ontology#repositoryType", "iri_local"),
    ("owner", "Owner / org", "https://open-pulse.epfl.ch/ontology#ownedBy", "iri"),
    ("discipline", "Discipline", "https://open-pulse.epfl.ch/ontology#discipline", "iri"),
    ("citation", "Cited works", "http://schema.org/citation", "iri"),
]


def _sparql_facet(predicate: str, kind: str) -> list[dict[str, Any]]:
    rows = stores.sparql_select(
        f"SELECT ?o (COUNT(*) AS ?n) WHERE {{ ?s <{predicate}> ?o }} "
        f"GROUP BY ?o ORDER BY DESC(?n) LIMIT {_TOP_N}"
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        o = r.get("o", {}).get("value", "")
        if not o:
            continue
        try:
            count = int(r.get("n", {}).get("value") or 0)
        except (TypeError, ValueError):
            count = 0
        if kind in ("literal", "language"):
            out.append({"value": o, "label": o, "count": count, "url": "", "external": False})
        elif kind == "iri_local":
            label = re.split(r"[#/]", o.rstrip("/"))[-1]
            out.append({"value": o, "label": label, "count": count, "url": "", "external": False})
        else:  # iri
            v = _iri_value(o)
            out.append({"value": o, "label": v["label"], "count": count,
                        "url": v["url"], "external": v["external"]})
    return out


def _store_facet(collection: str, title_col: str, id_col: str) -> list[dict[str, Any]]:
    """Top-N entities from a DuckDB store (ROR institutions / ORCID authors).
    No frequency available, so this is the store's head — a representative
    sample — each linking to its hub page."""
    res = ddb.list_rows(collection, page=1, size=_TOP_N) or {}
    cols = {c.lower(): c for c in res.get("columns", [])}
    out: list[dict[str, Any]] = []
    for row in res.get("rows", []):
        ident = row.get(cols.get(id_col, ""))
        title = row.get(cols.get(title_col, "")) or ident
        if not ident:
            continue
        v = _iri_value(str(ident), label_tail=False)
        out.append({"value": str(ident), "label": str(title), "count": None,
                    "url": v["url"], "external": v["external"]})
    return out


def gather(refresh: bool = False) -> list[dict[str, Any]]:
    """All facets with their top values — ``[{key,label,kind,values:[…]}]``.
    Empty facets are dropped. Cached; the data moves only on GME re-ingest.
    ``refresh=True`` recomputes and refreshes the cache (on-demand refresh)."""

    def _build() -> list[dict[str, Any]]:
        facets: list[dict[str, Any]] = []
        for key, label, pred, kind in _SPARQL_FACETS:
            try:
                values = _sparql_facet(pred, kind)
            except Exception as exc:  # noqa: BLE001
                log.info("facet %s failed: %s", key, exc)
                values = []
            if values:
                facets.append({"key": key, "label": label, "kind": kind, "values": values})
        # ROR organisations + ORCID people from the DuckDB stores.
        for key, label, coll, title_col, id_col in (
            ("ror", "ROR organisations", "institutions", "display_name", "ror"),
            ("orcid", "ORCID people", "authors", "display_name", "orcid"),
        ):
            try:
                values = _store_facet(coll, title_col, id_col) if ddb.is_browsable(coll) else []
            except Exception as exc:  # noqa: BLE001
                log.info("facet %s failed: %s", key, exc)
                values = []
            if values:
                facets.append({"key": key, "label": label, "kind": "iri", "values": values})
        return facets

    return qdrant.cached_panel("facets", "*", _build, force=refresh)
