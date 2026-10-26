"""Migration 077 drops the two codebase tables nothing writes, only while they are empty (goal bc3375d6, David's dead-code ruling 2026-10-26).

`codebase_facts` and `codebase_constraints` (and the FTS5 index and three triggers migration 033 hung on the first) had no writer in
production, held 0 rows on core, and lost their last readers in 456e64a94. The drop is guarded per table by a row count: a database
that somehow does hold rows keeps them, and says so, rather than losing data to a cleanup.
"""

from __future__ import annotations

import sqlite3

import pytest

LEGACY_FACTS = """
CREATE TABLE codebase_facts (
    id TEXT PRIMARY KEY, fact_text TEXT NOT NULL, valid_at REAL NOT NULL, invalid_at REAL,
    status TEXT NOT NULL DEFAULT 'canonical', entity_ids TEXT, evidence_type TEXT, evidence_path TEXT,
    confidence REAL DEFAULT 1.0, project_id TEXT, session_id TEXT
)
"""
LEGACY_CONSTRAINTS = """
CREATE TABLE codebase_constraints (
    id TEXT PRIMARY KEY, constraint_type TEXT NOT NULL, rule_name TEXT NOT NULL, file_pattern TEXT, description TEXT,
    violation_count INTEGER DEFAULT 0, last_violated REAL, examples TEXT, severity TEXT DEFAULT 'warning',
    project_id TEXT, session_id TEXT, created_at REAL NOT NULL
)
"""


def run_077(cursor):
    from empirica.data.migrations.migrations import migration_077_drop_empty_codebase_fact_tables

    migration_077_drop_empty_codebase_fact_tables(cursor)


def _names(conn, kind):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))}


def _legacy(conn, *, with_fts=True):
    conn.execute(LEGACY_FACTS)
    conn.execute(LEGACY_CONSTRAINTS)
    conn.execute("CREATE INDEX idx_codebase_facts_status ON codebase_facts(status)")
    conn.execute("CREATE INDEX idx_codebase_constraints_rule ON codebase_constraints(rule_name)")
    if with_fts:
        conn.execute(
            "CREATE VIRTUAL TABLE codebase_facts_fts USING fts5(fact_text, content='codebase_facts', content_rowid='rowid')"
        )
        conn.execute(
            "CREATE TRIGGER codebase_facts_ai AFTER INSERT ON codebase_facts BEGIN "
            "INSERT INTO codebase_facts_fts(rowid, fact_text) VALUES (new.rowid, new.fact_text); END"
        )


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


def test_empty_legacy_tables_the_fts_index_and_triggers_are_all_dropped(conn):
    _legacy(conn)
    run_077(conn.cursor())
    tables = _names(conn, "table")
    assert not {"codebase_facts", "codebase_constraints"} & tables
    assert not [t for t in tables if t.startswith("codebase_facts_fts")], "the FTS5 table and its shadow tables go too"
    assert not [t for t in _names(conn, "trigger") if t.startswith("codebase_facts")]
    assert not [i for i in _names(conn, "index") if i.startswith(("idx_codebase_facts", "idx_codebase_constraints"))]


def test_a_table_that_holds_rows_is_kept_with_its_index_and_triggers(conn):
    _legacy(conn)
    conn.execute("INSERT INTO codebase_facts (id, fact_text, valid_at) VALUES ('f1', 'a fact', 1.0)")
    run_077(conn.cursor())
    tables = _names(conn, "table")
    assert "codebase_facts" in tables and "codebase_facts_fts" in tables
    assert "codebase_facts_ai" in _names(conn, "trigger")
    assert conn.execute("SELECT fact_text FROM codebase_facts").fetchone()[0] == "a fact"


def test_the_two_tables_are_judged_independently(conn):
    _legacy(conn)
    conn.execute("INSERT INTO codebase_facts (id, fact_text, valid_at) VALUES ('f1', 'a fact', 1.0)")
    run_077(conn.cursor())
    assert "codebase_constraints" not in _names(conn, "table"), "the empty one is dropped"
    assert "codebase_facts" in _names(conn, "table"), "the populated one is kept"


def test_it_is_idempotent_and_survives_a_database_that_never_had_the_tables(conn):
    run_077(conn.cursor())  # nothing to drop
    _legacy(conn)
    run_077(conn.cursor())
    run_077(conn.cursor())  # second run: already gone
    assert not {"codebase_facts", "codebase_constraints"} & _names(conn, "table")


def test_a_legacy_database_without_the_fts_index_still_drops_cleanly(conn):
    _legacy(conn, with_fts=False)
    run_077(conn.cursor())
    assert not {"codebase_facts", "codebase_constraints"} & _names(conn, "table")


def test_a_fresh_database_never_creates_the_tables(tmp_path):
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase(db_path=str(tmp_path / "s.db"))
    try:
        names = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master")}
    finally:
        db.close()
    assert "codebase_entities" in names and "codebase_relationships" in names, (
        "control: the live codebase-model tables stay"
    )
    assert not [n for n in names if n.startswith(("codebase_facts", "codebase_constraints"))]


def test_migration_033_does_not_fail_on_a_database_without_the_facts_table(conn):
    """The fresh-install path: the schema no longer creates codebase_facts, so 033 must not hang a trigger on it."""
    from empirica.data.migrations.migrations import migration_033_codebase_model

    migration_033_codebase_model(conn.cursor())
    assert "codebase_facts_fts" not in _names(conn, "table")


def test_an_fts_index_left_behind_by_a_manual_drop_of_its_table_is_cleaned_too(conn):
    """The FTS drop sat inside the 'table exists' branch, so a database where codebase_facts was already gone kept its FTS5 table forever."""
    _legacy(conn)
    conn.execute("DROP TABLE codebase_facts")  # the trigger goes with it; the content-less FTS5 table survives
    assert "codebase_facts_fts" in _names(conn, "table")
    run_077(conn.cursor())
    assert not [t for t in _names(conn, "table") if t.startswith("codebase_facts_fts")]
    assert "codebase_constraints" not in _names(conn, "table")


def test_the_fts_index_of_a_table_that_holds_rows_is_not_touched_when_the_table_exists(conn):
    """Control for the above: the extra cleanup is only for an ORPHAN index, never for one whose table is kept."""
    _legacy(conn)
    conn.execute("INSERT INTO codebase_facts (id, fact_text, valid_at) VALUES ('f1', 'a fact', 1.0)")
    run_077(conn.cursor())
    assert "codebase_facts_fts" in _names(conn, "table")
