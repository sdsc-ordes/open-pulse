"""Cross-store person identity harmonization.

The two graphs identify people differently: **Neo4j** keys contributors by
their GitHub login (``caviri`` → "Carlos Vivar Rios"), while the **RDF** store's
``schema:author`` edges key them by **ORCID** (``orcid.org/0000-…``). So the
same person can show up twice — a Neo4j "contributed by" row and a disconnected
RDF "authored by" row — and the contributor never gets an RDF chip.

This module bridges them **using only local data, no external API**. The ORCID
author node in the RDF usually carries its own ``schema:name`` and, crucially,
``pulse:githubUsername`` — so we resolve the person's **name** (used as the
display label) and bridge ORCID ↔ GitHub **authoritatively** via that handle.
On a match the RDF author is rewritten onto the GitHub identity so the
downstream merge collapses the rows into one (Neo4j + RDF) carrying the ORCID
for a logo-link. Fallbacks, in order: githubUsername → exact normalised-name
match against a Neo4j contributor → the local ``authors`` index for the name.
Name matching is exact-after-normalisation only, so a common name can't wrongly
fuse two people; unmatched ORCID authors keep their own row but are labelled by
name (the ORCID itself shows only as the logo-link).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import replace

from . import duckdb_browser as ddb
from .entity import Neighbour

log = logging.getLogger(__name__)


def _norm_name(s: str) -> str:
    """Lower-case, strip accents + punctuation, collapse spaces — so
    "Oksana Riba‐Grognuz" and "Oksana Riba Grognuz" compare equal while two
    genuinely different names don't."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


_ORCID_INFO_CACHE: dict[str, tuple[str, str]] = {}


def _orcid_rdf_info(orcid_url: str) -> tuple[str, str]:
    """``(name, github_url)`` for an ORCID node from the RDF — unscoped so it
    works regardless of the pinned graph. Either may be ``""``. Cached."""
    if orcid_url in _ORCID_INFO_CACHE:
        return _ORCID_INFO_CACHE[orcid_url]
    name = gh = ""
    try:
        from . import stores

        for r in stores.sparql_describe(orcid_url, limit=80, scoped=False):
            p = r.get("p", {}).get("value", "").lower()
            o = r.get("o", {}).get("value", "")
            if not o:
                continue
            if "githubusername" in p and not gh:
                gh = o
            elif (p.endswith("#name") or p.endswith("/name")
                  or p.endswith("label") or p.endswith("fullname")) and not name:
                name = o
    except Exception as exc:  # noqa: BLE001
        log.info("orcid rdf info failed (%s): %s", orcid_url, exc)
    _ORCID_INFO_CACHE[orcid_url] = (name, gh)
    return name, gh


# DuckDB person stores that map an ORCID → display name, tried in order.
# ``(collection, orcid_column, name_sql)`` — name_sql is a hardcoded expression
# (no user input) that builds the display name, coalescing the parts a given
# store carries. Reconciling across all of them — not just OpenAlex ``authors``
# — is the point: a person absent from one store is often named in another
# (e.g. an EPFL contributor only in ``orcid_epfl_persons``).
_PERSON_STORES: tuple[tuple[str, str, str], ...] = (
    ("authors", "orcid", "display_name"),
    ("infoscience_persons", "orcid",
     "COALESCE(display_name, NULLIF(concat_ws(' ', given_name, family_name), ''))"),
    ("orcid_epfl_persons", "orcid_id",
     "COALESCE(display_name, NULLIF(concat_ws(' ', given_name, family_name), ''))"),
    ("orcid_switzerland_persons", "orcid_id",
     "COALESCE(display_name, NULLIF(concat_ws(' ', given_name, family_name), ''))"),
    ("ethz_research_collection_persons", "orcid",
     "COALESCE(display_name, NULLIF(concat_ws(' ', given_name, family_name), ''))"),
    ("snsf_persons", "orcid", "NULLIF(concat_ws(' ', first_name, last_name), '')"),
)
_ORCID_NAME_DUCKDB_CACHE: dict[str, str] = {}


