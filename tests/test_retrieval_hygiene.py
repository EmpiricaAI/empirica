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
    _reconcile_findings_against_sqlite,
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


# ── finding reconciliation (#307) ─────────────────────────────────────────────


def _make_findings_db(tmp_path, rows):
    """Build a temp project sessions.db with a minimal project_findings table.

    ``rows`` is a list of (id, finding, is_resolved) tuples.
    """
    root = tmp_path / "proj"
    db_dir = root / ".empirica" / "sessions"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(str(db_dir / "sessions.db"))
    conn.execute("CREATE TABLE project_findings (id TEXT PRIMARY KEY, finding TEXT, is_resolved INTEGER)")
    conn.executemany("INSERT INTO project_findings VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()
    return root


def test_finding_reconcile_drops_resolved_by_id(tmp_path, monkeypatch):
    root = _make_findings_db(tmp_path, [("r1", "resolved body", 1), ("l1", "live body", 0)])
    import empirica.data.session_database as sdb

    monkeypatch.setattr(sdb, "_resolve_canonical_project_root", lambda: str(root))
    raw = [{"artifact_id": "r1", "text": "resolved body"}, {"artifact_id": "l1", "text": "live body"}]
    out = _reconcile_findings_against_sqlite(raw)
    assert [f["artifact_id"] for f in out] == ["l1"]  # resolved dropped by id


def test_finding_reconcile_text_prefix_fallback(tmp_path, monkeypatch):
    # A finding embedded before #307 has no artifact_id in payload → match on text prefix.
    root = _make_findings_db(tmp_path, [("r1", "resolved body", 1)])
    import empirica.data.session_database as sdb

    monkeypatch.setattr(sdb, "_resolve_canonical_project_root", lambda: str(root))
    raw = [{"text": "resolved body"}, {"text": "some unrelated live finding"}]
    out = _reconcile_findings_against_sqlite(raw)
    assert [f["text"] for f in out] == ["some unrelated live finding"]  # resolved dropped by text prefix


def test_finding_reconcile_id_wins_over_text(tmp_path, monkeypatch):
    # A finding WITH an id that isn't resolved is kept even if its text collides with
    # a resolved finding's text — id is authoritative, text-fallback only when id absent.
    root = _make_findings_db(tmp_path, [("r1", "shared body", 1)])
    import empirica.data.session_database as sdb

    monkeypatch.setattr(sdb, "_resolve_canonical_project_root", lambda: str(root))
    raw = [{"artifact_id": "l1", "text": "shared body"}]  # different id, not resolved
    out = _reconcile_findings_against_sqlite(raw)
    assert [f["artifact_id"] for f in out] == ["l1"]  # kept: id not in resolved set


def test_finding_reconcile_fail_open_when_db_absent(tmp_path, monkeypatch):
    import empirica.data.session_database as sdb

    monkeypatch.setattr(sdb, "_resolve_canonical_project_root", lambda: str(tmp_path / "nope"))
    raw = [{"artifact_id": "f1", "text": "x"}]
    assert _reconcile_findings_against_sqlite(raw) == raw  # unchanged, never raises


def test_finding_reconcile_fail_open_when_column_absent(tmp_path, monkeypatch):
    # A DB predating migration_057 has no is_resolved column → keep raw, never raise.
    root = tmp_path / "proj"
    db_dir = root / ".empirica" / "sessions"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(str(db_dir / "sessions.db"))
    conn.execute("CREATE TABLE project_findings (id TEXT PRIMARY KEY, finding TEXT)")  # no is_resolved
    conn.commit()
    conn.close()
    import empirica.data.session_database as sdb

    monkeypatch.setattr(sdb, "_resolve_canonical_project_root", lambda: str(root))
    raw = [{"artifact_id": "f1", "text": "x"}]
    assert _reconcile_findings_against_sqlite(raw) == raw


def test_finding_reconcile_empty_is_noop():
    assert _reconcile_findings_against_sqlite([]) == []


# ── type-aware reconciliation + pre-fix text fallback (vacuous-reconciler fix) ─
#
# embed_goal never wrote goal_id into the payload, so ids was always empty and
# the reconciler vacuously passed every goal — the machinery was proven by the
# hand-built fixtures above (which all carry goal_id) while the live enumerator
# was empty. These pin the producer contract + the fallback for pre-fix points.


def test_subtask_dropped_when_parent_completed():
    raw = [{"goal_id": "g1", "type": "subtask", "status": "pending"}]
    assert _apply_goal_reconciliation(raw, {"g1": ("completed", True)}) == []


def test_subtask_status_never_rewritten_from_parent():
    # Parent open with a different status: the subtask keeps ITS OWN status —
    # an earlier form rewrote pending subtasks to the parent's in_progress.
    raw = [{"goal_id": "g1", "type": "subtask", "status": "pending"}]
    out = _apply_goal_reconciliation(raw, {"g1": ("in_progress", False)})
    assert out == [{"goal_id": "g1", "type": "subtask", "status": "pending"}]


def test_goal_without_id_dropped_by_objective_fallback():
    raw = [{"objective": "Ship the widget", "status": "in_progress"}]  # pre-fix point
    out = _apply_goal_reconciliation(raw, {}, completed_objectives={"ship the widget"})
    assert out == []


def test_goal_without_id_kept_when_objective_not_completed():
    raw = [{"objective": "Still open work", "status": "in_progress"}]
    out = _apply_goal_reconciliation(raw, {}, completed_objectives={"something else"})
    assert out == raw


def test_subtask_without_goal_id_never_text_matched():
    # The objective fallback is goals-only: a subtask row must not be dropped by
    # a text collision with a completed goal's objective.
    raw = [{"type": "subtask", "objective": "Ship the widget", "status": "pending"}]
    out = _apply_goal_reconciliation(raw, {}, completed_objectives={"ship the widget"})
    assert out == raw


def test_sqlite_wrapper_text_fallback_for_prefix_points(tmp_path, monkeypatch):
    """End-to-end: a pre-fix point (no goal_id in payload) whose objective matches
    a completed goal in SQLite is dropped — the empty-ids early return that made
    the reconciler vacuous is gone."""
    root = tmp_path / "proj"
    db_dir = root / ".empirica" / "sessions"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(str(db_dir / "sessions.db"))
    conn.execute("CREATE TABLE goals (id TEXT PRIMARY KEY, objective TEXT, status TEXT, is_completed INTEGER)")
    conn.executemany(
        "INSERT INTO goals VALUES (?,?,?,?)",
        [("g1", "Ship the widget", "completed", 1), ("g2", "Open thing", "in_progress", 0)],
    )
    conn.commit()
    conn.close()

    import empirica.data.session_database as sdb

    monkeypatch.setattr(sdb, "_resolve_canonical_project_root", lambda: str(root))

    raw = [
        {"objective": "Ship the widget", "status": "in_progress"},  # completed → drop
        {"objective": "Open thing", "status": "in_progress"},  # open → keep
    ]
    out = _reconcile_goals_against_sqlite(raw)
    assert [g["objective"] for g in out] == ["Open thing"]


def test_embed_goal_payload_carries_goal_id(monkeypatch):
    """Producer contract: the payload embed_goal writes MUST carry goal_id —
    the reconciler keys on it, and the md5 point_id is one-way."""

    import empirica.core.qdrant.goals as qg

    captured = {}

    class _FakeClient:
        def collection_exists(self, _c):
            return True

        def upsert(self, collection_name, points):
            captured["payload"] = points[0].payload

    class _PS:
        def __init__(self, id, vector, payload):
            self.id, self.vector, self.payload = id, vector, payload

    monkeypatch.setattr(qg, "_check_qdrant_available", lambda: True)
    monkeypatch.setattr(qg, "_get_qdrant_client", lambda: _FakeClient())
    monkeypatch.setattr(qg, "_get_qdrant_imports", lambda: (None, None, None, _PS))
    monkeypatch.setattr(qg, "_get_embedding_safe", lambda _t: [0.0] * 4)
    monkeypatch.setattr(qg, "_get_vector_size", lambda: 4)

    assert qg.embed_goal("proj", "goal-abc", "An objective") is True
    assert captured["payload"]["goal_id"] == "goal-abc"


def test_update_goal_status_normalizes_completed(monkeypatch):
    """'completed' (the SQL layer's spelling) must set is_completed=True — the
    exact-match on 'complete' silently recorded False for it. Also backfills
    goal_id into pre-fix payloads."""

    import empirica.core.qdrant.goals as qg

    captured = {}

    class _Point:
        def __init__(self):
            self.payload = {"status": "in_progress"}  # pre-fix payload: no goal_id
            self.vector = [0.0] * 4

    class _FakeClient:
        def collection_exists(self, _c):
            return True

        def retrieve(self, collection_name, ids, with_payload, with_vectors):
            return [_Point()]

        def upsert(self, collection_name, points):
            captured["payload"] = points[0].payload

    monkeypatch.setattr(qg, "_check_qdrant_available", lambda: True)
    monkeypatch.setattr(qg, "_get_qdrant_client", lambda: _FakeClient())

    assert qg.update_goal_status("proj", "goal-abc", "completed") is True
    assert captured["payload"]["is_completed"] is True
    assert captured["payload"]["goal_id"] == "goal-abc"  # backfilled
