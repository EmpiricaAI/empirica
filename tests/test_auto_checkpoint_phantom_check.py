"""An automatic checkpoint is not a CHECK assessment, and a manual one carries the real vectors.

ecodex (prop_u3xoxm2f3vdybgg5e2snmvb5ma), from David's report via ecodex-lab: 1.95 s after a real CHECK
row (uncertainty 0.60, reasoning, transaction) came a second CHECK-phase row with every vector 0.5,
no reasoning and no transaction. `checkpoint-create` passed `get_latest_vectors()`'s whole record (which
nests the vectors under a `vectors` key) as the vectors, so every lookup missed and defaulted to 0.5,
and `save_to_sqlite` wrote that as a CHECK row. The Sentinel and other readers take the latest CHECK
row for a session, so a phantom one is read as the practitioner's current state.

Two defects, each tested: the unwrap, and the auto-checkpoint writing a CHECK row at all (the CHECK that
triggered it already wrote its own).
"""

from __future__ import annotations

import json
import subprocess
import types

import pytest

from empirica.cli.command_handlers.checkpoint_commands import handle_checkpoint_create_command
from empirica.data.session_database import SessionDatabase

REAL = {
    "engagement": 0.9,
    "know": 0.8,
    "do": 0.7,
    "context": 0.75,
    "clarity": 0.85,
    "coherence": 0.8,
    "signal": 0.7,
    "density": 0.6,
    "state": 0.65,
    "change": 0.2,
    "completion": 0.1,
    "impact": 0.55,
    "uncertainty": 0.6,
}


@pytest.fixture
def session(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    (repo / "f").write_text("x")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "i"], cwd=repo, check=True, capture_output=True)
    monkeypatch.chdir(repo)
    dbp = tmp_path / "sessions.db"
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(dbp))
    db = SessionDatabase(db_path=str(dbp))
    sid = db.create_session(ai_id="t")
    db.store_vectors(sid, "CHECK", REAL, reasoning="the real CHECK", transaction_id="tx-real")
    db.close()
    return sid, dbp


def _rows(dbp, sid):
    import sqlite3

    con = sqlite3.connect(dbp)
    try:
        return con.execute(
            "SELECT phase, know, uncertainty, reasoning, transaction_id FROM reflexes WHERE session_id = ? ORDER BY id",
            (sid,),
        ).fetchall()
    finally:
        con.close()


def _args(sid, metadata=None):
    return types.SimpleNamespace(
        session_id=sid,
        phase="CHECK",
        round=1,
        metadata=json.dumps(metadata) if metadata is not None else None,
    )


def test_an_auto_checkpoint_does_not_write_a_second_check_row(session):
    sid, dbp = session

    handle_checkpoint_create_command(_args(sid, {"auto_checkpoint": True, "reason": "risky_decision"}))

    assert _rows(dbp, sid) == [("CHECK", 0.8, 0.6, "the real CHECK", "tx-real")]


def test_the_latest_check_after_an_auto_checkpoint_is_still_the_real_one(session):
    sid, dbp = session
    handle_checkpoint_create_command(_args(sid, {"auto_checkpoint": True}))

    db = SessionDatabase(db_path=str(dbp))
    try:
        latest = db.get_latest_vectors(sid, phase="CHECK")
    finally:
        db.close()

    assert latest["vectors"]["know"] == 0.8 and latest["vectors"]["uncertainty"] == 0.6


def test_a_manual_checkpoint_carries_the_real_vectors_not_the_default(session):
    """The unwrap. A manual checkpoint is an explicit act and still writes its row."""
    sid, dbp = session

    handle_checkpoint_create_command(_args(sid, {"note": "by hand"}))

    rows = _rows(dbp, sid)
    assert len(rows) == 2
    phase, know, uncertainty, _, _ = rows[1]
    assert (phase, know, uncertainty) == ("CHECK", 0.8, 0.6), "the vectors must be the latest record's, not 0.5"


def test_an_auto_checkpoint_still_leaves_its_git_note(session):
    """Skipping the reflexes row must not lose the checkpoint itself."""
    sid, _ = session

    handle_checkpoint_create_command(_args(sid, {"auto_checkpoint": True}))

    refs = subprocess.run(["git", "for-each-ref", "refs/notes"], capture_output=True, text=True)
    assert refs.stdout.strip(), "the checkpoint's git note must still be written"
