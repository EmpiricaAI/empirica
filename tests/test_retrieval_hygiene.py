"""Tests for retrieval-hygiene goal reconciliation (pattern_retrieval).

The PREFLIGHT/CHECK teaser served goal ``status`` straight from the Qdrant point
payload (embedded at index time), so a goal completed in SQLite kept surfacing as
``in_progress``. The reconciliation drops live-completed goals, corrects stale
status on open goals, and keeps cross-project goals (absent locally) unchanged.
"""

from __future__ import annotations

import sqlite3

from empirica.core.qdrant.pattern_retrieval import (
    _apply_goal_reconciliation,
    _reconcile_goals_against_sqlite,
)

# ── pure reconciliation ───────────────────────────────────────────────────────


def test_drops_completed_by_is_completed():
    raw = [{"goal_id": "g1", "status": "in_progress"}]  # stale Qdrant payload
    assert _apply_goal_reconciliation(raw, {"g1": ("completed", True)}) == []


def test_drops_completed_by_status_even_if_flag_zero():
    raw = [{"goal_id": "g1", "status": "in_progress"}]
    assert _apply_goal_reconciliation(raw, {"g1": ("completed", False)}) == []


def test_corrects_stale_status_in_place():
    raw = [{"goal_id": "g1", "status": "in_progress"}]  # payload stale
    assert _apply_goal_reconciliation(raw, {"g1": ("planned", False)}) == [{"goal_id": "g1", "status": "planned"}]


def test_keeps_open_unchanged():
    raw = [{"goal_id": "g1", "status": "in_progress"}]
    assert _apply_goal_reconciliation(raw, {"g1": ("in_progress", False)}) == raw


def test_keeps_cross_project_absent_from_map():
    raw = [{"goal_id": "gX", "status": "in_progress"}]
    assert _apply_goal_reconciliation(raw, {}) == raw  # not local → keep (cross-project)


def test_keeps_row_without_goal_id():
    raw = [{"status": "in_progress"}]  # e.g. a subtask row missing goal_id
    assert _apply_goal_reconciliation(raw, {"g1": ("completed", True)}) == raw


def test_mixed_batch():
    raw = [
        {"goal_id": "done", "status": "in_progress"},
        {"goal_id": "open", "status": "in_progress"},
        {"goal_id": "stale", "status": "in_progress"},
        {"goal_id": "other", "status": "in_progress"},  # not in live map
    ]
    live = {"done": ("completed", True), "open": ("in_progress", False), "stale": ("blocked", False)}
    out = _apply_goal_reconciliation(raw, live)
    assert [g["goal_id"] for g in out] == ["open", "stale", "other"]  # 'done' dropped
    assert next(g for g in out if g["goal_id"] == "stale")["status"] == "blocked"  # corrected
    assert next(g for g in out if g["goal_id"] == "other")["status"] == "in_progress"  # cross-project kept


# ── sqlite wrapper (end-to-end against a temp project db) ─────────────────────


def test_reconcile_against_sqlite(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    db_dir = root / ".empirica" / "sessions"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(str(db_dir / "sessions.db"))
    conn.execute("CREATE TABLE goals (id TEXT PRIMARY KEY, status TEXT, is_completed INTEGER)")
    conn.executemany(
        "INSERT INTO goals VALUES (?,?,?)",
        [("done", "completed", 1), ("open", "in_progress", 0), ("stale", "blocked", 0)],
    )
    conn.commit()
    conn.close()

    import empirica.data.session_database as sdb

    monkeypatch.setattr(sdb, "_resolve_canonical_project_root", lambda: str(root))

    raw = [
        {"goal_id": "done", "status": "in_progress"},  # completed in db → drop
        {"goal_id": "stale", "status": "in_progress"},  # correct → blocked
        {"goal_id": "other", "status": "in_progress"},  # absent → keep
    ]
    out = _reconcile_goals_against_sqlite(raw)
    assert [g["goal_id"] for g in out] == ["stale", "other"]
    assert out[0]["status"] == "blocked"


def test_reconcile_fail_open_when_db_absent(tmp_path, monkeypatch):
    import empirica.data.session_database as sdb

    monkeypatch.setattr(sdb, "_resolve_canonical_project_root", lambda: str(tmp_path / "nope"))
    raw = [{"goal_id": "g1", "status": "in_progress"}]
    assert _reconcile_goals_against_sqlite(raw) == raw  # unchanged, never raises


def test_reconcile_empty_is_noop():
    assert _reconcile_goals_against_sqlite([]) == []
