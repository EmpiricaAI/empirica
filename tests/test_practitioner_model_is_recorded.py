"""A calibration row says which model it graded, not only which practice.

David's ruling, 2026-09-21: calibration accrues to the practitioner, artifacts
to the practice. `ai_id` names the practice. The model is read from the Claude
Code transcript when a transaction closes, because it can change inside one
session. Every transcript here is built under tmp_path.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from empirica.cli.command_handlers import _workflow_postflight as wp
from empirica.data.migrations.migrations import migration_074_practitioner_model
from empirica.utils import practitioner_model as pm

SESSION = "11111111-2222-3333-4444-555555555555"


def _transcript(home, lines, session=SESSION, project="-home-someone-proj"):
    folder = home / ".claude" / "projects" / project
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session}.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return path


def _assistant(model):
    return {"type": "assistant", "message": {"model": model, "content": []}}


def test_the_last_assistant_line_wins_because_the_model_can_change(tmp_path):
    _transcript(tmp_path, [_assistant("model-a"), {"type": "user"}, _assistant("model-b"), {"type": "user"}])
    assert pm.current_practitioner_model(SESSION, home=tmp_path) == "model-b"


def test_a_synthetic_line_is_not_a_practitioner(tmp_path):
    _transcript(tmp_path, [_assistant("model-a"), _assistant("<synthetic>")])
    assert pm.current_practitioner_model(SESSION, home=tmp_path) == "model-a"


def test_unknown_is_none_never_a_default(tmp_path):
    assert pm.current_practitioner_model(SESSION, home=tmp_path) is None  # no transcript
    assert pm.current_practitioner_model(None, home=tmp_path) is None
    _transcript(tmp_path, [{"type": "user"}])
    assert pm.current_practitioner_model(SESSION, home=tmp_path) is None  # no assistant line yet


def test_a_session_id_cannot_walk_out_of_the_projects_folder(tmp_path):
    assert pm.transcript_path("../../etc/passwd", home=tmp_path) is None


def test_a_cut_first_line_in_the_tail_is_skipped(tmp_path):
    path = _transcript(tmp_path, [_assistant("model-a")])
    path.write_text('{"type": "assistant", "message": {"mod' + "\n" + path.read_text())
    assert pm.model_from_transcript(path) == "model-a"


def _calibration_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE calibration_trajectory (point_id TEXT, transaction_id TEXT)")
    conn.execute("CREATE TABLE grounded_verifications (verification_id TEXT, transaction_id TEXT)")
    migration_074_practitioner_model(conn.cursor())
    migration_074_practitioner_model(conn.cursor())  # idempotent
    conn.executemany("INSERT INTO calibration_trajectory VALUES (?, ?, NULL)", [("p1", "tx-1"), ("p2", "tx-other")])
    conn.execute("INSERT INTO grounded_verifications VALUES ('v1', 'tx-1', NULL)")
    return SimpleNamespace(conn=conn)


def test_postflight_stamps_only_its_own_transactions_rows(tmp_path, monkeypatch):
    import empirica.utils.session_resolver as sr

    _transcript(tmp_path, [_assistant("model-b")])
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sr, "get_claude_session_id", lambda: SESSION)
    db = _calibration_db()

    assert wp._stamp_practitioner_model(db, "tx-1") == "model-b"
    rows = dict(db.conn.execute("SELECT point_id, practitioner_model FROM calibration_trajectory"))
    assert rows == {"p1": "model-b", "p2": None}
    assert db.conn.execute("SELECT practitioner_model FROM grounded_verifications").fetchone()[0] == "model-b"


def test_no_transcript_leaves_the_rows_null_and_says_nothing_false(tmp_path, monkeypatch):
    import empirica.utils.session_resolver as sr

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sr, "get_claude_session_id", lambda: SESSION)
    db = _calibration_db()
    assert wp._stamp_practitioner_model(db, "tx-1") is None
    assert (
        db.conn.execute("SELECT count(*) FROM calibration_trajectory WHERE practitioner_model IS NOT NULL").fetchone()[
            0
        ]
        == 0
    )


def test_a_store_without_the_column_warns_and_does_not_raise(tmp_path, monkeypatch, caplog):
    import logging

    import empirica.utils.session_resolver as sr

    _transcript(tmp_path, [_assistant("model-b")])
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sr, "get_claude_session_id", lambda: SESSION)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE calibration_trajectory (point_id TEXT, transaction_id TEXT)")
    with caplog.at_level(logging.WARNING):
        assert wp._stamp_practitioner_model(SimpleNamespace(conn=conn), "tx-1") is None
    assert "practitioner model not recorded" in caplog.text
