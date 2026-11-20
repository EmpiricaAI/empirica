"""Chat's batch wrappers against the REAL verbs (goal 94c086d1's last task, from merged goal 37e35d31).

tests/test_chat_batch_actions.py mocks the subprocess, so a payload that drifted from the verb's schema would pass there and
fail in use; that is how resolve-artifacts was once sent {"unknown_ids": [...]}, which it never read. These run the CLI in a
throwaway practice: resolve really resolves, delete without apply only previews.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid

import pytest

from empirica.core.chat import actions
from empirica.data.session_database import SessionDatabase


@pytest.fixture
def practice(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=tmp_path, check=True)
    (tmp_path / ".empirica" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / ".empirica" / "sessions" / "sessions.db"))
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("EMPIRICA_ENABLE_EMBEDDINGS", "false")
    # The wrappers run `empirica` from PATH. Put a shim for this interpreter first, so the test never depends on an
    # installed binary (the clean-environment suite runs with a system-only PATH: tests must not measure the box).
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "empirica"
    shim.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" -c "import sys; from empirica.cli.cli_core import main; sys.exit(main())" "$@"\n'
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _row(table, column, text):
    db = SessionDatabase()
    try:
        rid = str(uuid.uuid4())
        cols = {"id": rid, "project_id": "p", "session_id": "s", column: text, "created_timestamp": time.time()}
        for _cid, name, ctype, notnull, default, _pk in db.conn.execute(f"PRAGMA table_info({table})").fetchall():
            if notnull and default is None and name not in cols:
                cols[name] = 0 if any(t in (ctype or "").upper() for t in ("INT", "REAL")) else "{}"
        db.conn.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", list(cols.values())
        )
        db.conn.commit()
        return rid
    finally:
        db.close()


def _one(sql, *args):
    db = SessionDatabase()
    try:
        return db.conn.execute(sql, args).fetchone()
    finally:
        db.close()


def test_resolve_from_chat_resolves_the_unknown_through_the_real_verb(practice):
    uid = _row("project_unknowns", "unknown", "is it cold?")
    resp = actions.resolve_artifacts_batch([uid], "answered in chat")
    assert resp.get("ok") is True and "1 of 1" in actions.summarize_batch("resolve", resp, 1)
    assert _one("SELECT is_resolved FROM project_unknowns WHERE id = ?", uid)[0] == 1


def test_delete_from_chat_without_apply_previews_and_keeps_the_row(practice):
    fid = _row("project_findings", "finding", "a finding")
    resp = actions.delete_artifacts_batch([("finding", fid)], apply=False)
    assert resp.get("ok") is True and resp.get("dry_run") is True
    assert "would delete 1" in actions.summarize_batch("delete", resp, 1, apply=False)
    assert _one("SELECT COUNT(*) FROM project_findings WHERE id = ?", fid)[0] == 1
