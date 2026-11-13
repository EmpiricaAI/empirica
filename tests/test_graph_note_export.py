"""Links between artifacts reach the git notes and come back on a rebuild (1.16.x, goal 966808c0).

artifact_edges lived in SQLite only, so a rebuild from the notes lost every link. An exporter run at each POSTFLIGHT (and
in full by doctor --reconcile-notes) writes one note per source artifact, empirica/edges/<from_id>, listing its edges; the
rebuild reads them back.
"""

from __future__ import annotations

import subprocess

import pytest

from empirica.cli.command_handlers.sync_commands import _rebuild_edges
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


def _edge(db, src, dst, rel, at, meta=None):
    db.conn.execute(
        "INSERT INTO artifact_edges (from_id, to_id, relation, created_at, metadata) VALUES (?, ?, ?, ?, ?)",
        (src, dst, rel, at, meta),
    )
    db.conn.commit()


def test_links_exported_then_rebuilt_come_back(practice, tmp_path):
    db = SessionDatabase()
    try:
        _edge(db, "f1", "f2", "evidence", "2026-11-01 10:00:00")
        _edge(db, "f1", "g1", "attached_to", "2026-11-01 10:00:01")
        _edge(db, "u1", "f1", "invalidates", "2026-11-02 09:00:00", '{"kind": "retracted"}')
        out = gx.export_edge_notes(db.conn, str(practice))
        assert out["written"] == 2  # two source artifacts
        _edge(db, "d1", "f1", "grounded_by", "2026-11-03 08:00:00")
        again = gx.export_edge_notes(db.conn, str(practice))
        assert again["written"] == 1  # only what changed since the watermark
    finally:
        db.close()

    fresh = SessionDatabase(db_path=str(tmp_path / "fresh.db"))
    try:
        rebuilt: dict = {}
        _rebuild_edges(fresh, rebuilt, str(practice))
        rows = sorted(
            tuple(r)
            for r in fresh.conn.execute("SELECT from_id, to_id, relation, metadata FROM artifact_edges").fetchall()
        )
    finally:
        fresh.close()
    assert rebuilt["edges"] == 4
    assert rows == [
        ("d1", "f1", "grounded_by", None),
        ("f1", "f2", "evidence", None),
        ("f1", "g1", "attached_to", None),
        ("u1", "f1", "invalidates", '{"kind": "retracted"}'),
    ]


def test_a_bounded_run_resumes_where_it_stopped(practice):
    db = SessionDatabase()
    try:
        for i in range(5):
            _edge(db, f"a{i}", "x", "related", f"2026-11-01 10:00:0{i}")
        assert gx.export_edge_notes(db.conn, str(practice), limit=2)["written"] == 2
        assert gx.export_edge_notes(db.conn, str(practice), limit=2)["written"] == 2
        assert gx.export_edge_notes(db.conn, str(practice), limit=2)["written"] == 1
        assert gx.export_edge_notes(db.conn, str(practice), limit=2)["written"] == 0
    finally:
        db.close()


def test_falsifiers_exported_then_rebuilt_come_back_with_their_verdict(practice, tmp_path):
    from empirica.cli.command_handlers.sync_commands import _rebuild_falsifiers

    db = SessionDatabase()
    try:
        cols = "id, project_id, session_id, statement, query, state, registered_at, parent_type, parent_id"
        db.conn.execute(
            f"INSERT INTO falsifiers ({cols}) VALUES ('fa1', 'p', 's', 'a row reads human', 'select 1', 'open', 100.0, 'finding', 'f1')"
        )
        db.conn.execute(
            f"INSERT INTO falsifiers ({cols}, evidence, adjudicated_at) VALUES ('fa2', 'p', 's', 'x', NULL, 'tripped', 101.0, "
            "'assumption', 'a1', 'it fired', 200.0)"
        )
        db.conn.commit()
        assert gx.export_falsifier_notes(db.conn, str(practice))["written"] == 2
        assert gx.export_falsifier_notes(db.conn, str(practice))["written"] == 0
        db.conn.execute("UPDATE falsifiers SET state = 'survived', adjudicated_at = 300.0 WHERE id = 'fa1'")
        db.conn.commit()
        assert gx.export_falsifier_notes(db.conn, str(practice))["written"] == 1
    finally:
        db.close()

    fresh = SessionDatabase(db_path=str(tmp_path / "fresh.db"))
    try:
        rebuilt: dict = {}
        _rebuild_falsifiers(fresh, rebuilt, str(practice))
        rows = dict(fresh.conn.execute("SELECT id, state FROM falsifiers").fetchall())
    finally:
        fresh.close()
    assert rebuilt["falsifiers"] == 2 and rows == {"fa1": "survived", "fa2": "tripped"}


