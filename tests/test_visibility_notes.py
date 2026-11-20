"""A rebuild from the git notes restores each artifact's shared or public tag (goal ecea0fd4).

The per-artifact notes carry no visibility, so a rebuild brought everything back local: safe, but a practice silently stopped
sending what it had shared. The POSTFLIGHT exporter now writes the non-local tags as 256 shard notes,
empirica/visibility/<first two hex digits of the id>, rewriting only a shard whose content changed; the rebuild applies them.
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


def _finding(db, visibility):
    fid = str(uuid.uuid4())
    db.conn.execute(
        "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, finding_data, visibility) "
        "VALUES (?, 'p', 's', 'x', 1.0, '{}', ?)",
        (fid, visibility),
    )
    db.conn.commit()
    return fid


def test_shared_and_public_tags_come_back_and_local_stays_local(practice, tmp_path):
    db = SessionDatabase()
    try:
        shared, public, local = _finding(db, "shared"), _finding(db, "public"), _finding(db, "local")
        out = gx.export_visibility_notes(db.conn, str(practice))
        assert out["written"] >= 1 and out["failed"] == 0
        assert gx.export_visibility_notes(db.conn, str(practice))["written"] == 0  # nothing changed: nothing rewritten
        db.conn.execute("UPDATE project_findings SET visibility = 'local' WHERE id = ?", (public,))
        db.conn.commit()
        assert gx.export_visibility_notes(db.conn, str(practice))["written"] == 1  # one shard changed
    finally:
        db.close()

    tiers = gx.discover_visibility_notes(str(practice))
    assert tiers == {shared: "shared"}  # the public one became local; local is never written

    from empirica.cli.command_handlers.sync_commands import _rebuild_visibility

    fresh = SessionDatabase(db_path=str(tmp_path / "fresh.db"))
    try:
        for fid in (shared, public, local):
            fresh.conn.execute(
                "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, finding_data, "
                "visibility) VALUES (?, 'p', 's', 'x', 1.0, '{}', 'local')",
                (fid,),
            )
        fresh.conn.commit()
        rebuilt: dict = {}
        _rebuild_visibility(fresh, rebuilt, str(practice))
        got = dict(fresh.conn.execute("SELECT id, visibility FROM project_findings").fetchall())
        assert got == {shared: "shared", public: "local", local: "local"} and rebuilt["visibility_restored"] == 1
    finally:
        fresh.close()


def test_the_shards_follow_the_id_prefix_and_a_garbage_note_is_ignored(practice):
    assert gx._visibility_shard("ab12cd34-0000") == "ab" and gx._visibility_shard("Z-weird") == "other"
    gx._write_note(str(practice), f"{gx.VISIBILITY_PREFIX}/zz", {"tiers": {"x": "banana", "y": "shared"}})
    assert gx.discover_visibility_notes(str(practice)) == {"y": "shared"}  # only tiers that exist


def test_the_full_export_and_the_plan_include_visibility(practice):
    db = SessionDatabase()
    try:
        _finding(db, "shared")
        assert "visibility" in gx.export_graph_notes(db.conn, str(practice), full=True)
        assert gx.plan_graph_notes(db.conn, str(practice))["visibility_tagged"] == 1
    finally:
        db.close()


def test_a_full_export_rewrites_a_shard_that_has_emptied(practice):
    """1.17.14 sweep: full mode started from no watermark, so an emptied shard was never rewritten and a tag moved back to
    local came back shared on a rebuild (and the same on a fresh clone, with no watermark file)."""
    db = SessionDatabase()
    try:
        fid = _finding(db, "shared")
        gx.export_visibility_notes(db.conn, str(practice))
        db.conn.execute("UPDATE project_findings SET visibility = 'local' WHERE id = ?", (fid,))
        db.conn.commit()
        (practice / ".empirica" / "notes_export.json").unlink()  # a fresh clone has none
        gx.export_visibility_notes(db.conn, str(practice), full=True)
    finally:
        db.close()
    assert fid not in gx.discover_visibility_notes(str(practice))


@pytest.mark.parametrize("artifact_id", ["../x", ".hidden", "a:b", "-weird"])
def test_an_id_that_cannot_name_a_git_ref_still_gets_a_shard(artifact_id):
    shard = gx._visibility_shard(artifact_id)
    import re

    assert re.fullmatch(r"[0-9a-z]{2}|other", shard)
