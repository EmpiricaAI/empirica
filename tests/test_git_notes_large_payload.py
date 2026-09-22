"""A note body larger than one argv string must still be written.

Every note writer ran `git notes add -m <payload>`. Linux caps a single argv
string at MAX_ARG_STRLEN (128 KB), so a larger payload raised E2BIG before git
started; the stores caught it, logged a warning and returned False, leaving the
SQLite row with no note for `rebuild` to import. Measured on core 2026-09-22:
finding 5ba56421 (103 KB of text plus 110 KB of finding_data) was the one row the
notes backfill could not write. The writers now pass the body on stdin (`-F -`).
"""

from __future__ import annotations

import subprocess
import uuid

import pytest

from empirica.core.canonical.empirica_git.finding_store import GitFindingStore
from empirica.core.canonical.empirica_git.unknown_store import GitUnknownStore

# Comfortably past 128 KB once serialised, so the old argv form cannot carry it.
BIG = "x" * 200_000


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


def test_argv_form_really_fails_at_this_size(git_repo):
    """Positive control: the payload is large enough that the old form breaks."""
    with pytest.raises(OSError):
        subprocess.run(["git", "notes", "add", "-f", "-m", BIG, "HEAD"], cwd=git_repo, capture_output=True)


def test_large_finding_round_trips(git_repo):
    s = GitFindingStore(workspace_root=git_repo)
    fid = str(uuid.uuid4())
    assert s.store_finding(finding_id=fid, project_id="p", session_id="sess", ai_id="ai", finding=BIG)
    assert s.load_finding(fid)["finding"] == BIG


def test_large_finding_resolution_is_written(git_repo):
    s = GitFindingStore(workspace_root=git_repo)
    fid = str(uuid.uuid4())
    s.store_finding(finding_id=fid, project_id="p", session_id="sess", ai_id="ai", finding=BIG)
    assert s.resolve_finding(fid, "stale") is True
    data = s.load_finding(fid)
    assert data["is_resolved"] is True
    assert data["finding"] == BIG


def test_large_unknown_round_trips(git_repo):
    s = GitUnknownStore(workspace_root=git_repo)
    uid = str(uuid.uuid4())
    assert s.store_unknown(unknown_id=uid, project_id="p", session_id="sess", ai_id="ai", unknown=BIG)
    assert s.load_unknown(uid)["unknown"] == BIG
