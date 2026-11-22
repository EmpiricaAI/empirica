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


def test_a_1_17_14_opt_in_user_who_withdraws_a_share_after_upgrading_keeps_it_local(practice, monkeypatch):
    """1.17.15 second pass: the 1.17.14 export record matched, so the first per-machine export wrote nothing, the withdrawal
    had no entry to override and the flat note kept the share. The record is now per machine: the first export is full."""
    import json as _json

    monkeypatch.setattr(gx, "machine_name", lambda: "alpha-abc123")
    db = SessionDatabase()
    try:
        fid = _finding(db, "shared", "ab000000-0000-0000-0000-000000000005")
        # What 1.17.14 left behind: a flat note and its export record.
        gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/ab", {"tiers": {fid: "shared"}})
        digest = __import__("hashlib").sha256(_json.dumps({fid: "shared"}, sort_keys=True).encode()).hexdigest()[:16]
        gx._save_watermark(str(practice), "visibility", {"ab": digest})
        assert gx.export_visibility_notes(db.conn, str(practice))["written"] == 1
        db.conn.execute("UPDATE project_findings SET visibility = 'local' WHERE id = ?", (fid,))
        db.conn.commit()
        gx.export_visibility_notes(db.conn, str(practice))
    finally:
        db.close()
    assert gx.discover_visibility_notes(str(practice))[fid] == "local"


def test_a_share_withdrawn_while_the_flag_was_off_overrides_the_1_17_14_note(practice, monkeypatch):
    """1.17.15 second pass, round 2: withdrawn with the export off, so this machine's notes never held the id and only the
    1.17.14 flat note did. The first full export under a name overrides flat entries this database holds as local, as
    1.17.14 dropped them when it rewrote the shard."""
    monkeypatch.setattr(gx, "machine_name", lambda: "alpha-abc123")
    db = SessionDatabase()
    try:
        fid = _finding(db, "local", "ab000000-0000-0000-0000-000000000006")
        kept = _finding(db, "shared", "ab000000-0000-0000-0000-000000000007")
        gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/ab", {"tiers": {fid: "shared", kept: "shared"}})
        gx.export_visibility_notes(db.conn, str(practice))
    finally:
        db.close()
    tiers = gx.discover_visibility_notes(str(practice))
    assert tiers[fid] == "local" and tiers[kept] == "shared"


def test_an_empty_export_record_is_not_a_first_export(practice, monkeypatch):
    """1.17.15 second pass, round 3: an empty record read as 'first export', so every POSTFLIGHT re-read the flat notes."""
    monkeypatch.setattr(gx, "machine_name", lambda: "alpha-abc123")
    calls = []
    monkeypatch.setattr(gx, "_flat_visibility_shards", lambda root: calls.append(1) or [])
    db = SessionDatabase()
    try:
        gx.export_visibility_notes(db.conn, str(practice))
        gx.export_visibility_notes(db.conn, str(practice))
    finally:
        db.close()
    assert len(calls) == 1


def test_the_legacy_override_never_beats_another_machines_entry(practice, monkeypatch):
    """Round 3: only a flat entry that is still the winner is overridden; another machine's own entry stands."""
    monkeypatch.setattr(gx, "machine_name", lambda: "alpha-abc123")
    db = SessionDatabase()
    try:
        fid = _finding(db, "local", "ab000000-0000-0000-0000-000000000008")
        gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/ab", {"tiers": {fid: "shared"}})
        gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/beta-def456/ab", {"tiers": {fid: ["shared", 50.0]}})
        gx.export_visibility_notes(db.conn, str(practice))
    finally:
        db.close()
    assert gx.discover_visibility_notes(str(practice))[fid] == "shared"


def test_a_shard_whose_first_write_failed_is_retried_by_the_next_incremental_export(practice, monkeypatch):
    """Goal c29b6931: a shard with no previous digest got an empty one on a failed write and dropped out of the record; a
    shard holding no shared tag of this database (here, the first export's override of a 1.17.14 note) was never a
    candidate again until a full export."""
    monkeypatch.setattr(gx, "machine_name", lambda: "alpha-abc123")
    db = SessionDatabase()
    try:
        fid = _finding(db, "local", "cd000000-0000-0000-0000-000000000009")
        _finding(db, "shared", "ef000000-0000-0000-0000-000000000010")  # so the record is not empty after the first run
        gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/cd", {"tiers": {fid: "shared"}})
        real = gx._write_note
        monkeypatch.setattr(
            gx, "_write_note", lambda root, ref, payload: False if ref.endswith("/cd") else real(root, ref, payload)
        )
        assert gx.export_visibility_notes(db.conn, str(practice))["failed"] == 1
        monkeypatch.setattr(gx, "_write_note", real)
        assert gx.export_visibility_notes(db.conn, str(practice))["written"] == 1
    finally:
        db.close()
    assert gx.discover_visibility_notes(str(practice))[fid] == "local"


def test_a_write_that_keeps_failing_stops_forcing_full_exports(practice, monkeypatch):
    """1.17.16 sweep: a shard that always failed kept a retry marker forever, and every POSTFLIGHT ran a full export
    (514 git calls, about 4.5 s, on 256 shards). After three failed full retries it is retried on its own."""
    monkeypatch.setattr(gx, "machine_name", lambda: "alpha-abc123")
    db = SessionDatabase()
    flat_reads = []
    try:
        _finding(db, "shared", "ab000000-0000-0000-0000-000000000011")
        real = gx._write_note
        monkeypatch.setattr(
            gx, "_write_note", lambda root, ref, payload: False if ref.endswith("/ab") else real(root, ref, payload)
        )
        monkeypatch.setattr(gx, "_flat_visibility_shards", lambda root: flat_reads.append(1) or [])
        for _ in range(6):
            assert gx.export_visibility_notes(db.conn, str(practice))["failed"] == 1
    finally:
        db.close()
    assert len(flat_reads) == 4  # the first export, then three full retries
