"""A goal's lifecycle state is written to its git note, and a rebuild restores it (goal 44e52011).

A goal note was written once, at creation, and never again, so the canonical log could not say whether a goal had been completed,
abandoned, archived or reopened. A from-notes rebuild therefore brought every goal back as ``in_progress`` (marked
``status_unknown`` since 79f39b4fa). Each transition now restamps the note with the goal's state and a lineage entry, a rebuild
reads that state back, and ``doctor --reconcile-notes`` repairs the backlog of creation-only notes.

Real git in a scratch repo, real SessionDatabase; the SQLite row is the source of truth the note must agree with.
"""

from __future__ import annotations

import json
import subprocess
import uuid

import pytest

from empirica.core.canonical.empirica_git import goal_note_state as gns
from empirica.core.canonical.empirica_git.goal_store import GitGoalStore
from empirica.data.session_database import SessionDatabase

_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd,
        capture_output=True,
        check=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd), **_ENV},
    )


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".empirica" / "sessions").mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "user.email", "t@example.invalid")
    (root / "f").write_text("x")
    _git(root, "add", "f")
    _git(root, "commit", "-q", "-m", "c")
    monkeypatch.chdir(root)
    db = SessionDatabase(db_path=str(root / ".empirica" / "sessions" / "sessions.db"))
    db.conn.execute("PRAGMA foreign_keys=OFF")
    yield root, db
    db.close()


def _goal(db, root, objective="ship it", status="in_progress") -> str:
    """A goal row WITH its creation-only note, as goals-create leaves it."""
    gid = str(uuid.uuid4())
    sid = db.create_session(ai_id="gns-test")
    db.conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, goal_data, status, project_id) "
        "VALUES (?, ?, ?, '{}', 1000.0, '{}', ?, 'p1')",
        (gid, sid, objective, status),
    )
    db.conn.commit()
    GitGoalStore(str(root)).store_goal(gid, sid, "creator-ai", {"objective": objective, "scope": {}})
    return gid


def _note(root, gid):
    return GitGoalStore(str(root)).load_goal(gid)


def _lineage_actions(note):
    return [e["action"] for e in note["lineage"]]


# --- restamp ------------------------------------------------------------------------------------------------------------------


def test_completing_a_goal_stamps_its_note(proj):
    root, db = proj
    gid = _goal(db, root)
    db.conn.execute(
        "UPDATE goals SET status='completed', is_completed=1, completed_timestamp=2000.0, completion_reason='done' WHERE id=?",
        (gid,),
    )
    db.conn.commit()
    assert gns.restamp_goal_note(db.conn, gid, "completed") is True
    note = _note(root, gid)
    data = note["goal_data"]
    assert data["status"] == "completed" and data["is_completed"] == 1 and data["completed_timestamp"] == 2000.0
    assert data["completion_reason"] == "done" and data["project_id"] == "p1"
    assert _lineage_actions(note) == ["created", "completed"]
    assert note["ai_id"] == "creator-ai", "the creator stays the creator"
    assert data["objective"] == "ship it", "the creation fields survive the restamp"


def test_restamping_keeps_the_original_creation_time(proj):
    root, db = proj
    gid = _goal(db, root)
    before = _note(root, gid)["created_at"]
    gns.restamp_goal_note(db.conn, gid, "archived")
    assert _note(root, gid)["created_at"] == before


def test_a_reopened_goal_loses_its_completion_in_the_note(proj):
    root, db = proj
    gid = _goal(db, root)
    db.conn.execute(
        "UPDATE goals SET status='completed', is_completed=1, completed_timestamp=2000.0 WHERE id=?", (gid,)
    )
    db.conn.commit()
    gns.restamp_goal_note(db.conn, gid, "completed")
    db.conn.execute(
        "UPDATE goals SET status='in_progress', is_completed=0, completed_timestamp=NULL WHERE id=?", (gid,)
    )
    db.conn.commit()
    gns.restamp_goal_note(db.conn, gid, "reopened")
    data = _note(root, gid)["goal_data"]
    assert data["status"] == "in_progress" and not data["is_completed"] and data["completed_timestamp"] is None


def test_a_goal_with_no_note_gets_one(proj):
    root, db = proj
    gid = str(uuid.uuid4())
    sid = db.create_session(ai_id="gns-test")
    db.conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, goal_data, status) "
        "VALUES (?, ?, 'no note yet', '{}', 1000.0, '{}', 'planned')",
        (gid, sid),
    )
    db.conn.commit()
    assert _note(root, gid) is None
    assert gns.restamp_goal_note(db.conn, gid, "activated") is True
    note = _note(root, gid)
    assert note["goal_data"]["objective"] == "no note yet" and note["goal_data"]["status"] == "planned"


def test_restamping_never_raises(proj, monkeypatch):
    """A verb must not fail because its audit trail could not be written."""
    root, db = proj
    assert gns.restamp_goal_note(db.conn, "no-such-goal", "completed") is False
    gid = _goal(db, root)
    monkeypatch.setattr(GitGoalStore, "store_goal", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("git down")))
    assert gns.restamp_goal_note(db.conn, gid, "completed") is False


