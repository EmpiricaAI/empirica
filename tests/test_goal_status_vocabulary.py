"""One word for a finished goal: `completed`.

Goals and tasks carried the status in two spellings, `complete` and `completed`, in code, comments and docs, and readers tolerated both (David 2026-11-04:
"just one should exist, comments, docs and code"). Subtasks were normalized by migration 064. This pins the same for goals: migration 078 rewrites the
legacy rows (6 on one practice, 0 on the rest of 36 scanned), and the readers and docs that tolerated or taught the singular now name one value.

The compliance loop's own result status (`complete`, `iteration_needed`, ...) is a different enumeration and is deliberately not covered here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from empirica.data.migrations.migrations import migration_078_goal_status_vocabulary

ROOT = Path(__file__).parent.parent


def _goals_db(tmp_path, rows):
    conn = sqlite3.connect(str(tmp_path / "g.db"))
    conn.execute("CREATE TABLE goals (id TEXT PRIMARY KEY, status TEXT, is_completed INT)")
    conn.executemany("INSERT INTO goals VALUES (?,?,?)", rows)
    conn.commit()
    return conn


def test_a_legacy_complete_goal_becomes_completed_and_stays_done(tmp_path):
    conn = _goals_db(tmp_path, [("a", "complete", 1), ("b", "completed", 1)])
    migration_078_goal_status_vocabulary(conn.cursor())
    conn.commit()
    assert conn.execute("SELECT status, is_completed FROM goals ORDER BY id").fetchall() == [
        ("completed", 1),
        ("completed", 1),
    ]


def test_the_migration_is_idempotent(tmp_path):
    conn = _goals_db(tmp_path, [("a", "complete", 1)])
    cur = conn.cursor()
    migration_078_goal_status_vocabulary(cur)
    migration_078_goal_status_vocabulary(cur)
    conn.commit()
    assert conn.execute("SELECT status FROM goals").fetchone()[0] == "completed"


def test_no_other_status_is_touched(tmp_path):
    rows = [(f"g{i}", s, 0) for i, s in enumerate(["planned", "in_progress", "blocked", "abandoned", "stale"])]
    conn = _goals_db(tmp_path, rows)
    before = conn.execute("SELECT id, status FROM goals ORDER BY id").fetchall()
    migration_078_goal_status_vocabulary(conn.cursor())
    conn.commit()
    assert conn.execute("SELECT id, status FROM goals ORDER BY id").fetchall() == before


def test_a_database_without_a_goals_table_is_left_alone(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "empty.db"))
    migration_078_goal_status_vocabulary(conn.cursor())


def test_the_readers_and_docs_name_one_spelling():
    """Each of these tolerated or taught the singular. A pattern, not a count: the word `complete` may appear in prose elsewhere."""
    tolerant = {
        "empirica/core/bootstrap/circles.py": "NOT IN ('complete', 'completed')",
        "empirica/core/qdrant/goals.py": 'status == "complete"',
        "docs/reference/api/goals_tasks.md": "'complete'",
    }
    for rel, needle in tolerant.items():
        assert needle not in (ROOT / rel).read_text(), f"{rel} still uses the singular status spelling: {needle}"


def test_the_goals_search_status_flag_offers_no_singular():
    import argparse

    from empirica.cli.parsers.checkpoint_parsers import add_checkpoint_parsers

    parser = argparse.ArgumentParser()
    add_checkpoint_parsers(parser.add_subparsers(dest="command"))
    try:
        parser.parse_args(["goals-search", "x", "--status", "complete"])
    except SystemExit:
        pass
    else:
        raise AssertionError("goals-search --status still accepts the singular spelling")
    assert parser.parse_args(["goals-search", "x", "--status", "completed"]).status == "completed"