def test_lessons_exported_then_rebuilt_come_back(practice, tmp_path):
    from empirica.cli.command_handlers.sync_commands import _rebuild_lessons
    from empirica.core.lessons import EpistemicDelta, Lesson, LessonEpistemic
    from empirica.core.lessons.storage import LessonStorageManager

    db = SessionDatabase()
    try:
        store = LessonStorageManager(
            db_conn=db.conn, cold_storage_path=practice / ".empirica" / "lessons", qdrant_client=None
        )
        for name in ("read before writing", "commit per task"):
            store.create_lesson(
                Lesson(
                    id=f"id-{name[:4]}",
                    name=name,
                    version="1.0",
                    description=f"{name}: why it matters",
                    epistemic=LessonEpistemic(
                        source_confidence=0.8,
                        teaching_quality=0.8,
                        reproducibility=0.8,
                        expected_delta=EpistemicDelta(),
                    ),
                )
            )
        assert gx.export_lesson_notes(db.conn, str(practice))["written"] == 2
        assert gx.export_lesson_notes(db.conn, str(practice))["written"] == 0
        # A lesson whose full record is in the table only (no YAML file) is exported too: the store reads it as a fallback.
        (practice / ".empirica" / "lessons" / "id-comm.yaml").unlink()
        db.conn.execute("UPDATE lessons SET updated_timestamp = updated_timestamp + 10 WHERE id = 'id-comm'")
        db.conn.commit()
        assert gx.export_lesson_notes(db.conn, str(practice))["written"] == 1
    finally:
        db.close()

    fresh = SessionDatabase(db_path=str(tmp_path / "fresh.db"))
    try:
        rebuilt: dict = {}
        _rebuild_lessons(fresh, rebuilt, str(practice), cold_storage_path=tmp_path / "fresh_lessons")
        names = sorted(r[0] for r in fresh.conn.execute("SELECT name FROM lessons").fetchall())
    finally:
        fresh.close()
    assert rebuilt["lessons"] == 2 and names == ["commit per task", "read before writing"]
    assert sorted(p.name for p in (tmp_path / "fresh_lessons").iterdir()) == ["id-comm.yaml", "id-read.yaml"]


def test_the_postflight_stage_exports_the_practices_links(practice, monkeypatch):
    from empirica.cli.command_handlers import _workflow_postflight as wp

    db = SessionDatabase()
    try:
        _edge(db, "f1", "f2", "evidence", "2026-11-01 10:00:00")
    finally:
        db.close()
    monkeypatch.setattr(wp, "_get_db_for_session", lambda sid: SessionDatabase())
    out = wp._pipeline_graph_notes_export("s1", "p1")
    assert out["edges"]["written"] == 1
    assert [n["from_id"] for n in gx.discover_edge_notes(str(practice))] == ["f1"]


def test_doctor_reconcile_lists_and_exports_the_graph_notes(practice):
    db = SessionDatabase()
    try:
        _edge(db, "f1", "f2", "evidence", "2026-11-01 10:00:00")
        assert gx.plan_graph_notes(db.conn, str(practice)) == {"edge_sources": 1, "falsifiers": 0, "lessons": 0}
        out = gx.export_graph_notes(db.conn, str(practice), full=True)
    finally:
        db.close()
    assert out["edges"]["written"] == 1 and out["falsifiers"]["written"] == 0 and out["lessons"]["written"] == 0


# ── deletions, id swaps and same-second ties (goal b29f37b0, 1.16.1 sweep A1-A3) ──────────────────────────────────────


def _rebuilt_edges(tmp_path, practice):
    fresh = SessionDatabase(db_path=str(tmp_path / "fresh.db"))
    try:
        _rebuild_edges(fresh, {}, str(practice))
        return sorted(
            tuple(r) for r in fresh.conn.execute("SELECT from_id, to_id, relation FROM artifact_edges").fetchall()
        )
    finally:
        fresh.close()


