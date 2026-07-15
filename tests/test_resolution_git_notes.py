"""B2: resolution is durable to git notes (survives a from-notes rebuild / sync).

Before this, resolve_finding/resolve_unknown were SQLite-only, so resolution
lived only in the derived layer — a `rebuild --from-notes` or multi-device sync
resurrected resolved artifacts as open. Now:
  - GitFindingStore carries is_resolved/resolution/superseded_by in the note +
    has resolve_finding (mirrors GitUnknownStore.resolve_unknown).
  - the rebuild re-applies the note's resolution to the re-created SQLite row.
"""

from __future__ import annotations

import subprocess
import uuid

import pytest

from empirica.core.canonical.empirica_git.finding_store import GitFindingStore
from empirica.core.canonical.empirica_git.unknown_store import GitUnknownStore


@pytest.fixture
def git_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    def run(*args):
        subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)

    run("init")
    run("config", "user.email", "t@t.com")
    run("config", "user.name", "t")
    run("commit", "--allow-empty", "-m", "init")
    return str(root)


# ── finding store: the new resolve_finding + payload fields ────────────────────
def test_finding_store_open_by_default(git_repo):
    s = GitFindingStore(workspace_root=git_repo)
    fid = str(uuid.uuid4())
    assert s.store_finding(finding_id=fid, project_id="p", session_id="sess", ai_id="ai", finding="open finding")
    data = s.load_finding(fid)
    assert data["is_resolved"] is False
    assert data["resolution"] is None


def test_finding_store_resolve_persists_to_note(git_repo):
    s = GitFindingStore(workspace_root=git_repo)
    fid = str(uuid.uuid4())
    s.store_finding(finding_id=fid, project_id="p", session_id="sess", ai_id="ai", finding="stale finding", impact=0.5)
    assert s.resolve_finding(fid, "stale", superseded_by="new-id") is True
    data = s.load_finding(fid)
    assert data["is_resolved"] is True
    assert data["resolution"] == "stale"
    assert data["superseded_by"] == "new-id"
    assert data["resolved_at"] is not None
    # the substantive fields (finding text, impact) survive the re-write
    assert data["finding"] == "stale finding"
    assert data["impact"] == 0.5


def test_finding_store_resolve_missing_returns_false(git_repo):
    s = GitFindingStore(workspace_root=git_repo)
    assert s.resolve_finding(str(uuid.uuid4()), "stale") is False


# ── unknown store: existing mechanism still persists (regression) ──────────────
def test_unknown_store_resolve_persists_to_note(git_repo):
    s = GitUnknownStore(workspace_root=git_repo)
    uid = str(uuid.uuid4())
    s.store_unknown(unknown_id=uid, project_id="p", session_id="sess", ai_id="ai", unknown="a question")
    assert s.resolve_unknown(uid, "answered") is True
    data = s.load_unknown(uid)
    assert data["resolved"] is True
    assert data["resolved_by"] == "answered"


# ── rebuild re-applies the note's resolution to the re-created row ─────────────
def test_rebuild_reapplies_resolution(tmp_path):
    from empirica.cli.command_handlers.sync_commands import _rebuild_apply_resolution
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase(db_path=str(tmp_path / "r.db"))
    try:
        pid, sid = str(uuid.uuid4()), str(uuid.uuid4())
        fid = db.log_finding(pid, sid, "rebuilt open finding")
        # simulate the rebuild loop seeing a note that says the finding is resolved
        _rebuild_apply_resolution(
            db, "findings", fid, {"is_resolved": True, "resolution": "stale", "superseded_by": None}
        )
        row = db.conn.execute("SELECT is_resolved, resolution FROM project_findings WHERE id = ?", (fid,)).fetchone()
        assert row[0] == 1 and row[1] == "stale"

        uid = db.log_unknown(project_id=pid, session_id=sid, unknown="rebuilt open unknown")
        _rebuild_apply_resolution(db, "unknowns", uid, {"resolved": True, "resolved_by": "answered"})
        urow = db.conn.execute("SELECT is_resolved, resolved_by FROM project_unknowns WHERE id = ?", (uid,)).fetchone()
        assert urow[0] == 1 and urow[1] == "answered"
    finally:
        db.close()
