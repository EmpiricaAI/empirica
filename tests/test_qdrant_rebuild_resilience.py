"""rebuild_qdrant_from_db must survive one bad/old-schema project DB.

The re-embed loop iterates every project in workspace.db. _embed_project_from_db
opens SessionDatabase (runs migrations) before its own try block, so an old
project DB missing a column (e.g. 'ai_id') raised uncaught and aborted every
remaining project — including a healthy live one queued after it. This is the
failure hit running `rebuild --qdrant-only` post-garden (the scatter left old
project DBs registered). The per-project guard isolates the bad one.
"""

from __future__ import annotations

import empirica.core.qdrant.rebuild as rb


def _fake_projects(tmp_path):
    projs = []
    for i in range(2):
        sessions = tmp_path / f"p{i}" / ".empirica" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / "sessions.db").write_text("")  # existence check only
        projs.append({"id": f"pid{i}", "name": f"proj{i}", "trajectory_path": str(sessions.parent)})
    return projs


def test_rebuild_survives_one_bad_project(tmp_path, monkeypatch):
    projs = _fake_projects(tmp_path)
    monkeypatch.setattr(rb, "_get_all_projects", lambda: projs)
    monkeypatch.setattr("empirica.core.qdrant.connection._check_qdrant_available", lambda: True)
    monkeypatch.setattr("empirica.core.qdrant.collections.recreate_project_collections", lambda pid: {"ok": True})
    monkeypatch.setattr("empirica.core.qdrant.collections.recreate_global_collections", lambda: {"ok": True})

    embedded: list[str] = []

    def fake_embed(pid, db_path, root):
        embedded.append(pid)
        if pid == "pid0":
            raise Exception("no such column: ai_id")  # old-schema project DB
        return {"embedded": 5}

    monkeypatch.setattr(rb, "_embed_project_from_db", fake_embed)

    result = rb.rebuild_qdrant_from_db()  # must NOT raise

    assert result["ok"] is True
    assert "pid1" in embedded, "healthy project queued after the bad one must still be processed"
    assert result["failed"] >= 1
    assert result["successful"] >= 1
    assert "Embed failed" in str(result["projects"]["proj0"]), "bad project's error is captured, not fatal"
