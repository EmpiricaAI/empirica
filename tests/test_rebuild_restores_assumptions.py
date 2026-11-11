"""A rebuild from the git notes restores assumptions, with their outcome (goal 1e885685).

The rebuild had no branch for assumptions, so it lost every one, and their notes held only the state at creation, so even
a rebuild could not have told a verified assumption from an open one. Round trip in a throwaway repo: log one with a due
date, verify it through the real resolve verb, rebuild into a fresh database from the notes alone.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import graph_commands as gc
from empirica.cli.command_handlers.sync_commands import _rebuild_assumptions
from empirica.core.canonical.empirica_git.assumption_store import GitAssumptionStore
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


def test_an_assumption_verified_then_rebuilt_from_notes_comes_back_verified(practice, tmp_path):
    out = gc.log_artifacts_graph(
        {"nodes": [
            {"ref": "a", "type": "assumption", "data": {"assumption": "y holds", "confidence": 0.6, "due": "2026-12-02"}},
            {"ref": "b", "type": "assumption", "data": {"assumption": "z holds", "confidence": 0.4}},
        ], "edges": []},
        session_id="sess-1", project_id="proj-1", project_path=str(practice),
    )  # fmt: skip
    a, b = out["created"]["a"], out["created"]["b"]
    resolved = _resolve({"resolutions": [{"type": "assumption", "id": a, "resolution": "held", "verified": True}]})
    assert resolved.get("ok") is not False, resolved
    note = GitAssumptionStore().load_assumption(a)
    assert note["status"] == "verified" and note.get("resolved_timestamp"), note

    fresh = SessionDatabase(db_path=str(tmp_path / "fresh.db"))
    fresh.conn.execute("PRAGMA foreign_keys=OFF")
    try:
        rebuilt: dict = {}
        _rebuild_assumptions(fresh, rebuilt)
        rows = {
            r[0]: r[1:]
            for r in fresh.conn.execute("SELECT id, assumption, status, confidence, due_at FROM assumptions").fetchall()
        }
    finally:
        fresh.close()
    assert rebuilt["assumptions"] == 2
    assert tuple(rows[a]) == ("y holds", "verified", 0.6, "2026-12-02")
    assert tuple(rows[b]) == ("z holds", "unverified", 0.4, None)
