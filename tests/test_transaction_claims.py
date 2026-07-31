"""CHECK declares load-bearing claims; POSTFLIGHT adjudicates them.

`know` is a scalar over heterogeneous beliefs. An HONEST `know=0.82` can be eleven
well-grounded claims plus one pure guess — and the average conceals the guess,
which is the one that breaks. Reported by cortex from a case that cost them four
wrong design rules: genuinely grounded on field distributions, ungrounded on show
identity, honest averaged `know`, built to a stale decision record.

David's refinement is what makes it a measurement rather than a nicer-looking
CHECK: **declare at CHECK, adjudicate at POSTFLIGHT.**

The third verdict carries the value. `refuted` is rare and `held` is cheap;
`untested` — *I acted on this and never checked it* — is the state the scalar
cannot express. So the central invariants here are:

  1. An unadjudicated claim becomes `untested` and is REPORTED, never left NULL.
     "No verdict" reading as "fine" would give the mechanism the exact defect it
     exists to catch.
  2. Adjudication happens at POSTFLIGHT only. The CHECK path calls the same
     retrospective builder for artifact counts, and adjudicating there would force
     every just-declared claim to `untested` seconds after storing it — a 100% gap
     rate forever, while looking like it worked.
"""

from __future__ import annotations

import pytest

from empirica.core import claims as C
from empirica.data.session_database import SessionDatabase


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "t.db"))
    try:
        yield d
    finally:
        d.close()


def _declare(db, *claims_in, tx="tx-1", session="s-1"):
    return C.declare(db, session_id=session, transaction_id=tx, claims=list(claims_in))


# ── the vocabulary ────────────────────────────────────────────────────


def test_retrieved_is_distinct_from_read():
    """Our own prior artifacts are TESTIMONY, not observation — true when written
    and ageing like any prior. Collapsing them into `read` is the hole that cost
    cortex four wrong design rules."""
    assert "retrieved" in C.GROUNDINGS
    assert C.is_weak("retrieved") is True, "retrieved must count as weak grounding"
    assert C.is_weak("read") is False
    assert C.is_weak("ran") is False
    assert C.is_weak("assumed") is True


def test_untested_is_in_the_verdict_vocabulary():
    assert set(C.VERDICTS) == {"held", "refuted", "untested"}


def test_an_unrecognised_grounding_is_not_downgraded_to_assumed():
    """A wrong label is worse than an absent one for a mechanism whose only output
    is labels — silently calling a labelled claim `assumed` would misreport the
    practitioner's state in the pessimistic direction."""
    assert C.normalize_grounding("verified-by-vibes") is None
    assert C.normalize_grounding(None) is None


# ── declaration ───────────────────────────────────────────────────────


def test_claims_are_stored_with_ids_and_order(db):
    stored = _declare(
        db,
        {"claim": "transaction_id joins CHECK to POSTFLIGHT", "grounding": "read", "ref": "check.py:226"},
        {"claim": "advisory beats gating in v0", "grounding": "assumed"},
    )

    assert len(stored) == 2
    assert [c["index"] for c in stored] == [1, 2]
    assert all(c["id"] for c in stored)
    assert stored[0]["ref"] == "check.py:226"


def test_a_malformed_claim_is_skipped_not_fatal(db):
    """Declaration must never be able to fail a CHECK — gating the noetic→praxic
    transition on the shape of an ADVISORY payload would let a reporting feature
    block real work."""
    stored = _declare(db, {"claim": "real"}, {"no_claim_key": "x"}, "not a dict", {"claim": "   "})

    assert len(stored) == 1
    assert stored[0]["claim"] == "real"


def test_the_check_summary_surfaces_weak_grounding(db):
    """Surfacing weakness AT CHECK, while the practitioner can still act, is the
    point of declaring rather than merely recording."""
    stored = _declare(
        db,
        {"claim": "a", "grounding": "read"},
        {"claim": "b", "grounding": "assumed"},
        {"claim": "c", "grounding": "retrieved"},
    )
    summary = C.summarize_for_check(stored)

    assert summary is not None
    assert summary["declared"] == 3
    assert summary["weakly_grounded"] == 2, "assumed AND retrieved are both weak"
    assert "testimony, not observation" in summary["note"]


def test_no_claims_means_no_block(db):
    assert C.summarize_for_check([]) is None
    assert _declare(db) == []


# ── adjudication, and THE invariant ───────────────────────────────────


def test_an_unadjudicated_claim_becomes_untested_and_is_reported(db):
    """THE regression. A declared claim with no verdict must not stay NULL —
    'never adjudicated' and 'nothing declared' must never collapse into one count."""
    _declare(db, {"claim": "acted on this", "grounding": "assumed"})

    out = C.adjudicate(db, session_id="s-1", transaction_id="tx-1", adjudications=[])

    assert out["declared"] == 1
    assert out["untested"] == 1
    assert len(out["gaps"]) == 1
    assert "never checked" in out["gaps"][0]["note"]

    row = db.conn.execute("SELECT verdict FROM transaction_claims").fetchone()
    assert row[0] == "untested", "must be PERSISTED as untested, not left NULL"


def test_verdicts_apply_by_index(db):
    _declare(db, {"claim": "one", "grounding": "read"}, {"claim": "two", "grounding": "ran"})

    out = C.adjudicate(
        db,
        session_id="s-1",
        transaction_id="tx-1",
        adjudications=[{"index": 1, "verdict": "held"}, {"index": 2, "verdict": "refuted"}],
    )

    assert (out["held"], out["refuted"], out["untested"]) == (1, 1, 0)
    assert out["gaps"] == []


