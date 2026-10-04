"""
Tests for transaction-scoped triage metrics in PostTestCollector.

Regression tests for the completion bias discovered 2026-04-07: every
praxic POSTFLIGHT in session 659f0619 reported completion observation
~0.51 regardless of actual per-transaction work. Root cause: the triage
metrics collector used `session_start` as the time filter instead of
`preflight_timestamp`, so each POSTFLIGHT counted ALL goals completed
since session start. The `do_score` formula (`0.4 + (n-1)*0.15`) was
also calibrated for session-scale counts (5-15 goals), under-scoring
per-transaction counts (1-3 goals) to ~0.40-0.55.

These tests lock in:
  - Transaction-scoped queries when preflight_timestamp is provided
  - Transaction-scale do_score formula for per-transaction counts
  - Session-scoped fallback when preflight_timestamp is None
  - Session-scale formula preserved for legacy triage sessions
"""

from __future__ import annotations

import sqlite3
import time
from unittest.mock import MagicMock

import pytest

from empirica.core.post_test.collector import PostTestCollector

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_db() -> sqlite3.Connection:
    """Create an in-memory SQLite with minimal schema for triage metrics."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("""
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            start_time REAL
        )
    """)
    c.execute("""
        CREATE TABLE goals (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            transaction_id TEXT,
            status TEXT,
            created_timestamp REAL,
            completed_timestamp REAL
        )
    """)
    c.execute("""
        CREATE TABLE project_unknowns (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            transaction_id TEXT,
            is_resolved INTEGER DEFAULT 0,
            resolved_timestamp REAL
        )
    """)
    c.execute("""
        CREATE TABLE subtasks (
            id TEXT PRIMARY KEY,
            goal_id TEXT,
            status TEXT,
            estimated_tokens INTEGER,
            actual_tokens INTEGER
        )
    """)
    conn.commit()
    return conn


def _make_collector(conn, session_id: str, preflight_timestamp: float | None):
    """Build a collector with a mocked _get_db()."""
    # Fake db wrapper: collector calls self._get_db().conn.cursor()
    fake_db = MagicMock()
    fake_db.conn = conn

    collector = PostTestCollector(
        session_id=session_id,
        db=fake_db,
        preflight_timestamp=preflight_timestamp,
    )
    # Bypass any lazy db lookup
    collector._get_db = lambda: fake_db  # type: ignore[method-assign]
    return collector


def _insert_session(conn, session_id: str, start: float):
    conn.execute(
        "INSERT INTO sessions (session_id, start_time) VALUES (?, ?)",
        (session_id, start),
    )
    conn.commit()


def _insert_goal(
    conn,
    goal_id: str,
    session_id: str,
    status: str,
    created: float,
    completed: float | None = None,
):
    conn.execute(
        """INSERT INTO goals
           (id, session_id, status, created_timestamp, completed_timestamp)
           VALUES (?, ?, ?, ?, ?)""",
        (goal_id, session_id, status, created, completed),
    )
    conn.commit()


def _insert_unknown(
    conn,
    unknown_id: str,
    session_id: str,
    resolved: bool,
    resolved_ts: float | None,
):
    conn.execute(
        """INSERT INTO project_unknowns
           (id, session_id, is_resolved, resolved_timestamp)
           VALUES (?, ?, ?, ?)""",
        (unknown_id, session_id, 1 if resolved else 0, resolved_ts),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Transaction scoping tests
# ---------------------------------------------------------------------------


class TestTriageTransactionScoping:
    """With preflight_timestamp set, triage metrics should ONLY count work
    done within the transaction window, not since session start.
    """

    def test_transaction_scope_excludes_pre_preflight_goals(self):
        """Goals completed BEFORE preflight should not be counted."""
        conn = _setup_db()
        now = time.time()
        session_start = now - 3600  # 1 hour ago
        preflight = now - 60  # 1 minute ago
        _insert_session(conn, "tx-test", session_start)

        # Goal completed 30 minutes ago — BEFORE preflight
        _insert_goal(conn, "g1", "tx-test", "completed", session_start + 100, now - 1800)
        # Goal completed 30 seconds ago — DURING transaction
        _insert_goal(conn, "g2", "tx-test", "completed", preflight - 10, now - 30)

        collector = _make_collector(conn, "tx-test", preflight_timestamp=preflight)
        items = collector._collect_triage_metrics()

        # Should see only g2 (1 goal), not g1+g2 (2 goals)
        goals_item = next((i for i in items if i.metric_name == "goals_completed"), None)
        assert goals_item is not None
        assert goals_item.raw_value["completed"] == 1
        assert goals_item.raw_value["scope"] == "transaction"

    def test_transaction_scope_do_score_is_high_for_one_goal(self):
        """One goal completed in a transaction should produce a high do_score
        (>= 0.7), not the session-scale 0.4 that was causing the ~0.51 bias.
        """
        conn = _setup_db()
        now = time.time()
        preflight = now - 60
        _insert_session(conn, "tx-test", now - 3600)
        _insert_goal(conn, "g1", "tx-test", "completed", preflight - 10, now - 30)

        collector = _make_collector(conn, "tx-test", preflight_timestamp=preflight)
        items = collector._collect_triage_metrics()

        goals_item = next((i for i in items if i.metric_name == "goals_completed"), None)
        assert goals_item is not None
        # Transaction scale: 0.5 + 1*0.2 = 0.7
        assert goals_item.value == pytest.approx(0.7, abs=0.01)

    def test_transaction_scope_do_score_caps_at_one_for_three_plus(self):
        """Three or more goals completed in a transaction = full do_score."""
        conn = _setup_db()
        now = time.time()
        preflight = now - 60
        _insert_session(conn, "tx-test", now - 3600)
        for i in range(4):
            _insert_goal(conn, f"g{i}", "tx-test", "completed", preflight - 10, now - 30)

        collector = _make_collector(conn, "tx-test", preflight_timestamp=preflight)
        items = collector._collect_triage_metrics()

        goals_item = next((i for i in items if i.metric_name == "goals_completed"), None)
        assert goals_item is not None
        assert goals_item.value == 1.0

    def test_no_preflight_timestamp_emits_no_items(self):
        """When preflight_timestamp is None, the triage collector emits NO
        items. Session-scoping is meaningless — without a transaction
        window, there's no honest way to attribute work to "now". Prior
        behavior fell back to session-scope which produced the ~0.51
        completion bias across long sessions. New behavior: honest absence.
        """
        conn = _setup_db()
        now = time.time()
        session_start = now - 3600
        _insert_session(conn, "sess-test", session_start)
        _insert_goal(conn, "g1", "sess-test", "completed", session_start + 100, now - 30)

        collector = _make_collector(conn, "sess-test", preflight_timestamp=None)
        items = collector._collect_triage_metrics()

        # No items should be emitted — the collector refuses to measure
        # without a transaction scope.
        assert items == [], (
            f"Expected no items when preflight_timestamp=None, got: "
            f"{[i.metric_name for i in items]}. Session-scoping was "
            f"removed 2026-04-07 because it's not a meaningful metric unit."
        )


class TestTriageUnknownResolution:
    """Unknown resolution metrics should also be transaction-scoped when
    preflight_timestamp is available."""

    def test_transaction_scope_unknowns_high_score_for_one(self):
        """One unknown resolved in a transaction = 0.7 (was ~0.033 under
        session formula 1/30)."""
        conn = _setup_db()
        now = time.time()
        preflight = now - 60
        _insert_session(conn, "tx-test", now - 3600)
        _insert_unknown(conn, "u1", "tx-test", resolved=True, resolved_ts=now - 30)

        collector = _make_collector(conn, "tx-test", preflight_timestamp=preflight)
        items = collector._collect_triage_metrics()

        unknown_item = next((i for i in items if i.metric_name == "unknowns_resolved"), None)
        assert unknown_item is not None
        # Transaction scale: 0.5 + 1*0.2 = 0.7
        assert unknown_item.value == pytest.approx(0.7, abs=0.01)

    def test_resolving_unknowns_never_emits_triage_change(self):
        """triage_change (min(1, resolved/4)) was removed 2026-10-04: resolving unknowns is not change.
        Both scopes, however many are resolved. See tests/test_no_count_to_change.py."""
        for session, preflight, n in (("tx-test", time.time() - 60, 2), ("sess-test", None, 6)):
            conn = _setup_db()
            now = time.time()
            _insert_session(conn, session, now - 3600)
            for i in range(n):
                _insert_unknown(conn, f"u{i}", session, resolved=True, resolved_ts=now - 30)

            items = _make_collector(conn, session, preflight_timestamp=preflight)._collect_triage_metrics()

            if preflight is not None:  # without a preflight timestamp the collector emits nothing at all
                assert any(i.metric_name == "unknowns_resolved" for i in items), "collector live (positive control)"
            assert not any(i.metric_name == "triage_change" for i in items)


# ---------------------------------------------------------------------------
# Regression: the 0.51 completion bias from session 659f0619
# ---------------------------------------------------------------------------


class TestCompletionBiasRegression:
    """Reproduces the specific pattern observed in session 659f0619 (2026-04-07):
    14 praxic POSTFLIGHTs all reported completion observation ~0.51 regardless
    of actual per-transaction work. With the fix, transaction-scoped POSTFLIGHTs
    should report values that reflect real per-transaction completion.
    """

    def test_release_transaction_with_one_goal_completed(self):
        """A release transaction that completes 1 goal should report do ~0.7,
        not ~0.51."""
        conn = _setup_db()
        now = time.time()
        # Simulate a session with history: many goals completed before,
        # then a new transaction preflight, then 1 goal completed in the tx.
        session_start = now - 3600
        preflight = now - 120  # Transaction started 2 min ago
        _insert_session(conn, "release-test", session_start)

        # Historical: 5 goals completed earlier in the session (pre-preflight)
        for i in range(5):
            _insert_goal(
                conn,
                f"old{i}",
                "release-test",
                "completed",
                session_start + 100,
                preflight - 100,
            )
        # In-transaction: 1 goal completed
        _insert_goal(
            conn,
            "new1",
            "release-test",
            "completed",
            preflight - 10,
            now - 30,
        )

        collector = _make_collector(conn, "release-test", preflight_timestamp=preflight)
        items = collector._collect_triage_metrics()

        goals_item = next((i for i in items if i.metric_name == "goals_completed"), None)
        assert goals_item is not None
        # Should count only the 1 in-transaction goal, not 6 total
        assert goals_item.raw_value["completed"] == 1
        # Score should be 0.7 (transaction scale), not 0.4 (session scale with 1)
        # or 1.0 (session scale with 6)
        assert goals_item.value == pytest.approx(0.7, abs=0.01)
