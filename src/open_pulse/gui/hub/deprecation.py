"""Legacy-path deprecation harness for the API v1 migration.

As the hub API moves to ``/api/v1/*`` (see ``docs/reference/api-v1.md``), old
paths keep working through this ASGI middleware. For a matched legacy request
it:

  1. rewrites the request path to its canonical ``/api/v1`` target, so the
     request is served by the new handler (query string preserved);
  2. stamps the response with the standard signals — ``Deprecation: true``,
     ``Sunset: <date>`` (RFC 8594), and ``Link: <new>; rel="successor-version"``;
  3. counts the hit in ``app.db`` so we can watch legacy traffic drain before
     deleting the shim at the sunset date.

Register once in ``main.py`` with ``app.add_middleware(DeprecationMiddleware)``.
Extend :data:`LEGACY_MAP` as each route group moves — only after its canonical
target actually exists.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import load_settings

_SETTINGS = load_settings()

# When the legacy surface is removed. Advertised in the ``Sunset`` header and
# in the design doc; after this date the shims flip to ``410 Gone`` and are
# then deleted.
SUNSET = "2026-09-15"

# Ordered (legacy_prefix, canonical_prefix) pairs. The first prefix that
# matches wins, so list the most specific first. A prefix maps every path
# beneath it, preserving the suffix and query string. Add a mapping only once
# its canonical target exists — until then the legacy route must stay live.
LEGACY_MAP: list[tuple[str, str]] = [
    # CHAOSS: the original alias shape → the canonical versioned surface,
    # which already exists. Folds the hand-written aliases into the harness.
    ("/api/chaoss/v1", "/api/v1/metrics/chaoss"),
]


def _canonical_for(path: str) -> str | None:
    """Return the canonical path for a legacy ``path``, or ``None``."""
    for legacy, canonical in LEGACY_MAP:
        if path == legacy or path.startswith(legacy + "/"):
            return canonical + path[len(legacy) :]
    return None


def _db() -> sqlite3.Connection:
    _SETTINGS.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_SETTINGS.data_dir / "app.db"))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS api_deprecation_hits ("
        "  path       TEXT PRIMARY KEY,"
        "  canonical  TEXT NOT NULL,"
        "  count      INTEGER NOT NULL DEFAULT 0,"
        "  first_seen TEXT NOT NULL DEFAULT (datetime('now')),"
        "  last_seen  TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    return conn


def record_hit(path: str, canonical: str) -> None:
    """Count one hit on a legacy ``path``. Never raises — telemetry must not
    break a request. The write is quick (indexed upsert on a local file); we
    do it inline since legacy traffic is meant to be low and draining."""
    try:
        conn = _db()
        try:
            conn.execute(
                "INSERT INTO api_deprecation_hits(path, canonical, count) "
                "VALUES (?, ?, 1) "
                "ON CONFLICT(path) DO UPDATE SET "
                "  count = count + 1, "
                "  canonical = excluded.canonical, "
                "  last_seen = datetime('now')",
                (path, canonical),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def hits() -> list[dict[str, Any]]:
    """Every legacy path seen, busiest first — for the operator readout."""
    try:
        conn = _db()
        try:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT path, canonical, count, first_seen, last_seen "
                    "FROM api_deprecation_hits "
                    "ORDER BY count DESC, last_seen DESC"
                ).fetchall()
            ]
        finally:
            conn.close()
    except sqlite3.Error:
        return []


class DeprecationMiddleware:
    """Rewrite matched legacy paths to their canonical target and stamp the
    deprecation signals on the response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        legacy_path = scope.get("path", "")
        canonical = _canonical_for(legacy_path)
        if canonical is None:
            await self.app(scope, receive, send)
            return

        record_hit(legacy_path, canonical)
        scope = dict(scope)
        scope["path"] = canonical
        scope["raw_path"] = canonical.encode("utf-8")
        link = f'<{canonical}>; rel="successor-version"'.encode("utf-8")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"deprecation", b"true"))
                headers.append((b"sunset", SUNSET.encode("utf-8")))
                headers.append((b"link", link))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)
