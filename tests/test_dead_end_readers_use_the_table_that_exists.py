"""Dead-end readers query a table the schema actually creates, and a failed
stage never holds sessions.db's write lock.

`session_dead_ends` was dropped in 73d63387b (2026-02-03, "use
project_dead_ends"). Three modules written later still queried it: prevention
detection, blindspot regret and the prevention oracle. Their tests built the
dropped table by hand, so fixture and code agreed with each other and disagreed
with every real store, and a missing table is quiet by design.

The cost was not the dead-end signal alone. In prevention detection the raise
came AFTER an UPDATE in the same loop, the except returned without rolling
back, and the caller never closed the connection. Under IMMEDIATE isolation
that pending write held sessions.db's lock for the rest of POSTFLIGHT, so
grounded verification waited out its 30 s timeout and was dropped as "database
is locked". Measured 2026-09-21: 4 of 42 POSTFLIGHTs on 2026-09-20 recorded a
verification, against 72 of 78 two days earlier; a POSTFLIGHT took 66 s.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from empirica.core.prevention.detection import apply_prevention_detection

REPO = Path(__file__).resolve().parent.parent
READERS = (
    "empirica/core/prevention/detection.py",
    "empirica/core/prevention/oracle.py",
    "empirica/core/blindspots/outcomes.py",
)


def _created_tables() -> set[str]:
    """Every table some migration or schema module creates AND none later drops."""
    created: set[str] = set()
    dropped: set[str] = set()
    for path in list((REPO / "empirica" / "data").rglob("*.py")):
        text = path.read_text()
        created |= set(re.findall(r"CREATE TABLE(?: IF NOT EXISTS)? (\w+)", text))
        dropped |= set(re.findall(r"DROP TABLE(?: IF EXISTS)? (\w+)", text))
    block = re.search(
        r"tables_to_drop\s*=\s*\[(.*?)\]", (REPO / "empirica/data/migrations/migrations.py").read_text(), re.S
    )
    if block:
        dropped |= set(re.findall(r'"(\w+)"', block.group(1)))
    return created - dropped


def test_positive_control_the_inventory_sees_a_live_and_a_dropped_table():
    tables = _created_tables()
    assert "project_dead_ends" in tables
    assert "session_dead_ends" not in tables, "the inventory cannot see drops, so this test proves nothing"


def test_every_table_the_readers_query_exists():
    tables = _created_tables()
    for rel in READERS:
        queried = set(re.findall(r"FROM (\w+)", (REPO / rel).read_text()))
        missing = sorted(t for t in queried if t not in tables)
        assert missing == [], f"{rel} queries tables no migration leaves in place: {missing}"


class _DB:
    def __init__(self, with_dead_ends: bool):
        self.conn = sqlite3.connect(":memory:", isolation_level="IMMEDIATE")
        self.conn.executescript(
            """
            CREATE TABLE prevention_events (id INTEGER PRIMARY KEY, session_id TEXT, goal_id TEXT,
              subtask_id TEXT, exposed_at REAL, acknowledged INTEGER, window_s REAL, shadow INTEGER,
              outcome TEXT, outcome_at REAL, outcome_family TEXT);
            CREATE TABLE mistakes_made (id INTEGER PRIMARY KEY, session_id TEXT, goal_id TEXT,
              subtask_id TEXT, created_timestamp REAL);
            -- row 1: NULL goal, not shadow -> no failure query at all, straight to an
            -- UPDATE ('unmeasurable'). row 2 is goal-bound, so it consults dead ends.
            -- That ORDER is the defect: a write, then a raise, in one transaction.
            INSERT INTO prevention_events VALUES (1,'s',NULL,NULL,100.0,0,10.0,0,'exposed',NULL,'prevention');
            INSERT INTO prevention_events VALUES (2,'s','g',NULL,100.0,1,10.0,0,'exposed',NULL,'prevention');
            """
        )
        if with_dead_ends:
            self.conn.execute(
                "CREATE TABLE project_dead_ends (id INTEGER PRIMARY KEY, session_id TEXT, goal_id TEXT, "
                "subtask_id TEXT, created_timestamp REAL)"
            )
        self.conn.commit()


def test_a_stage_that_raises_leaves_no_pending_write():
    db = _DB(with_dead_ends=False)
    assert apply_prevention_detection(db, "s", now=10_000.0) == 0
    assert db.conn.in_transaction is False, "a pending write here holds the lock for the rest of POSTFLIGHT"


def test_positive_control_with_the_table_present_rows_advance_and_commit():
    db = _DB(with_dead_ends=True)
    assert apply_prevention_detection(db, "s", now=10_000.0) == 2
    assert db.conn.in_transaction is False
    assert db.conn.execute("SELECT count(*) FROM prevention_events WHERE outcome='exposed'").fetchone()[0] == 0


def test_the_postflight_stage_closes_its_connection():
    import inspect

    from empirica.cli.command_handlers import _workflow_postflight as pf

    src = inspect.getsource(pf._postflight_prevention_detection)
    assert "finally:" in src and "db.close()" in src


def test_postflight_and_check_stages_that_write_always_close_their_connection():
    """Nine workflow functions opened a SessionDatabase and never closed it. The
    ones that WRITE, or hand the connection to a callee that does, are the hazard:
    under IMMEDIATE isolation a write left pending by a raise holds the lock for
    the rest of the process."""
    import inspect

    from empirica.cli.command_handlers import _workflow_check as ck
    from empirica.cli.command_handlers import _workflow_postflight as pf

    for fn in (
        pf._postflight_prevention_detection,
        pf._postflight_resolve_blindspots,
        pf._write_auto_structural_edges,
        pf._postflight_close_cascade_row,
        ck._persist_weave_event,
    ):
        assert ".close()" in inspect.getsource(fn), f"{fn.__name__} leaves its connection open"