def _orcid_name_local(orcid_url: str) -> str:
    """Display name for an ORCID, reconciled across every DuckDB person store
    (OpenAlex / Infoscience / ORCID-EPFL / ORCID-CH / ETHZ / SNSF). The ORCID is
    matched on its bare id so a store keying by full URL or by bare id both hit.
    No network; cached; ``""`` if not found anywhere."""
    if orcid_url in _ORCID_NAME_DUCKDB_CACHE:
        return _ORCID_NAME_DUCKDB_CACHE[orcid_url]
    bare = orcid_url.rstrip("/").rsplit("/", 1)[-1]
    name = ""
    for coll, col, name_sql in _PERSON_STORES:
        b = ddb._BACKING.get(coll)
        if b is None:
            continue
        try:
            with ddb._connect(b.db_path) as con:
                rows = con.execute(
                    f"SELECT {name_sql} FROM {ddb._source_expr(b)} "
                    f'WHERE CAST("{col}" AS VARCHAR) ILIKE ? LIMIT 1',
                    [f"%{bare}%"],
                ).fetchall()
            if rows and rows[0][0] and str(rows[0][0]).strip():
                name = str(rows[0][0]).strip()
                break
        except Exception as exc:  # noqa: BLE001
            log.info("orcid name lookup failed (%s on %s): %s", orcid_url, coll, exc)
    _ORCID_NAME_DUCKDB_CACHE[orcid_url] = name
    return name


def _hub_url_for_github(github_url: str) -> str:
    """``https://github.com/marftn`` → ``/hub/github.com/marftn`` (the in-hub
    identity a Neo4j contributor neighbour carries)."""
    stripped = re.sub(r"^https?://(www\.)?", "", (github_url or "").strip()).rstrip("/")
    return "/hub/" + stripped if stripped else ""


def _is_orcid(url: str) -> bool:
    return "orcid.org/" in (url or "").lower()


def harmonize_people(neighbours: list[Neighbour]) -> list[Neighbour]:
    """Bridge RDF ORCID authors onto matching Neo4j github contributors.

    A matched ORCID author is rewritten onto the github contributor's identity
    (so the downstream merge corroborates it) carrying its ORCID; unmatched
    ORCID authors keep their own row, labelled by name. Non-people edges pass
    through untouched."""
    by_hub: dict[str, Neighbour] = {}
    by_name: dict[str, Neighbour] = {}
    for n in neighbours:
        if n.category == "people" and "github.com" in (n.hub_url or ""):
            by_hub.setdefault(n.hub_url, n)
            if n.label:
                by_name.setdefault(_norm_name(n.label), n)

    out: list[Neighbour] = []
    for n in neighbours:
        if n.category != "people" or not _is_orcid(n.external_url):
            out.append(n)
            continue
        rdf_name, github_url = _orcid_rdf_info(n.external_url)
        name = rdf_name or _orcid_name_local(n.external_url)
        # Bridge: authoritative githubUsername first, then exact name.
        match = by_hub.get(_hub_url_for_github(github_url)) if github_url else None
        if match is None and name:
            match = by_name.get(_norm_name(name))
        if match is not None:
            out.append(replace(
                n, hub_url=match.hub_url, label=match.label or name,
                orcid_url=n.external_url,
            ))
        elif name:
            out.append(replace(n, label=name, orcid_url=n.external_url))
        else:
            out.append(replace(n, orcid_url=n.external_url))
    return out


_NODE_NAME_CACHE: dict[str, str] = {}


