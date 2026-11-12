"""Defects the 1.16.1 pre-publish sweep confirmed, each pinned where it was found (goal c5687532)."""

from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import types

import pytest

from empirica.cli.command_handlers import provision_practice_commands as _pp

REAL_EMBED_STEP = _pp._embedding_model_step  # captured at import, before conftest's autouse stub


def test_a_tag_sharing_a_branch_name_still_fires(tmp_path):
    from empirica.data.due import _release_tags

    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "config", "commit.gpgsign", "false"],
        ["git", "config", "tag.gpgsign", "false"],
        ["git", "commit", "-q", "--allow-empty", "-m", "i"],
        ["git", "tag", "v9.0.0"],
        ["git", "branch", "v9.0.0"],
    ):
        subprocess.run(cmd, cwd=tmp_path, check=True)
    assert "v9.0.0" in _release_tags(str(tmp_path))  # refname:short gave tags/v9.0.0 here


def test_reconcile_reads_a_practice_registered_under_its_root(tmp_path, monkeypatch):
    from empirica.data import global_sessions as gs

    home = tmp_path / "home"
    (home / ".empirica" / "workspace").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    reg = sqlite3.connect(str(gs.workspace_db_path()))
    reg.executescript(
        "CREATE TABLE global_projects (id TEXT PRIMARY KEY, name TEXT, trajectory_path TEXT);"
        "CREATE TABLE global_sessions (session_id TEXT PRIMARY KEY, ai_id TEXT, origin_project_id TEXT, current_project_id TEXT,"
        " status TEXT DEFAULT 'active', created_at REAL, closed_at REAL);"
    )
    root = tmp_path / "legacy"
    (root / ".empirica" / "sessions").mkdir(parents=True)
    local = sqlite3.connect(str(root / ".empirica" / "sessions" / "sessions.db"))
    local.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, end_time TEXT)")
    local.execute("INSERT INTO sessions VALUES ('live', NULL)")
    local.commit()
    local.close()
    reg.execute("INSERT INTO global_projects VALUES ('p', 'legacy', ?)", (str(root),))  # the root, not <root>/.empirica
    reg.execute(
        "INSERT INTO global_sessions (session_id, ai_id, origin_project_id, current_project_id, created_at) VALUES ('live', 't', 'p', 'p', 1)"
    )
    reg.commit()
    plan = gs.plan_reconcile(reg)
    reg.close()
    assert plan["counts"]["live"] == 1 and plan["close"] == []


