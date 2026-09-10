"""Tests for subagent_sessions table isolation (migration 034) and
post-compact session auto-heal.

Verifies:
- create_subagent_session writes to subagent_sessions, NOT main sessions
- end_subagent_session marks the right row as completed
- list_subagents_for_parent returns children correctly
- ensure_session_exists is idempotent and inserts when missing
- Migration 034 moves legacy subagent rows out of sessions table
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# Add empirica src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from collections.abc import Iterator

from empirica.data.migrations.migrations import migration_034_subagent_sessions
from empirica.data.schema.sessions_schema import SCHEMAS


@pytest.fixture
def fresh_db(tmp_path) -> Iterator[sqlite3.Connection]:
    """An in-memory style scratch DB with the sessions schema applied."""
    db_path = tmp_path / "test_sessions.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    # Apply only the schemas we need
    for schema in SCHEMAS:
        cursor.execute(schema)
    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Schema and migration
# ---------------------------------------------------------------------------


class TestSchema:
    def test_subagent_sessions_table_exists(self, fresh_db):
        cursor = fresh_db.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='subagent_sessions'")
        assert cursor.fetchone() is not None

    def test_subagent_sessions_has_required_columns(self, fresh_db):
        cursor = fresh_db.cursor()
        cursor.execute("PRAGMA table_info(subagent_sessions)")
        columns = {row[1] for row in cursor.fetchall()}
        for col in [
            "session_id",
            "agent_name",
            "parent_session_id",
            "project_id",
            "instance_id",
            "start_time",
            "end_time",
            "status",
            "rollup_summary",
            "created_at",
        ]:
            assert col in columns, f"missing column {col}"


class TestMigration034:
    def test_migration_moves_subagent_rows(self, fresh_db):
        """Pre-migration: stuff a subagent row into main sessions table.
        Post-migration: it should be in subagent_sessions, gone from sessions.
        """
        cursor = fresh_db.cursor()
        # Insert one parent + one subagent in the OLD layout (subagent in sessions)
        cursor.execute("""
            INSERT INTO sessions (session_id, ai_id, start_time, components_loaded, parent_session_id)
            VALUES ('parent-uuid', 'claude-code', '2026-04-07T00:00:00', 0, NULL)
        """)
        cursor.execute("""
            INSERT INTO sessions (session_id, ai_id, start_time, components_loaded, parent_session_id, end_time)
            VALUES ('child-uuid', 'Explore', '2026-04-07T00:01:00', 0, 'parent-uuid', '2026-04-07T00:02:00')
        """)
        fresh_db.commit()

        # Run the migration
        migration_034_subagent_sessions(cursor)
        fresh_db.commit()

        # Subagent row should be gone from sessions
        cursor.execute("SELECT COUNT(*) FROM sessions WHERE parent_session_id IS NOT NULL")
        assert cursor.fetchone()[0] == 0

        # Parent should still be there
        cursor.execute("SELECT COUNT(*) FROM sessions WHERE session_id = 'parent-uuid'")
        assert cursor.fetchone()[0] == 1

        # Subagent should be in subagent_sessions
        cursor.execute("SELECT * FROM subagent_sessions WHERE session_id = 'child-uuid'")
        row = cursor.fetchone()
        assert row is not None
        assert row["agent_name"] == "Explore"
        assert row["parent_session_id"] == "parent-uuid"
        assert row["status"] == "completed"  # had end_time

    def test_migration_marks_orphaned_status_for_no_end_time(self, fresh_db):
        cursor = fresh_db.cursor()
        cursor.execute("""
            INSERT INTO sessions (session_id, ai_id, start_time, components_loaded, parent_session_id)
            VALUES ('parent-2', 'claude-code', '2026-04-07T00:00:00', 0, NULL)
        """)
        cursor.execute("""
            INSERT INTO sessions (session_id, ai_id, start_time, components_loaded, parent_session_id)
            VALUES ('orphan-child', 'general-purpose', '2026-04-07T00:01:00', 0, 'parent-2')
        """)
        fresh_db.commit()

        migration_034_subagent_sessions(cursor)
        fresh_db.commit()

        cursor.execute("SELECT status FROM subagent_sessions WHERE session_id = 'orphan-child'")
        assert cursor.fetchone()["status"] == "orphaned"

    def test_migration_idempotent_on_empty(self, fresh_db):
        """Running migration with no subagent rows should be a no-op."""
        cursor = fresh_db.cursor()
        cursor.execute("""
            INSERT INTO sessions (session_id, ai_id, start_time, components_loaded)
            VALUES ('only-parent', 'claude-code', '2026-04-07T00:00:00', 0)
        """)
        fresh_db.commit()

        migration_034_subagent_sessions(cursor)
        fresh_db.commit()

        cursor.execute("SELECT COUNT(*) FROM sessions")
        assert cursor.fetchone()[0] == 1
        cursor.execute("SELECT COUNT(*) FROM subagent_sessions")
        assert cursor.fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Repository methods (require full SessionDatabase init — use a DB on disk)
# ---------------------------------------------------------------------------


@pytest.fixture
def session_db(tmp_path, monkeypatch):
    """Spin up a real SessionDatabase against a scratch path."""
    from empirica.data.session_database import SessionDatabase

    db_path = tmp_path / ".empirica" / "sessions" / "sessions.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Force the DB to use our scratch path by overriding path resolution
    monkeypatch.setenv("EMPIRICA_SESSION_DB_PATH", str(db_path))

    db = SessionDatabase(db_path=db_path)
    yield db
    db.close()


class TestSubagentRepoMethods:
    def test_create_subagent_session_writes_to_subagent_table(self, session_db):
        # Create a parent session first
        parent_id = session_db.create_session(ai_id="claude-code")

        # Create a subagent child
        child_id = session_db.create_subagent_session(
            agent_name="Explore",
            parent_session_id=parent_id,
        )

        # Should be in subagent_sessions, not in sessions
        cursor = session_db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sessions WHERE session_id = ?", (child_id,))
        assert cursor.fetchone()[0] == 0

        cursor.execute("SELECT * FROM subagent_sessions WHERE session_id = ?", (child_id,))
        row = cursor.fetchone()
        assert row is not None
        assert row["agent_name"] == "Explore"
        assert row["parent_session_id"] == parent_id
        assert row["status"] == "active"

    def test_end_subagent_session_marks_completed(self, session_db):
        parent_id = session_db.create_session(ai_id="claude-code")
        child_id = session_db.create_subagent_session(
            agent_name="general-purpose",
            parent_session_id=parent_id,
        )

        session_db.end_subagent_session(child_id, rollup_summary='{"findings": 3}')

        retrieved = session_db.get_subagent_session(child_id)
        assert retrieved is not None
        assert retrieved["status"] == "completed"
        assert retrieved["end_time"] is not None
        assert retrieved["rollup_summary"] == '{"findings": 3}'

    def test_list_subagents_for_parent(self, session_db):
        parent_id = session_db.create_session(ai_id="claude-code")
        a = session_db.create_subagent_session("Explore", parent_id)
        b = session_db.create_subagent_session("general-purpose", parent_id)
        c = session_db.create_subagent_session("superpowers:code-reviewer", parent_id)

        children = session_db.list_subagents_for_parent(parent_id)
        assert len(children) == 3
        ids = {ch["session_id"] for ch in children}
        assert ids == {a, b, c}

    def test_list_subagents_filtered_by_status(self, session_db):
        parent_id = session_db.create_session(ai_id="claude-code")
        active = session_db.create_subagent_session("Explore", parent_id)
        completed = session_db.create_subagent_session("general-purpose", parent_id)
        session_db.end_subagent_session(completed)

        active_children = session_db.list_subagents_for_parent(parent_id, status="active")
        assert len(active_children) == 1
        assert active_children[0]["session_id"] == active

        completed_children = session_db.list_subagents_for_parent(parent_id, status="completed")
        assert len(completed_children) == 1
        assert completed_children[0]["session_id"] == completed

    def test_subagents_dont_pollute_main_sessions_count(self, session_db):
        """Original bug: 5 recent subagents masked the actual parent in
        diagnostics. After fix, sessions table should only have parents.
        """
        parent_id = session_db.create_session(ai_id="claude-code")
        for agent in ["Explore", "general-purpose", "superpowers:code-reviewer"]:
            session_db.create_subagent_session(agent, parent_id)

        cursor = session_db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sessions")
        assert cursor.fetchone()[0] == 1  # only the parent

        cursor.execute("SELECT COUNT(*) FROM subagent_sessions")
        assert cursor.fetchone()[0] == 3


# ---------------------------------------------------------------------------
# Auto-heal: ensure_session_exists
# ---------------------------------------------------------------------------


class TestEnsureSessionExists:
    def test_inserts_when_missing(self, session_db):
        session_id = "missing-session-uuid-1234"
        cursor = session_db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sessions WHERE session_id = ?", (session_id,))
        assert cursor.fetchone()[0] == 0

        result = session_db.ensure_session_exists(session_id, ai_id="claude-code")
        assert result is True

        cursor.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        assert row is not None
        assert row["ai_id"] == "claude-code"
        assert row["session_notes"] == "auto-healed (cross-project session reuse)"

    def test_idempotent_when_exists(self, session_db):
        session_id = session_db.create_session(ai_id="claude-code")

        # Calling ensure_session_exists on an existing session should be a no-op
        result = session_db.ensure_session_exists(session_id)
        assert result is False

        # Should not have created a duplicate
        cursor = session_db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sessions WHERE session_id = ?", (session_id,))
        assert cursor.fetchone()[0] == 1

    def test_preserves_caller_provided_session_id(self, session_db):
        """The whole point of ensure_session_exists is to preserve the
        existing session_id (so transaction continuity holds across compact)."""
        session_id = "preserved-id-from-pre-compact"
        session_db.ensure_session_exists(session_id)

        # Subsequent code that queries by this ID should find it
        retrieved = session_db.sessions.get_session(session_id)
        assert retrieved is not None
        assert retrieved["session_id"] == session_id


# ---------------------------------------------------------------------------
# Validate-and-heal: heal_session_project_id (ghost-project_id pattern)
# ---------------------------------------------------------------------------


class TestHealSessionProjectId:
    """Project_id-grain extension of 11.24's session_id heal.

    Catches the case where a session row exists but its project_id is
    stale (cross-project --resume, ambiguous folder_name match, tmux
    pane reuse without project-switch). Caller resolves expected
    project_id from workspace.db trajectory_path lookup at session
    boundaries (cwd reliable).
    """

    def _seed_session_with_project_id(self, db, project_id):
        """Insert a session row with a specific project_id directly.

        create_session() doesn't persist project_id to the sessions row
        (only to the global registry) — production callers set project_id
        via later updates. For these heal tests we need the row to start
        with a known project_id so we can verify the heal mutation.
        """
        session_id = db.create_session(ai_id="claude-code")
        cursor = db.conn.cursor()
        cursor.execute(
            "UPDATE sessions SET project_id = ? WHERE session_id = ?",
            (project_id, session_id),
        )
        db.conn.commit()
        return session_id

    def test_heals_when_project_id_mismatch(self, session_db):
        # Session exists, bound to wrong project_id (ghost)
        session_id = self._seed_session_with_project_id(session_db, "ghost-pid-xxx")

        # Heal to expected
        result = session_db.heal_session_project_id(
            session_id=session_id,
            expected_project_id="real-pid-yyy",
        )
        assert result == "healed"

        # Row's project_id is now correct
        cursor = session_db.conn.cursor()
        cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
        assert cursor.fetchone()[0] == "real-pid-yyy"

    def test_no_op_when_already_correct(self, session_db):
        session_id = self._seed_session_with_project_id(session_db, "real-pid-yyy")

        result = session_db.heal_session_project_id(
            session_id=session_id,
            expected_project_id="real-pid-yyy",
        )
        assert result == "ok"

    def test_returns_missing_when_session_absent(self, session_db):
        # Caller is responsible for ensure_session_exists first; this
        # method must not silently insert (different concern).
        result = session_db.heal_session_project_id(
            session_id="never-created-uuid",
            expected_project_id="any-pid",
        )
        assert result == "missing"

        # Confirm no row was inserted as a side-effect
        cursor = session_db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sessions WHERE session_id = ?", ("never-created-uuid",))
        assert cursor.fetchone()[0] == 0

    def test_idempotent_after_heal(self, session_db):
        # After a heal, calling again should be a no-op (status="ok")
        session_id = self._seed_session_with_project_id(session_db, "ghost-pid-xxx")
        first = session_db.heal_session_project_id(session_id, "real-pid-yyy")
        second = session_db.heal_session_project_id(session_id, "real-pid-yyy")
        assert first == "healed"
        assert second == "ok"

    def test_heal_then_ensure_session_exists_no_op(self, session_db):
        # Composes correctly with the existing ensure_session_exists:
        # after a heal, ensure_session_exists should still see the row
        # (and return False = already-exists).
        session_id = self._seed_session_with_project_id(session_db, "ghost-pid-xxx")
        session_db.heal_session_project_id(session_id, "real-pid-yyy")
        assert session_db.ensure_session_exists(session_id) is False
