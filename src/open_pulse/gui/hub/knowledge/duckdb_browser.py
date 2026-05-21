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


def list_rows(
    collection: str,
    *,
    page: int = 1,
    size: int = DEFAULT_PAGE_SIZE,
    q: str = "",
) -> dict[str, Any] | None:
    """Return a paginated slice of the DuckDB table behind ``collection``.

    Returns ``None`` when the collection isn't a known browsable surface.
    Otherwise returns ``{collection, db_path, table, columns, all_columns,
    hidden, rows, page, size, total, pages, q, matched}``.

    ``q`` is a case-insensitive substring filter applied to every column
    in :attr:`Backing.search_cols`. When non-empty the row count + total
    pages reflect the filtered set; ``matched`` is the filtered count.
    """
    b = _BACKING.get(collection)
    if b is None:
        return None

    page = max(1, int(page or 1))
    size = max(1, min(MAX_PAGE_SIZE, int(size or DEFAULT_PAGE_SIZE)))
    q = (q or "").strip()

    # Build a parameterised WHERE clause when a search term is present.
    # We OR the same ``ILIKE`` predicate across every configured search
    # column. Column names are hardcoded in ``search_cols``; only the
    # match pattern is user input.
    params_filter: list[Any] = []
    where_sql = ""
    if q and b.search_cols:
        like = f"%{q}%"
        clauses = []
        for col in b.search_cols:
            clauses.append(f'CAST("{col}" AS VARCHAR) ILIKE ?')
            params_filter.append(like)
        where_sql = "WHERE " + " OR ".join(clauses)

    offset = (page - 1) * size

    with _connect(b.db_path) as con:
        # Total / matched row count
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

        # Schema for the table view
        all_cols = [c[0] for c in con.execute(f'DESCRIBE "{b.table}"').fetchall()]
        visible_cols = [c for c in all_cols if c not in b.hidden_cols]
        if not visible_cols:
            visible_cols = all_cols
        col_list = ", ".join(f'"{c}"' for c in visible_cols)

        # Pull the page slice. Table + columns are hardcoded; user
        # input only appears as bind params.
        rows = con.execute(
            f'SELECT {col_list} FROM "{b.table}" {where_sql} LIMIT ? OFFSET ?',
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
    }