def test_migration_080_handles_a_quoted_table_name(tmp_path):
    from empirica.data.migrations.migrations import migration_080_assumption_expired_status

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE "assumptions" (id TEXT PRIMARY KEY, assumption TEXT, status TEXT NOT NULL DEFAULT 'unverified'
        CHECK(status IN ('unverified', 'verified', 'falsified')), created_timestamp REAL)"""
    )
    conn.execute("INSERT INTO assumptions VALUES ('a', 'x', 'unverified', 1)")
    migration_080_assumption_expired_status(conn.cursor())
    conn.execute("UPDATE assumptions SET status = 'expired'")
    assert conn.execute("SELECT count(*) FROM assumptions").fetchone()[0] == 1


def test_an_assumption_status_other_than_expired_is_refused(tmp_path, monkeypatch, capsys):
    from empirica.cli.command_handlers.graph_commands import handle_resolve_artifacts_command
    from empirica.data.session_database import SessionDatabase

    db_path = tmp_path / ".empirica" / "sessions" / "sessions.db"
    db_path.parent.mkdir(parents=True)
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(db_path))
    monkeypatch.chdir(tmp_path)
    db = SessionDatabase(db_path=str(db_path))
    try:
        db.conn.execute(
            "INSERT INTO assumptions (id, assumption, status, created_timestamp) VALUES ('aaaaaaaa-0001', 'x', 'unverified', 1)"
        )
        db.conn.commit()
    finally:
        db.close()
    payload = {
        "resolutions": [{"type": "assumption", "id": "aaaaaaaa-0001", "resolution": "it held", "status": "verified"}]
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    handle_resolve_artifacts_command(types.SimpleNamespace(config="-", output="json", verbose=False))
    capsys.readouterr()
    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("SELECT status FROM assumptions WHERE id = 'aaaaaaaa-0001'").fetchone()[0] == "unverified"
    finally:
        conn.close()


def test_an_unreadable_lesson_stops_the_export_before_the_watermark_passes_it(tmp_path):
    from empirica.core.canonical.empirica_git import graph_note_export as gx

    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "config", "commit.gpgsign", "false"],
        ["git", "commit", "-q", "--allow-empty", "-m", "i"],
    ):
        subprocess.run(cmd, cwd=tmp_path, check=True)  # a repo, so the readable lesson after it CAN be written

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE lessons (id TEXT, lesson_data TEXT, created_timestamp REAL, updated_timestamp REAL)")
    conn.execute("INSERT INTO lessons VALUES ('bad', 'not json', 1, 1)")
    conn.execute("INSERT INTO lessons VALUES ('good', '{\"name\": \"x\"}', 2, 2)")
    out = gx.export_lesson_notes(conn, str(tmp_path))
    assert out == {"written": 0, "failed": 1}


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]"])
def test_the_ollama_routes_answer_a_bad_body_with_400(body):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from empirica.local_server import embedder

    app = FastAPI()
    embedder.register(app, [], embedder=embedder.Embedder(load=lambda: lambda texts: [[0.0] * 384 for _ in texts]))
    client = TestClient(app)
    for path in ("/ollama/api/embed", "/ollama/api/embeddings"):
        assert client.post(path, content=body, headers={"Content-Type": "application/json"}).status_code == 400


def test_an_empty_provider_in_the_config_file_reads_as_auto(monkeypatch):
    import empirica.core.qdrant.embeddings as emb

    monkeypatch.delenv("EMPIRICA_EMBEDDINGS_PROVIDER", raising=False)
    import empirica.core.qdrant.connection as qc

    monkeypatch.setattr(emb, "_load_config_file", lambda: {"provider": None})
    monkeypatch.setattr(
        emb, "_check_ollama_available", lambda url: False
    )  # past the Ollama branch, to the provider read
    services = {"embeddings": "http://127.0.0.1:8000/ollama", "vectors": "http://127.0.0.1:8000/qdrant"}
    monkeypatch.setattr(qc, "local_server_service", lambda name: services.get(name))
    monkeypatch.setattr(qc, "store_url", lambda project_id=None: services["vectors"])
    monkeypatch.delenv("CORTEX_QDRANT_URLS_BY_ORG", raising=False)
    out = REAL_EMBED_STEP(types.SimpleNamespace(no_local_server=False, no_cortex=False), True)
    assert out["changed"] is True and "dry-run" in out["note"]  # read as auto: the local model would be loaded


def test_the_cli_delete_is_not_ok_when_a_layer_raised_after_the_row_went(tmp_path, monkeypatch, capsys):
    # 32ba82cd0 caught the raise into the item's layer_errors, but `ok` read only the qdrant statuses, so the CLI said ok
    # and exited 0 where it had exited 1 before (1.16.1 sweep, C2).
    from empirica.cli.command_handlers import graph_commands as gc
    from empirica.data.session_database import SessionDatabase

    db_path = tmp_path / ".empirica" / "sessions" / "sessions.db"
    db_path.parent.mkdir(parents=True)
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(db_path))
    monkeypatch.chdir(tmp_path)
    db = SessionDatabase(db_path=str(db_path))
    try:
        db.conn.execute(
            "INSERT INTO project_findings (id, project_id, session_id, finding, finding_data, created_timestamp) "
            "VALUES ('ffffffff-0001', 'p', 's', 'x', '{}', 1)"
        )
        db.conn.commit()
    finally:
        db.close()

    def boom(*a, **k):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(gc, "_delete_artifact_git_notes", boom)
    monkeypatch.setattr(gc, "_delete_from_qdrant", lambda *a, **k: {})
    payload = {"deletions": [{"type": "finding", "id": "ffffffff-0001"}], "reason": "test"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    rc = gc.handle_delete_artifacts_command(types.SimpleNamespace(config="-", output="json", apply=True, verbose=False))
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and any("git exploded" in e for e in out["errors"])
    assert rc == 1


def test_the_seat_store_under_home_is_not_a_practice(tmp_path, monkeypatch):
    from empirica.core.canonical.empirica_git.goal_note_state import _project_root

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".empirica" / "sessions").mkdir(parents=True)
    seat = sqlite3.connect(str(tmp_path / ".empirica" / "sessions" / "sessions.db"))
    practice_dir = tmp_path / "work" / ".empirica" / "sessions"
    practice_dir.mkdir(parents=True)
    practice = sqlite3.connect(str(practice_dir / "sessions.db"))
    try:
        assert _project_root(seat) is None
        assert _project_root(practice) == str(tmp_path / "work")
    finally:
        seat.close()
        practice.close()
