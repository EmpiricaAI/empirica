"""CHECK Stage 13.6 — the blindspot advisory.

ADVISORY ONLY: attaches result['intent_gaps'], never touches the decision (a
blindspot is a prediction, not a measured fact), persists fresh candidates
(deduped), and is fail-open.
"""

from __future__ import annotations

import sqlite3
import types

from empirica.cli.command_handlers import _workflow_check as wc
from empirica.cli.command_handlers import _workflow_shared as ws
from empirica.data.migrations.migrations import migration_053_blindspot_events


def _fake_db(goal_tree):
    conn = sqlite3.connect(":memory:")
    migration_053_blindspot_events(conn.cursor())
    conn.commit()
    return types.SimpleNamespace(conn=conn, goals=types.SimpleNamespace(get_goal_tree=lambda sid: goal_tree))


def _tree(status="in_progress", findings=None):
    return [
        {
            "goal_id": "g",
            "objective": "Ship X",
            "status": status,
            "subtasks": [
                {
                    "subtask_id": "s1",
                    "description": "assess X",
                    "status": "pending",
                    "findings": findings or [],
                    "unknowns": [],
                    "dead_ends": [],
                }
            ],
        }
    ]


def test_surfaces_advisory_without_touching_decision(monkeypatch):
    db = _fake_db(_tree())
    monkeypatch.setattr(ws, "_get_db_for_session", lambda sid: db)
    result = {"decision": "proceed"}
    wc._check_surface_blindspots(result, "sess", "tx1")
    assert "intent_gaps" in result
    assert len(result["intent_gaps"]["predicted"]) == 1
    assert result["decision"] == "proceed"  # NEVER overridden — advisory only
    assert list(db.conn.execute("SELECT subtask_id, surfaced_at FROM blindspot_events")) == [("s1", "check")]


def test_no_gaps_no_advisory(monkeypatch):
    db = _fake_db(_tree(findings=["covered"]))  # covered → not a gap
    monkeypatch.setattr(ws, "_get_db_for_session", lambda sid: db)
    result = {"decision": "proceed"}
    wc._check_surface_blindspots(result, "sess", "tx1")
    assert "intent_gaps" not in result


def test_planned_goal_not_surfaced(monkeypatch):
    db = _fake_db(_tree(status="planned"))  # dormant → excluded by active_only
    monkeypatch.setattr(ws, "_get_db_for_session", lambda sid: db)
    result = {"decision": "proceed"}
    wc._check_surface_blindspots(result, "sess", "tx1")
    assert "intent_gaps" not in result


def test_persist_deduped_across_checks(monkeypatch):
    db = _fake_db(_tree())
    monkeypatch.setattr(ws, "_get_db_for_session", lambda sid: db)
    r = {"decision": "proceed"}
    wc._check_surface_blindspots(r, "sess", "tx1")
    wc._check_surface_blindspots(r, "sess", "tx1")  # second CHECK, same gap
    assert db.conn.execute("SELECT COUNT(*) FROM blindspot_events").fetchone()[0] == 1  # not duplicated


def test_fail_open_never_raises_or_blocks(monkeypatch):
    def boom(sid):
        raise RuntimeError("db locked")

    monkeypatch.setattr(ws, "_get_db_for_session", boom)
    result = {"decision": "proceed"}
    wc._check_surface_blindspots(result, "sess", "tx1")  # must not raise
    assert "intent_gaps" not in result
    assert result["decision"] == "proceed"
