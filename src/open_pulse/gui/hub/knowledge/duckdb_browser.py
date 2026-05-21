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
class Backing:
    """How a Qdrant collection maps to a DuckDB table."""

    db_path: Path
    table: str
    # Columns we deliberately hide from the table view because they're
    # huge / binary / not useful to scroll past (vectors, raw blobs,
    # embedding_text duplicates the human-readable fields).
    hidden_cols: tuple[str, ...] = ()


# Only the surfaces the user asked for in this pass — OAM Monitor +
# GitHub repos. Other DuckDBs (openalex, infoscience, ror, ...) ship in
# the same shape and can be added here one line at a time.
_DATA_ROOT = Path(os.environ.get("HUB_DATA_DIR_HOST", "/data"))

_BACKING: dict[str, Backing] = {
    "github_repos": Backing(
        db_path=_DATA_ROOT / "extractor/index/github/duckdb/github.duckdb",
        table="repos",
        hidden_cols=("languages", "topics"),  # JSON blobs — keep as `view raw` later
    ),
    "oamonitor_publications": Backing(
        db_path=_DATA_ROOT / "index/oamonitor/duckdb/oamonitor.duckdb",
        table="publications",
        hidden_cols=("embedding_text", "raw"),
    ),
    "oamonitor_journals": Backing(
        db_path=_DATA_ROOT / "index/oamonitor/duckdb/oamonitor.duckdb",
        table="journals",
        hidden_cols=("embedding_text", "raw"),
    ),
    "oamonitor_organisations": Backing(
        db_path=_DATA_ROOT / "index/oamonitor/duckdb/oamonitor.duckdb",
        table="organisations",
        hidden_cols=("embedding_text", "raw"),
    ),
    "oamonitor_publishers": Backing(
        db_path=_DATA_ROOT / "index/oamonitor/duckdb/oamonitor.duckdb",
        table="publishers",
        hidden_cols=("embedding_text", "raw"),
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


def list_rows(
    collection: str,
    *,
    page: int = 1,
    size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any] | None:
    """Return a paginated slice of the DuckDB table behind ``collection``.

    Returns ``None`` when the collection isn't a known browsable surface.
    Otherwise returns::

        {
          "collection": "<name>",
          "db_path":    "/data/.../<provider>.duckdb",
          "table":      "<table_name>",
          "columns":    ["c1", "c2", ...],          # without hidden_cols
          "all_columns": ["c1", "c2", ...],         # including hidden_cols
          "hidden":     ["raw", "embedding_text"],
          "rows":       [ { c1: v, c2: v, ... }, ... ],
          "page":       1,
          "size":       50,
          "total":      99331,
          "pages":      1987
        }
    """
    b = _BACKING.get(collection)
    if b is None:
        return None

    page = max(1, int(page or 1))
    size = max(1, min(MAX_PAGE_SIZE, int(size or DEFAULT_PAGE_SIZE)))
    offset = (page - 1) * size

    total = _row_count(b)
    pages = max(1, (total + size - 1) // size)

    with _connect(b.db_path) as con:
        # Pull the full schema once so the UI can show *all* column
        # names (including hidden ones) and let the user expand them.
        all_cols = [c[0] for c in con.execute(f'DESCRIBE "{b.table}"').fetchall()]
        visible_cols = [c for c in all_cols if c not in b.hidden_cols]
        if not visible_cols:
            visible_cols = all_cols  # defensive: never render zero columns
        col_list = ", ".join(f'"{c}"' for c in visible_cols)
        # Bind page params; table + columns are hardcoded.
        cur = con.execute(
            f'SELECT {col_list} FROM "{b.table}" LIMIT ? OFFSET ?',
            [size, offset],
        )
        rows = cur.fetchall()

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
        "pages": pages,
    }
