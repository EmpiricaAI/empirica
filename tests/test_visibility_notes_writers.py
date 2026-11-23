"""Visibility notes correct across machines (goal 38510fdd).

Two review passes of the per-machine layout (31c9e2f57) confirmed that a withdrawn share could come back:
- an entry carried the time this machine exported it, so a machine that had pulled a share and exported after the owner
  withdrew it outranked the withdrawal;
- the writer was named from the hostname, so a renamed machine could not override its own older entries, and two clones
  on one host wrote the same refs;
- a NaN or far-future timestamp locked a tag;
- sync-push sent every machine's visibility refs, so a stale copy of a peer's refused the push.
Now the writer is named per checkout, an adopted tag keeps the time it was set, only this checkout's own older entries are
overridden, timestamps are sanity-checked, and push sends only this writer's visibility refs.
"""

from __future__ import annotations

import math
import subprocess
import time

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


def _finding(db, visibility, fid):
    db.conn.execute(
        "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, finding_data, visibility) "
        "VALUES (?, 'p', 's', 'x', 1.0, '{}', ?)",
        (fid, visibility),
    )
    db.conn.commit()
    return fid


def _set(db, fid, visibility):
    db.conn.execute("UPDATE project_findings SET visibility = ? WHERE id = ?", (visibility, fid))
    db.conn.commit()


def _export(root):
    db = SessionDatabase()
    try:
        return gx.export_visibility_notes(db.conn, str(root))
    finally:
        db.close()


def test_the_writer_is_named_per_checkout_and_kept(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        (root / ".empirica").mkdir(parents=True)
    first = gx.writer_id(str(a))
    assert first == gx.writer_id(str(a)) != gx.writer_id(str(b))
    assert (a / ".empirica" / gx.WRITER_FILE).exists()


def test_an_adopted_share_keeps_the_time_it_was_set(practice):
    """The sweep's case: B pulled A's share and exported after A withdrew it, before pulling again."""
    fid = "ab000000-0000-0000-0000-000000000001"
    gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/writer-a/ab", {"tiers": {fid: ["shared", 100.0]}})
    db = SessionDatabase()
    try:
        _finding(db, "shared", fid)  # adopted from A by a rebuild
    finally:
        db.close()
    _export(practice)  # B exports before it has seen A's withdrawal
    mine = gx._read_shard(str(practice), f"{gx.writer_id(str(practice))}/ab")
    assert mine[fid] == ["shared", 100.0]
    gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/writer-a/ab", {"tiers": {fid: ["local", 200.0]}})
    assert gx.discover_visibility_notes(str(practice))[fid] == "local"


def test_a_change_made_here_is_stamped_now(practice):
    fid = "ab000000-0000-0000-0000-000000000002"
    gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/writer-a/ab", {"tiers": {fid: ["shared", 100.0]}})
    db = SessionDatabase()
    try:
        _finding(db, "public", fid)
    finally:
        db.close()
    before = time.time()
    _export(practice)
    tier, when = gx._read_shard(str(practice), f"{gx.writer_id(str(practice))}/ab")[fid]
    assert tier == "public" and when >= before


def test_a_row_never_shared_here_does_not_withdraw_another_writers_share(practice):
    fid = "ab000000-0000-0000-0000-000000000003"
    gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/writer-a/ab", {"tiers": {fid: ["shared", 100.0]}})
    db = SessionDatabase()
    try:
        _finding(db, "local", fid)  # imported before A shared it, never rebuilt
    finally:
        db.close()
    _export(practice)
    assert gx.discover_visibility_notes(str(practice))[fid] == "shared"


def test_a_withdrawal_overrides_this_checkouts_hostname_namespace(practice, monkeypatch):
    """1.17.15 and 1.17.16 named the writer from the hostname; that namespace is this checkout's own when its export record
    is here, so a share withdrawn later overrides it."""
    monkeypatch.setattr(gx, "machine_name", lambda: "oldhost-abc123")
    fid = "ab000000-0000-0000-0000-000000000004"
    gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/oldhost-abc123/ab", {"tiers": {fid: ["shared", 100.0]}})
    gx._save_watermark(str(practice), "visibility:oldhost-abc123", {"ab": "digest"})
    db = SessionDatabase()
    try:
        _finding(db, "local", fid)
    finally:
        db.close()
    _export(practice)
    assert gx.discover_visibility_notes(str(practice))[fid] == "local"


def test_another_writers_hostname_namespace_is_not_overridden(practice, monkeypatch):
    monkeypatch.setattr(gx, "machine_name", lambda: "thishost-abc123")
    fid = "ab000000-0000-0000-0000-000000000005"
    gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/otherhost-def456/ab", {"tiers": {fid: ["shared", 100.0]}})
    db = SessionDatabase()
    try:
        _finding(db, "local", fid)
    finally:
        db.close()
    _export(practice)
    assert gx.discover_visibility_notes(str(practice))[fid] == "shared"


@pytest.mark.parametrize("when", [math.nan, math.inf, 1e300, "soon"])
def test_a_timestamp_that_is_not_a_sane_time_counts_as_oldest(when):
    entries = gx._entries({"tiers": {"x": ["shared", when]}})
    assert entries["x"] == ["shared", 0.0]


def test_a_bad_timestamp_does_not_lock_a_tag(practice):
    fid = "ab000000-0000-0000-0000-000000000006"
    gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/writer-a/ab", {"tiers": {fid: ["shared", 1e300]}})
    gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/writer-b/ab", {"tiers": {fid: ["local", 200.0]}})
    assert gx.discover_visibility_notes(str(practice))[fid] == "local"


def test_push_sends_only_this_writers_visibility_refs(practice, monkeypatch):
    from empirica.cli.command_handlers import sync_commands as sc

    monkeypatch.setattr(sc, "_get_workspace_root", lambda: str(practice))
    me = gx.writer_id(str(practice))
    for ns in (me, "writer-a"):
        gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/{ns}/ab", {"tiers": {}})
    gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/ab", {"tiers": {}})  # 1.17.14 flat
    gx._write_note(str(practice), "empirica/lessons/x", {"a": 1})
    batches = sc._note_wildcard_batches("refs/notes/empirica/*:refs/notes/empirica/*") or []
    specs = [s for batch in batches for s in batch]
    vis = [s for s in specs if "/visibility/" in s]
    assert vis == [f"refs/notes/empirica/visibility/{me}/ab:refs/notes/empirica/visibility/{me}/ab"]
    assert any("/lessons/" in s for s in specs)
