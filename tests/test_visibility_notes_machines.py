"""Visibility notes that are safe across machines (goal 221679b1).

1.17.14 wrote one set of shard refs that every machine of a practice shared, and core never merges notes, so two machines
refused each other's sync. Now each machine writes only its own namespace (empirica/visibility/<machine>/<shard>), each entry
records when that machine saw the tag change, and a rebuild takes the latest entry per artifact across machines.
"""

from __future__ import annotations

import subprocess
import uuid

import pytest

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


def _finding(db, visibility, fid=None):
    fid = fid or str(uuid.uuid4())
    db.conn.execute(
        "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, finding_data, visibility) "
        "VALUES (?, 'p', 's', 'x', 1.0, '{}', ?)",
        (fid, visibility),
    )
    db.conn.commit()
    return fid


def _refs(root):
    out = subprocess.run(["git", "for-each-ref", "--format=%(refname)", "refs/notes/empirica/visibility/"], cwd=root,
                         capture_output=True, text=True, check=True).stdout.split()  # fmt: skip
    return out


def test_each_machine_writes_only_under_its_own_name(practice, monkeypatch):
    monkeypatch.setattr(gx, "machine_name", lambda: "alpha-1")
    db = SessionDatabase()
    try:
        _finding(db, "shared", "ab000000-0000-0000-0000-000000000001")
        gx.export_visibility_notes(db.conn, str(practice))
    finally:
        db.close()
    assert _refs(practice) == ["refs/notes/empirica/visibility/alpha-1/ab"]


def test_the_latest_change_wins_across_machines_including_a_move_back_to_local(practice):
    fid = "cd000000-0000-0000-0000-000000000002"
    gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/alpha/cd", {"tiers": {fid: ["shared", 100.0]}})
    gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/beta/cd", {"tiers": {fid: ["local", 200.0]}})
    assert gx.discover_visibility_notes(str(practice))[fid] == "local"
    gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/alpha/cd", {"tiers": {fid: ["public", 300.0]}})
    assert gx.discover_visibility_notes(str(practice))[fid] == "public"


def test_a_1_17_14_flat_shard_counts_as_the_oldest(practice):
    fid = "ef000000-0000-0000-0000-000000000003"
    gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/ef", {"tiers": {fid: "shared"}})  # the old layout
    assert gx.discover_visibility_notes(str(practice))[fid] == "shared"
    gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/gamma/ef", {"tiers": {fid: ["local", 5.0]}})
    assert gx.discover_visibility_notes(str(practice))[fid] == "local"


def test_a_tag_moved_back_to_local_is_written_as_local_so_it_can_win(practice, monkeypatch):
    monkeypatch.setattr(gx, "machine_name", lambda: "alpha")
    db = SessionDatabase()
    try:
        fid = _finding(db, "shared", "aa000000-0000-0000-0000-000000000004")
        gx.export_visibility_notes(db.conn, str(practice))
        db.conn.execute("UPDATE project_findings SET visibility = 'local' WHERE id = ?", (fid,))
        db.conn.commit()
        gx.export_visibility_notes(db.conn, str(practice))
    finally:
        db.close()
    assert gx._read_shard(str(practice), "alpha/aa")[fid][0] == "local"


def test_the_machine_name_is_ref_safe():
    import re

    assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", gx.machine_name())


def test_the_export_is_off_unless_asked_for(monkeypatch):
    """1.17.15 sweep: the per-machine layout can still bring back a withdrawn share, so it stays opt-in."""
    monkeypatch.delenv("EMPIRICA_VISIBILITY_NOTES", raising=False)
    assert gx.visibility_notes_enabled() is False
    monkeypatch.setenv("EMPIRICA_VISIBILITY_NOTES", "1")
    assert gx.visibility_notes_enabled() is True
