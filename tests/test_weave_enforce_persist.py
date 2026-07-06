"""weave_enforce_events persistence (migration 052).

Durable telemetry for the artifact-graph gate: every CHECK weave verdict is
recorded — the source for enforcement-report block-rate / self-resolve-rate and
the consecutive-miss history adaptive `patience` consumes. Writes are FAIL-OPEN:
a persistence error must never affect the CHECK decision (the CHECK path is
fleet-critical since enforce-by-default shipped).
"""

from __future__ import annotations

import sqlite3
import types

from empirica.cli.command_handlers import _workflow_check as wc
from empirica.cli.command_handlers import _workflow_shared as ws
from empirica.data.migrations.migrations import migration_052_weave_enforce_events


def _migrated_conn():
    conn = sqlite3.connect(":memory:")
    migration_052_weave_enforce_events(conn.cursor())
    conn.commit()
    return conn


def test_migration_creates_table_with_expected_columns():
    conn = _migrated_conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(weave_enforce_events)")}
    assert {
        "session_id",
        "transaction_id",
        "created_timestamp",
        "connectivity_ratio",
        "connectivity_floor",
        "strictness",
        "response_band",
        "enforced",
        "decision_in",
        "decision_out",
    } <= cols


def test_migration_is_idempotent():
    conn = _migrated_conn()
    migration_052_weave_enforce_events(conn.cursor())  # second run must not raise
    conn.commit()


def test_persist_weave_event_inserts_row(monkeypatch):
    conn = _migrated_conn()
    monkeypatch.setattr(ws, "_get_db_for_session", lambda sid: types.SimpleNamespace(conn=conn))
    block = {
        "connected_ratio": 0.2,
        "response": "enforce",
        "enforced": True,
        "scalars": {"connectivity_floor": 0.34, "strictness": 0.75},
    }
    wc._persist_weave_event("s1", "tx1", block, "proceed", "investigate")
    rows = list(
        conn.execute(
            "SELECT session_id, transaction_id, connectivity_ratio, connectivity_floor, "
            "strictness, response_band, enforced, decision_in, decision_out FROM weave_enforce_events"
        )
    )
    assert rows == [("s1", "tx1", 0.2, 0.34, 0.75, "enforce", 1, "proceed", "investigate")]


def test_persist_is_fail_open_on_missing_table(monkeypatch):
    conn = sqlite3.connect(":memory:")  # un-migrated → table absent
    monkeypatch.setattr(ws, "_get_db_for_session", lambda sid: types.SimpleNamespace(conn=conn))
    wc._persist_weave_event("s1", "tx1", {"scalars": {}}, "proceed", "proceed")  # must not raise


def test_persist_is_fail_open_on_db_error(monkeypatch):
    def boom(sid):
        raise RuntimeError("db locked")

    monkeypatch.setattr(ws, "_get_db_for_session", boom)
    wc._persist_weave_event("s1", "tx1", {"scalars": {}}, "proceed", "proceed")  # must not raise


def test_apply_weave_enforce_persists_the_verdict(monkeypatch):
    conn = _migrated_conn()
    monkeypatch.setattr(ws, "_get_db_for_session", lambda sid: types.SimpleNamespace(conn=conn))
    monkeypatch.setattr(
        ws,
        "_weave_enforcement_block",
        lambda sid, tx: {
            "connected_ratio": 0.0,
            "response": "enforce",
            "enforced": True,
            "scalars": {"connectivity_floor": 0.34, "strictness": 0.75},
            "note": "MUST weave more",
        },
    )
    result = {"decision": "proceed"}
    d = wc._check_apply_weave_enforce(result, "proceed", "s1", "tx1")
    assert d == "investigate"  # enforce override
    rows = list(conn.execute("SELECT decision_in, decision_out, enforced FROM weave_enforce_events"))
    assert rows == [("proceed", "investigate", 1)]
