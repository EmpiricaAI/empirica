"""A refuted claim must name what it refutes, or the strongest signal is inert.

`refuted` is the one verdict this layer produces that owes nothing to reading
text: the practitioner ran the thing and observed a contradiction. Measured
2026-08-21 across this practice: **490 claims, 80 carrying a `ref` (16%) — and of
17 refuted claims, exactly ONE named its referent.** So sixteen behavioural
contradictions were established, counted, and pointed at nothing.

Two halves, and the second is the load-bearing one:

1. **CHECK asks for the referent while it is still in hand.** Only for
   `grounding: retrieved`, where the claim came FROM an artifact and its id is
   definitionally available at declaration time. `read` and `ran` have referents
   too (a file, a command) but not artifact ids, so asking there would fire on
   almost every claim — an over-firing nudge trains dismissal of every signal
   printed beside it.
2. **POSTFLIGHT surfaces where a refutation points.** Before this, a refuted
   claim incremented a counter and stopped: the mechanism's best output had no
   output surface. A refuted `retrieved` claim is direct behavioural evidence
   that its source artifact is wrong, one `finding-resolve --kind retracted`
   away from correcting the graph.

Reported, never rejected. `declare()` is fail-soft by explicit design — a
malformed claim is skipped rather than failing the CHECK — so "required" here
means *echoed back at you*, not *refused*.
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


def _adjudicate(db, *entries, tx="tx-1", session="s-1"):
    return C.adjudicate(db, session_id=session, transaction_id=tx, adjudications=list(entries))


# ── half 1: CHECK asks, and asks NARROWLY ─────────────────────────────


def test_a_retrieved_claim_with_no_referent_is_echoed_at_check(db):
    stored = _declare(db, {"claim": "storage dedupes on hash", "grounding": "retrieved"})
    summary = C.summarize_for_check(stored)

    assert summary["missing_referents"] == 1
    assert "in your hand right now" in summary["referent_note"]


def test_naming_the_referent_silences_it(db):
    stored = _declare(db, {"claim": "storage dedupes on hash", "grounding": "retrieved", "ref": "f-9c1"})
    assert stored[0]["ref"] == "f-9c1", "the field was already live and simply unenforced"
    assert "missing_referents" not in C.summarize_for_check(stored)


@pytest.mark.parametrize("grounding", ["read", "ran", "assumed"])
def test_the_ask_does_not_fire_outside_retrieved(db, grounding):
    """A nudge that fires on nearly every claim is training, not feedback.

    `read` and `ran` DO have referents — a path, a command — but not artifact
    ids, so there is nothing for a refutation to propagate to. `assumed` has no
    referent by definition: not checking is what makes it an assumption.
    """
    stored = _declare(db, {"claim": "c", "grounding": grounding})
    summary = C.summarize_for_check(stored)
    assert "missing_referents" not in (summary or {})


def test_a_malformed_claim_is_still_skipped_not_rejected(db):
    """The whole module is advisory. `required` must mean reported, never refused."""
    stored = _declare(db, {"claim": "", "grounding": "retrieved"}, {"claim": "real", "grounding": "retrieved"})
    assert len(stored) == 1, "the empty claim was dropped, not raised on"
    assert C.summarize_for_check(stored)["missing_referents"] == 1


def test_the_referent_echo_does_not_displace_the_weakness_echo(db):
    """Both notes ride the same surface at the same moment; neither may shadow the other."""
    stored = _declare(
        db,
        {"claim": "a", "grounding": "assumed"},
        {"claim": "b", "grounding": "retrieved"},
    )
    summary = C.summarize_for_check(stored)
    assert summary["weakly_grounded"] == 2
    assert summary["missing_referents"] == 1
    assert summary["note"] and summary["referent_note"]


# ── half 2: POSTFLIGHT surfaces where the refutation POINTS ───────────


def test_a_refuted_retrieved_claim_names_the_artifact_it_contradicts(db):
    """This is the value. Everything above exists to make this line possible."""
    _declare(db, {"claim": "storage dedupes on hash", "grounding": "retrieved", "ref": "f-9c1"})
    out = _adjudicate(db, {"index": 1, "verdict": "refuted", "evidence": "two rows, same hash"})

    assert out["refuted"] == 1
    (r,) = out["refutations"]
    assert r["ref"] == "f-9c1"
    assert r["evidence"] == "two rows, same hash", "the evidence must survive the projection"
    assert "f-9c1" in r["note"] and "retracted" in r["note"], "it must say what to do about it"


def test_a_refuted_claim_with_no_referent_says_the_refutation_cannot_reach_it(db):
    """The 16-of-17 case. Naming the hole beats reporting a bare count."""
    _declare(db, {"claim": "storage dedupes on hash", "grounding": "retrieved"})
    (r,) = _adjudicate(db, {"index": 1, "verdict": "refuted"})["refutations"]

    assert r["ref"] is None
    assert "cannot reach it" in r["note"]


def test_the_surface_is_absent_when_nothing_is_refuted(db):
    """NEGATIVE CONTROL.

    `refutations` is emitted only when there is something to point at — so its
    presence is a real signal rather than an always-on empty list. Its absence on
    a held/untested population is what proves the key is doing work above.
    """
    _declare(db, {"claim": "a", "grounding": "ran"}, {"claim": "b", "grounding": "retrieved", "ref": "f-1"})
    out = _adjudicate(db, {"index": 1, "verdict": "held"})

    assert out["held"] == 1 and out["untested"] == 1
    assert "refutations" not in out


def test_a_refuted_read_claim_surfaces_without_an_artifact_prescription(db):
    """Refutation matters for every grounding — only the remedy is retrieved-specific.

    A `read` claim refuted means the code did not do what the source said. There
    may be no artifact to retract, so the note must not prescribe retracting one
    it cannot name.
    """
    _declare(db, {"claim": "the handler returns early", "grounding": "read"})
    (r,) = _adjudicate(db, {"index": 1, "verdict": "refuted"})["refutations"]

    assert r["grounding"] == "read"
    assert "finding-resolve" not in r["note"]
    assert "any artifact still asserts it" in r["note"]


def test_the_projection_reads_back_the_column_it_consumes(db):
    """`verdict_evidence` is stored by adjudicate and read by the refutation surface.

    Asserted directly off the row, not through the behaviour it enables — the
    tell for a projection that omits what storage holds is that every test
    exercises the feature and none reads the field back.
    """
    _declare(db, {"claim": "a", "grounding": "retrieved", "ref": "f-1"})
    _adjudicate(db, {"index": 1, "verdict": "refuted", "evidence": "observed otherwise"})

    rows = C._all_claims(db, "s-1", "tx-1")
    assert rows[0]["verdict_evidence"] == "observed otherwise"
    assert rows[0]["ref"] == "f-1"
