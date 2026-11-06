"""An older global_projects table gains the columns the current code writes.

`CREATE TABLE IF NOT EXISTS` does nothing when the table is already there, so a workspace.db made by an older release kept its old column
set and `project-register` failed with "table global_projects has no column named metadata". The schema helper now adds what is missing,
from the same column list the CREATE is built from, so a column added later cannot be forgotten the same way.

Core had two copies of the helper (the repository's and the CLI's). There is one now; the CLI name calls it.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.cli.command_handlers.project_commands import ensure_workspace_schema
from empirica.data.repositories import workspace_db as wdb
from empirica.data.repositories.workspace_db import WorkspaceDBRepository, _ensure_workspace_schema

#: The table as an older release made it: no project_type, no project_tags, no metadata.
OLD_TABLE = """
    CREATE TABLE global_projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        trajectory_path TEXT NOT NULL UNIQUE,
        git_remote_url TEXT,
        git_branch TEXT DEFAULT 'main',
        total_transactions INTEGER DEFAULT 0,
        total_findings INTEGER DEFAULT 0,
        total_unknowns INTEGER DEFAULT 0,
        total_dead_ends INTEGER DEFAULT 0,
        total_goals INTEGER DEFAULT 0,
        last_transaction_id TEXT,
        last_transaction_timestamp REAL,
        last_sync_timestamp REAL,
        status TEXT DEFAULT 'active',
        created_timestamp REAL NOT NULL,
        updated_timestamp REAL NOT NULL
    )
"""


def _columns(conn: sqlite3.Connection, table: str = "global_projects") -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _old_db(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "workspace.db"))
    conn.row_factory = sqlite3.Row
    conn.execute(OLD_TABLE)
    conn.execute(
        "INSERT INTO global_projects (id, name, trajectory_path, created_timestamp, updated_timestamp) VALUES (?, ?, ?, ?, ?)",
        ("p-old", "old project", "/somewhere/old/.empirica", 1.0, 1.0),
    )
    conn.commit()
    return conn


@pytest.mark.parametrize("ensure", [_ensure_workspace_schema, ensure_workspace_schema], ids=["repository", "cli"])
def test_an_old_table_gains_every_column_the_code_writes(tmp_path, ensure):
    conn = _old_db(tmp_path)
    assert "metadata" not in _columns(conn)  # positive control: the old table really lacks it

    ensure(conn)

    fresh = sqlite3.connect(":memory:")
    _ensure_workspace_schema(fresh)
    assert set(_columns(conn)) == set(_columns(fresh))


def test_a_project_registers_against_a_healed_table_and_the_old_row_is_kept(tmp_path):
    conn = _old_db(tmp_path)
    _ensure_workspace_schema(conn)

    WorkspaceDBRepository(conn).upsert_project(
        project_id="p-new",
        name="new project",
        trajectory_path=str(tmp_path / "new" / ".empirica"),
        project_type="cli",
        metadata='{"k": "v"}',
    )

    rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM global_projects").fetchall()}
    assert rows["p-new"]["metadata"] == '{"k": "v"}'
    assert rows["p-new"]["project_type"] == "cli"
    assert rows["p-old"]["name"] == "old project"
    assert rows["p-old"]["metadata"] is None


def test_healing_twice_changes_nothing(tmp_path):
    conn = _old_db(tmp_path)
    _ensure_workspace_schema(conn)
    before = _columns(conn)
    _ensure_workspace_schema(conn)
    assert _columns(conn) == before


def test_the_create_statement_and_the_added_columns_come_from_one_list():
    """Every column of a fresh table is in the list the healing loop walks, in the same order."""
    fresh = sqlite3.connect(":memory:")
    _ensure_workspace_schema(fresh)
    assert _columns(fresh) == [name for name, _type, _constraint in wdb.GLOBAL_PROJECTS_COLUMNS]


def test_both_helper_names_build_the_same_database():
    """One helper, two names: the CLI's is not a second copy that can fall behind."""

    def schema(ensure) -> list[tuple[str, str]]:
        conn = sqlite3.connect(":memory:")
        ensure(conn)
        return sorted(
            (row[0], row[1])
            for row in conn.execute("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall()
        )

    assert schema(ensure_workspace_schema) == schema(_ensure_workspace_schema)
