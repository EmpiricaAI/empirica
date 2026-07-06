"""enforcement-report — artifact-graph enforce telemetry aggregation.

The health metric is self-resolve-rate: of the transactions the gate blocked, how
many recovered on their own (wove an edge → re-CHECK proceeded). These pin the
block-rate / self-resolve-rate math without a DB, plus the fail-open read.
"""

from __future__ import annotations

import sqlite3
import types

from empirica.cli.command_handlers.enforcement_report_commands import (
    _aggregate_weave_events,
    _read_weave_events,
)


def _row(txn, ts, enforced, decision_out, band="enforce", conn=0.0):
    return {
        "transaction_id": txn,
        "created_timestamp": ts,
        "connectivity_ratio": conn,
        "response_band": band,
        "enforced": enforced,
        "decision_out": decision_out,
    }


def test_empty_rows():
    s = _aggregate_weave_events([])
    assert s["total_verdicts"] == 0
    assert s["total_transactions"] == 0
    assert s["block_rate"] == 0.0
    assert s["self_resolve_rate"] is None
    assert s["avg_connectivity_ratio"] is None
    assert s["band_distribution"] == {}


def test_clean_transaction_never_blocks():
    s = _aggregate_weave_events([_row("t1", 1, 0, "proceed", conn=1.0)])
    assert s["blocked_transactions"] == 0
    assert s["block_rate"] == 0.0
    assert s["self_resolve_rate"] is None  # nothing blocked


def test_blocked_then_self_resolved():
    rows = [
        _row("t1", 1, 1, "investigate", conn=0.0),  # blocked
        _row("t1", 2, 0, "proceed", conn=0.5),  # wove → proceeded
    ]
    s = _aggregate_weave_events(rows)
    assert s["blocked_transactions"] == 1
    assert s["self_resolved_transactions"] == 1
    assert s["block_rate"] == 1.0
    assert s["self_resolve_rate"] == 1.0


def test_blocked_and_stalled_is_not_self_resolved():
    rows = [
        _row("t1", 1, 1, "investigate"),
        _row("t1", 2, 1, "investigate"),  # still blocked, never proceeded
    ]
    s = _aggregate_weave_events(rows)
    assert s["blocked_transactions"] == 1
    assert s["self_resolved_transactions"] == 0
    assert s["self_resolve_rate"] == 0.0


def test_mixed_two_transactions():
    rows = [
        _row("t1", 1, 1, "investigate"),
        _row("t1", 2, 0, "proceed"),  # t1: blocked → resolved
        _row("t2", 1, 0, "proceed"),  # t2: clean
    ]
    s = _aggregate_weave_events(rows)
    assert s["total_transactions"] == 2
    assert s["blocked_transactions"] == 1
    assert s["block_rate"] == 0.5
    assert s["self_resolve_rate"] == 1.0


def test_band_distribution_and_avg_connectivity():
    rows = [
        _row("t1", 1, 0, "proceed", band="report", conn=0.4),
        _row("t2", 1, 0, "proceed", band="enforce", conn=0.6),
        _row("t3", 1, 1, "investigate", band="enforce", conn=0.0),
    ]
    s = _aggregate_weave_events(rows)
    assert s["band_distribution"] == {"report": 1, "enforce": 2}
    assert s["avg_connectivity_ratio"] == round((0.4 + 0.6 + 0.0) / 3, 3)


def test_read_is_fail_open_on_missing_table():
    conn = sqlite3.connect(":memory:")  # no weave_enforce_events table
    db = types.SimpleNamespace(conn=conn)
    assert _read_weave_events(db, None) == []


def test_read_returns_rows_when_present():
    conn = sqlite3.connect(":memory:")
    from empirica.data.migrations.migrations import migration_052_weave_enforce_events

    migration_052_weave_enforce_events(conn.cursor())
    conn.execute(
        "INSERT INTO weave_enforce_events (session_id, transaction_id, created_timestamp, "
        "connectivity_ratio, response_band, enforced, decision_in, decision_out) "
        "VALUES ('s1','t1',1.0,0.2,'enforce',1,'proceed','investigate')"
    )
    conn.commit()
    db = types.SimpleNamespace(conn=conn)
    rows = _read_weave_events(db, None)
    assert len(rows) == 1 and rows[0]["transaction_id"] == "t1"
    # session filter
    assert _read_weave_events(db, "nope") == []
