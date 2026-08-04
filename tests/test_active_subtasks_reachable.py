"""`active_subtasks` was structurally unreachable, and it looked like disuse.

The circle-1 query selected `name` / `importance` and filtered on `is_completed`.
The table has `description` / `epistemic_importance` / `status`. So it raised
`OperationalError` on every call, and:

    except sqlite3.OperationalError:
        # subtasks table shape differs across migrations; skip gracefully
        pass

swallowed it. `active_subtasks` was EMPTY for every practitioner from 2026-05-07
until the fix, while 2096 rows accumulated in the table.

**The appearance of disuse WAS the defect.** Tasks never surfaced in retrieval, so
nothing rewarded logging them — a dead read looks exactly like a feature nobody
wanted, and the two are indistinguishable from the outside.

Two guards, because the mismatch had two halves: the columns must match the live
schema, and the failure must not be silent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CIRCLES = _ROOT / "empirica" / "core" / "bootstrap" / "circles.py"


def _schema_columns() -> set[str]:
    """Columns the WRITER creates — read from the repository that owns the table,
    so this cannot drift from what actually exists."""
    src = (_ROOT / "empirica" / "data" / "repositories" / "goals.py").read_text()
    import re

    m = re.search(r"INSERT INTO subtasks \(([^)]+)\)", src)
    assert m, "the subtasks INSERT moved — this guard needs re-anchoring"
    return {c.strip() for c in m.group(1).split(",")}


def _subtask_query_columns() -> set[str]:
    """Columns the circle-1 query asks subtasks for.

    Scoped to the `# 2. Active subtasks` block and flattened, because the SQL is
    assembled from adjacent f-string fragments — a naive `SELECT ... FROM` match
    spans the quote boundaries and silently finds nothing, which would make every
    assertion below vacuous.
    """
    import re

    src = _CIRCLES.read_text()
    block = src.split("# 2. Active subtasks", 1)[1].split("# 3.", 1)[0]
    flat = " ".join(block.replace('f"', " ").replace('"', " ").split())
    m = re.search(r"SELECT (.+?) FROM subtasks", flat)
    assert m, "no SELECT ... FROM subtasks in the active-subtasks block"
    return {c.strip() for c in m.group(1).split(",") if c.strip()}


def test_the_guard_can_actually_see_the_query():
    """Guards the guard. If the extraction returns nothing, the column check below
    passes vacuously and reports safety it never verified."""
    assert len(_subtask_query_columns()) >= 4


def test_the_query_selects_columns_that_exist():
    """The exact defect: a pre-migration column shape, never updated."""
    phantom = sorted(_subtask_query_columns() - _schema_columns())

    assert not phantom, f"active_subtasks selects columns the table does not have: {phantom}"


def test_the_failure_path_is_not_silent():
    """A bare `pass` here is what hid a dead query for three months — the caller
    cannot tell 'no open tasks' from 'this never ran'."""
    src = _CIRCLES.read_text()
    block = src.split("FROM subtasks", 1)[1].split("# 3.", 1)[0]

    assert "logger.warning" in block, "the degrade path must say it degraded"
    import re as _re

    tail = block.split("except")[-1]
    assert not _re.search(r"^\s*pass\s*$", tail, _re.M), "silent pass reintroduced"


def test_both_completed_spellings_are_excluded():
    """`status` carries BOTH 'complete' and 'completed'. Matching one spelling
    silently leaks the other back into 'active'."""
    src = _CIRCLES.read_text()

    assert "'complete', 'completed'" in src or '"complete", "completed"' in src


def test_open_subtasks_surface_and_completed_ones_do_not():
    """Run the query's own shape against a real table.

    Scoped to the subtasks SQL rather than all of circle_1: the regression is the
    column mismatch and the status filter, and rebuilding every sibling table the
    circle also reads would test sqlite, not this.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE subtasks (id TEXT, goal_id TEXT, description TEXT, "
        "epistemic_importance TEXT, status TEXT, created_timestamp REAL, subtask_data TEXT)"
    )
    conn.executemany(
        "INSERT INTO subtasks VALUES (?,?,?,?,?,?,?)",
        [
            ("s1", "g1", "still open", "MEDIUM", "pending", 2.0, "{}"),
            ("s2", "g1", "done one way", "MEDIUM", "completed", 3.0, "{}"),
            ("s3", "g1", "done the other way", "MEDIUM", "complete", 4.0, "{}"),
            ("s4", "g1", "no status at all", "MEDIUM", None, 5.0, "{}"),
        ],
    )
    conn.commit()

    cols = ", ".join(["id", "description", "status", "epistemic_importance", "goal_id", "created_timestamp"])
    rows = conn.execute(
        f"SELECT {cols} FROM subtasks WHERE goal_id IN (?) "
        "AND COALESCE(status, '') NOT IN ('complete', 'completed') "
        "ORDER BY created_timestamp DESC",
        ("g1",),
    ).fetchall()

    surfaced = sorted(r[1] for r in rows)
    # BOTH completed spellings excluded; a NULL status is still open, not dropped.
    assert surfaced == ["no status at all", "still open"], surfaced
