"""Paginated read-only browser for the per-collection DuckDB source-of-truth tables.

Each Qdrant collection on the hub is backed by a DuckDB table that holds
the raw rows the embeddings + Qdrant points were derived from. This
module exposes a small read-only adapter the ``/api/hub/c/<name>/rows``
route can call to render a table view on the collection landing page.

Only collections that appear in :data:`_BACKING` are addressable —
unknown names return ``None``. Table names are hardcoded too; the
``name`` query parameter is never interpolated into SQL.

The DuckDB connection is opened ``read_only=True`` per request (cheap
on DuckDB) so an underlying file replacement is picked up without a
hub restart. Row counts are cached per-table because they don't change
between writes and a ``SELECT COUNT(*)`` over 100k rows can take 100s
of ms otherwise.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Stat:
    """One headline stat for the collection landing page.

    The SQL must return a single scalar (one row, one column). The
    ``unit`` hint tells the UI how to format the value: ``"int"`` →
    thousand-separator, ``"pct"`` → ``XX%``, ``"str"`` → as-is.
    """

    label: str
    sql: str
    unit: str = "int"


@dataclass(frozen=True)
class Backing:
    """How a Qdrant collection maps to a DuckDB table."""

    db_path: Path
    table: str
    # Columns we deliberately hide from the table view because they're
    # huge / binary / not useful to scroll past (vectors, raw blobs,
    # embedding_text duplicates the human-readable fields).
    hidden_cols: tuple[str, ...] = ()
    # Four small headline stats rendered at the top of the collection
    # page. Empty tuple → no stats card.
    stats: tuple[Stat, ...] = ()
    # Columns the search bar greps over (case-insensitive substring).
    # Empty tuple → no search box.
    search_cols: tuple[str, ...] = ()
    # Example terms shown as clickable chips below the search input.
    search_examples: tuple[str, ...] = ()


# Only the surfaces the user asked for in this pass — OAM Monitor +
# GitHub repos. Other DuckDBs (openalex, infoscience, ror, ...) ship in
# the same shape and can be added here one line at a time.
_DATA_ROOT = Path(os.environ.get("HUB_DATA_DIR_HOST", "/data"))

_BACKING: dict[str, Backing] = {
    "github_repos": Backing(
        db_path=_DATA_ROOT / "extractor/index/github/duckdb/github.duckdb",
        table="repos",
        hidden_cols=("languages", "topics", "raw"),
        stats=(
            Stat("Total repositories", "SELECT COUNT(*) FROM repos"),
            Stat("Distinct owners", "SELECT COUNT(DISTINCT owner) FROM repos"),
            Stat(
                "Total stars",
                "SELECT COALESCE(SUM(stargazers_count), 0) FROM repos",
            ),
            Stat(
                "Top language",
                "SELECT primary_language FROM repos "
                "WHERE primary_language IS NOT NULL AND primary_language <> '' "
                "GROUP BY primary_language ORDER BY COUNT(*) DESC LIMIT 1",
                unit="str",
            ),
        ),
        search_cols=("repo_id", "owner", "name", "description"),
        search_examples=("epfl", "deep learning", "snakemake", "rust"),
    ),
    "oamonitor_publications": Backing(
        db_path=_DATA_ROOT / "index/oamonitor/duckdb/oamonitor.duckdb",
        table="publications",
        hidden_cols=("embedding_text", "raw"),
        stats=(
            Stat("Total publications", "SELECT COUNT(*) FROM publications"),
            Stat(
                "Distinct publishers",
                "SELECT COUNT(DISTINCT publisher_id) FROM publications "
                "WHERE publisher_id IS NOT NULL",
            ),
            Stat(
                # oa_color is INTEGER (0 = closed, anything else = some OA flavour).
                "Open-access share",
                "SELECT 100.0 * COUNT(*) FILTER (WHERE oa_color <> 0) "
                "/ NULLIF(COUNT(*), 0) FROM publications",
                unit="pct",
            ),
            Stat(
                "Median year",
                "SELECT CAST(median(published_year) AS INTEGER) FROM publications "
                "WHERE published_year IS NOT NULL",
                unit="str",
            ),
        ),
        search_cols=("_id", "doi", "url", "publisher_name", "license"),
        search_examples=("10.1038", "nature", "springer", "elsevier"),
    ),
    "oamonitor_journals": Backing(
        db_path=_DATA_ROOT / "index/oamonitor/duckdb/oamonitor.duckdb",
        table="journals",
        hidden_cols=("embedding_text", "raw"),
        stats=(
            Stat("Total journals", "SELECT COUNT(*) FROM journals"),
            Stat(
                "Open-access journals",
                "SELECT COUNT(*) FROM journals WHERE oa_color <> 0",
            ),
            Stat(
                "Closed-access journals",
                "SELECT COUNT(*) FROM journals WHERE oa_color = 0",
            ),
            Stat(
                "With ISSN",
                "SELECT COUNT(*) FROM journals WHERE issns IS NOT NULL",
            ),
        ),
        search_cols=("title", "issns"),
        search_examples=("nature", "epfl", "science", "ieee"),
    ),
    "oamonitor_organisations": Backing(
        db_path=_DATA_ROOT / "index/oamonitor/duckdb/oamonitor.duckdb",
        table="organisations",
        hidden_cols=("embedding_text", "raw"),
        stats=(
            Stat("Total organisations", "SELECT COUNT(*) FROM organisations"),
            Stat(
                "Distinct countries",
                "SELECT COUNT(DISTINCT country_code) FROM organisations "
                "WHERE country_code IS NOT NULL",
            ),
            Stat(
                "Distinct types",
                "SELECT COUNT(DISTINCT type) FROM organisations WHERE type IS NOT NULL",
            ),
            Stat(
                "With GRID id",
                "SELECT COUNT(*) FROM organisations WHERE grid_id IS NOT NULL",
            ),
        ),
        search_cols=("name", "type", "country_code", "acronyms"),
        search_examples=("EPFL", "Swiss", "University", "CH"),
    ),
    "oamonitor_publishers": Backing(
        db_path=_DATA_ROOT / "index/oamonitor/duckdb/oamonitor.duckdb",
        table="publishers",
        hidden_cols=("embedding_text", "raw"),
        stats=(
            Stat("Total publishers", "SELECT COUNT(*) FROM publishers"),
            Stat(
                "Open-access publishers",
                "SELECT COUNT(*) FROM publishers WHERE oa_color <> 0",
            ),
            Stat(
                "Closed publishers",
                "SELECT COUNT(*) FROM publishers WHERE oa_color = 0",
            ),
            Stat(
                "With embedding",
                "SELECT COUNT(*) FROM publishers WHERE embedded_at IS NOT NULL",
            ),
        ),
        search_cols=("name",),
        search_examples=("springer", "elsevier", "nature", "wiley"),
    ),
}


# Remaining hub collections — wired as ``(db_path, table)`` pairs and
# enriched on module import with auto-derived stats + search columns
# (see ``_build_auto_backing``). ROR / SNSF have one DuckDB shared
# across regional Qdrant collections; we point each to the full table
# and let the search bar narrow it.
_AUTO_TABLES: dict[str, tuple[Path, str]] = {
    # OpenAlex — shared by the generic "authors" / "concepts" / ...
    "authors": (
        _DATA_ROOT / "extractor/index/openalex/duckdb/openalex.duckdb",
        "authors",
    ),
    "concepts": (
        _DATA_ROOT / "extractor/index/openalex/duckdb/openalex.duckdb",
        "concepts",
    ),
    "institutions": (
        _DATA_ROOT / "extractor/index/openalex/duckdb/openalex.duckdb",
        "institutions",
    ),
    "sources": (
        _DATA_ROOT / "extractor/index/openalex/duckdb/openalex.duckdb",
        "sources",
    ),
    "topics": (
        _DATA_ROOT / "extractor/index/openalex/duckdb/openalex.duckdb",
        "topics",
    ),
    # EPFL Graph
    "epfl_graph_disciplines": (
        _DATA_ROOT / "extractor/index/epfl_graph/duckdb/epfl_graph.duckdb",
        "categories",
    ),
    # ETH-Z Research Collection
    "ethz_research_collection_articles": (
        _DATA_ROOT
        / "extractor/index/ethz-research-collection/duckdb/ethz_research_collection.duckdb",
        "articles",
    ),
    "ethz_research_collection_organizations": (
        _DATA_ROOT
        / "extractor/index/ethz-research-collection/duckdb/ethz_research_collection.duckdb",
        "organizations",
    ),
    "ethz_research_collection_persons": (
        _DATA_ROOT
        / "extractor/index/ethz-research-collection/duckdb/ethz_research_collection.duckdb",
        "persons",
    ),
    # Hugging Face
    "hf_datasets": (
        _DATA_ROOT / "extractor/index/huggingface/duckdb/huggingface.duckdb",
        "datasets",
    ),
    "hf_models": (
        _DATA_ROOT / "extractor/index/huggingface/duckdb/huggingface.duckdb",
        "models",
    ),
    "hf_orgs": (
        _DATA_ROOT / "extractor/index/huggingface/duckdb/huggingface.duckdb",
        "orgs",
    ),
    "hf_spaces": (
        _DATA_ROOT / "extractor/index/huggingface/duckdb/huggingface.duckdb",
        "spaces",
    ),
    # Infoscience
    "infoscience_articles": (
        _DATA_ROOT / "extractor/index/infoscience/duckdb/infoscience.duckdb",
        "articles",
    ),
    "infoscience_organizations": (
        _DATA_ROOT / "extractor/index/infoscience/duckdb/infoscience.duckdb",
        "organizations",
    ),
    "infoscience_persons": (
        _DATA_ROOT / "extractor/index/infoscience/duckdb/infoscience.duckdb",
        "persons",
    ),
    # ORCID
    "orcid_epfl_educations": (
        _DATA_ROOT / "extractor/index/orcid-epfl/duckdb/orcid.duckdb",
        "educations",
    ),
    "orcid_epfl_employments": (
        _DATA_ROOT / "extractor/index/orcid-epfl/duckdb/orcid.duckdb",
        "employments",
    ),
    "orcid_epfl_persons": (
        _DATA_ROOT / "extractor/index/orcid-epfl/duckdb/orcid.duckdb",
        "persons",
    ),
    "orcid_switzerland_employments": (
        _DATA_ROOT / "extractor/index/orcid-switzerland/duckdb/orcid.duckdb",
        "employments",
    ),
    "orcid_switzerland_persons": (
        _DATA_ROOT / "extractor/index/orcid-switzerland/duckdb/orcid.duckdb",
        "persons",
    ),
    # RenkuLab
    "renkulab_data_connectors": (
        _DATA_ROOT / "extractor/index/renkulab/duckdb/renkulab.duckdb",
        "data_connectors",
    ),
    "renkulab_groups": (
        _DATA_ROOT / "extractor/index/renkulab/duckdb/renkulab.duckdb",
        "groups",
    ),
    "renkulab_projects": (
        _DATA_ROOT / "extractor/index/renkulab/duckdb/renkulab.duckdb",
        "projects",
    ),
    "renkulab_users": (
        _DATA_ROOT / "extractor/index/renkulab/duckdb/renkulab.duckdb",
        "users",
    ),
    # ROR — same records table for every regional flavour; search bar narrows it.
    "ror_epfl_ethz": (_DATA_ROOT / "extractor/index/ror/duckdb/ror.duckdb", "records"),
    "ror_europe": (_DATA_ROOT / "extractor/index/ror/duckdb/ror.duckdb", "records"),
    "ror_switzerland": (
        _DATA_ROOT / "extractor/index/ror/duckdb/ror.duckdb",
        "records",
    ),
    "ror_worldwide": (_DATA_ROOT / "extractor/index/ror/duckdb/ror.duckdb", "records"),
    # SNSF — same here. Grants is the headline table.
    "snsf_epfl": (_DATA_ROOT / "extractor/index/snsf/duckdb/snsf.duckdb", "grants"),
    "snsf_ethz": (_DATA_ROOT / "extractor/index/snsf/duckdb/snsf.duckdb", "grants"),
    "snsf_switzerland": (
        _DATA_ROOT / "extractor/index/snsf/duckdb/snsf.duckdb",
        "grants",
    ),
    # SwissUBase — studies is the largest non-empty table.
    "swissubase_entities": (
        _DATA_ROOT / "extractor/index/swissubase/duckdb/swissubase.duckdb",
        "studies",
    ),
    # Zenodo — records is the headline table; communities + creators are
    # exposed alongside so the row browser can serve them directly via
    # ``/hub/c/<name>`` even though they don't have their own Qdrant
    # collection (yet — they live only in the DuckDB source-of-truth).
    "zenodo_records": (
        _DATA_ROOT / "extractor/index/zenodo/duckdb/zenodo.duckdb",
        "records",
    ),
    "zenodo_communities": (
        _DATA_ROOT / "extractor/index/zenodo/duckdb/zenodo.duckdb",
        "communities",
    ),
    "zenodo_creators": (
        _DATA_ROOT / "extractor/index/zenodo/duckdb/zenodo.duckdb",
        "creators",
    ),
}


# Example search chips per auto-backed collection. Picked to demo the
# kinds of queries that hit useful matches — names of institutions /
# models / venues / topics — not arbitrary keywords.
_AUTO_SEARCH_EXAMPLES: dict[str, tuple[str, ...]] = {
    # OpenAlex
    "authors": ("LeCun", "Hinton", "EPFL", "ETH"),
    "concepts": ("machine learning", "quantum", "biology", "neural"),
    "institutions": ("EPFL", "ETH", "MIT", "Switzerland"),
    "sources": ("Nature", "Science", "IEEE", "ACM"),
    "topics": ("artificial intelligence", "genetics", "climate", "robotics"),
    # EPFL Graph
    "epfl_graph_disciplines": ("physics", "computer", "biology", "mathematics"),
    # ETH-Z Research Collection
    "ethz_research_collection_articles": (
        "deep learning",
        "robotics",
        "swiss",
        "quantum",
    ),
    "ethz_research_collection_organizations": ("Department", "Institute", "Laboratory"),
    "ethz_research_collection_persons": ("Müller", "Schmidt", "Anna", "Wolfgang"),
    # Hugging Face
    "hf_datasets": ("imagenet", "translation", "audio", "code"),
    "hf_models": ("llama", "bert", "diffusion", "whisper"),
    "hf_orgs": ("google", "meta", "microsoft", "stability"),
    "hf_spaces": ("chat", "image", "demo", "translator"),
    # Infoscience
    "infoscience_articles": ("deep learning", "epfl", "physics", "quantum"),
    "infoscience_organizations": ("Laboratory", "Institute", "Lab", "Group"),
    "infoscience_persons": ("Patrick", "Anna", "Müller", "Martin"),
    # ORCID
    "orcid_epfl_educations": ("EPFL", "Lausanne", "PhD", "Master"),
    "orcid_epfl_employments": ("Professor", "PostDoc", "EPFL", "Researcher"),
    "orcid_epfl_persons": ("Anna", "Patrick", "Müller", "Maria"),
    "orcid_switzerland_employments": ("Professor", "ETH", "EPFL", "Zurich"),
    "orcid_switzerland_persons": ("Anna", "Müller", "Patrick", "Schmidt"),
    # RenkuLab
    "renkulab_data_connectors": ("github", "s3", "azure", "doi"),
    "renkulab_groups": ("research", "epfl", "course", "lab"),
    "renkulab_projects": ("machine learning", "tutorial", "data", "python"),
    "renkulab_users": ("epfl", "alice", "patrick", "anna"),
    # ROR
    "ror_epfl_ethz": ("EPFL", "ETH", "Lausanne", "Zurich"),
    "ror_europe": ("Cambridge", "Oxford", "Munich", "Paris"),
    "ror_switzerland": ("EPFL", "ETH", "Zurich", "Basel"),
    "ror_worldwide": ("Stanford", "MIT", "Harvard", "Tokyo"),
    # SNSF
    "snsf_epfl": ("EPFL", "machine learning", "physics", "Lausanne"),
    "snsf_ethz": ("ETH", "Zurich", "robotics", "quantum"),
    "snsf_switzerland": ("Swiss", "professor", "biology", "chemistry"),
    # SwissUBase
    "swissubase_entities": ("survey", "FORS", "Switzerland", "households"),
    # Zenodo
    "zenodo_records": ("dataset", "epfl", "10.5281", "machine learning"),
    "zenodo_communities": ("epfl", "swiss", "open", "research"),
    "zenodo_creators": ("EPFL", "Müller", "Patrick", "Anna"),
}


# Heuristic stat templates, applied in order. Each entry yields one
# headline tile when the table has a column that matches.
# (label, candidate_col_names, sql_template{c,t}, unit)
_AUTO_STAT_PATTERNS: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    (
        "Distinct countries",
        ("country", "country_code", "country_iso"),
        'SELECT COUNT(DISTINCT "{c}") FROM "{t}" WHERE "{c}" IS NOT NULL',
        "int",
    ),
    (
        "Distinct types",
        ("type", "kind", "category", "model_type", "library_name"),
        'SELECT COUNT(DISTINCT "{c}") FROM "{t}" WHERE "{c}" IS NOT NULL',
        "int",
    ),
    (
        "Distinct languages",
        ("language", "primary_language", "lang"),
        'SELECT COUNT(DISTINCT "{c}") FROM "{t}" WHERE "{c}" IS NOT NULL',
        "int",
    ),
    (
        "Distinct hosts",
        ("host", "publisher_name", "publisher", "venue"),
        'SELECT COUNT(DISTINCT "{c}") FROM "{t}" WHERE "{c}" IS NOT NULL',
        "int",
    ),
    (
        "Distinct owners",
        ("owner", "organisation", "organization", "affiliation"),
        'SELECT COUNT(DISTINCT "{c}") FROM "{t}" WHERE "{c}" IS NOT NULL',
        "int",
    ),
    (
        "Total downloads",
        ("downloads", "downloads_count", "download_count"),
        'SELECT COALESCE(SUM(CAST("{c}" AS BIGINT)), 0) FROM "{t}"',
        "int",
    ),
    (
        "Total likes",
        ("likes", "likes_count", "stargazers_count"),
        'SELECT COALESCE(SUM(CAST("{c}" AS BIGINT)), 0) FROM "{t}"',
        "int",
    ),
    (
        "Median year",
        ("year", "published_year", "publication_year", "publicationYear"),
        'SELECT CAST(median("{c}") AS INTEGER) FROM "{t}" WHERE "{c}" IS NOT NULL',
        "str",
    ),
    (
        "Distinct ORCIDs",
        ("orcid", "orcid_id"),
        'SELECT COUNT(DISTINCT "{c}") FROM "{t}" WHERE "{c}" IS NOT NULL',
        "int",
    ),
    (
        "Distinct DOIs",
        ("doi",),
        'SELECT COUNT(DISTINCT "{c}") FROM "{t}" WHERE "{c}" IS NOT NULL',
        "int",
    ),
)

# Text-like column names worth grep'ing over by default.
_AUTO_SEARCH_PRIORITY = (
    "name",
    "title",
    "label",
    "display_name",
    "description",
    "owner",
    "author",
    "creator",
    "publisher_name",
    "publisher",
    "id",
    "_id",
    "doi",
    "url",
    "homepage",
)


# Columns hidden from the table view — bulky blobs and embedding vectors.
def _is_hidden_col(col: str) -> bool:
    nl = col.lower()
    return (
        "embedding" in nl
        or "_vector" in nl
        or "_blob" in nl
        or nl == "raw"
        or nl == "content"
    )


def _build_auto_backing(collection: str) -> Backing | None:
    """Construct a ``Backing`` for ``collection`` by sniffing the DuckDB schema.

    Returns ``None`` if the file is missing or the table can't be read.
    Stats are derived from ``_AUTO_STAT_PATTERNS`` against the available
    columns; the first matching pattern fills each slot (after Total).
    """
    pair = _AUTO_TABLES.get(collection)
    if pair is None:
        return None
    db_path, table = pair
    if not db_path.is_file():
        return None

    try:
        with _connect(db_path) as con:
            schema = con.execute(f'PRAGMA table_info("{table}")').fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("auto-backing schema sniff failed for %s: %s", collection, exc)
        return None

    if not schema:
        return None

    # PRAGMA table_info returns (cid, name, type, notnull, dflt_value, pk).
    cols = [(row[1], (row[2] or "").upper()) for row in schema]
    names = [c[0] for c in cols]
    lowered = {c.lower(): c for c in names}

    # Stats — always Total, then up to 3 more from the pattern table.
    stats: list[Stat] = [
        Stat(f"Total {table}", f'SELECT COUNT(*) FROM "{table}"'),
    ]
    used: set[str] = set()
    for label, opts, tmpl, unit in _AUTO_STAT_PATTERNS:
        if len(stats) >= 4:
            break
        for opt in opts:
            real = lowered.get(opt)
            if real and real not in used:
                used.add(real)
                stats.append(Stat(label, tmpl.format(c=real, t=table), unit=unit))
                break

    # Search columns — prioritised text-ish names, capped at 4.
    search_cols: list[str] = []
    for p in _AUTO_SEARCH_PRIORITY:
        real = lowered.get(p)
        if real and real not in search_cols:
            search_cols.append(real)
        if len(search_cols) >= 4:
            break
    if not search_cols:
        # Fallback: any VARCHAR/TEXT columns.
        search_cols = [
            n for n, t in cols if t.startswith(("VARCHAR", "TEXT", "STRING"))
        ][:4]

    hidden = tuple(n for n in names if _is_hidden_col(n))

    return Backing(
        db_path=db_path,
        table=table,
        hidden_cols=hidden,
        stats=tuple(stats),
        search_cols=tuple(search_cols),
        search_examples=_AUTO_SEARCH_EXAMPLES.get(collection, ()),
    )


# NOTE: the actual merge into ``_BACKING`` happens at the bottom of
# this module, after ``_connect`` is defined.


# Row-count cache — keyed by ``(db_path, table)``. Invalidated only on
# hub restart, which is acceptable here: a DuckDB table that changes
# also gets a manual swap of the ``.duckdb`` file, and we tell the
# operator to restart the hub for those swaps to fully apply (it
# already needs that for schema changes anyway).
_COUNT_CACHE: dict[tuple[str, str], int] = {}
_COUNT_LOCK = threading.Lock()


def is_browsable(collection: str) -> bool:
    """True when the collection has a registered DuckDB backing."""
    return collection in _BACKING


def backing_for(collection: str) -> Backing | None:
    """Return the backing record or ``None`` if the collection isn't registered."""
    return _BACKING.get(collection)


