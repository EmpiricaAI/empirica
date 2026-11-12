"""An assumption can be closed as expired: nobody checked it before its subject went away (goal 148d3199, David 2026-11-12).

verified and falsified were the only closes, so an assumption made moot (its code path removed, its window passed) stayed
open or was mislabelled. It is not a dead end: a dead end records an approach tried and failed and resurfaces by design.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from empirica.data.migrations.migrations import migration_080_assumption_expired_status

OLD_SQL = """CREATE TABLE assumptions (
        id TEXT PRIMARY KEY,
        assumption TEXT NOT NULL,
        confidence REAL DEFAULT 0.5 CHECK(confidence BETWEEN 0.0 AND 1.0),
        status TEXT NOT NULL DEFAULT 'unverified' CHECK(status IN (
            'unverified', 'verified', 'falsified'
        )),
        project_id TEXT,
        created_timestamp REAL NOT NULL,
        resolved_timestamp REAL, retrieval_count INTEGER DEFAULT 0, retrieval_count_at_resolution INTEGER DEFAULT NULL, due_at TEXT DEFAULT NULL
    )"""


@pytest.fixture
def old_db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "s.db"))
    conn.execute(OLD_SQL)
    conn.execute("CREATE INDEX idx_assumptions_status ON assumptions(status)")
    conn.execute(
        """CREATE TRIGGER trg_assumptions_retrieval_snapshot AFTER UPDATE ON assumptions FOR EACH ROW
        WHEN NEW.resolved_timestamp IS NOT NULL AND OLD.resolved_timestamp IS NULL AND NEW.retrieval_count_at_resolution IS NULL
        BEGIN UPDATE assumptions SET retrieval_count_at_resolution = COALESCE(NEW.retrieval_count, 0) WHERE id = NEW.id; END"""
    )
    conn.execute(
        "INSERT INTO assumptions (id, assumption, status, project_id, created_timestamp, retrieval_count, due_at) "
        "VALUES ('a1', 'x', 'unverified', 'p', 1, 4, '2026-12-01')"
    )
    conn.commit()
    yield conn
    conn.close()


def test_an_old_table_refuses_expired_until_the_migration_widens_it(old_db):
    with pytest.raises(sqlite3.IntegrityError):
        old_db.execute("UPDATE assumptions SET status = 'expired' WHERE id = 'a1'")
    old_db.rollback()

    migration_080_assumption_expired_status(old_db.cursor())
    old_db.commit()

    old_db.execute("UPDATE assumptions SET status = 'expired', resolved_timestamp = ? WHERE id = 'a1'", (time.time(),))
    row = old_db.execute(
        "SELECT status, due_at, retrieval_count_at_resolution FROM assumptions WHERE id = 'a1'"
    ).fetchone()
    assert row == ("expired", "2026-12-01", 4)  # columns kept, and the trigger survived the rebuild
    names = {r[0] for r in old_db.execute("SELECT name FROM sqlite_master WHERE tbl_name = 'assumptions'")}
    assert {"idx_assumptions_status", "trg_assumptions_retrieval_snapshot"} <= names
    with pytest.raises(sqlite3.IntegrityError):
        old_db.execute("UPDATE assumptions SET status = 'maybe' WHERE id = 'a1'")


def test_the_migration_is_a_no_op_on_a_table_that_already_allows_expired(old_db):
    migration_080_assumption_expired_status(old_db.cursor())
    old_db.commit()
    sql = old_db.execute("SELECT sql FROM sqlite_master WHERE name = 'assumptions'").fetchone()[0]
    migration_080_assumption_expired_status(old_db.cursor())
    assert old_db.execute("SELECT sql FROM sqlite_master WHERE name = 'assumptions'").fetchone()[0] == sql


def test_a_fresh_database_allows_expired(tmp_path):
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase(db_path=str(tmp_path / "fresh.db"))
    try:
        db.conn.execute(
            "INSERT INTO assumptions (id, assumption, status, created_timestamp) VALUES ('a', 'x', 'expired', 1)"
        )
    finally:
        db.close()


def test_resolve_artifacts_closes_an_assumption_as_expired(tmp_path, monkeypatch, capsys):
    import io
    import json
    import types

    from empirica.cli.command_handlers.graph_commands import handle_resolve_artifacts_command
    from empirica.data.session_database import SessionDatabase

    db_path = tmp_path / ".empirica" / "sessions" / "sessions.db"
    db_path.parent.mkdir(parents=True)
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(db_path))
    monkeypatch.chdir(tmp_path)
    db = SessionDatabase(db_path=str(db_path))
    try:
        db.conn.execute(
            "INSERT INTO assumptions (id, assumption, status, created_timestamp) VALUES ('a-moot', 'x', 'unverified', 1)"
        )
        db.conn.commit()
    finally:
        db.close()
    payload = {
        "resolutions": [
            {"type": "assumption", "id": "a-moot", "resolution": "its code path was removed", "status": "expired"}
        ]
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    handle_resolve_artifacts_command(types.SimpleNamespace(config="-", output="json", verbose=False))
    capsys.readouterr()
    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("SELECT status FROM assumptions WHERE id = 'a-moot'").fetchone()[0] == "expired"
    finally:
        conn.close()


def test_update_artifacts_accepts_expired():
    from empirica.data.artifact_fields import ASSUMPTION_STATUSES

    assert "expired" in ASSUMPTION_STATUSES
