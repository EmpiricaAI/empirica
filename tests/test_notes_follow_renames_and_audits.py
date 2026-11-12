"""A source's note follows its id swap, and the delete-artifacts audit decision has a note (1.16.x, goal a34eef6d)."""

from __future__ import annotations

import subprocess
import time

import pytest

from empirica.cli.command_handlers import graph_commands as gc
from empirica.cli.command_handlers import sources_reconcile_commands as src
from empirica.core.canonical.empirica_git.decision_store import GitDecisionStore
from empirica.core.canonical.empirica_git.source_store import GitSourceStore
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
    monkeypatch.setattr(src, "_swap_workspace_entity_links", lambda *a: "skipped")
    return tmp_path


def test_a_source_note_moves_with_its_id(practice):
    db = SessionDatabase()
    try:
        db.conn.execute(
            "INSERT INTO epistemic_sources (id, project_id, source_type, title, discovered_at, visibility) "
            "VALUES ('local-1', 'p', 'url', 'RFC', ?, 'local')",
            (time.time(),),
        )
        db.conn.commit()
        assert GitSourceStore().store_source(source_id="local-1", project_id="p", session_id="s", title="RFC")
        out = src._swap_source_id(db, "p", "local-1", "cortex-1")
    finally:
        db.close()
    assert out["swapped"] is True
    moved = GitSourceStore().load_source("cortex-1")
    assert moved and moved["source_id"] == "cortex-1" and moved["title"] == "RFC"
    assert GitSourceStore().load_source("local-1") is None


def test_the_deletion_audit_decision_has_a_note(practice):
    db = SessionDatabase()
    try:
        assert gc._log_deletion_decision(db.conn.cursor(), "p", "s", "deleted 3 test artifacts", "noise") == "recorded"
        db.conn.commit()
    finally:
        db.close()
    notes = GitDecisionStore().discover_decisions()
    assert [n["choice"] for n in notes] == ["deleted 3 test artifacts"]