# --- rebuild ------------------------------------------------------------------------------------------------------------------


def _rebuild_goals(tmp_path, root):
    """Rebuild only the goals from `root`'s notes into a fresh database; return that database."""
    import empirica.cli.command_handlers.sync_commands as sc

    fresh = SessionDatabase(db_path=str(tmp_path / "fresh.db"))
    fresh.conn.execute("PRAGMA foreign_keys=OFF")
    rebuilt = {"goals": 0}
    sc._rebuild_ensure_goals(fresh, 1.0, rebuilt)
    return fresh, rebuilt


def test_a_completed_goal_rebuilds_completed(proj, tmp_path):
    root, db = proj
    done, open_ = _goal(db, root, "done one"), _goal(db, root, "open one")
    db.conn.execute(
        "UPDATE goals SET status='completed', is_completed=1, completed_timestamp=2000.0, completion_reason='shipped' WHERE id=?",
        (done,),
    )
    db.conn.commit()
    gns.restamp_goal_note(db.conn, done, "completed")
    gns.restamp_goal_note(db.conn, open_, "activated")

    fresh, rebuilt = _rebuild_goals(tmp_path, root)
    try:
        row = fresh.conn.execute(
            "SELECT status, is_completed, completed_timestamp, completion_reason FROM goals WHERE id=?", (done,)
        ).fetchone()
        assert tuple(row) == ("completed", 1, 2000.0, "shipped")
        assert (
            fresh.conn.execute("SELECT status, is_completed FROM goals WHERE id=?", (open_,)).fetchone()[0]
            == "in_progress"
        )
        assert not rebuilt.get("goals_status_unknown"), "every note carried a status"
    finally:
        fresh.close()


def test_an_archived_goal_rebuilds_archived(proj, tmp_path):
    root, db = proj
    gid = _goal(db, root)
    db.conn.execute(
        "UPDATE goals SET status='completed', is_completed=1, completed_timestamp=2000.0, archived=1, archived_at=3000.0 WHERE id=?",
        (gid,),
    )
    db.conn.commit()
    gns.restamp_goal_note(db.conn, gid, "archived")
    fresh, _ = _rebuild_goals(tmp_path, root)
    try:
        assert tuple(fresh.conn.execute("SELECT archived, archived_at FROM goals WHERE id=?", (gid,)).fetchone()) == (
            1,
            3000.0,
        )
    finally:
        fresh.close()


def test_a_creation_only_note_still_rebuilds_as_unknown(proj, tmp_path):
    """Control: the honest fallback for a note nobody restamped is unchanged."""
    root, db = proj
    gid = _goal(db, root)
    fresh, rebuilt = _rebuild_goals(tmp_path, root)
    try:
        assert json.loads(fresh.conn.execute("SELECT goal_data FROM goals WHERE id=?", (gid,)).fetchone()[0])[
            "status_unknown"
        ]
        assert rebuilt["goals_status_unknown"] == 1
    finally:
        fresh.close()


# --- the backlog --------------------------------------------------------------------------------------------------------------


def test_reconcile_finds_notes_that_disagree_with_sqlite_and_repairs_them(proj):
    root, db = proj
    stale, fine = _goal(db, root, "stale note"), _goal(db, root, "fine note")
    gns.restamp_goal_note(db.conn, fine, "activated")  # note already agrees
    db.conn.execute(
        "UPDATE goals SET status='completed', is_completed=1, completed_timestamp=2000.0 WHERE id=?", (stale,)
    )
    db.conn.commit()

    found = gns.plan_goal_notes(db.conn, str(root))
    assert [g["id"] for g in found["drifted"]] == [stale], "only the disagreeing note is reported"

    result = gns.apply_goal_notes(db.conn, str(root))
    assert result["restamped"] == 1
    assert _note(root, stale)["goal_data"]["status"] == "completed"
    assert gns.plan_goal_notes(db.conn, str(root))["drifted"] == [], "idempotent: a second pass finds nothing"


# --- wiring -------------------------------------------------------------------------------------------------------------------


def test_the_repository_transitions_restamp_the_note(proj):
    root, db = proj
    gid = _goal(db, root)
    repo = db.goals

    assert repo.abandon_goal(gid, "no longer matters")
    assert _note(root, gid)["goal_data"]["status"] == "abandoned"
    assert repo.reopen_goal(gid, "was wrong")
    assert _note(root, gid)["goal_data"]["status"] == "in_progress"

    planned = _goal(db, root, "queued", status="planned")
    assert repo.activate_goal(planned)
    assert _note(root, planned)["goal_data"]["status"] == "in_progress"

    db.conn.execute("UPDATE goals SET status='completed', is_completed=1, completed_timestamp=1.0 WHERE id=?", (gid,))
    db.conn.commit()
    assert [r["id"] for r in repo.archive_stale_completed(apply=True, goal_id=gid)] == [gid]
    data = _note(root, gid)["goal_data"]
    assert data["archived"] == 1 and data["archived_at"]
    assert _lineage_actions(_note(root, gid))[-1] == "archived"