def test_verdicts_apply_by_id_and_by_prefix(db):
    stored = _declare(db, {"claim": "one", "grounding": "read"}, {"claim": "two", "grounding": "read"})

    out = C.adjudicate(
        db,
        session_id="s-1",
        transaction_id="tx-1",
        adjudications=[
            {"id": stored[0]["id"], "verdict": "held"},
            {"id": stored[1]["id"][:8], "verdict": "refuted"},
        ],
    )

    assert (out["held"], out["refuted"]) == (1, 1)


def test_a_partial_adjudication_leaves_the_rest_as_gaps(db):
    """The realistic case: two claims checked, one forgotten. The forgotten one is
    the whole point of the feature."""
    _declare(
        db,
        {"claim": "checked", "grounding": "read"},
        {"claim": "also checked", "grounding": "ran"},
        {"claim": "forgotten", "grounding": "assumed"},
    )

    out = C.adjudicate(
        db,
        session_id="s-1",
        transaction_id="tx-1",
        adjudications=[{"index": 1, "verdict": "held"}, {"index": 2, "verdict": "held"}],
    )

    assert out["untested"] == 1
    assert out["gaps"][0]["claim"] == "forgotten"
    assert "assumed" in out["gaps"][0]["note"]


def test_an_unknown_verdict_is_ignored_leaving_the_claim_untested(db):
    """A junk verdict must not silently count as a pass."""
    _declare(db, {"claim": "x", "grounding": "read"})

    out = C.adjudicate(
        db, session_id="s-1", transaction_id="tx-1", adjudications=[{"index": 1, "verdict": "probably fine"}]
    )

    assert out["untested"] == 1


def test_adjudication_is_scoped_to_the_transaction(db):
    """A later transaction must not sweep an earlier one's open claims."""
    _declare(db, {"claim": "old", "grounding": "read"}, tx="tx-old")
    _declare(db, {"claim": "new", "grounding": "read"}, tx="tx-new")

    C.adjudicate(db, session_id="s-1", transaction_id="tx-new", adjudications=[{"index": 1, "verdict": "held"}])

    old = db.conn.execute("SELECT verdict FROM transaction_claims WHERE transaction_id = 'tx-old'").fetchone()
    assert old[0] is None, "an earlier transaction's claims must stay open"


def test_evidence_is_recorded_when_given(db):
    _declare(db, {"claim": "x", "grounding": "read"})

    C.adjudicate(
        db,
        session_id="s-1",
        transaction_id="tx-1",
        adjudications=[{"index": 1, "verdict": "refuted", "evidence": "the test showed otherwise"}],
    )

    row = db.conn.execute("SELECT verdict, verdict_evidence FROM transaction_claims").fetchone()
    assert (row[0], row[1]) == ("refuted", "the test showed otherwise")


def test_nothing_declared_is_not_a_gap(db):
    """A transaction that declared no claims must not be reported as having gaps —
    that would make the feature nag every practice that has not adopted it."""
    out = C.adjudicate(db, session_id="s-1", transaction_id="tx-1", adjudications=[])

    assert out["declared"] == 0
    assert out["gaps"] == []


# ── adjudication must not fire at CHECK ───────────────────────────────


def test_the_retrospective_does_not_adjudicate_unless_asked(tmp_path, monkeypatch):
    """THE structural guard. The CHECK path calls _build_retrospective purely for
    artifact counts. If adjudication ran there, every claim would be forced to
    `untested` seconds after CHECK stored it — before any praxic work could test
    anything — producing a 100% gap rate forever while appearing to work.
    """
    import empirica.cli.command_handlers._workflow_shared as WS

    db_file = str(tmp_path / "t.db")
    real = SessionDatabase
    monkeypatch.setattr(WS, "_get_db_for_session", lambda _sid: real(db_path=db_file))

    d = real(db_path=db_file)
    try:
        C.declare(d, session_id="s-1", transaction_id="tx-1", claims=[{"claim": "x", "grounding": "read"}])
    finally:
        d.close()

    # The CHECK-shaped call: no opt-in.
    WS._build_retrospective("s-1", "tx-1")

    d = real(db_path=db_file)
    try:
        verdict = d.conn.execute("SELECT verdict FROM transaction_claims").fetchone()[0]
    finally:
        d.close()

    assert verdict is None, "CHECK must leave declared claims OPEN"


def test_the_retrospective_adjudicates_when_opted_in(tmp_path, monkeypatch):
    import empirica.cli.command_handlers._workflow_shared as WS

    db_file = str(tmp_path / "t.db")
    real = SessionDatabase
    monkeypatch.setattr(WS, "_get_db_for_session", lambda _sid: real(db_path=db_file))

    d = real(db_path=db_file)
    try:
        C.declare(d, session_id="s-1", transaction_id="tx-1", claims=[{"claim": "x", "grounding": "assumed"}])
    finally:
        d.close()

    retro = WS._build_retrospective("s-1", "tx-1", claim_adjudications=[], adjudicate_claims=True)

    assert retro.get("claims", {}).get("untested") == 1
    assert "claim_gap_note" in retro
    assert "UNTESTED" in retro["claim_gap_note"]