def _rdf_node_name(iri: str) -> str:
    """The ``schema:name`` / ``dc:title`` of an RDF node (a cited publication's
    DOI carries its title there) — unscoped + cached. ``""`` if none."""
    if iri in _NODE_NAME_CACHE:
        return _NODE_NAME_CACHE[iri]
    name = ""
    try:
        from . import stores

        for r in stores.sparql_describe(iri, limit=60, scoped=False):
            p = r.get("p", {}).get("value", "").lower()
            o = r.get("o", {}).get("value", "")
            if o and (p.endswith("#name") or p.endswith("/name")
                      or p.endswith("title") or p.endswith("label")
                      or p.endswith("headline")):
                name = o
                break
    except Exception as exc:  # noqa: BLE001
        log.info("rdf node name failed (%s): %s", iri, exc)
    name = re.sub(r"<[^>]+>", "", name).strip()[:120]
    _NODE_NAME_CACHE[iri] = name
    return name


# DuckDB publication stores that map a DOI → title, tried in order. Reconciling
# across all of them means a cited work indexed in Infoscience / Zenodo / ETHZ /
# HF-papers but not OpenAlex still gets a title.
_WORK_STORES: tuple[tuple[str, str, str], ...] = (
    ("works", "doi", "title"),
    ("infoscience_articles", "doi", "title"),
    ("zenodo_records", "doi", "title"),
    ("ethz_research_collection_articles", "doi", "title"),
    ("huggingface_papers", "doi", "title"),
)
_DOI_TITLE_DUCKDB_CACHE: dict[str, str] = {}


def _doi_title_duckdb(doi_url: str) -> str:
    """Title for a DOI, reconciled across every DuckDB publication store. The
    DOI is matched on its bare id (``10.x/…``) so full-URL or bare columns both
    hit. No network; cached; ``""`` if not found anywhere."""
    if doi_url in _DOI_TITLE_DUCKDB_CACHE:
        return _DOI_TITLE_DUCKDB_CACHE[doi_url]
    bare = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi_url, flags=re.I).strip("/")
    title = ""
    if bare:
        for coll, col, title_col in _WORK_STORES:
            b = ddb._BACKING.get(coll)
            if b is None:
                continue
            try:
                with ddb._connect(b.db_path) as con:
                    rows = con.execute(
                        f'SELECT "{title_col}" FROM {ddb._source_expr(b)} '
                        f'WHERE CAST("{col}" AS VARCHAR) ILIKE ? LIMIT 1',
                        [f"%{bare}%"],
                    ).fetchall()
                if rows and rows[0][0] and str(rows[0][0]).strip():
                    title = re.sub(r"<[^>]+>", "", str(rows[0][0])).strip()[:120]
                    break
            except Exception as exc:  # noqa: BLE001
                log.info("doi title lookup failed (%s on %s): %s", doi_url, coll, exc)
    _DOI_TITLE_DUCKDB_CACHE[doi_url] = title
    return title


def harmonize_works(neighbours: list[Neighbour]) -> list[Neighbour]:
    """Label cited-work (publication) edges by their title.

    A ``cites`` / ``references`` edge points at a DOI whose RDF node carries the
    publication's ``schema:name`` (title); use it as the label so the row reads
    as a paper title rather than a bare DOI. The DOI stays as the edge's link +
    source logo (the reference) — mirroring the name-as-label / ORCID-as-logo
    treatment for people."""
    out: list[Neighbour] = []
    for n in neighbours:
        if n.category == "works" and n.external_url:
            title = _rdf_node_name(n.external_url) or _doi_title_duckdb(n.external_url)
            if title and _norm_name(title) != _norm_name(n.label):
                out.append(replace(n, label=title))
                continue
        out.append(n)
    return out


# DuckDB org stores that map a ROR id → institution name, tried in order.
_ROR_STORES: tuple[tuple[str, str, str], ...] = (
    ("institutions", "ror", "display_name"),
    ("ror_worldwide", "ror_id", "name"),
    ("ror_epfl_ethz", "ror_id", "name"),
    ("ror_switzerland", "ror_id", "name"),
)
_ROR_NAME_CACHE: dict[str, str] = {}
_ORG_ROR_CACHE: dict[str, str] = {}


