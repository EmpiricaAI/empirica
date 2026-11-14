"""An invalidated dead end does not come back at session start (1.16.3 sweep).

The MEMORY.md focus block skipped dead ends and mistakes marked invalidated (they no longer constrain anything). When that block
was removed (goal ea3335ec), the session-start circle and post-compact became the only readers, and neither filtered them.
"""

from __future__ import annotations

from empirica.core.bootstrap.circles import circle_1_active_state
from empirica.data.session_database import SessionDatabase


def test_an_invalidated_dead_end_is_not_in_the_session_start_picture(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    (project / ".empirica" / "sessions").mkdir(parents=True)
    db_path = str(project / ".empirica" / "sessions" / "sessions.db")
    monkeypatch.setenv("EMPIRICA_SESSION_DB", db_path)
    db = SessionDatabase(db_path=db_path)
    pid = db.create_project(name="p")
    sid = db.create_session(ai_id="t", project_id=pid)
    live = db.log_dead_end(pid, sid, "still a dead end", "because")
    gone = db.log_dead_end(pid, sid, "no longer true", "the dependency changed")
    db.conn.execute("UPDATE project_dead_ends SET is_invalidated = 1 WHERE id = ?", (gone,))
    db.conn.commit()

    out = circle_1_active_state(project, pid)

    ids = [d["id"] for d in out["recent_dead_ends"]]
    assert live in ids and gone not in ids
    db.close()


def test_post_compact_filters_them_too():
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent / "empirica/plugins/claude-code-integration/hooks/post-compact.py"
    ).read_text()
    i = src.index("FROM project_dead_ends\n            WHERE project_id = ?{inval}")
    assert "COALESCE(is_invalidated, 0) = 0" in src[i : i + 300]
