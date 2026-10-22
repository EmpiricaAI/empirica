"""`rebuild --from-notes` must restore an artifact as itself: its id, its time, and how it was closed (goal d897e750, HIGH item).

The rebuild re-created every artifact through ``db.log_*``, which mints a NEW id and stamps NOW. The notes are the canonical
log, so a rebuilt database disagreed with the one it replaced about every identity in it: edges, citations, `superseded_by`
pointers and Qdrant points all name the OLD ids and found nothing, and every artifact claimed to have been written at the
instant of the rebuild, which flattens the recency ordering retrieval ranks by. Resolutions came back without their kind and
without their time (``resolution_kind`` / ``resolved_at`` were in the note and dropped).

Notes are fed in as the dicts ``discover_*`` returns, into a real database: a stub that accepted what the code passes would
have been green throughout.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from empirica.cli.command_handlers.sync_commands import _rebuild_insert_breadcrumbs
from empirica.data.session_database import SessionDatabase

PID, SID = str(uuid.uuid4()), str(uuid.uuid4())
WHEN = "2026-03-04T05:06:07+00:00"
WHEN_TS = datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone.utc).timestamp()
COUNTS = {"findings": 0, "unknowns": 0, "dead_ends": 0, "mistakes": 0}


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "s.db"))
    d.conn.execute("PRAGMA foreign_keys=OFF")
    yield d
    d.close()


def _rebuild(db, findings=(), unknowns=(), dead_ends=(), mistakes=(), goals=()):
    rebuilt = dict(COUNTS)
    _rebuild_insert_breadcrumbs(
        db, list(findings), list(unknowns), list(dead_ends), list(mistakes), set(goals), rebuilt
    )
    return rebuilt


def _finding(text="a finding", **extra):
    return {
        "finding_id": str(uuid.uuid4()),
        "project_id": PID,
        "session_id": SID,
        "created_at": WHEN,
        "finding": text,
        "impact": 0.6,
        **extra,
    }


def _row(db, table, id_):
    return db.conn.execute(f"SELECT id, created_timestamp FROM {table} WHERE id = ?", (id_,)).fetchone()


def test_every_artifact_type_comes_back_under_its_own_id_and_time(db):
    f = _finding()
    u = {"unknown_id": str(uuid.uuid4()), "project_id": PID, "session_id": SID, "created_at": WHEN, "unknown": "why?"}
    d = {
        "dead_end_id": str(uuid.uuid4()),
        "project_id": PID,
        "session_id": SID,
        "created_at": WHEN,
        "approach": "tried",
        "why_failed": "no",
    }
    m = {
        "mistake_id": str(uuid.uuid4()),
        "project_id": PID,
        "session_id": SID,
        "created_at": WHEN,
        "mistake": "slip",
        "why_wrong": "w",
        "prevention": "p",
    }
    rebuilt = _rebuild(db, [f], [u], [d], [m])
    assert rebuilt == {"findings": 1, "unknowns": 1, "dead_ends": 1, "mistakes": 1}
    for table, id_ in (
        ("project_findings", f["finding_id"]),
        ("project_unknowns", u["unknown_id"]),
        ("project_dead_ends", d["dead_end_id"]),
        ("mistakes_made", m["mistake_id"]),
    ):
        row = _row(db, table, id_)
        assert row is not None, f"{table}: the note's id must be the row's id"
        assert row[1] == pytest.approx(WHEN_TS), f"{table}: the note's time, not the rebuild's"


def test_a_naive_or_zulu_timestamp_is_read_as_utc(db):
    zulu, naive = (
        _finding("zulu", created_at="2026-03-04T05:06:07Z"),
        _finding("naive", created_at="2026-03-04T05:06:07"),
    )
    _rebuild(db, [zulu, naive])
    assert _row(db, "project_findings", zulu["finding_id"])[1] == pytest.approx(WHEN_TS)
    assert _row(db, "project_findings", naive["finding_id"])[1] == pytest.approx(WHEN_TS)


def test_the_goal_edge_follows_the_restored_id(db):
    goal = str(uuid.uuid4())
    f = _finding(goal_id=goal)
    _rebuild(db, [f], goals=[goal])
    edges = db.conn.execute(
        "SELECT from_id FROM artifact_edges WHERE to_id = ? AND relation = 'attached_to'", (goal,)
    ).fetchall()
    assert [e[0] for e in edges] == [f["finding_id"]], "no edge may be left naming the throwaway id"


def test_a_resolution_comes_back_with_its_kind_and_time(db):
    f = _finding(
        is_resolved=True, resolution="was wrong", resolution_kind="retracted", resolved_at="2026-04-05T06:07:08+00:00"
    )
    _rebuild(db, [f])
    kind, resolved = db.conn.execute(
        "SELECT resolution_kind, resolved_timestamp FROM project_findings WHERE id = ?", (f["finding_id"],)
    ).fetchone()
    assert kind == "retracted"
    assert resolved == pytest.approx(datetime(2026, 4, 5, 6, 7, 8, tzinfo=timezone.utc).timestamp())


def test_an_unclosed_finding_stays_unclosed_with_no_kind(db):
    f = _finding()
    _rebuild(db, [f])
    row = db.conn.execute(
        "SELECT COALESCE(is_resolved, 0), resolution_kind, resolved_timestamp FROM project_findings WHERE id = ?",
        (f["finding_id"],),
    ).fetchone()
    assert tuple(row) == (0, None, None)


def test_two_notes_with_the_same_content_do_not_rename_the_survivor(db):
    """log_finding deduplicates on content and hands back the EXISTING id: restoring the second note's id onto that row would
    rename an artifact other rows already point at."""
    a, b = _finding("same words"), _finding("same words")
    _rebuild(db, [a, b])
    rows = db.conn.execute("SELECT id FROM project_findings WHERE finding = 'same words'").fetchall()
    assert [r[0] for r in rows] == [a["finding_id"]]


def test_a_row_that_already_holds_the_notes_id_is_left_alone(db):
    first = _finding("original wording")
    _rebuild(db, [first])
    clash = _finding("different wording", finding_id=first["finding_id"])
    _rebuild(db, [clash])
    rows = db.conn.execute("SELECT id, finding FROM project_findings").fetchall()
    assert ("{}".format(first["finding_id"]), "original wording") in [tuple(r) for r in rows]
    assert len(rows) == 2, "the clashing note still rebuilds, under a fresh id, rather than overwriting or vanishing"


def test_a_legacy_note_without_an_id_or_time_rebuilds_as_before(db):
    legacy = {"project_id": PID, "session_id": SID, "finding": "from an old note", "impact": 0.5}
    rebuilt = _rebuild(db, [legacy])
    assert rebuilt["findings"] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM project_findings").fetchone()[0] == 1


def test_an_unknown_resolution_keeps_its_time(db):
    u = {
        "unknown_id": str(uuid.uuid4()),
        "project_id": PID,
        "session_id": SID,
        "created_at": WHEN,
        "unknown": "why?",
        "resolved": True,
        "resolved_by": "answered",
        "resolved_at": "2026-04-05T06:07:08+00:00",
    }
    _rebuild(db, unknowns=[u])
    resolved = db.conn.execute(
        "SELECT resolved_timestamp FROM project_unknowns WHERE id = ?", (u["unknown_id"],)
    ).fetchone()[0]
    assert resolved == pytest.approx(datetime(2026, 4, 5, 6, 7, 8, tzinfo=timezone.utc).timestamp())
