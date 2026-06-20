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


def _orcid_name_local(orcid_url: str) -> str:
    """Display name for an ORCID from the local ``authors`` index (fallback when
    the RDF node carries no name). No network. Best-effort."""
    b = ddb._BACKING.get("authors")
    if b is None:
        return ""
    try:
        with ddb._connect(b.db_path) as con:
            rows = con.execute(
                f"SELECT display_name FROM {ddb._source_expr(b)} "
                f'WHERE lower(CAST(orcid AS VARCHAR)) = lower(?) LIMIT 1',
                [orcid_url],
            ).fetchall()
        return (rows[0][0] if rows and rows[0][0] else "") or ""
    except Exception as exc:  # noqa: BLE001
        log.info("orcid local name lookup failed (%s): %s", orcid_url, exc)
        return ""


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
            title = _rdf_node_name(n.external_url)
            if title and _norm_name(title) != _norm_name(n.label):
                out.append(replace(n, label=title))
                continue
        out.append(n)
    return out
