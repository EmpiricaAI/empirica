"""The notes/sqlite check must read the store the writers write to.

It hardcoded `<cwd>/.empirica/sessions/sessions.db` while every verb honours
`EMPIRICA_SESSION_DB`. With the variable set — tests, CI, Docker, any pinned
store — it compared notes against a database that does not hold the artifacts,
called LIVE artifacts orphans, and offered `doctor --reconcile-notes --apply`,
which archives their notes. Found by running doctor in a scratch project, not by
reading it: the hardcoded path looks obviously right.

The first test fails against the old code, which is the point of it.
"""

from __future__ import annotations

import sqlite3
import subprocess
import uuid

import pytest

from empirica.cli.command_handlers import doctor
from empirica.core.canonical.empirica_git.finding_store import GitFindingStore


@pytest.fixture
def repo_with_pinned_store(tmp_path, monkeypatch):
    """A checkout whose artifacts live in a PINNED store, not the local one."""
    root = tmp_path / "proj"
    (root / ".empirica" / "sessions").mkdir(parents=True)

    def git(*args):
        subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (root / "f.txt").write_text("x")
    git("add", ".")
    git("commit", "-qm", "init")

    # The project-local store exists but holds nothing — this is what the check
    # used to read.
    local = root / ".empirica" / "sessions" / "sessions.db"
    sqlite3.connect(str(local)).close()

    # The pinned store is where the artifact actually lives — a REAL store built
    # by the migrations. A hand-built table lacks columns the check reads, and it
    # then honestly reports "could not be measured" rather than the thing under
    # test, which is how this fixture first passed for the wrong reason.
    pinned = tmp_path / "pinned.db"
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(pinned))
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()
    finding_id = str(uuid.uuid4())
    db.conn.execute(
        "INSERT INTO project_findings (id, project_id, session_id, finding, finding_data, created_timestamp)"
        " VALUES (?, 'p', 's', 'a live finding', '{}', 0)",
        (finding_id,),
    )
    db.conn.commit()
    db.close()

    # And it has a note, so the check has something to compare.
    GitFindingStore(workspace_root=str(root)).store_finding(
        finding_id=finding_id, project_id="p", session_id="s", ai_id="ai", finding="a live finding"
    )
    return root, finding_id


def test_a_live_artifact_in_the_pinned_store_is_not_called_an_orphan(repo_with_pinned_store):
    root, finding_id = repo_with_pinned_store
    check = doctor.check_notes_sqlite_divergence(cwd=root)
    orphaned = (check.data or {}).get("types", {}).get("findings", {}).get("orphaned", [])
    assert finding_id not in orphaned, (
        "the check read a store that does not hold this artifact and would have offered to archive its note"
    )


def test_a_genuine_orphan_is_still_reported(repo_with_pinned_store):
    """Positive control: the check still finds a real divergence in that store."""
    root, _ = repo_with_pinned_store
    ghost = str(uuid.uuid4())
    GitFindingStore(workspace_root=str(root)).store_finding(
        finding_id=ghost, project_id="p", session_id="s", ai_id="ai", finding="no row for this one"
    )
    check = doctor.check_notes_sqlite_divergence(cwd=root)
    orphaned = (check.data or {}).get("types", {}).get("findings", {}).get("orphaned", [])
    assert ghost in orphaned
