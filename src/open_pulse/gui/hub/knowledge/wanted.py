"""Capture-by-URL backlog.

When ``/hub/<ref>`` resolves to nothing across our stores we record
the request here and surface a "we don't know this yet" placeholder.
The wanted list is a feed for the crawler / extractor team: each row
points at a URL the public has shown interest in but the data plane
hasn't enriched yet.

Stored in the existing ``data/hub/app.db`` SQLite next to ``saved_queries``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

_TABLE = "hub_wanted"


def _connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data_dir / "app.db")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url         TEXT NOT NULL UNIQUE,
            host        TEXT NOT NULL,
            path        TEXT NOT NULL,
            first_seen  TEXT NOT NULL DEFAULT (datetime('now')),
            last_seen   TEXT NOT NULL DEFAULT (datetime('now')),
            hits        INTEGER NOT NULL DEFAULT 1,
            note        TEXT NOT NULL DEFAULT '',
            resolved_at TEXT
        )
        """
    )
    conn.commit()
    return conn


@dataclass(frozen=True)
class WantedRow:
    id: int
    url: str
    host: str
    path: str
    first_seen: str
    last_seen: str
    hits: int
    note: str
    resolved_at: str | None


def _row(r: sqlite3.Row | tuple) -> WantedRow:
    return WantedRow(
        id=r[0],
        url=r[1],
        host=r[2],
        path=r[3],
        first_seen=r[4],
        last_seen=r[5],
        hits=r[6],
        note=r[7],
        resolved_at=r[8],
    )


def record_miss(data_dir: Path, *, url: str, host: str, path: str) -> WantedRow:
    """Insert a new wanted row, or bump hits + last_seen if it already exists.

    Returns the row so the caller can decide whether to render the
    "newly queued" banner versus the "already in the queue" one.
    """
    conn = _connect(data_dir)
    try:
        conn.execute(
            f"""
            INSERT INTO {_TABLE} (url, host, path)
            VALUES (?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                hits = hits + 1,
                last_seen = datetime('now'),
                resolved_at = NULL
            """,
            (url, host, path),
        )
        conn.commit()
        cur = conn.execute(f"SELECT * FROM {_TABLE} WHERE url = ?", (url,))
        row = cur.fetchone()
        return _row(row)
    finally:
        conn.close()


def list_wanted(data_dir: Path, *, include_resolved: bool = False) -> list[WantedRow]:
    """Newest first, resolved rows hidden unless explicitly requested."""
    conn = _connect(data_dir)
    try:
        where = "" if include_resolved else "WHERE resolved_at IS NULL"
        cur = conn.execute(
            f"SELECT * FROM {_TABLE} {where} ORDER BY last_seen DESC LIMIT 500"
        )
        return [_row(r) for r in cur.fetchall()]
    finally:
        conn.close()


def mark_resolved(data_dir: Path, wanted_id: int) -> None:
    conn = _connect(data_dir)
    try:
        conn.execute(
            f"UPDATE {_TABLE} SET resolved_at = datetime('now') WHERE id = ?",
            (wanted_id,),
        )
        conn.commit()
    finally:
        conn.close()


def delete_wanted(data_dir: Path, wanted_id: int) -> None:
    conn = _connect(data_dir)
    try:
        conn.execute(f"DELETE FROM {_TABLE} WHERE id = ?", (wanted_id,))
        conn.commit()
    finally:
        conn.close()
