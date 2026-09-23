"""Falsifiers, FALSIFIER_SPEC Phase 1: register against a belief, surface while open, adjudicate.

The properties pinned here are the ones the spec names as the point:
- a falsifier with no belief behind it is refused, not stored (section 3);
- it outlives its transaction and is surfaced until someone adjudicates it;
- `survived` with no evidence is recorded as `expired` (section 6);
- a capped list says it is capped.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.core import falsifiers as fz
from empirica.data.migrations.migrations import migration_075_falsifiers


class _DB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        c = self.conn
        c.execute("CREATE TABLE sessions (session_id TEXT, project_id TEXT)")
        c.execute("INSERT INTO sessions VALUES ('s1', 'p1')")
        for table in ("project_findings", "assumptions", "decisions", "project_dead_ends", "mistakes_made"):
            c.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, visibility TEXT)")
        c.execute("CREATE TABLE lessons (id TEXT PRIMARY KEY, sharing_policy TEXT)")
        c.execute("CREATE TABLE project_unknowns (id TEXT PRIMARY KEY, visibility TEXT)")
        c.execute("INSERT INTO project_findings VALUES ('f0000000-aaaa', 'shared')")
        c.execute("INSERT INTO assumptions VALUES ('a0000000-bbbb', NULL)")
        c.execute("INSERT INTO project_dead_ends VALUES ('d0000000-cccc', 'local')")
        c.execute("INSERT INTO mistakes_made VALUES ('m0000000-dddd', 'shared')")
        c.execute("INSERT INTO lessons VALUES ('l0000000-eeee', 'org')")
        c.execute("INSERT INTO project_unknowns VALUES ('u0000000-ffff', 'local')")
        migration_075_falsifiers(c.cursor())
        migration_075_falsifiers(c.cursor())  # idempotent


@pytest.fixture
def db():
    return _DB()


def _reg(db, *items, tx="t1"):
    return fz.register(db, session_id="s1", transaction_id=tx, phase="check", items=list(items))


def test_a_falsifier_with_no_belief_is_refused_and_not_stored(db):
    out = _reg(db, {"statement": "any publish row reading human"})
    assert out["registered"] == []
    assert "falsifies" in out["refused"][0]["reason"]
    assert db.conn.execute("SELECT count(*) FROM falsifiers").fetchone()[0] == 0


def test_a_parent_that_does_not_exist_is_refused(db):
    out = _reg(db, {"statement": "x", "falsifies": "ffffffff-none"})
    assert out["registered"] == [] and "matches no" in out["refused"][0]["reason"]


def test_a_prefix_resolves_and_visibility_is_inherited(db):
    out = _reg(db, {"statement": "a row at eco_review after ntfy_skipped_afk_off", "falsifies": "f0000000"})
    [row] = out["registered"]
    assert row["falsifies"] == "finding:f0000000-aaaa"
    vis = db.conn.execute("SELECT visibility FROM falsifiers").fetchone()[0]
    assert vis == "shared", "a shared belief must not carry a local test"


def test_a_falsifier_without_a_query_is_flagged(db):
    out = _reg(db, {"statement": "x", "falsifies": "a0000000-bbbb"})
    assert out["registered"][0]["executable"] is False
    assert "query" in out["note"]


def test_open_falsifiers_survive_their_transaction_and_report_the_total(db):
    for i in range(3):
        _reg(db, {"statement": f"s{i}", "falsifies": "f0000000-aaaa", "query": "SELECT 1"}, tx=f"t{i}")
    page = fz.open_falsifiers(db, "p1", limit=2)
    assert page["open_total"] == 3 and page["shown"] == 2 and page["truncated"] is True
    assert fz.open_falsifiers(db, "other-project") is None


def test_survived_without_evidence_is_recorded_as_expired(db):
    fid = _reg(db, {"statement": "x", "falsifies": "f0000000-aaaa"})["registered"][0]["id"]
    out = fz.adjudicate(db, transaction_id="t9", items=[{"id": fid[:8], "state": "survived"}])
    assert out["adjudicated"] == [{"id": fid, "state": "expired"}]
    assert "EXPIRED" in out["survived_without_evidence"]


def test_survived_with_evidence_stays_survived_and_closes(db):
    fid = _reg(db, {"statement": "x", "falsifies": "f0000000-aaaa"})["registered"][0]["id"]
    out = fz.adjudicate(
        db, transaction_id="t9", items=[{"id": fid, "state": "survived", "evidence": "ran the query: 0 rows of 130"}]
    )
    assert out["adjudicated"][0]["state"] == "survived"
    assert fz.open_falsifiers(db, "p1") is None, "an adjudicated falsifier is no longer surfaced"


def test_tripped_needs_what_fired_it(db):
    fid = _reg(db, {"statement": "x", "falsifies": "f0000000-aaaa"})["registered"][0]["id"]
    out = fz.adjudicate(db, transaction_id="t9", items=[{"id": fid, "state": "tripped"}])
    assert out["adjudicated"] == [] and "tripped_by" in out["rejected"][0]["reason"]
    out = fz.adjudicate(db, transaction_id="t9", items=[{"id": fid, "state": "tripped", "tripped_by": "prop_x"}])
    assert out["adjudicated"][0]["state"] == "tripped"


def test_unknown_and_already_closed_ids_are_reported_not_dropped(db):
    fid = _reg(db, {"statement": "x", "falsifies": "f0000000-aaaa"})["registered"][0]["id"]
    fz.adjudicate(db, transaction_id="t9", items=[{"id": fid, "state": "expired"}])
    out = fz.adjudicate(
        db,
        transaction_id="t9",
        items=[
            {"id": fid, "state": "expired"},
            {"id": "deadbeef-0000", "state": "expired"},
            {"id": fid, "state": "held"},
        ],
    )
    reasons = " | ".join(r["reason"] for r in out["rejected"])
    assert "already expired" in reasons and "no falsifier matches" in reasons and "state must be" in reasons


def test_counts_cover_every_state(db):
    fid = _reg(db, {"statement": "x", "falsifies": "f0000000-aaaa"})["registered"][0]["id"]
    _reg(db, {"statement": "y", "falsifies": "f0000000-aaaa"})
    fz.adjudicate(db, transaction_id="t9", items=[{"id": fid, "state": "tripped", "evidence": "row found"}])
    assert fz.counts(db, "p1") == {"registered": 1, "tripped": 1, "survived": 0, "expired": 0}


# David widened the parent set on 2026-09-23: a falsifier tests anything that
# asserts something observable, not only the three "belief" types.
def test_a_dead_end_can_be_falsified(db):
    """The best case in the set: dead-ends are never resolved by policy, so a
    falsifier is the only thing that can ever retire a stale one."""
    out = _reg(db, {"statement": "the approach succeeds on any run after the upgrade", "falsifies": "d0000000-cccc"})
    assert out["registered"][0]["falsifies"] == "dead_end:d0000000-cccc"


def test_a_mistakes_prevention_can_be_falsified(db):
    out = _reg(
        db,
        {
            "statement": "the same mistake is logged again after the prevention was in place",
            "falsifies": "m0000000-dddd",
        },
    )
    assert out["registered"][0]["falsifies"] == "mistake:m0000000-dddd"


def test_a_lessons_sharing_policy_is_TRANSLATED_not_dropped(db):
    """`lessons` uses org/private/public. Mapping `org` to `local` made the test
    LESS visible than the lesson, which is the invariant inheriting exists to
    satisfy — and `org` is the most common value on this store."""
    _reg(db, {"statement": "a peer applies it and it does not hold", "falsifies": "l0000000-eeee"})
    assert db.conn.execute("SELECT visibility FROM falsifiers").fetchone()[0] == "shared"


def test_an_unrecognised_policy_falls_to_the_closed_value(db):
    """Positive control for the translation: an unknown word is not passed through."""
    db.conn.execute("INSERT INTO lessons VALUES ('l1111111-ffff', 'licensed')")
    _reg(db, {"statement": "x", "falsifies": "l1111111-ffff"})
    row = db.conn.execute("SELECT visibility FROM falsifiers WHERE parent_id = 'l1111111-ffff'").fetchone()
    assert row[0] == "local"


def test_a_session_with_no_project_is_refused_rather_than_written_unreachable(db, monkeypatch):
    """Both read surfaces filter on project_id, so a row without one is accepted
    and then invisible. 525 of core's 1346 session rows carry no project_id."""
    db.conn.execute("UPDATE sessions SET project_id = NULL")
    # The active-project fallback reads this machine; pin it so the test measures
    # the refusal and not the developer's checkout.
    monkeypatch.setattr(
        "empirica.utils.session_resolver.InstanceResolver.project_id_from_db", staticmethod(lambda *_a, **_k: None)
    )
    out = fz.register(
        db,
        session_id="s1",
        transaction_id="t1",
        phase="check",
        items=[{"statement": "x", "falsifies": "f0000000-aaaa"}],
    )
    assert out["registered"] == []
    assert "never be seen" in out["refused"][0]["reason"] or "never seen" in out["refused"][0]["reason"]
    assert db.conn.execute("SELECT count(*) FROM falsifiers").fetchone()[0] == 0


def test_an_unknown_cannot_be_falsified_and_the_refusal_says_why(db):
    out = _reg(db, {"statement": "x", "falsifies": "u0000000-ffff"})
    assert out["registered"] == []
    assert "asserts nothing" in out["refused"][0]["reason"]
