"""Tests for migration 041: artifact_edges table + backfill from data.edges JSON.

Verifies:
  - Table is created with the right columns + indexes
  - Backfill correctly extracts edges from existing data.edges JSON in tables
    that have a data column (findings, unknowns, dead_ends, mistakes, goals)
  - Idempotent — running migration twice doesn't duplicate rows
  - Tolerates missing tables (fresh DB / partial schema)
  - Tolerates malformed JSON in data columns
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from empirica.data.migrations.migrations import migration_041_artifact_edges


@pytest.fixture
def db():
    """Fresh in-memory DB with the legacy artifact tables (data columns + JSON edges)."""
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    # Subset of artifact tables, just enough columns to hold the JSON data field
    cursor.executescript("""
        CREATE TABLE project_findings (
            id TEXT PRIMARY KEY,
            finding_data TEXT
        );
        CREATE TABLE project_unknowns (
            id TEXT PRIMARY KEY,
            unknown_data TEXT
        );
        CREATE TABLE project_dead_ends (
            id TEXT PRIMARY KEY,
            dead_end_data TEXT
        );
        CREATE TABLE mistakes_made (
            id TEXT PRIMARY KEY,
            mistake_data TEXT
        );
        CREATE TABLE goals (
            id TEXT PRIMARY KEY,
            goal_data TEXT
        );
    """)
    conn.commit()
    yield conn
    conn.close()


def test_migration_creates_artifact_edges_table(db):
    cursor = db.cursor()
    migration_041_artifact_edges(cursor)
    db.commit()

    cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='artifact_edges'")
    row = cursor.fetchone()
    assert row is not None, "artifact_edges table should be created"
    sql = row[0]
    assert "from_id" in sql
    assert "to_id" in sql
    assert "relation" in sql
    assert "metadata" in sql


def test_migration_creates_inverse_index(db):
    cursor = db.cursor()
    migration_041_artifact_edges(cursor)
    db.commit()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='artifact_edges'")
    indexes = {row[0] for row in cursor.fetchall()}
    assert "idx_artifact_edges_to" in indexes, "must have (to_id, relation) index for inverse queries"
    assert "idx_artifact_edges_from" in indexes


def test_migration_backfills_edges_from_finding_data(db):
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO project_findings (id, finding_data) VALUES (?, ?)",
        ("f1", json.dumps({"edges": [
            {"to": "d1", "relation": "evidence"},
            {"to": "s1", "relation": "sourced_from"},
        ]})),
    )
    db.commit()

    migration_041_artifact_edges(cursor)
    db.commit()

    cursor.execute("SELECT from_id, to_id, relation FROM artifact_edges ORDER BY to_id")
    rows = cursor.fetchall()
    assert rows == [("f1", "d1", "evidence"), ("f1", "s1", "sourced_from")]


def test_migration_backfills_across_all_tables_with_data_columns(db):
    cursor = db.cursor()
    samples = [
        ("project_findings", "finding_data", "f1"),
        ("project_unknowns", "unknown_data", "u1"),
        ("project_dead_ends", "dead_end_data", "de1"),
        ("mistakes_made", "mistake_data", "m1"),
        ("goals", "goal_data", "g1"),
    ]
    for table, col, art_id in samples:
        cursor.execute(
            f"INSERT INTO {table} (id, {col}) VALUES (?, ?)",
            (art_id, json.dumps({"edges": [{"to": "shared-target", "relation": "related"}]})),
        )
    db.commit()

    migration_041_artifact_edges(cursor)
    db.commit()

    cursor.execute("SELECT COUNT(*) FROM artifact_edges WHERE to_id = 'shared-target'")
    count = cursor.fetchone()[0]
    assert count == 5, f"expected 5 backfilled edges (one per table), got {count}"


def test_migration_skips_rows_without_edges_field(db):
    """Rows where data is non-null but data.edges is empty/missing should be skipped silently."""
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO project_findings (id, finding_data) VALUES (?, ?)",
        ("f-no-edges", json.dumps({"impact": 0.5})),  # no edges key
    )
    cursor.execute(
        "INSERT INTO project_findings (id, finding_data) VALUES (?, ?)",
        ("f-empty-edges", json.dumps({"edges": []})),
    )
    db.commit()

    migration_041_artifact_edges(cursor)
    db.commit()

    cursor.execute("SELECT COUNT(*) FROM artifact_edges")
    assert cursor.fetchone()[0] == 0


def test_migration_tolerates_malformed_json(db):
    """Rows with garbage in data column shouldn't crash the migration."""
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO project_findings (id, finding_data) VALUES (?, ?)",
        ("f-bad-json", "not valid json {{{"),
    )
    cursor.execute(
        "INSERT INTO project_findings (id, finding_data) VALUES (?, ?)",
        ("f-good", json.dumps({"edges": [{"to": "x", "relation": "r"}]})),
    )
    db.commit()

    migration_041_artifact_edges(cursor)
    db.commit()

    # Good row backfilled, bad row skipped silently
    cursor.execute("SELECT from_id FROM artifact_edges")
    assert [row[0] for row in cursor.fetchall()] == ["f-good"]