def test_a_deleted_link_does_not_come_back_on_rebuild(practice, tmp_path):
    from empirica.cli.command_handlers.graph_commands import _delete_artifact_edges

    db = SessionDatabase()
    try:
        _edge(db, "a", "x", "evidence", "2026-11-01 10:00:00")
        _edge(db, "b", "x", "evidence", "2026-11-01 10:00:01")
        _edge(db, "b", "y", "evidence", "2026-11-01 10:00:02")
        assert gx.export_edge_notes(db.conn, str(practice))["written"] == 2
        _delete_artifact_edges(db.conn.cursor(), "x")  # x deleted: a loses its only link, b keeps b->y
        db.conn.commit()
        gx.export_edge_notes(db.conn, str(practice))
    finally:
        db.close()
    assert _rebuilt_edges(tmp_path, practice) == [("b", "y", "evidence")]


def test_a_rolled_back_delete_leaves_the_note_as_it_was(practice, tmp_path):
    from empirica.cli.command_handlers.graph_commands import _delete_artifact_edges

    db = SessionDatabase()
    try:
        _edge(db, "a", "x", "evidence", "2026-11-01 10:00:00")
        gx.export_edge_notes(db.conn, str(practice))
        _delete_artifact_edges(db.conn.cursor(), "x")
        db.conn.rollback()  # the delete never happened: the note is rewritten from what is committed
        gx.export_edge_notes(db.conn, str(practice))
    finally:
        db.close()
    assert _rebuilt_edges(tmp_path, practice) == [("a", "x", "evidence")]


def test_a_swapped_id_is_re_exported(practice, tmp_path):
    db = SessionDatabase()
    try:
        _edge(db, "f", "local-1", "sourced_from", "2026-11-01 10:00:00")
        gx.export_edge_notes(db.conn, str(practice))
        db.conn.execute("UPDATE artifact_edges SET to_id = 'cortex-1' WHERE to_id = 'local-1'")
        db.conn.commit()
        gx.mark_edges_dirty(db.conn, ["f"])
        gx.export_edge_notes(db.conn, str(practice))
    finally:
        db.close()
    assert _rebuilt_edges(tmp_path, practice) == [("f", "cortex-1", "sourced_from")]


def test_a_link_in_the_watermarks_second_from_a_smaller_id_is_not_skipped(practice, tmp_path):
    db = SessionDatabase()
    try:
        _edge(db, "m", "x", "evidence", "2026-11-01 10:00:00")
        gx.export_edge_notes(db.conn, str(practice))  # watermark: 10:00:00, m
        _edge(db, "a", "x", "evidence", "2026-11-01 10:00:00")  # same second, smaller id
        assert gx.export_edge_notes(db.conn, str(practice))["written"] == 1
        assert gx.export_edge_notes(db.conn, str(practice))["written"] == 0  # and not again
    finally:
        db.close()
    assert _rebuilt_edges(tmp_path, practice) == [("a", "x", "evidence"), ("m", "x", "evidence")]


def test_a_full_export_clears_the_note_of_a_source_that_lost_every_link(practice, tmp_path):
    db = SessionDatabase()
    try:
        _edge(db, "a", "x", "evidence", "2026-11-01 10:00:00")
        gx.export_edge_notes(db.conn, str(practice))
        db.conn.execute("DELETE FROM artifact_edges")  # behind the marking, as an older version or a hand edit would
        db.conn.commit()
        gx.export_edge_notes(db.conn, str(practice), full=True)
    finally:
        db.close()
    assert _rebuilt_edges(tmp_path, practice) == []


def test_the_sources_reconcile_swap_marks_its_sources(practice, tmp_path, monkeypatch):
    import time

    from empirica.cli.command_handlers import sources_reconcile_commands as src

    monkeypatch.setattr(src, "_swap_workspace_entity_links", lambda *a: "skipped")
    db = SessionDatabase()
    try:
        db.conn.execute(
            "INSERT INTO epistemic_sources (id, project_id, source_type, title, discovered_at, visibility) "
            "VALUES ('local-1', 'p', 'url', 'RFC', ?, 'local')",
            (time.time(),),
        )
        _edge(db, "f", "local-1", "sourced_from", "2026-11-01 10:00:00")
        gx.export_edge_notes(db.conn, str(practice))
        src._swap_source_id(db, "p", "local-1", "cortex-1")
        gx.export_edge_notes(db.conn, str(practice))
    finally:
        db.close()
    assert _rebuilt_edges(tmp_path, practice) == [("f", "cortex-1", "sourced_from")]
