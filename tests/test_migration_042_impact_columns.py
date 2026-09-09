"""Tests for migration 042: add impact column to project_dead_ends and mistakes_made.

Closes the /dead-ends 500 bug extension Claude caught in T5 integration testing.
Long-lived DBs were missing the impact column on these two tables (migrations
007 and 012 only covered project_findings and project_unknowns); the schema
file's CREATE TABLE has it for fresh DBs but no ALTER existed for upgrades.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.data.migrations.migrations import migration_042_impact_on_dead_ends_and_mistakes


@pytest.fixture
def legacy_db():
    """A long-lived DB shape: dead_ends and mistakes WITHOUT impact column."""
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE project_dead_ends (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            session_id TEXT,
            approach TEXT NOT NULL,
            why_failed TEXT,
            dead_end_data TEXT,
            created_timestamp REAL NOT NULL
        );
        CREATE TABLE mistakes_made (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            session_id TEXT,
            mistake TEXT NOT NULL,
            why_wrong TEXT,
            mistake_data TEXT,
            created_timestamp REAL NOT NULL
        );
    """)
    conn.commit()
    yield conn
    conn.close()


def test_migration_adds_impact_to_dead_ends(legacy_db):
    cursor = legacy_db.cursor()

    cursor.execute("PRAGMA table_info(project_dead_ends)")
    pre_cols = {row[1] for row in cursor.fetchall()}
    assert "impact" not in pre_cols  # confirms the legacy state

    migration_042_impact_on_dead_ends_and_mistakes(cursor)
    legacy_db.commit()

    cursor.execute("PRAGMA table_info(project_dead_ends)")
    post_cols = {row[1] for row in cursor.fetchall()}
    assert "impact" in post_cols


def test_migration_adds_impact_to_mistakes(legacy_db):
    cursor = legacy_db.cursor()
    migration_042_impact_on_dead_ends_and_mistakes(cursor)
    legacy_db.commit()

    cursor.execute("PRAGMA table_info(mistakes_made)")
    cols = {row[1] for row in cursor.fetchall()}
    assert "impact" in cols


def test_migration_default_value_is_0_5(legacy_db):
    """Default 0.5 matches existing migrations 007 and 012 (impact on findings/unknowns)."""
    cursor = legacy_db.cursor()
    migration_042_impact_on_dead_ends_and_mistakes(cursor)
    cursor.execute(
        "INSERT INTO project_dead_ends (id, approach, dead_end_data, created_timestamp) VALUES (?, ?, ?, ?)",
        ("d1", "tried X", "{}", 1.0),
    )
    legacy_db.commit()

    cursor.execute("SELECT impact FROM project_dead_ends WHERE id = 'd1'")
    impact = cursor.fetchone()[0]
    assert impact == 0.5


def test_migration_is_idempotent(legacy_db):
    """add_column_if_missing handles repeat runs without error."""
    cursor = legacy_db.cursor()
    migration_042_impact_on_dead_ends_and_mistakes(cursor)
    legacy_db.commit()
    # Run twice — should be a no-op the second time
    migration_042_impact_on_dead_ends_and_mistakes(cursor)
    legacy_db.commit()

    cursor.execute("PRAGMA table_info(project_dead_ends)")
    impact_cols = [row for row in cursor.fetchall() if row[1] == "impact"]
    assert len(impact_cols) == 1


def test_migration_tolerates_pre_existing_impact_column():
    """If a fresh DB already had impact column from CREATE TABLE, migration is a no-op."""
    conn = sqlite3.connect(":memory:")
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE project_dead_ends (
                id TEXT PRIMARY KEY,
                approach TEXT NOT NULL,
                impact REAL DEFAULT 0.5,
                created_timestamp REAL NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE mistakes_made (
                id TEXT PRIMARY KEY,
                mistake TEXT NOT NULL,
                impact REAL DEFAULT 0.5,
                created_timestamp REAL NOT NULL
            )
        """)
        conn.commit()
        # Must be a REAL no-op: asserted on the fingerprint, because "did not
        # raise" also describes a migration that added a duplicate column type
        # or re-wrote defaults.
        from tests.schema_shapes import db_fingerprint

        before = db_fingerprint(conn)
        migration_042_impact_on_dead_ends_and_mistakes(cursor)
        conn.commit()
        assert db_fingerprint(conn) == before, "no-op migration changed schema or data"
    finally:
        conn.close()


def test_dead_ends_select_with_impact_works_post_migration(legacy_db):
    """Regression test for the /dead-ends 500: SELECT impact FROM dead_ends must work."""
    cursor = legacy_db.cursor()
    cursor.execute(
        "INSERT INTO project_dead_ends (id, project_id, approach, why_failed, dead_end_data, created_timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("d1", "p1", "tried X", "Y", "{}", 1.0),
    )
    legacy_db.commit()

    # Pre-migration: SELECT impact would raise OperationalError
    with pytest.raises(sqlite3.OperationalError):
        cursor.execute("SELECT impact FROM project_dead_ends WHERE id = 'd1'")

    # Post-migration: SELECT impact returns 0.5 (default)
    migration_042_impact_on_dead_ends_and_mistakes(cursor)
    legacy_db.commit()
    cursor.execute("SELECT impact FROM project_dead_ends WHERE id = 'd1'")
    assert cursor.fetchone()[0] == 0.5
