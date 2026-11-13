"""project-switch attaches the calling conversation's session, never a stale one (goal 8c0b1cd2).

2026-11-13 17:27, core's pane: `project-switch empirica` attached an April session (ai_id claude-code) because the registry
ordered active rows by last_activity DESC NULLS LAST (205 rows never closed, 16 with last_activity), then bumped its
last_activity so it won again; the pane file was stamped with outreach's Claude id, borrowed by reverse lookup, and the next
SessionStart reported a false instance clash. The CLI runs inside Claude Code's Bash tool, which exports
CLAUDE_CODE_SESSION_ID: the conversation is known, so its own session comes first.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

import empirica.cli.command_handlers.project_commands as pc


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(pc.Path, "home", classmethod(lambda cls: tmp_path))
    ws = tmp_path / ".empirica" / "workspace"
    ws.mkdir(parents=True)
    con = sqlite3.connect(ws / "workspace.db")
    con.execute(
        "CREATE TABLE global_sessions (session_id TEXT PRIMARY KEY, ai_id TEXT NOT NULL, origin_project_id TEXT,"
        " current_project_id TEXT, instance_id TEXT, status TEXT DEFAULT 'active', created_at REAL NOT NULL,"
        " last_activity REAL, closed_at REAL, parent_session_id TEXT, metadata TEXT)"
    )
    con.commit()
    con.close()
    import empirica.core.statusline_cache as slc
    import empirica.data.repositories.sessions as sess

    monkeypatch.setattr(slc, "get_instance_id", lambda: "tmux_6")
    bumped = []
    monkeypatch.setattr(sess, "update_session_project", lambda sid, pid: bumped.append(sid))
    return tmp_path


def _row(home, sid, created, last=None, status="active", instance="tmux_6", ai_id="empirica"):
    con = sqlite3.connect(home / ".empirica" / "workspace" / "workspace.db")
    con.execute(
        "INSERT INTO global_sessions (session_id, ai_id, instance_id, status, created_at, last_activity) VALUES (?,?,?,?,?,?)",
        (sid, ai_id, instance, status, created, last),
    )
    con.commit()
    con.close()


def test_a_newer_session_beats_an_old_one_that_merely_has_last_activity(home):
    _row(home, "april", created=100.0, last=150.0, ai_id="claude-code")
    _row(home, "today", created=200.0, last=None)
    attached = pc._attach_session_from_global_registry("p", None, "json")
    assert attached["session_id"] == "today"


def test_the_calling_conversation_s_own_session_comes_first(home):
    _row(home, "mine", created=100.0)
    _row(home, "newest-in-pane", created=300.0, last=310.0)
    (home / ".empirica" / "active_work_conv-1.json").write_text(json.dumps({"empirica_session_id": "mine"}))
    attached = pc._attach_session_from_global_registry("p", None, "json", claude_session_id="conv-1")
    assert attached["session_id"] == "mine"


def test_an_own_session_that_was_closed_falls_back_to_the_registry(home):
    _row(home, "mine", created=100.0, status="closed")
    _row(home, "live", created=200.0)
    (home / ".empirica" / "active_work_conv-1.json").write_text(json.dumps({"empirica_session_id": "mine"}))
    attached = pc._attach_session_from_global_registry("p", None, "json", claude_session_id="conv-1")
    assert attached["session_id"] == "live"


def test_the_caller_s_conversation_comes_from_the_flag_then_the_environment(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "from-env")
    assert pc._caller_claude_session_id(SimpleNamespace(claude_session_id="from-flag")) == "from-flag"
    assert pc._caller_claude_session_id(SimpleNamespace(claude_session_id=None)) == "from-env"
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    assert pc._caller_claude_session_id(SimpleNamespace()) is None


def test_the_pane_file_takes_the_new_session_ids(home):
    tty = home / ".empirica" / "tty_sessions"
    tty.mkdir(parents=True)
    (tty / "pts-9.json").write_text(json.dumps({"empirica_session_id": "stale-outreach", "claude_session_id": "other"}))
    pc._write_marker_files(home / ".empirica", "/work/empirica", "empirica", None, "pts-9", "conv-1", "mine")
    data = json.loads((tty / "pts-9.json").read_text())
    assert (data["empirica_session_id"], data["claude_session_id"]) == ("mine", "conv-1")
