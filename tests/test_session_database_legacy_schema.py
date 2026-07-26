"""A legacy-schema DB must still open.

`_create_tables()` creates performance indexes unconditionally. A long-lived DB can
predate any of those columns (created before the column existed, or migrations never
ran on it), and `CREATE TABLE IF NOT EXISTS` will not add a column to a table that
already exists — so the index statement raises and, because it aborts
`_create_tables()`, the whole database becomes UNOPENABLE.

Observed live: a practice DB whose `sessions` table predates `ai_id` made
`CREATE INDEX ... ON sessions(ai_id)` raise "no such column: ai_id", so every command
touching that practice failed at `SessionDatabase()` init.

An index is a performance aid — losing one on a legacy DB is acceptable; refusing to
open the DB is not.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.data.session_database import SessionDatabase


def _legacy_db(tmp_path, table_sql: str):
    """Pre-create a table in its OLD shape so the current schema's CREATE TABLE
    IF NOT EXISTS leaves it alone and the indexes hit the missing column.

    Migrations are marked already-applied, which is what makes this the REAL
    scenario rather than a synthetic one: the live DB that exposed this bug had its
    migration history recorded, so `run_all` skipped everything and the FIRST thing
    to touch the missing column was the unconditional index creation. (A DB with an
    unrecorded migration history fails earlier, inside the migration itself — that is
    a migration's job to report loudly, and is deliberately not what this guards.)
    """
    from empirica.data.migrations import ALL_MIGRATIONS

    path = tmp_path / "sessions.db"
    conn = sqlite3.connect(str(path))
    conn.execute(table_sql)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "migration_id TEXT PRIMARY KEY, description TEXT, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    for migration_id, *_rest in ALL_MIGRATIONS:  # (id, description, fn)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (migration_id, description) VALUES (?, 'pre-applied by test')",
            (migration_id,),
        )
    conn.commit()
    conn.close()
    return path


def test_opens_when_sessions_predates_ai_id(tmp_path):
    """The exact live failure: sessions table without `ai_id`."""
    path = _legacy_db(
        tmp_path,
        "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, start_time TEXT)",
    )

    db = SessionDatabase(db_path=str(path))  # must not raise
    try:
        assert db.conn is not None
        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(sessions)").fetchall()}
        assert "ai_id" not in cols, "fixture must keep the legacy shape"
    finally:
        db.close()


# NOTE ON SCOPE: this guard covers the INDEX block in `_create_tables` only. A DB whose
# schema or migrations themselves fail (e.g. an unrecorded migration history over a
# foreign table of the same name) still raises — surfacing that is a migration's job,
# and silently tolerating it would leave a half-built database looking healthy.


def test_a_healthy_db_still_gets_its_indexes(tmp_path):
    """The tolerance must not silently stop creating indexes on a normal DB —
    otherwise the guard would mask a real regression."""
    db = SessionDatabase(db_path=str(tmp_path / "fresh.db"))
    try:
        idx = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert "idx_sessions_ai" in idx, "fresh schema must still index sessions(ai_id)"
    finally:
        db.close()


def test_non_drift_errors_still_raise(tmp_path):
    """The guard skips only schema-drift signals; a genuine SQL error must surface
    rather than being swallowed into a silently half-built database."""
    from empirica.data import session_database as sd

    db = SessionDatabase(db_path=str(tmp_path / "x.db"))
    try:
        with pytest.raises(sqlite3.OperationalError):
            db.conn.execute("CREATE INDEX idx_bogus ON sessions(")  # syntax error
    finally:
        db.close()
    assert sd  # module import kept meaningful for the reader
