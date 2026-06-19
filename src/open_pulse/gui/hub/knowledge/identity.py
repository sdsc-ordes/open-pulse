"""Cross-store person identity harmonization.

The two graphs identify people differently: **Neo4j** keys contributors by
their GitHub login (``caviri`` → "Carlos Vivar Rios"), while the **RDF** store's
``schema:author`` edges key them by **ORCID** (``orcid.org/0000-…``). When an
RDF author happens to be a GitHub IRI it already merges with the Neo4j edge on
the shared URL; but ORCID authors have no shared key, so the same person shows
up twice — a Neo4j "contributed by" row and a disconnected RDF "authored by"
row — and the contributor never gets an RDF chip.

This module bridges them **using only local data, no external API**: it resolves
an ORCID to a display name via the indexed ``authors`` store, normalises it, and
matches it to a Neo4j contributor's name. On an exact normalised match the RDF
author is rewritten onto the GitHub identity so the downstream merge collapses
them into one corroborated row (Neo4j + RDF) carrying the ORCID for a logo-link.
Matching is exact-after-normalisation only — never fuzzy — so a common name
can't wrongly fuse two different people; anything unmatched stays its own row
(but still gets its name resolved for readability).
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


_ORCID_NAME_CACHE: dict[str, str] = {}


def _orcid_name(orcid_url: str) -> str:
    """Display name for an ORCID URL from the local ``authors`` index, or ``""``
    if not indexed. Cached. No network — local store only, by design."""
    if orcid_url in _ORCID_NAME_CACHE:
        return _ORCID_NAME_CACHE[orcid_url]
    name = ""
    b = ddb._BACKING.get("authors")
    if b is not None:
        try:
            with ddb._connect(b.db_path) as con:
                rows = con.execute(
                    f"SELECT display_name FROM {ddb._source_expr(b)} "
                    f'WHERE lower(CAST(orcid AS VARCHAR)) = lower(?) LIMIT 1',
                    [orcid_url],
                ).fetchall()
            name = (rows[0][0] if rows and rows[0][0] else "") or ""
        except Exception as exc:  # noqa: BLE001
            log.info("orcid name lookup failed (%s): %s", orcid_url, exc)
    _ORCID_NAME_CACHE[orcid_url] = name
    return name


def _is_orcid(url: str) -> bool:
    return "orcid.org/" in (url or "").lower()


def harmonize_people(neighbours: list[Neighbour]) -> list[Neighbour]:
    """Bridge RDF ORCID authors onto matching Neo4j github contributors.

    Returns a new neighbour list where a matched RDF author is rewritten onto
    the github contributor's identity (so the downstream merge corroborates it)
    carrying its ORCID; unmatched ORCID authors keep their own row but get their
    name resolved for readability. Non-people edges pass through untouched."""
    # Index Neo4j github contributors by normalised name.
    by_name: dict[str, Neighbour] = {}
    for n in neighbours:
        if n.category == "people" and "github.com" in (n.hub_url or "") and n.label:
            by_name.setdefault(_norm_name(n.label), n)

    out: list[Neighbour] = []
    for n in neighbours:
        if n.category != "people" or not _is_orcid(n.external_url):
            out.append(n)
            continue
        name = _orcid_name(n.external_url)
        match = by_name.get(_norm_name(name)) if name else None
        if match is not None:
            # Same person → adopt the github identity so the merge collapses
            # the Neo4j + RDF rows; carry the ORCID for the logo-link.
            out.append(replace(
                n, hub_url=match.hub_url, label=match.label,
                orcid_url=n.external_url,
            ))
        elif name:
            # No github match, but give the ORCID author a human name instead
            # of the bare iD, and keep the ORCID link.
            out.append(replace(n, label=name, orcid_url=n.external_url))
        else:
            out.append(replace(n, orcid_url=n.external_url))
    return out