def row_count_for(collection: str) -> int | None:
    """Public wrapper around the cached row counter.

    Returns the underlying DuckDB row count for a browsable collection,
    or ``None`` if the collection isn't registered / its backing file
    is missing. Used by the hub home tiles to display the
    source-of-truth entity count instead of the (chunk-inflated)
    Qdrant points count.
    """
    b = _BACKING.get(collection)
    if b is None:
        return None
    try:
        return _row_count(b)
    except Exception as exc:  # noqa: BLE001
        log.warning("row_count_for(%s) failed: %s", collection, exc)
        return None


def _json_safe(v: Any) -> Any:
    """Coerce a DuckDB cell value into something the JSON encoder can handle.

    DuckDB returns ``datetime``, ``date``, ``Decimal``, ``bytes`` and
    sometimes already-decoded ``dict``/``list`` for ``JSON`` columns.
    """
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        # First few bytes only — these are usually embeddings (4-byte
        # floats), not human-readable content.
        return f"<{len(v)} bytes>"
    if isinstance(v, (list, tuple, dict)):
        return v
    return str(v)


def _connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    if not db_path.is_file():
        raise FileNotFoundError(f"DuckDB file missing: {db_path}")
    return duckdb.connect(str(db_path), read_only=True)


def _row_count(b: Backing) -> int:
    key = (str(b.db_path), b.table)
    with _COUNT_LOCK:
        cached = _COUNT_CACHE.get(key)
    if cached is not None:
        return cached
    with _connect(b.db_path) as con:
        # Table name is hardcoded in the mapping; quoting via double-
        # quote identifiers is safe (DuckDB does not interpolate them).
        n = con.execute(f'SELECT COUNT(*) FROM "{b.table}"').fetchone()[0]
    n = int(n)
    with _COUNT_LOCK:
        _COUNT_CACHE[key] = n
    return n


MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


def _format_stat(value: Any, unit: str) -> str:
    """Render a stat scalar according to its unit hint."""
    if value is None:
        return "—"
    if unit == "pct":
        try:
            return f"{float(value):.1f}%"
        except (TypeError, ValueError):
            return str(value)
    if unit == "int":
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def top_stats(collection: str) -> list[dict[str, str]] | None:
    """Run the per-collection scalar stat queries and return ``[{label, value}, ...]``.

    None when the collection isn't registered. Empty list when the
    backing has no ``stats`` configured.
    """
    b = _BACKING.get(collection)
    if b is None:
        return None
    if not b.stats:
        return []
    out: list[dict[str, str]] = []
    with _connect(b.db_path) as con:
        for stat in b.stats:
            try:
                row = con.execute(stat.sql).fetchone()
                raw = row[0] if row else None
                value = _format_stat(raw, stat.unit)
            except Exception as exc:  # noqa: BLE001
                log.warning("stat %r failed on %s: %s", stat.label, collection, exc)
                value = "—"
            out.append({"label": stat.label, "value": value})
    return out


def search_info(collection: str) -> dict[str, Any] | None:
    """Surface the search-bar metadata (cols + example chips) for the UI."""
    b = _BACKING.get(collection)
    if b is None:
        return None
    return {
        "enabled": bool(b.search_cols),
        "columns": list(b.search_cols),
        "examples": list(b.search_examples),
    }


