"""Reader API tokens — multiple, admin-managed, with per-token access logging.

A "token" is a high-entropy random string used as the reader password (the
hub's Basic auth is password-only). We store only ``sha256(token)``; the
plaintext is shown once at creation, never persisted. Because tokens are
high-entropy, a fast SHA-256 index lookup is safe — no bcrypt needed, so
matching a request against many tokens stays O(1).

Every request a token makes is logged (method + a coarse "kind" + timestamp,
**no IP**) so the admin Users panel can show what each token does and when.

Lives in the hub's ``app.db`` SQLite next to the query history.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from typing import Any

from .config import load_settings

_SETTINGS = load_settings()


def _db_path() -> str:
    _SETTINGS.data_dir.mkdir(parents=True, exist_ok=True)
    return str(_SETTINGS.data_dir / "app.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS reader_tokens (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            label        TEXT NOT NULL,
            token_sha256 TEXT NOT NULL UNIQUE,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            revoked_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS token_access (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id  INTEGER NOT NULL,
            ts        TEXT NOT NULL DEFAULT (datetime('now')),
            method    TEXT NOT NULL,
            kind      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_token_access_tid ON token_access(token_id, id);
        """
    )
    # Migration: per-token data scope (Phase 1 = named-graph allow-list). JSON:
    # {"graphs": [<iri>, …]} — absent/empty means full access (no ceiling).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(reader_tokens)")}
    if "scope_json" not in cols:
        conn.execute("ALTER TABLE reader_tokens ADD COLUMN scope_json TEXT")
        conn.commit()
    # Migration: record the full request path (endpoint + query string) so the
    # activity log shows what each call actually hit, not just the coarse kind.
    acols = {r[1] for r in conn.execute("PRAGMA table_info(token_access)")}
    if "path" not in acols:
        conn.execute("ALTER TABLE token_access ADD COLUMN path TEXT")
        conn.commit()
    return conn


def _scope_from_json(raw: str | None) -> dict[str, Any]:
    try:
        return json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}


def _graphs_of(scope: dict[str, Any]) -> list[str]:
    g = scope.get("graphs")
    return [x for x in g if isinstance(x, str)] if isinstance(g, list) else []


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── mutations (admin) ──────────────────────────────────────────────────────
def create_token(label: str, graphs: list[str] | None = None) -> dict[str, Any]:
    """Mint a reader token. Returns the plaintext ONCE (only place it exists).

    ``graphs`` (optional) restricts the token to those named graphs — a
    non-empty list becomes its scope ceiling; ``None``/empty = full reader."""
    token = "rdr_" + secrets.token_urlsafe(32)
    scope_json = json.dumps({"graphs": graphs}) if graphs else None
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO reader_tokens(label, token_sha256, scope_json) "
            "VALUES (?, ?, ?)",
            ((label or "").strip() or "reader", _sha(token), scope_json),
        )
        conn.commit()
        return {"id": cur.lastrowid, "label": label, "token": token}
    finally:
        conn.close()


def set_scope(token_id: int, graphs: list[str] | None) -> bool:
    """Set a token's named-graph ceiling (empty/None = full access)."""
    scope_json = json.dumps({"graphs": graphs}) if graphs else None
    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE reader_tokens SET scope_json = ? WHERE id = ?",
            (scope_json, token_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def token_scope_graphs(token_id: int | None) -> tuple[str, ...]:
    """The named-graph ceiling for a token, or ``()`` for full access. Called
    on the auth hot-path, so keep it a single indexed lookup."""
    if token_id is None:
        return ()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT scope_json FROM reader_tokens WHERE id = ? AND revoked_at IS NULL",
            (token_id,),
        ).fetchone()
        return tuple(_graphs_of(_scope_from_json(row["scope_json"]))) if row else ()
    finally:
        conn.close()


def revoke_token(token_id: int) -> bool:
    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE reader_tokens SET revoked_at = datetime('now') "
            "WHERE id = ? AND revoked_at IS NULL",
            (token_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── auth path (hot) ────────────────────────────────────────────────────────
def match_token(password: str) -> int | None:
    """Active reader-token id for a presented secret, or None. O(1) hash lookup."""
    if not password:
        return None
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id FROM reader_tokens "
            "WHERE token_sha256 = ? AND revoked_at IS NULL",
            (_sha(password),),
        ).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


_KIND_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("chaoss", ("/chaoss", "/metrics/chaoss")),
    ("sparql", ("/databases/sparql",)),
    ("cypher", ("/databases/cypher",)),
    ("opensearch", ("/databases/opensearch",)),
    ("duckdb", ("/databases/duckdb",)),
    ("agent", ("/api/ai", "/agent")),
    ("search", ("/api/hub/search", "/search")),
    ("entity", ("/api/hub/resolve", "/hub/")),
    ("hub", ("/hub", "/api/hub")),
)


def kind_for(path: str) -> str:
    p = (path or "").lower()
    for kind, needles in _KIND_RULES:
        if any(n in p for n in needles):
            return kind
    return "other"


def log_access(token_id: int, method: str, path: str) -> None:
    """Record one call by a token. Never raises — logging must not break a
    request."""
    try:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO token_access(token_id, method, kind, path) "
                "VALUES (?, ?, ?, ?)",
                (token_id, method, kind_for(path), (path or "")[:2000]),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


# ── reads (admin panel) ────────────────────────────────────────────────────
def list_tokens() -> list[dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT t.id, t.label, t.created_at, t.revoked_at, t.scope_json,
                   (SELECT count(*) FROM token_access a WHERE a.token_id = t.id) AS calls,
                   (SELECT max(ts) FROM token_access a WHERE a.token_id = t.id) AS last_seen
            FROM reader_tokens t
            ORDER BY (t.revoked_at IS NOT NULL), t.id DESC
            """
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # [] means full access (no ceiling); a list = the graph allow-list.
            d["scoped_graphs"] = _graphs_of(_scope_from_json(d.pop("scope_json", None)))
            out.append(d)
        return out
    finally:
        conn.close()


def token_activity(token_id: int, limit: int = 60) -> dict[str, Any]:
    conn = _conn()
    try:
        recent = [
            dict(r)
            for r in conn.execute(
                "SELECT ts, method, kind, path FROM token_access "
                "WHERE token_id = ? ORDER BY id DESC LIMIT ?",
                (token_id, limit),
            ).fetchall()
        ]
        by_kind = [
            dict(r)
            for r in conn.execute(
                "SELECT kind, count(*) AS n FROM token_access "
                "WHERE token_id = ? GROUP BY kind ORDER BY n DESC",
                (token_id,),
            ).fetchall()
        ]
        # Raw console queries this token ran (SPARQL/Cypher/OpenSearch/DuckDB),
        # tagged by the databases router. The table is created lazily on the
        # first console call, so tolerate its absence on a fresh deploy.
        try:
            queries = [
                dict(r)
                for r in conn.execute(
                    "SELECT ran_at, engine, query, row_count, error "
                    "FROM query_history WHERE token_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (token_id, limit),
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            queries = []
        return {"recent": recent, "by_kind": by_kind, "queries": queries}
    finally:
        conn.close()
