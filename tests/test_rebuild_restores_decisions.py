"""A rebuild from the git notes restores decisions, with their outcome (goal 25f4ab9a).

Same gap as assumptions (1e885685): the rebuild had no branch for decisions, and their notes held only the state at
creation, so a reversed decision could not be told from an unassessed one. Round trip in a throwaway repo: log two,
assess one through the real resolve verb, correct its regret through update-artifacts, rebuild from the notes alone.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import graph_commands as gc
from empirica.cli.command_handlers.sync_commands import _rebuild_decisions
from empirica.core.canonical.empirica_git.decision_store import GitDecisionStore
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


def _resolve(payload) -> dict:
    args = SimpleNamespace(config="-", schema=False, output="json", verbose=False, dry_run=False)
    stdout, stdin = sys.stdout, sys.stdin
    sys.stdin, sys.stdout = io.StringIO(json.dumps(payload)), io.StringIO()
    try:
        gc.handle_resolve_artifacts_command(args)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stdin = stdout, stdin


def _update(payload) -> dict:
    args = SimpleNamespace(config="-", schema=False, output="json", verbose=False, dry_run=False, project_id=None)
    stdout, stdin = sys.stdout, sys.stdin
    sys.stdin, sys.stdout = io.StringIO(json.dumps(payload)), io.StringIO()
    try:
        gc.handle_update_artifacts_command(args)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stdin = stdout, stdin


def test_a_decision_reversed_then_rebuilt_from_notes_comes_back_reversed(practice, tmp_path):
    out = gc.log_artifacts_graph(
        {"nodes": [
            {"ref": "a", "type": "decision", "data": {"choice": "use x", "rationale": "fast", "reversibility": "committal"}},
            {"ref": "b", "type": "decision", "data": {"choice": "use y", "rationale": "small", "reversibility": "exploratory"}},
        ], "edges": []},
        session_id="sess-1", project_id="proj-1", project_path=str(practice),
    )  # fmt: skip
    a, b = out["created"]["a"], out["created"]["b"]
    resolved = _resolve({"resolutions": [{"type": "decision", "id": a, "outcome": "reversed", "regret": 0.7}]})
    assert resolved.get("ok") is not False, resolved
    note = GitDecisionStore().load_decision(a)
    assert note["outcome"] == "reversed" and note.get("outcome_assessed_at") and note["regret_score"] == 0.7, note

    updated = _update({"updates": [{"type": "decision", "id": a, "regret_score": 0.4}]})
    assert not updated.get("errors"), updated
    assert GitDecisionStore().load_decision(a)["regret_score"] == 0.4

    fresh = SessionDatabase(db_path=str(tmp_path / "fresh.db"))
    fresh.conn.execute("PRAGMA foreign_keys=OFF")
    try:
        rebuilt: dict = {}
        _rebuild_decisions(fresh, rebuilt)
        rows = {
            r[0]: r[1:]
            for r in fresh.conn.execute(
                "SELECT id, choice, reversibility, outcome, regret_score, outcome_assessed_at IS NOT NULL FROM decisions"
            ).fetchall()
        }
    finally:
        fresh.close()
    assert rebuilt["decisions"] == 2
    assert tuple(rows[a]) == ("use x", "committal", "reversed", 0.4, 1)
    assert tuple(rows[b]) == ("use y", "exploratory", None, None, 0)