def _build_filter(b: Backing, q: str) -> tuple[str, list[Any]]:
    """Build the parameterised WHERE clause used by browse + export."""
    if not q or not b.search_cols:
        return "", []
    like = f"%{q}%"
    params: list[Any] = []
    clauses: list[str] = []
    for col in b.search_cols:
        clauses.append(f'CAST("{col}" AS VARCHAR) ILIKE ?')
        params.append(like)
    return "WHERE " + " OR ".join(clauses), params


def _build_order(sort: str, visible_cols: list[str]) -> str:
    """Validate ``sort`` against ``visible_cols`` and return an ORDER BY clause.

    Format: ``"colname"`` (asc) or ``"colname:desc"``. Returns ``""``
    when no/invalid sort. Column names are looked up in the (hardcoded)
    visible column list, so the value never reaches SQL by string
    interpolation without validation.
    """
    if not sort:
        return ""
    col, _, raw_dir = sort.partition(":")
    col = col.strip()
    direction = (raw_dir or "asc").strip().lower()
    if direction not in {"asc", "desc"}:
        direction = "asc"
    if col not in visible_cols:
        return ""
    return f'ORDER BY "{col}" {direction.upper()} NULLS LAST'


def _resolve_visible_cols(
    con: duckdb.DuckDBPyConnection, b: Backing
) -> tuple[list[str], list[str]]:
    """Return ``(all_cols, visible_cols)`` for the backing's table."""
    all_cols = [c[0] for c in con.execute(f'DESCRIBE "{b.table}"').fetchall()]
    visible = [c for c in all_cols if c not in b.hidden_cols]
    if not visible:
        visible = all_cols
    return all_cols, visible


