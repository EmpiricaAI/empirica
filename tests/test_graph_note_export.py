"""Links between artifacts reach the git notes and come back on a rebuild (1.16.x, goal 966808c0).

artifact_edges lived in SQLite only, so a rebuild from the notes lost every link. An exporter run at each POSTFLIGHT (and
in full by doctor --reconcile-notes) writes one note per source artifact, empirica/edges/<from_id>, listing its edges; the
rebuild reads them back.
"""

from __future__ import annotations

import subprocess

import pytest

from empirica.cli.command_handlers.sync_commands import _rebuild_edges
from empirica.core.canonical.empirica_git import graph_note_export as gx
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


def _edge(db, src, dst, rel, at, meta=None):
    db.conn.execute(
        "INSERT INTO artifact_edges (from_id, to_id, relation, created_at, metadata) VALUES (?, ?, ?, ?, ?)",
        (src, dst, rel, at, meta),
    )
    db.conn.commit()


def test_links_exported_then_rebuilt_come_back(practice, tmp_path):
    db = SessionDatabase()
    try:
        _edge(db, "f1", "f2", "evidence", "2026-11-01 10:00:00")
        _edge(db, "f1", "g1", "attached_to", "2026-11-01 10:00:01")
        _edge(db, "u1", "f1", "invalidates", "2026-11-02 09:00:00", '{"kind": "retracted"}')
        out = gx.export_edge_notes(db.conn, str(practice))
        assert out["written"] == 2  # two source artifacts
        _edge(db, "d1", "f1", "grounded_by", "2026-11-03 08:00:00")
        again = gx.export_edge_notes(db.conn, str(practice))
        assert again["written"] == 1  # only what changed since the watermark
    finally:
        db.close()

    fresh = SessionDatabase(db_path=str(tmp_path / "fresh.db"))
    try:
        rebuilt: dict = {}
        _rebuild_edges(fresh, rebuilt, str(practice))
        rows = sorted(
            tuple(r)
            for r in fresh.conn.execute("SELECT from_id, to_id, relation, metadata FROM artifact_edges").fetchall()
        )
    finally:
        fresh.close()
    assert rebuilt["edges"] == 4
    assert rows == [
        ("d1", "f1", "grounded_by", None),
        ("f1", "f2", "evidence", None),
        ("f1", "g1", "attached_to", None),
        ("u1", "f1", "invalidates", '{"kind": "retracted"}'),
    ]


def test_a_bounded_run_resumes_where_it_stopped(practice):
    db = SessionDatabase()
    try:
        for i in range(5):
            _edge(db, f"a{i}", "x", "related", f"2026-11-01 10:00:0{i}")
        assert gx.export_edge_notes(db.conn, str(practice), limit=2)["written"] == 2
        assert gx.export_edge_notes(db.conn, str(practice), limit=2)["written"] == 2
        assert gx.export_edge_notes(db.conn, str(practice), limit=2)["written"] == 1
        assert gx.export_edge_notes(db.conn, str(practice), limit=2)["written"] == 0
    finally:
        db.close()
