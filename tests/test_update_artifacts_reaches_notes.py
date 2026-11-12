"""update-artifacts corrections that a rebuild reads back reach the note, and only legal values are stored (1.16.0 sweep).

A goal's status changed through update-artifacts stayed in SQLite only, so a rebuild restored the old one; a decision
outcome such as "Upheld" was stored and restamped, then dropped by the rebuild, which reads only upheld/reversed/mixed.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import graph_commands as gc
from empirica.core.canonical.empirica_git.goal_store import GitGoalStore
from empirica.data.session_database import SessionDatabase


@pytest.fixture
def practice(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=tmp_path, check=True)
    (tmp_path / ".empirica" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / ".empirica" / "sessions" / "sessions.db"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _update(payload) -> dict:
    args = SimpleNamespace(config="-", schema=False, output="json", verbose=False, dry_run=False, project_id=None)
    stdout, stdin = sys.stdout, sys.stdin
    sys.stdin, sys.stdout = io.StringIO(json.dumps(payload)), io.StringIO()
    try:
        gc.handle_update_artifacts_command(args)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stdin = stdout, stdin


def test_a_goal_status_corrected_through_update_artifacts_reaches_its_note(practice):
    db = SessionDatabase()
    try:
        sid = db.create_session(ai_id="t")
        gid = db.create_goal(session_id=sid, objective="ship")
    finally:
        db.close()
    out = _update({"updates": [{"type": "goal", "id": gid, "status": "completed"}]})
    assert not out.get("errors"), out
    note = GitGoalStore().load_goal(gid)
    assert note["goal_data"]["status"] == "completed" and note["goal_data"]["is_completed"] == 1, note["goal_data"]


def test_a_decision_outcome_outside_the_vocabulary_is_refused(practice):
    created = gc.log_artifacts_graph(
        {"nodes": [{"ref": "d", "type": "decision", "data": {"choice": "x", "rationale": "y"}}], "edges": []},
        session_id="s", project_id="p", project_path=str(practice),
    )  # fmt: skip
    did = created["created"]["d"]
    out = _update({"updates": [{"type": "decision", "id": did, "outcome": "Upheld"}]})
    assert out.get("errors"), out
    db = SessionDatabase()
    try:
        assert db.conn.execute("SELECT outcome FROM decisions WHERE id = ?", (did,)).fetchone()[0] is None
    finally:
        db.close()


def test_corrected_metadata_reaches_the_notes(practice):
    # Each of these is correctable and read back by a rebuild; none reached the note, so a rebuild reverted it (a34eef6d).
    from empirica.core.canonical.empirica_git.assumption_store import GitAssumptionStore
    from empirica.core.canonical.empirica_git.decision_store import GitDecisionStore

    created = gc.log_artifacts_graph(
        {"nodes": [
            {"ref": "d", "type": "decision", "data": {"choice": "x", "rationale": "y", "reversibility": "exploratory"}},
            {"ref": "a", "type": "assumption", "data": {"assumption": "z", "confidence": 0.4}},
        ], "edges": []},
        session_id="s", project_id="p", project_path=str(practice),
    )  # fmt: skip
    did, aid = created["created"]["d"], created["created"]["a"]
    db = SessionDatabase()
    try:
        sid = db.create_session(ai_id="t")
        gid = db.create_goal(session_id=sid, objective="old objective")
    finally:
        db.close()
    out = _update(
        {
            "updates": [
                {"type": "decision", "id": did, "reversibility": "committal"},
                {"type": "assumption", "id": aid, "confidence": 0.9},
                {"type": "goal", "id": gid, "objective": "new objective"},
            ]
        }
    )
    assert not out.get("errors"), out
    assert GitDecisionStore().load_decision(did)["reversibility"] == "committal"
    assert GitAssumptionStore().load_assumption(aid)["confidence"] == 0.9
    assert GitGoalStore().load_goal(gid)["goal_data"]["objective"] == "new objective"
