"""Prevention exposures acquire a subject when their transaction acquires a goal.

Exposure is **structurally subjectless**: `wiring.py` emits at PREFLIGHT, before
any goal exists, writing `goal_id` NULL and `subject_key = f"session:{id}"`. So
there are only two honest architectures — invent a subject at emission, where
there is none to invent, or bind one later. This is the later.

Without it, `detection.py` has nothing to scope to and falls back to *any failure
anywhere in the session*, which for anyone logging mistakes at a normal rate
resolves to `failed` almost every time. Measured before this existed: 215 of 218
events `failed`, `prevented` recorded zero times ever.

**Both columns, deliberately.** `goal_id` is what detection scopes on;
`subject_key` is what the oracle joins through. Fixing one leaves the other
session-wide — and a `subject_key` still holding the session makes "scope it to
the subject" a rename rather than a fix. A peer measured that collapse as 167
patterns on 3 subjects; it was 204 on 1 here.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.data.repositories.breadcrumbs import BreadcrumbRepository

SCHEMA = """
CREATE TABLE prevention_events (
    id TEXT PRIMARY KEY, session_id TEXT, transaction_id TEXT,
    pattern_key TEXT, subject_key TEXT, goal_id TEXT, subtask_id TEXT,
    outcome TEXT, exposed_at REAL
);
"""


@pytest.fixture
def repo(tmp_path):
    conn = sqlite3.connect(tmp_path / "s.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield BreadcrumbRepository(conn)
    finally:
        conn.close()


def _expose(repo, rid, *, session="s1", tx="t1", goal=None, subject=None, pattern="p"):
    repo.conn.execute(
        "INSERT INTO prevention_events (id, session_id, transaction_id, pattern_key, subject_key, goal_id, outcome) "
        "VALUES (?,?,?,?,?,?, 'exposed')",
        (rid, session, tx, pattern, subject or "session:s1", goal),
    )


def _row(repo, rid):
    return repo.conn.execute("SELECT goal_id, subject_key FROM prevention_events WHERE id = ?", (rid,)).fetchone()


def test_binding_sets_both_columns_not_just_goal_id(repo):
    """The whole reason the second column is here: fixing one leaves the other session-wide."""
    _expose(repo, "e1")
    assert repo.bind_prevention_subjects("g1", "s1", "t1") == 1
    r = _row(repo, "e1")
    assert r["goal_id"] == "g1"
    assert r["subject_key"] == "goal:g1", "subject_key must stop holding the session"


def test_it_only_binds_this_transaction(repo):
    """A goal created mid-transaction owns that transaction's exposures, not the session's."""
    _expose(repo, "mine", tx="t1")
    _expose(repo, "other", tx="t2")
    assert repo.bind_prevention_subjects("g1", "s1", "t1") == 1
    assert _row(repo, "other")["goal_id"] is None


def test_an_already_bound_row_is_left_alone(repo):
    """Same rule as the artifact backfill — do not steal a row from another goal."""
    _expose(repo, "taken", goal="g0", subject="goal:g0")
    assert repo.bind_prevention_subjects("g1", "s1", "t1") == 0
    r = _row(repo, "taken")
    assert r["goal_id"] == "g0"
    assert r["subject_key"] == "goal:g0"


def test_it_is_idempotent(repo):
    """Two goals in one transaction must not rebind the first goal's rows."""
    _expose(repo, "e1")
    assert repo.bind_prevention_subjects("g1", "s1", "t1") == 1
    assert repo.bind_prevention_subjects("g2", "s1", "t1") == 0
    assert _row(repo, "e1")["goal_id"] == "g1"


def test_no_transaction_binds_nothing(repo):
    """Outside a transaction there is no set of exposures this goal owns."""
    _expose(repo, "e1")
    assert repo.bind_prevention_subjects("g1", "s1", None) == 0
    assert _row(repo, "e1")["goal_id"] is None


def test_a_missing_table_does_not_break_goal_creation(tmp_path):
    """Best-effort: an older DB without the table must not fail the goals-create path."""
    conn = sqlite3.connect(tmp_path / "bare.db")
    conn.row_factory = sqlite3.Row
    try:
        assert BreadcrumbRepository(conn).bind_prevention_subjects("g1", "s1", "t1") == 0
    finally:
        conn.close()


def test_binding_is_what_makes_the_population_anchorable(repo):
    """End-to-end with the P0 gate: unbound rows are unanchored, bound ones are not."""
    from empirica.core.prevention.anchoring import anchoring_verdict

    for i in range(3):
        _expose(repo, f"e{i}", pattern=f"p{i}")
    repo.conn.execute("UPDATE prevention_events SET outcome = 'failed'")

    rows = [dict(r) for r in repo.conn.execute("SELECT * FROM prevention_events").fetchall()]
    assert anchoring_verdict(rows)["anchored"] is False

    for i in range(3):
        repo.bind_prevention_subjects(f"g{i}", "s1", "t1")
        repo.conn.execute("UPDATE prevention_events SET transaction_id = 't1' WHERE id = ?", (f"e{i}",))
    # Rebind each row to its own goal so subjects are distinct.
    for i in range(3):
        repo.conn.execute(
            "UPDATE prevention_events SET goal_id = ?, subject_key = ? WHERE id = ?",
            (f"g{i}", f"goal:g{i}", f"e{i}"),
        )
    rows = [dict(r) for r in repo.conn.execute("SELECT * FROM prevention_events").fetchall()]
    assert anchoring_verdict(rows)["anchored"] is True
