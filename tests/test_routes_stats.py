"""Tests for ``open_pulse.gui.hub.routes.stats``.

The hub route module touches FastAPI / docker / httpx — heavyweight
to spin up. These tests focus on the parts that are *not* part of
the request hot path:

- the SQLite schema migration that adds per-backend columns to
  ``metrics_history`` without losing pre-existing rows;
- the persist + read round-trip — proving the INSERT column list
  and the SELECT column list line up after the schema bump (a
  classic source of silent breakage when a new column is added).

A regression here would either lose old samples on hub upgrade or
silently drop one of the new chart series.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch


def _make_legacy_db(path: Path) -> None:
    """Reproduce the pre-PR metrics_history schema, with one row of data
    so we can assert the migration preserves it."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE metrics_history (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                ts                  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                services_total      INTEGER,
                services_running    INTEGER,
                services_healthy    INTEGER,
                uptime_max_seconds  INTEGER,
                sparql_repos        INTEGER,
                neo4j_nodes         INTEGER,
                neo4j_rels          INTEGER
            );
            """
        )
        conn.execute(
            "INSERT INTO metrics_history("
            "  services_total, services_running, services_healthy,"
            "  uptime_max_seconds, sparql_repos, neo4j_nodes, neo4j_rels"
            ") VALUES (18, 16, 15, 905000, 631, 228785, 325586)"
        )
        conn.commit()
    finally:
        conn.close()


def test_history_schema_adds_missing_columns_idempotently(tmp_path: Path) -> None:
    """``_history_db`` upgrades a legacy DB without dropping existing rows.

    Boots a pre-PR shape, calls the migration twice (second pass must be
    a no-op), and asserts the pre-existing row survives with the new
    columns NULL.
    """
    from open_pulse.gui.hub.config import Settings

    db_path = tmp_path / "app.db"
    _make_legacy_db(db_path)

    fake_settings = Settings.__new__(Settings)
    object.__setattr__(fake_settings, "data_dir", tmp_path)

    with patch("open_pulse.gui.hub.routes.stats.get_settings", return_value=fake_settings):
        from open_pulse.gui.hub.routes import stats as stats_module

        # First call migrates.
        conn = stats_module._history_db()
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(metrics_history)")}
        finally:
            conn.close()

        for new_col in (
            "neo4j_repos",
            "neo4j_users",
            "neo4j_orgs",
            "sparql_users",
            "sparql_orgs",
            "opensearch_repos",
            "opensearch_users",
            "opensearch_orgs",
            "duckdb_github_repos",
            "duckdb_zenodo_records",
            "duckdb_huggingface_items",
        ):
            assert new_col in cols, f"{new_col} not added"

        # Second call is a no-op (no ALTER TABLE re-runs).
        conn = stats_module._history_db()
        try:
            n_cols = len(list(conn.execute("PRAGMA table_info(metrics_history)")))
        finally:
            conn.close()
        assert n_cols == len(cols), "second migration call changed the schema"

        # Pre-migration row survived, with new columns NULL.
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT services_total, sparql_repos, neo4j_repos, opensearch_users,"
                "       duckdb_github_repos FROM metrics_history"
            ).fetchone()
        finally:
            conn.close()
        assert row == (18, 631, None, None, None)


def test_persist_and_history_roundtrip(tmp_path: Path) -> None:
    """A persisted sample round-trips through ``history`` with every new
    column intact. Catches the classic INSERT-vs-SELECT column-list
    mismatch when adding columns later.
    """
    from open_pulse.gui.hub.config import Settings

    fake_settings = Settings.__new__(Settings)
    object.__setattr__(fake_settings, "data_dir", tmp_path)

    sample: dict[str, Any] = {
        "services": {
            "total": 18,
            "running": 16,
            "healthy": 15,
            "uptime_max_seconds": 905000,
        },
        "sparql": {"repos": 631, "users": 1136, "orgs": 264},
        "neo4j": {
            "nodes": 228785,
            "rels": 325586,
            "repos": 199117,
            "users": 28598,
            "orgs": 1070,
        },
        "opensearch": {"repos": 193, "users": 1829, "orgs": 8},
        "duckdb": {
            "github_repos": 7099,
            "zenodo_records": 1638,
            "huggingface_items": 1428,
        },
    }

    with patch("open_pulse.gui.hub.routes.stats.get_settings", return_value=fake_settings):
        from open_pulse.gui.hub.routes import stats as stats_module

        stats_module._persist_sample(sample)
        # ``history()`` is a FastAPI handler; call it directly with
        # defaults — it returns the same shape the chart consumes.
        payload = stats_module.history(range_="6h")

    assert payload["count"] == 1
    row = payload["samples"][0]
    # Every column we just inserted must come back out.
    expected = {
        "services_total": 18,
        "services_running": 16,
        "services_healthy": 15,
        "uptime_max_seconds": 905000,
        "sparql_repos": 631,
        "sparql_users": 1136,
        "sparql_orgs": 264,
        "neo4j_nodes": 228785,
        "neo4j_rels": 325586,
        "neo4j_repos": 199117,
        "neo4j_users": 28598,
        "neo4j_orgs": 1070,
        "opensearch_repos": 193,
        "opensearch_users": 1829,
        "opensearch_orgs": 8,
        "duckdb_github_repos": 7099,
        "duckdb_zenodo_records": 1638,
        "duckdb_huggingface_items": 1428,
    }
    for k, v in expected.items():
        assert row[k] == v, f"{k}: expected {v}, got {row[k]!r}"


def test_persist_tolerates_partial_sample(tmp_path: Path) -> None:
    """Backends that fail individually leave their column NULL — the
    sampler keeps going. Crucial because a transient OpenSearch outage
    must not drop the Neo4j / SPARQL / DuckDB columns for that minute."""
    from open_pulse.gui.hub.config import Settings

    fake_settings = Settings.__new__(Settings)
    object.__setattr__(fake_settings, "data_dir", tmp_path)

    partial: dict[str, Any] = {
        "services": {"total": 18, "running": 16, "healthy": 15, "uptime_max_seconds": 0},
        # opensearch + duckdb missing entirely (e.g. services down).
        "sparql": {"repos": 100, "users": None, "orgs": None},
        "neo4j": {
            "nodes": 1000,
            "rels": 5000,
            "repos": 800,
            "users": 100,
            "orgs": 5,
        },
    }

    with patch("open_pulse.gui.hub.routes.stats.get_settings", return_value=fake_settings):
        from open_pulse.gui.hub.routes import stats as stats_module

        stats_module._persist_sample(partial)
        payload = stats_module.history(range_="6h")

    row = payload["samples"][0]
    assert row["neo4j_repos"] == 800
    assert row["sparql_repos"] == 100
    assert row["sparql_users"] is None
    assert row["opensearch_repos"] is None
    assert row["duckdb_github_repos"] is None
