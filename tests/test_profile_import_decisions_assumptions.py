"""profile-sync / import_all now restores decisions and assumptions, not only findings, unknowns, dead ends, mistakes, goals.

ecodex-lab (prop_toajlquc4jag5gun7ygb4646ee): after the artifact import a doctor dry-run still showed 20 orphans, all decisions (5) and
assumptions (15), which ProfileImporter did not import. The importer is INSERT OR IGNORE by id, so it is idempotent.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from empirica.core.canonical.empirica_git.profile_import import ProfileImporter
from empirica.data.session_database import SessionDatabase


def _git(repo, *args):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        capture_output=True, text=True, env=env, check=True,
    )  # fmt: skip


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "c")
    db = SessionDatabase(db_path=str(tmp_path / "sessions.db"))
    pid = db.create_project(name="p")
    sid = db.create_session(ai_id="a", project_id=pid)
    yield type("W", (), {"repo": repo, "db": db, "pid": pid, "sid": sid})
    db.close()


def _note(world, kind, ident, body):
    _git(world.repo, "notes", f"--ref=empirica/{kind}/{ident}", "add", "-f", "-m", json.dumps(body), "HEAD")


def _decision(world, ident="d1"):
    _note(world, "decisions", ident, {
        "decision_id": ident, "choice": "use X", "rationale": "because", "alternatives": ["Y", "Z"],
        "confidence_at_decision": 0.8, "reversibility": "exploratory", "project_id": world.pid, "session_id": world.sid,
        "goal_id": None, "ai_id": "a", "created_at": "2026-10-03T16:52:03+00:00",
    })  # fmt: skip


def _assumption(world, ident="a1", **over):
    _note(world, "assumptions", ident, {
        "assumption_id": ident, "assumption": "it holds", "confidence": 0.6, "status": "unverified", "domain": "x",
        "project_id": world.pid, "session_id": world.sid, "goal_id": None, "ai_id": "a",
        "created_at": "2026-10-03T16:52:03+00:00", **over,
    })  # fmt: skip


def test_decisions_are_restored_with_their_fields(world):
    _decision(world)

    imp = ProfileImporter(str(world.repo))
    imp.import_all(world.db)
    row = world.db.conn.execute(
        "SELECT choice, rationale, alternatives, confidence_at_decision, reversibility, created_by_ai FROM decisions WHERE id='d1'"
    ).fetchone()

    assert tuple(row) == ("use X", "because", json.dumps(["Y", "Z"]), 0.8, "exploratory", "a")
    assert imp.stats["decisions"] == {"imported": 1, "skipped": 0, "total": 1}


def test_assumptions_are_restored_with_their_fields(world):
    _assumption(world, status="verified")

    imp = ProfileImporter(str(world.repo))
    imp.import_all(world.db)
    row = world.db.conn.execute(
        "SELECT assumption, confidence, status, created_by_ai FROM assumptions WHERE id='a1'"
    ).fetchone()

    assert tuple(row) == ("it holds", 0.6, "verified", "a")
    assert imp.stats["assumptions"]["imported"] == 1


def test_a_second_import_adds_nothing(world):
    _decision(world)
    _assumption(world)
    ProfileImporter(str(world.repo)).import_all(world.db)

    again = ProfileImporter(str(world.repo))
    again.import_all(world.db)

    assert again.stats["decisions"]["imported"] == 0 and again.stats["decisions"]["skipped"] == 1
    assert again.stats["assumptions"]["imported"] == 0
    assert world.db.conn.execute("SELECT count(*) FROM decisions").fetchone()[0] == 1


def test_an_assumption_note_with_an_invalid_status_or_confidence_is_imported_normalised_not_dropped(world):
    """The table CHECKs status and confidence; one odd note must not cost the assumption."""
    _assumption(world, ident="odd", status="maybe", confidence=7)

    ProfileImporter(str(world.repo)).import_all(world.db)
    row = world.db.conn.execute("SELECT status, confidence FROM assumptions WHERE id='odd'").fetchone()

    assert tuple(row) == ("unverified", None)


def test_an_existing_row_is_not_overwritten(world):
    _decision(world)
    world.db.conn.execute(
        "INSERT INTO decisions (id, choice, rationale, created_timestamp, project_id) VALUES ('d1', 'live choice', 'live', 1.0, ?)",
        (world.pid,),
    )
    world.db.conn.commit()

    ProfileImporter(str(world.repo)).import_all(world.db)

    assert world.db.conn.execute("SELECT choice FROM decisions WHERE id='d1'").fetchone()[0] == "live choice"
