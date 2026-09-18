"""Migration 072 drops empty legacy tables and cannot lose a row.

David's ruling 2026-09-18: drop the nine tables current code neither creates
nor reads, each only when empty; leave `clients` and `engagements`.
"""

from __future__ import annotations

import sqlite3

from empirica.data.migrations.migrations import (
    LEGACY_TABLES_072,
    migration_072_drop_empty_legacy_tables,
)


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _store(tmp_path):
    conn = sqlite3.connect(tmp_path / "s.db")
    for t in LEGACY_TABLES_072:
        conn.execute(f"CREATE TABLE {t} (id TEXT)")
    conn.execute("CREATE TABLE clients (id TEXT)")
    conn.execute("INSERT INTO clients VALUES ('c1')")
    conn.execute("CREATE TABLE engagements (id TEXT)")
    # one legacy table unexpectedly holds a row on this store
    conn.execute("INSERT INTO client_findings VALUES ('keep-me')")
    conn.commit()
    return conn


def test_empty_legacy_tables_go_and_populated_ones_stay(tmp_path, caplog):
    conn = _store(tmp_path)
    migration_072_drop_empty_legacy_tables(conn.cursor())
    left = _tables(conn)

    assert set(LEGACY_TABLES_072) - {"client_findings"} <= set(LEGACY_TABLES_072) - left
    assert "client_findings" in left, "a legacy table with a row must never be dropped"
    assert conn.execute("SELECT id FROM client_findings").fetchall() == [("keep-me",)]
    assert {"clients", "engagements"} <= left
    assert "client_findings(1)" in caplog.text


def test_rerun_and_absent_tables_are_no_ops(tmp_path):
    conn = _store(tmp_path)
    migration_072_drop_empty_legacy_tables(conn.cursor())
    before = _tables(conn)
    migration_072_drop_empty_legacy_tables(conn.cursor())
    assert _tables(conn) == before

    fresh = sqlite3.connect(tmp_path / "fresh.db")
    migration_072_drop_empty_legacy_tables(fresh.cursor())  # none exist: must not raise
    assert _tables(fresh) == set()
