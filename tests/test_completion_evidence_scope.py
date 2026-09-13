"""The completion evidence must say WHICH question its ratio answered.

`completion` is defined as progress toward the current phase goal. The collector
normally measures that transaction-scoped — but falls back to counting every
subtask in the SESSION when no transaction is in hand, which happens whenever
POSTFLIGHT cannot find an active-transaction file.

Both branches emit a plain float. Early in a long session the session denominator
is already large and its numerator is still zero, so the fallback writes a
truthful 0.0 about a quantity the self-assessment was never describing, and the
difference is stored as calibration error.

These tests hold the distinguisher: the scope rides along with the number.
Reported by empirica-mesh-support replicating Carly R. Anderson's foundation
measurement across 17 practices (2026-09-13).
"""

from __future__ import annotations

import sqlite3

from empirica.core.post_test.collector import PostTestCollector


class _Db:
    def __init__(self, conn):
        self.conn = conn


def _goals_db() -> sqlite3.Connection:
    """A session holding two goals: an older one finished, the current one not.

    This is the shape that produced the false zeros — a populated session-wide
    denominator with nothing completed inside the live transaction.
    """
    conn = sqlite3.connect(":memory:")
    # Every column `_collect_goal_metrics` touches, so a missing one surfaces as a
    # test-fixture bug rather than as an OperationalError mid-assertion.
    conn.execute("CREATE TABLE goals (id TEXT PRIMARY KEY, session_id TEXT, transaction_id TEXT, status TEXT)")
    conn.execute(
        "CREATE TABLE subtasks (id TEXT PRIMARY KEY, goal_id TEXT, status TEXT, "
        "estimated_tokens INTEGER, actual_tokens INTEGER)"
    )
    conn.executemany(
        "INSERT INTO goals VALUES (?, ?, ?, ?)",
        [("g_old", "s1", "tx_old", "completed"), ("g_now", "s1", "tx_now", "in_progress")],
    )
    conn.executemany(
        "INSERT INTO subtasks VALUES (?, ?, ?, ?, ?)",
        [
            ("t1", "g_old", "completed", None, None),
            ("t2", "g_old", "completed", None, None),
            ("t3", "g_now", "pending", None, None),
        ],
    )
    return conn


def _completion_item(collector: PostTestCollector):
    items = collector._collect_goal_metrics()
    matches = [i for i in items if i.metric_name == "subtask_completion_ratio"]
    return matches[0] if matches else None


def test_transaction_scoped_ratio_is_labelled_transaction():
    conn = _goals_db()
    item = _completion_item(PostTestCollector(session_id="s1", db=_Db(conn), transaction_id="tx_now"))

    assert item is not None
    assert item.raw_value["scope"] == "transaction"
    assert item.raw_value == {"completed": 0, "total": 1, "scope": "transaction"}


def test_session_fallback_is_labelled_session_not_silently_passed_off():
    """The fallback may still emit — it must not emit ANONYMOUSLY."""
    conn = _goals_db()
    item = _completion_item(PostTestCollector(session_id="s1", db=_Db(conn)))

    assert item is not None
    assert item.raw_value["scope"] == "session"
    assert item.raw_value["total"] == 3, "the session branch counts every subtask in the session"
    assert item.metadata["scope"] == "session"


def test_the_two_scopes_disagree_on_the_same_data():
    """A positive control: without this, both tests above could pass on one branch.

    If these ever returned the same ratio the scope label would be decoration —
    the label matters precisely because the underlying number differs.
    """
    conn = _goals_db()
    tx = _completion_item(PostTestCollector(session_id="s1", db=_Db(conn), transaction_id="tx_old"))
    session = _completion_item(PostTestCollector(session_id="s1", db=_Db(conn)))

    assert tx.value == 1.0, "the old transaction completed everything it opened"
    assert session.value < 1.0, "the session as a whole has not"
    assert tx.value != session.value
