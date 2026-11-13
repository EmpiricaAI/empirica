"""global_projects is core's table: an existing table loses workspace's project_type DEFAULT 'product' (goal f09ee70a).

Workspace also created global_projects, with `project_type TEXT DEFAULT 'product'`, and on this machine its definition was the
one in force; a row inserted without a type got the legacy value core no longer accepts. Workspace removed its definition
(2026-11-13); CREATE IF NOT EXISTS cannot change a table that exists, so core rebuilds it once, keeping every row and column.
"""

from __future__ import annotations

import sqlite3

from empirica.data.repositories.workspace_db import _ensure_workspace_schema

WORKSPACE_STYLE = """
CREATE TABLE global_projects (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, trajectory_path TEXT NOT NULL UNIQUE,
    git_remote_url TEXT, git_branch TEXT DEFAULT 'main', total_transactions INTEGER DEFAULT 0,
    total_findings INTEGER DEFAULT 0, total_unknowns INTEGER DEFAULT 0, total_dead_ends INTEGER DEFAULT 0,
    total_goals INTEGER DEFAULT 0, last_transaction_id TEXT, last_transaction_timestamp REAL, last_sync_timestamp REAL,
    status TEXT DEFAULT 'active', project_type TEXT DEFAULT 'product', project_tags TEXT,
    created_timestamp REAL NOT NULL, updated_timestamp REAL NOT NULL, metadata TEXT, extra_col TEXT
)
"""


def _default(con, column):
    return {r[1]: r[4] for r in con.execute("PRAGMA table_info(global_projects)")}[column]


def _workspace_db(tmp_path):
    con = sqlite3.connect(tmp_path / "workspace.db")
    con.execute(WORKSPACE_STYLE)
    con.execute(
        "INSERT INTO global_projects (id, name, trajectory_path, project_type, created_timestamp, updated_timestamp,"
        " extra_col) VALUES ('p1', 'core', '/w/core/.empirica', 'cli', 1, 2, 'kept')"
    )
    con.execute(
        "INSERT INTO global_projects (id, name, trajectory_path, created_timestamp, updated_timestamp)"
        " VALUES ('p2', 'old', '/w/old/.empirica', 3, 4)"
    )
    con.commit()
    return con


def test_the_product_default_is_removed_and_every_row_kept(tmp_path):
    con = _workspace_db(tmp_path)
    _ensure_workspace_schema(con)
    con.commit()
    assert _default(con, "project_type") is None
    rows = con.execute("SELECT id, project_type, extra_col, status FROM global_projects ORDER BY id").fetchall()
    assert rows == [("p1", "cli", "kept", "active"), ("p2", "product", None, "active")]  # data untouched


def test_a_row_inserted_after_the_migration_has_no_type(tmp_path):
    con = _workspace_db(tmp_path)
    _ensure_workspace_schema(con)
    con.execute(
        "INSERT INTO global_projects (id, name, trajectory_path, created_timestamp, updated_timestamp)"
        " VALUES ('p3', 'new', '/w/new/.empirica', 5, 6)"
    )
    assert con.execute("SELECT project_type FROM global_projects WHERE id = 'p3'").fetchone() == (None,)


def test_the_indexes_and_other_defaults_survive_and_a_second_run_changes_nothing(tmp_path):
    con = _workspace_db(tmp_path)
    _ensure_workspace_schema(con)
    sql_after_first = con.execute("SELECT sql FROM sqlite_master WHERE name = 'global_projects'").fetchone()[0]
    _ensure_workspace_schema(con)
    assert con.execute("SELECT sql FROM sqlite_master WHERE name = 'global_projects'").fetchone()[0] == sql_after_first
    indexes = {
        r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='global_projects'")
    }
    assert {"idx_global_projects_status", "idx_global_projects_type", "idx_global_projects_last_tx"} <= indexes
    assert _default(con, "status") == "'active'"


def test_a_core_table_is_never_rebuilt(tmp_path):
    con = sqlite3.connect(tmp_path / "workspace.db")
    _ensure_workspace_schema(con)
    before = con.execute("SELECT sql FROM sqlite_master WHERE name = 'global_projects'").fetchone()[0]
    _ensure_workspace_schema(con)
    assert con.execute("SELECT sql FROM sqlite_master WHERE name = 'global_projects'").fetchone()[0] == before
    assert _default(con, "project_type") is None


# ── shapes the rebuild must survive (pre-publish sweep B1, goal 4c935e3b) ──────────────────────────────────────────────


def _shaped(tmp_path, create_sql):
    con = sqlite3.connect(tmp_path / "workspace.db")
    con.execute(create_sql)
    con.execute(
        "INSERT INTO global_projects (id, name, trajectory_path, created_timestamp, updated_timestamp)"
        " VALUES ('p1', 'core', '/w/core/.empirica', 1, 2)"
    )
    con.commit()
    return con


_COLS = (
    "(id TEXT PRIMARY KEY, name TEXT NOT NULL, trajectory_path TEXT NOT NULL UNIQUE, status TEXT DEFAULT 'active',"
    " project_type TEXT {default}, created_timestamp REAL NOT NULL, updated_timestamp REAL NOT NULL)"
)


def test_a_quoted_table_name_is_rebuilt(tmp_path):
    con = _shaped(tmp_path, 'CREATE TABLE "global_projects" ' + _COLS.format(default="DEFAULT 'product'"))
    _ensure_workspace_schema(con)
    assert _default(con, "project_type") is None
    assert con.execute("SELECT id, status FROM global_projects").fetchall() == [("p1", "active")]


def test_a_default_null_or_parenthesised_default_never_breaks_the_table(tmp_path):
    for i, default in enumerate(("DEFAULT NULL", "DEFAULT ('product')")):
        sub = tmp_path / str(i)
        sub.mkdir()
        con = _shaped(sub, "CREATE TABLE global_projects " + _COLS.format(default=default))
        _ensure_workspace_schema(con)  # must not raise
        assert con.execute("SELECT id, status FROM global_projects").fetchall() == [("p1", "active")]


def test_a_shape_it_cannot_parse_is_left_alone_not_raised(tmp_path):
    con = _shaped(tmp_path, "CREATE TABLE global_projects " + _COLS.format(default="DEFAULT 'product' COLLATE NOCASE"))
    _ensure_workspace_schema(con)  # must not raise, whatever it decides
    assert con.execute("SELECT count(*) FROM global_projects").fetchone() == (1,)