def test_a_transition_that_changes_nothing_does_not_touch_the_note(proj):
    root, db = proj
    done = _goal(db, root)
    db.conn.execute("UPDATE goals SET status='completed', is_completed=1 WHERE id=?", (done,))
    db.conn.commit()
    before = _lineage_actions(_note(root, done))
    assert not db.goals.abandon_goal(done, "too late"), "a completed goal is not re-labelled"
    assert _lineage_actions(_note(root, done)) == before


def test_doctor_reconcile_reports_and_repairs_goal_notes(proj, capsys):
    from empirica.cli.command_handlers.doctor import _handle_reconcile_notes

    root, db = proj
    gid = _goal(db, root)
    db.conn.execute(
        "UPDATE goals SET status='completed', is_completed=1, completed_timestamp=2000.0 WHERE id=?", (gid,)
    )
    db.conn.commit()

    assert _handle_reconcile_notes(root, apply_it=False) == 0
    out = json.loads(capsys.readouterr().out)
    assert [g["id"] for g in out["goals"]["drifted"]] == [gid] and out["dry_run"] is True
    assert _note(root, gid)["goal_data"].get("status") != "completed", "a dry run changes nothing"

    assert _handle_reconcile_notes(root, apply_it=True) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["goals"]["restamped"] == 1
    assert _note(root, gid)["goal_data"]["status"] == "completed"


def test_goals_complete_restamps_the_note(proj, monkeypatch):
    from empirica.cli.command_handlers.goal_commands import _gc_mark_completed

    root, db = proj
    gid = _goal(db, root)
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(root / ".empirica" / "sessions" / "sessions.db"))
    assert _gc_mark_completed(gid, "shipped in abc123") is True
    note = _note(root, gid)
    assert note["goal_data"]["status"] == "completed" and note["goal_data"]["completion_reason"] == "shipped in abc123"
    assert _lineage_actions(note)[-1] == "completed"


# --- a moving HEAD ------------------------------------------------------------------------------------------------------------
#
# `git notes add -f` replaces a note only on the SAME annotated commit. store_goal wrote at HEAD, so once HEAD had moved since the
# goal was created, a restamp left TWO notes under the one ref and load_goal read whichever `git notes list` printed first: the
# older one. The tests above never moved HEAD, which is why 1727 restamps on the real store (db62168e8) converged for none of the
# goals whose creation commit was no longer HEAD.


def _move_head(root, name="g"):
    (root / name).write_text(name)
    _git(root, "add", name)
    _git(root, "commit", "-q", "-m", name)


def _entries(root, gid):
    out = subprocess.run(
        ["git", "notes", f"--ref=empirica/goals/{gid}", "list"], cwd=root, capture_output=True, text=True, check=True
    ).stdout
    return [line.split() for line in out.splitlines() if line.strip()]


def test_a_restamp_after_head_moved_replaces_the_note_rather_than_adding_one(proj):
    root, db = proj
    gid = _goal(db, root)
    _move_head(root)
    db.conn.execute(
        "UPDATE goals SET status='completed', is_completed=1, completed_timestamp=2000.0 WHERE id=?", (gid,)
    )
    db.conn.commit()
    assert gns.restamp_goal_note(db.conn, gid, "completed") is True
    assert len(_entries(root, gid)) == 1, "one note per goal ref"
    assert _note(root, gid)["goal_data"]["status"] == "completed"


def test_a_lineage_entry_after_head_moved_does_not_fork_the_note(proj):
    root, db = proj
    gid = _goal(db, root)
    _move_head(root)
    assert GitGoalStore(str(root)).add_lineage(gid, "other-ai", "resumed") is True
    assert len(_entries(root, gid)) == 1
    assert _lineage_actions(_note(root, gid)) == ["created", "resumed"]


def test_reconcile_consolidates_notes_the_old_writer_forked(proj):
    """The real store holds 1727 of these: the old creation note on the first commit, the restamp on a later one."""
    root, db = proj
    gid = _goal(db, root)
    _move_head(root)
    db.conn.execute(
        "UPDATE goals SET status='completed', is_completed=1, completed_timestamp=2000.0 WHERE id=?", (gid,)
    )
    db.conn.commit()
    # reproduce the old writer: add the restamped payload on the NEW head, leaving the creation note where it was
    payload = dict(_note(root, gid) or {})
    payload["goal_data"] = {
        **(payload.get("goal_data") or {}),
        "status": "completed",
        "is_completed": 1,
        "completed_timestamp": 2000.0,
        "state_updated_at": 9e9,
    }
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(
        ["git", "notes", f"--ref=empirica/goals/{gid}", "add", "-f", "-F", "-", head],
        cwd=root,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    assert len(_entries(root, gid)) == 2, "forked"
    reader_commit = _entries(root, gid)[0][1]  # git lists by annotated commit; the reader takes the first line

    result = gns.apply_goal_notes(db.conn, str(root))
    entries = _entries(root, gid)
    assert len(entries) == 1 and entries[0][1] == reader_commit, "one note, on the commit the reader looks at first"
    assert _note(root, gid)["goal_data"]["status"] == "completed"
    assert result["failed"] == 0
    assert gns.plan_goal_notes(db.conn, str(root))["drifted"] == [], "and the second pass is clean"
