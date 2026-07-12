"""Log-time goal-attachment edges (weave-gate timing fix).

The weave-gate enforces graph connectivity at CHECK, but the structural
`attached_to` (artifact→goal) edge used to be written only at POSTFLIGHT — so an
artifact logged under an active goal read as unconnected at CHECK and the gate
false-blocked the disciplined goal-per-transaction flow. Now every `log_*` method
materializes the edge at log time (idempotent `INSERT OR IGNORE`, best-effort).
"""

from __future__ import annotations

import uuid

import pytest

from empirica.data.session_database import SessionDatabase

PROJECT_ID = str(uuid.uuid4())
SESSION_ID = str(uuid.uuid4())
GOAL_ID = str(uuid.uuid4())


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "attach.db"))
    yield d
    d.close()


def _edge_count(db, artifact_id):
    return db.conn.execute(
        "SELECT COUNT(*) FROM artifact_edges WHERE from_id = ? AND to_id = ? AND relation = 'attached_to'",
        (artifact_id, GOAL_ID),
    ).fetchone()[0]


def test_finding_under_goal_attaches_at_log_time(db):
    fid = db.log_finding(PROJECT_ID, SESSION_ID, "a finding under a goal", goal_id=GOAL_ID)
    assert _edge_count(db, fid) == 1


def test_unknown_under_goal_attaches(db):
    uid = db.log_unknown(PROJECT_ID, SESSION_ID, "an unknown under a goal", goal_id=GOAL_ID)
    assert _edge_count(db, uid) == 1


def test_finding_without_goal_has_no_attach_edge(db):
    fid = db.log_finding(PROJECT_ID, SESSION_ID, "a goalless finding")
    cnt = db.conn.execute(
        "SELECT COUNT(*) FROM artifact_edges WHERE from_id = ? AND relation = 'attached_to'", (fid,)
    ).fetchone()[0]
    assert cnt == 0


def test_attach_is_idempotent(db):
    # Two logs under the same goal, plus a manual re-attach — INSERT OR IGNORE
    # keeps exactly one edge per (artifact, goal).
    fid = db.log_finding(PROJECT_ID, SESSION_ID, "idempotent attach", goal_id=GOAL_ID)
    db.breadcrumbs._attach_to_goal(fid, GOAL_ID)  # re-run — must not duplicate
    assert _edge_count(db, fid) == 1


def test_attach_noop_when_goal_none(db):
    # _attach_to_goal with no goal is a silent no-op (never raises, writes nothing).
    fid = db.log_finding(PROJECT_ID, SESSION_ID, "no goal")
    db.breadcrumbs._attach_to_goal(fid, None)
    cnt = db.conn.execute("SELECT COUNT(*) FROM artifact_edges WHERE from_id = ?", (fid,)).fetchone()[0]
    assert cnt == 0


def test_backfill_attaches_transaction_orphans(db):
    # The log-then-create-goal order: a finding logged with a transaction_id but no
    # goal is orphaned; backfill (fired by goal-create) attaches it to the new goal.
    TX = "tx-backfill-1"
    fid = db.log_finding(PROJECT_ID, "sess-bf", "orphan before goal", transaction_id=TX)
    assert (
        db.conn.execute(
            "SELECT COUNT(*) FROM artifact_edges WHERE from_id=? AND relation='attached_to'", (fid,)
        ).fetchone()[0]
        == 0
    )
    n = db.breadcrumbs.backfill_goal_attachment("goal-bf", "sess-bf", TX)
    assert n == 1
    assert (
        db.conn.execute(
            "SELECT COUNT(*) FROM artifact_edges WHERE from_id=? AND to_id='goal-bf' AND relation='attached_to'",
            (fid,),
        ).fetchone()[0]
        == 1
    )


def test_backfill_skips_already_attached(db):
    # An artifact already bound to a goal is left alone (not re-attached elsewhere).
    TX = "tx-backfill-2"
    fid = db.log_finding(PROJECT_ID, "sess-bf2", "already attached", goal_id="goal-first", transaction_id=TX)
    n = db.breadcrumbs.backfill_goal_attachment("goal-second", "sess-bf2", TX)
    assert n == 0  # already has an attached_to edge → skipped
    # still only bound to the first goal
    tos = [
        r[0]
        for r in db.conn.execute(
            "SELECT to_id FROM artifact_edges WHERE from_id=? AND relation='attached_to'", (fid,)
        ).fetchall()
    ]
    assert tos == ["goal-first"]


def test_backfill_noop_without_transaction(db):
    assert db.breadcrumbs.backfill_goal_attachment("g", "s", None) == 0
