"""Batch resolve must not manufacture confirmation.

Found by the 2026-10-06 pipeline sweep (U4): POST /artifacts/resolve set every assumption to 'verified', while PATCH
/artifacts/{id}/resolve and the CLI batch path default to 'falsified' (a ruling that defaulting to verified writes false confirmations
into calibration data). An assumption now resolves as falsified unless its item says `verified: true`. David's ruling 2026-10-06.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from fastapi import HTTPException

from empirica.api.routes import artifacts as art


def _setup(tmp_path, monkeypatch):
    db_dir = tmp_path / ".empirica" / "sessions"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "sessions.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE assumptions (id TEXT PRIMARY KEY, assumption TEXT, confidence REAL, "
        "status TEXT, project_id TEXT, resolved_timestamp REAL, created_timestamp REAL)"
    )
    for aid in ("a1", "a2", "a3"):
        conn.execute("INSERT INTO assumptions VALUES (?, 'x', 0.5, 'unverified', 'P', NULL, 1.0)", (aid,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        art,
        "_resolve_project_dict",
        lambda project_id=None, path=None: {"project_id": "P", "project_path": str(tmp_path)},
    )
    return db_path


def _status(db_path, aid):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT status FROM assumptions WHERE id = ?", (aid,)).fetchone()[0]
    finally:
        conn.close()


def _resolve(body):
    return asyncio.run(art.post_artifacts_resolve(body, project_id=None, path=None))


def test_ids_form_resolves_an_assumption_as_falsified(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    _resolve({"ids": ["a1"]})
    assert _status(db, "a1") == "falsified"


def test_items_form_without_a_verdict_is_falsified_and_verified_true_is_honoured(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    out = _resolve({"items": [{"id": "a1"}, {"id": "a2", "verified": True}, {"id": "a3", "verified": "yes"}]})
    assert [_status(db, a) for a in ("a1", "a2", "a3")] == [
        "falsified",
        "verified",
        "falsified",
    ]  # only a literal True verifies
    assert [r["status"] for r in out["results"]] == ["falsified", "verified", "falsified"]


def test_ids_win_over_items_when_both_are_sent_as_before(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    _resolve({"ids": ["a1"], "items": [{"id": "a2", "verified": True}]})
    assert (_status(db, "a1"), _status(db, "a2")) == ("falsified", "unverified")


def test_an_item_without_an_id_is_reported_not_dropped(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = _resolve({"items": [{"verified": True}, {"id": "a1"}]})
    assert out["results"][0] == {"outcome": "missing_id"}
    assert out["results"][1]["outcome"] == "resolved"


def test_an_empty_body_is_still_rejected(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _resolve({})
    assert exc.value.status_code == 422