def list_rows(
    collection: str,
    *,
    page: int = 1,
    size: int = DEFAULT_PAGE_SIZE,
    q: str = "",
    sort: str = "",
) -> dict[str, Any] | None:
    """Return a paginated slice of the DuckDB table behind ``collection``.

    Returns ``None`` when the collection isn't a known browsable surface.
    Otherwise returns ``{collection, db_path, table, columns, all_columns,
    hidden, rows, page, size, total, pages, q, sort, matched}``.

    ``q`` is a case-insensitive substring filter applied to every column
    in :attr:`Backing.search_cols`. When non-empty the row count + total
    pages reflect the filtered set; ``matched`` is the filtered count.

    ``sort`` is ``"col"`` (asc) or ``"col:desc"``. Invalid columns are
    silently ignored.
    """
    b = _BACKING.get(collection)
    if b is None:
        return None

    page = max(1, int(page or 1))
    size = max(1, min(MAX_PAGE_SIZE, int(size or DEFAULT_PAGE_SIZE)))
    q = (q or "").strip()
    sort = (sort or "").strip()

    where_sql, params_filter = _build_filter(b, q)
    offset = (page - 1) * size

    with _connect(b.db_path) as con:
        if where_sql:
            matched = int(
                con.execute(
                    f'SELECT COUNT(*) FROM "{b.table}" {where_sql}', params_filter
                ).fetchone()[0]
            )
        else:
            matched = _row_count(b)
        total = _row_count(b)
        pages = max(1, (matched + size - 1) // size)

        all_cols, visible_cols = _resolve_visible_cols(con, b)
        col_list = ", ".join(f'"{c}"' for c in visible_cols)
        order_sql = _build_order(sort, visible_cols)

        rows = con.execute(
            f'SELECT {col_list} FROM "{b.table}" {where_sql} {order_sql} '
            "LIMIT ? OFFSET ?",
            [*params_filter, size, offset],
        ).fetchall()

    rows_out = [
        {col: _json_safe(val) for col, val in zip(visible_cols, row)} for row in rows
    ]
    return {
        "collection": collection,
        "db_path": str(b.db_path),
        "table": b.table,
        "columns": visible_cols,
        "all_columns": all_cols,
        "hidden": list(b.hidden_cols),
        "rows": rows_out,
        "page": page,
        "size": size,
        "total": total,
        "matched": matched,
        "pages": pages,
        "q": q,
        "sort": sort,
    }


# Cap on how many rows a single ``/export`` call may return. Picked
# conservatively so a single click can't materialise a 100MB payload
# in memory. The UI shows a "(truncated)" hint when this kicks in.
MAX_EXPORT_ROWS = 50_000


def export_rows(
    collection: str,
    *,
    q: str = "",
    sort: str = "",
    limit: int = MAX_EXPORT_ROWS,
) -> dict[str, Any] | None:
    """Fetch the full filtered+sorted dataset for export — no pagination.

    Returns ``{columns, rows, matched, truncated, table}`` or ``None``
    when the collection isn't registered.
    """
    b = _BACKING.get(collection)
    if b is None:
        return None

    q = (q or "").strip()
    sort = (sort or "").strip()
    limit = max(1, min(int(limit or MAX_EXPORT_ROWS), MAX_EXPORT_ROWS))

    where_sql, params_filter = _build_filter(b, q)

    with _connect(b.db_path) as con:
        if where_sql:
            matched = int(
                con.execute(
                    f'SELECT COUNT(*) FROM "{b.table}" {where_sql}', params_filter
                ).fetchone()[0]
            )
        else:
            matched = _row_count(b)

        _, visible_cols = _resolve_visible_cols(con, b)
        col_list = ", ".join(f'"{c}"' for c in visible_cols)
        order_sql = _build_order(sort, visible_cols)

        rows = con.execute(
            f'SELECT {col_list} FROM "{b.table}" {where_sql} {order_sql} LIMIT ?',
            [*params_filter, limit + 1],
        ).fetchall()

    truncated = len(rows) > limit
    rows = rows[:limit]
    rows_out = [[_json_safe(v) for v in row] for row in rows]
    return {
        "columns": visible_cols,
        "rows": rows_out,
        "matched": matched,
        "truncated": truncated,
        "table": b.table,
    }


def _csv_cell(v: Any) -> str:
    """RFC-4180 cell encoding — same rules the /databases page uses."""
    if v is None:
        return ""
    if isinstance(v, (list, tuple, dict)):
        s = json.dumps(v, ensure_ascii=False, default=str)
    else:
        s = str(v)
    if any(c in s for c in ('"', ",", "\n", "\r")):
        s = '"' + s.replace('"', '""') + '"'
    return s


def _tsv_cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple, dict)):
        s = json.dumps(v, ensure_ascii=False, default=str)
    else:
        s = str(v)
    # Strip TSV-breaking whitespace.
    return s.replace("\t", " ").replace("\n", " ").replace("\r", " ")