def test_migration_skips_edge_entries_missing_to_or_relation(db):
    """Edge entries lacking 'to' or 'relation' should be skipped, not errored."""
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO project_findings (id, finding_data) VALUES (?, ?)",
        ("f1", json.dumps({"edges": [
            {"to": "d1", "relation": "evidence"},      # valid
            {"to": "d2"},                                # missing relation
            {"relation": "evidence"},                    # missing to
            {},                                          # both missing
        ]})),
    )
    db.commit()

    migration_041_artifact_edges(cursor)
    db.commit()

    cursor.execute("SELECT to_id FROM artifact_edges")
    assert [row[0] for row in cursor.fetchall()] == ["d1"]


def test_migration_is_idempotent(db):
    """Running twice produces the same result — INSERT OR IGNORE on PK handles it."""
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO project_findings (id, finding_data) VALUES (?, ?)",
        ("f1", json.dumps({"edges": [{"to": "d1", "relation": "evidence"}]})),
    )
    db.commit()

    migration_041_artifact_edges(cursor)
    db.commit()
    cursor.execute("SELECT COUNT(*) FROM artifact_edges")
    first_count = cursor.fetchone()[0]

    migration_041_artifact_edges(cursor)
    db.commit()
    cursor.execute("SELECT COUNT(*) FROM artifact_edges")
    second_count = cursor.fetchone()[0]

    assert first_count == second_count == 1


def test_migration_tolerates_missing_artifact_tables():
    """Fresh DB without the artifact tables shouldn't crash — just no backfill."""
    conn = sqlite3.connect(":memory:")
    try:
        cursor = conn.cursor()
        # Don't create any artifact tables — simulate a partial schema
        migration_041_artifact_edges(cursor)
        conn.commit()

        # artifact_edges table should still be created
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='artifact_edges'")
        assert cursor.fetchone() is not None
        # No backfill happened
        cursor.execute("SELECT COUNT(*) FROM artifact_edges")
        assert cursor.fetchone()[0] == 0
    finally:
        conn.close()


def test_migration_pk_prevents_duplicate_edges(db):
    """The (from_id, to_id, relation) PRIMARY KEY blocks duplicates."""
    cursor = db.cursor()
    migration_041_artifact_edges(cursor)
    db.commit()

    cursor.execute(
        "INSERT INTO artifact_edges (from_id, to_id, relation) VALUES (?, ?, ?)",
        ("a", "b", "rel"),
    )
    db.commit()

    # Re-insert same triple → should be silently OK (or raise, depending on caller)
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute(
            "INSERT INTO artifact_edges (from_id, to_id, relation) VALUES (?, ?, ?)",
            ("a", "b", "rel"),
        )

    # But INSERT OR IGNORE works
    cursor.execute(
        "INSERT OR IGNORE INTO artifact_edges (from_id, to_id, relation) VALUES (?, ?, ?)",
        ("a", "b", "rel"),
    )
    db.commit()
    cursor.execute("SELECT COUNT(*) FROM artifact_edges")
    assert cursor.fetchone()[0] == 1
