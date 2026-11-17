"""project-switch never takes another conversation's session (1.16.2 pre-publish sweep B4, goal 8e9ca487).

With no session of its own and no instance id, the switch attached the NEWEST active session in the registry, whichever
conversation it belonged to, moved that session to the target project and wrote it into the caller's pane files. It now attaches
nothing in that case; the calling conversation's own session and the pane's instance still come first.
"""

from __future__ import annotations

import sqlite3
import time

from empirica.cli.command_handlers import project_commands as pc


def _registry(home):
    db = home / ".empirica" / "workspace" / "workspace.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    pc.ensure_workspace_schema(conn)
    conn.execute(
        "INSERT INTO global_sessions (session_id, ai_id, origin_project_id, current_project_id, instance_id, status,"
        " created_at, last_activity) VALUES ('someone-else', 'other', 'p-other', 'p-other', 'tmux_9', 'active', ?, ?)",
        (time.time(), time.time()),
    )
    conn.commit()
    return conn


def test_no_own_session_and_no_instance_attaches_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    conn = _registry(tmp_path)
    monkeypatch.setattr("empirica.core.statusline_cache.get_instance_id", lambda: None)

    assert pc._attach_session_from_global_registry("p-target", None, "json", claude_session_id=None) is None
    (project,) = conn.execute(
        "SELECT current_project_id FROM global_sessions WHERE session_id = 'someone-else'"
    ).fetchone()
    assert project == "p-other", "another conversation's session was moved to this switch's project"


def test_the_pane_s_own_instance_is_still_used(tmp_path, monkeypatch):  # control
    monkeypatch.setenv("HOME", str(tmp_path))
    _registry(tmp_path)
    monkeypatch.setattr("empirica.core.statusline_cache.get_instance_id", lambda: "tmux_9")
    got = pc._attach_session_from_global_registry("p-target", None, "json", claude_session_id=None)
    assert got and got["session_id"] == "someone-else"


def test_an_instance_shared_by_several_active_sessions_attaches_none(tmp_path, monkeypatch):
    """Goal 56f557b6: an instance id carried by several conversations (EMPIRICA_INSTANCE_ID set to a practice name, a
    reused tmux pane) made the switch attach and move whichever of them was newest."""
    monkeypatch.setenv("HOME", str(tmp_path))
    conn = _registry(tmp_path)
    conn.execute(
        "INSERT INTO global_sessions (session_id, ai_id, origin_project_id, current_project_id, instance_id, status,"
        " created_at, last_activity) VALUES ('another', 'other', 'p-x', 'p-x', 'tmux_9', 'active', ?, ?)",
        (time.time(), time.time() + 1),  # both active within minutes of each other
    )
    conn.commit()
    monkeypatch.setattr("empirica.core.statusline_cache.get_instance_id", lambda: "tmux_9")
    assert pc._attach_session_from_global_registry("p-target", None, "json", claude_session_id=None) is None
    moved = conn.execute("SELECT count(*) FROM global_sessions WHERE current_project_id = 'p-target'").fetchone()[0]
    assert moved == 0
