"""Housekeeping counts no longer ground `change`.

Bias-source investigation (2026-10-04), ruled on by David the same day: three post-test sensors mapped a COUNT of
housekeeping onto `change`, the same defect class as the prose proxies ruled on that morning.

* goals/goal_completion_change = completed/total subtasks, read as the amount of change.
* triage/goal_completion_ratio supported [completion, change]: the ratio of goals completed in the window.
* triage/triage_change = min(1, unknowns_resolved / 4): a sweep that resolves four unknowns read as change 1.0.

Over the last 300 core verifications these rows carried grounded `change` of 0.87 (goals, 30 rows) and 1.0 (triage, 27 rows)
against a self-assessment near 0.3. Resolving unknowns and finishing goals are not change in the code or the world; they stay
as evidence for `do` / `completion` / `know`, where they belong. Real change is still observed by the git sensors.
"""

from __future__ import annotations

import sqlite3
import time
from unittest.mock import MagicMock

from empirica.core.post_test.collector import PostTestCollector


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, start_time REAL)")
    conn.execute(
        "CREATE TABLE goals (id TEXT PRIMARY KEY, session_id TEXT, transaction_id TEXT, status TEXT, "
        "created_timestamp REAL, completed_timestamp REAL)"
    )
    conn.execute(
        "CREATE TABLE project_unknowns (id TEXT PRIMARY KEY, session_id TEXT, transaction_id TEXT, "
        "is_resolved INTEGER DEFAULT 0, resolved_timestamp REAL)"
    )
    conn.execute(
        "CREATE TABLE subtasks (id TEXT PRIMARY KEY, goal_id TEXT, status TEXT, "
        "estimated_tokens INTEGER, actual_tokens INTEGER)"
    )
    return conn


def _collector(conn, preflight=None, transaction_id=None) -> PostTestCollector:
    fake = MagicMock()
    fake.conn = conn
    collector = PostTestCollector(
        session_id="s1", db=fake, preflight_timestamp=preflight, transaction_id=transaction_id
    )
    collector._get_db = lambda: fake  # type: ignore[method-assign]
    return collector


def _busy_triage_transaction():
    """A transaction that resolved 6 unknowns and completed 2 goals, and changed nothing in the code."""
    conn = _db()
    now = time.time()
    conn.execute("INSERT INTO sessions VALUES ('s1', ?)", (now - 3600,))
    for i in range(6):
        conn.execute("INSERT INTO project_unknowns VALUES (?, 's1', NULL, 1, ?)", (f"u{i}", now - 30))
    for i in range(2):
        conn.execute("INSERT INTO goals VALUES (?, 's1', NULL, 'completed', ?, ?)", (f"g{i}", now - 100, now - 20))
    conn.commit()
    return _collector(conn, preflight=now - 60)


def _items(collector, method):
    return getattr(collector, method)()


def test_resolving_unknowns_is_still_evidence_for_do_and_know():
    """Positive control: the triage collector is live on this fixture, so the absences below prove something."""
    items = _items(_busy_triage_transaction(), "_collect_triage_metrics")

    resolved = [i for i in items if i.metric_name == "unknowns_resolved"]
    assert len(resolved) == 1 and set(resolved[0].supports_vectors) == {"do", "know"}


def test_resolving_many_unknowns_no_longer_emits_triage_change():
    items = _items(_busy_triage_transaction(), "_collect_triage_metrics")

    assert [i for i in items if i.metric_name == "triage_change"] == []


def test_goal_completion_ratio_keeps_completion_but_not_change():
    items = _items(_busy_triage_transaction(), "_collect_triage_metrics")
    ratio = [i for i in items if i.metric_name == "goal_completion_ratio"]

    assert len(ratio) == 1, "the ratio is emitted (positive control)"
    assert "completion" in ratio[0].supports_vectors and "change" not in ratio[0].supports_vectors


def test_no_triage_observation_supports_change_at_all():
    items = _items(_busy_triage_transaction(), "_collect_triage_metrics")

    assert items and all("change" not in i.supports_vectors for i in items)


def _goals_collector():
    conn = _db()
    conn.execute("INSERT INTO goals VALUES ('g1', 's1', 'tx1', 'completed', NULL, NULL)")
    conn.executemany("INSERT INTO subtasks VALUES (?, 'g1', 'completed', NULL, NULL)", [("t1",), ("t2",), ("t3",)])
    conn.commit()
    return _collector(conn, transaction_id="tx1")


def test_completing_subtasks_still_grounds_completion_and_impact():
    """Positive control for the goals collector."""
    names = {i.metric_name for i in _items(_goals_collector(), "_collect_goal_metrics")}

    assert {"subtask_completion_ratio", "goal_completion_impact"} <= names


def test_completing_subtasks_no_longer_emits_goal_completion_change():
    items = _items(_goals_collector(), "_collect_goal_metrics")

    assert [i for i in items if i.metric_name == "goal_completion_change"] == []
    assert all("change" not in i.supports_vectors for i in items)
