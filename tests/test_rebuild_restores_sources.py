"""A rebuild from the git notes restores sources, archived ones as archived (goal 25f4ab9a).

Source notes had no reader and recorded nothing about archiving, so the rebuild had no branch for sources and could not
have told an archived source from a live one. Round trip in a throwaway repo: write two sources the way source-add does
(row plus note), archive one through the real verb, rebuild into a fresh database from the notes alone.
"""

from __future__ import annotations

import io
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import artifact_log_commands as alc
from empirica.cli.command_handlers.sync_commands import _rebuild_sources
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
    monkeypatch.setattr(alc, "_hard_delete_source_chunks", lambda *a: 0)
    monkeypatch.setattr(alc, "_hard_delete_source_memory_embed", lambda *a: 0)
    monkeypatch.setattr(alc, "_push_source_archive_to_cortex", lambda *a: None)
    return tmp_path


def _add(title: str, url: str) -> str:
    sid = str(uuid.uuid4())
    db = SessionDatabase()
    try:
        db.conn.execute(
            "INSERT INTO epistemic_sources (id, project_id, session_id, source_type, source_url, title, description, "
            "confidence, epistemic_layer, discovered_by_ai, discovered_at, visibility) "
            "VALUES (?, 'proj-1', 'sess-1', 'url', ?, ?, 'd', 0.8, 'noetic', 'ai', ?, 'local')",
            (sid, url, title, time.time()),
        )
        db.conn.commit()
    finally:
        db.close()
    assert GitSourceStore().store_source(
        source_id=sid, project_id="proj-1", session_id="sess-1", title=title, source_type="url", source_url=url,
        confidence=0.8, direction="noetic", ai_id="ai",
    )  # fmt: skip
    return sid


def test_a_source_archived_then_rebuilt_from_notes_comes_back_archived(practice, tmp_path):
    a = _add("RFC 7519", "https://example.org/rfc7519")
    b = _add("Paper", "https://example.org/paper")
    out, sys.stdout = sys.stdout, io.StringIO()
    try:
        code = alc.handle_source_archive_command(
            SimpleNamespace(source_id=a, reason="url_unreachable", target_id=None, output="json", verbose=False)
        )
    finally:
        sys.stdout = out
    assert code == 0
    note = GitSourceStore().load_source(a)
    assert note["archived"] is True and note["archive_reason"] == "url_unreachable" and note.get("archived_at"), note

    fresh = SessionDatabase(db_path=str(tmp_path / "fresh.db"))
    fresh.conn.execute("PRAGMA foreign_keys=OFF")
    try:
        rebuilt: dict = {}
        _rebuild_sources(fresh, rebuilt)
        rows = {
            r[0]: r[1:]
            for r in fresh.conn.execute(
                "SELECT id, title, source_url, epistemic_layer, archived, archive_reason FROM epistemic_sources"
            ).fetchall()
        }
    finally:
        fresh.close()
    assert rebuilt["sources"] == 2
    assert tuple(rows[a]) == ("RFC 7519", "https://example.org/rfc7519", "noetic", 1, "url_unreachable")
    assert tuple(rows[b]) == ("Paper", "https://example.org/paper", "noetic", 0, None)
