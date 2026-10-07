"""POST /artifacts/log honours ?project_id / ?path like its sibling write routes.

Found by the 2026-10-06 pipeline sweep (U4): the route logged into the daemon's own project whatever the caller asked for, and
log_artifacts_graph opened SessionDatabase() with no path, so a batch aimed at another practice landed in this one.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from fastapi import HTTPException

from empirica.api.routes import artifacts as art

BODY = {
    "session_id": "s1",
    "nodes": [{"ref": "f", "type": "finding", "data": {"finding": "hello from the scoped route", "impact": 0.5}}],
    "edges": [],
}


def _project(tmp_path, name):
    root = tmp_path / name
    (root / ".empirica" / "sessions").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text(f"name: {name}\nproject_id: {name}-id\n")
    return root


def _findings(root):
    db = root / ".empirica" / "sessions" / "sessions.db"
    if not db.exists():
        return []
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute("SELECT finding FROM project_findings")]
    finally:
        conn.close()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def test_a_batch_aimed_at_a_project_lands_in_that_project_only(tmp_path, monkeypatch, home):
    target, daemon = _project(tmp_path, "target"), _project(tmp_path, "daemon")
    monkeypatch.setattr(
        art,
        "_resolve_project_dict",
        lambda project_id=None, path=None: {"project_id": "target-id", "project_path": str(target)},
    )
    monkeypatch.setattr(
        art, "get_cached_daemon_project", lambda: {"project_id": "daemon-id", "project_path": str(daemon)}
    )
    out = asyncio.run(art.post_artifacts_log(BODY, project_id="target-id", path=str(target)))
    assert out["ok"] is True and out["nodes_created"] == 1
    assert _findings(target) == ["hello from the scoped route"]
    assert _findings(daemon) == []
    assert list(home.iterdir()) == []  # nothing written under the (isolated) home either


def test_an_unresolvable_project_is_refused_not_logged_elsewhere(tmp_path, monkeypatch, home):
    def refuse(project_id=None, path=None):
        raise HTTPException(status_code=404, detail="no such project")

    monkeypatch.setattr(art, "_resolve_project_dict", refuse)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(art.post_artifacts_log(BODY, project_id="ghost", path=None))
    assert exc.value.status_code == 404
    assert list(home.iterdir()) == []