def _md_cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple, dict)):
        s = json.dumps(v, ensure_ascii=False, default=str)
    else:
        s = str(v)
    return s.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_export(collection: str, fmt: str, **kwargs: Any) -> tuple[str, str] | None:
    """Render the export payload for a given format.

    Returns ``(body, mime)`` or ``None`` if the collection / format is
    unknown. Supported formats: ``csv``, ``tsv``, ``md``, ``json-rec``,
    ``json-col``.
    """
    payload = export_rows(collection, **kwargs)
    if payload is None:
        return None
    cols: list[str] = payload["columns"]
    rows: list[list[Any]] = payload["rows"]

    if fmt == "csv":
        lines = [",".join(_csv_cell(c) for c in cols)]
        lines.extend(",".join(_csv_cell(v) for v in row) for row in rows)
        return "\n".join(lines) + "\n", "text/csv; charset=utf-8"
    if fmt == "tsv":
        lines = ["\t".join(_tsv_cell(c) for c in cols)]
        lines.extend("\t".join(_tsv_cell(v) for v in row) for row in rows)
        return "\n".join(lines) + "\n", "text/tab-separated-values; charset=utf-8"
    if fmt == "md":
        head = "| " + " | ".join(_md_cell(c) for c in cols) + " |"
        sep = "| " + " | ".join("---" for _ in cols) + " |"
        body = ["| " + " | ".join(_md_cell(v) for v in row) + " |" for row in rows]
        return "\n".join([head, sep, *body]) + "\n", "text/markdown; charset=utf-8"
    if fmt == "json-rec":
        records = [{c: v for c, v in zip(cols, row)} for row in rows]
        return json.dumps(
            records, ensure_ascii=False, default=str, indent=2
        ), "application/json; charset=utf-8"
    if fmt == "json-col":
        columnar: dict[str, list[Any]] = {c: [] for c in cols}
        for row in rows:
            for c, v in zip(cols, row):
                columnar[c].append(v)
        return json.dumps(
            columnar, ensure_ascii=False, default=str, indent=2
        ), "application/json; charset=utf-8"

    return None


# Final step at module import: enrich ``_BACKING`` with auto-derived
# entries for every collection in ``_AUTO_TABLES`` that doesn't already
# have a hand-tuned record. Done at the bottom so all helpers used by
# ``_build_auto_backing`` (notably ``_connect``) are already defined.
for _coll in _AUTO_TABLES:
    if _coll not in _BACKING:
        _auto = _build_auto_backing(_coll)
        if _auto is not None:
            _BACKING[_coll] = _auto
