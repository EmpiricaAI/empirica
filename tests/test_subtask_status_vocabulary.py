"""One table, two words for one state — and the rollup counts only one.

`goals.py` computes per-goal progress with
``SUM(CASE WHEN s.status = 'completed' THEN 1 ELSE 0 END)``, so a subtask stored
as the singular `complete` is silently counted as NOT done. Measured on the
empirica practice before migration 064: 67 `complete`, 1162 `completed`, 895
`pending`.

Dead legacy data rather than a live split — `TaskStatus.COMPLETED` is
`"completed"`, no current writer emits the singular, and the 67 rows span
2025-12-03 to 2026-02-03 with nothing writing that value since. That is what
makes a one-time normalization the right instrument.
"""

from __future__ import annotations

import sqlite3

from empirica.core.tasks.types import TaskStatus
from empirica.data.migrations.migrations import migration_064_subtask_status_vocabulary


def _subtasks_db(tmp_path, rows):
    conn = sqlite3.connect(str(tmp_path / "s.db"))
    conn.execute("CREATE TABLE subtasks (id TEXT PRIMARY KEY, goal_id TEXT, status TEXT)")
    conn.executemany("INSERT INTO subtasks (id, goal_id, status) VALUES (?,?,?)", rows)
    conn.commit()
    return conn


def test_singular_complete_is_normalized(tmp_path):
    conn = _subtasks_db(
        tmp_path,
        [("t1", "g", "complete"), ("t2", "g", "completed"), ("t3", "g", "pending")],
    )
    migration_064_subtask_status_vocabulary(conn.cursor())
    conn.commit()
    got = dict(conn.execute("SELECT status, COUNT(*) FROM subtasks GROUP BY status").fetchall())
    assert got == {"completed": 2, "pending": 1}


def test_migration_is_idempotent(tmp_path):
    conn = _subtasks_db(tmp_path, [("t1", "g", "complete")])
    cur = conn.cursor()
    migration_064_subtask_status_vocabulary(cur)
    migration_064_subtask_status_vocabulary(cur)
    conn.commit()
    assert conn.execute("SELECT status FROM subtasks").fetchone()[0] == "completed"


def test_no_other_status_is_touched(tmp_path):
    """`pending` / `in_progress` / `blocked` / `skipped` must survive untouched —
    normalizing one word is not licence to normalize the vocabulary."""
    rows = [(f"t{i}", "g", s.value) for i, s in enumerate(TaskStatus)]
    conn = _subtasks_db(tmp_path, rows)
    before = sorted(r[0] for r in conn.execute("SELECT status FROM subtasks").fetchall())
    migration_064_subtask_status_vocabulary(conn.cursor())
    conn.commit()
    after = sorted(r[0] for r in conn.execute("SELECT status FROM subtasks").fetchall())
    assert before == after


def test_task_status_enum_writes_the_plural():
    """The premise the migration rests on: if a writer ever emitted the singular
    again, normalizing history would be pointless."""
    assert TaskStatus.COMPLETED.value == "completed"
    assert "complete" not in {s.value for s in TaskStatus}


def test_goal_progress_rollup_counts_only_the_plural():
    """Pins WHY this matters — the rollup that made the split invisible."""
    from pathlib import Path

    src = (Path(__file__).parent.parent / "empirica" / "data" / "repositories" / "goals.py").read_text()
    assert "s.status = 'completed'" in src, (
        "if the rollup changes shape, re-check whether the normalization is still the fix "
        "or whether reader tolerance became the right answer"
    )
