"""The backfill writes a note for every artifact that lacks one, with its own
timestamp and resolution state, and touches nothing that already has a note.
Runs in a throwaway git repo against a throwaway store."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backfill_git_notes.py"
NOTED = "11111111-0000-4000-8000-000000000001"
BARE = "22222222-0000-4000-8000-000000000002"
RESOLVED = "33333333-0000-4000-8000-000000000003"


@pytest.fixture
def world(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
    (tmp_path / "f").write_text("x")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "sessions.db"
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(db_path))
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()
    sid = db.create_session(ai_id="empirica-test")
    for fid, resolved in ((NOTED, 0), (BARE, 0), (RESOLVED, 1)):
        db.conn.execute(
            "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, finding_data,"
            " impact, is_resolved, resolution, resolution_kind) VALUES (?, 'p', ?, 'f', 1700000000, '{}', 0.5, ?, ?, ?)",
            (fid, sid, resolved, "was wrong" if resolved else None, "retracted" if resolved else None),
        )
    db.conn.execute(
        "INSERT INTO project_unknowns (id, project_id, session_id, unknown, created_timestamp, unknown_data)"
        " VALUES (?, 'p', ?, 'u', 1700000000, '{}')",
        (BARE, sid),
    )
    db.conn.commit()
    db.close()
    subprocess.run(
        [
            "git",
            "notes",
            f"--ref=refs/notes/empirica/findings/{NOTED}",
            "add",
            "-f",
            "-m",
            '{"finding_id": "pre"}',
            "HEAD",
        ],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path, db_path


def _run(db_path, *flags):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db_path), *flags], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _note(repo, kind, aid):
    ref = f"refs/notes/empirica/{kind}/{aid}"
    out = subprocess.run(["git", "notes", f"--ref={ref}", "show", "HEAD"], cwd=repo, capture_output=True, text=True)
    return json.loads(out.stdout) if out.returncode == 0 else None


def test_dry_run_counts_and_writes_nothing(world):
    repo, db_path = world
    report = _run(db_path)
    assert report["mode"] == "dry-run"
    assert report["findings_missing"] == 2 and report["findings_missing_resolved"] == 1
    assert report["unknowns_missing"] == 1
    assert _note(repo, "findings", BARE) is None


def test_apply_writes_the_missing_notes_with_state_and_leaves_the_noted_one(world):
    repo, db_path = world
    report = _run(db_path, "--apply")
    assert report["written"] == {"findings": 2, "unknowns": 1} and not any(report["failed"].values())
    bare = _note(repo, "findings", BARE)
    assert (
        bare["finding_id"] == BARE and bare["created_at"].startswith("2023-11-14") and bare["ai_id"] == "empirica-test"
    )
    resolved = _note(repo, "findings", RESOLVED)
    assert resolved["is_resolved"] is True and resolved["resolution_kind"] == "retracted"
    assert _note(repo, "unknowns", BARE)["unknown_id"] == BARE
    assert _note(repo, "findings", NOTED) == {"finding_id": "pre"}  # untouched
    assert _run(db_path)["findings_missing"] == 0  # idempotent
