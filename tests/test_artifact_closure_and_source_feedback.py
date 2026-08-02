"""Phases 2-3: closing permanent-constraint artifacts, and feeding sources.

Before this, `dead_end` (750 rows) and `mistake` (133) could not be closed at all and
`decision` (486) had never been assessed. Nothing flowed back to the sources those
artifacts cite, so source quality was unmeasurable.
"""

from __future__ import annotations

import json

import pytest

from empirica.cli.command_handlers import graph_commands as gc


@pytest.fixture
def db(monkeypatch, tmp_path):
    from empirica.data.session_database import SessionDatabase

    # tmp_path, not mkdtemp — same leak class as the four cortex reported. This
    # one they did NOT report (its dirs are indistinguishable from the 820 they
    # attributed to test_migration_060, since both write "t.db"), which is why
    # the sweep was by CONSTRUCT rather than by their list.
    real = SessionDatabase(db_path=str(tmp_path / "t.db"))
    conn = real.conn
    conn.execute(
        "INSERT INTO project_dead_ends (id, project_id, session_id, approach, why_failed, "
        "created_timestamp, dead_end_data) VALUES ('de1','p','s','tried X','because Y',0.0,'{}')"
    )
    conn.execute(
        "INSERT INTO decisions (id, project_id, session_id, choice, rationale, created_timestamp) "
        "VALUES ('dc1','p','s','use redis','fast',0.0)"
    )
    conn.execute(
        "INSERT INTO epistemic_sources (id, project_id, title, source_type, discovered_at) "
        "VALUES ('src1','p','A Source','doc',0.0)"
    )
    conn.execute("INSERT INTO artifact_edges (from_id, to_id, relation) VALUES ('de1','src1','sourced_from')")
    conn.commit()
    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", lambda *a, **k: real)
    yield real
    real.close()


def _outcomes(db, source_id="src1"):
    row = db.conn.execute("SELECT lifecycle_audit_log FROM epistemic_sources WHERE id=?", (source_id,)).fetchone()
    return [e for e in json.loads(row[0] or "[]") if e.get("event") == "source_outcome"]


# ── closure transitions ───────────────────────────────────────────────


def test_a_dead_end_can_finally_be_invalidated(db):
    """The whole point: a dead-end says 'approach X failed' and nothing ever retries
    it, so before this there was no event that could contradict it."""
    assert hasattr(gc, "_record_source_outcomes")
    cur = db.conn.cursor()
    import time

    cur.execute(
        "UPDATE project_dead_ends SET is_invalidated=1, invalidated_at=?, invalidated_by=?, "
        "invalidation_reason=?, last_revisited_at=? WHERE id LIKE ?",
        (time.time(), "tester", "retried — it works now", time.time(), "de1%"),
    )
    db.conn.commit()
    row = db.conn.execute("SELECT is_invalidated, invalidation_reason FROM project_dead_ends WHERE id='de1'").fetchone()
    assert row[0] == 1
    assert "works now" in row[1]


# ── source feedback ───────────────────────────────────────────────────


def test_outcome_flows_to_the_cited_source(db):
    n = gc._record_source_outcomes(db, "de1", "dead_end", "invalidated", {})
    assert n == 1
    ev = _outcomes(db)
    assert len(ev) == 1
    assert ev[0]["artifact_type"] == "dead_end"
    assert ev[0]["outcome"] == "invalidated"


def test_attribution_is_declared_not_inferred(db):
    """An invalidated artifact does NOT implicate its source by default — it may have
    failed because the REASONING was wrong. Inferring blame would slander good
    sources, so accuracy only moves when someone says so."""
    gc._record_source_outcomes(db, "de1", "dead_end", "invalidated", {})
    assert _outcomes(db)[0]["implicated"] is False, "must not blame the source by default"


def test_declared_implication_is_recorded(db):
    gc._record_source_outcomes(db, "de1", "dead_end", "invalidated", {"source_implicated": ["src1"]})
    assert _outcomes(db)[0]["implicated"] is True


def test_implicated_true_covers_every_cited_source(db):
    gc._record_source_outcomes(db, "de1", "dead_end", "invalidated", {"source_implicated": True})
    assert _outcomes(db)[0]["implicated"] is True


def test_recording_is_fail_open(db):
    """Bookkeeping must never break the resolution that triggered it."""
    assert gc._record_source_outcomes(db, "nonexistent", "finding", "confirmed", {}) == 0
    assert gc._record_source_outcomes(None, "de1", "dead_end", "invalidated", {}) == 0


def test_events_accumulate_rather_than_overwrite(db):
    """Metrics are DERIVED on read, so the trail must stay complete — no event may be
    lost to a later one."""
    gc._record_source_outcomes(db, "de1", "dead_end", "invalidated", {})
    gc._record_source_outcomes(db, "de1", "dead_end", "confirmed", {})
    assert len(_outcomes(db)) == 2


def test_existing_lifecycle_events_are_preserved(db):
    """The log already carries `repointed` and archive events — appending must not
    clobber them."""
    db.conn.execute(
        "UPDATE epistemic_sources SET lifecycle_audit_log=? WHERE id='src1'",
        (json.dumps([{"event": "repointed", "at": 1.0}]),),
    )
    db.conn.commit()
    gc._record_source_outcomes(db, "de1", "dead_end", "invalidated", {})
    row = db.conn.execute("SELECT lifecycle_audit_log FROM epistemic_sources WHERE id='src1'").fetchone()
    events = [e["event"] for e in json.loads(row[0])]
    assert "repointed" in events and "source_outcome" in events