def _ror_name(ror_url: str) -> str:
    """Institution name for a ROR iD — the RDF node's name first, then the
    DuckDB ROR / institutions stores. Cached; matched on the bare ROR id."""
    if ror_url in _ROR_NAME_CACHE:
        return _ROR_NAME_CACHE[ror_url]
    name = _rdf_node_name(ror_url)
    if not name:
        bare = ror_url.rstrip("/").rsplit("/", 1)[-1]
        for coll, col, name_col in _ROR_STORES:
            b = ddb._BACKING.get(coll)
            if b is None:
                continue
            try:
                with ddb._connect(b.db_path) as con:
                    rows = con.execute(
                        f'SELECT "{name_col}" FROM {ddb._source_expr(b)} '
                        f'WHERE CAST("{col}" AS VARCHAR) ILIKE ? LIMIT 1',
                        [f"%{bare}%"],
                    ).fetchall()
                if rows and rows[0][0] and str(rows[0][0]).strip():
                    name = str(rows[0][0]).strip()
                    break
            except Exception as exc:  # noqa: BLE001
                log.info("ror name lookup failed (%s on %s): %s", ror_url, coll, exc)
    _ROR_NAME_CACHE[ror_url] = name
    return name


def _github_org_ror(github_url: str) -> str:
    """The ``unitOf`` ROR iD of a github org (the institution it's a unit of),
    from the unscoped RDF. Cached; ``""`` if none."""
    if github_url in _ORG_ROR_CACHE:
        return _ORG_ROR_CACHE[github_url]
    ror = ""
    try:
        from . import stores

        for r in stores.sparql_describe(github_url, limit=120, scoped=False):
            p = r.get("p", {}).get("value", "").lower()
            o = r.get("o", {}).get("value", "")
            if p.endswith("unitof") and "ror.org/" in o.lower():
                ror = o
                break
    except Exception as exc:  # noqa: BLE001
        log.info("github org ror lookup failed (%s): %s", github_url, exc)
    _ORG_ROR_CACHE[github_url] = ror
    return ror


def harmonize_orgs(neighbours: list[Neighbour]) -> list[Neighbour]:
    """Identity across organisations — the same name-as-label / id-as-reference
    treatment as people and publications.

    * A **github org** carries its ``unitOf`` ROR as an extra reference logo.
    * An org edge pointing straight at a **ROR** is labelled by the institution
      **name** (the ROR stays as the link + logo) instead of a bare ROR id; and
      when that ROR is the parent of a github org also present, it's rewritten
      onto the github identity so the two collapse into one corroborated row
      (the same org, two identifiers) on the downstream re-merge."""
    # Index github orgs by their unitOf ROR (lower-cased) → in-hub identity.
    ror_to_github: dict[str, str] = {}
    for n in neighbours:
        if n.category == "orgs" and "github.com/" in (n.external_url or "").lower():
            ror = _github_org_ror(n.external_url)
            if ror:
                ror_to_github.setdefault(ror.rstrip("/").lower(), n.hub_url or n.external_url)

    out: list[Neighbour] = []
    for n in neighbours:
        if n.category != "orgs" or not n.external_url:
            out.append(n)
            continue
        ext = n.external_url.lower()
        if "ror.org/" in ext:
            name = _ror_name(n.external_url)
            gh_identity = ror_to_github.get(ext.rstrip("/"))
            if gh_identity:
                # Same org as a present github org → adopt its identity so the
                # merge corroborates them; carry the ROR as the reference.
                out.append(replace(
                    n, hub_url=gh_identity, label=name or n.label,
                    ror_url=n.external_url,
                ))
                continue
            if name and _norm_name(name) != _norm_name(n.label):
                out.append(replace(n, label=name, ror_url=n.external_url))
                continue
        elif "github.com/" in ext:
            ror = _github_org_ror(n.external_url)
            if ror:
                out.append(replace(n, ror_url=ror))
                continue
        out.append(n)
    return out
